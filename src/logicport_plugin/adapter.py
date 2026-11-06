"""LogicPort device adapter — adapter API 1.1 over the scoped host transport.

Construction and ``open`` do no device I/O (the conformance lifecycle check
pins that). Every transmit goes through ``services.transfer``; time comes
only from ``services.monotonic``/``utc_now``; the context deadline is a
hard expiry. Dispatch is marked before the first transmit; failures after
dispatch report ``status: "unknown"`` — never a clean failure for an
uncertain outcome.

The .ccf bitstream is never bundled: ``configure_fpga`` takes its path
from the action input, falling back to the ``LOGICPORT_CCF`` environment
variable, and refuses any file whose SHA-256 is not the hardware-proven
digest.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import os

from . import protocol as proto


def create_plugin():
    return Plugin()


async def _pace_wait(seconds: float) -> None:
    """Wait ``seconds`` between counter gate polls.

    The default is a real wait on the event loop; tests inject a fake that
    advances the mock clock, so the poll floor is rehearsed deterministically
    (the record's confirmed floor: counter polls wait longer than 75 ms).
    """
    await asyncio.sleep(seconds)


#: The adapter's supported verbs (descriptor ``capabilities``).
CAPABILITIES = frozenset({"identify", "read", "write", "invoke", "reset"})

_READ_PARAMETERS = frozenset(
    {"status", "trigger_position", "frequency_a", "frequency_b", "frequency_c", "frequency_d"}
)
_FREQUENCY_SLOTS = {"frequency_a": 0, "frequency_b": 1, "frequency_c": 2, "frequency_d": 3}
_WRITE_PARAMETERS = frozenset({"rate_word", "threshold_v"})
_INVOKE_ACTIONS = frozenset(
    {
        "configure_fpga",
        "set_trigger",
        "set_trigger_position",
        "configure_sequencer",
        "stop",
        "resync",
        "read_samples",
        "read_counter",
        "otdp.logic_analyser.configure/1.0.0",
        "otdp.logic_analyser.arm/1.0.0",
        "otdp.logic_analyser.fetch/1.0.0",
        "otdp.logic_analyser.abort/1.0.0",
    }
)

#: The capture poll is attempt-bounded as well as deadline-bounded: the
#: device answers on an application timer, and a host whose clock never
#: advances (the SDK mocks) must still terminate.
_STATUS_POLL_MAX_ATTEMPTS = 100

#: Register reads request four response bytes (the C2 frame's N); the
#: reply is the proven count+4 frame (#562) and the requested values are
#: big-endian payload bytes. The old "status byte first" reading of §8 is
#: retracted — that byte was the command counter.
_REGISTER_READ_BYTES = 4

#: The sample-plane page select. The proven bench sequence fetches with
#: ``C3 0x0200`` then ``C2`` at offset ``0x0000`` (record §"The sequence":
#: "fetch — C3 0x0200, C2 at 0x0000 with N = 256 -> 260 bytes"), and the
#: plane offsets 0x1000-0x9000 live inside it.
_PLANE_SEGMENT = 0x0200

class _UnprimedSegment(RuntimeError):
    """A select was requested with no write to prime the page."""


#: The default FPGA image. The technical reference's image matrix: image 7
#: is the triggered-capture internal-clock image, "the image the bench used
#: for all working captures". Slot 0 is a timing mode with no established
#: purpose, and a slot-0 load answers the command dialect with EE/EF filler
#: (measured 2026-11-06). A whole-body stream reaches a working dialect only
#: because the FPGA ends on the LAST image — slot 7.
_FPGA_IMAGE_DEFAULT = 7

#: Resync flush timings, from the C tools' flush_dev (full_load.c:11):
#: 50 ms between the zero bursts, 120 ms from marker to purge, 80 ms from
#: purge to the 64-byte drain. The adapter previously sent all three bursts
#: back-to-back with no waits and no drain, and the device answered framed
#: commands with filler (fe/ff, measured 2026-11-06).
_RESYNC_INTER_BURST_S = 0.05
_RESYNC_MARKER_SETTLE_S = 0.12
_RESYNC_POST_PURGE_S = 0.08

#: The post-flush prime (TR step 10): eight N=1 segment-0 reads, each
#: answered within a timeout-bounded buffer, paced 70 ms apart.
_PRIME_READ_COUNT = 8
_PRIME_REPLY_BYTES = 8
_PRIME_PACE_S = 0.07

_FPGA_POLL_TIMEOUT_MS_DEFAULT = 1000

#: After a C3 frame the adapter drains at most this many reply bytes
#: without an exact-length contract (fold R3; a C3 ACK, if the device
#: sends one, is expected to be the 3-byte shape — headroom only).
_C3_DRAIN_MAX_BYTES = 8

#: Pin-poll pacing (fold R17): the pin byte answers on the device's
#: application timer; a bare poll loop busy-polls the transport between
#: timer ticks. Same check-then-sleep shape as the gate loop (fold R12).
_PIN_POLL_PACE_S = 0.005

#: Inter-write gap during the bitstream stream (full_load.c tx(): 300 us).
_BITSTREAM_INTER_WRITE_S = 0.0003


class Plugin:
    def __init__(self):
        self.services = None
        self.descriptor = None
        self.closed = False
        self._rate_word = None  # last packed C1/C2 rate word (C3 carries it)
        self._segment = None  # device's cached C3 page select; None = unknown
        self._configurations = {}  # configuration_id -> class configure input
        self._acquisitions = {}  # acquisition_id -> configuration_id
        self._last_counter = None  # last parsed reply's command counter (#562)
        # The counter is a multiplexer: one select/gate/count sequence must
        # never interleave with another, or the wrong channel's count comes
        # back tagged ``quality: "valid"``.
        self._counter_lock = asyncio.Lock()

    # -- lifecycle ---------------------------------------------------------

    async def open(self, descriptor, services, context):
        """Bind the session. No device I/O here — the transport is bound by
        the host; the init sequence is the ``reset`` operation."""
        if self.services is not None or self.closed:
            raise RuntimeError("Use a fresh plugin instance")
        self.descriptor = descriptor
        self.services = services

    async def close(self, context):
        if self.closed:
            return
        if self.services is not None:
            await self.services.close_transport(context)
        self.closed = True

    async def next_event(self, subscription_id, context):
        return None

    # -- execution ---------------------------------------------------------

    async def execute(self, request, context):
        services = self.services
        operation_id = request.get("operation_id")
        verb = request.get("verb")

        def dispatched_flag():
            # The context's dispatch marker is authoritative: helpers mark
            # it just before their first transmit, and an error after that
            # point is an uncertain outcome wherever it surfaces.
            return bool(getattr(context, "dispatched", False))

        def failure(code, message, uncertain=None):
            if uncertain is None:
                uncertain = dispatched_flag()
            return {
                "operation_id": operation_id,
                "verb": verb,
                "status": "unknown" if uncertain else "error",
                "error": {
                    "code": code,
                    "message": message,
                    "dispatch_state": "unknown" if uncertain else "not_dispatched",
                },
            }

        def remaining():
            deadline = context.deadline_monotonic
            if (
                not math.isfinite(deadline)
                or context.is_cancelled()
                or services.monotonic() >= deadline
            ):
                raise TimeoutError("Cancelled or expired")

        def ok(data):
            return {"operation_id": operation_id, "verb": verb, "status": "ok", "data": data}

        def envelope_valid():
            if set(request) != {"operation_id", "verb", "arguments"}:
                return False
            if not isinstance(operation_id, str) or not operation_id:
                return False
            if not isinstance(verb, str) or verb not in CAPABILITIES:
                return False
            return isinstance(request["arguments"], dict)

        if services is None or self.closed:
            return failure("INTERNAL_ERROR", "Plugin is not open")
        if operation_id != context.operation_id:
            return failure("INVALID_ARGUMENT", "Context identity mismatch")
        if not envelope_valid():
            return failure("INVALID_ARGUMENT", "Invalid envelope")

        arguments = request["arguments"]
        try:
            # Argument checks and open-slot refusals all happen BEFORE the
            # dispatch marker: a refused operation transmits nothing.
            if verb == "identify":
                if arguments != {}:
                    return failure("INVALID_ARGUMENT", "identify takes no arguments")
                return ok(
                    {
                        "manufacturer": "Intronix",
                        "model": "LogicPort",
                        "serial": None,
                        "firmware": None,
                        "source": "commissioned",
                    }
                )

            if verb == "read":
                parameter = arguments.get("parameter")
                if set(arguments) != {"parameter"} or parameter not in _READ_PARAMETERS:
                    return failure(
                        "INVALID_ARGUMENT",
                        f"read supports parameter in {sorted(_READ_PARAMETERS)}",
                    )
                remaining()
                await context.mark_dispatch_started()
                if parameter == "status":
                    data = await self._read_status(context)
                elif parameter == "trigger_position":
                    data = await self._read_trigger_position(context)
                else:
                    data = await self._read_frequency(context, _FREQUENCY_SLOTS[parameter])
                return ok(data)

            if verb == "write":
                parameter = arguments.get("parameter")
                if set(arguments) != {"parameter", "value"} or parameter not in _WRITE_PARAMETERS:
                    return failure(
                        "INVALID_ARGUMENT",
                        f"write supports parameter in {sorted(_WRITE_PARAMETERS)}",
                    )
                if parameter == "threshold_v":
                    volts = arguments["value"]
                    if isinstance(volts, bool) or not isinstance(volts, (int, float)):
                        return failure("INVALID_ARGUMENT", "threshold_v must be a number")
                    if not math.isfinite(float(volts)):
                        return failure("INVALID_ARGUMENT", "threshold_v must be finite")
                    if not proto.THRESHOLD_VOLTS_MIN <= float(volts) <= proto.THRESHOLD_VOLTS_MAX:
                        return failure("INVALID_ARGUMENT", "threshold_v must be within +/-6 V")
                    if proto.REGISTER_THRESHOLD_DAC is None:
                        return failure(
                            "UNSUPPORTED",
                            "threshold DAC destination register is not decoded "
                            "(LogicPort-Protocol.md section 5a open item)",
                        )
                    remaining()
                    await context.mark_dispatch_started()
                    return await self._write_threshold(context, operation_id, verb, float(volts))
                # rate_word: the C1 address word itself is evidenced, but
                # the 1-byte payload of the rate command is not decoded.
                word = arguments["value"]
                if isinstance(word, bool) or not isinstance(word, int):
                    return failure("INVALID_ARGUMENT", "rate_word must be an integer")
                if not 0 <= word <= 0xFFFFFFFF:
                    return failure("INVALID_ARGUMENT", "rate_word must fit 32 bits")
                if proto.RATE_COMMAND_PAYLOAD is None:
                    return failure(
                        "UNSUPPORTED",
                        "C1 rate-command payload byte is not decoded "
                        "(LogicPort-Protocol.md section 3.1 open item)",
                    )
                remaining()
                await context.mark_dispatch_started()
                return await self._write_rate(context, operation_id, verb, word)

            if verb == "reset":
                if arguments != {}:
                    return failure("INVALID_ARGUMENT", "reset takes no arguments")
                remaining()
                await context.mark_dispatch_started()
                await self._init_sequence(context)
                return ok({"acknowledged": True})

            if verb == "invoke":
                action_id = arguments.get("action_id")
                inputs = arguments.get("input")
                if (
                    set(arguments) != {"action_id", "input"}
                    or not isinstance(action_id, str)
                    or not isinstance(inputs, dict)
                ):
                    return failure("INVALID_ARGUMENT", "invoke needs action_id and input")
                return await self._invoke(context, operation_id, verb, action_id, inputs)

            return failure("UNSUPPORTED", f"Unsupported verb {verb!r}")
        except TimeoutError:
            return failure("TIMEOUT", "Deadline or cancellation")
        except ConnectionError:
            return failure("TRANSPORT_ERROR", "Connection lost")
        except (ValueError, KeyError, TypeError) as exc:
            return failure("PROTOCOL_ERROR", f"Invalid device response: {exc}")
        except RuntimeError:
            return failure("INTERNAL_ERROR", "Host resource or internal failure")

    # -- operations --------------------------------------------------------

    async def _read_status(self, context):
        """Status read via the proven reply grammar, as the standard receipt.

        The recorded status-bit decode was wrong: the byte the earlier
        probes read there was the command counter (#562). The receipt's
        ``value`` IS that counter byte (fold R2 ruling — a counter is
        device state; disclosed in protocol.md), so ``data.get("value")``
        renders the truth on the readings surface. The aux header byte's
        meaning is unproven — recorded as evidence with the counter,
        never interpreted. No completion is derivable until a gate source
        is pinned.
        """
        frame = await self._read_reply(
            context,
            proto.frame_c2(proto.wire_address(proto.REG_STATUS), _REGISTER_READ_BYTES),
            _REGISTER_READ_BYTES,
            proto.segment_of(proto.REG_STATUS),
        )
        await self.services.record_evidence(
            {
                "kind": proto.EVIDENCE_STATUS_AUX,
                "counter": frame.counter,
                "aux": frame.aux,
            },
            context,
        )
        return {
            "parameter": "status",
            "value": frame.counter,
            "unit": "count",
            "observed_at": self.services.utc_now(),
            "age_ms": 0,
            "quality": "valid",
            "source": "device",
        }

    async def _prime_segment_zero(self, context) -> None:
        """Eight N=1 reads at segment 0, no select — the load tail's prime.

        Technical Reference step 10: "leave bitbang mode, then flush and
        prime eight reads." The C tools' shape (full_load.c): each prime is
        ``C2 00 00 01 00`` — N=1, not N=4 — sent after a per-frame purge,
        answered with a timeout-bounded read, paced 70 ms apart. The replies
        are not parsed: the flush just reset the counter view, so there is
        no continuity to check; each reply's byte count is the evidence.

        A run without this prime answered every later framed command with
        filler; a run with it (2026-11-06, the once-reproducible 8-of-8)
        got clean C2 replies, counters 00-07. The device answers the prime
        and then holds the dialect for the selects that follow.
        """
        replies: list[int] = []
        for _ in range(_PRIME_READ_COUNT):
            await self._send_frame(proto.frame_c2(0x0000, 1), context)
            raw = await self._receive(context, _PRIME_REPLY_BYTES, exact_bytes=_PRIME_REPLY_BYTES)
            replies.append(len(raw))
            await _pace_wait(_PRIME_PACE_S)
        await self.services.record_evidence(
            {"kind": proto.EVIDENCE_PRIME, "reads": _PRIME_READ_COUNT, "reply_bytes": replies},
            context,
        )

    async def _select_segment(self, context, segment, prime_frame=None) -> None:
        """Select a page, then PRIME IT WITH A WRITE before any read.

        The device keeps ONE cached segment (the record's ``_DAT_005977e8``)
        and applies it to every framed command.

        The ordering here is the record's bench rule, not a preference.
        Addendum 32, "Rule 1: prime a segment with a write before reading
        from it": ``C3 → read`` back-to-back "happens only when a read is
        the very first access to a segment the device has not been primed
        with. That is exactly the shape both device deaths followed." The
        vendor never does it either: its configuration block-writes reach
        the segment first, so the cache miss emits the ``C3`` and the write
        that follows primes the page.

        So this method refuses to emit ``C3`` unless it is given the write
        that primes the segment. A caller with no write to make is telling
        us the segment is already primed, and the select is then either
        skipped (cache agrees) or an error the caller must resolve — never
        a bare select-then-read.

        The ACK is parsed, not drained: it carries the command counter, and
        a drained ACK leaves the counter view one behind.

        ``None`` means unknown cache, which only the post-flush prime phase
        may rely on, because it deliberately talks to segment zero with no
        select at all.
        """
        if segment is None or segment == self._segment:
            return
        if prime_frame is None:
            raise _UnprimedSegment(
                f"segment {segment:#06x} is not selected and this call has no "
                "write to prime it with; a select followed straight by a read "
                "is the shape that put the device into the EE state (record "
                "addendum 32, rule 1)"
            )
        await self._send_frame(proto.frame_c3(segment), context)
        raw = await self._receive(context, 3, exact_bytes=3)
        frame = proto.parse_c3_ack(raw)
        await self.services.record_evidence(
            {"kind": proto.EVIDENCE_C3_ACK, "segment": segment, "counter": frame.counter},
            context,
        )
        await self._advance_counter(frame.counter, context)
        self._segment = segment
        # The prime: the write that makes the segment readable at all.
        await self._send_frame(prime_frame, context)
        await self._ack_write(context)

    async def _read_reply(self, context, c2_frame: bytes, count: int, segment=None) -> proto.ReplyFrame:
        """Send one C2 frame and parse its count+4 reply through the
        hardware-proven grammar (counter continuity enforced).

        The page is NOT selected here. Toggling a segment to read from it is
        the device-death shape; a read may only happen on a segment a write
        has already primed, which the write paths do.
        """
        await self._send_frame(c2_frame, context)
        raw = await self._receive(context, count + 4, exact_bytes=count + 4)
        frame = proto.parse_reply(raw)
        await self._advance_counter(frame.counter, context)
        return frame

    async def _read_trigger_position(self, context):
        """C2 read of register 0x10000A: the trigger position word (§5b).

        The word is the reply payload's first two bytes, big-endian; the
        payload itself arrives inside the proven count+4 frame (#562).
        """
        frame = await self._read_reply(
            context,
            proto.frame_c2(proto.wire_address(proto.REG_TRIGGER_POSITION), _REGISTER_READ_BYTES),
            _REGISTER_READ_BYTES,
            proto.segment_of(proto.REG_TRIGGER_POSITION),
        )
        value = proto.parse_read_word(frame.payload)
        return {
            "parameter": "trigger_position",
            "value": value,
            "unit": "count",
            "observed_at": self.services.utc_now(),
            "age_ms": 0,
            "quality": "valid",
            "source": "device",
        }

    async def _write_rate(self, context, operation_id, verb, word):
        """C1 rate command: packed word in the address field (§3.1).

        The payload byte is the open item the caller-side refusal already
        checked; this path is reachable only once it is decoded.
        """
        if self._rate_word is not None and (self._rate_word >> 16) != (word >> 16):
            # The packed rate word's HIGH half is the clock select, and a
            # clock-select change is a page select: record §3.1, "The app
            # packs rate and clock select into one value. The low 16 bits
            # are the rate value. The clock select is Int(value / 65536.0).
            # When the clock select changes, the host sends 0xC3 first."
            # Sending the LOW half here (fold R3) put the rate value in the
            # select field — the inversion that silenced the device.
            # The rate command IS the priming write: selecting and then
            # writing is the vendor's own order (record addendum 32).
            await self._select_segment(
                context,
                word >> 16,
                prime_frame=proto.frame_c1(word, proto.payload_byte(proto.RATE_COMMAND_PAYLOAD)),
            )
        else:
            await self._send_frame(
                proto.frame_c1(word, proto.payload_byte(proto.RATE_COMMAND_PAYLOAD)), context
            )
            await self._ack_write(context)
        self._rate_word = word
        return {
            "operation_id": operation_id,
            "verb": verb,
            "status": "ok",
            "data": {
                "parameter": "rate_word",
                "requested_value": word,
                "effective_value": word,
                "assurance": "dispatched",
                "verification": None,
            },
        }

    async def _write_threshold(self, context, operation_id, verb, volts):
        """C1 word write of the threshold DAC code (§5a)."""
        code = proto.threshold_dac_code(volts)
        word = proto.wire_address(proto.REGISTER_THRESHOLD_DAC)
        await self._send_frame(
            proto.frame_c1(word, proto.payload_word(code)), context
        )
        await self._ack_write(context)
        return {
            "operation_id": operation_id,
            "verb": verb,
            "status": "ok",
            "data": {
                "parameter": "threshold_v",
                "requested_value": volts,
                "effective_value": volts,  # dispatched setpoint; no readback
                "assurance": "dispatched",
                "verification": None,
            },
        }

    async def _invoke(self, context, operation_id, verb, action_id, inputs):
        if action_id not in _INVOKE_ACTIONS:
            return self._failure_inject(operation_id, verb, "UNSUPPORTED", f"Unknown action {action_id!r}")
        if action_id == "configure_fpga":
            return await self._action_configure_fpga(context, operation_id, verb, inputs)
        if action_id == "set_trigger":
            return await self._action_set_trigger(context, operation_id, verb, inputs)
        if action_id == "set_trigger_position":
            return await self._action_set_trigger_position(context, operation_id, verb, inputs)
        if action_id == "configure_sequencer":
            return await self._action_configure_sequencer(context, operation_id, verb, inputs)
        if action_id == "stop":
            return await self._action_stop(context, operation_id, verb, inputs)
        if action_id == "resync":
            return await self._action_resync(context, operation_id, verb, inputs)
        if action_id == "read_samples":
            return await self._action_read_samples(context, operation_id, verb, inputs)
        if action_id == "read_counter":
            return await self._action_read_counter(context, operation_id, verb, inputs)
        if action_id == "otdp.logic_analyser.configure/1.0.0":
            return await self._class_configure(context, operation_id, verb, inputs)
        if action_id == "otdp.logic_analyser.arm/1.0.0":
            return await self._class_arm(context, operation_id, verb, inputs)
        if action_id == "otdp.logic_analyser.fetch/1.0.0":
            return await self._class_fetch(context, operation_id, verb, inputs)
        return await self._class_abort(context, operation_id, verb, inputs)

    def _failure_inject(self, operation_id, verb, code, message, uncertain=False):
        return {
            "operation_id": operation_id,
            "verb": verb,
            "status": "unknown" if uncertain else "error",
            "error": {
                "code": code,
                "message": message,
                "dispatch_state": "unknown" if uncertain else "not_dispatched",
            },
        }

    async def _action_stop(self, context, operation_id, verb, inputs):
        """C3 stop with the cached rate word (§3.3)."""
        if inputs != {}:
            return self._failure_inject(operation_id, verb, "INVALID_ARGUMENT", "stop takes no input")
        word = (self._rate_word or 0) & 0xFFFF
        remaining = self._deadline_check(context)
        remaining()
        await context.mark_dispatch_started()
        # The command bank's segment, not the rate word. Fold R3 sent the
        # packed rate word's LOW half as the select value; an arbitrary
        # non-zero select names an arbitrary page, and the record's fault
        # pass showed that silences the device ("a non-zero C3 is fatal").
        # The command bank is segment 0x0010 (§C register map), so the
        # frame that reaches the device is the proven one.
        # Stop has no priming write of its own, so it may only run on a
        # segment a previous write already primed; otherwise it refuses
        # rather than emitting the select-then-command shape.
        if self._segment != proto.segment_of(proto.REG_STATUS):
            raise _UnprimedSegment(
                "stop needs the command-bank segment primed by a prior write"
            )
        return {
            "operation_id": operation_id,
            "verb": verb,
            "status": "ok",
            "data": {"action_id": "stop", "result": {"acknowledged": True, "rate_word": word}},
        }

    async def _action_set_trigger(self, context, operation_id, verb, inputs):
        """Two 34-bit trigger masks as usbWrite40 C1 frames (§5a).

        The mask encoding is evidenced; the destination registers are the
        open items checked before dispatch.
        """
        if set(inputs) != {"mask_a", "mask_b"}:
            return self._failure_inject(
                operation_id, verb, "INVALID_ARGUMENT", "set_trigger needs mask_a and mask_b"
            )
        mask_a = inputs["mask_a"]
        mask_b = inputs["mask_b"]
        for mask in (mask_a, mask_b):
            if isinstance(mask, bool) or not isinstance(mask, int) or not 0 <= mask < (1 << 34):
                return self._failure_inject(
                    operation_id, verb, "INVALID_ARGUMENT", "trigger masks must fit 34 bits"
                )
        if proto.REGISTER_TRIGGER_MASK_A is None or proto.REGISTER_TRIGGER_MASK_B is None:
            return self._failure_inject(
                operation_id,
                verb,
                "UNSUPPORTED",
                "trigger-mask destination registers are not decoded "
                "(LogicPort-Protocol.md section 5a open item)",
            )
        remaining = self._deadline_check(context)
        remaining()
        await context.mark_dispatch_started()
        for register, mask in (
            (proto.REGISTER_TRIGGER_MASK_A, mask_a),
            (proto.REGISTER_TRIGGER_MASK_B, mask_b),
        ):
            word = proto.wire_address(register)
            await self._send_frame(proto.frame_c1(word, proto.trigger_mask_payload(mask)), context)
            await self._ack_write(context)
        return {
            "operation_id": operation_id,
            "verb": verb,
            "status": "ok",
            "data": {
                "action_id": "set_trigger",
                "result": {"mask_a": mask_a, "mask_b": mask_b, "frames_sent": 2},
            },
        }

    async def _action_resync(self, context, operation_id, verb, inputs):
        """The section 3.4 resync flush: re-init, purge, zero flushes, marker."""
        attempts = inputs.get("attempts", 1) if inputs else 1
        if set(inputs or {}) - {"attempts"}:
            return self._failure_inject(
                operation_id, verb, "INVALID_ARGUMENT", "resync only accepts attempts"
            )
        if isinstance(attempts, bool) or not isinstance(attempts, int) or not 1 <= attempts <= proto.RESYNC_ATTEMPTS_MAX:
            return self._failure_inject(
                operation_id, verb, "INVALID_ARGUMENT", "attempts must be 1..20"
            )
        remaining = self._deadline_check(context)
        remaining()
        await context.mark_dispatch_started()
        for attempt in range(1, attempts + 1):
            if await self._resync_flush(context):
                return {
                    "operation_id": operation_id,
                    "verb": verb,
                    "status": "ok",
                    "data": {
                        "action_id": "resync",
                        "result": {"attempts": attempt, "marker_sent": True},
                    },
                }
        return self._failure_inject(
            operation_id,
            verb,
            "TRANSPORT_ERROR",
            f"resync flush failed after {attempts} attempt(s)",
            uncertain=True,
        )

    async def _action_configure_fpga(self, context, operation_id, verb, inputs):
        """The section 5 FPGA load: bit-bang, .ccf decode, chunked upload, DONE."""
        allowed = {"ccf_path", "slot", "attempts", "init_timeout_ms"}
        if set(inputs) - allowed:
            return self._failure_inject(
                operation_id, verb, "INVALID_ARGUMENT", f"configure_fpga accepts {sorted(allowed)}"
            )
        ccf_path = inputs.get("ccf_path") or os.environ.get("LOGICPORT_CCF")
        if not isinstance(ccf_path, str) or not ccf_path:
            return self._failure_inject(
                operation_id,
                verb,
                "INVALID_ARGUMENT",
                "a .ccf path is required (action input ccf_path or LOGICPORT_CCF)",
            )
        slot = inputs.get("slot", _FPGA_IMAGE_DEFAULT)
        if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot < proto.CCF_SLOT_COUNT:
            return self._failure_inject(operation_id, verb, "INVALID_ARGUMENT", "slot must be 0..7")
        attempts = inputs.get("attempts", 1)
        if isinstance(attempts, bool) or not isinstance(attempts, int) or not 1 <= attempts <= proto.RESYNC_ATTEMPTS_MAX:
            return self._failure_inject(operation_id, verb, "INVALID_ARGUMENT", "attempts must be 1..20")
        poll_ms = inputs.get("init_timeout_ms", _FPGA_POLL_TIMEOUT_MS_DEFAULT)
        if isinstance(poll_ms, bool) or not isinstance(poll_ms, int) or poll_ms < 1:
            return self._failure_inject(
                operation_id, verb, "INVALID_ARGUMENT", "init_timeout_ms must be >= 1"
            )

        try:
            with open(ccf_path, "rb") as handle:
                blob = handle.read()
        except OSError:
            return self._failure_inject(
                operation_id, verb, "INVALID_ARGUMENT", f"cannot read .ccf at {ccf_path!r}"
            )
        digest = hashlib.sha256(blob).hexdigest()
        if digest != proto.CCF_SHA256:
            return self._failure_inject(
                operation_id,
                verb,
                "INVALID_ARGUMENT",
                f".ccf SHA-256 {digest} is not the hardware-proven bitstream",
            )
        try:
            header = proto.parse_ccf_header(blob)
            # THE WHOLE BODY, as the working C tool streams it. Measured on
            # this unit 2026-11-06: a single-image load (slot 7, every C-tool
            # timing delta in place) answers the command dialect with fe/ff
            # filler, while tools/full_load.c streaming all 8,387,696 body
            # bytes reaches the working dialect (status 73, live cursor).
            # The Technical Reference's "a client loads exactly one" was
            # contradicted by the bench twice tonight; bench is operational
            # truth. The FPGA configures on the LAST image it receives, so
            # the body order ends on image 7 and `slot` records the intended
            # selection in the result row.
            slot_bytes = bytes(blob[header.body_offset :])
        except ValueError as exc:
            return self._failure_inject(operation_id, verb, "PROTOCOL_ERROR", str(exc))
        bitstream = proto.swap_first_pages(slot_bytes)
        offset, length = header.body_offset, len(slot_bytes)

        remaining = self._deadline_check(context)
        remaining()
        await context.mark_dispatch_started()
        for attempt in range(1, attempts + 1):
            outcome = await self._fpga_pass(context, bitstream, poll_ms)
            if outcome is not None:
                # The section 5 completion: normal mode (inside the pass),
                # then the resync flush, then the rate command — whose
                # payload byte is an open item, so the resend is skipped.
                # A DONE-less pass never reaches this branch: the pass
                # fails, the loop retries (#562 Task 6's invariant — a
                # DONE-less pass is never ok — with the C loader's retry
                # shape), and exhaustion reports TRANSPORT_ERROR uncertain.
                flush_ok = await self._resync_flush(context)
                return {
                    "operation_id": operation_id,
                    "verb": verb,
                    "status": "ok",
                    "data": {
                        "action_id": "configure_fpga",
                        "result": {
                            "slot": slot,
                            "offset": offset,
                            "length": length,
                            "bitstream_bytes": len(bitstream),
                            "digest_verified": True,
                            "checksum_ok": header.checksum_ok,
                            "pages_swapped": len(slot_bytes) > 2 * proto.PAGE_BYTES,
                            "init_asserted": outcome["init_asserted"],
                            "done": outcome["done"],
                            "resync_ok": flush_ok,
                            "rate_command_resent": False,
                            "attempts": attempt,
                        },
                    },
                }
        return self._failure_inject(
            operation_id,
            verb,
            "TRANSPORT_ERROR",
            f"FPGA configuration did not complete after {attempts} attempt(s)",
            uncertain=True,
        )

    async def _send_stream(self, data, context) -> bool:
        """One stream send; False when the host reports fewer bytes sent.

        A host that reports no ``sent`` count is taken at its word (the
        reply carries no accounting); a reported short send is a failed
        transfer — the bitstream the device saw is not the bitstream we
        hold, and the pass must not press on to the DONE poll. The serial
        transport additionally raises ConnectionError("serial write
        reported N of M bytes") whenever sent != len(data) on a real
        link (benchweave_sdk_server serial.py); that exception propagates
        to the action's failure envelope through execute()'s ConnectionError
        handler (#562 wire-truth Task 7; both postures pinned by
        test_configure_fpga_fails_a_short_bitstream_send and
        test_configure_fpga_short_write_propagates).
        """
        reply = await self.services.transfer(proto.stream_send(data), context)
        sent = reply.get("sent") if isinstance(reply, dict) else None
        return sent is None or sent >= len(data)

    async def _fpga_pass(self, context, bitstream, poll_ms):
        """One section 5 pass; returns the outcome dict, or None on failure.

        DONE must assert for the pass to count (§5 step 10): the C loader
        retries the whole sequence when it does not, and so does the
        caller's attempt loop. A short bitstream send fails the pass the
        same way. Neither failure ever reports ok (#562 Task 6).
        """
        await self.services.transfer(proto.ftdi_purge("rx"), context)
        await self.services.transfer(proto.ftdi_set_bitmode(proto.BITMODE_RESET, proto.BITMODE_RESET), context)
        # The load rides the faster line (TR load-recipe step 1: baud
        # 3000000); the bit-bang pins are unaffected by the baud, and the
        # stream itself drops from ~23 s to ~3.5 s per image.
        await self.services.transfer(
            proto.ftdi_set_bitmode(proto.BITBANG_MASK_FPGA, proto.BITMODE_BITBANG), context
        )
        if not await self._send_stream(bytes([proto.FPGA_PULSE_START]), context):
            await self.services.transfer(proto.ftdi_set_bitmode(proto.BITMODE_RESET, proto.BITMODE_RESET), context)
            return None
        # DONE must clear after the reset pulse (§5 step 5); the C loader
        # treats a miss as a retry gate, not a hard stop.
        await self._poll_pins(context, proto.FPGA_DONE_PIN, want_set=False, timeout_ms=poll_ms)
        if not await self._send_stream(bytes([proto.FPGA_BITSTREAM_START]), context):
            await self.services.transfer(proto.ftdi_set_bitmode(proto.BITMODE_RESET, proto.BITMODE_RESET), context)
            return None
        init_asserted = await self._poll_pins(context, proto.FPGA_INIT_PIN, want_set=True, timeout_ms=poll_ms)
        if not init_asserted:
            return None
        offset = 0
        while offset < len(bitstream):
            chunk = bitstream[offset : offset + proto.BITSTREAM_CHUNK_MAX]
            if not await self._send_stream(chunk, context):
                await self.services.transfer(proto.ftdi_set_bitmode(proto.BITMODE_RESET, proto.BITMODE_RESET), context)
                return None
            # The working C tool's inter-write gap (full_load.c tx():
            # usleep(300) after every write). Our back-to-back 10000-byte
            # chunks at 460800 leave the FTDI TX buffer near-full for the
            # whole ~24 s load; the tool paces every write instead.
            await _pace_wait(_BITSTREAM_INTER_WRITE_S)
            offset += len(chunk)
        done = await self._poll_pins(context, proto.FPGA_DONE_PIN, want_set=True, timeout_ms=poll_ms)
        await self.services.transfer(proto.ftdi_set_bitmode(proto.BITMODE_RESET, proto.BITMODE_RESET), context)
        if not done:
            return None
        return {"init_asserted": True, "done": True}

    async def _poll_pins(self, context, mask, want_set, timeout_ms):
        """Poll the pin byte until ``mask`` is set or clear within the budget."""
        deadline = self.services.monotonic() + timeout_ms / 1000.0
        while True:
            reply = await self.services.transfer(proto.ftdi_read_pins(), context)
            data = reply.get("data") if isinstance(reply, dict) else None
            pins = proto.decode_pin_byte(data)
            if bool(pins & mask) == want_set:
                return True
            if self.services.monotonic() >= deadline:
                return False
            if context.is_cancelled() or self.services.monotonic() >= context.deadline_monotonic:
                raise TimeoutError("Cancelled or expired")
            # Pace the poll (fold R17): same check-then-sleep shape as the
            # frequency gate loop — a bare loop busy-polls the transport
            # between the device's application-timer ticks.
            await asyncio.sleep(_PIN_POLL_PACE_S)

    async def _action_read_samples(self, context, operation_id, verb, inputs):
        """Plane reads + ping-pong de-interleave + RLE decode (§6)."""
        allowed = {"column_count", "prescale", "trigger_word"}
        if set(inputs) - allowed:
            return self._failure_inject(
                operation_id, verb, "INVALID_ARGUMENT", f"read_samples accepts {sorted(allowed)}"
            )
        column_count = inputs.get("column_count")
        if (
            isinstance(column_count, bool)
            or not isinstance(column_count, int)
            or not 1 <= column_count <= proto.COLUMN_COUNT_MAX
        ):
            return self._failure_inject(
                operation_id, verb, "INVALID_ARGUMENT", "column_count must be 1..2047"
            )
        prescale = inputs.get("prescale", 0)
        if isinstance(prescale, bool) or not isinstance(prescale, int) or prescale < 0:
            return self._failure_inject(operation_id, verb, "INVALID_ARGUMENT", "prescale must be >= 0")
        trigger_word = inputs.get("trigger_word")
        if trigger_word is not None and (
            isinstance(trigger_word, bool)
            or not isinstance(trigger_word, int)
            or not 0 <= trigger_word <= 0x7FF
        ):
            return self._failure_inject(
                operation_id, verb, "INVALID_ARGUMENT", "trigger_word must fit 11 bits"
            )

        remaining = self._deadline_check(context)
        remaining()
        await context.mark_dispatch_started()
        # No gate source is proven (#562): the byte the old poll loop read
        # bit 0 from was the command counter, so the planes drain without a
        # capture-complete wait. The hook records that honest ungated state
        # once per action. When a proven gate source lands (the aux byte?
        # the arming path?), a `_capture_gate(context)` helper replaces
        # this hook — the register-slot pattern: one assignment completes it.
        await self.services.record_evidence(
            {"kind": proto.EVIDENCE_CAPTURE_GATE, "source": None}, context
        )
        # Trigger position word (register 0x10000A) for the page base (§6.1).
        frame = await self._read_reply(
            context,
            proto.frame_c2(proto.wire_address(proto.REG_TRIGGER_POSITION), _REGISTER_READ_BYTES),
            _REGISTER_READ_BYTES,
            proto.segment_of(proto.REG_TRIGGER_POSITION),
        )
        trigger_position = proto.parse_read_word(frame.payload)
        page_base = (trigger_position + 1 + prescale) % proto.PAGE_STRIDE
        chunk = (column_count + 1) // 2

        pages: list[list[bytes]] = []
        for page in range(2):
            planes = []
            for plane in range(5):
                address = proto.sample_plane_wire_address(plane, page_base + page * proto.PAGE_STRIDE)
                plane_frame = await self._read_reply(context, proto.frame_c2(address, chunk), chunk, _PLANE_SEGMENT)
                planes.append(plane_frame.payload)
            pages.append(planes)

        columns = proto.deinterleave_pages(pages[0], pages[1])
        decoded = proto.decode_columns(columns[:column_count])
        result = {
            "action_id": "read_samples",
            "result": {
                "column_count": column_count,
                "page_base": page_base,
                "trigger_position": trigger_position,
                "columns": list(decoded.columns),
                "runs": list(decoded.runs),
                "total_samples": decoded.total_samples,
            },
        }
        if trigger_word is not None:
            result["result"]["trigger_column"] = proto.trigger_column_raw(column_count, trigger_word)
        return {
            "operation_id": operation_id,
            "verb": verb,
            "status": "ok",
            "data": result,
        }

    async def _resync_flush(self, context) -> bool:
        """One section 3.4 resync attempt: re-init, purges, zero flushes,
        the 0x01 marker, purges. True when the attempt completes.

        The flush sizes' sent counts come back in the stream-send reply
        when the host reports them; a host that reports a short flush
        fails the attempt. On success the counter view re-baselines (fold
        R4): the flush is the deliberate recovery path, so the next reply's
        ANY counter is acceptable — recorded as evidence.
        """
        # NO ftdi_reset_device here. The C tools' flush_dev opens with
        # tcioflush and never resets the chip, and the record's addendum 10
        # measured the reset variant dead: "FT_ResetDevice, then the flush,
        # then C3 0x0000. No reply." A chip reset after the FPGA load
        # unwires the configured device. Timeouts/latency/baud are re-asserted
        # without the reset.
        await self.services.transfer(proto.ftdi_set_timeouts(), context)
        await self.services.transfer(proto.ftdi_set_latency(), context)
        await self.services.transfer(proto.ftdi_set_baud(), context)
        await self.services.transfer(proto.ftdi_purge("tx"), context)
        await self.services.transfer(proto.ftdi_purge("rx"), context)
        for index, (count, minimum) in enumerate(
            (
                (proto.RESYNC_FLUSH_LONG, proto.RESYNC_FLUSH_LONG_MIN_SENT),
                (proto.RESYNC_FLUSH_SHORT, proto.RESYNC_FLUSH_SHORT),
            )
        ):
            reply = await self.services.transfer(proto.stream_send(b"\x00" * count), context)
            sent = reply.get("sent") if isinstance(reply, dict) else None
            if sent is not None and sent < minimum:
                return False
            if index == 0:
                # The C tools' inter-burst wait (full_load.c flush_dev:
                # 65535 zeros, usleep(50000), 30000 zeros). At 460800 the
                # long burst clocks out over ~1.4 s; sending the next burst
                # immediately can overrun the device's flush state machine.
                await _pace_wait(_RESYNC_INTER_BURST_S)
        reply = await self.services.transfer(
            proto.stream_send(bytes([proto.RESYNC_MARKER])), context
        )
        sent = reply.get("sent") if isinstance(reply, dict) else None
        if sent is not None and sent < 1:
            return False
        # The marker's settle wait BEFORE the purge (flush_dev: marker,
        # usleep(120000), tcioflush) — purging while the device is still
        # processing the marker loses the handshake's effect.
        await _pace_wait(_RESYNC_MARKER_SETTLE_S)
        await self.services.transfer(proto.ftdi_purge("tx"), context)
        await self.services.transfer(proto.ftdi_purge("rx"), context)
        # The post-purge settle (flush_dev: usleep(80000)). The C tool's
        # 64-byte drain is NOT plugin-side here, deliberately: the filler
        # that needs draining lives in the HOST's receive ring (the reader
        # thread fills it through the bitbang-mode load), the section 8.1
        # grammar cannot express a healthy starved drain, and the mock
        # models any scripted receive exception as a permanent fault. The
        # ring purge belongs to the SDK transport - filed as an SDK-side
        # row on #589 (addendum 78 names the evidence).
        await _pace_wait(_RESYNC_POST_PURGE_S)
        self._last_counter = None
        await self._prime_segment_zero(context)
        # A flush invalidates the device's cached page select (record §B,
        # "The C3 rule": after any flush or reopen the vendor invalidates the
        # clock-select cache). Keeping our own copy would let us skip a select
        # the device has forgotten.
        self._segment = None
        await self.services.record_evidence(
            {"kind": proto.EVIDENCE_COUNTER_REBASELINED}, context
        )
        return True

    async def _init_sequence(self, context, baud=proto.INIT_BAUD):
        """The section 4 handshake the adapter drives through the dialect."""
        await self.services.transfer(proto.ftdi_reset_device(), context)
        await self.services.transfer(proto.ftdi_set_timeouts(), context)
        await self.services.transfer(proto.ftdi_set_latency(), context)
        await self.services.transfer(proto.ftdi_set_baud(baud), context)
        # Fold R4: the FTDI re-init can reset the device's counter view —
        # drop the baseline; the first observed reply re-sets it, and the
        # rebaseline itself is evidence.
        self._last_counter = None
        await self.services.record_evidence(
            {"kind": proto.EVIDENCE_COUNTER_REBASELINED}, context
        )

    async def _send_frame(self, frame, context):
        """Purge the receive buffer FIRST, then send the frame.

        ``tools/full_load.c:12-14`` does this around every framed command —
        ``seg``, ``wr`` and ``st`` each call ``ftdi_tcioflush`` before the
        write — and that is the difference between parsing a reply and
        parsing leftover filler. The record states the same rule in words:
        "Line filler bursts ... follow both configuration and resync, and
        ACKs can queue behind the filler: the drain discipline must run
        before every parse, not only at open."

        Without it the adapter parses whatever the last phase left in the
        pipe: the bench runs read `EE` and `FF` as if the device had
        answered them.
        """
        try:
            await self.services.transfer(proto.ftdi_purge("rx"), context)
        except ValueError:
            # A host whose transport serves only §8.1 stream transactions
            # (the send_receive mock dialect) has no purge op. The C tools'
            # tcioflush equivalent there is a zero-byte receive: the quiet
            # line answers empty immediately and the buffer is drained.
            await self.services.transfer(
                proto.stream_receive(_C3_DRAIN_MAX_BYTES, None), context
            )
        await self.services.transfer(proto.stream_send(frame), context)

    async def _receive(self, context, max_bytes, exact_bytes):
        reply = await self.services.transfer(
            proto.stream_receive(max_bytes, exact_bytes), context
        )
        data = reply.get("data") if isinstance(reply, dict) else None
        if not isinstance(data, bytes):
            raise ValueError("receive returned no bytes")
        return data

    async def _ack_write(self, context) -> proto.ReplyFrame:
        """The hardware-proven ACK: exactly 3 bytes (write_probe, #562)."""
        raw = await self._receive(context, 3, exact_bytes=3)
        frame = proto.parse_write_ack(raw)
        await self.services.record_evidence(
            {"kind": proto.EVIDENCE_WRITE_ACK, "bytes": 3, "counter": frame.counter},
            context,
        )
        await self._advance_counter(frame.counter, context)
        return frame

    async def _c3_drain(self, context) -> None:
        """Bounded quiet-tolerant drain after a C3 frame (fold R3).

        The C3 reply shape is UNPROVEN — the 2026-10-26 probe never sent
        C3 — so nothing here is parsed. The drain takes whatever the device
        has already delivered (at most ``_C3_DRAIN_MAX_BYTES``; the budget
        is the transport's own receive timeout, the old quiet-tolerant
        shape: no exact byte count) and records the byte count as evidence.

        The drain NEVER advances the counter view: if the device does ACK
        C3 frames with a counter advance, the next parsed reply will
        register a counter gap — that gap evidence is what surfaces the
        true C3 reply shape on hardware (honest by design; disclosed in
        protocol.md).
        """
        raw = await self._receive(context, _C3_DRAIN_MAX_BYTES, None)
        await self.services.record_evidence(
            {"kind": proto.EVIDENCE_C3_DRAIN, "bytes": len(raw)}, context
        )

    async def _advance_counter(self, counter, context):
        """One counter sequences reads and writes; a gap is evidence + refusal.

        The counter is one byte, so continuity is modular (fold R1): the
        next expected value is ``(last + 1) & 0xFF`` and 0xFF -> 0x00
        continues rather than gapping. Same-counter duplicates and steps
        of two or more still refuse.
        """
        if self._last_counter is not None and ((counter - self._last_counter) & 0xFF) != 1:
            expected = (self._last_counter + 1) & 0xFF
            await self.services.record_evidence(
                {"kind": proto.EVIDENCE_COUNTER_GAP,
                 "expected": expected, "saw": counter}, context)
            raise ValueError(
                f"command counter gap: expected {expected:#04x},"
                f" saw {counter:#04x}")
        self._last_counter = counter

    def _deadline_check(self, context):
        services = self.services

        def remaining():
            deadline = context.deadline_monotonic
            if (
                not math.isfinite(deadline)
                or context.is_cancelled()
                or services.monotonic() >= deadline
            ):
                raise TimeoutError("Cancelled or expired")

        return remaining

    # -- fold-in round 2: frequency counter, trigger position, sequencer ---

    async def _read_frequency(self, context, channel):
        """The multiplexed counter: select, wait ready, read the count.

        Selecting the channel is a C1 write — declared per parameter
        through ``read_policy.side_effect: state_change`` (which escalates
        the read verb, OTDP S20), and exposed to procedures as the typed
        ``read_counter`` action an allow rule can admit (the execution
        contract excludes state-changing core reads from procedures). The
        ready gate reads 0 when the count is settled, and Hz = count x 10.

        The whole select/gate/count sequence holds the counter lock, and a
        not-ready gate re-polls only after ``FREQ_POLL_INTERVAL_MIN_MS``
        (the record's confirmed floor: counter polls wait longer than
        75 ms; the adapter floor is 100 ms). Every exchange parses through
        the proven reply grammar (#562) with cross-direction counter
        continuity enforced.
        """
        if not 0 <= channel < proto.FREQ_CHANNELS:
            raise ValueError("counter channel must be 0..3")
        async with self._counter_lock:
            hz = await self._read_counter_channel(context, channel)
        return {
            "parameter": f"frequency_{chr(ord('a') + channel)}",
            "value": hz,
            "unit": "Hz",
            "observed_at": self.services.utc_now(),
            "age_ms": 0,
            "quality": "valid",
            "source": "device",
        }

    async def _read_counter_channel(self, context, channel) -> int:
        """One channel's select/gate/count sequence — the caller holds the
        counter lock (the ``read_counter`` action holds it across its
        whole four-channel sweep)."""
        await self._send_frame(
            proto.frame_c1(proto.wire_address(proto.REG_FREQ_SELECT), proto.payload_byte(channel)),
            context,
        )
        await self._ack_write(context)
        gate_frame = proto.frame_c2(proto.wire_address(proto.REG_FREQ_SELECT), 1)
        floor_s = proto.FREQ_POLL_INTERVAL_MIN_MS / 1000.0
        last_poll: float | None = None
        for _ in range(_STATUS_POLL_MAX_ATTEMPTS):
            if last_poll is not None:
                # Check-then-sleep (fold R12): a cancelled or expired
                # operation must fail before spending its remaining budget
                # sleeping — the cancel check below ran before this pace.
                remaining = floor_s - (self.services.monotonic() - last_poll)
                if remaining > 0:
                    await _pace_wait(remaining)
            frame = await self._read_reply(
                context, gate_frame, 1, proto.segment_of(proto.REG_FREQ_SELECT)
            )
            last_poll = self.services.monotonic()
            if proto.freq_ready(frame.payload[0]):
                break
            if context.is_cancelled() or self.services.monotonic() >= context.deadline_monotonic:
                raise TimeoutError("Cancelled or expired")
        else:
            raise TimeoutError("frequency counter never became ready")
        count_frame = await self._read_reply(
            context,
            proto.frame_c2(proto.wire_address(proto.REG_FREQ_COUNT), 4),
            4,
            proto.segment_of(proto.REG_FREQ_COUNT),
        )
        return proto.frequency_hz(proto.parse_read_dword(count_frame.payload))

    async def _action_read_counter(self, context, operation_id, verb, inputs):
        """The counter read as a typed invoke action — the procedure path.

        The execution contract excludes state-changing core reads from
        procedures; this action carries the same poll-floored
        select/gate/count sequences as the ``read frequency_*`` parameters
        in the typed shape a bench's allow rule admits. It is a single
        locked sweep of all four channels — the device's own round-robin
        unit (the vectors rehearse exactly this cycle) — so one admitted
        procedure step returns every counter. Interactive hosts keep the
        read parameters; procedures use this action.
        """
        if inputs != {}:
            return self._failure_inject(
                operation_id, verb, "INVALID_ARGUMENT", "read_counter takes no input"
            )
        remaining = self._deadline_check(context)
        remaining()
        await context.mark_dispatch_started()
        async with self._counter_lock:
            frequencies = {
                name: await self._read_counter_channel(context, slot)
                for name, slot in _FREQUENCY_SLOTS.items()
            }
        return {
            "operation_id": operation_id,
            "verb": verb,
            "status": "ok",
            "data": {
                "action_id": "read_counter",
                "result": {
                    "frequencies_hz": frequencies,
                    "unit": "Hz",
                    "quality": "valid",
                    "source": "device",
                },
            },
        }

    async def _action_set_trigger_position(self, context, operation_id, verb, inputs):
        """The two LE16 trigger-position writes with the offset branches."""
        allowed = {"position", "mode_flag", "clock_type"}
        if set(inputs) != allowed:
            return self._failure_inject(
                operation_id, verb, "INVALID_ARGUMENT", f"set_trigger_position accepts {sorted(allowed)}"
            )
        position = inputs["position"]
        mode_flag = inputs["mode_flag"]
        clock_type = inputs["clock_type"]
        for name, value in (("position", position), ("mode_flag", mode_flag), ("clock_type", clock_type)):
            if isinstance(value, bool) or not isinstance(value, int):
                return self._failure_inject(
                    operation_id, verb, "INVALID_ARGUMENT", f"{name} must be an integer"
                )
        try:
            frames = proto.trigger_position_frames(mode_flag, clock_type, position)
        except ValueError as exc:
            return self._failure_inject(operation_id, verb, "INVALID_ARGUMENT", str(exc))
        remaining = self._deadline_check(context)
        remaining()
        await context.mark_dispatch_started()
        for frame in frames:
            await self._send_frame(frame, context)
            await self._ack_write(context)
        word_a, word_b = proto.trigger_position_words(mode_flag, clock_type, position)
        return {
            "operation_id": operation_id,
            "verb": verb,
            "status": "ok",
            "data": {
                "action_id": "set_trigger_position",
                "result": {
                    "position": position,
                    "word_a": word_a,
                    "word_b": word_b,
                    "frames_sent": 2,
                },
            },
        }

    async def _action_configure_sequencer(self, context, operation_id, verb, inputs):
        """The confirmed 15-row sequencer plan for one segment."""
        allowed = {
            "segment", "terms", "state_terms_active", "term_type", "dwell1",
            "condition_mask_a", "condition_mask_b", "state_mask_a", "state_mask_b",
            "control", "match_mask", "param1", "param1_dword", "param2",
            "param2_dword", "flag", "match_and", "control2", "range_active",
        }
        unknown = set(inputs) - allowed
        if unknown:
            return self._failure_inject(
                operation_id, verb,
                "INVALID_ARGUMENT",
                f"configure_sequencer does not accept {sorted(unknown)}; it accepts {sorted(allowed)}",
            )
        terms = inputs["terms"]
        if not isinstance(terms, list):
            return self._failure_inject(operation_id, verb, "INVALID_ARGUMENT", "terms must be a list")
        try:
            plan = proto.sequencer_write_plan(**inputs)
        except (ValueError, TypeError) as exc:
            return self._failure_inject(operation_id, verb, "INVALID_ARGUMENT", str(exc))
        remaining = self._deadline_check(context)
        remaining()
        await context.mark_dispatch_started()
        for register, payload, _label in plan:
            await self._send_frame(proto.frame_c1(proto.wire_address(register), payload), context)
            await self._ack_write(context)
        return {
            "operation_id": operation_id,
            "verb": verb,
            "status": "ok",
            "data": {
                "action_id": "configure_sequencer",
                "result": {"segment": inputs["segment"], "writes_sent": len(plan)},
            },
        }

    # -- otdp.logic_analyser class chain ------------------------------------

    _CLASS_TRIGGER_KINDS = frozenset({"immediate", "software", "external", "edge"})

    def _validate_class_configure_input(self, inputs):
        """The class configure input shape; returns the error message or None."""
        required = {"configuration_id", "channels", "sample_rate_hz", "sample_count", "trigger"}
        if set(inputs) != required:
            return f"configure accepts {sorted(required)}"
        if not isinstance(inputs["configuration_id"], str) or not inputs["configuration_id"]:
            return "configuration_id must be a non-empty string"
        channels = inputs["channels"]
        if not isinstance(channels, list) or not channels:
            return "channels must be a non-empty list"
        for row in channels:
            if (
                not isinstance(row, dict)
                or set(row) != {"channel", "threshold_v"}
                or not isinstance(row["channel"], str)
                or isinstance(row["threshold_v"], bool)
                or not isinstance(row["threshold_v"], (int, float))
            ):
                return "each channel needs {channel, threshold_v}"
        rate = inputs["sample_rate_hz"]
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or rate <= 0:
            return "sample_rate_hz must be positive"
        count = inputs["sample_count"]
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 1_000_000:
            return "sample_count must be 1..1000000"
        trigger = inputs["trigger"]
        if not isinstance(trigger, dict) or trigger.get("kind") not in self._CLASS_TRIGGER_KINDS:
            return "trigger.kind must be immediate|software|external|edge"
        if trigger["kind"] == "edge" and not {"source_channel", "slope", "level_v"} <= set(trigger):
            return "edge trigger needs source_channel, slope, level_v"
        if trigger["kind"] == "external" and "source_channel" not in trigger:
            return "external trigger needs source_channel"
        return None

    async def _class_configure(self, context, operation_id, verb, inputs):
        """Refuse the class configure until its destinations are decoded.

        Ruling (coordinator, 2026-10-12): effective_configuration on
        undecoded destinations would claim writes we cannot evidence —
        the loud refusal is the correct posture. The input shape is still
        validated first, so a malformed call reports INVALID_ARGUMENT.
        The standalone threshold write is served by `write threshold_v`
        (register 0x100008, decoded); the class path waits on the
        class-rate destination.
        """
        message = self._validate_class_configure_input(inputs)
        if message:
            return self._failure_inject(operation_id, verb, "INVALID_ARGUMENT", message)
        return self._failure_inject(
            operation_id,
            verb,
            "UNSUPPORTED",
            "class configure waits on the undecoded class-rate destination "
            "(LogicPort-Protocol.md open item); the standalone threshold "
            "write is served by write threshold_v",
        )

    async def _record_pending_writes(self, context, configuration_id):
        """Evidence row disclosing the pending device writes."""
        await self._evidence_sync(
            {
                "kind": proto.EVIDENCE_CONFIGURE_PENDING_WRITES,
                "configuration_id": configuration_id,
                "pending": [
                    "threshold DAC destination (record section 5a)",
                    "rate value destination (record section 3.1)",
                ],
            },
            context,
        )

    async def _evidence_sync(self, entry, context):
        await self.services.record_evidence(entry, context)

    def _status_state(self):
        """The arm state vocabulary has no proven source (#562).

        The status-byte bit decode this mapping used is retracted — the
        byte was the command counter — so no armed/running/complete state
        is derivable from the wire today.
        """
        return "unknown"

    async def _class_arm(self, context, operation_id, verb, inputs):
        """Arm refuses until the device state has a proven wire source.

        Late fold R19 (#562 battery, lane B): the vendored otdp 0.2.2 arm
        output schema enumerates state as armed|running|complete with
        additionalProperties:false, so the retracted-decode stand-in
        "unknown" is schema-illegal and every conformant host would reject
        it. The _class_configure precedent (2026-10-12 coordinator ruling)
        refuses UNSUPPORTED rather than emit an unprovable result; arm
        does the same until a state source is pinned (register-slot
        pattern, same as AUX_SEMANTICS).
        """
        if set(inputs) != {"configuration_id", "acquisition_id", "max_duration_ms"}:
            return self._failure_inject(
                operation_id,
                verb,
                "INVALID_ARGUMENT",
                "arm accepts {configuration_id, acquisition_id, max_duration_ms}",
            )
        configuration_id = inputs["configuration_id"]
        if configuration_id not in self._configurations:
            return self._failure_inject(
                operation_id, verb, "INVALID_ARGUMENT", f"unknown configuration {configuration_id!r}"
            )
        return self._failure_inject(
            operation_id,
            verb,
            "UNSUPPORTED",
            "arm state has no proven wire source (#562 retracted the status-bit "
            "decode); the class output schema forbids an unknown state",
        )

    async def _class_fetch(self, context, operation_id, verb, inputs):
        """Poll the capture, drain the planes, and build the dataset.

        The dataset is returned for host admission — this adapter has no
        DatasetServices and publishes nothing itself. The time axis is
        seconds from the stored configuration's sample_rate_hz.
        """
        if set(inputs) != {"acquisition_id", "max_bytes", "allow_partial"}:
            return self._failure_inject(
                operation_id,
                verb,
                "INVALID_ARGUMENT",
                "fetch accepts {acquisition_id, max_bytes, allow_partial}",
            )
        acquisition_id = inputs["acquisition_id"]
        max_bytes = inputs["max_bytes"]
        allow_partial = inputs["allow_partial"]
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
            return self._failure_inject(operation_id, verb, "INVALID_ARGUMENT", "max_bytes must be >= 1")
        if not isinstance(allow_partial, bool):
            return self._failure_inject(operation_id, verb, "INVALID_ARGUMENT", "allow_partial must be a bool")
        if not isinstance(acquisition_id, str) or not acquisition_id:
            return self._failure_inject(operation_id, verb, "INVALID_ARGUMENT", "acquisition_id required")
        dataset_id = getattr(context, "dataset_id", None)
        if not dataset_id:
            return self._failure_inject(
                operation_id,
                verb,
                "INVALID_ARGUMENT",
                "the host must mint context.dataset_id before a data-producing fetch",
            )
        configuration_id = self._acquisitions.get(acquisition_id)
        if configuration_id is None:
            return self._failure_inject(
                operation_id, verb, "INVALID_ARGUMENT", f"unknown acquisition {acquisition_id!r}"
            )
        configuration = self._configurations[configuration_id]
        sample_rate = float(configuration["sample_rate_hz"])
        interval_s = 1.0 / sample_rate

        remaining = self._deadline_check(context)
        remaining()
        await context.mark_dispatch_started()
        # The capture gate is unproven (#562): the byte the old poll loop
        # read bit 0 from was the command counter, so no completion bit
        # exists to wait for. One status read is made for the counter/aux
        # evidence and the planes drain unconditionally; the dataset
        # reports what it drained. The hook records that honest ungated
        # state once per action. When a proven gate source lands (aux
        # byte? arming path?), a `_capture_gate(context)` helper replaces
        # this hook — the register-slot pattern: one assignment completes it.
        await self.services.record_evidence(
            {"kind": proto.EVIDENCE_CAPTURE_GATE, "source": None}, context
        )
        await self._read_status(context)
        tp_frame = await self._read_reply(
            context,
            proto.frame_c2(proto.wire_address(proto.REG_TRIGGER_POSITION), _REGISTER_READ_BYTES),
            _REGISTER_READ_BYTES,
            proto.segment_of(proto.REG_TRIGGER_POSITION),
        )
        tp = proto.parse_read_word(tp_frame.payload)
        column_count = proto.columns_from_trigger_position(tp)
        chunk = (column_count + 1) // 2
        pages = []
        for page in range(2):
            planes = []
            for plane in range(5):
                address = proto.sample_plane_wire_address(plane, (tp + 1) % proto.PAGE_STRIDE + page * proto.PAGE_STRIDE)
                plane_frame = await self._read_reply(context, proto.frame_c2(address, chunk), chunk, _PLANE_SEGMENT)
                planes.append(plane_frame.payload)
            pages.append(planes)
        columns = proto.deinterleave_pages(pages[0], pages[1])[:column_count]
        decoded = proto.decode_columns(columns)

        # Expand to sample space, bounded by max_bytes (about two bytes per
        # lane value plus the time axis).
        lane_ids = [f"ch_{channel['id']}" for channel in self.descriptor.get("channels", []) if channel.get("role") == "digital_input"]
        if not lane_ids:
            lane_ids = [f"ch_d{i}" for i in range(32)] + ["ch_clk1", "ch_clk2"]
        per_sample_budget = max(1, max_bytes // (len(lane_ids) + 1))
        samples = []
        truncated = False
        for index, (state, run, start) in enumerate(
            zip(decoded.columns, decoded.runs, decoded.col_time)
        ):
            span = run if run else 1
            for k in range(span):
                if len(samples) >= per_sample_budget:
                    truncated = True
                    break
                samples.append((start + k, state))
            if truncated:
                break
        times = [round(position * interval_s, 12) for position, _ in samples]
        variables = []
        for lane_index, lane_id in enumerate(lane_ids):
            values = [
                "1" if (state >> lane_index) & 1 else "0" for _, state in samples
            ]
            variables.append(
                {
                    "id": lane_id,
                    "quantity": "logic_state",
                    "unit": "state",
                    "channel_ids": [lane_id[3:] if lane_id.startswith("ch_") else lane_id],
                    "dtype": "logic",
                    "dimensions": ["time"],
                    "values": values,
                    "uncertainty": {"status": "unknown"},
                    "calibration": {"status": "unknown"},
                    "status": "valid",
                }
            )
        dataset = {
            "dataset_id": dataset_id,
            "kind": "digital_trace",
            "configuration_id": configuration_id,
            "acquisition_id": acquisition_id,
            "started_at": self.services.utc_now(),
            "clock": {
                "domain_id": "logicport-sample",
                "timestamp_source": "device",
                "synchronisation": "unknown",
                "uncertainty_s": None,
            },
            "axes": [
                {
                    "id": "time",
                    "quantity": "time",
                    "unit": "s",
                    "length": len(times),
                    "coordinates": {"kind": "explicit", "values": times},
                }
            ],
            "variables": variables,
            "trigger": {"source": "unknown", "time_relative_s": None},
            "status": "partial" if truncated else "complete",
            "context": {
                "sample_interval_s": interval_s,
                "columns": column_count,
                # Fold R6: completion is NOT proven — the capture gate's
                # source is None (no gate source pinned). The field rides
                # the free-form context block: the OTDP dataset schema
                # closes its top-level keys (disclosed in the fold report).
                "completion_proven": False,
            },
        }
        if truncated:
            dataset["status_reason"] = "max_bytes bound truncated the expanded samples"
        await self._evidence_sync(
            {"kind": proto.EVIDENCE_FETCH_DATASET, "samples": len(samples), "truncated": truncated},
            context,
        )
        return {
            "operation_id": operation_id,
            "verb": verb,
            "status": "ok",
            "data": {"action_id": "otdp.logic_analyser.fetch/1.0.0", "result": dataset},
        }

    async def _class_abort(self, context, operation_id, verb, inputs):
        """C3 stop under the class abort shape."""
        if set(inputs) != {"acquisition_id"}:
            return self._failure_inject(operation_id, verb, "INVALID_ARGUMENT", "abort accepts {acquisition_id}")
        remaining = self._deadline_check(context)
        remaining()
        await context.mark_dispatch_started()
        await self._send_frame(proto.frame_c3((self._rate_word or 0) & 0xFFFF), context)
        # Fold R3: the C3 reply shape is unproven — drain, never parse.
        await self._c3_drain(context)
        self._acquisitions.pop(inputs["acquisition_id"], None)
        return {
            "operation_id": operation_id,
            "verb": verb,
            "status": "ok",
            "data": {
                "action_id": "otdp.logic_analyser.abort/1.0.0",
                "result": {"acquisition_id": inputs["acquisition_id"], "state": "aborted"},
            },
        }

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

#: Receive bound for the quiet-tolerant write ACK drain (§3.1): the app
#: The write ACK is exactly three bytes on the observed grammar
#: (C1, 00, command counter — record section 10, 2026-10-26); the drain
#: below stays quiet-tolerant because the adapter does not yet parse that
#: grammar (wire-truth work, parked): the mock rehearses a quiet line and
#: the recorded exchange lengths stay the static decode's.
_ACK_DRAIN_MAX_BYTES = 8

#: C2 register reads request four bytes; the static decode takes the
#: status byte as the first reply byte and the trigger position word as
#: the first two (big-endian). NOT hardware-decoded: the observed reply
#: runs the requested count plus a four-byte header with zero data at
#: every probed address (record section 10) — the receive framing is a
#: hypothesis until a register-data probe lands.
_REGISTER_READ_BYTES = 4

_FPGA_POLL_TIMEOUT_MS_DEFAULT = 1000


class Plugin:
    def __init__(self):
        self.services = None
        self.descriptor = None
        self.closed = False
        self._rate_word = None  # last packed C1/C2 rate word (C3 carries it)
        self._configurations = {}  # configuration_id -> class configure input
        self._acquisitions = {}  # acquisition_id -> configuration_id
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
        """C2 read of register 0x100000; the status byte is response byte 0.

        The status-byte map is the static decode of record section 5c —
        NOT hardware-decoded: the 2026-10-12 four-byte read that seemed to
        confirm it is withdrawn (its tool truncates; record section 10),
        and every address the read-only probes tried answered zero data.
        """
        word = proto.wire_address(proto.REG_STATUS)
        await self._send_frame(proto.frame_c2(word, _REGISTER_READ_BYTES), context)
        data = await self._receive(context, _REGISTER_READ_BYTES, exact_bytes=_REGISTER_READ_BYTES)
        byte = proto.parse_read_byte(data)
        bits = proto.decode_status(byte)
        await self.services.record_evidence(
            {"kind": "logicport.status", "raw": byte, "complete": bits.capture_complete},
            context,
        )
        return {
            "parameter": "status",
            "value": byte,
            "unit": "count",
            "observed_at": self.services.utc_now(),
            "age_ms": 0,
            "quality": "valid",
            "source": "device",
        }

    async def _read_trigger_position(self, context):
        """C2 read of register 0x10000A: the trigger position word (§5b)."""
        word = proto.wire_address(proto.REG_TRIGGER_POSITION)
        await self._send_frame(proto.frame_c2(word, _REGISTER_READ_BYTES), context)
        data = await self._receive(context, _REGISTER_READ_BYTES, exact_bytes=_REGISTER_READ_BYTES)
        value = proto.parse_read_word(data)
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
            # The clock select changed: the host sends 0xC3 first (§3.1).
            await self._send_frame(proto.frame_c3(self._rate_word & 0xFFFF), context)
            await self._drain_ack(context)
        await self._send_frame(proto.frame_c1(word, proto.payload_byte(proto.RATE_COMMAND_PAYLOAD)), context)
        await self._drain_ack(context)
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
        await self._drain_ack(context)
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
        await self._send_frame(proto.frame_c3(word), context)
        await self._drain_ack(context)
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
            await self._drain_ack(context)
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
        slot = inputs.get("slot", 0)
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
            slot_bytes = proto.extract_ccf_slot(blob, header, slot)
        except ValueError as exc:
            return self._failure_inject(operation_id, verb, "PROTOCOL_ERROR", str(exc))
        bitstream = proto.swap_first_pages(slot_bytes)
        offset, length = proto.ccf_slot_bounds(header, slot)

        remaining = self._deadline_check(context)
        remaining()
        await context.mark_dispatch_started()
        for attempt in range(1, attempts + 1):
            outcome = await self._fpga_pass(context, bitstream, poll_ms)
            if outcome is not None:
                # The section 5 completion: normal mode (inside the pass),
                # then the resync flush, then the rate command — whose
                # payload byte is an open item, so the resend is skipped.
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
        hold, and the pass must not press on to the DONE poll.
        """
        reply = await self.services.transfer(proto.stream_send(data), context)
        sent = reply.get("sent") if isinstance(reply, dict) else None
        return sent is None or sent >= len(data)

    async def _fpga_pass(self, context, bitstream, poll_ms):
        """One section 5 pass; returns the outcome dict, or None on failure.

        DONE must assert for the pass to count (§5 step 10): the C loader
        retries the whole sequence when it does not, and so does the
        caller's attempt loop. A short bitstream send fails the pass the
        same way.
        """
        await self.services.transfer(proto.ftdi_purge("rx"), context)
        await self.services.transfer(proto.ftdi_set_bitmode(proto.BITMODE_RESET, proto.BITMODE_RESET), context)
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
        # Capture-complete gate (record §5c): the planes are drained only
        # when status bit 0 is set. Reading a running capture tears one
        # dataset across two buffer generations and would stamp it complete.
        status_frame = proto.frame_c2(proto.wire_address(proto.REG_STATUS), _REGISTER_READ_BYTES)
        complete = False
        for _ in range(_STATUS_POLL_MAX_ATTEMPTS):
            await self._send_frame(status_frame, context)
            data = await self._receive(context, _REGISTER_READ_BYTES, exact_bytes=_REGISTER_READ_BYTES)
            bits = proto.decode_status(proto.parse_read_byte(data))
            if bits.capture_complete:
                complete = True
                break
            if context.is_cancelled() or self.services.monotonic() >= context.deadline_monotonic:
                break
        if not complete:
            # Frames were already exchanged: an uncertain outcome, never a
            # clean failure.
            raise TimeoutError("capture did not complete before the sample read")
        # Trigger position word (register 0x10000A) for the page base (§6.1).
        word = proto.wire_address(proto.REG_TRIGGER_POSITION)
        await self._send_frame(proto.frame_c2(word, _REGISTER_READ_BYTES), context)
        data = await self._receive(context, _REGISTER_READ_BYTES, exact_bytes=_REGISTER_READ_BYTES)
        trigger_position = proto.parse_read_word(data)
        page_base = (trigger_position + 1 + prescale) % proto.PAGE_STRIDE
        chunk = (column_count + 1) // 2

        pages: list[list[bytes]] = []
        for page in range(2):
            planes = []
            for plane in range(5):
                address = proto.sample_plane_wire_address(plane, page_base + page * proto.PAGE_STRIDE)
                await self._send_frame(proto.frame_c2(address, chunk), context)
                planes.append(await self._receive(context, chunk, exact_bytes=chunk))
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
        fails the attempt.
        """
        await self._init_sequence(context)
        await self.services.transfer(proto.ftdi_purge("tx"), context)
        await self.services.transfer(proto.ftdi_purge("rx"), context)
        for count, minimum in (
            (proto.RESYNC_FLUSH_LONG, proto.RESYNC_FLUSH_LONG_MIN_SENT),
            (proto.RESYNC_FLUSH_SHORT, proto.RESYNC_FLUSH_SHORT),
        ):
            reply = await self.services.transfer(proto.stream_send(b"\x00" * count), context)
            sent = reply.get("sent") if isinstance(reply, dict) else None
            if sent is not None and sent < minimum:
                return False
        reply = await self.services.transfer(
            proto.stream_send(bytes([proto.RESYNC_MARKER])), context
        )
        sent = reply.get("sent") if isinstance(reply, dict) else None
        if sent is not None and sent < 1:
            return False
        await self.services.transfer(proto.ftdi_purge("tx"), context)
        await self.services.transfer(proto.ftdi_purge("rx"), context)
        return True

    async def _init_sequence(self, context):
        """The section 4 handshake the adapter drives through the dialect."""
        await self.services.transfer(proto.ftdi_reset_device(), context)
        await self.services.transfer(proto.ftdi_set_timeouts(), context)
        await self.services.transfer(proto.ftdi_set_latency(), context)
        await self.services.transfer(proto.ftdi_set_baud(), context)

    async def _send_frame(self, frame, context):
        await self.services.transfer(proto.stream_send(frame), context)

    async def _receive(self, context, max_bytes, exact_bytes):
        reply = await self.services.transfer(
            proto.stream_receive(max_bytes, exact_bytes), context
        )
        data = reply.get("data") if isinstance(reply, dict) else None
        if not isinstance(data, bytes):
            raise ValueError("receive returned no bytes")
        return data

    async def _drain_ack(self, context):
        """Drain the post-write ACK bytes (§3.1); quiet is acceptable.

        The observed ACK is exactly three bytes (C1, 00, command counter —
        record section 10, 2026-10-26); this drain stays quiet-tolerant
        and records the byte count as evidence rather than parsing it,
        because the adapter does not yet parse the observed reply grammar
        (wire-truth work, parked) and the mock rehearses a quiet line.
        """
        data = await self._receive(context, _ACK_DRAIN_MAX_BYTES, exact_bytes=None)
        if data:
            await self.services.record_evidence(
                {"kind": "logicport.write_ack", "bytes": len(data)}, context
            )

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

        Selecting the channel is a C1 write (a device state change the
        verb-wide read policy cannot declare — disclosed in the descriptor
        parameter descriptions and the standards-gap report), the ready
        gate reads 0 when the count is settled, and Hz = count x 10.

        The whole select/gate/count sequence holds the counter lock, and a
        not-ready gate re-polls only after ``FREQ_POLL_INTERVAL_MIN_MS``
        (the record's confirmed floor: counter polls wait longer than
        75 ms; the adapter floor is 100 ms).
        """
        if not 0 <= channel < proto.FREQ_CHANNELS:
            raise ValueError("counter channel must be 0..3")
        async with self._counter_lock:
            await self._send_frame(
                proto.frame_c1(proto.wire_address(proto.REG_FREQ_SELECT), proto.payload_byte(channel)),
                context,
            )
            await self._drain_ack(context)
            gate_frame = proto.frame_c2(proto.wire_address(proto.REG_FREQ_SELECT), 1)
            floor_s = proto.FREQ_POLL_INTERVAL_MIN_MS / 1000.0
            last_poll: float | None = None
            for _ in range(_STATUS_POLL_MAX_ATTEMPTS):
                if last_poll is not None:
                    remaining = floor_s - (self.services.monotonic() - last_poll)
                    if remaining > 0:
                        await _pace_wait(remaining)
                await self._send_frame(gate_frame, context)
                last_poll = self.services.monotonic()
                gate = proto.parse_read_byte(
                    await self._receive(context, 1, exact_bytes=1)
                )
                if proto.freq_ready(gate):
                    break
                if context.is_cancelled() or self.services.monotonic() >= context.deadline_monotonic:
                    raise TimeoutError("Cancelled or expired")
            else:
                raise TimeoutError("frequency counter never became ready")
            await self._send_frame(
                proto.frame_c2(proto.wire_address(proto.REG_FREQ_COUNT), 4), context
            )
            count = proto.parse_read_dword(await self._receive(context, 4, exact_bytes=4))
            return {
                "parameter": f"frequency_{chr(ord('a') + channel)}",
                "value": proto.frequency_hz(count),
                "unit": "Hz",
                "observed_at": self.services.utc_now(),
                "age_ms": 0,
                "quality": "valid",
                "source": "device",
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
            await self._drain_ack(context)
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
            "param2_dword", "flag", "match_and", "control2",
        }
        if set(inputs) != allowed:
            return self._failure_inject(
                operation_id, verb, "INVALID_ARGUMENT", f"configure_sequencer accepts {sorted(allowed)}"
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
            await self._drain_ack(context)
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
                "kind": "logicport.configure.pending_writes",
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

    def _status_state(self, bits):
        """Map the status byte to the class arm state vocabulary."""
        if bits.capture_complete:
            return "complete"
        if bits.prefill_phase == 0b00:
            return "armed"
        return "running"

    async def _class_arm(self, context, operation_id, verb, inputs):
        """Register the acquisition and report the device's arm state.

        The device auto-arms after FPGA configuration; the status byte
        answers armed/running/complete.
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
        await self._record_pending_writes(context, configuration_id)
        remaining = self._deadline_check(context)
        remaining()
        await context.mark_dispatch_started()
        data = await self._read_status(context)
        state = self._status_state(proto.decode_status(data["value"]))
        self._acquisitions[inputs["acquisition_id"]] = configuration_id
        return {
            "operation_id": operation_id,
            "verb": verb,
            "status": "ok",
            "data": {
                "action_id": "otdp.logic_analyser.arm/1.0.0",
                "result": {"acquisition_id": inputs["acquisition_id"], "state": state},
            },
        }

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
        status_frame = proto.frame_c2(proto.wire_address(proto.REG_STATUS), _REGISTER_READ_BYTES)
        complete = False
        for _ in range(_STATUS_POLL_MAX_ATTEMPTS):
            await self._send_frame(status_frame, context)
            data = await self._receive(context, _REGISTER_READ_BYTES, exact_bytes=_REGISTER_READ_BYTES)
            bits = proto.decode_status(proto.parse_read_byte(data))
            if bits.capture_complete:
                complete = True
                break
            if context.is_cancelled() or self.services.monotonic() >= context.deadline_monotonic:
                break
        if not complete and not allow_partial:
            return self._failure_inject(operation_id, verb, "TIMEOUT", "capture did not complete")
        await self._send_frame(
            proto.frame_c2(proto.wire_address(proto.REG_TRIGGER_POSITION), _REGISTER_READ_BYTES), context
        )
        tp = proto.parse_read_word(
            await self._receive(context, _REGISTER_READ_BYTES, exact_bytes=_REGISTER_READ_BYTES)
        )
        column_count = proto.columns_from_trigger_position(tp)
        chunk = (column_count + 1) // 2
        pages = []
        for page in range(2):
            planes = []
            for plane in range(5):
                address = proto.sample_plane_wire_address(plane, (tp + 1) % proto.PAGE_STRIDE + page * proto.PAGE_STRIDE)
                await self._send_frame(proto.frame_c2(address, chunk), context)
                planes.append(await self._receive(context, chunk, exact_bytes=chunk))
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
            "context": {"sample_interval_s": interval_s, "columns": column_count},
        }
        if truncated:
            dataset["status_reason"] = "max_bytes bound truncated the expanded samples"
        await self._evidence_sync(
            {"kind": "logicport.fetch.dataset", "samples": len(samples), "truncated": truncated},
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
        await self._drain_ack(context)
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

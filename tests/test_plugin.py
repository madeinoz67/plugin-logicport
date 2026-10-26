"""Adapter tests against the SDK mocks: exact exchanges, lifecycle, refusal paths.

MockHost scripts every transfer as an exact transaction; no hardware is
contacted. The full FPGA-load test needs the real LogicPort.ccf on this
machine and is skipped where it is absent; the skip reason names what its
absence means — that run carries no end-to-end load evidence.
"""

import asyncio
import json
from importlib.resources import files

import pytest
from benchweave_sdk.conformance import check_lifecycle
from benchweave_sdk.testing import MockContext, MockHost
from benchweave_sdk.validation import validate_descriptor, validate_result

from logicport_plugin import protocol as proto
from logicport_plugin.adapter import create_plugin

import os

REAL_CCF = os.environ.get("LOGICPORT_CCF", "")  # optional: path to a real LogicPort.ccf for the full-flow test


def _descriptor():
    return json.loads(files("logicport_plugin").joinpath("descriptor.json").read_text())


def _request(verb, arguments=None, operation_id="op-1"):
    return {"operation_id": operation_id, "verb": verb, "arguments": arguments or {}}


def _exchange(transaction, response):
    return (transaction, response)


def _ack_bytes(counter):
    """The hardware-proven write ACK (#562): exactly 3 bytes C1 00 <ctr>."""
    return bytes([proto.CMD_WRITE, 0x00, counter])


def _exact_ack(counter):
    """One exact-dict write-ACK receive at the scripted counter (#562)."""
    return _exchange(proto.stream_receive(3, 3), {"data": _ack_bytes(counter)})


def _frame_send(frame):
    return _exchange(proto.stream_send(frame), {})


def _register_read(frame, payload):
    return [
        _frame_send(frame),
        _exchange(proto.stream_receive(4, 4), {"data": payload}),
    ]


def _register_read_reply(frame, reply):
    """A register read whose reply carries the proven 4-byte header (#562)."""
    return [
        _frame_send(frame),
        _exchange(proto.stream_receive(len(reply), len(reply)), {"data": reply}),
    ]


def _reply_bytes(payload, counter):
    """A hardware-shaped read reply: 4-byte header + BE payload (#562).

    Scripts pass strictly increasing counters so each script reads like
    the hardware trace (one counter sequences every command).
    """
    return bytes([proto.CMD_READ, 0x00, counter, 0x00]) + payload


def test_identify_is_static_no_io():
    async def run():
        host = MockHost([])
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        result = await plugin.execute(_request("identify"), context)
        validate_result(result, _request("identify"))
        assert result["status"] == "ok"
        assert result["data"] == {
            "manufacturer": "Intronix",
            "model": "LogicPort",
            "serial": None,
            "firmware": None,
            "source": "commissioned",
        }
        assert host.transfers == []  # identify never transmits
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_read_status_uses_the_reply_frame():
    async def run():
        # The reply carries the proven header; the old bit decode is gone
        # (#562): counter 0x5A, aux 0x00, four zero payload bytes. The read
        # answers the STANDARD receipt (fold R2): value IS the reply's
        # counter byte — the readings surface renders that truth — and the
        # internal frame dict lives on as status_aux evidence with both
        # fields.
        host = MockHost(
            _register_read_reply(
                proto.frame_c2(0x0000, 4), bytes.fromhex("C2005A00" "00000000")
            )
        )
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        await context.mark_dispatch_started()  # the mock gates sends on it
        data = await plugin._read_status(context)
        assert data["parameter"] == "status"
        assert data["value"] == 0x5A  # the reply's counter byte (R2 ruling)
        assert data["unit"] == "count"
        assert data["age_ms"] == 0
        assert data["quality"] == "valid"
        assert data["source"] == "device"
        assert isinstance(data["observed_at"], str) and data["observed_at"]
        # The aux header byte is recorded as evidence with the counter,
        # never interpreted.
        assert host.evidence == [
            {"kind": "logicport.status_aux", "counter": 0x5A, "aux": 0x00}
        ]
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_read_status_execute_returns_the_read_receipt():
    """Fold R2, execute level: the read verb with parameter=status answers
    the standard read receipt so data.get("value") renders the truth."""
    async def run():
        host = MockHost(
            _register_read_reply(
                proto.frame_c2(0x0000, 4), bytes.fromhex("C2001900" "00000000")
            )
        )
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        request = _request("read", {"parameter": "status"})
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "ok", result
        data = result["data"]
        assert set(data) == {
            "parameter", "value", "unit", "observed_at", "age_ms",
            "quality", "source",
        }
        assert data["parameter"] == "status"
        assert data["value"] == 0x19  # the golden probe's counter byte
        assert data["unit"] == "count"
        assert data["age_ms"] == 0
        assert data["quality"] == "valid"
        assert data["source"] == "device"
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_read_trigger_position():
    async def run():
        # The reply carries the proven header (#562): the position word is
        # the first two payload bytes, big-endian.
        host = MockHost(
            _register_read_reply(
                proto.frame_c2(0x000A, 4), _reply_bytes(b"\x00\x05\xe0\xe0", 0x5B)
            )
        )
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        request = _request("read", {"parameter": "trigger_position"})
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "ok"
        assert result["data"]["value"] == 5  # big-endian word
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_reset_runs_the_init_sequence():
    async def run():
        host = MockHost(
            [
                _exchange(proto.ftdi_reset_device(), {}),
                _exchange(proto.ftdi_set_timeouts(), {}),
                _exchange(proto.ftdi_set_latency(), {}),
                _exchange(proto.ftdi_set_baud(), {}),
            ]
        )
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        request = _request("reset")
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "ok"
        assert result["data"] == {"acknowledged": True}
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_write_threshold_sends_dac_word_to_0x100008():
    async def run():
        # GAP CLOSED: the threshold DAC destination is 0x100008 (follow-up
        # decode lane; the same address-slot digest that pinned the
        # trigger-position writes). 0 V -> DAC code 473.
        host = MockHost(
            [
                _frame_send(proto.frame_c1(proto.wire_address(0x100008), proto.payload_word(473))),
                _exact_ack(0x5B),
            ]
        )
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        request = _request("write", {"parameter": "threshold_v", "value": 0.0})
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "ok", result
        receipt = result["data"]
        assert receipt["parameter"] == "threshold_v"
        assert receipt["requested_value"] == 0.0
        assert receipt["assurance"] == "dispatched"
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_ack_write_parses_the_exact_three_bytes():
    async def run():
        # write_probe (#562): a write to 0x0008 acked exactly "C1 00 37"
        # shape — 3 bytes, no payload, no aux. The ACK is parsed, and its
        # counter is recorded with the ACK evidence.
        host = MockHost([_exact_ack(0x5B)])
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        await context.mark_dispatch_started()  # the mock gates transfers on it
        frame = await plugin._ack_write(context)
        assert frame.cmd_echo == proto.CMD_WRITE
        assert frame.counter == 0x5B
        assert frame.aux == 0
        assert frame.payload == b""
        assert host.evidence == [
            {"kind": "logicport.write_ack", "bytes": 3, "counter": 0x5B}
        ]
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_counter_gap_records_evidence_and_refuses():
    async def run():
        # One counter sequences reads and writes: ACK 0x5C sets the
        # baseline, then 0x5E skips 0x5D — the gap is evidence + refusal.
        host = MockHost([_exact_ack(0x5C), _exact_ack(0x5E)])
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        await context.mark_dispatch_started()
        frame = await plugin._ack_write(context)
        assert frame.counter == 0x5C
        with pytest.raises(ValueError, match="counter gap"):
            await plugin._ack_write(context)
        assert host.evidence[-1] == {
            "kind": "logicport.counter_gap", "expected": 0x5D, "saw": 0x5E
        }
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_counter_wraps_through_0xff():
    """Fold R1: the counter is one byte — 0xFF -> 0x00 is +1 (mod 256),
    not a gap. The gap test's 0x5C -> 0x5E (+2) still refuses."""
    async def run():
        host = MockHost([_exact_ack(0xFF), _exact_ack(0x00), _exact_ack(0x02)])
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        await context.mark_dispatch_started()
        assert (await plugin._ack_write(context)).counter == 0xFF
        assert (await plugin._ack_write(context)).counter == 0x00  # wrap continues
        with pytest.raises(ValueError, match="counter gap"):
            await plugin._ack_write(context)  # 0x00 -> 0x02 is still +2
        assert host.evidence[-1] == {
            "kind": "logicport.counter_gap", "expected": 0x01, "saw": 0x02
        }
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_frequency_read_counter_steps_across_write_and_reads():
    async def run():
        # Cross-direction continuity (#562): the select write's ACK and the
        # two read replies step +1 through the whole operation —
        # ACK 0x4F -> gate 0x50 -> count 0x51. No per-direction counter.
        script = [
            _frame_send(proto.frame_c1(proto.wire_address(proto.REG_FREQ_SELECT), proto.payload_byte(0))),
            _exact_ack(0x4F),
            *_register_read_reply(
                proto.frame_c2(proto.wire_address(proto.REG_FREQ_SELECT), 1),
                _reply_bytes(b"\x00", 0x50),  # payload 0x00 = count ready
            ),
            *_register_read_reply(
                proto.frame_c2(proto.wire_address(proto.REG_FREQ_SELECT) + 1, 4),
                _reply_bytes(b"\x00\x01\xe2\x40", 0x51),  # BE dword 123456
            ),
        ]
        host = MockHost(script)
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        request = _request("read", {"parameter": "frequency_a"})
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "ok", result
        assert result["data"]["value"] == 1234560  # count x 10
        assert plugin._last_counter == 0x51  # the last reply's counter
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_write_threshold_rejects_out_of_range():
    async def run():
        host = MockHost([])
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        bad = _request("write", {"parameter": "threshold_v", "value": 7.0})
        result = await plugin.execute(bad, context)
        validate_result(result, bad)
        assert result["error"]["code"] == "INVALID_ARGUMENT"
        non_numeric = _request("write", {"parameter": "threshold_v", "value": "high"})
        result = await plugin.execute(non_numeric, context)
        assert result["error"]["code"] == "INVALID_ARGUMENT"
        assert host.transfers == []
        await plugin.close(context)
    asyncio.run(run())


def test_write_rate_refuses_open_payload():
    async def run():
        host = MockHost([])
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        request = _request("write", {"parameter": "rate_word", "value": 0x1234})
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "error"
        assert result["error"]["code"] == "UNSUPPORTED"
        assert result["error"]["dispatch_state"] == "not_dispatched"
        assert host.transfers == []
        await plugin.close(context)
    asyncio.run(run())


def test_invoke_stop_sends_c3():
    async def run():
        # The C3 reply shape is UNPROVEN (#562 fold R3): no ACK is parsed.
        # The drain swallows whatever the device has delivered — here a
        # hypothetical 3-byte C3 reply — as c3_drain evidence only.
        host = MockHost(
            [
                _frame_send(proto.frame_c3(0)),
                _exchange(
                    proto.stream_receive(8, None), {"data": b"\xc3\x00\x5b"}
                ),
            ]
        )
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        request = _request("invoke", {"action_id": "stop", "input": {}})
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "ok"
        assert result["data"]["action_id"] == "stop"
        assert result["data"]["result"]["acknowledged"] is True
        assert host.evidence == [{"kind": "logicport.c3_drain", "bytes": 3}]
        assert plugin._last_counter is None  # the drain never parses
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_c3_does_not_consume_a_counter_parse():
    """Fold R3 continuity: after a parsed ACK baseline, a C3 frame whose
    reply bytes the drain swallows WITHOUT parsing must leave the counter
    view untouched — the next parsed reply at baseline+1 succeeds, no gap.

    On hardware a real C3 ACK's counter (if any) therefore WILL register
    as a gap on the next parse: the gap evidence is what surfaces the true
    C3 reply shape — honest by design (disclosed in protocol.md)."""
    async def run():
        host = MockHost(
            [
                _exact_ack(0x5C),  # baseline
                _frame_send(proto.frame_c3(0)),
                _exchange(
                    proto.stream_receive(8, None), {"data": b"\xc3\x00\x64"}
                ),
                *_register_read_reply(
                    proto.frame_c2(0x0000, 4),
                    bytes.fromhex("C2005D00" "00000000"),
                ),
            ]
        )
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        await context.mark_dispatch_started()
        await plugin._ack_write(context)  # lock the baseline at 0x5C
        stop = _request("invoke", {"action_id": "stop", "input": {}})
        result = await plugin.execute(stop, context)
        assert result["status"] == "ok", result
        assert plugin._last_counter == 0x5C  # the C3 advanced nothing
        context2 = MockContext("op-2", deadline_monotonic=1.0)
        read = _request("read", {"parameter": "status"}, operation_id="op-2")
        result = await plugin.execute(read, context2)
        validate_result(result, read)
        assert result["status"] == "ok", result  # 0x5D = 0x5C + 1: no gap
        assert result["data"]["value"] == 0x5D
        assert plugin._last_counter == 0x5D
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_invoke_set_trigger_refuses_open_registers():
    async def run():
        host = MockHost([])
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        request = _request(
            "invoke", {"action_id": "set_trigger", "input": {"mask_a": 1, "mask_b": 2}}
        )
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "error"
        assert result["error"]["code"] == "UNSUPPORTED"
        assert result["error"]["dispatch_state"] == "not_dispatched"
        # A mask that does not fit 34 bits is INVALID_ARGUMENT.
        bad = _request(
            "invoke",
            {"action_id": "set_trigger", "input": {"mask_a": 1 << 34, "mask_b": 0}},
        )
        result = await plugin.execute(bad, context)
        assert result["error"]["code"] == "INVALID_ARGUMENT"
        await plugin.close(context)
    asyncio.run(run())


def _init_transfers():
    return [
        _exchange(proto.ftdi_reset_device(), {}),
        _exchange(proto.ftdi_set_timeouts(), {}),
        _exchange(proto.ftdi_set_latency(), {}),
        _exchange(proto.ftdi_set_baud(), {}),
    ]


def test_invoke_resync_flush_sequence():
    async def run():
        host = MockHost(
            _init_transfers()
            + [
                _exchange(proto.ftdi_purge("tx"), {}),
                _exchange(proto.ftdi_purge("rx"), {}),
                _exchange(proto.stream_send(b"\x00" * 65535), {"sent": 65535}),
                _exchange(proto.stream_send(b"\x00" * 30000), {"sent": 30000}),
                _exchange(proto.stream_send(b"\x01"), {"sent": 1}),
                _exchange(proto.ftdi_purge("tx"), {}),
                _exchange(proto.ftdi_purge("rx"), {}),
            ]
        )
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        request = _request("invoke", {"action_id": "resync", "input": {"attempts": 1}})
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "ok"
        assert result["data"]["result"] == {"attempts": 1, "marker_sent": True}
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_invoke_resync_fails_when_flush_short():
    async def run():
        # The long flush reports fewer than 65534 sent: the attempt fails
        # and the operation reports an uncertain outcome (post-dispatch).
        host = MockHost(
            _init_transfers()
            + [
                _exchange(proto.ftdi_purge("tx"), {}),
                _exchange(proto.ftdi_purge("rx"), {}),
                _exchange(proto.stream_send(b"\x00" * 65535), {"sent": 10}),
            ]
        )
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        request = _request("invoke", {"action_id": "resync", "input": {"attempts": 1}})
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "unknown"
        assert result["error"]["dispatch_state"] == "unknown"
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_resync_rebaselines_the_counter():
    """Fold R4: a successful resync flush re-baselines the counter — the
    next reply's ANY counter succeeds. A locked baseline 0x5C, then the
    flush, then a reply at 0x99 (nowhere near 0x5C+1) must not refuse.
    Both rebaseline sites fire: the flush's embedded init AND the flush's
    own success."""
    async def run():
        host = MockHost(
            [_exact_ack(0x5C)]  # lock the baseline
            + _init_transfers()
            + [
                _exchange(proto.ftdi_purge("tx"), {}),
                _exchange(proto.ftdi_purge("rx"), {}),
                _exchange(proto.stream_send(b"\x00" * 65535), {"sent": 65535}),
                _exchange(proto.stream_send(b"\x00" * 30000), {"sent": 30000}),
                _exchange(proto.stream_send(b"\x01"), {"sent": 1}),
                _exchange(proto.ftdi_purge("tx"), {}),
                _exchange(proto.ftdi_purge("rx"), {}),
                *_register_read_reply(
                    proto.frame_c2(0x0000, 4),
                    bytes.fromhex("C2009900" "00000000"),
                ),
            ]
        )
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        await context.mark_dispatch_started()
        await plugin._ack_write(context)  # baseline locked at 0x5C
        request = _request("invoke", {"action_id": "resync", "input": {"attempts": 1}})
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "ok", result
        context2 = MockContext("op-2", deadline_monotonic=1.0)
        read = _request("read", {"parameter": "status"}, operation_id="op-2")
        result = await plugin.execute(read, context2)
        validate_result(result, read)
        assert result["status"] == "ok", result  # ANY counter: re-baselined
        assert result["data"]["value"] == 0x99
        rebaselines = [
            entry
            for entry in host.evidence
            if entry["kind"] == "logicport.counter_rebaselined"
        ]
        assert len(rebaselines) == 2  # the embedded init + the flush success
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_configure_fpga_refuses_bad_digest(tmp_path):
    async def run():
        fake = tmp_path / "fake.ccf"
        fake.write_bytes(b"not the bitstream")
        host = MockHost([])
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        request = _request(
            "invoke",
            {"action_id": "configure_fpga", "input": {"ccf_path": str(fake)}},
        )
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "error"
        assert result["error"]["code"] == "INVALID_ARGUMENT"
        assert "SHA-256" in result["error"]["message"]
        assert result["error"]["dispatch_state"] == "not_dispatched"
        assert host.transfers == []
        # A missing path is refused before any dispatch too.
        request = _request(
            "invoke",
            {"action_id": "configure_fpga", "input": {"ccf_path": str(tmp_path / "nope.ccf")}},
        )
        result = await plugin.execute(request, context)
        assert result["error"]["code"] == "INVALID_ARGUMENT"
        await plugin.close(context)
    asyncio.run(run())


def test_configure_fpga_requires_a_path():
    async def run():
        import os

        host = MockHost([])
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        saved = os.environ.pop("LOGICPORT_CCF", None)
        try:
            request = _request("invoke", {"action_id": "configure_fpga", "input": {}})
            result = await plugin.execute(request, context)
            assert result["error"]["code"] == "INVALID_ARGUMENT"
            assert host.transfers == []
        finally:
            if saved is not None:
                os.environ["LOGICPORT_CCF"] = saved
            await plugin.close(context)
    asyncio.run(run())


def _synthetic_ccf(tmp_path, slot_len=4096):
    """A minimal valid-shaped .ccf: nine XOR'd header fields and one
    slot 0 of ``slot_len`` bytes. The digest gate is patched open for the
    tests that need the action's live path — the gate itself stays pinned
    against the real constant by test_configure_fpga_refuses_bad_digest."""
    import hashlib

    decoded = [slot_len // 16] + [1] * 7
    checksum = 0
    for value in decoded:
        checksum ^= value
    decoded.append(checksum)
    line = ",".join(str(value ^ proto.CCF_XOR_KEY) for value in decoded) + "\n"
    blob = line.encode("ascii") + bytes(slot_len + 7 * 16)
    path = tmp_path / "synthetic.ccf"
    path.write_bytes(blob)
    return str(path), hashlib.sha256(blob).hexdigest()


def _fpga_pass_script(bitstream, done_reads, chunk_sent=None, stop_after_short_chunk=False):
    """The section 5 pass over MockHost: pulse, DONE-clear check, INIT
    poll, the bitstream chunks, then the DONE pin reads.

    ``chunk_sent`` optionally maps a chunk index to a short ``sent``
    count its stream-send reply reports (the accounting posture); when
    such a reply fires and ``stop_after_short_chunk`` is set, the script
    ends there — the failed pass resets the interface mode itself and
    never reaches the DONE poll."""
    script = [
        _exchange(proto.ftdi_purge("rx"), {}),
        _exchange(proto.ftdi_set_bitmode(proto.BITMODE_RESET, proto.BITMODE_RESET), {}),
        _exchange(proto.ftdi_set_bitmode(proto.BITBANG_MASK_FPGA, proto.BITMODE_BITBANG), {}),
        _frame_send(bytes([proto.FPGA_PULSE_START])),
        _exchange(proto.ftdi_read_pins(), {"data": b"\x00"}),  # DONE cleared
        _frame_send(bytes([proto.FPGA_BITSTREAM_START])),
        _exchange(proto.ftdi_read_pins(), {"data": b"\x08"}),  # INIT high
    ]
    for index, offset in enumerate(range(0, len(bitstream), proto.BITSTREAM_CHUNK_MAX)):
        chunk = bitstream[offset : offset + proto.BITSTREAM_CHUNK_MAX]
        if chunk_sent is not None and index in chunk_sent:
            script.append(_exchange(proto.stream_send(chunk), {"sent": chunk_sent[index]}))
            if stop_after_short_chunk:
                script.append(
                    _exchange(proto.ftdi_set_bitmode(proto.BITMODE_RESET, proto.BITMODE_RESET), {})
                )
                return script
        else:
            script.append(_frame_send(chunk))
    for pins in done_reads:
        script.append(_exchange(proto.ftdi_read_pins(), {"data": pins}))
    script.append(_exchange(proto.ftdi_set_bitmode(proto.BITMODE_RESET, proto.BITMODE_RESET), {}))
    return script


def _pace_clock(host, per_transfer_s=0.05):
    """Advance the mock's manual clock with every transport op: real
    transfers cost wall time, and a never-matching poll ends only when
    elapsed time spends its budget (init_timeout_ms=1 below, so one
    paced transfer already exceeds it)."""
    real_transfer = host.transfer

    async def paced(transaction, context):
        host.advance(per_transfer_s)
        return await real_transfer(transaction, context)

    host.transfer = paced


def _flush_script():
    """The section 3.4 completion flush that follows a successful pass."""
    return _init_transfers() + [
        _exchange(proto.ftdi_purge("tx"), {}),
        _exchange(proto.ftdi_purge("rx"), {}),
        _exchange(proto.stream_send(b"\x00" * 65535), {"sent": 65535}),
        _exchange(proto.stream_send(b"\x00" * 30000), {"sent": 30000}),
        _exchange(proto.stream_send(b"\x01"), {"sent": 1}),
        _exchange(proto.ftdi_purge("tx"), {}),
        _exchange(proto.ftdi_purge("rx"), {}),
    ]


def test_configure_fpga_done_pin_never_asserts_refuses(tmp_path, monkeypatch):
    """A pass whose DONE pin never asserted is a FAILED pass, never an ok
    envelope (#562 wire-truth Task 6): the bitstream went out, so the
    outcome is uncertain (post-dispatch), the completion flush never runs,
    and with the attempt budget spent the action reports TRANSPORT_ERROR
    (the C loader's retry shape — a DONE-less pass is never ok)."""
    ccf_path, digest = _synthetic_ccf(tmp_path)
    monkeypatch.setattr(proto, "CCF_SHA256", digest)

    async def run():
        host = MockHost(_fpga_pass_script(bytes(4096), [b"\x00"]) + _flush_script())
        _pace_clock(host)
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=300.0)
        await plugin.open(_descriptor(), host, context)
        request = _request(
            "invoke",
            {
                "action_id": "configure_fpga",
                "input": {"ccf_path": ccf_path, "attempts": 1, "init_timeout_ms": 1},
            },
        )
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] != "ok", result
        assert result["status"] == "unknown"  # post-dispatch uncertainty
        assert result["error"]["dispatch_state"] == "unknown"
        assert result["error"]["code"] == "TRANSPORT_ERROR"
        assert "did not complete" in result["error"]["message"]
        # The failure fires before the completion flush: the flush
        # exchanges were never transmitted.
        assert host.pending == len(_flush_script())
        await plugin.close(context)
    asyncio.run(run())


def test_configure_fpga_done_asserted_still_ok(tmp_path, monkeypatch):
    """The ok path is unchanged: a DONE-asserted pass completes with the
    resync flush and the full result row (Task 6 preserves the branch)."""
    ccf_path, digest = _synthetic_ccf(tmp_path)
    monkeypatch.setattr(proto, "CCF_SHA256", digest)

    async def run():
        host = MockHost(_fpga_pass_script(bytes(4096), [b"\x10"]) + _flush_script())
        _pace_clock(host)
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=300.0)
        await plugin.open(_descriptor(), host, context)
        request = _request(
            "invoke",
            {
                "action_id": "configure_fpga",
                "input": {"ccf_path": ccf_path, "attempts": 1, "init_timeout_ms": 1},
            },
        )
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "ok", result
        outcome = result["data"]["result"]
        assert outcome["init_asserted"] is True
        assert outcome["done"] is True
        assert outcome["digest_verified"] is True
        assert outcome["resync_ok"] is True
        assert outcome["attempts"] == 1
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_configure_fpga_short_write_propagates(tmp_path, monkeypatch):
    """A transport short-write is a propagated failure, never a swallow
    (#562 wire-truth Task 7). The serial transport owns the invariant —
    it raises ConnectionError("serial write reported N of M bytes")
    whenever sent != len(data) — so the adapter re-checks nothing: the
    exception surfaces as a failure envelope, not a TIMEOUT and never
    ok. The synthetic slot is 4096 bytes (one chunk at
    BITSTREAM_CHUNK_MAX = 10000); the scripted refusal models the
    transport dying mid-chunk on the bitstream's own stream_send."""
    ccf_path, digest = _synthetic_ccf(tmp_path)
    monkeypatch.setattr(proto, "CCF_SHA256", digest)

    async def run():
        script = [
            _exchange(proto.ftdi_purge("rx"), {}),
            _exchange(proto.ftdi_set_bitmode(proto.BITMODE_RESET, proto.BITMODE_RESET), {}),
            _exchange(proto.ftdi_set_bitmode(proto.BITBANG_MASK_FPGA, proto.BITMODE_BITBANG), {}),
            _frame_send(bytes([proto.FPGA_PULSE_START])),
            _exchange(proto.ftdi_read_pins(), {"data": b"\x00"}),  # DONE cleared
            _frame_send(bytes([proto.FPGA_BITSTREAM_START])),
            _exchange(proto.ftdi_read_pins(), {"data": b"\x08"}),  # INIT high
            _exchange(
                proto.stream_send(bytes(4096)),
                ConnectionError("serial write reported 1023 of 1024 bytes"),
            ),
        ]
        host = MockHost(script)
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=300.0)
        await plugin.open(_descriptor(), host, context)
        request = _request(
            "invoke",
            {
                "action_id": "configure_fpga",
                "input": {"ccf_path": ccf_path, "attempts": 1, "init_timeout_ms": 1},
            },
        )
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "unknown", result  # uncertain, never ok
        assert result["error"]["code"] == "TRANSPORT_ERROR", result  # not TIMEOUT
        assert result["error"]["dispatch_state"] == "unknown"
        # The failure stops the pass: nothing else transmits — no second
        # chunk, no completion flush.
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


@pytest.mark.skipif(
    not __import__("pathlib").Path(REAL_CCF).is_file(),
    reason="no hardware capture in this environment: LOGICPORT_CCF names no "
    "hardware-proven LogicPort.ccf, so the end-to-end FPGA-load rehearsal "
    "cannot run and this run carries no end-to-end load evidence",
)
def test_configure_fpga_full_pass_real_bitstream():
    """The proven FPGA-load chain over MockHost: DONE asserted end to end."""
    import hashlib
    from pathlib import Path

    blob = Path(REAL_CCF).read_bytes()
    assert hashlib.sha256(blob).hexdigest() == proto.CCF_SHA256
    header = proto.parse_ccf_header(blob)
    slot = proto.extract_ccf_slot(blob, header, 0)
    bitstream = proto.swap_first_pages(slot)
    chunk_count = (len(bitstream) + proto.BITSTREAM_CHUNK_MAX - 1) // proto.BITSTREAM_CHUNK_MAX

    transfers = []
    for offset in range(0, len(bitstream), proto.BITSTREAM_CHUNK_MAX):
        chunk = bitstream[offset : offset + proto.BITSTREAM_CHUNK_MAX]
        transfers.append(_frame_send(chunk))

    async def run():
        host = MockHost(
            [
                _exchange(proto.ftdi_purge("rx"), {}),
                _exchange(proto.ftdi_set_bitmode(0x00, 0x00), {}),
                _exchange(proto.ftdi_set_bitmode(0x07, 0x01), {}),
                _frame_send(b"\x00"),
                _exchange(proto.ftdi_read_pins(), {"data": b"\x00"}),  # DONE cleared
                _frame_send(b"\x04"),
                _exchange(proto.ftdi_read_pins(), {"data": b"\x08"}),  # INIT high
            ]
            + transfers
            + [
                _exchange(proto.ftdi_read_pins(), {"data": b"\x10"}),  # DONE high
                _exchange(proto.ftdi_set_bitmode(0x00, 0x00), {}),
            ]
            + _init_transfers()
            + [
                _exchange(proto.ftdi_purge("tx"), {}),
                _exchange(proto.ftdi_purge("rx"), {}),
                _exchange(proto.stream_send(b"\x00" * 65535), {"sent": 65535}),
                _exchange(proto.stream_send(b"\x00" * 30000), {"sent": 30000}),
                _exchange(proto.stream_send(b"\x01"), {"sent": 1}),
                _exchange(proto.ftdi_purge("tx"), {}),
                _exchange(proto.ftdi_purge("rx"), {}),
            ]
        )
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=300.0)
        await plugin.open(_descriptor(), host, context)
        request = _request(
            "invoke",
            {"action_id": "configure_fpga", "input": {"ccf_path": REAL_CCF, "attempts": 1}},
        )
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "ok", result
        outcome = result["data"]["result"]
        assert outcome["digest_verified"] is True
        assert outcome["checksum_ok"] is True
        assert outcome["pages_swapped"] is True
        assert outcome["init_asserted"] is True
        assert outcome["done"] is True
        assert outcome["bitstream_bytes"] == len(bitstream)
        assert outcome["attempts"] == 1
        assert chunk_count > 100  # 1,057,840 bytes in <=10000-byte chunks
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_invoke_read_samples_exact_exchange():
    """The vectors.json demo conversation: tp=0, 8 columns, one run column.

    Every read is a headered reply (#562): the trigger position at
    counter 0x60, then the ten plane chunks at 0x61..0x6A.
    """
    async def run():
        script = _register_read_reply(
            proto.frame_c2(0x000A, 4), _reply_bytes(b"\x00\x00\xe0\xe0", 0x60)
        )
        counter = 0x61
        for address, payload in (
            # page A planes: D0-7 / D8-15 / D16-23 / D24-31 / flags
            (0x0102, b"\x00\x00\x00\x00"),
            (0x2001, b"\xff\xff\xff\xff"),
            (0x3001, b"\x0f\x0f\x0f\x0f"),
            (0x4001, b"\x00\x00\x00\x00"),
            (0x9001, b"\x00\x00\x00\x00"),
            # page B planes
            (0x0502, b"\x05\x05\x05\x05"),
            (0x2401, b"\x00\x5a\x5a\x5a"),
            (0x3401, b"\x00\x00\x00\x00"),
            (0x4401, b"\x00\xff\xff\xff"),
            (0x9401, b"\x08\x00\x00\x00"),
        ):
            script += _register_read_reply(
                proto.frame_c2(address, 4), _reply_bytes(payload, counter)
            )
            counter += 1
        host = MockHost(script)
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        request = _request(
            "invoke",
            {"action_id": "read_samples", "input": {"column_count": 8, "trigger_word": 0}},
        )
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "ok", result
        data = result["data"]["result"]
        assert data["column_count"] == 8
        assert data["page_base"] == 1
        assert data["trigger_position"] == 0
        assert len(data["columns"]) == 8
        # Column 0 (page A byte 0: planes 00 ff 0f 00, flag 00): D0-7 HIGH,
        # D8-15 LOW, D16-19 LOW, D20-23 HIGH, D24-31 HIGH, clocks HIGH.
        expected_c0 = 0xFF | (0xF0 << 16) | (0xFF << 24) | (0b11 << 32)
        assert data["columns"][0] == expected_c0
        # Column 1's flag byte is 0x08: the one run column, count 5
        # (page B plane bytes 05 00 00 00 + flag low bits).
        assert data["runs"] == [0, 5, 0, 0, 0, 0, 0, 0]
        assert data["columns"][1] == expected_c0  # the run carries
        assert data["total_samples"] == 7 + 5  # 7 sample columns + a run of 5
        assert data["trigger_column"] == proto.trigger_column_raw(8, 0)
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_invoke_read_samples_rejects_bad_input():
    async def run():
        host = MockHost([])
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        for inputs in (
            {},
            {"column_count": 0},
            {"column_count": 2048},
            {"column_count": 8, "bogus": 1},
        ):
            request = _request("invoke", {"action_id": "read_samples", "input": inputs})
            result = await plugin.execute(request, context)
            assert result["status"] == "error"
            assert result["error"]["code"] == "INVALID_ARGUMENT"
            assert result["error"]["dispatch_state"] == "not_dispatched"
        assert host.transfers == []
        await plugin.close(context)
    asyncio.run(run())


def test_read_samples_records_the_capture_gate_hook_once():
    """No gate source is proven (#562): the drain path records the honest
    ungated state — exactly one capture_gate evidence entry, source None —
    once per read_samples invocation."""
    async def run():
        script = _register_read_reply(
            proto.frame_c2(0x000A, 4), _reply_bytes(b"\x00\x00\xe0\xe0", 0x60)
        )
        counter = 0x61
        for address in (
            0x0102, 0x2001, 0x3001, 0x4001, 0x9001,  # page A planes
            0x0502, 0x2401, 0x3401, 0x4401, 0x9401,  # page B planes
        ):
            script += _register_read_reply(
                proto.frame_c2(address, 4), _reply_bytes(b"\x00" * 4, counter)
            )
            counter += 1
        host = MockHost(script)
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        request = _request(
            "invoke",
            {"action_id": "read_samples", "input": {"column_count": 8}},
        )
        result = await plugin.execute(request, context)
        assert result["status"] == "ok", result
        gates = [
            entry
            for entry in host.evidence
            if entry == {"kind": "logicport.capture_gate", "source": None}
        ]
        assert gates == [{"kind": "logicport.capture_gate", "source": None}]
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_vectors_match_protocol_builders():
    """Every vectors.json request frame is a protocol builder's exact bytes.

    The vectors script the LIVE mock poller's demand cycle in the
    descriptor's read-only walk order (fold R5): status, then the four
    frequency slots (select write, ready gate, count), then
    trigger_position — THREE full cycles, so a three-cycle replay through
    the mock session never self-wedges. Every reply matches the
    hardware-proven grammar (#562): the 4-byte header plus the requested
    payload, and 3-byte write ACKs. The command counter advances +1 across
    the whole capture (one counter sequences reads and writes), 0x19
    through 0x42; three identical 14-row periods make the cycle unit
    derivable, so the mock replays exactly this descriptor-order cycle.
    """
    vectors = json.loads(files("logicport_plugin").joinpath("vectors.json").read_text())
    assert vectors["transaction_dialect"] == "send_receive"
    assert vectors["encoding"] == "hex"
    rows = vectors["exchanges"]
    # Descriptor RO walk: status first, frequency a..d, trigger position last.
    cycle = [proto.frame_c2(0x0000, 4).hex()]
    for channel in range(4):
        cycle.append(proto.frame_c1(proto.wire_address(proto.REG_FREQ_SELECT), proto.payload_byte(channel)).hex())
        cycle.append(proto.frame_c2(proto.wire_address(proto.REG_FREQ_SELECT), 1).hex())
        cycle.append(proto.frame_c2(proto.wire_address(proto.REG_FREQ_COUNT), 4).hex())
    cycle.append(proto.frame_c2(0x000A, 4).hex())
    assert len(rows) == 3 * len(cycle)
    assert [row["request"] for row in rows] == cycle * 3
    # The first exchange is the hand-recorded addr_probe capture
    # (#562): an idle status read replying counter 0x19, aux 0x00, four
    # zero payload bytes — the unarmed-device read of record.
    assert rows[0]["response"].lower() == "c200190000000000"
    # The frequency counts decode from the reply payload (count x 10) to
    # the fixture Hz values; the 4-byte reply header is stripped first.
    hz = [int.from_bytes(bytes.fromhex(row["response"])[4:], "big") * 10
          for row in rows if row["name"].endswith("-count")]
    assert hz == [1000000, 125000, 0, 10000000] * 3


def test_quiet_lifecycle():
    descriptor = _descriptor()
    asyncio.run(check_lifecycle(create_plugin, descriptor))


def test_no_transmit_before_dispatch():
    async def run():
        base = _request("read", {"parameter": "status"})
        for reason in ("cancelled", "expired", "bad_arguments", "wrong_context"):
            host = MockHost([])
            plugin = create_plugin()
            context = MockContext("op-1", deadline_monotonic=1.0)
            await plugin.open(_descriptor(), host, context)
            request = dict(base)
            if reason == "cancelled":
                context.cancel()
            elif reason == "expired":
                host.advance(1.0)
            elif reason == "bad_arguments":
                # Schema-valid shape, unknown parameter: refused pre-dispatch.
                request["arguments"] = {"parameter": "bogus_param"}
            else:
                context.operation_id = "other"
            result = await plugin.execute(request, context)
            validate_result(result, request)
            assert result["status"] == "error", (reason, result)
            assert result["error"]["dispatch_state"] == "not_dispatched"
            assert host.transfers == []
            await plugin.close(MockContext("cleanup", deadline_monotonic=2.0))
    asyncio.run(run())


def test_uncertain_outcome_after_dispatch():
    async def run():
        for response in (
            ConnectionError("lost"),
            TimeoutError("expired"),
            RuntimeError("host"),
            {"data": b""},  # a receive with no bytes: PROTOCOL_ERROR
        ):
            host = MockHost(
                [
                    _frame_send(proto.frame_c2(0x0000, 4)),
                    # The status reply is the proven count+4 frame (#562).
                    _exchange(proto.stream_receive(8, 8), response),
                ]
            )
            plugin = create_plugin()
            context = MockContext("op-1", deadline_monotonic=1.0)
            await plugin.open(_descriptor(), host, context)
            request = _request("read", {"parameter": "status"})
            result = await plugin.execute(request, context)
            validate_result(result, request)
            assert result["status"] == "unknown"
            assert result["error"]["dispatch_state"] == "unknown"
            assert context.dispatched
            host.assert_complete()
            await plugin.close(context)
    asyncio.run(run())


def test_unknown_verb_and_envelope():
    async def run():
        host = MockHost([])
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        for request in (
            {"operation_id": "op-1", "verb": "capture", "arguments": {}},
            {"operation_id": "op-1", "verb": "read"},  # missing arguments
            {"operation_id": "op-1", "verb": "read", "arguments": {}, "extra": 1},
            {"operation_id": "op-1", "verb": "invoke", "arguments": {"action_id": "nope", "input": {}}},
        ):
            result = await plugin.execute(request, context)
            assert result["status"] == "error"
            assert result["error"]["code"] in ("UNSUPPORTED", "INVALID_ARGUMENT")
            assert result["error"]["dispatch_state"] == "not_dispatched"
        assert host.transfers == []
        await plugin.close(context)
    asyncio.run(run())


def test_close_is_idempotent():
    async def run():
        host = MockHost([])
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        await plugin.close(context)
        await plugin.close(context)
        assert host.closed
    asyncio.run(run())


def test_fresh_instance_required():
    async def run():
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), MockHost([]), context)
        with pytest.raises(RuntimeError):
            await plugin.open(_descriptor(), MockHost([]), context)
    asyncio.run(run())


# --- Fold-in round 2 (RED first): frequency reads, trigger position,
# sequencer, and the logic_analyser class chain. ---


def _freq_script(channel, base=0x4F):
    # Headered replies with a monotone counter (#562): the select ACK at
    # `base`, the gate at base+1, the count read at base+2 — one counter
    # across the write and both reads. `base` lets a multi-sequence test
    # continue one Plugin's counter across its own exchanges.
    return [
        _frame_send(proto.frame_c1(proto.wire_address(proto.REG_FREQ_SELECT), proto.payload_byte(channel))),
        _exact_ack(base),
        *_register_read_reply(
            proto.frame_c2(proto.wire_address(proto.REG_FREQ_SELECT), 1),
            _reply_bytes(b"\x00", base + 1),  # payload 0x00 = count ready
        ),
        *_register_read_reply(
            proto.frame_c2(proto.wire_address(proto.REG_FREQ_SELECT) + 1, 4),
            _reply_bytes(b"\x00\x01\xe2\x40", base + 2),  # BE dword 123456
        ),
    ]


@pytest.mark.parametrize("slot,channel", [("a", 0), ("b", 1), ("c", 2), ("d", 3)])
def test_read_frequency_slot(slot, channel):
    async def run():
        host = MockHost(_freq_script(channel))
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        request = _request("read", {"parameter": f"frequency_{slot}"})
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "ok", result
        assert result["data"]["parameter"] == f"frequency_{slot}"
        assert result["data"]["value"] == 1234560  # count x 10
        assert result["data"]["unit"] == "Hz"
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_read_frequency_paces_not_ready_gate_polls(monkeypatch):
    """A not-ready gate retries at the poll floor: attempts - 1 paced waits
    of FREQ_POLL_INTERVAL_MIN_MS / 1000 s (#562 wire-truth Task 5)."""
    calls = []

    async def _record_sleep(seconds):
        calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _record_sleep)

    async def run():
        host = MockHost([
            # select write -> 3-byte ACK
            _frame_send(proto.frame_c1(proto.wire_address(proto.REG_FREQ_SELECT), proto.payload_byte(0))),
            _exact_ack(0x4F),
            # first gate read: NOT ready (payload 0x01), second: ready (0x00)
            *_register_read_reply(
                proto.frame_c2(proto.wire_address(proto.REG_FREQ_SELECT), 1),
                _reply_bytes(b"\x01", 0x50),
            ),
            *_register_read_reply(
                proto.frame_c2(proto.wire_address(proto.REG_FREQ_SELECT), 1),
                _reply_bytes(b"\x00", 0x51),
            ),
            *_register_read_reply(
                proto.frame_c2(proto.wire_address(proto.REG_FREQ_SELECT) + 1, 4),
                _reply_bytes(b"\x00\x01\xe2\x40", 0x52),  # BE dword 123456
            ),
        ])
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        request = _request("read", {"parameter": "frequency_a"})
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "ok", result
        assert result["data"]["value"] == 1234560  # count x 10
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())

    # Two gate attempts -> exactly one paced wait of 0.1 s between them.
    assert calls == [proto.FREQ_POLL_INTERVAL_MIN_MS / 1000]


def test_read_frequency_rejects_unknown():
    async def run():
        host = MockHost([])
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        request = _request("read", {"parameter": "frequency_e"})
        result = await plugin.execute(request, context)
        assert result["error"]["code"] == "INVALID_ARGUMENT"
        await plugin.close(context)
    asyncio.run(run())


def test_invoke_set_trigger_position():
    async def run():
        frames = proto.trigger_position_frames(1, 2, 100)
        host = MockHost(
            # Two writes, two ACKs: the counters step +1 (#562).
            [_frame_send(frames[0]), _exact_ack(0x5B), _frame_send(frames[1]), _exact_ack(0x5C)]
        )
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        request = _request(
            "invoke",
            {
                "action_id": "set_trigger_position",
                "input": {"position": 100, "mode_flag": 1, "clock_type": 2},
            },
        )
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "ok", result
        assert result["data"]["result"] == {
            "position": 100, "word_a": 135, "word_b": 70, "frames_sent": 2,
        }
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def _sequencer_input(segment, state_terms_active):
    terms = [0] * 34
    terms[0] = 1  # one active term keeps the condition-mask rows in the plan
    return {
        "segment": segment,
        "terms": terms,
        "state_terms_active": state_terms_active,
        "term_type": 1,
        "dwell1": 0,
        "condition_mask_a": 1 << 33,
        "condition_mask_b": 0,
        "state_mask_a": 0,
        "state_mask_b": 0,
        "control": 0,
        "match_mask": 0xFF,
        "param1": 100,
        "param1_dword": True,
        "param2": 7,
        "param2_dword": False,
        "flag": 0,
        "match_and": 0,
        "control2": 0,
    }


def test_invoke_configure_sequencer():
    async def run():
        plan = proto.sequencer_write_plan(**_sequencer_input(0, True))
        # Fifteen writes, fifteen ACKs: the counters step +1 each (#562).
        script = []
        counter = 0x40
        for register, payload, _ in plan:
            script.append(_frame_send(proto.frame_c1(proto.wire_address(register), payload)))
            script.append(_exact_ack(counter))
            counter += 1
        host = MockHost(script)
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        request = _request(
            "invoke",
            {"action_id": "configure_sequencer", "input": _sequencer_input(0, True)},
        )
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "ok", result
        assert result["data"]["result"]["writes_sent"] == 15
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def _class_configure_input():
    return {
        "configuration_id": "cfg-1",
        "channels": [{"channel": "d0", "threshold_v": 1.5}],
        "sample_rate_hz": 1000000.0,
        "sample_count": 2048,
        "trigger": {"kind": "immediate"},
    }


def test_class_configure_refuses_open_destinations():
    async def run():
        # RULING (coordinator, 2026-10-12): effective_configuration on
        # undecoded destinations would claim writes we cannot evidence —
        # configure refuses loudly until the threshold/rate destinations
        # for the CLASS path land. (The standalone threshold write is now
        # decoded and served by `write threshold_v`.)
        host = MockHost([])
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        request = _request(
            "invoke",
            {"action_id": "otdp.logic_analyser.configure/1.0.0", "input": _class_configure_input()},
        )
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "error", result
        assert result["error"]["code"] == "UNSUPPORTED"
        assert result["error"]["dispatch_state"] == "not_dispatched"
        assert host.transfers == []
        # Malformed class input still refuses as INVALID_ARGUMENT first.
        bad = _request(
            "invoke",
            {"action_id": "otdp.logic_analyser.configure/1.0.0", "input": {"configuration_id": "x"}},
        )
        result = await plugin.execute(bad, context)
        assert result["error"]["code"] == "INVALID_ARGUMENT"
        await plugin.close(context)
    asyncio.run(run())


def _seed_class_state(plugin, configuration_id="cfg-1", acquisition_id="acq-1"):
    """Seed the class registries directly.

    The class configure entry refuses on the undecoded destinations
    (ruling 2026-10-12); these tests cover the arm/fetch/abort
    implementations against an explicitly seeded session state — the code
    activates unchanged when the destinations decode.
    """
    plugin._configurations[configuration_id] = _class_configure_input()
    plugin._acquisitions[acquisition_id] = configuration_id


def test_class_arm_refuses_without_a_proven_state():
    async def run():
        # Late fold R19 (#562 battery, lane B): the vendored otdp 0.2.2 arm
        # output_schema enumerates armed|running|complete with
        # additionalProperties:false, so "unknown" is schema-illegal and
        # every conformant host rejects it. The codebase precedent
        # (_class_configure) refuses UNSUPPORTED rather than emit an
        # unprovable result; arm does the same until a state source is
        # proven.
        host = MockHost([])  # pre-dispatch refusal: no device traffic
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        _seed_class_state(plugin)
        request = _request(
            "invoke",
            {
                "action_id": "otdp.logic_analyser.arm/1.0.0",
                "input": {"configuration_id": "cfg-1", "acquisition_id": "acq-1", "max_duration_ms": 1000},
            },
        )
        result = await plugin.execute(request, context)
        assert result["status"] == "error", result
        assert result["error"]["code"] == "UNSUPPORTED"
        assert result["error"]["dispatch_state"] == "not_dispatched"
        assert "proven wire source" in result["error"]["message"]
        assert host.transfers == []
        await plugin.close(context)
    asyncio.run(run())


def test_class_fetch_builds_a_conforming_dataset():
    async def run():
        # The capture gate is unproven (#562): fetch reads the status
        # register once for the counter/aux evidence (the hand-recorded
        # golden 0x5A), reads the trigger position (0x07F7 -> N=8, chunk 4)
        # at counter 0x5B, then the ten headered plane chunks 0x5C..0x65.
        script = [
            *_register_read_reply(
                proto.frame_c2(0x0000, 4), bytes.fromhex("C2005A00" "00000000")
            ),
            *_register_read_reply(
                proto.frame_c2(0x000A, 4), _reply_bytes(b"\x07\xf7\xe0\xe0", 0x5B)
            ),
        ]
        counter = 0x5C
        for address, payload in (
            # page A planes at the tp page, then page B (+ 0x400 stride)
            (0x04f9, b"\x00\x00\x00\x00"),
            (0x23f8, b"\xff\xff\xff\xff"),
            (0x33f8, b"\x00\x00\x00\x00"),
            (0x43f8, b"\x00\x00\x00\x00"),
            (0x93f8, b"\x00\x00\x00\x00"),
            (0x08f9, b"\xff\xff\xff\xff"),
            (0x27f8, b"\xff\xff\xff\xff"),
            (0x37f8, b"\xff\xff\xff\xff"),
            (0x47f8, b"\xff\xff\xff\xff"),
            (0x97f8, b"\x06\x06\x06\x06"),
        ):
            script += _register_read_reply(
                proto.frame_c2(address, 4), _reply_bytes(payload, counter)
            )
            counter += 1
        host = MockHost(script)
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=5.0, dataset_id="ds-1")
        await plugin.open(_descriptor(), host, context)
        _seed_class_state(plugin)
        request = _request(
            "invoke",
            {
                "action_id": "otdp.logic_analyser.fetch/1.0.0",
                "input": {"acquisition_id": "acq-1", "max_bytes": 1000000, "allow_partial": False},
            },
        )
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "ok", result
        dataset = result["data"]["result"]
        from benchweave_sdk.validation import validate as validate_doc

        validate_doc(dataset, "otdp/0.2.4/otdp-measurement.schema.json")
        assert dataset["dataset_id"] == "ds-1"
        assert dataset["kind"] == "digital_trace"
        assert dataset["acquisition_id"] == "acq-1"
        assert dataset["status"] == "complete"
        # Fold R6: completion is NOT proven — the gate hook's source is
        # None. The field rides the dataset's free-form context block (the
        # OTDP dataset schema closes its top-level keys; disclosed in the
        # fold report).
        assert dataset["context"]["completion_proven"] is False
        assert dataset["axes"][0]["unit"] == "s"
        lanes = {row["id"]: row for row in dataset["variables"]}
        assert len(lanes) == 34
        # Even columns (page A: planes 00 ff 00 00) -> D8-15 LOW, rest
        # HIGH; odd columns (page B stored 1 everywhere) -> every lane LOW.
        assert lanes["ch_d0"]["values"] == ["1", "0"] * 4
        assert lanes["ch_d8"]["values"] == ["0"] * 8
        # Odd columns read CLK bits from flag bits 5/6 — 0x06 sets neither,
        # so both clocks read HIGH on the odd (page B) columns.
        assert lanes["ch_clk1"]["values"] == ["1"] * 8
        assert lanes["ch_clk2"]["values"] == ["1"] * 8
        assert len(lanes["ch_d0"]["values"]) == 8
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_class_fetch_requires_host_dataset_id_and_arm():
    async def run():
        host = MockHost([])
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)  # dataset_id None
        await plugin.open(_descriptor(), host, context)
        request = _request(
            "invoke",
            {
                "action_id": "otdp.logic_analyser.fetch/1.0.0",
                "input": {"acquisition_id": "acq-1", "max_bytes": 1000, "allow_partial": False},
            },
        )
        result = await plugin.execute(request, context)
        assert result["error"]["code"] == "INVALID_ARGUMENT"
        assert host.transfers == []
        await plugin.close(context)
    asyncio.run(run())


def test_class_fetch_records_the_capture_gate_hook_once():
    """The fetch drain path carries the same honest hook (#562): exactly
    one capture_gate evidence entry, source None, per fetch invocation —
    the status_aux evidence beside it is a different kind."""
    async def run():
        script = [
            *_register_read_reply(
                proto.frame_c2(0x0000, 4), bytes.fromhex("C2005A00" "00000000")
            ),
            *_register_read_reply(
                proto.frame_c2(0x000A, 4), _reply_bytes(b"\x07\xf7\xe0\xe0", 0x5B)
            ),
        ]
        counter = 0x5C
        for address in (
            0x04f9, 0x23f8, 0x33f8, 0x43f8, 0x93f8,  # page A planes
            0x08f9, 0x27f8, 0x37f8, 0x47f8, 0x97f8,  # page B planes
        ):
            script += _register_read_reply(
                proto.frame_c2(address, 4), _reply_bytes(b"\x00" * 4, counter)
            )
            counter += 1
        host = MockHost(script)
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=5.0, dataset_id="ds-1")
        await plugin.open(_descriptor(), host, context)
        _seed_class_state(plugin)
        request = _request(
            "invoke",
            {
                "action_id": "otdp.logic_analyser.fetch/1.0.0",
                "input": {"acquisition_id": "acq-1", "max_bytes": 1000000, "allow_partial": False},
            },
        )
        result = await plugin.execute(request, context)
        assert result["status"] == "ok", result
        gates = [
            entry
            for entry in host.evidence
            if entry == {"kind": "logicport.capture_gate", "source": None}
        ]
        assert gates == [{"kind": "logicport.capture_gate", "source": None}]
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_class_abort_sends_stop():
    async def run():
        # Fold R3: no C3 ACK is parsed — a quiet line is the acceptable
        # drain outcome, and the operation still succeeds.
        host = MockHost(
            [
                _frame_send(proto.frame_c3(0)),
                _exchange(proto.stream_receive(8, None), {"data": b""}),
            ]
        )
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        request = _request(
            "invoke",
            {"action_id": "otdp.logic_analyser.abort/1.0.0", "input": {"acquisition_id": "acq-1"}},
        )
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "ok", result
        assert result["data"]["result"] == {"acquisition_id": "acq-1", "state": "aborted"}
        assert host.evidence == [{"kind": "logicport.c3_drain", "bytes": 0}]
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_threshold_write_then_status_read_share_one_counter():
    """Fold R13: two operations on ONE plugin instance — the threshold
    write's ACK locks the baseline, the status read continues it — the same
    counter sequences reads and writes across operations."""
    async def run():
        code = proto.threshold_dac_code(0.0)
        host = MockHost(
            [
                _frame_send(
                    proto.frame_c1(
                        proto.wire_address(proto.REGISTER_THRESHOLD_DAC),
                        proto.payload_word(code),
                    )
                ),
                _exact_ack(0x5C),
                *_register_read_reply(
                    proto.frame_c2(0x0000, 4),
                    bytes.fromhex("C2005D00" "00000000"),
                ),
            ]
        )
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        write = _request("write", {"parameter": "threshold_v", "value": 0.0})
        result = await plugin.execute(write, context)
        validate_result(result, write)
        assert result["status"] == "ok", result
        context2 = MockContext("op-2", deadline_monotonic=1.0)
        read = _request("read", {"parameter": "status"}, operation_id="op-2")
        result = await plugin.execute(read, context2)
        validate_result(result, read)
        assert result["status"] == "ok", result  # 0x5D = 0x5C + 1: no gap
        assert result["data"]["value"] == 0x5D
        assert plugin._last_counter == 0x5D
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


# --- Sibling wire-truth round (main #562): typed action, serialization,
# poll floor, FPGA retry/accounting — ported into the grammar-consuming
# suite. ---------------------------------------------------------------


def test_invoke_read_counter_channel():
    """The typed counter read: the execution contract's sanctioned path
    for a state-changing read (a core read verb declaring state_change is
    excluded from procedures; this action is what an allow rule admits).
    One locked sweep of all four channels — the device's round-robin
    unit, the vectors' exact cycle shape. The four sequences share ONE
    Plugin's command counter, so each channel's script continues the
    last (0x4F..0x51, 0x52..0x54, 0x55..0x57, 0x58..0x5A)."""
    async def run():
        host = MockHost(
            _freq_script(0, 0x4F)
            + _freq_script(1, 0x52)
            + _freq_script(2, 0x55)
            + _freq_script(3, 0x58)
        )
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=20.0)
        await plugin.open(_descriptor(), host, context)
        request = _request("invoke", {"action_id": "read_counter", "input": {}})
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "ok", result
        assert result["data"]["action_id"] == "read_counter"
        assert result["data"]["result"] == {
            "frequencies_hz": {
                "frequency_a": 1234560,
                "frequency_b": 1234560,
                "frequency_c": 1234560,
                "frequency_d": 1234560,
            },
            "unit": "Hz",
            "quality": "valid",
            "source": "device",
        }
        host.assert_complete()
        await plugin.close(context)
        # Stray inputs refuse before any transmit.
        host = MockHost([])
        plugin = create_plugin()
        context = MockContext("op-2", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        for bad in ({"channel": "a"}, {"bogus": 1}):
            request = _request("invoke", {"action_id": "read_counter", "input": bad})
            result = await plugin.execute(request, context)
            validate_result(result, request)
            assert result["error"]["code"] == "INVALID_ARGUMENT", bad
            assert result["error"]["dispatch_state"] == "not_dispatched"
        assert host.transfers == []
        await plugin.close(context)
    asyncio.run(run())


class _ParkOnNthHost(MockHost):
    """Park the caller before serving the Nth transfer until released.

    MockHost serves every transfer without suspending, so two concurrent
    operations run atomically back to back and never interleave. Parking
    read A inside its gate exchange — on a future only the test resolves —
    is the deterministic interleaving point this test owns: read B runs to
    its next await while A is genuinely suspended.
    """

    def __init__(self, exchanges, park_before):
        super().__init__(exchanges)
        self._served = 0
        self._park_before = park_before
        self._release: asyncio.Future | None = None

    async def transfer(self, transaction, context):
        self._served += 1
        if self._served == self._park_before and self._release is not None:
            await self._release
        return await super().transfer(transaction, context)


def test_counter_reads_are_serialized():
    """One read-select-read sequence cannot interleave with another.

    The counter is a multiplexer: an interleaved pair would tag the wrong
    channel's count as ``quality: "valid"``. The adapter must serialize the
    whole select/gate/count sequence (the counter lock), and the second
    sequence's command counter continues the first's — no gap refusal on
    the serialized handoff.
    """

    async def run():
        host = _ParkOnNthHost(_freq_script(0, 0x4F) + _freq_script(1, 0x52), park_before=3)
        release = asyncio.get_running_loop().create_future()
        host._release = release
        plugin = create_plugin()
        context_a = MockContext("op-a", deadline_monotonic=20.0)
        await plugin.open(_descriptor(), host, context_a)
        context_b = MockContext("op-b", deadline_monotonic=20.0)
        task_a = asyncio.create_task(
            plugin.execute(
                _request("read", {"parameter": "frequency_a"}, operation_id="op-a"), context_a
            )
        )
        while host._served < 3:
            await asyncio.sleep(0)  # A is parked inside its first gate exchange
        task_b = asyncio.create_task(
            plugin.execute(
                _request("read", {"parameter": "frequency_b"}, operation_id="op-b"), context_b
            )
        )
        await asyncio.sleep(0)  # B runs to its next await (its lock acquire)
        release.set_result(None)  # A resumes and finishes its sequence
        result_a, result_b = await asyncio.gather(task_a, task_b)
        assert result_a["status"] == "ok", result_a
        assert result_b["status"] == "ok", result_b
        assert result_a["data"]["parameter"] == "frequency_a"
        assert result_b["data"]["parameter"] == "frequency_b"
        assert result_a["data"]["value"] == 1234560
        assert result_b["data"]["value"] == 1234560
        host.assert_complete()
        await plugin.close(context_a)
    asyncio.run(run())


def test_counter_gate_poll_respects_interval_floor(monkeypatch):
    """A not-ready gate re-polls only after FREQ_POLL_INTERVAL_MIN_MS.

    The record's confirmed floor: counter polls wait longer than 75 ms
    (adapter floor 100 ms). Hammering the gate register in a tight loop is
    the unwired-constant defect. The pacing runs through the injectable
    ``_pace_wait``; the exchanges parse through the proven reply grammar.
    """

    async def run():
        import logicport_plugin.adapter as adapter_module

        host = MockHost(
            [
                _frame_send(proto.frame_c1(proto.wire_address(proto.REG_FREQ_SELECT), proto.payload_byte(0))),
                _exact_ack(0x4F),
                *_register_read_reply(
                    proto.frame_c2(proto.wire_address(proto.REG_FREQ_SELECT), 1),
                    _reply_bytes(b"\x01", 0x50),  # count not settled
                ),
                *_register_read_reply(
                    proto.frame_c2(proto.wire_address(proto.REG_FREQ_SELECT), 1),
                    _reply_bytes(b"\x00", 0x51),  # ready
                ),
                *_register_read_reply(
                    proto.frame_c2(proto.wire_address(proto.REG_FREQ_SELECT) + 1, 4),
                    _reply_bytes(b"\x00\x01\xe2\x40", 0x52),
                ),
            ]
        )
        waits: list[float] = []

        async def fake_wait(seconds):
            waits.append(seconds)
            host.advance(seconds)

        monkeypatch.setattr(adapter_module, "_pace_wait", fake_wait, raising=False)
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=20.0)
        await plugin.open(_descriptor(), host, context)
        request = _request("read", {"parameter": "frequency_a"})
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "ok", result
        assert waits, "no pace wait requested between gate polls: the floor constant is unwired"
        floor_s = proto.FREQ_POLL_INTERVAL_MIN_MS / 1000.0
        assert waits == [floor_s]
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


class _AdvancingPinsHost(MockHost):
    """Advance the clock before every pin poll so a failing poll exhausts.

    ``_poll_pins`` budgets ``timeout_ms`` on the host clock; advancing 2 s
    before each pin read makes every NOT-matching poll return False after
    exactly one read (the default poll budget is 1000 ms).
    """

    async def transfer(self, transaction, context):
        if transaction.get("op") == "read_pins":
            self.advance(2.0)
        return await super().transfer(transaction, context)


def test_configure_fpga_retries_when_done_never_asserts(monkeypatch, tmp_path):
    """A pass whose DONE never asserts is a FAILED pass, not ok-done-false.

    The C loader retries the whole sequence when DONE does not assert
    (record section 5); the adapter must do the same (#562 Task 6's
    never-ok invariant with the landed retry shape).
    """
    path, digest = _synthetic_ccf(tmp_path, slot_len=20000)
    monkeypatch.setattr(proto, "CCF_SHA256", digest)
    blob = __import__("pathlib").Path(path).read_bytes()
    header = proto.parse_ccf_header(blob)
    bitstream = proto.swap_first_pages(proto.extract_ccf_slot(blob, header, 0))

    async def run():
        host = _AdvancingPinsHost(
            _fpga_pass_script(bitstream, [b"\x00"])  # pass 1: DONE never
            + _fpga_pass_script(bitstream, [b"\x10"])  # pass 2: DONE asserts
            + _flush_script()
        )
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=600.0)
        await plugin.open(_descriptor(), host, context)
        request = _request(
            "invoke",
            {"action_id": "configure_fpga", "input": {"ccf_path": path, "attempts": 2}},
        )
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "ok", result
        outcome = result["data"]["result"]
        assert outcome["done"] is True
        assert outcome["attempts"] == 2, "a DONE-less pass must fail and retry, not report ok"
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_configure_fpga_fails_a_short_bitstream_send(monkeypatch, tmp_path):
    """A chunk the host reports short is a failed pass, never a load that
    presses on to the DONE poll as if the bitstream had been delivered —
    the sent-count accounting posture (the transport-raises posture is
    pinned separately by test_configure_fpga_short_write_propagates)."""
    path, digest = _synthetic_ccf(tmp_path, slot_len=20000)
    monkeypatch.setattr(proto, "CCF_SHA256", digest)
    blob = __import__("pathlib").Path(path).read_bytes()
    header = proto.parse_ccf_header(blob)
    bitstream = proto.swap_first_pages(proto.extract_ccf_slot(blob, header, 0))
    chunk_count = (len(bitstream) + proto.BITSTREAM_CHUNK_MAX - 1) // proto.BITSTREAM_CHUNK_MAX
    assert chunk_count == 2

    async def run():
        # Chunk 2 reports 5000 of 10000 bytes sent: the pass fails at the
        # chunk, resets the interface mode, and never polls DONE.
        host = _AdvancingPinsHost(
            _fpga_pass_script(
                bitstream, [b"\x10"], chunk_sent={1: 5000}, stop_after_short_chunk=True
            )
        )
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=600.0)
        await plugin.open(_descriptor(), host, context)
        request = _request(
            "invoke",
            {"action_id": "configure_fpga", "input": {"ccf_path": path, "attempts": 1}},
        )
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "unknown", result
        assert result["error"]["code"] == "TRANSPORT_ERROR"
        assert result["error"]["dispatch_state"] == "unknown"
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())

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


def _quiet_ack():
    return _exchange(proto.stream_receive(8, None), {"data": b""})


def _frame_send(frame):
    return _exchange(proto.stream_send(frame), {})


def _register_read(frame, payload):
    return [
        _frame_send(frame),
        _exchange(proto.stream_receive(4, 4), {"data": payload}),
    ]


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


def test_read_status_hardware_capture():
    async def run():
        # A constructed fixture, NOT a hardware capture: the 2026-10-12
        # four-byte E0 read is withdrawn evidence (its tool truncates at
        # four bytes; record section 10), and the status byte is not
        # hardware-decoded. The value rehearses the static §5c decode.
        host = MockHost(_register_read(proto.frame_c2(0x0000, 4), b"\xe0\xe0\xe0\xe0"))
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        request = _request("read", {"parameter": "status"})
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "ok"
        assert result["data"]["parameter"] == "status"
        assert result["data"]["value"] == 0xE0
        assert result["data"]["unit"] == "count"
        assert result["data"]["quality"] == "valid"
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_read_trigger_position():
    async def run():
        host = MockHost(_register_read(proto.frame_c2(0x000A, 4), b"\x00\x05\xe0\xe0"))
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
                _quiet_ack(),
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
        host = MockHost([_frame_send(proto.frame_c3(0)), _quiet_ack()])
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=1.0)
        await plugin.open(_descriptor(), host, context)
        request = _request("invoke", {"action_id": "stop", "input": {}})
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "ok"
        assert result["data"]["action_id"] == "stop"
        assert result["data"]["result"]["acknowledged"] is True
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
    """The vectors.json demo conversation: tp=0, 8 columns, one run column."""
    async def run():
        host = MockHost(
            # The capture-complete gate: status bit 0 set on the first poll.
            _register_read(proto.frame_c2(0x0000, 4), b"\x01\x00\x00\x00")
            + _register_read(proto.frame_c2(0x000A, 4), b"\x00\x00\xe0\xe0")
            + [
                _frame_send(proto.frame_c2(0x0102, 4)),
                _exchange(proto.stream_receive(4, 4), {"data": b"\x00\x00\x00\x00"}),
                _frame_send(proto.frame_c2(0x2001, 4)),
                _exchange(proto.stream_receive(4, 4), {"data": b"\xff\xff\xff\xff"}),
                _frame_send(proto.frame_c2(0x3001, 4)),
                _exchange(proto.stream_receive(4, 4), {"data": b"\x0f\x0f\x0f\x0f"}),
                _frame_send(proto.frame_c2(0x4001, 4)),
                _exchange(proto.stream_receive(4, 4), {"data": b"\x00\x00\x00\x00"}),
                _frame_send(proto.frame_c2(0x9001, 4)),
                _exchange(proto.stream_receive(4, 4), {"data": b"\x00\x00\x00\x00"}),
                _frame_send(proto.frame_c2(0x0502, 4)),
                _exchange(proto.stream_receive(4, 4), {"data": b"\x05\x05\x05\x05"}),
                _frame_send(proto.frame_c2(0x2401, 4)),
                _exchange(proto.stream_receive(4, 4), {"data": b"\x00\x5a\x5a\x5a"}),
                _frame_send(proto.frame_c2(0x3401, 4)),
                _exchange(proto.stream_receive(4, 4), {"data": b"\x00\x00\x00\x00"}),
                _frame_send(proto.frame_c2(0x4401, 4)),
                _exchange(proto.stream_receive(4, 4), {"data": b"\x00\xff\xff\xff"}),
                _frame_send(proto.frame_c2(0x9401, 4)),
                _exchange(proto.stream_receive(4, 4), {"data": b"\x08\x00\x00\x00"}),
            ]
        )
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


def test_vectors_match_protocol_builders():
    """Every vectors.json request frame is a protocol builder's exact bytes.

    The vectors script the LIVE mock poller's demand cycle: the host reads
    every declared ro parameter in descriptor order — status,
    trigger_position, then the four frequency slots (select write, ready
    gate, count) — and the script carries two full cycles so the mock
    detects the repeating unit.
    """
    vectors = json.loads(files("logicport_plugin").joinpath("vectors.json").read_text())
    assert vectors["transaction_dialect"] == "send_receive"
    assert vectors["encoding"] == "hex"
    rows = vectors["exchanges"]
    cycle = [proto.frame_c2(0x0000, 4).hex(), proto.frame_c2(0x000A, 4).hex()]
    for channel in range(4):
        cycle.append(proto.frame_c1(proto.wire_address(proto.REG_FREQ_SELECT), proto.payload_byte(channel)).hex())
        cycle.append(proto.frame_c2(proto.wire_address(proto.REG_FREQ_SELECT), 1).hex())
        cycle.append(proto.frame_c2(proto.wire_address(proto.REG_FREQ_COUNT), 4).hex())
    assert len(rows) == 2 * len(cycle)
    assert [row["request"] for row in rows] == cycle * 2
    # Every response is a constructed fixture: the status rows' E0 bytes
    # are withdrawn hardware evidence (record section 10), and the
    # frequency counts decode to the fixture Hz values (count x 10).
    assert rows[0]["response"] == "e0e0e0e0"
    hz = [int.from_bytes(bytes.fromhex(row["response"]), "big") * 10
          for row in rows if row["name"].endswith("-count")]
    assert hz == [1000000, 125000, 0, 10000000] * 2


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
                    _exchange(proto.stream_receive(4, 4), response),
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


def _freq_script(channel):
    return [
        _frame_send(proto.frame_c1(proto.wire_address(proto.REG_FREQ_SELECT), proto.payload_byte(channel))),
        _quiet_ack(),
        _frame_send(proto.frame_c2(proto.wire_address(proto.REG_FREQ_SELECT), 1)),
        _exchange(proto.stream_receive(1, 1), {"data": b"\x00"}),  # count ready
        _frame_send(proto.frame_c2(proto.wire_address(proto.REG_FREQ_SELECT) + 1, 4)),
        _exchange(proto.stream_receive(4, 4), {"data": b"\x00\x01\xe2\x40"}),  # 123456
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
            [_frame_send(frames[0]), _quiet_ack(), _frame_send(frames[1]), _quiet_ack()]
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
        host = MockHost(
            [
                exchange
                for register, payload, _ in plan
                for exchange in (_frame_send(proto.frame_c1(proto.wire_address(register), payload)), _quiet_ack())
            ]
        )
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


def test_class_arm_reports_device_state():
    async def run():
        # The device auto-arms after FPGA configuration: the status byte
        # (armed: bit0=0, prefill 00) answers the arm state.
        host = MockHost(_register_read(proto.frame_c2(0x0000, 4), b"\x00\x00\x00\x00"))
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
        validate_result(result, request)
        assert result["status"] == "ok", result
        assert result["data"]["result"] == {"acquisition_id": "acq-1", "state": "armed"}
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_class_fetch_builds_a_conforming_dataset():
    async def run():
        # Arm reads status (armed); fetch polls running then complete,
        # reads the trigger position (0x07F7 -> N=8, chunk 4), then the
        # ten plane chunks.
        status_frames = [
            *_register_read(proto.frame_c2(0x0000, 4), b"\x00\x00\x00\x00"),
            *_register_read(proto.frame_c2(0x0000, 4), b"\x00\x00\x00\x00"),
            *_register_read(proto.frame_c2(0x0000, 4), b"\x01\x00\x00\x00"),
            *_register_read(proto.frame_c2(0x000A, 4), b"\x07\xf7\xe0\xe0"),
        ]
        planes = [
            _frame_send(proto.frame_c2(0x04f9, 4)),
            _exchange(proto.stream_receive(4, 4), {"data": b"\x00\x00\x00\x00"}),
            _frame_send(proto.frame_c2(0x23f8, 4)),
            _exchange(proto.stream_receive(4, 4), {"data": b"\xff\xff\xff\xff"}),
            _frame_send(proto.frame_c2(0x33f8, 4)),
            _exchange(proto.stream_receive(4, 4), {"data": b"\x00\x00\x00\x00"}),
            _frame_send(proto.frame_c2(0x43f8, 4)),
            _exchange(proto.stream_receive(4, 4), {"data": b"\x00\x00\x00\x00"}),
            _frame_send(proto.frame_c2(0x93f8, 4)),
            _exchange(proto.stream_receive(4, 4), {"data": b"\x00\x00\x00\x00"}),
            _frame_send(proto.frame_c2(0x08f9, 4)),
            _exchange(proto.stream_receive(4, 4), {"data": b"\xff\xff\xff\xff"}),
            _frame_send(proto.frame_c2(0x27f8, 4)),
            _exchange(proto.stream_receive(4, 4), {"data": b"\xff\xff\xff\xff"}),
            _frame_send(proto.frame_c2(0x37f8, 4)),
            _exchange(proto.stream_receive(4, 4), {"data": b"\xff\xff\xff\xff"}),
            _frame_send(proto.frame_c2(0x47f8, 4)),
            _exchange(proto.stream_receive(4, 4), {"data": b"\xff\xff\xff\xff"}),
            _frame_send(proto.frame_c2(0x97f8, 4)),
            _exchange(proto.stream_receive(4, 4), {"data": b"\x06\x06\x06\x06"}),
        ]
        host = MockHost(status_frames + planes)
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

        validate_doc(dataset, "otdp/0.2.2/otdp-measurement.schema.json")
        assert dataset["dataset_id"] == "ds-1"
        assert dataset["kind"] == "digital_trace"
        assert dataset["acquisition_id"] == "acq-1"
        assert dataset["status"] == "complete"
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


def test_class_abort_sends_stop():
    async def run():
        host = MockHost([_frame_send(proto.frame_c3(0)), _quiet_ack()])
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
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


# --- Issue #562 machine-truth round: counter serialization, poll floor,
# FPGA completion honesty, bitstream send accounting, capture gate. ---


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
    whole select/gate/count sequence.
    """

    async def run():
        host = _ParkOnNthHost(_freq_script(0) + _freq_script(1), park_before=3)
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
        await asyncio.sleep(0)  # B runs to its next await (its select or the lock)
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
    the unwired-constant defect.
    """

    async def run():
        import logicport_plugin.adapter as adapter_module

        host = MockHost(
            [
                _frame_send(proto.frame_c1(proto.wire_address(proto.REG_FREQ_SELECT), proto.payload_byte(0))),
                _quiet_ack(),
                _frame_send(proto.frame_c2(proto.wire_address(proto.REG_FREQ_SELECT), 1)),
                _exchange(proto.stream_receive(1, 1), {"data": b"\x01"}),  # count not settled
                _frame_send(proto.frame_c2(proto.wire_address(proto.REG_FREQ_SELECT), 1)),
                _exchange(proto.stream_receive(1, 1), {"data": b"\x00"}),  # ready
                _frame_send(proto.frame_c2(proto.wire_address(proto.REG_FREQ_SELECT) + 1, 4)),
                _exchange(proto.stream_receive(4, 4), {"data": b"\x00\x01\xe2\x40"}),
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


def _synthetic_ccf(tmp_path, slot_len=20000):
    """A synthetic .ccf with the real container format (invented bytes).

    Nine XOR-obfuscated header values, slot 0 ``slot_len`` bytes, the other
    seven slots 256 bytes each. Returns the path and the SHA-256 to pin as
    the adapter's expected digest for the test.
    """
    values = [slot_len // 16] + [16] * 7
    chain = 0
    for value in values:
        chain ^= value
    header = ",".join(str(value ^ proto.CCF_XOR_KEY) for value in values + [chain])
    body = bytes((index * 7 + 3) & 0xFF for index in range(slot_len + 7 * 256))
    blob = header.encode("ascii") + b"\n" + body
    path = tmp_path / "synthetic.ccf"
    path.write_bytes(blob)
    import hashlib

    return path, hashlib.sha256(blob).hexdigest()


def _fpga_pass_script(bitstream, pins_done_byte, chunk_sends=None, stop_after_chunks=False):
    """One section 5 pass over a synthetic bitstream.

    ``chunk_sends`` optionally overrides the stream-send reply of the chunk
    with that index (for short-send rehearsal; None keeps the bare reply).
    ``stop_after_chunks`` omits the DONE poll and the trailing bit-mode
    reset — the shape of a pass that fails AT a chunk: the adapter resets
    the interface itself and never reaches the DONE poll.
    """
    transfers = [
        _exchange(proto.ftdi_purge("rx"), {}),
        _exchange(proto.ftdi_set_bitmode(proto.BITMODE_RESET, proto.BITMODE_RESET), {}),
        _exchange(proto.ftdi_set_bitmode(proto.BITBANG_MASK_FPGA, proto.BITMODE_BITBANG), {}),
        _frame_send(bytes([proto.FPGA_PULSE_START])),
        _exchange(proto.ftdi_read_pins(), {"data": b"\x00"}),  # DONE cleared
        _frame_send(bytes([proto.FPGA_BITSTREAM_START])),
        _exchange(proto.ftdi_read_pins(), {"data": b"\x08"}),  # INIT high
    ]
    short_at = None
    if chunk_sends is not None:
        short_at = [index for index, sent in enumerate(chunk_sends) if sent is not None]
    for index, offset in enumerate(range(0, len(bitstream), proto.BITSTREAM_CHUNK_MAX)):
        chunk = bitstream[offset : offset + proto.BITSTREAM_CHUNK_MAX]
        if short_at and index in short_at:
            reply = {"sent": chunk_sends[index]}
        else:
            reply = {}
        transfers.append(_exchange(proto.stream_send(chunk), reply))
        if short_at and index in short_at and index + 1 == len(short_at) and stop_after_chunks:
            break
    if stop_after_chunks:
        # The failed pass resets the interface mode; no DONE poll follows.
        transfers.append(_exchange(proto.ftdi_set_bitmode(proto.BITMODE_RESET, proto.BITMODE_RESET), {}))
        return transfers
    transfers.append(_exchange(proto.ftdi_read_pins(), {"data": bytes([pins_done_byte])}))
    transfers.append(_exchange(proto.ftdi_set_bitmode(proto.BITMODE_RESET, proto.BITMODE_RESET), {}))
    return transfers


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
    (record section 5); the adapter must do the same.
    """
    path, digest = _synthetic_ccf(tmp_path)
    monkeypatch.setattr(proto, "CCF_SHA256", digest)
    blob = path.read_bytes()
    header = proto.parse_ccf_header(blob)
    bitstream = proto.swap_first_pages(proto.extract_ccf_slot(blob, header, 0))

    async def run():
        host = _AdvancingPinsHost(
            _fpga_pass_script(bitstream, pins_done_byte=0x00)  # pass 1: DONE never
            + _fpga_pass_script(bitstream, pins_done_byte=0x10)  # pass 2: DONE asserts
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
        context = MockContext("op-1", deadline_monotonic=600.0)
        await plugin.open(_descriptor(), host, context)
        request = _request(
            "invoke",
            {"action_id": "configure_fpga", "input": {"ccf_path": str(path), "attempts": 2}},
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


def test_configure_fpga_reports_uncertain_when_done_never_asserts(monkeypatch, tmp_path):
    path, digest = _synthetic_ccf(tmp_path)
    monkeypatch.setattr(proto, "CCF_SHA256", digest)
    blob = path.read_bytes()
    header = proto.parse_ccf_header(blob)
    bitstream = proto.swap_first_pages(proto.extract_ccf_slot(blob, header, 0))

    async def run():
        host = _AdvancingPinsHost(_fpga_pass_script(bitstream, pins_done_byte=0x00))
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=600.0)
        await plugin.open(_descriptor(), host, context)
        request = _request(
            "invoke",
            {"action_id": "configure_fpga", "input": {"ccf_path": str(path), "attempts": 1}},
        )
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "unknown", result
        assert result["error"]["code"] == "TRANSPORT_ERROR"
        assert result["error"]["dispatch_state"] == "unknown"
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_configure_fpga_fails_a_short_bitstream_send(monkeypatch, tmp_path):
    """A chunk the host reports short is a failed pass, never a load that
    presses on to the DONE poll as if the bitstream had been delivered."""
    path, digest = _synthetic_ccf(tmp_path)
    monkeypatch.setattr(proto, "CCF_SHA256", digest)
    blob = path.read_bytes()
    header = proto.parse_ccf_header(blob)
    bitstream = proto.swap_first_pages(proto.extract_ccf_slot(blob, header, 0))
    chunk_count = (len(bitstream) + proto.BITSTREAM_CHUNK_MAX - 1) // proto.BITSTREAM_CHUNK_MAX
    assert chunk_count == 2

    async def run():
        # Chunk 2 reports 5000 of 10000 bytes sent: the pass fails at the
        # chunk, resets the interface mode, and never polls DONE.
        host = _AdvancingPinsHost(
            _fpga_pass_script(
                bitstream, pins_done_byte=0x10, chunk_sends=[None, 5000], stop_after_chunks=True
            )
        )
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=600.0)
        await plugin.open(_descriptor(), host, context)
        request = _request(
            "invoke",
            {"action_id": "configure_fpga", "input": {"ccf_path": str(path), "attempts": 1}},
        )
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "unknown", result
        assert result["error"]["code"] == "TRANSPORT_ERROR"
        assert result["error"]["dispatch_state"] == "unknown"
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())


def test_read_samples_waits_for_capture_complete():
    """read_samples must gate on status bit 0 before touching the planes.

    Reading a running capture tears one dataset across two buffer
    generations and stamps it complete.
    """

    async def run():
        # One hundred not-complete status answers: the bounded poll loop
        # exhausts its attempts and must refuse with TIMEOUT (uncertain:
        # frames were already exchanged).
        script = []
        for _ in range(100):
            script += _register_read(proto.frame_c2(0x0000, 4), b"\x00\x00\x00\x00")
        host = MockHost(script)
        plugin = create_plugin()
        context = MockContext("op-1", deadline_monotonic=10.0)
        await plugin.open(_descriptor(), host, context)
        request = _request("invoke", {"action_id": "read_samples", "input": {"column_count": 8}})
        result = await plugin.execute(request, context)
        validate_result(result, request)
        assert result["status"] == "unknown", result
        assert result["error"]["code"] == "TIMEOUT"
        assert result["error"]["dispatch_state"] == "unknown"
        host.assert_complete()
        await plugin.close(context)
    asyncio.run(run())

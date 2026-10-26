"""Anti-circularity pins on vectors.json (#562 wire-truth Task 9).

The mock serve replays these bytes verbatim, so the pins refuse
builder-generated reply shapes: every reply must parse through the
hardware-proven grammar (protocol.py has no reply builder — a reply the
builders could have produced is a raw payload echo, which parse_reply
refuses), and one exchange must carry the hand-recorded addr_probe bytes
exactly. Paths are repo-root relative: the test command runs from the
worktree root.

Fold R5 (2026-10-26 review wave): the corpus is THREE full poll cycles
in the descriptor's read-only walk order (status, frequency_a..d
select/gate/count, trigger_position) so a three-cycle replay never
self-wedges. Fold R9 pins a SHA-256 over the serialized exchanges array
so any regeneration is a deliberate, reviewed edit.
"""

import asyncio
import hashlib
import json
from pathlib import Path

from logicport_plugin import protocol as proto

VECTORS = Path("src/logicport_plugin/vectors.json")
DESCRIPTOR = Path("src/logicport_plugin/descriptor.json")

#: The 2026-10-26 addr_probe run's first D2 row, hand-recorded: a count=4
#: read at wire address 0x0000 (REG_STATUS, idle) replied exactly
#: ``C2 00 19 00 00 00 00 00`` — header [C2, 0x00, counter 0x19, aux 0x00],
#: four zero payload bytes (all reads returned zeros on the unarmed device).
GOLDEN_ADDR_PROBE_REPLY = bytes.fromhex("C200190000000000")

#: SHA-256 over the canonical serialization of the exchanges array:
#: ``json.dumps(exchanges, sort_keys=True, separators=(",", ":"))``.
#: Regenerating the corpus is a deliberate, reviewed edit — the pin changes
#: only when this constant is re-derived alongside it (fold R9).
VECTORS_EXCHANGES_SHA256 = (
    "c7adf83b8e80de3efcabebaef9c3b7510f7b76fa5e59eeae749e2a3b2dab009b"
)


def _exchanges_sha256(vectors: dict) -> str:
    payload = json.dumps(vectors["exchanges"], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_vectors_carry_hardware_golden_bytes():
    raw = json.loads(VECTORS.read_text())
    blob = json.dumps(raw)
    assert GOLDEN_ADDR_PROBE_REPLY.hex() in blob.lower()


def test_vectors_exchanges_are_sha_pinned():
    """Fold R9: the exchanges array carries a reviewed sha256 pin — a
    regenerated corpus must change this constant deliberately."""
    vectors = json.loads(VECTORS.read_text())
    assert _exchanges_sha256(vectors) == VECTORS_EXCHANGES_SHA256


def test_vectors_replies_match_the_hardware_grammar():
    """Every reply parses the hardware-proven grammar with a strictly
    incrementing command counter across the whole capture.

    A C2 request of count N is answered by exactly N+4 bytes
    ([C2, 0x00, counter, aux] + N payload bytes); a C1 write is acked by
    exactly 3 bytes ([C1, 0x00, counter]). One counter sequences reads and
    writes, +1 in conversation order — reads and ACKs share the sequence.
    """
    vectors = json.loads(VECTORS.read_text())
    assert vectors["transaction_dialect"] == "send_receive"
    assert vectors["encoding"] == "hex"
    rows = vectors["exchanges"]
    expected = 0x19  # the golden row's counter; advances across the file
    for row in rows:
        request = bytes.fromhex(row["request"])
        response = bytes.fromhex(row["response"])
        if request[0] == proto.CMD_READ:
            count = int.from_bytes(request[3:5], "little")
            frame = proto.parse_reply(response)
            assert frame.cmd_echo == proto.CMD_READ
            assert len(frame.payload) == count
        else:
            assert request[0] == proto.CMD_WRITE
            assert len(response) == 3  # the ACK is exactly 3 bytes
            ack = proto.parse_write_ack(response)
            assert ack.cmd_echo == proto.CMD_WRITE
        assert response[2] == expected
        expected = (expected + 1) & 0xFF
    assert len(rows) == 42  # three full poll cycles of 14 exchanges
    assert expected == 0x43  # counters 0x19..0x42 consumed in order
    # The golden probe bytes ride an idle status read: C2 of REG_STATUS
    # (wire 0x0000), count 4 — the exchange the hardware probe recorded.
    assert bytes.fromhex(rows[0]["request"]) == proto.frame_c2(
        proto.wire_address(proto.REG_STATUS), 4
    )
    assert bytes.fromhex(rows[0]["response"]) == GOLDEN_ADDR_PROBE_REPLY


def test_three_cycle_replay_never_wedges_the_counter():
    """Fold R5: replay THREE consecutive cycles through the
    ByteStreamMockHost (the mock session's send_receive dialect over
    vectors.json) driving the descriptor's read-only walk demand — status,
    frequency_a..d, trigger_position — with no gap refusal anywhere.

    The 42-row corpus is exactly three 14-row cycles, so three full walks
    consume it in order; the adapter's counter continuity holds across all
    42 exchanges (a gap surfaces as a PROTOCOL_ERROR envelope, which the
    per-read assertions refuse). The mech-critic's replay technique:
    load_plugin_project + mock session, descriptor-order demand.
    """
    async def run():
        from benchweave_sdk_server.session import (
            load_plugin_project,
            mock_plugin_session,
        )

        descriptor = json.loads(DESCRIPTOR.read_text())
        ro_parameters = [
            row["name"] for row in descriptor["parameters"] if row["access"] == "ro"
        ]
        # The descriptor's declared RO walk is the corpus's row order (R5).
        assert ro_parameters == [
            "status",
            "frequency_a",
            "frequency_b",
            "frequency_c",
            "frequency_d",
            "trigger_position",
        ]

        plugin = load_plugin_project(Path("."))
        session = mock_plugin_session(plugin)
        await session.connect()
        try:
            for cycle in range(3):
                for parameter in ro_parameters:
                    result = await session.execute("read", {"parameter": parameter})
                    assert result["status"] == "ok", (cycle, parameter, result)
                    data = result["data"]
                    if cycle == 0 and parameter == "status":
                        assert data["value"] == 0x19  # the golden first reply
                    if cycle == 1 and parameter == "status":
                        assert data["value"] == 0x27  # 0x19 + 14, still +1
                    if cycle == 2 and parameter == "status":
                        assert data["value"] == 0x35  # third cycle's first
            # All 42 scripted rows consumed in order, inbound drained.
            session.services.assert_complete()
        finally:
            await session.close()
    asyncio.run(run())

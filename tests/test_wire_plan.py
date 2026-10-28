"""Decode pins on the wire plan (docs addenda 5 and 6, 2026-10-27/28).

Every expected value is HAND-DERIVED from the addenda text — never from
wire_plan's own builders, which would make these pins circular. This is
the anti-circularity discipline test_vectors.py already models.

No hardware is contacted.
"""

import pytest

from logicport_plugin import wire_plan as wp

RATE = 0x0100  # worked rate word: wire bytes 00 01


def test_frame_bytes_known_rate():
    """Exact wire bytes, derived by hand from addendum 5's frame table.

    rate = 0x0100 -> byte1 = 0x00 (rate & 0xFF), byte2 = 0x01
    ((rate >> 8) & 0xFF) — little-endian; the length field is
    (len(payload) - 1), also little-endian.
    """
    # C1, two-byte payload (the addendum's worked threshold example):
    # len - 1 = 1 -> C1 00 01 01 00 BD 01.
    assert wp.rate_write_frame(RATE, b"\xBD\x01") == bytes([0xC1, 0x00, 0x01, 0x01, 0, 0xBD, 0x01])
    # C1, one-byte payload: len - 1 = 0.
    assert wp.rate_write_frame(RATE, b"\x2A") == bytes([0xC1, 0x00, 0x01, 0x00, 0x00, 0x2A])


def test_c1_drain_rule_and_timeouts():
    """The drain rule and both timeouts are addendum constants."""
    assert wp.C1_DRAIN_BYTES == 3  # every C1 drains exactly three bytes
    assert wp.C1_TIMEOUT_MS == 1000
    assert wp.C2_TIMEOUT_MS == 2000
    assert wp.STATUS_POLL_BYTES == 1  # the status poll is a ONE-byte C2


def test_status_bit_decode_table():
    """The addendum 6 status table, hand-expanded to bytes.

    bit0 set wins (complete); 0x60 = triggered/collecting; 0x40 alone =
    waiting; bit0 clear and 0x40 clear = pre-fill.
    """
    assert wp.decode_status(0x00) is wp.StatusPhase.PREFILL
    assert wp.decode_status(0x20) is wp.StatusPhase.PREFILL  # 0x40 clear
    assert wp.decode_status(0x40) is wp.StatusPhase.WAITING_TRIGGER
    assert wp.decode_status(0x60) is wp.StatusPhase.COLLECTING
    assert wp.decode_status(0x01) is wp.StatusPhase.COMPLETE
    assert wp.decode_status(0x41) is wp.StatusPhase.COMPLETE  # bit0 wins
    assert wp.decode_status(0x61) is wp.StatusPhase.COMPLETE
    assert wp.decode_status(0xC0) is wp.StatusPhase.WAITING_TRIGGER  # 0x20 clear


def test_status_poll_is_a_one_byte_c2():
    assert wp.status_poll_frame(RATE) == bytes([0xC2, 0x00, 0x01, 0x01, 0x00])


def test_byte_mode_read_plan_counts():
    """Worked offset 5: cols = 0x7FF - 5 = 0x7FA = 2042; every one of the
    ten plane reads carries (cols + 1) / 2 = 1021 bytes."""
    plan = wp.byte_mode_read_plan(5)
    assert len(plan) == 10
    assert all(read.byte_count == 1021 for read in plan)
    assert [read.slot for read in plan] == [
        "c08", "c10", "c18", "c20", "c48",  # the even-column set
        "c28", "c30", "c38", "c40", "c48",  # the odd-column set
    ]


def test_run_length_mode_read_plan_counts():
    """Worked offset 5: the offset advances first to 6, cols = 0x7F9 =
    2041, so each of the five plane reads carries cols + 1 = 2042 bytes.
    The c48 flag plane at the trigger position is read first."""
    plan = wp.run_length_mode_read_plan(5)
    assert [read.slot for read in plan] == ["c48", "c08", "c10", "c18", "c20"]
    assert all(read.byte_count == 2042 for read in plan)


def test_read_plan_edge_offsets():
    """The floor and the wrap, hand-derived."""
    # offset 0x7FF: cols = 0x7FF - 0x7FF = 0, floored to 1 -> (1+1)/2 = 1.
    assert wp.byte_mode_cols(0x7FF) == 1
    assert all(read.byte_count == 1 for read in wp.byte_mode_read_plan(0x7FF))
    # offset 0x7FF in run-length mode: the offset advances to 0
    # ((0x7FF + 1) & 0x7FF), cols = 0x7FF, count = 0x800 = 2048.
    assert all(read.byte_count == 2048 for read in wp.run_length_mode_read_plan(0x7FF))


def test_byte_mode_column_inversion():
    """The inversion rule, hand-computed for one even and one odd column.

    A stored 0 is a HIGH pin, a stored 1 is a LOW pin. Even column:
    c08=0x00 (ch0-7 HIGH), c10=0x01 (ch8 STORED 1 -> LOW, ch9-15 stored
    0 -> HIGH), c18=0xFF (ch16-23 LOW), c20=0x00 (ch24-31 HIGH),
    c48=0b110 (CLK1 and CLK2 stored 1 -> both LOW). Bits 0-7, 9-15 and
    24-31 set: 0xFF00FEFF.
    """
    even = wp.decode_byte_column((0x00, 0x01, 0xFF, 0x00, 0b00000110), odd_column=False)
    assert even == 0xFF00FEFF
    # Odd column: c28=0x02 (ch0 HIGH, ch1 LOW, ch2-7 HIGH), c30/c38/c40=0
    # (ch8-31 HIGH), c48=0b100000 (bit5 -> CLK1 LOW; bit6 -> CLK2 HIGH).
    odd = wp.decode_byte_column((0x02, 0x00, 0x00, 0x00, 0b00100000), odd_column=True)
    assert odd == 0x2FFFFFFFD


def test_run_length_column_35bit_and_digital():
    """The 35-bit big-endian run count and one digital column.

    Run: c08=0x11, c10=0x22, c18=0x33, c20=0x04, flag=0x0D -> bit3 set;
    count = ((0x0D & 7) << 32) | 0x04<<24 | 0x33<<16 | 0x22<<8 | 0x11
    = (5 << 32) | 0x04332211 = 0x504332211.
    """
    value, is_run = wp.decode_run_length_column((0x11, 0x22, 0x33, 0x04, 0x0D))
    assert is_run is True
    assert value == 0x504332211
    # Digital column: flag 0x04 (bit3 clear, bit2 set -> CLK2 LOW, CLK1
    # HIGH), all data planes zero (ch0-31 HIGH) -> 0x1FFFFFFFF.
    value, is_run = wp.decode_run_length_column((0x00, 0x00, 0x00, 0x00, 0x04))
    assert is_run is False
    assert value == 0x1FFFFFFFF


def test_run_length_columns_loop():
    """The full loop: a run column copies the previous digital state
    forward and accumulates its count."""
    decoded = wp.decode_run_length_columns(
        [
            (0x00, 0x00, 0x00, 0x00, 0x00),  # digital: all 34 HIGH
            (0x11, 0x22, 0x33, 0x04, 0x0D),  # run of 0x504332211
            (0xFF, 0x00, 0x00, 0x00, 0x00),  # digital: ch0-7 LOW, rest HIGH
        ]
    )
    assert decoded.columns[0] == (1 << 34) - 1
    assert decoded.columns[1] == decoded.columns[0]  # the run carries
    assert decoded.columns[2] == 0x3FFFFFF00
    assert decoded.runs == (0, 0x504332211, 0)
    assert decoded.total_samples == 2 + 0x504332211
    assert decoded.col_time == (0, 1, 1 + 0x504332211)


def test_byte_mode_pipeline():
    """Two bytes per plane -> four interleaved columns, each hand-derived."""
    decoded = wp.decode_byte_mode(
        even_planes=(
            bytes([0x00, 0x02]),
            bytes([0x00, 0x00]),
            bytes([0x00, 0x00]),
            bytes([0x00, 0x00]),
            bytes([0b00000110, 0b00000010]),
        ),
        odd_planes=(
            bytes([0x00, 0x02]),
            bytes([0x00, 0x00]),
            bytes([0x00, 0x00]),
            bytes([0x00, 0x00]),
            bytes([0b00100000, 0b00000000]),
        ),
    )
    # Column 0 (even byte 0): data all stored 0 (HIGH), c48=0b110 (CLK1
    # and CLK2 LOW) -> 0xFFFFFFFF.
    assert decoded.columns[0] == 0xFFFFFFFF
    # Column 1 (odd byte 0): c48=0b100000 -> CLK1 LOW, CLK2 HIGH ->
    # 0x2FFFFFFFF.
    assert decoded.columns[1] == 0x2FFFFFFFF
    # Column 2 (even byte 1): c08=0x02 (ch0 HIGH, ch1 LOW), c48=0b010
    # (CLK1 LOW, CLK2 HIGH) -> 0x2FFFFFFFD.
    assert decoded.columns[2] == 0x2FFFFFFFD
    # Column 3 (odd byte 1): c28=0x02, c48=0 -> CLK1/CLK2 HIGH ->
    # 0x3FFFFFFFD.
    assert decoded.columns[3] == 0x3FFFFFFFD
    assert decoded.total_samples == 4


def test_command_plan_order():
    """The dispatcher send order (addendum 5, six blocks): payload sizes
    in sequence position; the sequencer segments are the variable steps."""
    plan = wp.command_plan()
    assert [step.payload_size for step in plan] == [
        1, 1,      # block 1: the profile byte, sent twice
        1, 1, 5, 5, 1, 1,  # block 2: mode byte, second byte, two 40-bit tallies, two bytes
        1, 1, None, None, 1,  # block 3: two bytes, two segments, one byte
        2,         # block 4: the threshold DAC word
        2, 2,      # block 5: the two trigger-position words
        1,         # block 6: the arm byte
    ]
    assert len(plan) == 17
    # The variable steps are the two sequencer segments.
    variable = [i for i, step in enumerate(plan) if step.payload_size is None]
    assert variable == [10, 11]


@pytest.mark.skip(
    "Threshold conflict 2026-10-28: the decode claims "
    "code = Int((4.559 - volts) / 5.875 * 1023) (2.0 V -> 445) while the "
    "shipped two-stage chain in protocol.threshold_dac_code returns 613 "
    "at 2.0 V (its verified check points +6 V -> 893, 0 V -> 473, "
    "-6 V -> 53 stand). The conflict is recorded, nothing asserted; the "
    "existing implementation is untouched until a runtime capture "
    "discriminates the formulas."
)
def test_threshold_formula_conflict_2026_10_28():
    pytest.skip("documented in the decorator reason; nothing to assert")

"""Protocol-layer tests: framing, .ccf decode, sample decode, DAC, dialect.

The .ccf header fixture is the real LogicPort.ccf header line (9 CSV
values, CRLF-terminated, body offset 73) with the hardware-proven slot
table; the bitstream body is synthetic. No hardware is contacted.
"""

import pytest

from logicport_plugin import protocol as proto

# The real LogicPort.ccf header line (SHA-256 9298a5ea...; record section 9).
REAL_CCF_HEADER = (
    b"9462817,9541984,9542133,9542096,9541917,9463795,9543078,9462791,9477193\r\n"
)


def test_frame_c1_word_and_payload():
    # C1 to word 0x1234 with a one-byte payload: L = 0.
    assert proto.frame_c1(0x1234, b"\x01") == bytes([0xC1, 0x34, 0x12, 0x00, 0x00, 0x01])
    # A five-byte usbWrite40 payload: L = 4.
    frame = proto.frame_c1(0x000A, b"\x01\x02\x03\x04\x05")
    assert frame[:5] == bytes([0xC1, 0x0A, 0x00, 0x04, 0x00])
    assert frame[5:] == b"\x01\x02\x03\x04\x05"


def test_frame_c1_rejects_empty_payload():
    # A C1 always carries at least one payload byte (L = len-1 >= 0).
    with pytest.raises(ValueError):
        proto.frame_c1(0, b"")


def test_frame_c2_matches_hardware_probe():
    # The 2026-10-12 live probe's exact status frame (record section 8).
    assert proto.frame_c2(0x0000, 4) == bytes([0xC2, 0x00, 0x00, 0x04, 0x00])


def test_frame_c3_stop():
    assert proto.frame_c3(0x0000) == bytes([0xC3, 0x00, 0x00, 0x00, 0x00])
    assert proto.frame_c3(0xABCD) == bytes([0xC3, 0xCD, 0xAB, 0x00, 0x00])


def test_payload_wrappers_little_endian():
    assert proto.payload_byte(0x7F) == b"\x7f"
    assert proto.payload_word(0x1234) == b"\x34\x12"
    assert proto.payload_dword(0x12345678) == b"\x78\x56\x34\x12"
    assert proto.payload_40(0x0102030405) == b"\x05\x04\x03\x02\x01"
    with pytest.raises(ValueError):
        proto.payload_word(0x10000)
    with pytest.raises(ValueError):
        proto.payload_40(1 << 40)


def test_trigger_mask_payload_34_bits():
    full = (1 << 34) - 1
    payload = proto.trigger_mask_payload(full)
    assert len(payload) == 5
    assert int.from_bytes(payload, "little") == full
    with pytest.raises(ValueError):
        proto.trigger_mask_payload(1 << 34)


def test_response_parsers_big_endian():
    # Response VALUES are big-endian (record section 3.2/3.5).
    assert proto.parse_read_byte(b"\xe0\xe0\xe0\xe0") == 0xE0
    assert proto.parse_read_word(b"\x01\x02\xff\xff") == 0x0102
    assert proto.parse_read_dword(b"\x01\x02\x03\x04") == 0x01020304
    with pytest.raises(ValueError):
        proto.parse_read_word(b"\x01")
    with pytest.raises(ValueError):
        proto.parse_read_dword(b"\x01\x02")


def test_decode_status_hardware_capture():
    # The §9 follow-up probe: E0 E0 E0 E0 — armed/running, prefill 11.
    bits = proto.decode_status(0xE0)
    assert bits.capture_complete is False
    assert bits.prefill_phase == 0b11
    assert bits.raw == 0xE0
    complete = proto.decode_status(0x01)
    assert complete.capture_complete is True
    immediate = proto.decode_status(0x03)
    assert immediate.immediate_trigger is True


def test_threshold_dac_check_points():
    # The record's check points: +6 V -> 893, 0 V -> 473, -6 V -> 53.
    assert proto.threshold_dac_code(6.0) == 893
    assert proto.threshold_dac_code(0.0) == 473
    assert proto.threshold_dac_code(-6.0) == 53
    with pytest.raises(ValueError):
        proto.threshold_dac_code(6.1)
    with pytest.raises(ValueError):
        proto.threshold_dac_code(float("nan"))


def test_pack_rate_word():
    assert proto.pack_rate_word(0x1234, 0x0005) == 0x00051234
    assert proto.pack_rate_word(0xFFFF) == 0xFFFF
    with pytest.raises(ValueError):
        proto.pack_rate_word(0x10000)


def test_wire_address_proven_and_families():
    # Proven: status 0x100000 -> wire 0x0000 (live probe).
    assert proto.wire_address(proto.REG_STATUS) == 0x0000
    assert proto.wire_address(proto.REG_TRIGGER_POSITION) == 0x000A
    # All documented families encode low-16 (flagged assumption: the page
    # bits of the decomposition are not hardware-proven and several
    # families collide on the low word — protocol.py's wire_address is
    # the single place to fix when a live probe pins them).
    assert proto.wire_address(proto.REG_FREQ_SELECT) == 0x0000
    assert proto.wire_address(0x1000000) == 0x0000


def test_sample_plane_wire_address():
    assert proto.sample_plane_wire_address(0, 1) == 0x0102
    assert proto.sample_plane_wire_address(1, 1) == 0x2001
    assert proto.sample_plane_wire_address(4, 1) == 0x9001
    # The second ping-pong page sits a PAGE_STRIDE above the first.
    assert proto.sample_plane_wire_address(0, 1 + proto.PAGE_STRIDE) == 0x0502
    with pytest.raises(ValueError):
        proto.sample_plane_wire_address(5, 0)


def test_ccf_header_real_values():
    header = proto.parse_ccf_header(REAL_CCF_HEADER)
    assert header.body_offset == 73
    assert header.decoded == (
        0x10243, 0xFF02, 0xFF97, 0xFFB2, 0xFF7F, 0x10191, 0xFBC4, 0x10265, 0x1FA2B
    )
    assert header.slot_lengths[0] == 1057840
    assert header.slot_lengths[7] == 1058384
    # The XOR-chain checksum matches exactly (record section 9).
    assert header.checksum == 0x1FA2B
    assert header.chain == 0x1FA2B
    assert header.checksum_ok is True


def test_ccf_slots_tile_the_real_file():
    header = proto.parse_ccf_header(REAL_CCF_HEADER)
    assert proto.ccf_slot_bounds(header, 0) == (73, 1057840)
    offset, length = proto.ccf_slot_bounds(header, 7)
    assert offset == 7329385
    # The eight slots tile the 8,387,769-byte file exactly.
    assert offset + length == 8387769
    with pytest.raises(ValueError):
        proto.ccf_slot_bounds(header, 8)


def test_ccf_header_rejects_malformed():
    with pytest.raises(ValueError):
        proto.parse_ccf_header(b"")
    with pytest.raises(ValueError):
        proto.parse_ccf_header(b"1,2,3\n")  # wrong field count
    with pytest.raises(ValueError):
        proto.parse_ccf_header(b"a,b,c,d,e,f,g,h,i\n")  # non-decimal
    with pytest.raises(ValueError):
        proto.parse_ccf_header(b"1,2,3,4,5,6,7,8,9")  # no LF


def test_extract_ccf_slot_and_overrun():
    header = proto.parse_ccf_header(REAL_CCF_HEADER)
    data = REAL_CCF_HEADER + b"\xaa" * 16
    with pytest.raises(ValueError):
        proto.extract_ccf_slot(data, header, 0)  # slot overruns the file
    # A small synthetic .ccf: raw values are the XOR of the wanted decoded
    # lengths' bases (decoded 100 -> raw 100 ^ 0x916662), slots of 1600 bytes.
    raw_value = str(100 ^ proto.CCF_XOR_KEY).encode()
    line = b",".join([raw_value] * 9) + b"\n"
    body = b"\x11" * (8 * 1600)
    blob = line + body
    header2 = proto.parse_ccf_header(blob)
    slot = proto.extract_ccf_slot(blob, header2, 2)
    assert slot == b"\x11" * 1600


def test_swap_first_pages_boundary():
    # The C loader swaps only when the slot is longer than 8192 bytes.
    exact = bytes(range(256)) * 32  # 8192 bytes
    assert proto.swap_first_pages(exact) == exact
    longer = exact + b"tail"
    swapped = proto.swap_first_pages(longer)
    assert swapped[:4096] == exact[4096:8192]
    assert swapped[4096:8192] == exact[:4096]
    assert swapped[8192:] == b"tail"


def test_rle_count_formula():
    # count = (flag&7)<<32 | p3<<24 | p2<<16 | p1<<8 | p0 (record section 6.5).
    planes = bytes([0xA5, 0x5A, 0x00, 0xFF, 0x0B])
    assert proto.decode_rle_count(planes) == (0x3 << 32) | (0xFF << 24) | (0x5A << 8) | 0xA5
    # A minimal run of 5: p0=5, everything else 0, flag bit 3 set.
    assert proto.decode_rle_count(bytes([5, 0, 0, 0, 0x08])) == 5
    with pytest.raises(ValueError):
        proto.decode_rle_count(b"\x00\x00")


def test_sample_state_inverted_storage():
    # Stored 0 means HIGH: all-zero planes -> all 34 channels HIGH.
    assert proto.decode_sample_state(bytes(5), odd_column=False) == (1 << 34) - 1
    # Stored 1 means LOW: all-ones -> all LOW.
    assert proto.decode_sample_state(b"\xff" * 5, odd_column=True) == 0
    # D0 stored 0 -> HIGH; D1 stored 1 -> LOW.
    state = proto.decode_sample_state(bytes([0b00000010, 0, 0, 0, 0]), odd_column=False)
    assert state & 0b11 == 0b01
    # CLK1/CLK2 on even columns: flag bits 1/2, inverted (stored 1 -> LOW).
    state = proto.decode_sample_state(bytes([0, 0, 0, 0, 0b00000110]), odd_column=False)
    assert (state >> 32) & 0b11 == 0b00
    # Odd columns read CLK1 from flag bit 5.
    state = proto.decode_sample_state(bytes([0, 0, 0, 0, 0b00100000]), odd_column=True)
    assert (state >> 32) & 1 == 0


def test_deinterleave_and_decode_columns():
    # Two pages of five planes, one column each -> two output columns.
    page_a = [b"\x00", b"\x00", b"\x00", b"\x00", b"\x00"]
    page_b = [b"\x05", b"\x00", b"\x00", b"\x00", b"\x08"]  # odd col: a run of 5
    columns = proto.deinterleave_pages(page_a, page_b)
    assert len(columns) == 2
    assert columns[0][1] is False  # even
    assert columns[1][1] is True  # odd
    decoded = proto.decode_columns(columns)
    assert decoded.columns[0] == (1 << 34) - 1  # all HIGH
    assert decoded.runs == (0, 5)
    assert decoded.columns[1] == decoded.columns[0]  # the run carries
    assert decoded.col_time == (0, 1)
    assert decoded.total_samples == 6  # 1 sample column + a run of 5


def test_decode_columns_mixed_planes():
    # D0 stored 0 (HIGH), D1 stored 1 (LOW) on the even column.
    page_a = [bytes([0b00000010]), b"\x00", b"\x00", b"\x00", b"\x00"]
    page_b = [b"\x00", b"\x00", b"\x00", b"\x00", b"\x00"]
    decoded = proto.decode_columns(proto.deinterleave_pages(page_a, page_b))
    assert decoded.columns[0] & 0b11 == 0b01
    assert decoded.columns[1] == (1 << 34) - 1
    assert decoded.total_samples == 2


def test_trigger_column_formulas():
    # Raw form (record section 6.7) and the compensated form (section 6.4).
    assert proto.trigger_column_raw(8, 3) == 6
    assert proto.trigger_column_raw(2047, 0) == 0  # ((2047-0)+1) mod 2048
    assert proto.trigger_column_compensated(2047, 0) == -21  # may go negative
    with pytest.raises(ValueError):
        proto.trigger_column_raw(8, 1 << 11)


def test_sample_mode_byte_literal_formula():
    # The documented formula with the VB-style complement (record 5a):
    # the first term is always 4; values are unverified (flagged).
    assert proto.sample_mode_byte(0, 0) == 12
    assert proto.sample_mode_byte(0, 1) == 4
    assert proto.sample_mode_byte(1, 0) == 13
    assert proto.sample_mode_byte(1, 1) == 5


def test_stream_transactions():
    assert proto.stream_send(b"\xc3\x00\x00\x00\x00") == {
        "kind": "stream_send",
        "data": b"\xc3\x00\x00\x00\x00",
    }
    receive = proto.stream_receive(4, 4)
    assert receive == {
        "kind": "stream_receive",
        "max_bytes": 4,
        "termination": "lf",
        "exact_bytes": 4,
    }
    with pytest.raises(ValueError):
        proto.stream_receive(0, None)
    with pytest.raises(ValueError):
        proto.stream_receive(4, 5)


def test_ftdi_control_dialect():
    assert proto.ftdi_reset_device() == {"kind": "ftdi_control", "op": "reset_device"}
    assert proto.ftdi_set_timeouts() == {
        "kind": "ftdi_control", "op": "set_timeouts", "read_ms": 5000, "write_ms": 5000
    }
    assert proto.ftdi_set_latency() == {"kind": "ftdi_control", "op": "set_latency", "ms": 4}
    assert proto.ftdi_set_baud() == {"kind": "ftdi_control", "op": "set_baud", "baud": 460800}
    assert proto.ftdi_purge("rx") == {"kind": "ftdi_control", "op": "purge", "queue": "rx"}
    assert proto.ftdi_set_bitmode(0x07, 0x01) == {
        "kind": "ftdi_control", "op": "set_bitmode", "mask": 0x07, "mode": 0x01
    }
    assert proto.ftdi_read_pins() == {"kind": "ftdi_control", "op": "read_pins"}
    with pytest.raises(ValueError):
        proto.ftdi_purge("both")
    assert proto.decode_pin_byte(b"\x10") == 0x10
    with pytest.raises(ValueError):
        proto.decode_pin_byte(b"")


def test_open_slots_state():
    # Threshold decoded (0x100008); the mask and rate-payload slots stay
    # open and the matching operations refuse while they are None.
    assert proto.REGISTER_THRESHOLD_DAC == 0x100008
    assert proto.REGISTER_TRIGGER_MASK_A is None
    assert proto.REGISTER_TRIGGER_MASK_B is None
    assert proto.RATE_COMMAND_PAYLOAD is None


# --- Fold-in round 2 (RED first): frequency counter, bounds, trigger
# position writes, sequencer (protocol-spec.md, confirmed 2026-10-12). ---


def test_frequency_counter_registers_and_math():
    # Register constants of the confirmed counter page.
    assert proto.REG_FREQ_SELECT == 0x40000000
    assert proto.REG_FREQ_COUNT == 0x40000001
    assert proto.REG_FREQ_RUNNING_MAX == 0x40000005
    # Hz = count x 10 (the 10 Hz resolution of the specifications).
    assert proto.frequency_hz(123456) == 1234560
    # The ready gate: 0 means the count is ready.
    assert proto.freq_ready(0x00) is True
    assert proto.freq_ready(0x01) is False
    with pytest.raises(ValueError):
        proto.freq_ready(b"")  # type: ignore[arg-type]


def test_frequency_counter_period_convention():
    # The device's own display convention: zero frequency reads ">100ms".
    assert proto.period_display(0) == ">100ms"
    assert proto.period_display(10) == "100ms"  # 1/10 Hz = 100 ms
    assert proto.period_display(1000) == "1ms"
    assert proto.period_display(2_500_000) == "400ns"


def test_frequency_poll_interval_floor():
    # Round-robin polling waits longer than 75 ms between cycles.
    assert proto.FREQ_POLL_INTERVAL_MIN_MS > 75


def test_hardware_observed_reply_grammar():
    """The reply grammar the 2026-10-26 read-only probes showed (record
    section 10).

    These pin observed wire shape only — the requested count plus a
    four-byte read header, and a three-byte write ACK. No register value,
    page bit or status bit is pinned anywhere: every probed address
    returned zero data, and the status byte is not hardware-decoded.
    """
    assert proto.READ_REPLY_HEADER_BYTES == 4
    assert proto.WRITE_ACK_BYTES == 3
    # About 119 ms observed per write; the hypothesis that poll loops
    # must pace at this cadence is recorded in the record, not asserted.
    assert proto.WRITE_ACK_LATENCY_MS == 119


def test_frequency_round_robin_lag_plan():
    # One-iteration lag: select N+1, then read N, over the four channels.
    plan = proto.freq_round_robin(4)
    assert plan == [(1, 0), (2, 1), (3, 2), (0, 3)]
    with pytest.raises(ValueError):
        proto.freq_round_robin(5)  # only four counter channels exist


def test_reader_column_bounds_settled():
    # SDR buffers 4096 columns (0xFFF bounds); DDR buffers 2048 (0x7FF).
    assert proto.SDR_COLUMN_COUNT_MAX == 0xFFF
    assert proto.DDR_COLUMN_COUNT_MAX == 0x7FF
    assert proto.DDR_COLUMN_COUNT_MAX == proto.COLUMN_COUNT_MAX
    # N = 0x7FF - trigger position for BOTH readers.
    assert proto.columns_from_trigger_position(0) == 0x7FF
    assert proto.columns_from_trigger_position(5) == 0x7FA


def test_trigger_position_words_branches():
    # Mode flag not 1: +7 / -7, no division.
    assert proto.trigger_position_words(0, 1, 100) == (107, 93)
    # Mode flag 1, clock type 2: +35 / -30.
    assert proto.trigger_position_words(1, 2, 100) == (135, 70)
    # Mode flag 1, other clock types: +7 / -7, then divide by clock type.
    assert proto.trigger_position_words(1, 3, 300) == (307 // 3, 293 // 3)
    # Clock type 4 is not implemented.
    with pytest.raises(ValueError):
        proto.trigger_position_words(1, 4, 100)
    with pytest.raises(ValueError):
        proto.trigger_position_words(1, 5, 100)


def test_trigger_position_write_frames():
    # Registers 0x100004 and 0x100006, LE16 payloads, C1 frames.
    frames = proto.trigger_position_frames(0, 1, 100)
    assert len(frames) == 2
    assert frames[0] == proto.frame_c1(proto.wire_address(0x100004), proto.payload_word(107))
    assert frames[1] == proto.frame_c1(proto.wire_address(0x100006), proto.payload_word(93))


def test_term_states_pack_msb_first():
    # 34 term states, two bits each, channel 0 most significant:
    # 0 = don't care, 1 = A, 2 = B, 3 = both.
    terms = [0] * 34
    terms[0] = 1  # channel 0 -> mask A, highest bit pair
    terms[33] = 2  # channel 33 -> mask B, lowest bit pair
    terms[17] = 3  # both masks
    mask_a, mask_b = proto.pack_term_states(terms)
    # Each mask is 34 bits, channel i at bit (33 - i): A accumulates
    # states 1 and 3, B accumulates 2 and 3.
    assert mask_a == (1 << 33) | (1 << 16)
    assert mask_b == (1 << 0) | (1 << 16)
    # 40-bit carriers, fitting the usbWrite40 payloads.
    proto.payload_40(mask_a)
    proto.payload_40(mask_b)
    with pytest.raises(ValueError):
        proto.pack_term_states([0] * 33)
    with pytest.raises(ValueError):
        proto.pack_term_states([4] + [0] * 33)


def test_sequencer_write_plan():
    # 15 ordered writes per segment at 0x200000+ (seg0) / 0x600000+ (seg1)
    # when terms AND state terms are active; the condition rows drop
    # without terms and the state rows drop without state terms.
    terms = [0] * 34
    terms[0] = 1
    writes = proto.sequencer_write_plan(
        segment=0,
        terms=terms,
        state_terms_active=True,
        term_type=1,
        dwell1=0,
        condition_mask_a=1 << 33,
        condition_mask_b=0,
        state_mask_a=0,
        state_mask_b=0,
        control=0,
        match_mask=0xFF,
        param1=100,
        param1_dword=True,
        param2=7,
        param2_dword=False,
        flag=0,
        match_and=0,
        control2=0,
    )
    assert len(writes) == 15
    # Without state terms the two state-mask rows drop: 13 writes.
    writes_no_state = proto.sequencer_write_plan(
        segment=0,
        terms=terms,
        state_terms_active=False,
        term_type=1,
        dwell1=0,
        condition_mask_a=1 << 33,
        condition_mask_b=0,
        state_mask_a=0,
        state_mask_b=0,
        control=0,
        match_mask=0xFF,
        param1=100,
        param1_dword=True,
        param2=7,
        param2_dword=False,
        flag=0,
        match_and=0,
        control2=0,
    )
    assert len(writes_no_state) == 13
    base = proto.SEQUENCER_SEGMENT_BASES[0]
    assert base == 0x200000
    order = [(register, len(payload)) for register, payload, _ in writes]
    assert order[0] == (base + 0x12, 1)  # term-type byte first
    assert order[1] == (base + 0x1A, 5)  # mask A accumulator (40-bit)
    assert order[2] == (base + 0x1F, 5)  # mask B accumulator
    assert order[3] == (base + 0x24, 5)  # dwell value 1
    assert order[4] == (base + 0x00, 5)  # condition mask A
    assert order[5] == (base + 0x05, 5)  # condition mask B
    assert order[9] == (base + 0x19, 1)  # match mask
    assert order[10] == (base + 0x0E, 4)  # param 1 as dword
    assert order[11] == (base + 0x0A, 2)  # param 2 as word
    assert order[-1] == (base + 0x17, 1)  # control byte 2
    # Segment 1 uses the 0x600000 base.
    writes1 = proto.sequencer_write_plan(
        segment=1,
        terms=terms,
        state_terms_active=False,
        term_type=1,
        dwell1=0,
        condition_mask_a=1 << 33,
        condition_mask_b=0,
        state_mask_a=0,
        state_mask_b=0,
        control=0,
        match_mask=0,
        param1=0,
        param1_dword=False,
        param2=0,
        param2_dword=False,
        flag=0,
        match_and=0,
        control2=0,
    )
    assert all(register >= 0x600000 for register, _, _ in writes1)
    with pytest.raises(ValueError):
        proto.sequencer_write_plan(
            segment=2, terms=terms, state_terms_active=False, term_type=0, dwell1=0,
            condition_mask_a=0, condition_mask_b=0, state_mask_a=0,
            state_mask_b=0, control=0, match_mask=0, param1=0,
            param1_dword=False, param2=0, param2_dword=False,
            flag=0, match_and=0, control2=0,
        )


def test_wire_address_families_low16():
    # The record's register addresses encode low-16 (flagged assumption
    # for the page bits — the doc addresses are authoritative).
    assert proto.wire_address(0x40000000) == 0x0000
    assert proto.wire_address(0x40000001) == 0x0001
    assert proto.wire_address(0x40000005) == 0x0005
    assert proto.wire_address(0x100004) == 0x0004
    assert proto.wire_address(0x100006) == 0x0006
    assert proto.wire_address(0x200012) == 0x0012
    assert proto.wire_address(0x600012) == 0x0012

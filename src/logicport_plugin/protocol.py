"""LogicPort wire protocol: framing, registers, .ccf decode, sample decode.

Evidence: ``LogicPort-Protocol.md`` (static analysis of LogicPort.exe
v3.4.0.2371 plus the 2026-10-12 hardware probes). Every function here is a
pure transform of bytes or numbers — no device I/O, no host services. The
adapter owns the transports; this module owns the encoding the evidence
pins.

Open items (undecoded in the source, deliberately ``None`` here so a fix is
one assignment): the destination registers of the configuration writes
(threshold DAC word, trigger masks A/B) and the 1-byte payload of the C1
rate command. The adapter refuses those operations loudly while the slots
are unset — it never invents a register address.
"""

from __future__ import annotations

from typing import NamedTuple

# --- Transport identity (Protocol §1; live check 2026-10-12) ---------------

FTDI_VID = 0x0403
FTDI_PID = 0xDC48

# --- Wire opcodes (Protocol §3) -------------------------------------------

CMD_WRITE = 0xC1
CMD_READ = 0xC2
CMD_STOP = 0xC3

#: The C1/C2 16-bit address (clock/rate) word is little-endian on the wire;
#: C2's N and C1's L are little-endian too. Response VALUES are big-endian
#: (Protocol §3.2: usbReadWord/usbReadDword read big-endian).

RATE_SELECT_SHIFT = 65536  # clock select = value / 65536 (Protocol §3.1)

#: FTDI control dialect (Protocol §1/§4/§5): the adapter reaches bit-bang
#: mode changes, pin polls, purges and the device reset only through the
#: scoped transport, as custom ``ftdi_control`` transactions. A pinned
#: transport-provider contract is the long-term home for this grammar; the
#: kinds never shadow the reserved section 8.1 stream kinds.
DIALECT_KIND = "ftdi_control"

BITMODE_RESET = 0x00
BITMODE_BITBANG = 0x01
BITBANG_MASK_FPGA = 0x07  # D0-D2 outputs (Protocol §5 step 3)
FPGA_DONE_PIN = 0x10  # input D4 (Protocol §5 step 5)
FPGA_INIT_PIN = 0x08  # input D3 (Protocol §5 step 8)
FPGA_PULSE_START = 0x00  # config-start pulse byte (Protocol §5 step 4)
FPGA_BITSTREAM_START = 0x04  # bitstream-start byte (Protocol §5 step 7)

#: Init constants (Protocol §4): timeouts 5000/5000 ms, latency timer 4,
#: baud 460800.
INIT_READ_TIMEOUT_MS = 5000
INIT_WRITE_TIMEOUT_MS = 5000
INIT_LATENCY_MS = 4
INIT_BAUD = 460800

#: Bitstream chunks of at most 10000 bytes, no response framing (§5 step 9).
BITSTREAM_CHUNK_MAX = 10000

#: Resync flush (Protocol §3.4): 65535 zero bytes (>= 65534 sent), then
#: 30000 zero bytes, then one 0x01 marker byte.
RESYNC_FLUSH_LONG = 65535
RESYNC_FLUSH_LONG_MIN_SENT = 65534
RESYNC_FLUSH_SHORT = 30000
RESYNC_MARKER = 0x01
RESYNC_ATTEMPTS_MAX = 20

# --- Register map (Protocol §5b) ------------------------------------------
#
# The documented register addresses are 24- to 32-bit; the C1/C2 frame
# carries a 16-bit address field. NO wire mapping is hardware-proven: the
# 2026-10-26 read-only probes (record section 10) got the same zero-data
# reply shape at every address tried, so the low-16 decomposition below is
# consistent with the observed ACK framing and untested against register
# data. The sample-plane addresses live in one page (0x02000000) whose
# offsets fit the 16-bit field without collision, so they are encoded
# low-16 too.
# Three documented addresses collide on low-16 (0x1000000, 0x2000000,
# 0x40000000 all reduce to 0x0000) — their wire encoding is NOT decoded,
# and :func:`wire_address` refuses them rather than guessing a page-bank.

REG_STATUS = 0x100000  # read: status read — the §5c bit decode is retracted (2026-10-26, #562)
REG_WIRE_STATUS = 0x100001  # read: 9 wire-status bytes
REG_CAPTURE_COMPLETE_WRITE = 0x100002  # write at capture completion
REG_CAPTURE_COMPLETE_WRITE_B = 0x100003
REG_TRIGGER_POSITION = 0x10000A  # read: trigger position word
REG_REARM_A = 0x10000B  # write: recurring-acquisition re-arm
REG_REARM_B = 0x10000C

#: Sample-plane base addresses (Protocol §6.1, 0x02000000 page).
PLANE_D0_D7 = 0x02000101
PLANE_D8_D15 = 0x02002000
PLANE_D16_D23 = 0x02003000
PLANE_D24_D31 = 0x02004000
PLANE_FLAGS = 0x02009000

#: The five plane bases in channel order, flags last.
PLANE_BASES = (PLANE_D0_D7, PLANE_D8_D15, PLANE_D16_D23, PLANE_D24_D31, PLANE_FLAGS)

#: Ping-pong page stride within the sample page. ASSUMPTION (flagged): the
#: spec derives a page base of (tp + 1 + prescale) mod 0x400 and reads ten
#: chunks — five planes from each of two capture pages — but does not state
#: the second page's address. A 0x400 stride keeps every plane+base inside
#: the 16-bit field and matches the plane geometry ((N+1)/2 <= 1024 bytes).
PAGE_STRIDE = 0x400

#: Column bound: N <= 0x7FF (2047) per Protocol §6.1/§6.7; a column covers
#: two channels-planes-worth of samples, so a chunk is (N+1)/2 bytes.
COLUMN_COUNT_MAX = 0x7FF

#: The .ccf bitstream (Protocol §5): SHA-256 of the LogicPort.ccf that the
#: 2026-10-12 hardware load proved (DONE asserted, §9). The adapter refuses
#: any other bitstream.
CCF_SHA256 = "9298a5eacb3cf1a791b8ce48460a19e031eb81baffa378bab3f94f2d78483f9c"
CCF_XOR_KEY = 0x916662
CCF_HEADER_FIELDS = 9
CCF_SLOT_COUNT = 8
PAGE_BYTES = 4096

# --- OPEN ITEMS: destination not decoded (Protocol §3.1/§5a) --------------
#
# Each slot is the single place to assign once the acquisition-decode pass
# pins the address. The adapter refuses the matching operation while unset.

#: usbWriteWord destination of the threshold DAC code. CLOSED: the
#: follow-up decode lane pinned the dispatcher's address-slot constant to
#: 0x100008 (2^20+8) — the same slot digest that fixed the
#: trigger-position writes at 0x100004/0x100006.
REGISTER_THRESHOLD_DAC: int | None = 0x100008
#: usbWrite40 destinations of the trigger masks (Protocol §5a).
REGISTER_TRIGGER_MASK_A: int | None = None
REGISTER_TRIGGER_MASK_B: int | None = None
#: The 1-byte usbWriteByte payload of the C1 rate command (Protocol §5
#: step 13 routes it through FUN_004ca679); its value is not decoded.
RATE_COMMAND_PAYLOAD: int | None = None
#: The reply header's aux byte meaning — UNPROVEN (#562): parsed,
#: recorded as evidence, never interpreted. Register-slot pattern (fold
#: R10): one assignment pins the decode once a probe observes meaning.
AUX_SEMANTICS: int | None = None

# --- Evidence taxonomy (fold R16, #562) ------------------------------------
#
# Every evidence kind the adapter's record_evidence call sites emit is
# named here, once. The adapter's call sites reference these names, so a
# typo at a call site is a NameError, not a silent new kind. EVIDENCE_KINDS
# is exactly their union: the taxonomy's one definition.

EVIDENCE_STATUS_AUX = "logicport.status_aux"
EVIDENCE_WRITE_ACK = "logicport.write_ack"
EVIDENCE_COUNTER_GAP = "logicport.counter_gap"
EVIDENCE_COUNTER_REBASELINED = "logicport.counter_rebaselined"
EVIDENCE_CAPTURE_GATE = "logicport.capture_gate"
EVIDENCE_C3_DRAIN = "logicport.c3_drain"
EVIDENCE_CONFIGURE_PENDING_WRITES = "logicport.configure.pending_writes"
EVIDENCE_FETCH_DATASET = "logicport.fetch.dataset"

EVIDENCE_KINDS = frozenset(
    {
        EVIDENCE_STATUS_AUX,
        EVIDENCE_WRITE_ACK,
        EVIDENCE_COUNTER_GAP,
        EVIDENCE_COUNTER_REBASELINED,
        EVIDENCE_CAPTURE_GATE,
        EVIDENCE_C3_DRAIN,
        EVIDENCE_CONFIGURE_PENDING_WRITES,
        EVIDENCE_FETCH_DATASET,
    }
)


# --- Frame builders (Protocol §3.1-§3.3) ----------------------------------


def pack_rate_word(rate_value: int, clock_select: int = 0) -> int:
    """Pack the C1/C2 address word: low 16 bits rate value, upper clock select.

    Protocol §3.1: the app packs rate and clock select into one value; the
    clock select is Int(value / 65536.0).
    """
    if not isinstance(rate_value, int) or isinstance(rate_value, bool):
        raise ValueError("rate_value must be an int")
    if not isinstance(clock_select, int) or isinstance(clock_select, bool):
        raise ValueError("clock_select must be an int")
    if not 0 <= rate_value <= 0xFFFF or not 0 <= clock_select <= 0xFFFF:
        raise ValueError("rate_value and clock_select must fit 16 bits")
    return (clock_select << 16) | rate_value


def frame_c1(word: int, payload: bytes) -> bytes:
    """Build a C1 write frame: ``C1 w0 w1 L0 L1 [payload...]`` (§3.1).

    ``word`` is the little-endian 16-bit address/rate word; the payload is
    L+1 bytes (L = len(payload) - 1), so a C1 always carries at least one
    payload byte — the usbWriteByte/Word/Dword/40 wrappers.
    """
    if not isinstance(word, int) or isinstance(word, bool) or not 0 <= word <= 0xFFFF:
        raise ValueError("word must be a 16-bit int")
    if not isinstance(payload, bytes) or not payload:
        raise ValueError("a C1 frame needs at least one payload byte")
    if len(payload) > 0x10000:
        raise ValueError("payload exceeds 65536 bytes")
    length = len(payload) - 1
    return bytes([CMD_WRITE, word & 0xFF, (word >> 8) & 0xFF, length & 0xFF, (length >> 8) & 0xFF]) + payload


def frame_c2(word: int, n: int) -> bytes:
    """Build a C2 read-request frame: ``C2 w0 w1 n0 n1`` (§3.2).

    ``n`` is how many response bytes the device must send back.
    """
    if not isinstance(word, int) or isinstance(word, bool) or not 0 <= word <= 0xFFFF:
        raise ValueError("word must be a 16-bit int")
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 0xFFFF:
        raise ValueError("n must be a 16-bit int >= 1")
    return bytes([CMD_READ, word & 0xFF, (word >> 8) & 0xFF, n & 0xFF, (n >> 8) & 0xFF])


def frame_c3(word: int = 0) -> bytes:
    """Build a C3 stop frame: ``C3 w0 w1 00 00`` (§3.3).

    The rate bytes carry the current cached rate; the last two are zero.
    """
    if not isinstance(word, int) or isinstance(word, bool) or not 0 <= word <= 0xFFFF:
        raise ValueError("word must be a 16-bit int")
    return bytes([CMD_STOP, word & 0xFF, (word >> 8) & 0xFF, 0, 0])


# --- Payload wrappers (Protocol §3.1: USB_IO.bas) -------------------------


def payload_byte(value: int) -> bytes:
    """usbWriteByte payload: 1 byte."""
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFF:
        raise ValueError("value must be a byte")
    return bytes([value])


def payload_word(value: int) -> bytes:
    """usbWriteWord payload: 2 bytes little-endian."""
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFFFF:
        raise ValueError("value must fit 16 bits")
    return value.to_bytes(2, "little")


def payload_dword(value: int) -> bytes:
    """usbWriteDword payload: 4 bytes little-endian."""
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError("value must fit 32 bits")
    return value.to_bytes(4, "little")


def payload_40(value: int) -> bytes:
    """usbWrite40 payload: 5 bytes little-endian 40-bit value.

    Carries the 34-channel trigger masks and dwell values (§3.1, §5a).
    """
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFFFFFFFFFF:
        raise ValueError("value must fit 40 bits")
    return value.to_bytes(5, "little")


def trigger_mask_payload(mask: int) -> bytes:
    """A 34-channel trigger mask as its usbWrite40 payload (§5a).

    Each mask is 34 bits, one bit per channel; two masks (A and B) are
    sent per segment.
    """
    if not isinstance(mask, int) or isinstance(mask, bool) or not 0 <= mask < (1 << 34):
        raise ValueError("trigger mask must fit 34 bits")
    return payload_40(mask)


# --- Response value parsers (Protocol §3.2) -------------------------------


def parse_read_byte(data: bytes) -> int:
    """usbReadByte: one byte at the cursor."""
    if not isinstance(data, bytes) or not data:
        raise ValueError("expected at least one response byte")
    return data[0]


def parse_read_word(data: bytes) -> int:
    """usbReadWord: two bytes, BIG-endian (Protocol §3.2/§3.5)."""
    if not isinstance(data, bytes) or len(data) < 2:
        raise ValueError("expected at least two response bytes")
    return int.from_bytes(data[:2], "big")


def parse_read_dword(data: bytes) -> int:
    """usbReadDword: four bytes, BIG-endian (Protocol §3.2/§3.5)."""
    if not isinstance(data, bytes) or len(data) < 4:
        raise ValueError("expected at least four response bytes")
    return int.from_bytes(data[:4], "big")


# --- Status byte (Protocol §5c) — RETRACTED 2026-10-26 (#562) --------------
#
# The status-bit decode this section carried (bit 0 capture complete,
# bit 1 immediate trigger, bits 6:5 pre-fill phase) is retracted: the
# 2026-10-26 probes proved the byte read at that register advances with
# every command — it is the command counter (see the reply grammar
# below). No capture-complete, immediate-trigger or pre-fill semantics
# are proven. The aux header byte is parsed and recorded; its meaning is
# unproven. `Adapter._read_status` consumes the proven reply frame and
# reports `complete: None` until a gate source is pinned.


# --- Threshold DAC (Protocol §5a) ------------------------------------------

_THRESHOLD_T_OFFSET = 1.315
_THRESHOLD_T_SLOPE = 0.4020381098624974
_THRESHOLD_DAC_SPAN_A = 4.559
_THRESHOLD_DAC_SPAN_B = 5.875
_THRESHOLD_DAC_STEPS = 1023
THRESHOLD_VOLTS_MIN = -6.0
THRESHOLD_VOLTS_MAX = 6.0


def threshold_dac_code(volts: float) -> int:
    """Convert threshold volts to the 10-bit DAC code (Protocol §5a).

    ``T = 1.315 + 0.4020381098624974 * (1.315 - V)`` then
    ``code = Round(((4.559 - T) / 5.875) * 1023)``. The documented check
    points: +6 V -> 893, 0 V -> 473, -6 V -> 53 (about 14.3 mV per code).
    Half-up rounding (the C Round the app uses); the check points are not
    half-integers, so half-even would agree there too.
    """
    if isinstance(volts, bool) or not isinstance(volts, (int, float)):
        raise ValueError("volts must be a number")
    v = float(volts)
    if not THRESHOLD_VOLTS_MIN <= v <= THRESHOLD_VOLTS_MAX:
        raise ValueError("threshold must be within +/-6 V")
    t = _THRESHOLD_T_OFFSET + _THRESHOLD_T_SLOPE * (_THRESHOLD_T_OFFSET - v)
    import math

    code = math.floor(((_THRESHOLD_DAC_SPAN_A - t) / _THRESHOLD_DAC_SPAN_B) * _THRESHOLD_DAC_STEPS + 0.5)
    return int(code)


# --- Sample mode byte (Protocol §5a) ---------------------------------------


def sample_mode_byte(clk_state: int, other_state: int) -> int:
    """The sample-mode byte: ``2*((NOT clkState) AND 2) + clkState + 8*(otherState XOR 1)``.

    AMBIGUOUS (flagged): the source formula names only two of the three
    state bits and uses the VB-style integer complement (Not 0 = -1,
    Not 1 = -2), under which the first term is always 4. Values are
    unverified; no destination register is decoded, so the adapter does
    not send this byte yet.
    """
    if not isinstance(clk_state, int) or isinstance(clk_state, bool) or clk_state not in (0, 1):
        raise ValueError("clk_state must be 0 or 1")
    if not isinstance(other_state, int) or isinstance(other_state, bool) or other_state not in (0, 1):
        raise ValueError("other_state must be 0 or 1")
    return 2 * ((~clk_state) & 2) + clk_state + 8 * (other_state ^ 1)


# --- Register wire addressing (Protocol §5b) ------------------------------


def wire_address(register: int) -> int:
    """Map a documented register address to the 16-bit C1/C2 address word.

    The frame field is 16 bits (§5b). NO mapping here is hardware-proven:
    the 2026-10-26 read-only probes (record section 10) returned the same
    zero-data reply shape for every address tried, so the low-16
    decomposition is consistent with the observed ACK framing and
    untested against register data — a read-only probe cannot
    discriminate it, and the discriminating experiment (register write,
    then read-back) has not been run. The record's page-qualified
    addresses (status 0x100000, sequencer 0x200000/0x600000, sample
    0x02000000, counter 0x40000000, trigger word 0x1000000) encode low-16
    here on that assumption. FLAGGED: several families collide on low-16
    (status 0x100000 and counter select 0x40000000 both reduce to 0x0000,
    and sequencer segments 0 and 1 share every offset word); this function
    is the one place to fix when a register-data probe pins the
    decomposition.
    """
    if not isinstance(register, int) or isinstance(register, bool) or register < 0:
        raise ValueError("register must be a nonnegative int")
    return register & 0xFFFF


def sample_plane_wire_address(plane: int, page_base: int) -> int:
    """Wire address of one sample-plane chunk (Protocol §6.1).

    ``plane`` indexes PLANE_BASES (0-3 channel data, 4 flags); the page
    base is ``(tp + 1 + prescale) mod 0x400`` (plus PAGE_STRIDE for the
    second ping-pong page). The plane bases share one page whose offsets
    fit the 16-bit field without collision.
    """
    if not isinstance(plane, int) or isinstance(plane, bool) or not 0 <= plane < len(PLANE_BASES):
        raise ValueError("plane must be 0..4")
    if not isinstance(page_base, int) or isinstance(page_base, bool) or page_base < 0:
        raise ValueError("page_base must be a nonnegative int")
    return wire_address(PLANE_BASES[plane] + page_base)


# --- .ccf decode (Protocol §5, §5 step 6) ----------------------------------


class CcfHeader(NamedTuple):
    """The decoded .ccf CSV header (nine XOR-obfuscated decimal values)."""

    raw: tuple[int, ...]  # the nine CSV values as written
    decoded: tuple[int, ...]  # after XOR 0x916662
    slot_lengths: tuple[int, ...]  # L[i] = decoded[i] * 16, i in 0..7
    checksum: int  # decoded[8]
    chain: int  # XOR of decoded[0..7] — the advisory chain checksum
    body_offset: int  # byte offset of the bitstream body (past the LF)

    @property
    def checksum_ok(self) -> bool:
        """The XOR-chain checksum matches the stored header value.

        The C loader treats a mismatch as advisory, not fatal; the header
        decode of the real file matches exactly (chain 0x1fa2b, §9).
        """
        return self.chain == self.checksum


def parse_ccf_header(data: bytes) -> CcfHeader:
    """Decode the .ccf text header: nine CSV values XOR 0x916662 (§5 step 6).

    Raises ValueError on a malformed header (no LF, wrong field count,
    non-decimal fields).
    """
    if not isinstance(data, bytes) or not data:
        raise ValueError("empty .ccf data")
    try:
        lf = data.index(b"\n")
    except ValueError:
        raise ValueError(".ccf header line is not LF-terminated") from None
    try:
        line = data[:lf].decode("ascii")
    except UnicodeDecodeError:
        raise ValueError(".ccf header is not ASCII") from None
    fields = line.split(",")
    if len(fields) != CCF_HEADER_FIELDS:
        raise ValueError(f".ccf header must carry {CCF_HEADER_FIELDS} values, got {len(fields)}")
    try:
        raw = tuple(int(field.strip()) for field in fields)
    except ValueError:
        raise ValueError(".ccf header fields must be decimal integers") from None
    decoded = tuple(value ^ CCF_XOR_KEY for value in raw)
    slot_lengths = tuple(decoded[i] * 16 for i in range(CCF_SLOT_COUNT))
    if any(length <= 0 for length in slot_lengths):
        raise ValueError(".ccf slot lengths must be positive")
    chain = 0
    for value in decoded[:CCF_SLOT_COUNT]:
        chain ^= value
    return CcfHeader(
        raw=raw,
        decoded=decoded,
        slot_lengths=slot_lengths,
        checksum=decoded[CCF_SLOT_COUNT],
        chain=chain,
        body_offset=lf + 1,
    )


def ccf_slot_bounds(header: CcfHeader, slot: int) -> tuple[int, int]:
    """Byte (offset, length) of one bitstream slot (§5 step 6).

    Slots tile the file exactly: O[0] is the body offset and each next
    offset adds the previous slot's length. Slot 0 of the real file is
    offset 73, length 1057840 (hardware-proven, §9).
    """
    if not isinstance(slot, int) or isinstance(slot, bool) or not 0 <= slot < CCF_SLOT_COUNT:
        raise ValueError("slot must be 0..7")
    offset = header.body_offset
    for i in range(slot):
        offset += header.slot_lengths[i]
    return offset, header.slot_lengths[slot]


def extract_ccf_slot(data: bytes, header: CcfHeader, slot: int) -> bytes:
    """Copy one slot's bitstream bytes out of the .ccf file."""
    offset, length = ccf_slot_bounds(header, slot)
    if offset + length > len(data):
        raise ValueError("slot overruns the .ccf file")
    return bytes(data[offset : offset + length])


def swap_first_pages(payload: bytes) -> bytes:
    """Swap the first two 4096-byte pages of a bitstream slot (§5 step 6).

    The C loader swaps only when the slot is longer than 8192 bytes — an
    exact-8192 slot is left alone. Proven on hardware with slot 0
    (1057840 bytes, §9).
    """
    if not isinstance(payload, bytes):
        raise ValueError("payload must be bytes")
    if len(payload) <= PAGE_BYTES * 2:
        return payload
    return payload[PAGE_BYTES : 2 * PAGE_BYTES] + payload[:PAGE_BYTES] + payload[2 * PAGE_BYTES :]


# --- Sample decode (Protocol §6) -------------------------------------------

RLE_FLAG_BIT = 0x08  # flag byte bit 3 marks a run column (§6.5)
CHANNEL_COUNT = 34  # D0-D31 + CLK1 (ch32) + CLK2 (ch33)
FLAG_BIT_CLK1_EVEN = 1  # even columns: flag bit 1 = CLK1, bit 2 = CLK2
FLAG_BIT_CLK2_EVEN = 2
FLAG_BIT_CLK1_ODD = 5  # odd columns: flag bit 5 = CLK1, bit 6 = CLK2
FLAG_BIT_CLK2_ODD = 6


def decode_rle_count(planes: bytes) -> int:
    """The 35-bit run count of a run column (§6.5).

    ``planes`` is the five-plane column: planes[0..3] are the four data
    planes (plane1..plane4 of the formula, plane1 least-significant) and
    planes[4] is the flag byte whose low three bits supply bits 34:32::

        count = (flag & 7) << 32 | plane3 << 24 | plane2 << 16
                | plane1 << 8 | plane0

    Counts reach 2^35 - 1; a run column repeats the previous column state
    for ``count`` sample periods.
    """
    if not isinstance(planes, bytes) or len(planes) != 5:
        raise ValueError("a column is exactly five plane bytes")
    flag = planes[4]
    return ((flag & 0x07) << 32) | (planes[3] << 24) | (planes[2] << 16) | (planes[1] << 8) | planes[0]


def decode_sample_state(planes: bytes, *, odd_column: bool) -> int:
    """Decode one sample column into a 34-bit channel state (§6.1, §6.3).

    A stored bit 0 means the channel is HIGH, a stored bit 1 means LOW —
    the storage is inverted, so each channel bit is complemented. The
    flags plane carries the two clock channels: bits 1/2 on even columns,
    bits 5/6 on odd columns, inverted like the rest of the plane (§6.1
    "All inverted"; §6.3). Bit i of the result is channel i; bits 32/33
    are CLK1/CLK2.
    """
    if not isinstance(planes, bytes) or len(planes) != 5:
        raise ValueError("a column is exactly five plane bytes")
    state = 0
    for channel in range(32):
        stored = (planes[channel // 8] >> (channel % 8)) & 1
        state |= (1 - stored) << channel
    if odd_column:
        clk1 = (planes[4] >> FLAG_BIT_CLK1_ODD) & 1
        clk2 = (planes[4] >> FLAG_BIT_CLK2_ODD) & 1
    else:
        clk1 = (planes[4] >> FLAG_BIT_CLK1_EVEN) & 1
        clk2 = (planes[4] >> FLAG_BIT_CLK2_EVEN) & 1
    state |= (1 - clk1) << 32
    state |= (1 - clk2) << 33
    return state


def deinterleave_pages(page_a: list[bytes], page_b: list[bytes]) -> list[tuple[bytes, bool]]:
    """Interleave two ping-pong capture pages into decode columns (§6.2).

    Each page is five plane buffers of equal length. Even column ``2j``
    comes from page A byte ``j``; odd column ``2j+1`` from page B byte
    ``j``. Returns, in column order, ``(planes5, odd_column)`` tuples.
    """
    if len(page_a) != len(PLANE_BASES) or len(page_b) != len(PLANE_BASES):
        raise ValueError("each page is five planes")
    length = len(page_a[0])
    if any(len(plane) != length for plane in page_a + page_b):
        raise ValueError("planes of one page must share a length")
    columns: list[tuple[bytes, bool]] = []
    for j in range(length):
        even = bytes(page_a[plane][j] for plane in range(5))
        odd = bytes(page_b[plane][j] for plane in range(5))
        columns.append((even, False))
        columns.append((odd, True))
    return columns


class DecodedColumns(NamedTuple):
    """The three arrays of the §6.5 decode loop."""

    columns: tuple[int, ...]  # 34-bit state per column (run columns carry)
    runs: tuple[int, ...]  # run count per column, 0 for sample columns
    col_time: tuple[int, ...]  # cumulative sample position per column
    total_samples: int  # sample columns + sum of run counts


def decode_columns(columns: list[tuple[bytes, bool]]) -> DecodedColumns:
    """Run the §6.5 decode loop over interleaved columns.

    A sample column (flag bit 3 clear) decodes its channels; a run column
    (bit 3 set) carries the previous channel state for ``count`` sample
    periods. ``col_time`` is the cumulative sample position of each
    column; the total is the sample columns plus every run count.
    """
    states: list[int] = []
    runs: list[int] = []
    times: list[int] = []
    position = 0
    previous = 0
    for planes, odd in columns:
        times.append(position)
        if planes[4] & RLE_FLAG_BIT:
            count = decode_rle_count(planes)
            states.append(previous)
            runs.append(count)
            position += count
        else:
            previous = decode_sample_state(planes, odd_column=odd)
            states.append(previous)
            runs.append(0)
            position += 1
    return DecodedColumns(
        columns=tuple(states),
        runs=tuple(runs),
        col_time=tuple(times),
        total_samples=position,
    )


def trigger_column_raw(column_count: int, trigger_word: int) -> int:
    """Trigger column without the pipeline compensation (§6.7).

    ``triggerColumn = ((N - trigWord) + 1) mod (N + 1)`` where trigWord is
    the trigger word (register 0x1000000) masked to 0x7FF.
    """
    if not isinstance(column_count, int) or isinstance(column_count, bool) or column_count < 1:
        raise ValueError("column_count must be >= 1")
    if not isinstance(trigger_word, int) or isinstance(trigger_word, bool) or not 0 <= trigger_word <= 0x7FF:
        raise ValueError("trigger_word must fit 11 bits")
    return ((column_count - trigger_word) + 1) % (column_count + 1)


def trigger_column_compensated(column_count: int, trigger_word: int, pipeline: int = 21) -> int:
    """Trigger column with the pipeline delay compensation (§6.4).

    ``triggerColumn = ((N - triggerWord + 1) mod (N+1)) - 21`` — the
    constant 21 compensates the pipeline delay (§6.4); §6.7's raw form is
    :func:`trigger_column_raw`. May go negative when the raw column sits
    near zero; the caller clamps per its mode.
    """
    if not isinstance(pipeline, int) or isinstance(pipeline, bool) or pipeline < 0:
        raise ValueError("pipeline must be a nonnegative int")
    return (trigger_column_raw(column_count, trigger_word) - pipeline)


# --- Hardware-observed reply grammar (record section 10, 2026-10-26) -------
#
# Read-only probes of the live device (tools/addr_probe.c reads,
# tools/write_probe.c writes; no register writes). This section records
# OBSERVED WIRE SHAPE only: every probed address returned zero data, so no
# register value, page bit or status bit is confirmed by it. The adapter
# consumes this grammar (#562 wire truth): every C2 exchange parses the
# count+4 reply through :func:`parse_reply`, every write ACK parses
# through :func:`parse_write_ack`, and one command counter sequences both
# directions (continuity enforced, wrap-aware).
#
# Read reply: count+4 bytes  [cmd_echo, 0x00, counter, aux] + count payload
# bytes, values big-endian. Write ACK: exactly 3 bytes [CMD_WRITE, 0x00,
# counter]. The aux byte's meaning is UNPROVEN — parse, record, never
# interpret. Where the payload boundary sits inside the count+4 total is
# the one open interpretation: :func:`parse_reply` treats byte 3 as the
# aux header and the payload as the count bytes (the shape the adapter
# consumes); :func:`parse_read_reply` treats byte 3 as the first of
# count+1 data bytes (the record's L+1-mirror reading). Both agree on the
# total length; every probed address answered zeros, so no register data
# discriminates them — a register-data probe is the experiment that
# closes it.

#: A read reply runs the requested count plus four bytes: `C2`, `00`, the
#: command counter, the aux byte, then the payload (see the boundary note
#: above). Every probed address answered zeros.
READ_REPLY_HEADER_BYTES = 4
#: A write ACK is exactly three bytes: C1, a zero byte, the same command
#: counter the read replies carry. The ACK-shape open item is closed at
#: the grammar level; the ACK payload semantics stay open.
WRITE_ACK_BYTES = 3
#: One write answered in about 119 ms on the live device. HYPOTHESIS
#: (record section 10): poll loops pace at this cadence — observed once,
#: not established as a requirement.
WRITE_ACK_LATENCY_MS = 119


def read_reply_length(count: int) -> int:
    """The observed read-reply length: the requested count plus four."""
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError("count must be a positive int")
    return count + READ_REPLY_HEADER_BYTES


class ReplyFrame(NamedTuple):
    """One parsed reply through the hardware-proven grammar (#562)."""

    cmd_echo: int
    counter: int
    aux: int
    payload: bytes


def parse_reply(data: bytes, expected_cmd: int = CMD_READ) -> ReplyFrame:
    """Parse a read reply of the hardware-proven shape."""
    if not isinstance(data, bytes) or len(data) < 4:
        raise ValueError("reply needs at least 4 header bytes")
    if data[0] != expected_cmd:
        raise ValueError(f"command echo {data[0]:#04x} != {expected_cmd:#04x}")
    if data[1] != 0:
        raise ValueError(f"second byte {data[1]:#04x} is not 0x00")
    return ReplyFrame(data[0], data[2], data[3], data[4:])


def parse_write_ack(data: bytes) -> ReplyFrame:
    """Parse the 3-byte write ACK [CMD_WRITE, 0x00, counter]."""
    if not isinstance(data, bytes) or len(data) != WRITE_ACK_BYTES:
        raise ValueError(f"write ACK is exactly {WRITE_ACK_BYTES} bytes")
    if data[0] != CMD_WRITE:
        raise ValueError(f"ACK echo {data[0]:#04x} != {CMD_WRITE:#04x}")
    if data[1] != 0:
        raise ValueError(f"ACK second byte {data[1]:#04x} is not 0x00")
    return ReplyFrame(data[0], data[2], 0, b"")


def parse_read_reply(data: bytes, count: int) -> tuple[int, bytes]:
    """Split an observed read reply into ``(counter, data)``.

    The frame is ``C2 00 <counter>`` then count+1 data bytes; the total
    length is the requested count plus four (the payload-boundary
    interpretation differs from :func:`parse_reply` — see the section
    note; no register data discriminates them). The data bytes come back
    verbatim — nothing here decodes register values (every probed
    address answered zeros; the status/register layer is not
    hardware-decoded, record section 10).
    """
    if not isinstance(data, bytes):
        raise ValueError("a reply is bytes")
    expected = read_reply_length(count)
    if len(data) != expected:
        raise ValueError(
            f"a read reply of count {count} is exactly {expected} bytes, got {len(data)}"
        )
    if data[0] != CMD_READ:
        raise ValueError("a read reply echoes C2")
    if data[1] != 0x00:
        raise ValueError("read-reply byte 1 is 00 on every observed frame")
    return data[2], data[3:]


def counter_step(previous: int, current: int) -> str:
    """Classify one step of the shared command counter.

    HYPOTHESIS (record section 10): one counter covers reads and writes,
    so ``"ok"`` (a contiguous step) is the only healthy transition, a
    ``"gap"`` means a lost frame, and a ``"repeat"`` means a duplicated
    one. The counter is one byte, so continuity is modular (fold R1):
    ``0xFF`` to ``0x00`` continues and does not gap — the adapter's
    :meth:`Adapter._advance_counter` enforces the same modular rule.
    """
    for name, value in (("previous", previous), ("current", current)):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFF:
            raise ValueError(f"{name} must be a byte")
    if current == previous:
        return "repeat"
    if current == ((previous + 1) & 0xFF):
        return "ok"
    return "gap"


# --- Section 8.1 stream transactions and the FTDI control dialect ---------


def stream_send(data: bytes) -> dict:
    """One send-only stream transaction (write without a framed response)."""
    if not isinstance(data, bytes) or not data:
        raise ValueError("stream_send needs bytes")
    return {"kind": "stream_send", "data": data}


def stream_receive(max_bytes: int, exact_bytes: int | None) -> dict:
    """One receive transaction with section 8.1 bounds.

    ``exact_bytes`` takes precedence over the terminator; the serial
    grammar has only the ``lf``/``crlf`` terminators, and a quiet line
    answers ``b""`` when nothing was offered.
    """
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    if exact_bytes is not None and (
        not isinstance(exact_bytes, int) or isinstance(exact_bytes, bool) or not 0 <= exact_bytes <= max_bytes
    ):
        raise ValueError("exact_bytes must be None or 0..max_bytes")
    return {"kind": "stream_receive", "max_bytes": max_bytes, "termination": "lf", "exact_bytes": exact_bytes}


def ftdi_control(op: str, **params) -> dict:
    """One FTDI control-dialect transaction (the LogicPort transport).

    The dialect carries the D2XX/libftdi control calls the protocol needs
    around the wire frames: device reset, timeouts, latency, baud, purge,
    bit-bang mode and pin polls (Protocol §1, §4, §5). It is the adapter's
    own grammar — a pinned otdp transport-provider contract is its
    long-term conformance home.
    """
    if not isinstance(op, str) or not op:
        raise ValueError("op must be a non-empty string")
    return {"kind": DIALECT_KIND, "op": op, **params}


def ftdi_reset_device() -> dict:
    """FT_ResetDevice (Protocol §4 step 3)."""
    return ftdi_control("reset_device")


def ftdi_set_timeouts(read_ms: int = INIT_READ_TIMEOUT_MS, write_ms: int = INIT_WRITE_TIMEOUT_MS) -> dict:
    """FT_SetTimeouts (Protocol §4 step 4: 5000/5000 ms)."""
    if not isinstance(read_ms, int) or isinstance(read_ms, bool) or read_ms < 0:
        raise ValueError("read_ms must be a nonnegative int")
    if not isinstance(write_ms, int) or isinstance(write_ms, bool) or write_ms < 0:
        raise ValueError("write_ms must be a nonnegative int")
    return ftdi_control("set_timeouts", read_ms=read_ms, write_ms=write_ms)


def ftdi_set_latency(ms: int = INIT_LATENCY_MS) -> dict:
    """FT_SetLatencyTimer (Protocol §4 step 5: latency 4)."""
    if not isinstance(ms, int) or isinstance(ms, bool) or not 0 <= ms <= 255:
        raise ValueError("latency must be a byte")
    return ftdi_control("set_latency", ms=ms)


def ftdi_set_baud(baud: int = INIT_BAUD) -> dict:
    """FT_SetBaudRate (Protocol §4 step 6: 460800)."""
    if not isinstance(baud, int) or isinstance(baud, bool) or baud < 1:
        raise ValueError("baud must be positive")
    return ftdi_control("set_baud", baud=baud)


def ftdi_purge(queue: str) -> dict:
    """FT_Purge of one queue: ``\"tx\"`` or ``\"rx\"`` (Protocol §3.4, §5)."""
    if queue not in ("tx", "rx"):
        raise ValueError("queue must be 'tx' or 'rx'")
    return ftdi_control("purge", queue=queue)


def ftdi_set_bitmode(mask: int, mode: int) -> dict:
    """FT_SetBitMode: pin-direction mask and interface mode (Protocol §5)."""
    if not isinstance(mask, int) or isinstance(mask, bool) or not 0 <= mask <= 0xFF:
        raise ValueError("mask must be a byte")
    if not isinstance(mode, int) or isinstance(mode, bool) or not 0 <= mode <= 0xFF:
        raise ValueError("mode must be a byte")
    return ftdi_control("set_bitmode", mask=mask, mode=mode)


def ftdi_read_pins() -> dict:
    """Read the bit-bang pin states (FT_GetBitMode / ftdi_read_pins).

    The response carries one byte; FPGA DONE is bit 0x10, INIT is 0x08
    (Protocol §5 steps 5/8/10).
    """
    return ftdi_control("read_pins")


def decode_pin_byte(data: bytes) -> int:
    """Decode a pin-poll response: one status byte."""
    if not isinstance(data, bytes) or not data:
        raise ValueError("expected one pin byte")
    return data[0]


# --- Frequency counter (protocol-spec.md, confirmed 2026-10-12) ------------

#: Multiplexed hardware frequency counter, upper register page.
REG_FREQ_SELECT = 0x40000000  # read byte: ready gate (0 = count ready); write byte: channel select 0-3
REG_FREQ_COUNT = 0x40000001  # read dword: count; Hz = count x 10
REG_FREQ_RUNNING_MAX = 0x40000005  # read byte: running maximum (fallback for channel 0)
FREQ_CHANNELS = 4

#: The confirmed poll floor: round-robin cycles wait longer than 75 ms.
#: The adapter's floor is 100 ms.
FREQ_POLL_INTERVAL_MIN_MS = 100


def frequency_hz(count: int) -> int:
    """Counter ticks to Hz: the x10 factor of the 10 Hz resolution."""
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise ValueError("count must be a nonnegative int")
    return count * 10


def freq_ready(gate: int) -> bool:
    """The ready gate: value 0 means the count is ready."""
    if isinstance(gate, bool) or not isinstance(gate, int) or not 0 <= gate <= 0xFF:
        raise ValueError("gate must be a byte")
    return gate == 0


def freq_round_robin(channels: int = FREQ_CHANNELS) -> list[tuple[int, int]]:
    """The one-iteration-lag cycle: select N+1, then read N, per channel."""
    if channels != FREQ_CHANNELS:
        raise ValueError("the counter multiplexes exactly four channels")
    return [((n + 1) % channels, n) for n in range(channels)]


def period_display(hz: int) -> str:
    """Period for a frequency readout, the device's own convention.

    Zero frequency reads ``>100ms`` (the display's floor marker); other
    values render in ns/us/ms/s with trimmed precision.
    """
    if not isinstance(hz, int) or isinstance(hz, bool) or hz < 0:
        raise ValueError("hz must be a nonnegative int")
    if hz == 0:
        return ">100ms"
    period_s = 1.0 / hz
    if period_s >= 1.0:
        return f"{period_s:g}s"
    if period_s >= 1e-3:
        return f"{period_s * 1e3:g}ms"
    if period_s >= 1e-6:
        return f"{period_s * 1e6:g}us"
    return f"{period_s * 1e9:g}ns"


# --- Reader column bounds (protocol-spec.md, settled) ----------------------

#: GetSamplesSDR buffers 4096 columns (0xFFF bounds).
SDR_COLUMN_COUNT_MAX = 0xFFF
#: GetSamplesDDR buffers 2048 columns (0x7FF bounds) — the 34 x 2048
#: specification buffer. The adapter's DDR-style reader uses this bound.
DDR_COLUMN_COUNT_MAX = 0x7FF


def columns_from_trigger_position(trigger_position: int) -> int:
    """N = 0x7FF minus the trigger position (both readers)."""
    if (
        isinstance(trigger_position, bool)
        or not isinstance(trigger_position, int)
        or not 0 <= trigger_position <= 0x7FF
    ):
        raise ValueError("trigger_position must fit 11 bits")
    return DDR_COLUMN_COUNT_MAX - trigger_position


# --- Trigger position writes (protocol-spec.md, confirmed) -----------------

REG_TRIGGER_POSITION_WRITE_A = 0x100004  # LE16
REG_TRIGGER_POSITION_WRITE_B = 0x100006  # LE16


def trigger_position_words(mode_flag: int, clock_type: int, position: int) -> tuple[int, int]:
    """The two trigger-position words with the pipeline offset branches.

    Mode flag 1 with clock type 2: +35 / -30. Mode flag 1 with other
    implemented clock types (1, 3): +7 / -7, then both divided by the
    clock type. Clock type 4 is not implemented in the source. Mode flag
    not 1: +7 / -7, no division.
    """
    for name, value in (("mode_flag", mode_flag), ("clock_type", clock_type), ("position", position)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be an int")
    if not 0 <= position <= 0xFFFF:
        raise ValueError("position must fit 16 bits")
    if mode_flag == 1:
        if clock_type == 2:
            return position + 35, position - 30
        if clock_type in (1, 3):
            return (position + 7) // clock_type, (position - 7) // clock_type
        raise ValueError(f"clock type {clock_type} is not implemented")
    return position + 7, position - 7


def trigger_position_frames(mode_flag: int, clock_type: int, position: int) -> list[bytes]:
    """The two C1 LE16 writes: registers 0x100004 then 0x100006."""
    word_a, word_b = trigger_position_words(mode_flag, clock_type, position)
    return [
        frame_c1(wire_address(REG_TRIGGER_POSITION_WRITE_A), payload_word(word_a)),
        frame_c1(wire_address(REG_TRIGGER_POSITION_WRITE_B), payload_word(word_b)),
    ]


# --- Trigger sequencer (protocol-spec.md, confirmed) -----------------------

#: Per-segment register bases: segment 0 at 0x200000, segment 1 at 0x600000.
SEQUENCER_SEGMENT_BASES = (0x200000, 0x600000)

#: Two-bit per-channel term states, packed most-significant-first over the
#: 34 channels: 0 = do not care, 1 = accumulate A, 2 = accumulate B,
#: 3 = accumulate both.
TERM_DONT_CARE = 0
TERM_A = 1
TERM_B = 2
TERM_BOTH = 3


def pack_term_states(terms: list[int]) -> tuple[int, int]:
    """Pack 34 per-channel term states into the (mask A, mask B) pair.

    Channel i occupies bit (33 - i) of each 34-bit mask: mask A collects
    states 1 and 3, mask B collects 2 and 3.
    """
    if not isinstance(terms, list) or len(terms) != CHANNEL_COUNT:
        raise ValueError(f"terms must list {CHANNEL_COUNT} channel states")
    mask_a = 0
    mask_b = 0
    for channel, state in enumerate(terms):
        if isinstance(state, bool) or not isinstance(state, int) or not 0 <= state <= 3:
            raise ValueError("term states must be 0..3")
        bit = 1 << (CHANNEL_COUNT - 1 - channel)
        if state in (TERM_A, TERM_BOTH):
            mask_a |= bit
        if state in (TERM_B, TERM_BOTH):
            mask_b |= bit
    return mask_a, mask_b


def sequencer_write_plan(
    segment: int,
    terms: list[int],
    state_terms_active: bool,
    term_type: int,
    dwell1: int,
    condition_mask_a: int,
    condition_mask_b: int,
    state_mask_a: int,
    state_mask_b: int,
    control: int,
    match_mask: int,
    param1: int,
    param1_dword: bool,
    param2: int,
    param2_dword: bool,
    flag: int,
    match_and: int,
    control2: int,
) -> list[tuple[int, bytes, str]]:
    """The confirmed 15-row sequencer write plan for one segment.

    Returns ``(register, payload, label)`` tuples in write order, registers
    at the segment base plus the documented offsets. The condition-mask
    rows carry caller-supplied masks only when terms are active; the
    state-mask rows only when state terms are active (13 rows then). The
    accumulator masks derive from ``terms``; the rest are the segment's
    own field values (the byte-level semantics are the record's labels).
    """
    if not isinstance(segment, int) or isinstance(segment, bool) or not 0 <= segment < len(SEQUENCER_SEGMENT_BASES):
        raise ValueError("segment must be 0 or 1")
    mask_a, mask_b = pack_term_states(terms)
    terms_active = any(state != TERM_DONT_CARE for state in terms)
    base = SEQUENCER_SEGMENT_BASES[segment]
    plan: list[tuple[int, bytes, str]] = [
        (base + 0x12, payload_byte(term_type), "term type"),
        (base + 0x1A, payload_40(mask_a), "mask A accumulator"),
        (base + 0x1F, payload_40(mask_b), "mask B accumulator"),
        (base + 0x24, payload_40(dwell1), "dwell value 1"),
    ]
    if terms_active:
        plan += [
            (base + 0x00, payload_40(condition_mask_a), "condition mask A"),
            (base + 0x05, payload_40(condition_mask_b), "condition mask B"),
        ]
    if state_terms_active:
        plan += [
            (base + 0x29, payload_40(state_mask_a), "state mask A"),
            (base + 0x2E, payload_40(state_mask_b), "state mask B"),
        ]
    plan += [
        (base + 0x18, payload_byte(control), "control byte"),
        (base + 0x19, payload_byte(match_mask), "match mask"),
        (
            base + 0x0E,
            payload_dword(param1) if param1_dword else payload_word(param1),
            "segment parameter 1",
        ),
        (
            base + 0x0A,
            payload_dword(param2) if param2_dword else payload_word(param2),
            "segment parameter 2",
        ),
        (base + 0x14, payload_byte(flag), "flag byte"),
        (base + 0x16, payload_byte(match_and), "match-and flag"),
        (base + 0x17, payload_byte(control2), "control byte 2"),
    ]
    return plan

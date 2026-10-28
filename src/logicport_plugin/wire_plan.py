"""The decoded wire model as data (docs addenda 5 and 6, 2026-10-27/28).

The reverse-engineering decode settled what the C1/C2 frames carry: the
16-bit field is the SAMPLE-RATE word (low byte first), not an address.
There is no register, page or per-cell address anywhere on the wire;
command identity is sequence position plus payload size only. This
module expresses that model as pure functions — frame builders reusing
``protocol``'s, the vendor's dispatcher send order, the one-byte status
decode, the two sample-read plans with explicit counts, and the column
decoders for both read modes.

No device I/O. Timeouts and drain sizes are constants a caller hands to
its transport; nothing here performs or schedules a transfer.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import NamedTuple

from logicport_plugin import protocol as proto

# --- The decoded frame grammar (addendum 5) ---------------------------------

#: Every C1 exchange drains exactly this many response bytes. The drain
#: size is a constant written once at device open (addendum 5: all
#: seventeen references to the vendor's slot are reads).
C1_DRAIN_BYTES = 3

#: C1 response timeout (addendum 5: 1000 ms against C2's 2000).
C1_TIMEOUT_MS = 1000

#: C2 response timeout (addendum 5: ``FUN_004c6aa3``; addendum 6 table).
C2_TIMEOUT_MS = 2000

#: The status poll is a ONE-byte C2 (addendum 6: N = 1).
STATUS_POLL_BYTES = 1


def rate_write_frame(rate: int, payload: bytes) -> bytes:
    """A C1 write frame carrying the sample-rate word (addendum 5).

    Wire order: ``C1 rate_lo rate_hi len_lo len_hi payload...`` where
    ``len = len(payload) - 1``. The first wrapper argument is the rate
    variable, not a register cell — there is no address field.
    """
    return proto.frame_c1(rate, payload)


def read_request_frame(rate: int, n: int) -> bytes:
    """A C2 read-request frame (addendum 5: ``FUN_004c6aa3``).

    Wire order: ``C2 rate_lo rate_hi n_lo n_hi``. ``n`` is the requested
    byte count; it goes on the wire AND is the read count
    (``bytes_read >= n`` enforced).
    """
    return proto.frame_c2(rate, n)


def status_poll_frame(rate: int) -> bytes:
    """The one-byte status poll: a C2 with N = 1 (addendum 6, section 1)."""
    return read_request_frame(rate, STATUS_POLL_BYTES)


# --- The dispatcher send order (addendum 5, "The dispatcher send order") ----

#: Payload size marker for a step whose width the decode did not pin (the
#: sequencer segments send eight to ten frames each of mixed 1/4/5-byte
#: payloads; identity is position, so the width set stays open).
VARIABLE_PAYLOAD = None


class CommandStep(NamedTuple):
    """One ordered send of the dispatcher (addendum 5).

    ``payload_size`` is the C1 payload width in bytes, or
    :data:`VARIABLE_PAYLOAD` (``None``) when the decode did not pin the
    widths. ``purpose`` names the step; conditional sends carry their
    condition in the text.
    """

    payload_size: int | None
    purpose: str


def command_plan() -> tuple[CommandStep, ...]:
    """The vendor's ordered dispatcher sends (addendum 5, six blocks).

    Command identity is sequence position plus payload size only. The
    three dirty-flag blocks appear in the vendor's evaluation order;
    inside block 2 the two 40-bit tallies are sent only when the vendor's
    ``0x5963f4`` flag is nonzero, and block 1 only when the mode is 1.
    The two sequencer segments are the variable-width steps.
    """
    return (
        CommandStep(1, "profile byte, first send (block 1, mode 1 only)"),
        CommandStep(1, "profile byte, second send (block 1, mode 1 only)"),
        CommandStep(1, "mode byte (block 2)"),
        CommandStep(1, "second byte (block 2)"),
        CommandStep(5, "channel-mask tally A, 40-bit (block 2, vendor flag nonzero only)"),
        CommandStep(5, "channel-mask tally B, 40-bit (block 2, vendor flag nonzero only)"),
        CommandStep(1, "third byte (block 2)"),
        CommandStep(1, "fourth byte (block 2)"),
        CommandStep(1, "byte before the segments (block 3)"),
        CommandStep(1, "second byte before the segments (block 3)"),
        CommandStep(VARIABLE_PAYLOAD, "sequencer segment 0 (block 3; 8-10 C1 frames)"),
        CommandStep(VARIABLE_PAYLOAD, "sequencer segment 1 (block 3; 8-10 C1 frames)"),
        CommandStep(1, "byte after the segments (block 3)"),
        CommandStep(2, "threshold DAC word, little-endian (block 4)"),
        CommandStep(2, "trigger-position word A, little-endian (block 5)"),
        CommandStep(2, "trigger-position word B, little-endian (block 5)"),
        CommandStep(1, "arm byte (block 6)"),
    )


# --- The capture status byte (addendum 6, section 1) ------------------------


class StatusPhase(Enum):
    """The decode of one status-poll reply byte (addendum 6, section 1)."""

    PREFILL = auto()  # bit0 clear, 0x40 clear — phase 5
    WAITING_TRIGGER = auto()  # 0x40 set, 0x20 clear — phase 6
    COLLECTING = auto()  # 0x60 set — phase 7, triggered and collecting
    COMPLETE = auto()  # bit0 set — go to fetch


STATUS_COMPLETE_BIT = 0x01
STATUS_WAIT_BIT = 0x40
STATUS_COLLECT_BITS = 0x60


def decode_status(status: int) -> StatusPhase:
    """Decode one status-poll byte (addendum 6's bit table).

    ``bit0`` set wins outright (complete); otherwise both ``0x40`` and
    ``0x20`` set mean triggered and collecting; ``0x40`` alone means
    waiting for the trigger; everything else (bit0 clear, ``0x40`` clear)
    is the pre-fill phase.
    """
    if isinstance(status, bool) or not isinstance(status, int) or not 0 <= status <= 0xFF:
        raise ValueError("status must be a byte")
    if status & STATUS_COMPLETE_BIT:
        return StatusPhase.COMPLETE
    if status & STATUS_COLLECT_BITS == STATUS_COLLECT_BITS:
        return StatusPhase.COLLECTING
    if status & STATUS_WAIT_BIT:
        return StatusPhase.WAITING_TRIGGER
    return StatusPhase.PREFILL


# --- The sample-read plans (addendum 6, section 2) --------------------------

#: Column arithmetic of both readers (addendum 6): cols = 0x7FF - offset.
COLUMN_BASE = 0x7FF

#: Plane slot names of the vendor's reader (addendum 6, section 2).
BYTE_MODE_EVEN_SLOTS = ("c08", "c10", "c18", "c20", "c48")
BYTE_MODE_ODD_SLOTS = ("c28", "c30", "c38", "c40", "c48")
RUN_LENGTH_MODE_SLOTS = ("c48", "c08", "c10", "c18", "c20")


class SampleRead(NamedTuple):
    """One C2 plane read of the sample fetch (addendum 6, section 2).

    ``slot`` names the vendor's source slot; ``byte_count`` is the C2 N.
    Every plane read is one C2 with the 2000 ms timeout.
    """

    slot: str
    byte_count: int


def byte_mode_cols(offset: int) -> int:
    """Byte-mode column count: ``0x7FF - offset``, floored at 1 (addendum 6)."""
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= COLUMN_BASE:
        raise ValueError("offset must fit 0..0x7FF")
    return max(1, COLUMN_BASE - offset)


def byte_mode_read_plan(offset: int) -> tuple[SampleRead, ...]:
    """The byte-mode fetch: ten plane reads of ``(cols + 1) / 2`` bytes.

    Three scalar reads come first in the vendor (two cursors and the
    offset); they are not counted here — this plan lists the ten plane
    reads. Five read the even-column set (``c08``/``c10``/``c18``/``c20``
    plus the ``c48`` flags) and five the odd-column set
    (``c28``/``c30``/``c38``/``c40`` plus ``c48`` again). Each plane read
    is one C2 of that N with the 2000 ms timeout.
    """
    count = (byte_mode_cols(offset) + 1) // 2
    return tuple(SampleRead(slot, count) for slot in BYTE_MODE_EVEN_SLOTS + BYTE_MODE_ODD_SLOTS)


def run_length_mode_read_plan(offset: int) -> tuple[SampleRead, ...]:
    """The word/run-length fetch: five plane reads of ``cols + 1`` bytes.

    The offset advances first — ``(offset + 1) & 0x7FF`` — then
    ``cols = 0x7FF - offset``. The ``c48`` flag plane at the trigger
    position is read first, then the four data planes. One column is
    trimmed at extraction time when a flag byte's bit 3 is set; the trim
    happens after the reads, so the plan's counts are unaffected.
    """
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= COLUMN_BASE:
        raise ValueError("offset must fit 0..0x7FF")
    advanced = (offset + 1) & COLUMN_BASE
    count = COLUMN_BASE - advanced + 1
    return tuple(SampleRead(slot, count) for slot in RUN_LENGTH_MODE_SLOTS)


# --- The column decoders (addendum 6, section 3) ----------------------------

#: The five plane bytes of one column, data planes first, flags last.
PlaneTuple = tuple[int, int, int, int, int]


def decode_byte_column(planes: PlaneTuple, *, odd_column: bool) -> int:
    """Decode one byte-mode column into its 34-bit channel state.

    Byte *k* of a plane carries columns ``2k`` and ``2k+1``; this decodes
    ONE column's five bytes (the even set ``c08``/``c10``/``c18``/``c20``
    plus ``c48``, or the odd set ``c28``/``c30``/``c38``/``c40`` plus
    ``c48``). Data planes map ``c08 -> ch0-7`` .. ``c20 -> ch24-31``; the
    flag byte carries CLK1/CLK2 in bits 1/2 on even columns and bits 5/6
    on odd columns. Every stored bit is the COMPLEMENT of the raw level —
    a low pin stores as 1.
    """
    return proto.decode_sample_state(bytes(planes), odd_column=odd_column)


def decode_run_length_column(planes: PlaneTuple) -> tuple[int, bool]:
    """Decode one word/run-length-mode column.

    Returns ``(value, is_run)``. Flag bit 3 clear: a digital column —
    bits 1/2 of the flag byte carry ch32/ch33 and the four data planes
    carry ch0-31, bit *n* of each byte to a channel, inverted — the value
    is the 34-bit state. Flag bit 3 set: a run-length column — the count
    is 35-bit big-endian, ``((flag & 7) << 32) | c20 | c18 | c10 | c08``.
    """
    return (
        proto.decode_rle_count(bytes(planes)) if planes[4] & proto.RLE_FLAG_BIT else proto.decode_sample_state(bytes(planes), odd_column=False),
        bool(planes[4] & proto.RLE_FLAG_BIT),
    )


def decode_run_length_columns(columns: list[PlaneTuple]) -> proto.DecodedColumns:
    """Run the full word/run-length decode loop (addendum 6, section 3).

    A run column holds and copies forward the previous digital state and
    accumulates its count; a digital column decodes fresh. There is no
    even/odd split in this mode — the single flag byte always carries
    CLK1/CLK2 in bits 1/2.
    """
    return proto.decode_columns([(bytes(planes), False) for planes in columns])


def decode_byte_mode(
    even_planes: tuple[bytes, bytes, bytes, bytes, bytes],
    odd_planes: tuple[bytes, bytes, bytes, bytes, bytes],
) -> proto.DecodedColumns:
    """The byte-mode decode loop (addendum 6, section 3).

    Byte *k* of each plane carries columns ``2k`` (even set) and ``2k+1``
    (odd set); the ten plane buffers interleave into one column stream
    and run through the same column loop.
    """
    return proto.decode_columns(proto.deinterleave_pages(list(even_planes), list(odd_planes)))

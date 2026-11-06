# LogicPort Trigger Programming Reference

> **Sources.** Three decode reports over `LogicPort.exe` v3.4.0.2371
> (SHA-256 `343745f3...`), read with REA/Ghidra and LLVM objdump. The
> reports are the sequencer-record lane, the range and duration lane, and
> the term-to-plane lane. Every register and frame below carries an
> address from one of them.
>
> **Register.** ASD-STE100 Simplified Technical English. Technical names
> stay as they are.
>
> **Status.** This document is complete for the frame shapes, the plane
> encoding, the sequencer block and the immediate trigger. It is not
> complete for two trigger families, and it is not complete for the
> six-way combine logic. Section 8 lists every gap as a question with a
> test. Read section 8 before you write a driver.

---

## 0. Why two mask writes never fired a trigger

The client writes two 40-bit values at `0x1000003` and `0x1000008` and
expects an edge trigger. That cannot work.

Those two registers are **channel-membership masks**. They select which
channels the engine looks at. They do not encode a level, an edge or a
condition. An edge trigger needs a separate term record in the **trigger
term bank** at `0x200000` and `0x400000` (report 3, `FUN_004f2179` masks
at `0x4f2499` and `0x4f255a`; report 1, term sender `FUN_004f2dd4`).

The vendor never writes a level into `0x1000003`. The whole 0/1/2 grid
encoding collapses to a single bit per channel before it reaches that
register (report 3, `FUN_004fe1eb` at `0x4fe374`).

---

## 1. Terms, levels and the two segments

The device carries **two trigger levels**, A and B. The vendor application
calls them level A and level B. The wire calls them **segment 0** and
**segment 1**.

| Level | Segment | Term-bank base | Page select |
| --- | --- | --- | --- |
| A | 0 | `0x200000` | `0x20` |
| B | 1 | `0x400000` | `0x40` |

**Correction.** Earlier notes and `protocol.py` state segment 1 base
`0x600000`. That is wrong. `FUN_00471af1` builds segment 1 as `2^22`
(`0x400000`) at `0x472259`. The sender indexes the two register tables as
`0x596c50 + segment * 0x80`, which puts the second table at `0x596cd0`
(report 1, `0x4f2ecf`; `freq-decode-lane.md` section 9). The value
`0x600000` appears in no decode.

### 1.1 The record

Each segment has one record. The record is **112 bytes** (`0x70`), not
128. `FUN_004f2dd4` copies `0x70` bytes at `0x4f3d16`. The last field is
the word at `+0x6E`, so the layout closes at `+0x70` (report 1).

The `0x80` stride that earlier notes call the record stride is the
**register-address table** stride. A table row is 16 doubles, which is
`0x80` bytes. The sender computes `EDI = 0x596c50 + segment * 0x80`
(`SHL EDI,0x7` at `0x4f2ecf`). Report 2 read the same instruction as a
record stride. The instruction belongs to the ladder.

**Gap.** Report 2 gives `+0x58` and `+0x60` as **doubles** and report 1
gives them as **BSTR pointers**. Both fit a 112-byte VB6 UDT. See
section 8, question 6.

### 1.2 The record field map

Offsets come from report 1. The meaning column merges all three reports.
A value marked CONFLICT has two readings in the reports.

| Offset | Type | Meaning | Wire destination | Reading |
| --- | --- | --- | --- | --- |
| `+0x00` | word | gate: pattern plane family is active | `0x200000`, `0x200005` (40-bit) | agreed |
| `+0x04` | double | mask accumulator, written back by the sender | none | agreed |
| `+0x0C` | word | gate: edge plane family is active | `0x200029`, `0x20002E` (40-bit) | agreed |
| `+0x10` | double | mask accumulator, written back | none | agreed |
| `+0x18` | double | mask accumulator, written back | none | agreed |
| `+0x20` | long | occurrence count, or DurationRight | `0x20000A` (4 bytes) | agreed destination |
| `+0x24` | word | a Boolean copy of a checkbox | none | report 2 only |
| `+0x38` | long | DurationLeft | `0x20000E` (4 bytes) | agreed destination |
| `+0x3C` | ushort | byte ANDed with `+0x46`, or the raw low byte | `0x200019` (1 byte) | agreed |
| `+0x40` | long | copy of `group+0x20` | none | report 2 only |
| `+0x44` | word | copy of `group+0x24` | none | report 2 only |
| `+0x46` | ushort | **term-enable word** | gates the four value frames; AND term for `0x200019` | agreed |
| `+0x48` | double | value accumulator | `0x20001A` (40-bit) | agreed |
| `+0x50` | byte | digit mode, or a group Boolean | `0x200012` (1 byte) | **CONFLICT** |
| `+0x54` | long | group index, or the combine-mode selector | none decoded | **CONFLICT** |
| `+0x58` | double or BSTR | RangeLeft, or a digit string | none decoded | **CONFLICT** |
| `+0x60` | double or BSTR | RangeRight, or a second digit string | none decoded | **CONFLICT** |
| `+0x68` | ushort | flag word, low byte sent | `0x200014` (1 byte) | agreed |
| `+0x69` | byte | term class: `3` for Edge, `1` or `2` turns on the AND gate | `0x200016` (1 byte) | agreed destination |
| `+0x6A` | byte | **term type**: `1` for Edge and Pattern, `3` for Duration | `0x200017` (1 byte) | agreed destination, value gap for Range |
| `+0x6C` | byte | control byte | `0x200018` (1 byte) | agreed |
| `+0x6E` | ushort | `0xFFFF` when the mask accumulator is above zero | none | agreed |

**Note on `+0x16`.** Report 2 states that `0x200016` is sourced from
`record+0x00`. Reports 1 and `freq-decode-lane.md` section 9 state that
`0x200016` is sourced from `+0x69`. The instruction stream in report 2
shows `movb (%record)` at `0x4f3c87`. Section 8, question 5.

---

## 2. The term model: four families

The vendor application presents four term families per level, plus one
combine selector. The `.LPF` project file stores them at the keys below
(`LogicPort-LPF-Format.md` section 2).

| Family | `.LPF` keys | Record fields | Wire registers | Reaches the wire |
| --- | --- | --- | --- | --- |
| Immediate (no term) | none | all gates zero | 7 ungated frames per segment | yes |
| Edge | `EdgeTerm`, `EdgeCount`, `EdgeCountMode`, `Edge[]` | `+0x46`, `+0x0C`, `+0x20`, `+0x69`, `+0x6A`, `+0x6C`, `+0x3C`, `+0x68` | `0x200029`, `0x20002E`, `0x20000A`, `0x200016`, `0x200017`, `0x200018`, `0x200019`, `0x200014` | yes |
| Pattern | `PatternTerm`, `PatternMode`, `PatternCount`, `PatternCountMode`, `Pattern[]` | `+0x46`, `+0x00`, `+0x20`, `+0x69`, `+0x6A` | `0x200000`, `0x200005`, `0x20000A`, `0x200016`, `0x200017` | yes |
| Duration | `DurationTerm`, `DurationMode`, `DurationLeft`, `DurationRight`, `DurationUnits` | `+0x46`, `+0x38`, `+0x20`, `+0x6A` | `0x20000E`, `0x20000A`, `0x200017` | yes |
| Value (Range) | `RangeTerm`, `RangeGroup`, `RangeMode`, `RangeLeft`, `RangeRight` | `+0x54`, `+0x58`, `+0x60`, `+0x40`, `+0x44`, `+0x50` | none decoded | **no** |
| Combine | `CombineMode` | `+0x54`? | none decoded | **no** |

### 2.1 What each family writes

**Edge.** Term type `+0x6A = 1`. Term class `+0x69 = 3`. It writes the
per-channel states into the **edge column**, and the count into `+0x20`.
The Edge validator is `FUN_004d918c`. Its stores are `0x4d9980` (`+0x69 =
3`), `0x4d9995` (`+0x6A = 1`) and `0x4d9963` (`+0x6C`).

**Pattern.** Term type `+0x6A = 1` as well. The term class `+0x69` comes
from `0x4d89f8`. The per-channel states go into the **pattern column**.
The Pattern validator is `FUN_004d8696`.

Edge and Pattern therefore share one term-type value. The plane family
gate (`+0x0C` against `+0x00`) and the `+0x69` class byte separate them.
Do not rely on `+0x6A` alone.

**Duration.** Term type `+0x6A = 3`. Two dwords: `+0x38` to `0x20000E`
and `+0x20` to `0x20000A`. The validator truncates a double to an integer
with `__vbaFpI4` before the store (`0x4dab75`, `0x4daffa`, `0x4db238`).
The wire therefore carries **sample counts**, not seconds. The units
choice (`DurationUnits`) is host-side.

**Value (Range).** No register receives `+0x58`, `+0x60`, `+0x40`, `+0x44`
or `+0x50`. Report 2 gives two readings and cannot separate them:

1. The comparison runs on the host over the captured buffer.
2. The two bounds ride the same two 20-bit parameter registers that
   Duration uses, `0x20000A` and `0x20000E`.

Reading 2 is structurally more plausible. The CHM says the per-level
20-bit counter serves edge count, pattern count and duration, and a value
term needs two bounds. Section 8, question 3.

**Combine.** `CombineMode` is the six-way selector: Immediately, A, B,
A or B, A and B in any order, A then B. No decode found a register that
carries it. `LogicPort-LPF-Format.md` section 6.3 reports no read site for
the key anywhere in the binary. Section 8, question 4.

### 2.2 Counters and limits

| Item | Limit | Evidence |
| --- | --- | --- |
| Occurrence count | 1 to 1 048 575 (`0xFFFFF`) | `0x4da7cd`, `0x4da89e` and six more; CHM Specifications |
| Duration | 1 to 1 048 575 samples | same constants |
| Edge or pattern count | one 20-bit counter per level | CHM `Trigger Setup.htm` |
| Channels per plane | 34, index 0 to 33 | report 3 |
| Pre-fill to trigger | the trigger logic arms only after Pre-Fill completes | CHM, record section G |

The Duration validator emits the message `"Trigger Level " + " Duration
value(s) constrained"` on a violation. The Range validator emits
`"Trigger Level " + " Range value(s) constrained"`.

---

## 3. The plane encoding

### 3.1 The two mode-bank masks

| Register | Source array | Role |
| --- | --- | --- |
| `0x1000003` | `[0x5962a4]` | trigger scope: the channels that carry a term |
| `0x1000008` | `[0x5962a8]` | sample set: the channels the engine monitors |

**Gate.** The word at `0x5963f4` controls mask A. When it is zero, the
application **skips** mask A, forces a scratch array to all `0xFFFF`, and
**still sends mask B** (`0x4f2429` to `0x4f2525`, write at `0x4f255a`).

A capture with mask B equal to zero monitors no channel. The engine sees
no transition, collects nothing, and the buffer keeps its power-on
pattern. This is the root cause recorded in
`freq-decode-lane.md` section 15.

**Therefore: always write mask B, and make it non-zero.**

### 3.2 Bit order

```
acc = 0
for i = 33 down to 0:
    acc = acc + acc
    if array[i] != 0:
        acc = acc + 1
write_40(register, acc)
```

The loop processes channel 33 first, so channel 33 lands in bit 33 and
channel 0 lands in bit 0.

| Channel | Wire bit | Value |
| --- | --- | --- |
| D0 (index 0) | 0 | `0x000000001` |
| D31 (index 31) | 31 | `0x080000000` |
| CLK1 (index 32) | 32 | `0x100000000` |
| CLK2 (index 33) | 33 | `0x200000000` |
| all 34 | 0 to 33 | `0x3FFFFFFFF` |

**Correction.** `freq-decode-lane.md` section 15 states that channel *i*
occupies bit `33 - i`, and that D0 alone is `0x200000000`. That is wrong.
Reports 1 and 3 agree on identity order. D0 alone is `0x1`.

Payload: five bytes, little-endian. Byte 0 holds bits 0 to 7. Bits 34 to
39 are zero. `FUN_004cad4a` splits the value with `& 0xFF` and `/ 256`
(`0x4f3637`, `0x4f364d`).

### 3.3 The per-channel states collapse

`FUN_004fe1eb` walks the trigger-term collection `[0x596f1c]`. For every
item with kind field `+0x04 == 1`, it sets `element[channel] = 0xFFFF` on
the channel held at `+0x14`. It **never reads the 0/1/2 grid value**.

| Grid value | Bit in `0x1000003` / `0x1000008` |
| --- | --- |
| 0 (no entry) | clear |
| 1 | set |
| 2 | set |
| 3 | set |

So the two mode-bank masks hold booleans only. The level lives in the term
record, not in these planes.

### 3.4 The term-bank planes and their vocabularies

The term bank has **two plane pairs**, and each pair has its own state
vocabulary. This is the part that surprises a new driver.

| Plane pair | Registers | Source array | Column |
| --- | --- | --- | --- |
| Condition | `0x200000`, `0x200005` | `DAT_005962e0` | Pattern |
| State | `0x200029`, `0x20002E` | `DAT_005962d4` | Edge |

The column assignment is INFERRED. `FUN_004e7843` reads `DAT_005962e0` as
grid column 1 and `DAT_005962d4` as grid column 2 at `0x4e8319` and
`0x4e8479`. The `.LPF` order is `Pattern` then `Edge`.

**Pattern vocabulary** (`DAT_005962e0`, loop at `0x4f34b5` to `0x4f3624`):

| Value | Plane A bit | Plane B bit |
| --- | --- | --- |
| 0 | 0 | 0 |
| 1 | 1 | 0 |
| 2 | 1 | 1 |
| 3 | 0 | 0 |

Branch addresses: `0x4f3569` (zero skips), `0x4f3595` (one sets A),
`0x4f35d5` (two sets A and B). Value 3 has no branch.

**Edge vocabulary** (`DAT_005962d4`, loop at `0x4f377a` to `0x4f382e`):

| Value | Plane A bit | Plane B bit |
| --- | --- | --- |
| 0 | 0 | 0 |
| 1 | 1 | 0 |
| 2 | 0 | 1 |
| 3 | 1 | 1 |

Branch addresses: `0x4f377a` (zero), `0x4f37a6` (one, plane A),
`0x4f37e6` (two, plane B), `0x4f380e` (three, plane A and plane B).

**The two vocabularies are not interchangeable.** A value of 2 means
"both planes" in the Pattern column and "plane B only" in the Edge
column. Do not copy one column into the other.

### 3.5 What the four states mean

| State | Edge column | Pattern column |
| --- | --- | --- |
| 0 | no term on this channel | no term on this channel |
| 1 | one edge direction, plane A | plane A only |
| 2 | the other edge direction, plane B | both planes |
| 3 | both planes | no term (no branch) |

**Named gaps.**

1. Which plane is the rising direction is UNVERIFIED. The `.LPF` stores
   only 1 and 2, so the working assumption is that the two values are the
   two edge directions. Use state 1 first. If the capture triggers on the
   wrong edge, use state 2.
2. The meaning of state 3 is UNVERIFIED. The common hardware reading is
   "either edge". The `.LPF` never produces it.
3. The meaning of Pattern state 2 ("both planes") is UNVERIFIED. A
   HYPOTHESIS that fits the hardware: plane A marks "this channel is part
   of the pattern", plane B marks "the needed level is 1". Pattern state
   1 would then mean level 0 and state 2 level 1. Test in section 8,
   question 2.

### 3.6 The extra AND with the channel-use array

Report 3 shows the term-bank loop reads `DAT_005962a4[ch]` first and skips
the channel when that element is zero (`0x4f34e7`, `0x4f3521`). Report 1
does not show this test. The likely reading is that a channel contributes
only when it is selected in the waveform table **and** the column value
matches. Confirm this on the bench before you rely on it.

---

## 4. The sequencer block

### 4.1 What the block is

`FUN_004f2dd4(segment)` sends one segment. `FUN_004f2179` calls it twice,
once per segment, behind one flag.

| Item | Value | Evidence |
| --- | --- | --- |
| Dirty flag | `DAT_005963cc = 0xFFFF` | set at `0x4f81f0`, line `0x107` |
| Consumer | `CMP word ptr [0x5963cc],BX` / `JZ 0x4f2768` | `0x4f269c` |
| Clear | after both segments | `0x4f2761` |
| Caller | `FUN_004f1453`, the pump tick | `0x4f1453` |

**The block is unconditional.** No code path sends only segment 0. It is
not gated on "sequencer in use" and not gated on the segment count. Every
apply pass in which the term table is dirty ships both segments.

**Untested.** Nothing in the binary proves the FPGA rejects a capture that
omits the block. The vendor always sends it. Treat "send it" as needed
and "the FPGA rejects a short block" as untested.

### 4.2 The register ladder

The sender indexes a table of 16 doubles at `0x596c50 + segment * 0x80`.
Each entry is `base + offset`, where base is `0x200000` or `0x400000`.

| Entry | Offset | Register (segment 0) |
| --- | --- | --- |
| 0 | 0 | `0x200000` |
| 1 | 0 | `0x200000` |
| 2 | 5 | `0x200005` |
| 3 | `0x0A` | `0x20000A` |
| 4 | `0x0E` | `0x20000E` |
| 5 | `0x14` | `0x200014` |
| 6 | `0x16` | `0x200016` |
| 7 | `0x17` | `0x200017` |
| 8 | `0x18` | `0x200018` |
| 9 | `0x19` | `0x200019` |
| 10 | `0x12` | `0x200012` |
| 11 | `0x1A` | `0x20001A` |
| 12 | `0x1F` | `0x20001F` |
| 13 | `0x24` | `0x200024` |
| 14 | `0x29` | `0x200029` |
| 15 | `0x2E` | `0x20002E` |

Entries 0 and 1 hold the same address. That is intended. Entry 0 carries a
one-byte payload and entry 1 carries a five-byte payload (`freq-decode-lane.md`
section 9, ladder built at `0x4720b5` to `0x472253`).

### 4.3 Emission order and gates

The order is fixed by address. A gate that is zero removes its frames
from the sequence. Later frames do not move.

| Step | Frames | Gate |
| --- | --- | --- |
| 1 | `0x12`, `0x1A`, `0x1F`, `0x24` | `+0x46 != 0` |
| 2 | `0x00`, `0x05` | `+0x00 != 0` |
| 3 | `0x29`, `0x2E` | `+0x0C != 0` |
| 4 | `0x18`, `0x19`, `0x0E`, `0x0A`, `0x14`, `0x16`, `0x17` | always |

Payload sizes in step 4 follow the ladder, not the record type:
`0x18` and `0x19` are 1 byte, `0x0E` and `0x0A` are 4 bytes, `0x14`,
`0x16` and `0x17` are 1 byte.

### 4.4 The four value frames

Step 1 fires whenever the term-enable word is non-zero. It sends four
40-bit values:

| Frame | Source | Reading |
| --- | --- | --- |
| `0x12` | `+0x50` | digit mode, or a group Boolean |
| `0x1A` | accumulator from `+0x48` | value low |
| `0x1F` | local built from `+0x58` and `+0x60` | value middle |
| `0x24` | local built from `+0x60` | value high |

Report 1 reads `+0x58` and `+0x60` as BSTR digit strings and builds the
two locals by summing the positions of the character `"1"`
(`DAT_00423694`). Report 2 reads the same offsets as doubles.

Both readings agree on one thing: **for Edge, Pattern and Duration terms
the correct payload is zero.** Send zeros. Section 8, question 6.

### 4.5 The frame list for a plain capture

A plain capture sets every gate to zero. The block is then 17 writes:
two lead frames, seven frames per segment, one tail.

| # | Frame (page 0x0010 or 0x0020 or 0x0040) | Payload |
| --- | --- | --- |
| 1 | `C1 03 00 00 00 00` | `0x100003 <- 0` |
| 2 | `C1 02 00 00 00 01` | `0x100002 <- 1` |
| 3 | `C1 18 00 00 00 00` | `0x200018` |
| 4 | `C1 19 00 00 00 00` | `0x200019` |
| 5 | `C1 0E 00 03 00 00 00 00 00` | `0x20000E` |
| 6 | `C1 0A 00 03 00 00 00 00 00` | `0x20000A` |
| 7 | `C1 14 00 00 00 00` | `0x200014` |
| 8 | `C1 16 00 00 00 00` | `0x200016` |
| 9 | `C1 17 00 00 00 00` | `0x200017` |
| 10 to 16 | the same seven on page `0x40` | `0x400018` to `0x400017` |
| 17 | `C1 0D 00 00 00 00` | `0x10000D <- 0` |

`0x10000D` reads `DAT_005963ec`, a global with no writer, so it is `0x00`
(`0x4f2737`, `0x4f274f`).

A page select goes before each group when the page changes. See
section 5.2.

---

## 5. Frame grammar

### 5.1 Frame shapes

```
C1 <off_lo> <off_hi> <len_lo> <len_hi> <payload...>
C2 <off_lo> <off_hi> <len_lo> <len_hi>
C3 <sel_lo> <sel_hi> 00 00
```

`len = payload_len - 1` for `C1`. The select field of `C1` and `C2` is the
low 16 bits of the register address. The select field of `C3` is
`address >> 16`.

| Payload | `C1` length field | Total frame bytes |
| --- | --- | --- |
| 1 byte | `00 00` | 6 |
| 2 bytes | `01 00` | 7 |
| 4 bytes | `03 00` | 9 |
| 5 bytes | `04 00` | 10 |

Payload bytes for multi-byte values are **little-endian**.

The device replies to `C1` and `C3` with three bytes: `<opcode> <ctr_hi>
<ctr_lo>`. It replies to `C2` with `N + 3` bytes. The host counter starts
at zero after the resync flush and increments after every success.

**Correction.** `freq-decode-lane.md` section 9 prints a dword frame as
`C1 0E 00 00 03 00 00 00 00 00`. That line has one byte too many. The
length field is `03 00`, so the frame is `C1 0E 00 03 00 <4 bytes>`.

### 5.2 Page selects

| Address range | Page | `C3` bytes |
| --- | --- | --- |
| `0x100000` | `0x0010` | `C3 10 00 00 00` |
| `0x1000000` | `0x0100` | `C3 00 01 00 00` |
| `0x200000` | `0x0020` | `C3 20 00 00 00` |
| `0x400000` | `0x0040` | `C3 40 00 00 00` |
| `0x2000000` | `0x0200` | `C3 00 02 00 00` |
| `0x20000000` | `0x2000` | `C3 00 20 00 00` |

Send `C3` only when the page changes. The host caches the page at
`0x5977e8`. The cache starts at `-1`, so the first command after a flush
always starts with a `C3`.

### 5.3 The full apply block, in vendor order

The vendor order comes from the write ledger (`freq-decode-lane.md`
section 14, `0x4f225a` to `0x4f2c39`).

| Block | Page | Frames |
| --- | --- | --- |
| A clock | `0x2000` | `byteA` to `0x20000000`, `byteB` to `0x20000001` |
| B mode | `0x0100` | mode to `0x1000000`, `0x1000002`, mask A `0x1000003` (gated), mask B `0x1000008`, aux to `0x1000001` |
| C pre-arm | `0x0010` | `0x100003 <- 0`, `0x100002 <- 1` |
| D terms | `0x0020`, `0x0040` | the two segment blocks, section 6 |
| E tail | `0x0010` | `0x10000D <- 0`, threshold `0x100008`, positions `0x100004` and `0x100006`, arm `0x100003` |

Frame order is not proven strict. The hardware-proven capture in
`LogicPort-Protocol.md` section G2 uses a different order and works. Use
the vendor order for a trigger, because it is the only order with
evidence for a term block.

The mode byte, the threshold word and the position words are decoded in
`LogicPort-Protocol.md` section D. This document does not re-derive them.

---

## 6. Ordered frame lists for the useful triggers

Every list below starts at a known page state and assumes the device is
open, flushed and configured. The lists show the **trigger core**. Add the
clock, threshold, position and arm frames from section 5.3.

### 6.1 Common preamble

```
C3 00 01 00 00                 # page 0x0100
C1 00 00 00 00 <mode>          # 0x1000000
C1 02 00 00 00 01              # 0x1000002 <- 1, trigger enabled
C1 03 00 04 00 <A:5B>          # 0x1000003, trigger scope
C1 08 00 04 00 FF FF FF FF 3F  # 0x1000008, all 34 channels
C1 01 00 00 00 <aux>           # 0x1000001
C3 10 00 00 00                 # page 0x0010
C1 03 00 00 00 00              # 0x100003 <- 0
C1 02 00 00 00 01              # 0x100002 <- 1
```

Set mask B to `FF FF FF FF 3F` in every trigger. A zero mask B monitors
no channel and the capture never completes.

Set mask A to the channels that carry a term. When in doubt, use all 34.

### 6.2 Trigger A: immediate, no terms

This is the vendor's "no terms selected" case. The CHM states that a
trigger level with no terms is unconditionally satisfied.

| Page | Segment | Frames |
| --- | --- | --- |
| `0x0020` | 0 | the seven ungated frames, all zero (section 4.5 rows 3 to 9) |
| `0x0040` | 1 | the same seven, all zero |

Total 14 term frames, plus the preamble and the tail.

This shape matches the hardware-proven capture in
`LogicPort-Protocol.md` section G2, which returns status `0x42` and real
sample data. The bench has **not** yet shown the trigger event itself. The
capture completes. That is the strongest evidence in this document.

### 6.3 Trigger B: rising edge on one channel

Example: a rising edge on D0, level A only.

**Preamble.** mask A = D0 (`01 00 00 00 00`), mask B = all.

**Segment 0 block, 13 frames:**

```
C3 20 00 00 00                 # page 0x0020
C1 12 00 00 00 00              # 0x200012, value/term mode
C1 1A 00 04 00 00 00 00 00 00  # 0x20001A, value low   = 0
C1 1F 00 04 00 00 00 00 00 00  # 0x20001F, value mid   = 0
C1 24 00 04 00 00 00 00 00 00  # 0x200024, value high  = 0
C1 29 00 04 00 01 00 00 00 00  # 0x200029 edge plane A = D0
C1 2E 00 04 00 00 00 00 00 00  # 0x20002E edge plane B = none
C1 18 00 00 00 00              # 0x200018, control
C1 19 00 00 00 00              # 0x200019, match byte
C1 0E 00 03 00 00 00 00 00     # 0x20000E, second parameter
C1 0A 00 03 00 01 00 00 00     # 0x20000A, occurrence count = 1
C1 14 00 00 00 00              # 0x200014, flag
C1 16 00 00 00 03              # 0x200016 <- +0x69 = 3, Edge class
C1 17 00 00 00 01              # 0x200017 <- +0x6A = 1, term type
```

**Segment 1 block, 7 frames.** The same seven ungated frames on page
`0x40`, all zero. Level B has no term.

**If the capture triggers on the falling edge,** move the set bit from
`0x200029` to `0x20002E`. That is Edge state 2.

The four value frames carry zero. `+0x46` is non-zero, so the sender emits
them. They are not optional in the vendor shape.

### 6.4 Trigger C: pattern match on one channel

Example: D0 must be high when the trigger fires, level A only.

**Preamble.** mask A = D0, mask B = all.

**Segment 0 block, 13 frames:**

```
C3 20 00 00 00                 # page 0x0020
C1 12 00 00 00 00              # 0x200012
C1 1A 00 04 00 00 00 00 00 00  # 0x20001A = 0
C1 1F 00 04 00 00 00 00 00 00  # 0x20001F = 0
C1 24 00 04 00 00 00 00 00 00  # 0x200024 = 0
C1 00 00 04 00 01 00 00 00 00  # 0x200000 pattern plane A = D0
C1 05 00 04 00 01 00 00 00 00  # 0x200005 pattern plane B = D0
C1 18 00 00 00 00
C1 19 00 00 00 00
C1 0E 00 03 00 00 00 00 00
C1 0A 00 03 00 01 00 00 00     # count = 1
C1 14 00 00 00 00
C1 16 00 00 00 00              # 0x200016, Pattern class byte
C1 17 00 00 00 01              # 0x200017, term type 1
```

The two plane writes use **Pattern state 2**. That is the value that sets
both planes. Under the section 3.5 hypothesis it means "participate and
need level 1". If the pattern matches the wrong level, set
`0x200005` to zero and retry with Pattern state 1.

Do not copy these two payloads from the Edge recipe. The vocabularies
differ.

**Segment 1 block, 7 frames,** all zero.

### 6.5 Trigger D: duration

Example: the condition must hold for 100 samples.

**Preamble.** mask A = the channel or channels of interest, mask B = all.

**Segment 0 block, 11 frames:**

```
C3 20 00 00 00
C1 12 00 00 00 00              # 0x200012
C1 1A 00 04 00 00 00 00 00 00  # 0x20001A = 0
C1 1F 00 04 00 00 00 00 00 00  # 0x20001F = 0
C1 24 00 04 00 00 00 00 00 00  # 0x200024 = 0
C1 18 00 00 00 00
C1 19 00 00 00 00
C1 0E 00 03 00 00 00 00 00     # 0x20000E <- +0x38
C1 0A 00 03 00 64 00 00 00     # 0x20000A <- +0x20, 100 samples
C1 14 00 00 00 00
C1 16 00 00 00 00
C1 17 00 00 00 03              # 0x200017, term type 3, Duration
```

`0x64` is 100 decimal. Duration uses the two dword registers. No gate
`+0x00` or `+0x0C` is set, so the plane frames are absent.

**Gap.** Which of the two dwords carries the duration and which carries a
second parameter is UNVERIFIED. Report 2 maps `+0x38` to `0x20000E` and
`+0x20` to `0x20000A`, and its `.LPF` reading calls them two independent
bounds, not min and max. If the trigger does not fire, swap the two
values.

**Segment 1 block, 7 frames,** all zero.

### 6.6 Trigger E: value (Range)

No register receives the Range bounds. Program the preamble and the seven
ungated frames, set the term type, and expect no comparison.

```
C3 20 00 00 00
C1 18 00 00 00 00
C1 19 00 00 00 00
C1 0E 00 03 00 <bound 1 or 0>
C1 0A 00 03 00 <bound 2 or 0>
C1 14 00 00 00 00
C1 16 00 00 00 <class>
C1 17 00 00 00 <term type>
```

Two candidate payload sources exist for the two dwords. Test them in this
order:

1. Try the two bounds on `0x20000A` and `0x20000E`, truncated to
   integers.
2. If that fails, the comparison is host-side and the device carries only
   the plane masks.

Section 8, question 3.

### 6.7 Trigger F: two levels A then B

The two segments carry the two levels. Program each segment with its own
term block.

**The combine logic is not decoded.** No register was found that selects
A or B, A and B, A then B, or "any order". `CombineMode` has no wire
destination in any of the three reports.

You can therefore **arm both levels**. You cannot yet tell the device
which combine rule to apply. Section 8, question 4.

### 6.8 Trigger Immediate (the phase-advance command)

"Trigger Immediate" is not a term and not a capture mode. It is a
phase-advance command. The CHM describes it as follows.

| Phase | Effect |
| --- | --- |
| Pre-Fill | exit to the Trigger phase |
| Trigger | force a trigger now |
| Post-Fill | stop sampling and complete |

| Register | Page | Effect | Address |
| --- | --- | --- | --- |
| `0x10000A` | `0x0010` | exit Pre-Fill | `0x505375` |
| `0x10000B` | `0x0010` | force the trigger | `0x5053d3` |
| `0x10000C` | `0x0010` | stop | `0x5053af` |

Frame shape:

```
C3 10 00 00 00
C1 0A 00 00 00 <byte>          # exit Pre-Fill
C1 0B 00 00 00 <byte>          # force trigger
C1 0C 00 00 00 <byte>          # stop
```

Send one frame only. The payload value is UNVERIFIED. Use `0x01`.

These three registers need no terms. Use them with Trigger A when you
want a capture without waiting for Pre-Fill to finish.

---

## 7. Corrections to earlier notes

| # | Earlier statement | Corrected statement | Evidence |
| --- | --- | --- | --- |
| 1 | The record is 128 bytes | The record is 112 bytes (`0x70`) | `__vbaCopyBytes(0x70, ...)` at `0x4f3d16` |
| 2 | Segment 1 base is `0x600000` | Segment 1 base is `0x400000` | `PowerR8(2,22)` at `0x472259`; `protocol.py` `SEQUENCER_SEGMENT_BASES` is wrong |
| 3 | Channel *i* occupies bit `33 - i`; D0 is `0x200000000` | Channel *i* occupies bit *i*; D0 is `0x1` | reports 1 and 3; `freq-decode-lane.md` section 15 is wrong |
| 4 | `0x1000003` and `0x1000008` carry a level encoding | They carry channel membership only | `FUN_004fe1eb` never reads the grid value |
| 5 | `DAT_005962e0` and `DAT_005962d4` are "segment 0" and "the other" | Both are per-channel term arrays, indexed by the same segment argument | report 1 |
| 6 | A dword frame reads `C1 0E 00 00 03 00 ...` | A dword frame reads `C1 0E 00 03 00 <4 bytes>` | section 5.1 |
| 7 | `FUN_004f2dd4` writes ten term-bank registers | It writes fifteen when all three gates are set: four value frames, two pattern planes, two edge planes and seven ungated frames | reports 1 and 2; report 2 stopped at the ungated tail |

`protocol.py` needs four changes: the segment base, the bit order, the
`pack_term_states` collapse (the two mode-bank masks are booleans, not a
level pair), and the `+0x46` gate that adds the four value frames.

---

## 8. What remains unknown

Each item is a question with a test. Do not guess past these.

**Question 1. Which edge value is rising?**
Test: arm Trigger B with Edge state 1 on a signal held low, then driven
high. Read the captured trigger column. If the capture reports the
falling edge, use state 2.

**Question 2. What does Pattern state 2 mean?**
Hypothesis: plane A marks participation, plane B marks the needed
level 1.
Test: four one-channel runs. Pattern state 1 on a signal held low, then
high. Pattern state 2 on the same signal, then high. The run that
triggers identifies the level.

**Question 3. Do the Range bounds reach the device?**
Test: set `0x20000A` and `0x20000E` to two different values and vary the
input slowly through both. If the trigger point moves, reading 2 in
section 2.1 is correct. If it does not move, the comparison is host-side.

**Question 4. What register carries the combine mode?**
Test: capture the vendor's own frame stream for all six CombineMode
values on the same terms. Diff the frames. Any register that changes is
the carrier. If no register changes, the combine logic runs on the host
over the captured buffer.

**Question 5. Is `0x200016` sourced from `+0x69` or from `+0x00`?**
Test: a bench A/B. Send `0x200016 = 0` and `0x200016 = 3` with an
otherwise identical Edge block. The value that fires is correct. Report 2
gives `0x4f3c87` as a read of `record+0x00`; reports 1 and
`freq-decode-lane.md` give `+0x69`.

**Question 6. Are `+0x58` and `+0x60` doubles or BSTR pointers?**
Test: set both to a known double such as 100.0 and look for the value in
`0x20001F` and `0x200024`. If the frames carry the digits of a string
instead, report 1 is right and report 2 is wrong. This affects the Value
family only. Edge, Pattern and Duration send zeros and are unaffected.

**Question 7. Does the FPGA need the sequencer block?**
Test: arm a capture with the block omitted. If the capture completes, the
block is optional for a term-less trigger. If not, it is needed.

**Question 8. Does a channel need `DAT_005962a4[ch]` set before its term
bits count?**
Test: arm an Edge term on D0 with D0 absent from the waveform table. Then
add D0. Compare the two captures.

**Question 9. Does `+0x46` have a device meaning?**
Test: send an Edge block with `+0x46` cleared. The four value frames and
the `0x200019` AND disappear. If the trigger still fires, `+0x46` is
host-side only.

**Question 10. Is the plane family assignment right?**
`DAT_005962e0` to Pattern and `DAT_005962d4` to Edge is inferred from
`FUN_004e7843`. Test: swap the two arrays in a capture and compare the
grid columns in the vendor display.

---

## 9. Wire index

Every register this document writes.

| Register | Width | Page | Written by | Purpose |
| --- | --- | --- | --- | --- |
| `0x1000000` | 1 | `0x0100` | block B | clock and mode byte |
| `0x1000001` | 1 | `0x0100` | block B | auxiliary, value UNVERIFIED |
| `0x1000002` | 1 | `0x0100` | block B | trigger enable (`1` enable, `0` disarm) |
| `0x1000003` | 5 | `0x0100` | block B | mask A, trigger scope, 34 bits |
| `0x1000008` | 5 | `0x0100` | block B | mask B, sample set, 34 bits |
| `0x100003` | 1 | `0x0010` | block C and E | arm register (`1` free-run, `0` single-shot) |
| `0x100004` | 2 | `0x0010` | block E | trigger position A |
| `0x100006` | 2 | `0x0010` | block E | trigger position B |
| `0x100008` | 2 | `0x0010` | block E | threshold DAC |
| `0x10000A` | 1 | `0x0010` | Trigger Immediate | exit Pre-Fill |
| `0x10000B` | 1 | `0x0010` | Trigger Immediate | force trigger |
| `0x10000C` | 1 | `0x0010` | Trigger Immediate | stop |
| `0x10000D` | 1 | `0x0010` | block E | tail byte, always `0` |
| `0x200000` | 5 | `0x0020` | block D | pattern plane A |
| `0x200005` | 5 | `0x0020` | block D | pattern plane B |
| `0x20000A` | 4 | `0x0020` | block D | occurrence count or DurationRight |
| `0x20000E` | 4 | `0x0020` | block D | DurationLeft or second parameter |
| `0x200012` | 1 | `0x0020` | block D | digit mode or group Boolean |
| `0x200014` | 1 | `0x0020` | block D | flag byte |
| `0x200016` | 1 | `0x0020` | block D | term class byte |
| `0x200017` | 1 | `0x0020` | block D | term type byte |
| `0x200018` | 1 | `0x0020` | block D | control byte |
| `0x200019` | 1 | `0x0020` | block D | match byte |
| `0x20001A` | 5 | `0x0020` | block D | value low |
| `0x20001F` | 5 | `0x0020` | block D | value middle |
| `0x200024` | 5 | `0x0020` | block D | value high |
| `0x200029` | 5 | `0x0020` | block D | edge plane A |
| `0x20002E` | 5 | `0x0020` | block D | edge plane B |
| `0x400000` to `0x40002E` | as above | `0x0040` | block D | level B, the same offsets |

`0x100003` and `0x1000003` are different registers. The first is the arm
register in the command bank. The second is mask A in the mode bank. A
bug that drops or adds a zero redirects a mask write into the arm
register, or the reverse.

---

## 10. Conformance checklist

A driver is ready when it passes all ten checks.

1. Segment 1 uses base `0x400000` and page `0x0040`.
2. Channel *k* maps to bit *k*. D0 is `0x1`. All 34 is `0x3FFFFFFFF`.
3. Mask B is never zero.
4. Every 40-bit payload is five bytes, little-endian.
5. Both segments are always sent, even when level B has no term.
6. The term block is preceded by `0x100003 <- 0` and `0x100002 <- 1`.
7. A term with `+0x46` non-zero also emits the four value frames.
8. The Edge column and the Pattern column use different state
   vocabularies.
9. `0x1000003` is written as a five-byte mask on page `0x0100` and as a
   one-byte arm value on page `0x0010`.
10. A trigger that does not fire is first checked against the named gaps
    in section 8, not against a new guess.


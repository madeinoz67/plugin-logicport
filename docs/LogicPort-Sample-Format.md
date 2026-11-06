# LogicPort sample format: decoder specification

> Derived 2026-10-29 from `extracted/files/LogicPort.exe` (sha256 `343745f3…`) by a
> three-target decode plus an adjudication pass. Three independent reports, their
> conflicts resolved, and every residual gap named.
>
> Companion to `LogicPort-Protocol.md`: that record covers the wire and the bench
> history; this file covers turning fetched bytes into samples.
>
> **Read the contradictions section before implementing.** It states what is
> resolved, what is a rule rather than a value, and what remains UNVERIFIED.

`start) mod 0x800`
```

DDR mode, `FUN_004ed082`:

```
start    = 2 * (triggerWord - 0x3FF), or 0 when pre-trigger is off
n        = 0x7FF - start;  if (n < 1) n = 1
cursorA  = (counterA + 1 + start) mod 0x400
cursorB  = (counterB + 1 + start) mod 0x400
```

The modulus 0x400 in DDR mode comes from `2048.0 / 2.0` at 0x406ff0 and 0x402588. The modulus 0x800 in SDR mode is a literal at 0x4ef611.

### 4.4 Count expression per read

Compressed mode:

```
byte_count = 0x800 - start          (report 1: n + 1)
byte_count = 0x7FF - start          (report 3: n)
```

This one-byte difference is unresolved. See 5.2 for the resolution rule. Both values are clamped to at least 1 and to at most 2048.

DDR mode:

```
byte_count = (0x800 - start) / 2    (report 1)
byte_count = 0x7FF - start          (report 3)
```

The DDR count is about half the compressed count because two samples share one column.

### 4.5 Reads per mode

Compressed mode (SDR), five reads, all with cursor A:

```
A0 (0x1000), A1 (0x2000), A2 (0x3000), A3 (0x4000), C (0x9000)
```

DDR mode, ten reads:

```
set A: A0, A1, A2, A3, C   all at cursorA
set B: B0, B1, B2, B3, C   all at cursorB
```

Plane C (0x9000) is the only plane fetched twice. That is why the same control byte carries set A clocks in bits 1 and 2, and set B clocks in bits 5 and 6. See 5.15.

### 4.6 C2 read command

`FUN_004c6aa3` at 0x4c6aa3 builds the command. `FUN_004c74b0` is C3 SetSegment.

```
C3:  [0xC3][segment_lo][segment_hi][0x00]        sent only when the segment changes
C2:  [0xC2][off_lo][off_hi][(count-1)_lo][(count-1)_hi]
```

- `off` is the low 16 bits of the absolute address: `planeOffset + cursor`.
- The count field carries `count - 1`. The payload is `count` bytes.
- The response is a 4-byte header, then `count` payload bytes.

The 4-byte header is `[opcode][0x00][counter][0x00]`. `FUN_004cd527` at 0x4cd527 checks the header bytes against a per-address expected-signature array. The header carries no sample data. A decoder must skip it.

The N=256 hardware check fits this model. Count field 255 gives payload 256. Response length is 4 + 256 = 260 bytes.

### 4.7 Client recipe, fetch side

1. Read 2 bytes at 0x0200:0x0000. This is counter A.
2. Read 2 bytes at 0x0200:0x0100. This is counter B.
3. Read 2 bytes at 0x0100:0x000A. This is the trigger word.
4. Compute `start`, `n`, and the cursors as in 4.3.
5. Set segment 0x0200.
6. For each plane, send C2 with offset `planeOffset + cursor` and count from 4.4.
7. Discard the 4-byte header of each response.
8. Confirm all payloads have equal length. Decode with section 3.

---

## 5. Contradictions and gaps

Each item names the conflict, gives the resolution, and gives the confidence. Items marked BLOCKING change decoded output. Items marked NON-BLOCKING affect only interpretation or unneeded paths.

### 5.1 Plane offset table (BLOCKING, resolved)

**Conflict.** Report 1 gives `c08 = base + 0x2000`, `c10 = base + 0x2800`, and lists an extra `c00 = base + 0x1000`. Report 3 gives `c08 = base + 0x1000`, `c10 = base + 0x2000`, and puts `c00` at `base + 0x0101`.

**Resolution.** Report 3 is correct. Four independent reasons:

1. Report 1's own constant list is not a 4 KB stride. `c10 = 10240` is 0x2800, not a plane boundary. Report 3's list is `4096, 8192, 12288, 16384, 20480, 24576, 28672, 32768, 36864` with an exact 8-byte .rdata stride.
2. Report 3 decoded the trailing bytes of each double in .rdata. The last constant ends `b0 40`, which is 4096.0. Report 1 read 0x4028e0 as 8192.
3. Report 1's `c00 = base + 0x1000` collides with plane A0. Report 3's `c00 = base + 0x0101` sits directly above counter B at 0x0100 and is registered as having no consumer.
4. The hardware saw data at 0x1000 and 0x1800. Under report 3, both are plane A0: 0x1000 + cursor 0 and 0x1000 + cursor 0x800. Under report 1, there is no plane at 0x1000 that carries D0 to D7, and 0x1800 has no explanation.

Report 1 applied its own offset table as a shorthand, so its read list `{0x2000, 0x2800, 0x3000, 0x4000, 0x9000}` becomes `{0x1000, 0x2000, 0x3000, 0x4000, 0x9000}` under the correct table. Use the table in section 4.1.

### 5.2 Count expression, one-byte difference (BLOCKING, unresolved)

**Conflict.** Report 1 says the byte count is `DAT_00596e38 + 1` for SDR and `(DAT_00596e38 + 1) / 2` for DDR. Report 3 says the byte count is `max(1, 0x7FF - start)`, which equals `DAT_00596e38`.

**Why it is unresolved.** Report 1 marks the variant boolean chain at 0x4ef745 to 0x4ef7df as UNVERIFIED. It says the operand pair is clear, but the operator chain is not. Report 3 states the `max(1, ...)` form as a direct read. The reported array bounds are `0..DAT_00596e38`, which is `n + 1` entries, and that favours report 1. The 4 KB window fit is exact under `n + 1`: cursor 0x7FF plus count 0x800 ends at 0xFFF.

**Resolution rule for the decoder.** Do not depend on the expression. Request `0x800 - start` bytes per plane. Then decode exactly the number of payload bytes that the device returns. The count is in the C2 header, and the payload always matches the count. The payload length is the only authoritative column count.

### 5.3 Counter read width (NON-BLOCKING, resolved)

**Conflict.** Report 1 says `FUN_004cb534` reads a 32-bit device word. Report 3 says it reads 2 payload bytes and assembles them big-endian: `value = value * 0x100 + byte`.

**Resolution.** Use report 3. It names the assembly line and the call to `FUN_004c6aa3` with count `2.0`. Report 1's "32-bit word" is a loose description. Read 2 bytes and assemble big-endian.

### 5.4 Run value width (BLOCKING, resolved)

**Conflict.** Report 3 calls the run value a "32-bit run length". Reports 1 and 2 say 35 bits.

**Resolution.** Use 35 bits. The code multiplies by 256 five times: `c48 & 7`, then `c20`, `c18`, `c10`, `c08`. That is 3 + 8 + 8 + 8 + 8 bits. Report 3's own pseudocode shows all five accumulations, so the "32-bit" label is a wording error. A `uint64_t` holds the value.

### 5.5 DDR run tokens (NON-BLOCKING, unresolved)

**Conflict.** None. All three reports agree that the DDR body has no bit 3 run-token branch.

**Gap.** It is unknown whether the device can emit run tokens in DDR mode. Report 1 says DDR has no run-length decoding at all and forces the run value to 0. Reports 2 and 3 say the branch is absent from the decompiled body and mark DDR compression UNVERIFIED.

**Resolution.** Treat DDR as uncompressed until a capture proves otherwise. If a DDR capture shows bit 3 set on a control byte, stop and re-analyse. The decode is undefined for that case.

### 5.6 Run value: repeat count or time delta (NON-BLOCKING, reconciled)

**Conflict.** Report 1 says the run value is a time value in sample periods and not a repeat count. Report 2 says it is a repeat count and each run column gives `R + 1` samples.

**Resolution.** Both are the same arithmetic. The run value is a count of sample periods. The application stores it as a per-column time delta and also accumulates it into the expanded sample count. With a constant sample period, a count of sample periods and a repeat count are the same number. Use `R + 1` samples per run column and `total = N + sum(R)`.

### 5.7 The 0x1800 region (NON-BLOCKING, resolved)

**Conflict.** Report 1 calls 0x1800 a naming discrepancy and says the nearest plane offset is 0x2800. Report 3 says 0x1800 is plane A0's window upper half.

**Resolution.** Report 3 is correct. cursor can reach 0x7FF in compressed mode, so plane A0 is read from 0x1000 to 0x1FFF. A read at cursor 0x800 lands at 0x1800. This also confirms the 4 KB window is two copies of the 2048-byte ring.

### 5.8 Base address 2^25 (NON-BLOCKING, resolved)

**Conflict.** Report 1 marks `DAT_00596be0 = 2^25` as UNVERIFIED and infers the operand order from the mask-table idiom. Report 3 states `2^25 = 0x02000000` as a direct read and confirms it with the hardware segment 0x0200.

**Resolution.** Use 0x02000000. Report 3's hardware check closes report 1's gap.

### 5.9 Variant boolean chain for the count (BLOCKING, unresolved)

**Conflict.** Report 1 says the operator chain at 0x4ef745 produces 0 or -1 under standard VB6 semantics. The 0 < count ≤ 65536 guard would reject that.

**Resolution.** The geometry needs a positive count. The operator chain is not decoded. Use the `max(1, ...)` form from report 3 plus the payload-length rule in 5.2.

### 5.10 `DAT_005966fc` (NON-BLOCKING, unresolved)

**Gap.** Report 1 says the tail handler adjusts the last-sample time by +1.0 or -7.0 depending on `DAT_005966fc`. The meaning of that global is unknown. It looks like sample-period or edge calibration.

**Resolution.** This affects only the final time stamp, not the channel decode. Leave it out of the decoder. Add it later if a timing test needs it.

### 5.11 `FUN_004c2da3` start operator (NON-BLOCKING, unresolved)

**Gap.** It is unknown whether the compressed-mode `start` is a clamp of `(triggerWord + 1)` to `[0, 0x7FF]` or a masked AND with 0x7FF.

**Resolution.** Both give the same cursor for in-range trigger words. Use a clamp. Flag the choice in the code comment.

### 5.12 Pre-roll skip purpose (NON-BLOCKING, inferred)

**Gap.** The application peeks one control byte at the start cursor. If bit 3 is set, it advances the cursor by one and drops the count by one. No report states why.

**Resolution.** INFERRED: the device writes a leading control token before the first real column, and the skip discards it. Implement the skip exactly as written. Confirm it with the experiment in section 6.

### 5.13 Set B above 0x4000 not seen on hardware (NON-BLOCKING, unresolved)

**Gap.** Report 1 says the original hardware scan may not have covered 0x5000 to 0x9000. The code says planes B0 to B3 and C exist.

**Resolution.** This is expected. Compression uses set A only. Rescan 0x5000 to 0x9000 with compression off to confirm set B. This does not affect the compressed decoder.

### 5.14 Ring duplication in the 4 KB window (NON-BLOCKING, inferred)

**Gap.** The cursor is modulo 0x800, but reads of up to 0x800 bytes can start at 0x7FF. A linear read then needs the bytes from 0x800 onward.

**Resolution.** INFERRED: the hardware mirrors the 2048-byte ring across the 4096-byte window. The geometry is exact. Cursor 0x7FF plus count 0x800 ends at 0xFFF, which is the last byte of the window.

### 5.15 DDR clock cursor alignment (NON-BLOCKING, unresolved)

**Gap.** The DDR path reads plane C with cursor A and again with cursor B. It is unknown whether counter A and counter B are always equal.

**Resolution.** The two clocks of an odd sample come from the cursor B copy. If the counters ever differ, the odd-sample clocks are misaligned. Check counter A against counter B in the experiment.

---

## 6. Minimal confirming experiment

One capture proves or falsifies the decoder. The signal is known, so the decoded output has a known correct shape.

### 6.1 Setup

- One LogicPort pod, compression ON, compressed (SDR) mode.
- Sample period Ts. Set a slow sample rate so the waveform is easy to count.
- D0: square wave with period 32 samples. That is 16 samples high, 16 samples low.
- D1: tied to VCC. This is the absolute polarity probe.
- D2: tied to GND.
- D3 to D33: tied to GND. Tie the clock inputs low.
- Fetch one window with N = 256 columns. Use the working fetch in segment 0x0200.

### 6.2 What the decoder must produce

1. **Channel 0** is a square wave with period 32 samples, 16 high and 16 low, within one sample.
2. **Channel 1** is constant 1. This proves the inversion rule. Without inversion it decodes to 0.
3. **Channel 2** is constant 0.
4. **Channels 3 to 33** are constant 0, including D32 and D33.
5. **Literal columns** occur at each edge of D0. The count of literal columns equals the number of D0 edges in the window, plus 1.
6. **Run columns** are the rest. The run values are close to 15.
7. **Total samples** is `N + sum(R)`. It must equal the number of sample periods from the first token to the last. Cross-check against the known sample rate and window time.

### 6.3 What proves the decoder correct

- Items 1 to 4 all hold.
- `lp_expand` output matches the injected waveform on channel 0 and the static levels on channels 1 and 2.
- The literal-column count is within 1 of the edge count.
- `sum(R) + N` matches the expected window length within 1 sample.

### 6.4 What falsifies the decoder, and the diagnosis

| Symptom | Cause |
|---|---|
| Channel 0 is constant, channels 1 to 33 show the square wave | Plane offset table is wrong. You used report 1's table. Use section 4.1. |
| Channel 1 decodes to 0, channel 2 decodes to 0 | Inversion rule is missing or inverted. Check 2.7. |
| Channel 0 is a square wave with the wrong period, and run values look like 0x0A0000xx | Run byte order is wrong. Swap the C, A3, A2, A1, A0 order. |
| Run values are correct but every column is a run | Bit 3 test polarity is inverted. Test the raw byte, not the inverted byte. |
| Output is shifted by one sample, or the first sample is garbage | Pre-roll skip is missing or wrong. Check 2.5 case 2. |
| Channel 0 appears on channel 8 or channel 24 | Bit-order or base-offset error inside the plane loop. Check 2.6. |
| Square wave appears on channel 32 or 33 | Control plane offset is wrong. D32 and D33 come from 0x9000 bits 1 and 2. |
| Last column is missing or duplicated | Count off-by-one. Use the payload length as the column count. See 5.2. |

### 6.5 Cheap paired confirmation

Capture the same signal a second time with compression OFF. Every column is then a literal, bit 3 is clear, and there are no run tokens. Decode both captures. The channel 0 arrays must be identical. This separates plane and inversion errors from token errors. If the uncompressed capture is right and the compressed one is wrong, the fault is in the run token path only.

### 6.6 DDR check, optional

Set mode to DDR with two channels driven: D0 and D8 with independent square waves of different periods. Each column index must produce two samples. D0 appears in the even sample from set A, and D8 appears in the even sample too. Set B supplies the odd sample. Confirm that counter A equals counter B (5.15) before you trust the D32 and D33 values.

---

## Appendix A: quick reference card

```
Segment for all planes: 0x0200
Base address:           0x02000000

Counters:  0x0200:0x0000 (A), 0x0200:0x0100 (B), 2 bytes, big-endian
Trigger:   0x0100:0x000A, 2 bytes, big-endian

Planes (SDR, compressed):  0x1000 D0-D7 | 0x2000 D8-D15 |
                           0x3000 D16-D23 | 0x4000 D24-D31 | 0x9000 control
Planes (DDR):              add 0x5000 D0-D7 | 0x6000 D8-D15 |
                           0x7000 D16-D23 | 0x8000 D24-D31, control read twice

Column type:  bit 3 of control byte, raw. 0 = literal, 1 = run token.
Literal:      channel = NOT(bit i of plane byte), base 0/8/16/24.
Clocks:       ch 32 = NOT(ctl bit 1), ch 33 = NOT(ctl bit 2). DDR odd: bits 5, 6.
Run value:    ((ctl & 7) << 32) | (A3 << 24) | (A2 << 16) | (A1 << 8) | A0
Run length:   R + 1 sample periods. State is held from column c-1.
Total:        N + sum(R) samples.
Cursor SDR:   (ctrA + 1 + start) mod 0x800
Cursor DDR:   (ctrA + 1 + start) mod 0x400, (ctrB + 1 + start) mod 0x400
start DDR:    2 * (trigger - 0x3FF), or 0 when pre-trigger is off
C2 command:   [C2][off_lo][off_hi][(count-1)_lo][(count-1)_hi]
Response:     4-byte header, then count payload bytes
```

## Appendix B: source function map

| Name | VA | Role |
|---|---|---|
| `GetSamplesSDR` | 0x4ef26e | compressed decoder |
| `GetSamplesDDR` | 0x4ed082 | double-rate decoder, no compression |
| dispatcher | 0x504778 | mode select at 0x504ec5 |
| `DefineHardwareAddresses` | 0x471af1 | writes plane offsets at 0x471cc2 |
| read primitive | 0x4cd218 | count guard, CopyMemory to array |
| block fetch | 0x4c6aa3 | builds C3 and C2 |
| SetSegment | 0x4c74b0 | C3 |
| header check | 0x4cd527 | validates 4-byte echo |
| word read | 0x4cb534 | 2-byte big-endian counter read |
| one-byte peek | 0x4cb0c5 | pre-roll skip probe |
| start clamp | 0x4c2da3 | variant clamp or mask |
| `SampleFromExpanded` | 0x4fc331 | expanded-model helper |
| `StateFromExpandedSample` | 0x51e058 | expanded-model helper |
| `PreviousTransitionExpandedSample` | 0x51e635 | expanded-model helper |
| `NextTransitionExpandedSample` | 0x51f343 | expanded-model helper |## Part A. Implementation summary

Composed from the three source decodes in Part B. Where they disagreed, the
resolution rule from the contradictions section applies.

### Reading the device

| Step | Frame | Notes |
| --- | --- | --- |
| counter A | `C3 0x0200`, `C2` at `0x0000`, 2 bytes | assemble big-endian |
| counter B | `C2` at `0x0100`, 2 bytes | set B only, DDR mode |
| trigger word | `C3 0x0100`, `C2` at `0x000A`, 2 bytes | feeds `start` |

```
start   = 2 * (triggerWord - 0x3FF)   when pre-trigger is used, else 0
n       = 0x7FF - start               (minimum 1)
cursorA = (counterA + 1 + start) mod 0x800     SDR / compressed mode
cursorA = (counterA + 1 + start) mod 0x400     DDR mode
cursorB = (counterB + 1 + start) mod 0x400     DDR mode only
```

### Reading a plane

`off = planeOffset + cursor`. Request `0x800 - start` bytes and trust the
returned payload length, not the count expression.

```
C2: [0xC2][off_lo][off_hi][(count-1)_lo][(count-1)_hi]
reply: 4-byte header [op][0x00][counter][0x00]  then  count payload bytes
```

Reads per mode: **SDR (compressed)** five planes at cursor A, `0x1000`,
`0x2000`, `0x3000`, `0x4000`, `0x9000`. **DDR** ten planes, both column sets,
`0x1000` to `0x8000` at their cursors plus `0x9000` twice.

### Plane to channel

| Offset | Channels |
| --- | --- |
| `0x1000` | D0 to D7 |
| `0x2000` | D8 to D15 |
| `0x3000` | D16 to D23 |
| `0x4000` | D24 to D31 |
| `0x5000` to `0x8000` | the same four groups, set B (DDR only) |
| `0x9000` | D32/CLK1 and D33/CLK2 |

### Decoding a column

Per column, examine the control byte:

- **bit 3 set, run-length column.** The run value is 35 bits:
  `(c48 & 7) << 32 | c20 << 24 | c18 << 16 | c10 << 8 | c08`.
  The column expands to `R + 1` samples. The previous digital state is held
  and copied forward; `total = N + sum(R)`.
- **bit 3 clear, digital column.** Bits 1 and 2 carry ch32 and ch33. The four
  data planes carry ch0 to ch31. **Every stored bit is the complement of the
  raw level.**

---

## Part B: Source decode reports



### B.1: fetch path (GetSamplesDDR / GetSamplesSDR)

**GetSamplesDDR = FUN_004ed082 / GetSamplesSDR = FUN_004ef26e** (names confirmed by the VB6 error strings `Scheduled.bas->GetSamplesDDR` @0x42e4dc and `Scheduled.bas->GetSamplesSDR` @0x42e57c). Both are called from one dispatcher, **FUN_00504778** (DDR @0x504fd7, SDR @0x504fe9). All addresses are VA in `.text`/`.data`.

---

## 1. Per-plane reads, offsets, cursor, and the count expression

**Read primitive.** `FUN_004cd218(plane_index, byte_count, dest_array)` @0x4cd218:
- 0x4cd28f–0x4cd2b9: `fldl (param2); fcompl 0.0 / 65536.0` → **rejects byte_count ≤ 0 or > 65536** (`Err.Raise 0x7f8, "Invalid Count Value: "`).
- 0x4cd2bb: `FUN_004c6aa3()` = device block fetch (it calls `FUN_004cd527` @0x4cd527, which validates the returned bytes against a per-address expected-signature array, i.e. an echo/header check).
- 0x4cd2e0–0x4cd2f3:
```
fldl (%edi)            ; edi = param2 = byte_count
call __vbaFpI4         ; -> eax = count
esi = DAT_0059776c + DAT_00597798   ; src = fetched buffer
push eax / push esi / push 0x10(%ebp) ; (dst, src, count)
call FUN_0041e9f8      ; = kernel32!RtlMoveMemory
```
`FUN_0041e9f8` is a VB6 `Declare` thunk whose descriptor string at **0x41e480 = "RtlMoveMemory"** (library "kernel32" at 0x41e334), so it is **CopyMemory(dest_array, fetched_buffer, count)**.

**Plane offsets are runtime-initialised.** 0x471c6e–0x471db2 does `DAT_00596cXX = DAT_00596be0 + K` for each plane, K decoded from .rdata:
| global | K | offset |
|---|---|---|
| _DAT_00596c00 | 0x4028e8 = 4096 | base+0x1000 |
| _DAT_00596c08 | 0x4028e0 = 8192 | **base+0x2000** |
| _DAT_00596c10 | 0x4028d8 = 10240 | **base+0x2800** |
| _DAT_00596c18 | 0x4028d0 = 12288 | **base+0x3000** |
| _DAT_00596c20 | 0x4028c8 = 16384 | **base+0x4000** |
| _DAT_00596c28 | 0x4028c0 = 20480 | **base+0x5000** |
| _DAT_00596c30 | 0x4028b8 = 24576 | **base+0x6000** |
| _DAT_00596c38 | 0x4028b0 = 28672 | **base+0x7000** |
| _DAT_00596c40 | 0x4028a8 = 32768 | **base+0x8000** |
| _DAT_00596c48 | 0x4028a0 = 36864 | **base+0x9000** |
| _DAT_00596bf8 | 0x4028f0 = 256 | base+0x0100 |
| _DAT_00596bf0 | 0x401f18 = 1.0 | base+1 |

So the `c08/c10/c18/c20/c48` names in the earlier decode are the **storage addresses** of the offset globals (0x596c08…), not the plane numbers. The real reads are at base+0x2000/0x2800/0x3000/0x4000/0x9000. (`DAT_00596be0` is set at 0x471c37–0x471c52 by `__vbaPowerR8(25.0, 2.0)`. The same idiom initialises the bit-mask table `0x596770 = 2^i` at 0x47cb30–0x47cb6c, so base = **2^25 = 0x2000000**, with operand order inferred from the mask table. The value is **UNVERIFIED**.)

**SDR (FUN_004ef26e), 5 reads, offsets 0x2000/0x2800/0x3000/0x4000/0x9000.**
- 0x4ef42e `FUN_004cb534(&DAT_00596be8,&local_f4)` = start counter (a byte address; `FUN_004cb534` @0x4cb534 reads a 32-bit device word).
- 0x4ef504 `FUN_004cb534(&DAT_00596b48,&local_c4)` = sample count.
- 0x4ef5da: `DAT_00596e38 = 0x7FF − count`, clamped ≥1 (`n` = number of columns).
- 0x4ef5f6–0x4ef619: `local_70 = (start + 1 + count) mod 0x800`, **cursor into a 2048-byte circular window**.
- 0x4ef628–0x4ef658: peek **one byte** (via `FUN_004cb0c5` @0x4cb0c5) at `local_70 + 0x9000`; `testb $0x8` @0x4ef670. If set and `n>0`: `local_70++`, `DAT_00596e38--` (0x4ef689–0x4ef6a8).
- Then 5× `FUN_004cd218(local_70 + off_k, count, array_k)` at 0x4ef83f, 0x4ef88d, 0x4ef9xx… (offsets 0x2000, 0x2800, 0x3000, 0x4000, 0x9000).

**DDR (FUN_004ed082), 10 reads = two plane sets.**
- 0x4ed360 `FUN_004cb534(&DAT_00596be8,&local_168)` (even-set counter, `% 0x400` at 0x4ed3e8), 0x4ed430 `FUN_004cb534(&DAT_00596bf8,&local_180)` (odd-set counter, `% 0x400`), 0x4ed… `&DAT_00596b48` = count.
- 0x4ed433: `DAT_00596e38 = 0x7FF − count`, clamped ≥1.
- 0x4ed44e–0x4ed4be: **modulus = 0x406ff0 (2048.0) / 2.0 = 1024** → `local_fc = (p_be8+1+count) mod 0x400`.
- 0x4ed4ce–0x4ed524: `local_11c = (p_be8'–based counter +1+count) mod 0x400`.
- Then 10× `FUN_004cd218(base+off, count, array)`: set 1 = {0x2000, 0x2800, 0x3000, 0x4000, 0x9000} (0x4ed6e6…0x4ed8xx), set 2 = {0x5000, 0x6000, 0x7000, 0x8000, 0x9000} (0x4ed9xx…). **0x9000 is fetched twice** (once per set).

**Count expression (the value passed as byte_count):**
- SDR: operand pair **VT_I2 2048** vs **VT_I4 (DAT_00596e38+1)**, 0x4ef745 (`movl $0x800,-0x170`), 0x4ef74f (vt=2=`%ebx`), 0x4ef755–0x4ef768 (vt=3, n+1).
- DDR: **VT_I2 2048** vs **VT_R8 (DAT_00596e38+1)/2**, 0x4ed5a5–0x4ed60c (`fildl; fdivl [0x402588]=2.0`).

Both then run `__vbaVarTstLt` (wrapper FUN_004be5f1 → 0x40c534), `__vbaVarTstGt` vs VT_I2 0 (FUN_004be3c0 → 0x40c3b4), `__vbaR8Var` (0x40c558). Both then store the Double into the count slot (SDR `fstpl -0xf8` @0x4ef7df; DDR `fstpl -0x16c` @0x4ed683). **UNVERIFIED:** under standard VB6 Boolean semantics that chain yields 0/−1, which the 0<count≤65536 guard rejects, that contradicts working captures. The unambiguous part is the operand pair. The geometry is: SDR indexes planes by column 0…n, and DDR by column/2. That geometry needs **count = (n+1) for SDR and (n+1)/2 for DDR**, i.e. `(0x7FF − SampleCount + 1)` and half that, bounded by 2048. Treat the exact operator chain as unresolved; treat the effective byte counts as above.

---

## 2. The token parse loop (SDR, FUN_004ef26e)

Arrays ReDim'd at 0x4ef6d1–0x4ef736: `DAT_00596f40` = **Byte[0..0x21][0..DAT_00596e38]** (34 channels × n+1 columns), `DAT_00596f38` and `DAT_00596a9c` = **Double[0..n]** (per-column time delta, per-column value).

Loop `local_28 = 0 … DAT_00596e38` (0x4ef8xx–0x4f05xx). For each column index `c`:

```
if (local_d0[c] & 8) == 0           ; 0x4ef670-style test, c48 plane byte, RAW (not inverted)
    -> DIGITAL column
else if (0 < c)
    -> RUN-LENGTH column
```
(`local_d0` = the 0x9000 plane array; `local_b0/local_38/local_54/local_7c` = 0x2000/0x2800/0x3000/0x4000.)

**Digital column** (e.g. 0x4ef6xx–0x4ef8xx, bits then loops over 8):
- channels 0x20,0x21: `-((byte)~c48[c] >> 1 & 1)` and `>> 2 & 1` → `DAT_00596f40[0x20][c]`, `[0x21][c]`
- 4 × 8 bits: `-(ushort)((plane_k[c] & mask[i]) == 0)` → `DAT_00596f40[i + 0/8/0x10/0x18][c]`, mask = `DAT_00596770[i]` (a Long[] initialised to `2^i`, 1…0x80 at 0x47cb30)
- `DAT_00596f38[c] = 0` (no time added)

**Run-length column** (`else if 0<c`, 0x4f05xx):
```
local_104 = (c48[c] & 7)
local_104 = local_104*256 + c20_plane[c]
local_104 = local_104*256 + c18_plane[c]
local_104 = local_104*256 + c10_plane[c]
local_104 = local_104*256 + c08_plane[c]
DAT_00596f38[c] = local_104 ; local_a4 += local_104
for k = 0..0x21: DAT_00596f40[k][c] = DAT_00596f40[k][c-1]   ; hold previous state
```
So a **run count is 35 bits**, big-endian across planes: **bit 0–7 = c08 (0x2000), 8–15 = c10 (0x2800), 16–23 = c18 (0x3000)**. Higher bits: **24–31 = c20 (0x4000), 32–34 = low 3 bits of c48 (0x9000)**. It is a *time* value (delta in sample periods), not a repeat count, the channel states are just held.

**"Column"** = one byte position `c` in the plane set; it is one sample instant. **Flag/type byte** = the 0x9000-plane byte at the same index, **bit 3 (0x08)**: 0 = digital (8 bits/plane), 1 = run-length.

**Polarity:** all channel bits are taken inverted (`notb`/`~` then test), matches the hardware note that samples are stored inverted. The *flag* test uses the raw byte.

---

## 3. Termination

- **Plane/array end:** the column index exceeds `DAT_00596e38` (n). The do-while's `if (n < c)` branches to the tail handler at 0x4f05df/0x4f0635.
- **Run end:** the next column with bit3 = 0, a digital column rewrites all 34 channel bits and zeroes `DAT_00596f38[c]`. There is **no explicit run counter**; a run's only record is the accumulated time `local_a4`.
- **Column 0 special case:** `else if (0 < local_28)`, a run-length flag at c=0 is ignored entirely (nothing written).
- **Pre-loop skip:** if the byte at the start cursor has bit3 set and n>0. Then the cursor is advanced and n decremented (0x4ef689–0x4ef6a8). So the run-flag byte itself is never decoded as a column.
- **Tail/time base:** `_DAT_00596e28 = DAT_00596e38 + local_a4` (0x4f0635); then `_DAT_0059748c = DAT_00596a9c[(n − m + 1) mod (n+1)]` where `m = FUN_004cb534(&DAT_00596bd8)`, adjusted **+1.0 or −7.0** depending on `DAT_005966fc`, then `_DAT_00596ef8 = _DAT_005962e4 + _DAT_0059748c` and clamped ≥0. (Meaning of `DAT_005966fc` = UNVERIFIED; the ±1/−7 looks like sample-period/edge calibration.)

---

## 4. Even/odd combination and 8-plane → per-channel bits

**SDR:** one column per iteration; no even/odd split. Channel map per column:
`bit i of 0x2000 → ch i` · `bit i of 0x2800 → ch i+8` · `bit i of 0x3000 → ch i+0x10` · `bit i of 0x4000 → ch i+0x18` (i=0..7, 1 when the stored bit is 0) · `~c48 bit1 → ch 0x20`, `~c48 bit2 → ch 0x21`. Total 0x22 = 34 channels, stored as `DAT_00596f40[ch][column]`.

**DDR:** two columns per loop pass (`local_28` even, `local_dc = local_28+1`), byte index = `Int(column/2)` (0x4eda86–0x4edb02: `fildl c; fdivl 2.0; faddl [0x401f18]=1.0; __vbaFpI4; leal -1(%eax)`):
| source plane | → channels | column |
|---|---|---|
| 0x2000 (c08) bit i | ch i (0..7) | even |
| 0x2800 (c10) bit i | ch i+8 | even |
| 0x3000 (c18) bit i | ch i+0x10 | even |
| 0x4000 (c20) bit i | ch i+0x18 | even |
| 0x9000 (c48) bit1 / bit2 | ch 0x20 / 0x21 | even |
| 0x5000 (c28) bit i | ch i | odd |
| 0x6000 (c30) bit i | ch i+8 | odd |
| 0x7000 (c38) bit i | ch i+0x10 | odd |
| 0x8000 (c40) bit i | ch i+0x18 | odd |
| 0x9000 (c48) bit5 / bit6 | ch 0x20 / 0x21 | odd |

So the even and odd sample sets are **separate plane groups**. The single 0x9000 plane carries the top two channels for both: bits 1–2 for even, bits 5–6 for odd. `DAT_00596f38[c]` is forced 0 for both columns (DDR has no run-length decoding at all), and the sequence is produced by writing columns 0,1,2,3… in order.

---

## 5. DDR vs SDR selection (FUN_00504778, 0x504ec5–0x504f00)

```
504ec5: cmpl $0x1, 0x59660c        ; global mode gate
504ecc: jne  0x504fde              ; != 1  -> SDR (0x4ef26e)
504ed9: push 0x5965c0 ; push 0x59678c
504ee5: call __vbaDerefAry1        ; element of array DAT_0059678c at index DAT_005965c0
504eea: movl 0x8(%eax), %eax       ; DWORD at element+8
504eed/4eee: dec; dec ; je 0x504fcc   ; == 2 -> DDR (0x4ed082)
504ef5/4ef6: dec; dec ; je 0x504f05   ; == 4 -> Err.Raise 2040 (0x7f8)
504ef9: jmp 0x504fe5               ; else   -> SDR (0x4ef26e)
```
- **`[0x59660c] != 1` → SDR immediately.**
- If `== 1`, the selector is the DWORD at **element+8** of the record array `DAT_0059678c[DAT_005965c0]`: **2 → DDR**. **4 → `Err.Raise 2040, "QDR Not implemented yet"`** (string @0x42f82c, verified UTF-16). **Anything else → SDR**.
- Consequence: the task brief's “DDR = four-byte elements / SDR = two-byte elements” is **contradicted**. The code maps **2 → DDR**, **4 → QDR (unimplemented)**, so 1 byte/sample → SDR, 2 → DDR, 4 → QDR. The same element+8 field is used inside DDR at 0x4ed6xx as `element+8 × DAT_005974a0` (a size×count buffer-length check), consistent with a per-sample element size.

---

### Cross-checks against the ground truth
- Consistent: 0x9000 = the plane carrying D32/CLK1/D33/CLK2 and the run-length flag. 0x2000/0x2800/0x3000/0x4000 = D0–D31. Data regions at 0x1000/0x2000/0x3000/0x4000 seen on hardware match the initialised offsets. The 0x1800 in the ground truth has no matching offset here; the closest is **0x2800**. Flag this as a naming discrepancy, not a contradiction.
- The 2048-byte circular window (SDR `mod 0x800`) and 1024-byte per-set window (DDR `mod 0x400`, from `2048.0/2.0` at 0x406ff0) bracket the column counts exactly. The bounds are n+1 ≤ 2048 SDR and (n+1)/2 ≤ 1024 DDR. This supports the count readings above.


### B.2: the expanded/decompression family

I have the complete picture. Compiling findings.

---

# LogicPort.exe: "Expanded" (decompression) family decoded

**Target:** `LogicPort.exe` sha256 `343745f3300c77a7c3c28b370c1ed34416810ec000aa5fd2d79cfe73e3aaa868` (PE32/i386, `.text` 0x401000–0x596000, image base 0x400000). Ghidra unattached these, so I used the prologue-scan + objdump routes.

## 0. Address correction (important)

The four addresses in the brief are **the addresses of the VB6 procedure-name strings, not code entry points.** `0x42f594` is the UTF‑16LE string `"Scheduled.bas->SampleFromExpanded"`. Each string's 4‑byte LE address appears once as a `mov edx, <string>` in the owning function's error-handler block. Real entries (verified: `55 8B EC 83 EC 14 68 76 C2 40 00` = `push ebp; mov ebp,esp; sub esp,14h; push 0x40c276`):

| Proc (as named by its own string) | String VMA | xref site | **Code entry** |
|---|---|---|---|
| `SampleFromExpanded` | 0x42f594 | 0x4fc541 | **0x4fc331** |
| `StateFromExpandedSample` | 0x4302d4 | 0x51e4f2 | **0x51e058** |
| `PreviousTransitionExpandedSample` | 0x430328 | 0x51f1c9 | **0x51e635** |
| `NextTransitionExpandedSample` | 0x43038c | 0x51fe79 | **0x51f343** |

Prologue rule used throughout: marker `68 76 C2 40 00` in a canonical VB6 prologue sits at **entry+6** (entry = marker−6). All four above decompile cleanly under Ghidra. The `0x40c67e` in those blocks is `MSVBVM60.DLL::__vbaStrCopy`, confirms they are VB6 error-handler prologues that name their own procedure, so the string→function binding is solid.

**Crucial:** the actual decompressor is **not** in these four functions. They are navigation helpers over an *already-expanded* transition model. The decompression itself is in **`Scheduled.bas->GetSamplesSDR` @ 0x4ef26e** (and `GetSamplesDDR` @ 0x4ed082). I decoded it below, that is where the wire format lives.

---

## 1. Wire format: compressed sample record (the answer to "what an expanded sample looks like vs a compressed one")

`GetSamplesSDR` (0x4ef26e) reads **five parallel byte-planes** and, per slot `i`, forms a 34-channel digital word. Plane names are the same `.data` globals the earlier decode called `c08..c48`, and the code's channel assignment matches your ground truth exactly:

- `_DAT_00596c08` → channels **D0..D7** (buffer `local_b0`)
- `_DAT_00596c10` → **D8..D15** (`local_38`)
- `_DAT_00596c18` → **D16..D23** (`local_54`)
- `_DAT_00596c20` → **D24..D31** (`local_7c`)
- `_DAT_00596c48` → **D32, D33 + control** (`local_d0`)

Decisive evidence, the arrays are built as:
```
__vbaRedim(0x80,1,&DAT_00596f40,0x11,2,0x21,0,DAT_00596e38,0);  // byte[0..0x21][0..N]  (34 channels x slots)
__vbaRedim(0x80,8,&DAT_00596f38,5,1,DAT_00596e38,0);            // double[1..N]  (per-slot run count)
__vbaRedim(0x80,8,&DAT_00596a9c,5,1,DAT_00596e38,0);            // double[1..N]  (per-slot time)
```
`0x21` = 33 → 34 channels (D0..D33). `DAT_00596e38` = N (slot count).

**Per slot, the five plane bytes are read as:**

| Plane byte | Meaning in a NORMAL sample | Meaning in a COMPRESSED token |
|---|---|---|
| `c48` (`local_d0`) bit3 (0x08) | **compressed flag = 0** | **compressed flag = 1** |
| `c48` bit1, bit2 | D32 = NOT(bit1), D33 = NOT(bit2) | part of run counter |
| `c48` bits0–2 | bit0 unused | run counter bits [34:32] (MSB) |
| `c20` (`local_7c`) | D24..D31 | run counter bits [31:24] |
| `c18` (`local_54`) | D16..D23 | run counter bits [23:16] |
| `c10` (`local_38`) | D8..D15 | run counter bits [15:8] |
| `c08` (`local_b0`) | D0..D7 | run counter bits [7:0] (LSB) |

So a **normal** sample = 34 real bits (low pin → stored 1, inverted). A **compressed** sample is *not* data at all. It is a **run-length token that borrows the whole 34-bit field to carry a 35-bit repeat count**. That is why "five to eight bits toggle per plane region when one pin is driven": you are looking at counter bytes, not channels. Both are 5 bytes/slot; the record type is self-describing via bit3.

---

## 2. The expansion algorithm (item 2)

Flags/tests and the exact branch, quoted from `GetSamplesSDR` (0x4ef26e):

```
if ((*param_1 & 2) == 0) { ... }        // param_1 = mode bits; bit1 selects SDR vs DDR path
...
if ((*(byte *)(local_d0 + uVar3) & 8) == 0) {      // NORMAL sample
    ... // store NOT(raw bit) into DAT_00596f40[ch][slot]  (see item 3 inversion)
    *(DAT_00596f38 + local_28) = 0;                // run count = 0
}
else if (0 < (int)local_28) {                       // COMPRESSED token (slot>0)
    local_104 = (double)(*(byte *)(local_d0 + uVar3) & 7);
    local_104 = local_104 * 256.0 + (double)*(byte *)(local_7c + uVar3);
    local_104 = local_104 * 256.0 + (double)*(byte *)(local_54 + uVar3);
    local_104 = local_104 * 256.0 + (double)*(byte *)(local_38 + uVar3);
    local_104 = local_104 * 256.0 + (double)*(byte *)(local_b0 + uVar3);
    *(DAT_00596f38 + local_28) = local_104;        // store run count at this slot
    local_a4 = local_104 + local_a4;               // accumulate Σrun
    // carry previous state forward for all 34 channels:
    pdVar6 = 0; while ((int)pdVar6 < 0x22) {
        *(DAT_00596f40[ch][local_28]) = *(DAT_00596f40[ch][local_28 - 1]);
        pdVar6++;
    }
}
...
_DAT_00596e28 = (double)DAT_00596e38 + local_a4;   // expanded total = N + Σrun   (at 0x4f0635)
```

**Expansion semantics:**
- A normal slot stores the 34-bit inverted state and run=0 → contributes 1 expanded sample.
- A compressed slot **holds all 34 channels at the previous slot's state** and records a run count R. It contributes **R + 1** expanded samples (the slot's held sample plus R repeats). Mathematically the whole buffer expands to **`expandedTotal = N + Σ R_i`** (`_DAT_00596e28`), where `DAT_00596f38[i]` = R_i (0 for real samples). This is pure run-length "hold previous state", there is no per-token sample payload.
- This is the app's *read* side: it faithfully reconstructs `DAT_00596f40` (34×N states), `DAT_00596f38` (N runs), `DAT_00596a9c` (N times), and the expanded total. A decoder must do the same.

`GetSamplesDDR` (0x4ed082) is the double-data-rate sibling. It un-interleaves **two sample columns per raw slot** from **eight** data planes plus a control byte (`c08/c10/c18/c20` even sample, `c28/c30/c38/c40` odd sample, `c48` control). It writes `[][local_28]` and `[][local_dc=local_28+1]`. The extra `c28/c30/c38/c40` doubles are referenced there. It does **not** show the `bit3` run-token branch in its decompiled body, DDR compression, if any, is not in this function. *UNVERIFIED whether DDR supports the run token.*

---

## 3. Run count: location, width, bit order (item 3)

- **Location:** read from the five per-slot plane bytes, raw (no inversion).
- **Width:** 35 bits = **3 bits (control-plane low bits, bits[2:0]) + 4 full bytes**, i.e. `(c48 & 0x07) << 32 | c20 << 24 | c18 << 16 | c10 << 8 | c08`.
- **Bit order:** big-endian across plane order **c48 → c20 → c18 → c10 → c08** (c48 is MSB, c08 is LSB).
- **Flag bit alongside it:** bit **0x08** of the c48 plane = "this slot is a run token". When set, the D32/D33 pins are *not* transported (all channels carried).
- The accumulation is a `double` in VB (`local_a4`), so the effective cap is 2^35; no static clamp found.

**Normal-sample inversion (confirmed):** each data bit is stored negated
```
local_164 = ... -((byte)~*(byte *)(local_d0 + uVar3) >> 1 & 1);   // D32 = NOT(raw bit1)
...
local_164 = ... -(ushort)((*(byte *)(local_b0 + uVar3) & *(byte *)(DAT_00596770 + uVar14*4)) == 0);  // Dx = NOT(raw bit)
```
`DAT_00596770 + i*4` is a byte bit-mask table (stride 4). This is your "samples stored inverted" rule. Note that it applies **only to the data-bit decode of normal samples, never to the run counter or flag**.

---

## 4. How the app knows a record is compressed (item 4)

- `0x429134` is **not** a `CompressedData` field. It is one VB6 member-name table string listing all three public members of the acquisition class: `"SamplePeriod","TriggerSample","CompressedData"` (47 chars, UTF‑16LE). It is referenced exactly once in the whole image: `push $0x429134` at **0x4ac546**, inside the large acquisition/device manager **`FUN_004ac14d`** (body 0x4ac14d–0x4b0123). The surrounding `push/mov ecx/call 0x40c5d0` + `push 0x422758/call 0x40c5ca` is a late-bound member access on that object (VB6 `__vba*` thunks; exact slot not statically resolved).
- **The decode path does not read the `CompressedData` property at all.** Compression is detected **per sample** by bit3 of the c48 control byte, read as `(*(byte*)(local_d0+i) & 8)`. That is why one mixed buffer can hold both normal and run-token slots.
- Interpretation (LABELED): `CompressedData` is the *configuration toggle* sent to the device. The same class carries `SamplePeriod` and `TriggerSample`. The device then emits self-describing per-slot tokens. So `CompressedData` governs what the device *produces*, not how the app decodes. The app always keys off bit3.

---

## 5. The transition helpers (item 5): what they compute

These four operate on the **expanded/post-processed model**, not the wire buffer. Shared globals:
- `DAT_00596a9c[i]` = `double` **sample time** at index `i` (sorted ascending).
- `DAT_00596f1c[i]` = **signal-row descriptor** array. Fields (read-only, from decompile):
  - `+0x04` int type (1 = single channel, 2 = group/bus)
  - `+0x10` int link (≥0 → index of another `f1c` row; <0 → direct)
  - `+0x14` int channel number D0..D33 (0..0x21) for type-1, or index into `DAT_00596abc` for type-2
  - `+0x18` short flag; `+0x1C` value
  - `+0x48` pointer to the row's **transition array**; `+0x4C` int = transition count/last index
- Transition record (element of `row+0x48`). Field `+0x00` is a short valid/present flag. Field `+0x0C` is an int **base sample index** into `DAT_00596a9c`. Field `+0x10` is a double **time offset**, so Prev/Next return `transitionTime = DAT_00596a9c[+0x0C] + (double)(+0x10)`, at sub-sample resolution. *(Field offsets confirmed by reader code; the writer was not decompiled, UNVERIFIED for exact construction.)*

**`SampleFromExpanded(time)` @0x4fc331**, given a time, returns the sample index at/before it by a backward scan of the time array:
```
iVar1 = __vbaI4Var(...);                 // starting index (hint; -1 → default DAT_00596e38)
while (-1 < iVar1) {
    pdVar2 = __vbaDerefAry1(DAT_00596a9c, iVar1);
    if (*pdVar2 <= *param_1) { local_28 = iVar1; break; }
    iVar1--;
}
```
This is the anchor the other three start from.

**`StateFromExpandedSample(rowIndex, time, skipEmptyFlag)` @0x51e058**, within one row's transition array (`row+0x48`), finds the transition covering `time` and returns its index:
```
iVar2 = *(int *)(iVar1 + 0x4c);                       // count
while (-1 < iVar2) {
    if (*param_3 == 0 || (psVar4 = __vbaDerefAry1(*(iVar1+0x48), iVar2), *psVar4 != 0)) {
        iVar2b = __vbaDerefAry1(*(iVar1+0x48), local_24);
        __vbaDerefAry1(DAT_00596a9c, *(int *)(iVar2b + 0xc));   // transition time
        if (fVar5 <= *param_2) { local_28 = local_24; break; }  // time <= target
    }
    iVar2 = local_24 - 1;                             // walk backwards
}
```

**`PreviousTransitionExpandedSample(time, …)` @0x51e635**, global backward search across **all** rows for the nearest transition strictly before the cursor:
```
local_5c = FUN_004fc331(param_1, -1);                 // anchor sample index from SampleFromExpanded
... for each row local_2c, for each transition local_38:
    if (fVar18 < fVar17) {                            // candidate time < current best
        local_28 = *(uint *)(iVar6 + 0xc);            // chosen transition's base sample index
        local_34 = *(undefined8 *)(iVar6 + 0x10);     // its time offset
        *param_7 = local_2c; *param_8 = local_38;     // owning row + transition
    }
```
Returns the previous transition's sample index, its offset, and the owning row/transition indices.

**`NextTransitionExpandedSample(time, …)` @0x51f343** is the forward mirror. It scans rows ascending from the anchor for the first transition with time > cursor. It returns `local_30 = *(double *)(iVar6 + 0x10) + *pdVar4` (transition time) and row/transition indices. When none is found it returns `*param_4 = DAT_00596e38` (last index) with `local_30 = _DAT_00596e28` (the expanded total).

So the "transition index" model is: **each edge is `{validFlag(+0x00), baseSampleIndex(+0x0C), timeOffset(+0x10)}`, stored per signal row. Edges are found by sample-index bracket on the shared `DAT_00596a9c` time array.** `SampleFromExpanded` maps time→sample index. State/Prev/Next map that into edge positions.

---

## Confidence ledger

**CONFIRMED (decompiled):** function entries; SDR plane→channel assignment (c08=D0‑7, c10=D8‑15, c18=D16‑23, c20=D24‑31, c48=D32/33+ctrl); bit3 compressed flag. **Also confirmed:** the 35-bit big-endian run count in (c48&7)<<32|c20<<24|c18<<16|c10<<8|c08; carry-forward + `expandedTotal = N + Σrun`; data-bit inversion. **Also confirmed:** the four Expanded helpers' scan logic; `0x429134` member-name table + single set-site at 0x4ac546.

**UNVERIFIED:**
- Numeric plane offsets: `_DAT_00596c08…c48` are **runtime-initialized doubles, all zero in the static image** (filled via `FUN_004cb534` config reads). We cannot map them statically to the observed device regions 0x1000/0x1800/0x2000/0x3000/0x4000. The *channel grouping* is confirmed; the *numeric region order* is not.
- `+0x22` slot–record writer (who builds `DAT_00596f1c[i].+0x48`) not decompiled; transition struct offsets are reader-derived.
- DDR (`GetSamplesDDR` 0x4ed082) even/odd interleave is clear, but its run-token path (if any) is not present in the decompiled body.
- Exact helper identity of the late-bound member access at 0x4ac546 (Ghidra's thunk names at 0x40c5xx look aliased; treated as a generic property set/get).

Key files: `extracted/files/LogicPort.exe`; full `GetSamplesSDR` pseudocode and the four helpers are in the persisted REA tool-result files in the persisted REA tool-result files.


### B.3: plane layout and addressing

## RESULT: LogicPort plane layout and fetch addressing (decoded from `LogicPort.exe` sha256 343745f3…)

All addresses below are Ghidra/objdump VA in `.text` (VMA 0x401000, file offset = VA − 0x400).

---

### 0. Correction to the premise (important)

`DAT_00596c08 / c10 / c18 / c20 / c28 / c30 / c38 / c40 / c48` are **plane BASE addresses, not cursors.** They are written once, as doubles, by `DefineHardwareAddresses` **FUN_00471af1** at `0x471cc2`–`0x471dae`:

```
471cc2: fldl 0x596be0          ; base = 2^25 = 0x02000000
471cc8: faddl 0x4028e0         ; + 4096.0
471cce: fstpl 0x596c08         ; c08 = 0x02001000
   … +8192→c10, +12288→c18, +16384→c20, +20480→c28,
     +24576→c30, +28672→c38, +32768→c40, +36864→c48
```

Constants read from memory at `0x4028a0..0x4028e0` = 36864, 32768, 28672, 24576, 20480, 16384, 12288, 8192, 4096.0 (`x402890..x4028e0` bytes `…e2 40 / …e0 40 / …dc 40 / …d8 40 / …d4 40 / …d0 40 / …c8 40 / …c0 40 / …b0 40`).

The **actual cursors are 16-bit registers read by the app at runtime**:
* `DAT_00596be8` = `0x02000000` (`_DAT_00596be0` = `__vbaPowerR8(2,25)`, `0x471c…`)
* `DAT_00596bf8` = `0x02000000 + 256 = 0x02000100`

Read via **FUN_004cb534** (`0x4cb534`), which calls `FUN_004c6aa3` with count `2.0` and assembles 2 payload bytes **big-endian** (`*param_2 = *param_2*0x100 + buf[i]`).

So the earlier "slots are cursors" reading was a mislabel: the slots are the 4 KB plane windows. The cursors are the two counter registers at `0x02000000` / `0x02000100`.

---

### 1. Plane → (segment, offset) table  [VERIFIED]

`_DAT_00596be0 = 2^25 = 0x02000000` ⇒ **segment 0x0200** for every plane.

| slot | absolute address | segment | offset | channels (verified in unpacker) |
|---|---|---|---|---|
| `DAT_00596c08` | 0x02001000 | **0x0200** | **0x1000** | D0–D7 (set A) |
| `DAT_00596c10` | 0x02002000 | 0x0200 | 0x2000 | D8–D15 (set A) |
| `DAT_00596c18` | 0x02003000 | 0x0200 | 0x3000 | D16–D23 (set A) |
| `DAT_00596c20` | 0x02004000 | 0x0200 | 0x4000 | D24–D31 (set A) |
| `DAT_00596c28` | 0x02005000 | 0x0200 | 0x5000 | D0–D7 (set B) |
| `DAT_00596c30` | 0x02006000 | 0x0200 | 0x6000 | D8–D15 (set B) |
| `DAT_00596c38` | 0x02007000 | 0x0200 | 0x7000 | D16–D23 (set B) |
| `DAT_00596c40` | 0x02008000 | 0x0200 | 0x8000 | D24–D31 (set B) |
| `DAT_00596c48` | 0x02009000 | 0x0200 | 0x9000 | D32/CLK1 + D33/CLK2 (both sets) |

Non-plane addresses built by the same function:

| slot | address | seg | off | meaning |
|---|---|---|---|---|
| `DAT_00596be8` | 0x02000000 | 0x0200 | 0x0000 | cursor counter A (2-byte read) |
| `DAT_00596bf8` | 0x02000100 | 0x0200 | 0x0100 | cursor counter B |
| `DAT_00596bf0` | 0x02000001 | 0x0200 | 0x0001 | defined; no consumer found |
| `DAT_00596c00` | 0x02000101 | 0x0200 | 0x0101 | defined; no consumer found |
| `DAT_00596b48` | 0x010000A | 0x0100 | 0x000A | trigger-position word (source of `start`) |

Channel grouping evidence (`FUN_004ed082`, per-plane bit loops, mask `DAT_00596770 + k*4`):

* `local_134` (`0x02001000`) → `DAT_00596f40[uVar+0]` … `+7`  → **D0–D7**
* `local_90`  (`0x02002000`) → `…uVar+8` … `+15` → **D8–D15**
* `local_c8`  (`0x02003000`) → `…uVar+0x10` … `+0x17` → **D16–D23**
* `local_108` (`0x02004000`) → `…uVar+0x18` … `+0x1F` → **D24–D31**
* `local_ac`  (`0x02005000`) → index 0–7 of the *second* slot `local_dc`
* `local_154` (`0x02006000`) → `+8` of `local_dc`
* `local_74`  (`0x02007000`) → `+0x10` of `local_dc`
* `local_e8`  (`0x02008000`) → `+0x18` of `local_dc`
* `local_34`  (`0x02009000`, cursor A) → `f40[0x20]`, `f40[0x21]` from bits 1 and 2 → **D32/CLK1, D33/CLK2**
* `local_58`  (`0x02009000`, cursor B) → `f40[0x20]`, `f40[0x21]` from bits 5 and 6 → same two clocks, set B

Decisive lines (pseudocode of `FUN_004ed082`, program address 0x4ed6b6 onward):

```
local_248 = (double)local_fc  + _DAT_00596c08;   FUN_004cd218(&local_248,&local_170,local_134);
local_248 = (double)local_fc  + _DAT_00596c10;   … local_90
local_248 = (double)local_fc  + _DAT_00596c18;   … local_c8
local_248 = (double)local_fc  + _DAT_00596c20;   … local_108
local_248 = (double)local_fc  + _DAT_00596c48;   … local_34
local_248 = (double)local_11c + _DAT_00596c28;   … local_ac
local_248 = (double)local_11c + _DAT_00596c30;   … local_154
local_248 = (double)local_11c + _DAT_00596c38;   … local_74
local_248 = (double)local_11c + _DAT_00596c40;   … local_e8
local_248 = (double)local_11c + _DAT_00596c48;   … local_58
```
(assembly 0x4ed6bc / 0x4ed710 / 0x4ed764 / 0x4ed7b8 / 0x4ed80c / 0x4ed85d / 0x4ed8b1 / 0x4ed905 / 0x4ed956 / 0x4ed9aa)

**Note:** `c48` (0x9000) is fetched **twice**, once with cursor A, once with cursor B. That is the only slot read twice, and it is the reason the clock byte carries two clock channels plus the compression flag.

Only two functions in the whole binary consume these globals: **FUN_004ed082** (0x4ed082, bulk 2-sample decoder) and **FUN_004ef26e** (0x4ef26e, streaming/compressed decoder). Verified by scanning every reference to `0x596c00..0x596cff` in the disassembly.

---

### 2. Byte count and how the cursor enters  [VERIFIED]

```c
DAT_00596e38 = 0x7FF - start;          // 0x4ed42e-0x4ed43f
if (DAT_00596e38 < 1) DAT_00596e38 = 1; // 0x4ed451-0x4ed455

start = (triggerPos - 0x3FF) * 2;      // 0x4ed4a7..0x4ed4be, triggerPos = read(0x010000A)
                                       // = 0 when (*param_1 & 2) != 0 (no pre-trigger)
cursorA = (ctrA + 1 + start) % 0x400;  // 0x4ed4a7..0x4ed4be  (ctrA = read(0x02000000) % 0x400)
cursorB = (ctrB + 1 + start) % 0x400;  // 0x4ed507..0x4ed524  (ctrB = read(0x02000100) % 0x400)
```

The `% 0x400` is literal: modulus comes from `fldl 0x406ff0 (=2048.0)` / `fdivl 0x402588 (=2.0)` → 1024, then `idivl`.

Streaming/compressed path **FUN_004ef26e** (0x4ef42e–0x4ef4b9, 0x4ef4fd–0x4ef619):

```c
ctrA   = read(0x02000000);
start  = <variant clamp/select of (triggerPos+1) against 0 and 0x7FF>;  // FUN_004c2da3 @0x4c2da3
DAT_00596e38 = 0x7FF - start;  if (<1) = 1;
iVar13 = (ctrA + 1 + start) % 0x800;      // 0x4ef611  idivl $0x800
local_1c0 = (double)iVar13 + _DAT_00596c48;   // 0x4ef637 → single-byte read of clock plane
```

So per-plane read:

```
address = plane_base[slot] + cursor
count   = max(1, 0x7FF - start)
```
with `cursor ∈ [0,0x3FF]` (2-samples-per-column mode, 9 planes) **or** `[0,0x7FF]` (compressed mode, 5 planes).

---

### 3. What the 0x7FF arithmetic means  [VERIFIED + one inference]

* `0x7FF = 2047` is the **last valid sample index of a plane row** ⇒ **2048 samples (columns) per plane row**. Independent bounds checks confirm the row: `if (0x7ff < uVar1) __vbaGenerateBoundsError();` at 0x4ed-f… (repeated 12×, e.g. 0x4ed… "0x7ff" in FUN_004ed082) and `movl $0x7ff, -0x130(%ebp)` at 0x4f069a.
* `count = 0x7FF - start` therefore means "everything from `start` to the end of the 2048-sample row", clamped to ≥1. `start` itself is a **sample offset** supplied by the trigger-position word.
* Why the 4 KB stride: max cursor (0x7FF) + max count (0x7FF bytes) = 0xFFE. So a read can never cross the 4 KB plane window. The 0x1000 stride is exactly sized for this. That is a structural consistency check, not an independent proof, but it is exact.
* The `(o + 1 − 0x400)` you were given is the pre-trigger subtraction: `2*(triggerPos − 0x3FF) ≡ 2*(triggerPos + 1 − 0x400)`. `0x400` = 1024 = half of the 2048-sample row, i.e. the trigger word is biased by `0x3FF` so that "centre of window" reads as `0x400`. The ×2 is the interleave (two samples share a column in uncompressed mode).
* INFERRED: `FUN_004c2da3` (0x4c2da3) is a VB variant clamp/select (it calls `__vbaVarTstLt`/`__vbaVarTstGt`), so the streaming path's `start` is `clamp(triggerPos+1, 0, 0x7FF)`, i.e. the same row-local sample offset, without the ×2 because the compressed stream is one sample per column. **UNVERIFIED** whether it is a clamp or a masked AND; the downstream `% 0x800` is real regardless (0x4ef611).

---

### 4. Observed hardware offsets → decoded planes

| observed offset in segment 0x0200 | what it is |
|---|---|
| 0x0000–0x00FF | register window of `0x02000000` (cursor counter A, 2-byte). Explains `02 5B 02 5B…` and the rolling 5B→5E byte, it is a 16-bit counter read repeatedly, not sample data |
| 0x0100 | register `0x02000100` (cursor counter B); upper half of the counter at 0x0101 |
| 0x0100–0x0FFF | control/counter area; reads 0x00 when idle → **your "0x0100–0x0800 all zeros" is this, not missing sample data** |
| **0x1000** | plane 0 = **D0–D7** |
| **0x1800** | still plane 0 (0x1000 + cursor 0x800), the compressed-mode cursor reaches 0x7FF, so the upper half of the 4 KB window is live |
| **0x2000** | plane 1 = **D8–D15** |
| **0x3000** | plane 2 = **D16–D23** |
| **0x4000** | plane 3 = **D24–D31** |
| 0x5000 / 0x6000 / 0x7000 / 0x8000 | planes 4–7 = D0–D31, **set B**, used only in 2-samples-per-column mode; **unused when compression is on** |
| 0x9000 | clock/flag plane: D32/CLK1 + D33/CLK2 (+ the compression run flag) |

So the five data-bearing regions you saw (0x1000, 0x1800, 0x2000, 0x3000, 0x4000) are exactly the four data planes. The fifth region, 0x1800, is the upper half of plane 0 of the **compressed** stream. The absence of data at 0x5000–0x9000 is consistent with compression being active (set B unused). **UNVERIFIED**: whether your original scan actually covered >0x4000, a 0x800-step scan that stopped at 0x4000 would look identical. Re-scan 0x5000–0x9000 to confirm.

---

### 5. Wire addressing (this is what a client needs to emit)  [VERIFIED]

**FUN_004c6aa3** (0x4c6aa3) is the block read; **FUN_004c74b0** is SetSegment (C3).

```
4c6b42: fldl (%esi)            ; address
4c6b4d: fdivl 0x402418         ; address / 65536.0     (0x402418 = 65536.0)
4c6b70: call __vbaFPInt        ; -> segment
4c6b84: fcompl -0x124          ; compare with cached DAT_005977e8
4c6b96: call 0x4c74b0          ; C3 SetSegment. ONLY when segment changed
4c6ba4: fldl (%esi); FpI4; andl $0xffff    ; offset = address & 0xFFFF
4c6bbf: cx = 0xC2
4c6bcb: byte[1] = 0xC2
4c6be7: byte[2] = offset & 0xFF            ; low
4c6c04: byte[3] = offset / 0x100           ; high
4c6c0a: fldl 0xc(%ebp); fsubl 0x401f18     ; count - 1.0   (0x401f18 = 1.0)
4c6c2d: byte[4] = (count-1) & 0xFF
4c6c59: byte[5] = (count-1) / 0x100
Ordinal_644(local_5c + 1)  ; sends 5 bytes
```

⇒ **C2 = `[0xC2][off_lo][off_hi][(count-1)_lo][(count-1)_hi]`, 5 bytes**, preceded by a C3 carrying the segment. This is a **correction** to the earlier note that those two bytes were a "rate": they are the **low 16 bits of the address**. It also reconciles the hardware fact "N=256 → 260 bytes": the field carries 255, payload = field+1 = 256.

Client recipe:
1. `start = 2*(triggerPos − 0x3FF)` (or 0 for no pre-trigger), `count = max(1, 0x7FF − start)`
2. read 2 bytes at seg 0x0200 off 0x0000 → ctrA; off 0x0100 → ctrB
3. `curA = (ctrA + 1 + start) & 0x3FF` (uncompressed) / `& 0x7FF` (compressed); same for curB
4. for each plane: SetSegment(0x0200), C2(offset = planeOffset + cur, N = count)

---

### 6. Bonus: the compression format is in this same code  [VERIFIED]

`FUN_004ef26e` (`0x4ef26e`) is the compressed stream decoder. Clock-plane byte = `local_d0`:

```c
if ((*(byte *)(local_d0 + uVar3) & 8) == 0) {         // bit 3 clear = LITERAL sample
    ... extract 8 bits per plane via mask DAT_00596770[k*4], inverted: (byte & mask)==0 ? 1 : 0
    CLK1 = -((~local_d0[i] >> 1) & 1);
    CLK2 = -((~local_d0[i] >> 2) & 1);
} else if (0 < local_28) {                            // bit 3 set = RUN token
    local_104 = (local_d0[i] & 7);
    local_104 = local_104*256 + local_7c[i];          // 0x4000
    local_104 = local_104*256 + local_54[i];          // 0x3000
    local_104 = local_104*256 + local_38[i];          // 0x2000
    local_104 = local_104*256 + local_b0[i];          // 0x1000  → 32-bit run length
    ... shift previous sample forward, accumulate local_a4 += run
}
```

So: **bit 3 (0x08) of the clock byte = "this column is a run/repeat token, not a sample"**. The 32-bit value assembled from the four data planes is the run length. The lower 3 bits of the clock byte hold CLK1/CLK2 (bits 1,2 for set A, bits 5,6 for set B). That explains exactly why one driven pin produced 5–8 toggling bits per plane region: those are run-length fields, not channel bits.

---

### UNVERIFIED / not pinned

* Whether the original hardware scan covered offsets above 0x4000 (0x5000–0x9000). Code says set B planes exist; hardware says nothing was reported there; both are consistent with compression = on.
* Exact operator of `FUN_004c2da3` (variant clamp vs AND-with-0x7FF) for the streaming path's `start`.
* Whether the 4 KB per-plane window is hardware decode granularity or just a safe address stride: the arithmetic is exact either way. The underlying gate array layout was not inspected.
* The 4-byte per-fetch header returned ahead of the payload (seen on hardware) was not traced to code in this pass.
* `DAT_00596bf0` (0x02000001) and `DAT_00596c00` (0x02000101) are defined but have no consumer found in the binary.

**Artifacts**: `extracted/files/LogicPort.exe`; full disassembly cached at `/tmp/lp.asm`; pseudocode extracts at `/tmp/f_ed082.c`. Findings banked to MuninnDB `01M62MHXGMP3QYMCZNY77PCRRZ`.


### B.4: synthesis (front sections truncated by the agent; see Part B)

`start) mod 0x800`
```

DDR mode, `FUN_004ed082`:

```
start    = 2 * (triggerWord - 0x3FF), or 0 when pre-trigger is off
n        = 0x7FF - start;  if (n < 1) n = 1
cursorA  = (counterA + 1 + start) mod 0x400
cursorB  = (counterB + 1 + start) mod 0x400
```

The modulus 0x400 in DDR mode comes from `2048.0 / 2.0` at 0x406ff0 and 0x402588. The modulus 0x800 in SDR mode is a literal at 0x4ef611.

### 4.4 Count expression per read

Compressed mode:

```
byte_count = 0x800 - start          (report 1: n + 1)
byte_count = 0x7FF - start          (report 3: n)
```

This one-byte difference is unresolved. See 5.2 for the resolution rule. Both values are clamped to at least 1 and to at most 2048.

DDR mode:

```
byte_count = (0x800 - start) / 2    (report 1)
byte_count = 0x7FF - start          (report 3)
```

The DDR count is about half the compressed count because two samples share one column.

### 4.5 Reads per mode

Compressed mode (SDR), five reads, all with cursor A:

```
A0 (0x1000), A1 (0x2000), A2 (0x3000), A3 (0x4000), C (0x9000)
```

DDR mode, ten reads:

```
set A: A0, A1, A2, A3, C   all at cursorA
set B: B0, B1, B2, B3, C   all at cursorB
```

Plane C (0x9000) is the only plane fetched twice. That is why the same control byte carries set A clocks in bits 1 and 2, and set B clocks in bits 5 and 6. See 5.15.

### 4.6 C2 read command

`FUN_004c6aa3` at 0x4c6aa3 builds the command. `FUN_004c74b0` is C3 SetSegment.

```
C3:  [0xC3][segment_lo][segment_hi][0x00]        sent only when the segment changes
C2:  [0xC2][off_lo][off_hi][(count-1)_lo][(count-1)_hi]
```

- `off` is the low 16 bits of the absolute address: `planeOffset + cursor`.
- The count field carries `count - 1`. The payload is `count` bytes.
- The response is a 4-byte header, then `count` payload bytes.

The 4-byte header is `[opcode][0x00][counter][0x00]`. `FUN_004cd527` at 0x4cd527 checks the header bytes against a per-address expected-signature array. The header carries no sample data. A decoder must skip it.

The N=256 hardware check fits this model. Count field 255 gives payload 256. Response length is 4 + 256 = 260 bytes.

### 4.7 Client recipe, fetch side

1. Read 2 bytes at 0x0200:0x0000. This is counter A.
2. Read 2 bytes at 0x0200:0x0100. This is counter B.
3. Read 2 bytes at 0x0100:0x000A. This is the trigger word.
4. Compute `start`, `n`, and the cursors as in 4.3.
5. Set segment 0x0200.
6. For each plane, send C2 with offset `planeOffset + cursor` and count from 4.4.
7. Discard the 4-byte header of each response.
8. Confirm all payloads have equal length. Decode with section 3.

---

## 5. Contradictions and gaps

Each item names the conflict, gives the resolution, and gives the confidence. Items marked BLOCKING change decoded output. Items marked NON-BLOCKING affect only interpretation or unneeded paths.

### 5.1 Plane offset table (BLOCKING, resolved)

**Conflict.** Report 1 gives `c08 = base + 0x2000`, `c10 = base + 0x2800`, and lists an extra `c00 = base + 0x1000`. Report 3 gives `c08 = base + 0x1000`, `c10 = base + 0x2000`, and puts `c00` at `base + 0x0101`.

**Resolution.** Report 3 is correct. Four independent reasons:

1. Report 1's own constant list is not a 4 KB stride. `c10 = 10240` is 0x2800, not a plane boundary. Report 3's list is `4096, 8192, 12288, 16384, 20480, 24576, 28672, 32768, 36864` with an exact 8-byte .rdata stride.
2. Report 3 decoded the trailing bytes of each double in .rdata. The last constant ends `b0 40`, which is 4096.0. Report 1 read 0x4028e0 as 8192.
3. Report 1's `c00 = base + 0x1000` collides with plane A0. Report 3's `c00 = base + 0x0101` sits directly above counter B at 0x0100 and is registered as having no consumer.
4. The hardware saw data at 0x1000 and 0x1800. Under report 3, both are plane A0: 0x1000 + cursor 0 and 0x1000 + cursor 0x800. Under report 1, there is no plane at 0x1000 that carries D0 to D7, and 0x1800 has no explanation.

Report 1 applied its own offset table as a shorthand, so its read list `{0x2000, 0x2800, 0x3000, 0x4000, 0x9000}` becomes `{0x1000, 0x2000, 0x3000, 0x4000, 0x9000}` under the correct table. Use the table in section 4.1.

### 5.2 Count expression, one-byte difference (BLOCKING, unresolved)

**Conflict.** Report 1 says the byte count is `DAT_00596e38 + 1` for SDR and `(DAT_00596e38 + 1) / 2` for DDR. Report 3 says the byte count is `max(1, 0x7FF - start)`, which equals `DAT_00596e38`.

**Why it is unresolved.** Report 1 marks the variant boolean chain at 0x4ef745 to 0x4ef7df as UNVERIFIED. It says the operand pair is clear, but the operator chain is not. Report 3 states the `max(1, ...)` form as a direct read. The reported array bounds are `0..DAT_00596e38`, which is `n + 1` entries, and that favours report 1. The 4 KB window fit is exact under `n + 1`: cursor 0x7FF plus count 0x800 ends at 0xFFF.

**Resolution rule for the decoder.** Do not depend on the expression. Request `0x800 - start` bytes per plane. Then decode exactly the number of payload bytes that the device returns. The count is in the C2 header, and the payload always matches the count. The payload length is the only authoritative column count.

### 5.3 Counter read width (NON-BLOCKING, resolved)

**Conflict.** Report 1 says `FUN_004cb534` reads a 32-bit device word. Report 3 says it reads 2 payload bytes and assembles them big-endian: `value = value * 0x100 + byte`.

**Resolution.** Use report 3. It names the assembly line and the call to `FUN_004c6aa3` with count `2.0`. Report 1's "32-bit word" is a loose description. Read 2 bytes and assemble big-endian.

### 5.4 Run value width (BLOCKING, resolved)

**Conflict.** Report 3 calls the run value a "32-bit run length". Reports 1 and 2 say 35 bits.

**Resolution.** Use 35 bits. The code multiplies by 256 five times: `c48 & 7`, then `c20`, `c18`, `c10`, `c08`. That is 3 + 8 + 8 + 8 + 8 bits. Report 3's own pseudocode shows all five accumulations, so the "32-bit" label is a wording error. A `uint64_t` holds the value.

### 5.5 DDR run tokens (NON-BLOCKING, unresolved)

**Conflict.** None. All three reports agree that the DDR body has no bit 3 run-token branch.

**Gap.** It is unknown whether the device can emit run tokens in DDR mode. Report 1 says DDR has no run-length decoding at all and forces the run value to 0. Reports 2 and 3 say the branch is absent from the decompiled body and mark DDR compression UNVERIFIED.

**Resolution.** Treat DDR as uncompressed until a capture proves otherwise. If a DDR capture shows bit 3 set on a control byte, stop and re-analyse. The decode is undefined for that case.

### 5.6 Run value: repeat count or time delta (NON-BLOCKING, reconciled)

**Conflict.** Report 1 says the run value is a time value in sample periods and not a repeat count. Report 2 says it is a repeat count and each run column gives `R + 1` samples.

**Resolution.** Both are the same arithmetic. The run value is a count of sample periods. The application stores it as a per-column time delta and also accumulates it into the expanded sample count. With a constant sample period, a count of sample periods and a repeat count are the same number. Use `R + 1` samples per run column and `total = N + sum(R)`.

### 5.7 The 0x1800 region (NON-BLOCKING, resolved)

**Conflict.** Report 1 calls 0x1800 a naming discrepancy and says the nearest plane offset is 0x2800. Report 3 says 0x1800 is plane A0's window upper half.

**Resolution.** Report 3 is correct. cursor can reach 0x7FF in compressed mode, so plane A0 is read from 0x1000 to 0x1FFF. A read at cursor 0x800 lands at 0x1800. This also confirms the 4 KB window is two copies of the 2048-byte ring.

### 5.8 Base address 2^25 (NON-BLOCKING, resolved)

**Conflict.** Report 1 marks `DAT_00596be0 = 2^25` as UNVERIFIED and infers the operand order from the mask-table idiom. Report 3 states `2^25 = 0x02000000` as a direct read and confirms it with the hardware segment 0x0200.

**Resolution.** Use 0x02000000. Report 3's hardware check closes report 1's gap.

### 5.9 Variant boolean chain for the count (BLOCKING, unresolved)

**Conflict.** Report 1 says the operator chain at 0x4ef745 produces 0 or -1 under standard VB6 semantics. The 0 < count ≤ 65536 guard would reject that.

**Resolution.** The geometry needs a positive count. The operator chain is not decoded. Use the `max(1, ...)` form from report 3 plus the payload-length rule in 5.2.

### 5.10 `DAT_005966fc` (NON-BLOCKING, unresolved)

**Gap.** Report 1 says the tail handler adjusts the last-sample time by +1.0 or -7.0 depending on `DAT_005966fc`. The meaning of that global is unknown. It looks like sample-period or edge calibration.

**Resolution.** This affects only the final time stamp, not the channel decode. Leave it out of the decoder. Add it later if a timing test needs it.

### 5.11 `FUN_004c2da3` start operator (NON-BLOCKING, unresolved)

**Gap.** It is unknown whether the compressed-mode `start` is a clamp of `(triggerWord + 1)` to `[0, 0x7FF]` or a masked AND with 0x7FF.

**Resolution.** Both give the same cursor for in-range trigger words. Use a clamp. Flag the choice in the code comment.

### 5.12 Pre-roll skip purpose (NON-BLOCKING, inferred)

**Gap.** The application peeks one control byte at the start cursor. If bit 3 is set, it advances the cursor by one and drops the count by one. No report states why.

**Resolution.** INFERRED: the device writes a leading control token before the first real column, and the skip discards it. Implement the skip exactly as written. Confirm it with the experiment in section 6.

### 5.13 Set B above 0x4000 not seen on hardware (NON-BLOCKING, unresolved)

**Gap.** Report 1 says the original hardware scan may not have covered 0x5000 to 0x9000. The code says planes B0 to B3 and C exist.

**Resolution.** This is expected. Compression uses set A only. Rescan 0x5000 to 0x9000 with compression off to confirm set B. This does not affect the compressed decoder.

### 5.14 Ring duplication in the 4 KB window (NON-BLOCKING, inferred)

**Gap.** The cursor is modulo 0x800, but reads of up to 0x800 bytes can start at 0x7FF. A linear read then needs the bytes from 0x800 onward.

**Resolution.** INFERRED: the hardware mirrors the 2048-byte ring across the 4096-byte window. The geometry is exact. Cursor 0x7FF plus count 0x800 ends at 0xFFF, which is the last byte of the window.

### 5.15 DDR clock cursor alignment (NON-BLOCKING, unresolved)

**Gap.** The DDR path reads plane C with cursor A and again with cursor B. It is unknown whether counter A and counter B are always equal.

**Resolution.** The two clocks of an odd sample come from the cursor B copy. If the counters ever differ, the odd-sample clocks are misaligned. Check counter A against counter B in the experiment.

---

## 6. Minimal confirming experiment

One capture proves or falsifies the decoder. The signal is known, so the decoded output has a known correct shape.

### 6.1 Setup

- One LogicPort pod, compression ON, compressed (SDR) mode.
- Sample period Ts. Set a slow sample rate so the waveform is easy to count.
- D0: square wave with period 32 samples. That is 16 samples high, 16 samples low.
- D1: tied to VCC. This is the absolute polarity probe.
- D2: tied to GND.
- D3 to D33: tied to GND. Tie the clock inputs low.
- Fetch one window with N = 256 columns. Use the working fetch in segment 0x0200.

### 6.2 What the decoder must produce

1. **Channel 0** is a square wave with period 32 samples, 16 high and 16 low, within one sample.
2. **Channel 1** is constant 1. This proves the inversion rule. Without inversion it decodes to 0.
3. **Channel 2** is constant 0.
4. **Channels 3 to 33** are constant 0, including D32 and D33.
5. **Literal columns** occur at each edge of D0. The count of literal columns equals the number of D0 edges in the window, plus 1.
6. **Run columns** are the rest. The run values are close to 15.
7. **Total samples** is `N + sum(R)`. It must equal the number of sample periods from the first token to the last. Cross-check against the known sample rate and window time.

### 6.3 What proves the decoder correct

- Items 1 to 4 all hold.
- `lp_expand` output matches the injected waveform on channel 0 and the static levels on channels 1 and 2.
- The literal-column count is within 1 of the edge count.
- `sum(R) + N` matches the expected window length within 1 sample.

### 6.4 What falsifies the decoder, and the diagnosis

| Symptom | Cause |
|---|---|
| Channel 0 is constant, channels 1 to 33 show the square wave | Plane offset table is wrong. You used report 1's table. Use section 4.1. |
| Channel 1 decodes to 0, channel 2 decodes to 0 | Inversion rule is missing or inverted. Check 2.7. |
| Channel 0 is a square wave with the wrong period, and run values look like 0x0A0000xx | Run byte order is wrong. Swap the C, A3, A2, A1, A0 order. |
| Run values are correct but every column is a run | Bit 3 test polarity is inverted. Test the raw byte, not the inverted byte. |
| Output is shifted by one sample, or the first sample is garbage | Pre-roll skip is missing or wrong. Check 2.5 case 2. |
| Channel 0 appears on channel 8 or channel 24 | Bit-order or base-offset error inside the plane loop. Check 2.6. |
| Square wave appears on channel 32 or 33 | Control plane offset is wrong. D32 and D33 come from 0x9000 bits 1 and 2. |
| Last column is missing or duplicated | Count off-by-one. Use the payload length as the column count. See 5.2. |

### 6.5 Cheap paired confirmation

Capture the same signal a second time with compression OFF. Every column is then a literal, bit 3 is clear, and there are no run tokens. Decode both captures. The channel 0 arrays must be identical. This separates plane and inversion errors from token errors. If the uncompressed capture is right and the compressed one is wrong, the fault is in the run token path only.

### 6.6 DDR check, optional

Set mode to DDR with two channels driven: D0 and D8 with independent square waves of different periods. Each column index must produce two samples. D0 appears in the even sample from set A, and D8 appears in the even sample too. Set B supplies the odd sample. Confirm that counter A equals counter B (5.15) before you trust the D32 and D33 values.

---

## Appendix A: quick reference card

```
Segment for all planes: 0x0200
Base address:           0x02000000

Counters:  0x0200:0x0000 (A), 0x0200:0x0100 (B), 2 bytes, big-endian
Trigger:   0x0100:0x000A, 2 bytes, big-endian

Planes (SDR, compressed):  0x1000 D0-D7 | 0x2000 D8-D15 |
                           0x3000 D16-D23 | 0x4000 D24-D31 | 0x9000 control
Planes (DDR):              add 0x5000 D0-D7 | 0x6000 D8-D15 |
                           0x7000 D16-D23 | 0x8000 D24-D31, control read twice

Column type:  bit 3 of control byte, raw. 0 = literal, 1 = run token.
Literal:      channel = NOT(bit i of plane byte), base 0/8/16/24.
Clocks:       ch 32 = NOT(ctl bit 1), ch 33 = NOT(ctl bit 2). DDR odd: bits 5, 6.
Run value:    ((ctl & 7) << 32) | (A3 << 24) | (A2 << 16) | (A1 << 8) | A0
Run length:   R + 1 sample periods. State is held from column c-1.
Total:        N + sum(R) samples.
Cursor SDR:   (ctrA + 1 + start) mod 0x800
Cursor DDR:   (ctrA + 1 + start) mod 0x400, (ctrB + 1 + start) mod 0x400
start DDR:    2 * (trigger - 0x3FF), or 0 when pre-trigger is off
C2 command:   [C2][off_lo][off_hi][(count-1)_lo][(count-1)_hi]
Response:     4-byte header, then count payload bytes
```

## Appendix B: source function map

| Name | VA | Role |
|---|---|---|
| `GetSamplesSDR` | 0x4ef26e | compressed decoder |
| `GetSamplesDDR` | 0x4ed082 | double-rate decoder, no compression |
| dispatcher | 0x504778 | mode select at 0x504ec5 |
| `DefineHardwareAddresses` | 0x471af1 | writes plane offsets at 0x471cc2 |
| read primitive | 0x4cd218 | count guard, CopyMemory to array |
| block fetch | 0x4c6aa3 | builds C3 and C2 |
| SetSegment | 0x4c74b0 | C3 |
| header check | 0x4cd527 | validates 4-byte echo |
| word read | 0x4cb534 | 2-byte big-endian counter read |
| one-byte peek | 0x4cb0c5 | pre-roll skip probe |
| start clamp | 0x4c2da3 | variant clamp or mask |
| `SampleFromExpanded` | 0x4fc331 | expanded-model helper |
| `StateFromExpandedSample` | 0x51e058 | expanded-model helper |
| `PreviousTransitionExpandedSample` | 0x51e635 | expanded-model helper |
| `NextTransitionExpandedSample` | 0x51f343 | expanded-model helper |

## 7. The compressed record format, decoded 2026-10-29

This section records the record format from the reader `FUN_004ef26e`. It
supersedes the earlier reading of section 6.5 where the two disagree.

### Five planes, one byte per plane per record

One record consumes one byte from each of five planes. The reader adds the
cursor to each plane base before the read.

The flags byte decides the record type, and **bit 3 is the discriminator**:

| Bit 3 of the flags byte | Record type |
| --- | --- |
| clear, `0x00` to `0x07` range | channel-state record, one raw sample |
| set | time-delta record, a 35 bit interval in sample-clock ticks |

### A channel-state record

The four data planes carry channels 0 to 31, eight channels per plane. A stored
bit is the complement of the level, so a channel reads high when its masked bit
reads zero.

Channels 32 and 33 come from the flags byte itself, in bits 1 and 2, and they
carry the same complement.

A stream of nothing but channel-state records is the uncompressed form. **The
same reader decodes both forms**, which is why a client that reads five planes
gets raw samples without telling the device anything about compression.

### A time-delta record

The value is 35 bits, assembled from the flags byte's low three bits and the
four data planes:

```
delta = ((flags & 0x07) << 32) | (plane4 << 24) | (plane3 << 16) | (plane2 << 8) | plane1
```

The decoder adds this interval, in sample-clock ticks, to the running time. The
channel states carry forward unchanged, so a delta record costs no sample.

The decoder also peeks one byte before the bulk read. If that byte has bit 3
set, it is a coarse time marker and the reader skips it and advances the cursor
by one.

### What this means for a client

1. Read five planes at the cursor, one byte per plane per record.
2. Walk the records in step. On bit 3 clear, emit a sample from the four data
   planes and the flags byte. On bit 3 set, advance time by the delta and emit
   nothing.
3. An uncompressed stream needs no special case. Every record will have bit 3
   clear.

### Compression is not carried by any image

Since the sample-position setting turned out to select the image family, the same
possibility applied to compression. It does not hold. The bench loaded every
image, captured, and examined its flags plane for records with bit 3 set:

| Image | Mode byte | State records | Delta records |
| --- | --- | --- | --- |
| 0, 5 | `0x0C` | 2047 | **0** |
| 1 | `0x1C` | 2047 | **0** |
| 2 | `0x1A` | 2047 | **0** |
| 3 | `0x1D` | 2047 | **0** |
| 4 | `0x1B` | 2047 | **0** |
| 6, 7 | `0x0C` | 2047 | **0** |

All eight store raw channel-state records. No image provides compression, so
compression is not an image axis and a client cannot reach it through image
selection.

That leaves compression with one unobserved route only: the late-bound
`UseCompression` property in the application. That application cannot drive the
hardware on this machine, so its traffic is not capturable. A client
therefore reads raw data, which is correct for every image.

### The compression checkbox has no route to the device, proven exhaustively

A full trace settles it. The checkbox is `chkMain` index 0 in `SamplingSetup.frm`
and it changes exactly three things: the enabled state of the Pre and Post-Fill
limit controls, one in-memory settings field at `0x5973c0` which is struct
`0x5971cc` plus `0x1f4`, and the `UseCompression` line in the settings file.

The field has **five references in the entire image**: the defaults initialiser
writes 1, the settings-file load stores it, the settings-file save reads it, one
GUI routine consumes it, and the dialog writes it. Nothing else touches it, so no
path leads from the checkbox to a register, to the mode byte, to the reader
dispatch, or to the image choice.

The mode byte's bits 3 and 4 hold constant values in this build. They come from
`0x596308` and `0x59630c`, each with exactly one reference, the mode formula
itself, and the data section there is zero. So no configuration a user sets can
reach those bits.

**The last route, now closed.** The help text links compression to the Pre and
Post Fill Time Limits, so the limits were the remaining candidate. They do reach
the device, but not as configuration: **the host enforces the deadline itself**
with a millisecond clock and, on expiry, writes the constant `0x01` to the
pre-fill slot `0x10000A` or the post-fill slot `0x10000B`. The limit value never
reaches any register in any form, and the two expiry writes carry no companion
bit.

So the whole compression half of the Sample Mode dialog is host-side: the
checkbox, the two limits, and the reader choice. The only device-visible effect of
a fill limit is the same `0x01` poke that Trigger Immediate produces, identical
whatever the limit value or the compression state.

One residual, named rather than claimed closed: the mode table at `DAT_0059678c`
is built by code no lane walked, and if compression couples into device
configuration at all it could only hide there. The dispatcher itself never
references the compression field.

### The compression checkbox itself

The checkbox writes a late-bound property named `UseCompression` on the device
object. This decode found no wire byte that depends on that boolean. The sample-mode value and the clock mode
choose the reader, not the checkbox.

So the checkbox has no proven route to the device, and a client cannot set it
from a decoded frame. Treat the checkbox as a host-side decode choice until a
live capture of the application's own USB traffic shows otherwise. A client
reads five planes and decodes a mixed stream, which covers both forms.

### The flags byte bit map, measured

| Bit | Meaning | Evidence |
| --- | --- | --- |
| 0 | pre and post fill boundary: clear during pre-fill, set during post-fill, one transition at the trigger | runs of 1030 and 1016 |
| 1 | CLK1, inverted | 1027 transitions with a 500 Hz square on CLK1 |
| 2 | CLK2, inverted and live while floating | set in all 2047 real records |
| 3 | record type: clear is a channel-state record, set is a 35 bit delta | clear in all 2048 records of a raw capture |
| 5 and 6 | the byte-mode second sample CLK pair | unproven |
| 4 and 7 | zero throughout, unassigned | observed |

Ignore the two boundary records. Record 0 and record 2047 are sentinels, and the
last one reads `0x00`. Slice records 1 to 2046, then take the trigger index from
the single bit 0 transition. The configured pre-fill was 1032 records and the
data shows 1030, and the two sentinels account for the difference.

### The plane offsets, reconciled

An earlier note here recorded a conflict: the decode placed channels 0 to 7 at
`0x800` while measurement placed them at `0x1000`. The decode had arithmetic
errors in three constants, and once corrected it agrees with measurement exactly:
`0x1000` for channels 0 to 7, `0x2000` for 8 to 15, `0x3000` for 16 to 23,
`0x4000` for 24 to 31, and `0x9000` for the flags plane. There is no conflict,
and the measured map stands as the reference.


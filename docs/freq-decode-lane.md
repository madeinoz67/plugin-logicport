# Frequency-Read and StartAcquisition Decode: Static Lane (2026-10-28)

> Source: `LogicPort.exe` v3.4.0.2371, disassembled with LLVM objdump (macOS).
> Tooling disclosure: the REA MCP server advertised itself but bound no callable
> tools in this lane, so the fallback (objdump + a PE import-table parser in
> python3) was used, per the dispatch brief. VB6 runtime thunks were named by
> resolving the IAT. Every claim below cites a VA. The exe is unlicensed vendor
> content; this file records disassembly facts only and copies no vendor bytes.

## 0. Headline

The 2026-10-28 wire sessions left the device in the EE state because the
reconstructed dialect was wrong in four independent ways:

1. The 16-bit frame field is a **register offset, little-endian**, not a rate
   word and not a big-endian address.
2. **C3 is a page-select command.** Every register address is 32-bit; the upper
   16 bits (`Int(reg / 65536)`) select a page and are transmitted only in a C3
   frame, sent when the page changes. No probe ever selected the frequency
   page, so every probe read hit the wrong page.
3. The device reply is `<echo> <ctr_hi> <ctr_lo> [data...]` and the app
   **validates the counter echo** against a host-side counter that starts at 0
   after the resync flush and increments after every C1/C2/C3. There is no
   "aux" byte: data starts at reply byte 3.
4. The C1 write frame is 6 bytes for a byte write (one payload byte), written
   as two FT_Writes (5-byte header, then payload). The hardware session's
   `freq_probe.c` sent 7 bytes with two payload bytes.

## 1. Frame grammar (corrected)

All evidence in this section: C1 sender FUN_004c6fad, C2 sender FUN_004c6aa3,
C3 sender FUN_004c74b0, ACK parser FUN_004cd527, init FUN_004c5a6d, resync
FUN_004c78c9.

### 1.1 The register-double argument

Every usb wrapper takes `(pointer_to_double, value_pointer)`. The double is the
full 32-bit register address as a floating-point value. The senders split it:

- `sel = Int(double / 65536.0)`, the page (`fdivl 0x402418` at 0x4c7051;
  0x402418 = 65536.0, confirmed in the literal pool).
- `r = int(double) & 0xFFFF`, the offset (`0x4c70a8..0x4c70af`).
- The register doubles are built once at startup by FUN_00471af1 with
  `__vbaPowerR8(2.0, exp)`: `2^30 = 0x40000000` (frequency page, at
  0x471bb2), `2^29 = 0x20000000` (sample-mode page, at 0x471b5f), `2^25 =
  0x02000000` (sample planes, at 0x471c37), `2^24 = 0x1000000` (clock/masks,
  at 0x471dbe), `2^21 = 0x200000` (sequencer segment 0, at 0x4720b0),
  `2^20 = 0x100000` (status page, at 0x471ec4), plus fixed offsets from the
  literal pool (1, 2, 3, 4, 5, 6, 8, 10, 11, 12, 13, 15 at 0x401f18..0x402888;
  0x100..0x9000 for the sample planes at 0x4028f0..0x4028a0).

### 1.2 C1 write (FUN_004c6fad)

```
send:  FT_Write(FB+1, 5)   ->  C1 r_lo r_hi L_lo L_hi      (0x4c7157)
       FT_Write(payload, L+1)                              (0x4c7198)
wait:  FT_GetStatus poll until RxQueue >= 3, timeout 1000 ms (0x4c71ca..0x4c71e0)
read:  FT_Read(3)                                          (0x4c720a)
parse: FUN_004cd527 (counter check)
ctr++: 0x5977e0 = (0x5977e0 + 1) mod 65536                 (0x4c7267..0x4c7296)
```

`L = payload_len - 1` (0x4c710b..0x4c7143). Payload bytes are little-endian
for multi-byte wrappers (usbWriteWord FUN_004ca84e, usbWriteDword FUN_004caab4,
usbWrite40 FUN_004cad4a all use the same `& 0xFF` / `/ 256` byte-split idiom).

### 1.3 C2 read (FUN_004c6aa3)

```
send:  FT_Write(FB+1, 5)   ->  C2 r_lo r_hi (N-1)_lo (N-1)_hi   (0x4c6c99)
wait:  until RxQueue >= 3+N, timeout 2000 ms                    (0x4c6cbf)
read:  FT_Read(3+N)                                             (0x4c6cfb)
parse: FUN_004cd527; ctr++
```

The frame length field carries `N-1` (0x4c6c0c `fsubl 0x401f18` = minus 1.0).
`N` is the number of data bytes the caller wants: usbReadByte FUN_004cb0c5
passes N=1 (`fld1`, 0x4cb139); usbReadWord FUN_004cb534 N=2; usbReadDword
FUN_004cb2c8 N=4 (`fldl 0x4026f8` = 4.0, 0x4cb33c).

Reply total on the wire = `frame_field + 4` = `3 + N`. Data bytes are reply
bytes `3 .. 3+N-1`. Values are big-endian: usbReadByte takes reply[3]
(0x4cb171, `movzbw (%esi,%eax)` with esi=3); usbReadDword computes
`reply[6]*256^3 + reply[5]*256^2 + reply[4]*256 + reply[3]` (loop at
0x4cb378..0x4cb3dc, esi runs 6,5,4,3 with `fmull 0x4028f0` = 256.0).

### 1.4 C3 page select (FUN_004c74b0)

Before the frame, both senders compare `sel` against the cached page global
`0x5977e8` (0x4c707c..0x4c7094). On change, C3 is sent first:

```
send:  C3 sel_lo sel_hi 00 00     (bytes from Int(double/65536), 0x4c754f..0x4c759e)
wait/read/parse/ctr++ like C1
cache: 0x5977e8 = sel             (0x4c76c0..0x4c76c7)
```

The cache starts at -1 (FUN_004c5a6d, 0x4c5ad8), so the first register command
after device open always begins with a C3. On error the C3 path falls into the
resync flush FUN_004c78c9 (0x4c773d).

### 1.5 The counter handshake (FUN_004cd527)

- Reply byte 0 must echo the opcode (variant/string compare, 0x4cd5a0..0x4cd5f1).
- Reply byte 1 must equal `Int(counter / 256)` (0x4cd615..0x4cd680).
- Reply byte 2 must equal `counter & 0xFF` (0x4cd68a..0x4cd6a5).
- The host counter `0x5977e0` is a double (!), incremented after every
  C1/C2/C3 success, and **zeroed at the end of the resync flush**
  (`fldz; fstpl 0x5977e0` at 0x4c7f16). The device counter restarting at zero
  on FPGA reconfiguration (wire fact, 2026-10-28) is the mirror image: the app
  re-runs config + resync, which resets its side to zero.

This explains the observed `C1 00 37` / `C2 00 19 ...` replies byte-for-byte
(`00 37` = counter 0x0037 big-endian) and why one counter covers reads and
writes. The "status byte" that seemed to advance with every command in the
2026-10-26 session was the counter echo read from the wrong page without a
page select.

## 2. The frequency-counter read (FUN_00504778, the application timer)

The whole feature lives in one timer function (VA 0x504778..0x50558b). Local
`-0x3c(%ebp)` is the VB6 line number; the line numbers below mark the order.

Per tick, gated to `|now - last| > 75` ms (`0x4025a0 = 75.0`, compare at
0x5048e2; tick source FUN_004c471c, last-poll slot `+0x40` of the state object
at 0x597800):

1. **One-shot** (flag `0x5963d6` != 0, line 7): `usbWriteByte(0x100002, 1)`
   then clear the flag (0x504856..0x50489e). The dispatcher sets this flag
   whenever config changes.
2. **Running-max read** (line 0xe): `usbReadByte(0x40000005, &max)` (0x504915).
   If greater than the cached word `0x5974d0`, update it (0x504932).
3. **Gate read** (line 0x10): `usbReadByte(0x40000000, &gate)` (0x50494c).
   **If gate != 0, the iteration skips select+count entirely** (0x504966,
   `jne 0x504b27` jumps to the status section). Gate == 0 means count ready.
4. **Select write** (lines 0x12..0x14): `next = (channel + 1) mod 4`
   (0x50498c..0x5049a2); the written byte is element `next` of the module byte
   array at `[0x5962b4]` (`__vbaAryLock` 0x5049b6, `__vbaDerefAry1` 0x5049c6,
   then `usbWriteByte(0x40000000, ...)` at 0x5049d1). NOTE: `[0x5962b4]` is
   the same array the trigger sequencer reads per-channel term states from
   (34 byte elements, values 0..3), see §5 open items; with a sane default
   config the effective value is the next channel number.
5. **Channel-0 fallback** (lines 0x15/0x16): if UI flag `0x5971c8` is set and
   the channel being read is 0, the display value for slot 0 becomes the
   running max from step 2 (0x5049f9..0x504a2b), the "running maximum
   fallback for channel 0" of the record.
6. **Count read** (lines 0x17/0x18): `usbReadDword(0x40000001, &freq[ch])`
   (0x504a4d..0x504a52), big-endian count, then `freq[ch] = count * 10`
   (`fmull 0x401568` = 10.0, 0x504a90). The read is for the OLD channel;
   the select in step 4 targeted the next one. This is the one-iteration lag.
7. **Advance** (line 0x19): `channel = next` (0x504aac).

Exact wire sequence for one iteration on the frequency page (sel = 0x4000),
starting from counter value `c`, assuming the page is already selected:

```
C2 05 00 00 00            ->  C2 <ctr_hi> <ctr_lo=c>  <max>            (4 bytes)
C2 00 00 00 00            ->  C2 <ctr_hi> <ctr_lo=c+1> <gate>          (4 bytes, must be 00)
C1 00 00 00 00 <sel>      ->  C1 <ctr_hi> <ctr_lo=c+2>                 (3 bytes)
C2 01 00 03 00            ->  C2 <ctr_hi> <ctr_lo=c+3> <b3> <b2> <b1> <b0>   (7 bytes)
count = b3<<24 | b2<<16 | b1<<8 | b0 ;  freq_hz = count * 10
```

Entering the page from any other page (or as the first command after open,
when the cache is -1) prepends `C3 00 40 00 00` with its own 3-byte ACK and
its own counter increment.

## 3. StartAcquisition (FUN_004f2179, the settings dispatcher)

Called from FUN_004f1cdb (`calll 0x4f2179` at 0x4f1ce2), then one synchronous
timer tick (0x4f1cf3). The dispatcher walks dirty flags in a fixed order; with
everything dirty (a fresh start) the wire order is:

| # | Flag | Wire (page via C3 on change; r = reg & 0xFFFF) | Evidence |
|---|------|-----------------------------------------------|----------|
| 1 | 0x5963c8 (and 0x5962fc==1) | C1 0x20000000 ← mode byte (clock-table elem+0xC) | 0x4f2255 |
| 2 | 〃 | C1 0x20000001 ← qualifier byte (elem+0x10) | 0x4f228e |
| 3 | 0x5963ca | C1 0x1000000 ← clock byte (module var 0x596308) | 0x4f2393 |
| 4 | 〃 | C1 0x1000002 ← 0x00 (disarm trigger) | 0x4f241b |
| 5 | 〃 | C1 0x1000003 ← mask A, 5 bytes LE (packed MSB-first from term array [0x5962a4], loop 33..0) | 0x4f2499 |
| 6 | 〃 | C1 0x1000008 ← mask B, 5 bytes LE (array [0x5962a8]) | 0x4f255a |
| 7 | 〃 | C1 0x1000002 ← arm flag byte (word 0x5963f4), rearm | 0x4f25cc |
| 8 | 〃 | C1 0x1000001 ← byte (word 0x5974bc) | 0x4f2659 |
| 9 | 0x5963cc | C1 0x100003 ← 0x00 (disarm ARM register) | 0x4f26c3 |
| 10 | 〃 | C1 0x100002 ← 0x01 | 0x4f26eb |
| 11 | 〃 | sequencer segment 0: FUN_004f2dd4(0), ~15 writes to 0x200000+{0,5,10,14,22,...} | 0x4f270e |
| 12 | 〃 | sequencer segment 1: FUN_004f2dd4(1), same offsets on 0x600000 | 0x4f2729 |
| 13 | 〃 | C1 0x10000D ← byte (0x5963ec) | 0x4f2741 |
| 14 | 0x5963ce | C1 0x100008 ← threshold DAC word, 2 bytes LE (the record's volts formula; round) | 0x4f28a2 |
| 15 | 0x5963d0 | C1 0x100004 ← trigger-position word A, 2 bytes LE (offsets +7/-7, /clock, +35/-30 per mode) | 0x4f2bee |
| 16 | 〃 | C1 0x100006 ← word B | 0x4f2c05 |
| 17 | 0x5963d2 (set by step 9's block) | **C1 0x100003 ← arm byte (0x597488: 0 single-shot, 1 repetitive). THE ARM** | 0x4f2c39 |

Notes:

- The mask packing loop (0x4f2436..0x4f248c) starts at 0.0, and for channel
  i from 33 down to 0 does `mask <<= 1; if (term[i] != 0) mask |= 1`
  MSB-first over 34 channels, matching the record's §5a description.
- The trigger-position math (0x4f2913..0x4f2b78) matches the record's
  branches on mode flag 0x5962FC and clock type exactly (switch at
  0x4f295f..0x4f2969 on clock-table elem+8).
- The threshold write destination 0x100008 independently confirms the slot
  the earlier decode lane pinned in protocol.py.
- The FPGA-config epilogue (FUN_004c8541 tail) validates the device with
  `usbReadByte(0x10000F)` compared against `expected | 0x10` (0x4c899f..0x4c8c1),
  optionally reads a calibration word from 0x10000D (0x4c89e5), and on
  mismatch writes `C1 0x100000 ← 0x00` (0x4c8a3b), this is the "0xC1 rate
  command" of record §5 step 13; its payload byte is **0**.

### Capture lifecycle (timer, 100 ms cadence: `0x402c80 = 100.0`)

At capture complete (FUN_00504778 lines 0x57..0x63):

1. `C1 0x100003 ← 0x00` (disarm; 0x5052b4 register arg 0x596ae8)
2. `C1 0x100002 ← 0x01` (0x5052e4)
3. `usbReadByte(0x100000, &status)` (0x505322)
4. `status & 0x01` → done, skip re-arm (0x50533c), the record §5c bit 0 has
   a real static basis; it failed on hardware only because the probes read the
   wrong page with no C3 and saw the counter echo instead.
5. Else `status & 0x40` → `C1 0x10000C ← 1` (0x5053af);
   `status & 0x20` → `C1 0x10000B ← 1` (0x5053d3);
   else `C1 0x10000A ← 1` (0x505370), the recurring re-arm registers.
6. Sample drain: GetSamplesDDR FUN_004ed082 (0x504fd7) / GetSamplesSDR
   FUN_004ef26e (0x504fe9).

## 4. Corrected register map

`reg = page*65536 + offset`; C3 selects the page, the frame carries the offset.

| Page (sel) | Base | Offsets in use (this decode) |
|---|---|---|
| 0x10 | 0x100000 | 0 status, 1 wire-status, 2 capture-complete, 3 **ARM**, 4/6 trigger-position words, 8 threshold DAC, 0xA trigpos-read/re-arm, 0xB/0xC re-arm, 0D, 0F hardware validation |
| 0x20 | 0x200000 | sequencer segment 0 (0, 5, 10, 14, 22, ...) |
| 0x60 | 0x600000 | sequencer segment 1 (same offsets) |
| 0x100 | 0x1000000 | 0 clock byte, 1 aux byte, 2 trigger-enable/disarm, 3 mask A, 8 mask B |
| 0x200 | 0x02000000 | sample planes (0x101, 0x1000..0x9000 ladder) |
| 0x4000 | 0x40000000 | 0 freq select (write) / ready gate (read), 1 count dword, 5 running max byte |
| 0x8000 | 0x20000000 | 0 sample-mode byte, 1 qualifier byte |

## 5. Divergences from the plugin model (protocol.py) and the record

1. `wire_address(reg) = reg & 0xFFFF`, right for the offset, but the model
   has **no page select at all**. The low-16 collisions the docstring flags
   (0x100000 vs 0x40000000 both → 0x0000) are real and resolved by pages,
   not by the offset alone. Every register operation needs the C3-first rule
   with a cached page.
2. `pack_rate_word` / `RATE_SELECT_SHIFT`, mis-model. Nothing packs a rate
   into the frame; the upper 16 bits are the page and travel only in C3.
   The record's §3.1 "clock word" reading came from this same code viewed as
   a rate; the clock/rate itself reaches the device as payload bytes
   (0x1000000 clock byte, 0x20000000/1 mode+qualifier), never as a frame
   field.
3. `frame_c2(word, n)`, the app puts `N-1` in the length field (N = wanted
   data bytes). protocol.py sends `n` directly; a caller asking for 4 must
   send 3 to mirror the app.
4. `parse_reply` / `READ_REPLY_HEADER_BYTES = 4`, wrong boundary. The header
   is 3 bytes (`echo, ctr_hi, ctr_lo`); there is no aux byte; data is bytes
   3..3+N-1. The record's two competing interpretations (§10 /
   `parse_read_reply`) both dissolve: the app's own helpers read from byte 3.
5. `AUX_SEMANTICS`, the byte is data byte 0 of the register read. Closed.
6. The resync flush (§3.4), sequence confirmed, plus the new fact that it
   zeroes the host command counter at its end (0x4c7f16).
7. Frequency model, registers, gate==0 semantics, ×10 factor, 4-channel
   one-iteration-lag round robin, and the >75 ms cadence all confirmed
   exactly (0x4025a0 = 75.0 at 0x5048e2; ×10 at 0x504a90; lag at
   0x50498c..0x504ab4). The 100 ms floor in protocol.py is safe.
8. The status byte (§5c), the bit decode has a real static basis (bits 0,
   0x20, 0x40 tested at 0x50533c/0x50534d/0x50538c). The retraction stands
   for the *wire* evidence it was based on, but the reason the probes saw a
   counter there is now explained: wrong page, no C3.
9. `freq_probe.c` (the hardware session's reconstruction), four bugs against
   this decode: no page C3; address bytes big-endian in the frame field (the
   app is little-endian offset); 7-byte C1 with two payload bytes (app: one);
   `ftdi_tcioflush` before every write (the app purges only at open/resync
   the flush can eat an in-flight ACK; the filler-drain should happen before
   the parse, not by purging the queues).

## 6. Open items (honest limits)

1. **The freq select byte value.** The code writes byte-array element
   `[(N+1) mod 4]` where the array is the module term-state array
   `[0x5962b4]` (34 byte elements; the sequencer reads the same array).
   The intended runtime values for elements 0..3 are not statically pinned
   with a default trigger config they plausibly encode the channel number.
   The probe takes the select value as a parameter, default `(N+1) & 3`,
   and the hardware run should try the other three values if the gate never
   opens.
2. **The clock byte at 0x1000000** comes from module var 0x596308, which no
   compiled store writes directly (it is written through a structure copy);
   its value is not pinned. It does not affect the frequency-counter path.
3. **Sequencer write order** inside FUN_004f2dd4 was not re-walked this pass;
   the record's 15-row table stands. The ladder init (FUN_00471af1) confirms
   the segment offsets {0, 5, 10, 14, 22, ...}.
4. **The EE state.** Strongly correlated with commands sent without a page
   select / with a stale counter; not proven. The probe discriminates: after
   a clean `C3 00 40` the gate read should answer a well-formed `C2` frame
   with the counter echoing our own count.

## 7. The arming model (second decode pass, 2026-10-28 late: landed in record section 5d)

Two selectors. **Capture mode** `0x596730` (UI combo/menu; `0x52080f`,
`0x568361`): 1 = run variant, 2 = Single Acquisition (menu string
`mnuSingleAcquisition_Click` VA 0x436dec), 4 = stopped (startup default
0x47c8e4), 5 = transitional, **8 = Trigger Immediate, the free-run mode**
(menu string `mnuTriggerImmediate_Click` VA 0x436fd0, handler FUN_00568209
sets mode 8 at 0x568361). **Arm byte** `0x597488` refreshed on every mode
change by FUN_0047e7cf (0x47e831..0x47e866): modes 1/2 -> 1, 4 -> 0, 5/8
unchanged, so `0x100003 <- 1` = single capture, `<- 0` = continuous/stop.
**Trigger enable** = word `0x5963f4` ("trigger terms present", indirectly
written): when 0, mask A is SKIPPED (0x4f2429 branch), the trigger-enable
register gets the flag value (`0x1000002 <- 0`), and the timer's
phase-advance timeout writes (modes 5/7) do not fire, the device free-cycles.

**Acquisition state machine** `0x597460` (set via messages FUN_00482fcb
0x4830fa; strings in the jump table at 0x48347a): 1 Searching, 2
Initializing, 4 Ready, 5 Pre-filling, 6 "Prefill complete, Waiting for
trigger", 7 "Triggered, Post-filling", 8 "New acquisition completed", 9
Software Demo. Status register 0x100000 phase bits statically CONFIRM the
retracted section 5c decode: 0x00 pre-fill, 0x40 waiting, 0x60 triggered,
bit 0 complete (0x504d4a..0x504e3c, 0x50533c..0x5053b6), the wire test
that retracted it read the wrong page.

**Free-run sequence** = the 17-step chain with `0x1000002 <- 00`
(trigger disabled; mask A skipped), arm `0x100003 <- 00`; completion
(mode 8 -> flag 0x5963d4 at 0x504dea) runs the auto-restart: disarm
0x100003<-0, 0x100002<-1, status read, phase advance (0x10000C<-1 if
waiting, 0x10000B<-1 if triggered, else 0x10000A<-1). **Valid vs garbage:**
a triggered capture holds at 0x40 before completing; free-run never waits.
Timeouts during triggered acquisition force-advance: mode 5 ->
0x10000A<-1, mode 7 -> 0x10000B<-1 (intervals 0x596ebc/0x596eb8).

## 8. The clock table (FUN_0047480e = `Main.bas->DefineClockSettings`, string VA 0x4253f4)

Built once at startup (guarded by `0x596ef0 == 1`): a 20-element array
(Redim upper bound `0x13` at 0x4748f7), element size **0x14 bytes**, selected
by the index global `0x59678c` (written by the rate UI). Element layout:

| Offset | Type | Meaning | Goes to the wire? |
|---|---|---|---|
| +0x00 | double | sample PERIOD in seconds | NO, display math only (cached at 0x596488) |
| +0x08 | int32 | clock type | NO, host-side: trigger-position branch (0x4f295f), DDR/SDR reader choice (0x504eed) |
| +0x0C | int32 | mode byte | **C1 0x20000000** (dispatcher 0x4f225a) |
| +0x10 | int32 | qualifier byte | **C1 0x20000001** (dispatcher 0x4f228e) |

Row 19's tail loop (0x474dc2..0x474e01) overwrites `+8 = 1` for every index
2..19, so only rows 0 and 1 keep their explicit types (4 and 2).

| idx | period | frequency | type | mode | qual | reader |
|---|---|---|---|---|---|---|
| 0 | 1 ns | 1 GHz (500 MHz DDR ×2) | 4 | 0x00 | 0x00 | special (COM path) |
| 1 | 2 ns | 500 MHz | 2 | 0x00 | 0x00 | GetSamplesDDR |
| 2 | 4 ns | 250 MHz | 1 | 0x00 | 0x00 | GetSamplesSDR |
| 3 | 5 ns | 200 MHz | 1 | 0x01 | 0x00 | SDR |
| 4 | 10 ns | 100 MHz | 1 | 0x01 | 0x00 | SDR |
| 5 | 20 ns | 50 MHz | 1 | 0x01 | 0x01 | SDR |
| 6 | 50 ns | 20 MHz | 1 | 0x01 | 0x04 | SDR |
| 7 | 100 ns | 10 MHz | 1 | 0x21 | 0x00 | SDR |
| 8 | 200 ns | 5 MHz | 1 | 0x21 | 0x01 | SDR |
| 9 | 500 ns | 2 MHz | 1 | 0x21 | 0x04 | SDR |
| 10 | 1 µs | 1 MHz | 1 | 0x31 | 0x00 | SDR |
| 11 | 2 µs | 500 kHz | 1 | 0x31 | 0x01 | SDR |
| 12 | 5 µs | 200 kHz | 1 | 0x31 | 0x04 | SDR |
| 13 | 10 µs | 100 kHz | 1 | 0x41 | 0x00 | SDR |
| 14 | 20 µs | 50 kHz | 1 | 0x41 | 0x01 | SDR |
| 15 | 50 µs | 20 kHz | 1 | 0x41 | 0x04 | SDR |
| 16 | 100 µs | 10 kHz | 1 | 0x51 | 0x00 | SDR |
| 17 | 200 µs | 5 kHz | 1 | 0x51 | 0x01 | SDR |
| 18 | 500 µs | 2 kHz | 1 | 0x51 | 0x04 | SDR |
| 19 | 1 ms | 1 kHz | 1 | 0x61 | 0x00 | SDR |

The mode byte's nibbles read as a prescaler-tap select (high nibble 0..6,
stepping one decade per group of three rows) with the low nibble/qualifier
pair {0,1,4} completing the 1-2-5 subdivision; the exact device semantics of
each nibble stay undecoded, the VALUES are what the app sends.

### The clock byte at 0x1000000 carries the row index

The third wire byte comes from word `0x596308` (dispatcher 0x4f2332-0x4f2393,
byref VT_I2 -> CByte). `0x596308` has no compiled writer (a registry-loaded
settings field, the neighboring keys `StateClockDelay/Polarity/Select/Rate/
Units`, VAs 0x423e54..0x423ef0, name the cluster). Proof that it carries the
row discrimination: **rows 3 and 4 have identical (type, mode, qual) = (1,
0x01, 0x00)** yet are different sample rates (200 vs 100 MHz), no other
table field or wire write distinguishes them, so the 0x1000000 byte must.
Reading it as the row index (0..19) fits the byte width and the
`StateClockSelect` name. HYPOTHESIS, high confidence, one probe observation
from proof.

Consequence for the stalled free-run session: writing clock bytes 0..7 with
mode=0/qual=0 sends rows 3..7 with the WRONG mode bytes (rows 3-6 need mode
0x01, row 7 needs 0x21). The device stalls in pre-fill because the
(mode,qual,index) triple is internally inconsistent. Send all three from one
row of the table above.

### No rate-divisor register exists

Element = exactly 0x14 bytes / 4 fields (above). Only +0xC and +0x10 reach
the wire (0x20000000/0x20000001), plus the index byte at 0x1000000. The rate
double is never transmitted, the 20 discrete rates are fully encoded by the
triple. The other table readers are host-side: 0x4da5cd (variant math on
type/rate), 0x4ee771 (GetSamplesDDR), 0x4f295c (trigger-position offsets).
The dispatcher's §5a formula byte (-0xec at 0x4f2312) is a dead store in
that pass, never consumed.

## 9. The sequencer segment wire table (FUN_004f2dd4) + CHM cross-check (2026-10-28 late)

FUN_004f2dd4(segment) writes one segment. The register doubles come from a
contiguous 16-entry array: **`0x596c50 + segment*0x80`** (seg0 base
0x596c50 = 2^21 = 0x200000; seg1 base 0x596cd0 = 2^22 = **0x400000**, see
the divergence note). Each entry i holds base+offset_i. The 15 writes, in
call order, with their payload sources (segment struct = array `[0x5963f0]`,
element per segment):

| # | Register (seg0) | off | size | Payload | Gate (0x4f2dd4 VA) |
|---|---|---|---|---|---|
| 1 | 0x200012 | 0x12 | byte | term-type, from a selector table via struct+0x50 (0x4f33f7..0x4f3410) | struct+0x46 (segment enable) != 0, 0x4f2efd |
| 2 | 0x20001A | 0x1A | 40-bit LE | mask A accumulator (33..0 MSB-first pack) | enable |
| 3 | 0x20001F | 0x1F | 40-bit LE | mask B accumulator | enable |
| 4 | 0x200024 | 0x24 | 40-bit LE | dwell/duration value 1 | enable |
| 5 | 0x200000 | 0x00 | 40-bit LE | condition mask A | struct+0x00 != 0 (terms present), 0x4f34a4 |
| 6 | 0x200005 | 0x05 | 40-bit LE | condition mask B | terms present |
| 7 | 0x200029 | 0x29 | 40-bit LE | state mask A | state-terms path (loop 0x4f36c6..0x4f3858) |
| 8 | 0x20002E | 0x2E | 40-bit LE | state mask B | state terms |
| 9 | 0x200018 | 0x18 | byte | struct+0x6C (control), byref variant (0x4f3902) | always |
| 10 | 0x200019 | 0x19 | byte | boolean-derived byte (VT_BOOL variant, 0x4f3aa6) | always |
| 11 | 0x20000E | 0x0E | dword LE | struct+0x38 (count/duration, 20-bit range) | always (0x4f3b42) |
| 12 | 0x20000A | 0x0A | dword LE | struct+0x20 (count/duration) | always (0x4f3b71) |
| 13 | 0x200014 | 0x14 | byte | computed state-terms flag (local -0x6c, VT_UI1 variant 0x4f3bcb) | always |
| 14 | 0x200016 | 0x16 | byte | struct+0x69 (0x4f3c84) | always |
| 15 | 0x200017 | 0x17 | byte | struct+0x6A (0x4f3ccf) | always |

Segment 1: same 15 registers on base **0x400000** (page sel **0x40**, vs
seg0's 0x20). The offsets ladder (FUN_00471af1, globals 0x596c50..0x596cc8 =
idx 0..15): {0, 0, 5, 10, 14, 20, 22, 23, 24, 25, 18, 26, 31, 36, 41, 46}.

**Divergence from the record:** section 5a/5b and protocol.py's
`SEQUENCER_SEGMENT_BASES` say segment 1 = 0x600000. The binary says 2^22 =
0x400000 (PowerR8(2,22) at 0x472259..0x472274; the seg1 array 0x596cd0..d48
is built as 0x400000 + seg0 offsets at 0x4722a1..0x472493; and
`0x4f2ecf: edi = 0x596c50 + segment*0x80` makes the arrays contiguous, so
seg1 = 0x596cd0 = the 2^22 base). 0x600000 appears nowhere.

### The minimal sequencer config (all terms disabled: vendor-legal)

The CHM "Trigger Setup" page states: **"If no terms are selected, the
trigger level is unconditionally satisfied."** So the least-content legal
segment = disabled/term-less: writes #1-8 are gated off (enable and
terms-present checks fail) and only the seven ungated writes go out, with
the zeroed struct's field values. Exact wire for segment 0 (page select
`C3 20 00 00 00` first if the page differs), from counter c:

```
C1 18 00 00 00 00                     #9  control  (+0x18) <- struct+0x6C
C1 19 00 00 00 00                     #10 match    (+0x19)
C1 0E 00 00 03 00 00 00 00 00         #11 dword    (+0x0E) <- struct+0x38
C1 0A 00 00 03 00 00 00 00 00         #12 dword    (+0x0A) <- struct+0x20
C1 14 00 00 00 00                     #13 flag     (+0x14)
C1 16 00 00 00 00                     #14 match-and(+0x16) <- struct+0x69
C1 17 00 00 00 00                     #15 control2 (+0x17) <- struct+0x6A
```

then the same seven on page 0x40 for segment 1. CAVEAT: the CHM documents
the event-count range as 1 to 1,048,576 and the app "corrects" out-of-range
values on Apply, the struct DEFAULTS for the two dwords are UI-state this
lane could not pin; if dword 0 stalls the device, send **1** (the CH-legal
minimum) in both. The remaining byte fields' defaults are likewise unpinned;
zero is the zeroed-struct reading.

### CHM cross-check (chm_out/html/, extracted 2026-10-28)

Reconciled against `Trigger Setup.htm`, `Sample Mode Setup.htm`,
`Specifications.htm`:

- **Specifications**, "Timing mode sample rate: 1KHz to 500MHz, standard
  1-2-5 sequence" = the 20-row clock table (row 0's 1 ns period = the 500 MHz
  double-rate setting). "Trigger event counters: 20 bits (range 1 to
  1,048,576) for each trigger level" = the two dword writes #11/#12. "Trigger
  sequencer: 250MHz max" (the record's 4-level claim: the CHM describes TWO
  trigger levels A/B with six logical combinations, "Immediately / A / B /
  A-or-B / A-and-B any order / A then B"; the segment-struct selector +0x54
  indexes this six-way list. The record's "four-level sequencer" phrasing
  diverges from the vendor's two-level description; the wire still carries
  two segments). "Frequency counter range: 300MHz with 10Hz resolution,
  4 channels" = the ×10 Hz scaling and the 4-slot round robin.
- **Trigger Setup**, the term types Edge/Pattern/Value/Duration map to the
  record's term-type byte (write #1) and the mask/dwell writes; "Consecutive
  vs Cumulative" and "Prequalify Pattern/Value Terms" are boolean settings
  riding in the control bytes (#9/#13/#14/#15); duration "1 to 1,048,575
  samples" = 20-bit, matching the dwords.
- **Sample Mode Setup. Trigger Immediate**, the vendor describes it as a
  **phase-advance function**, not a capture mode: during Pre-Fill it exits
  to the Trigger phase; during Trigger it forces an immediate trigger; during
  Post-Fill it stops sampling and completes. This maps one-to-one onto
  the three advance registers `0x10000A` (from pre-fill), `0x10000B` (force
  trigger), `0x10000C` (stop) written by the app's timer/click paths,
  and onto the menu mode 8 auto-loop (free-run) decoded in section 7. The
  three-phase sample sequence (Pre-Fill → Trigger → Post-Fill) is exactly
  the app's states 5/6/7 and the status byte's 0x00/0x40/0x60 phase bits.
- **No divergence** between the CHM and any wire fact in this lane except
  the record's own two items: the segment-1 base (above) and the
  "four-level" phrasing.

## 10. The DDR drain decoded (FUN_004ed082): corrects the section 6 plane map

The principal's priority surface: the raw channel capture. Full drain
sequence, VA-anchored (FUN_004ed082; bulk reader FUN_004cd218(regPtr, dest,
count) wraps the C2 sender at 0x4cd2bf):

1. `usbReadWord(0x02000000)` -> geom1; **geom1 %= 0x400** (0x4ed2c3..0x4ed2f0).
2. `usbReadWord(0x02000100)` -> geom2; **geom2 %= 0x400** (0x4ed307..0x4ed32f).
3. `usbReadWord(0x10000A)` -> tp, the trigger-position word (0x4ed35b).
4. tp_comp = variant math on `tp + 1 - 0x400` (0x4ed396..0x4ed401), the
   record's prescale, effectively (tp+1) mod 0x400.
5. N = max(0x7FF - tp_comp, 1) (0x4ed42e..0x4ed455, global 0x596e38);
   chunk_len = (N + 1) / 2 (0x4ed5b5..0x4ed5fc).
6. **pageA_base = geom1 + 1 + tp_comp** (0x4ed49e..0x4ed4af, local -0xf8);
   **pageB_base = geom2 + 1 + tp_comp** (0x4ed507..0x4ed524, local -0x118).
7. Ten bulk reads, each register = page base + a plane-offset global
   (ladder at 0x471c37..0x471dae: base 0x02000000 + {0, 1, 0x100, 0x101,
   0x1000, 0x2000, 0x3000, 0x4000, 0x5000, 0x6000, 0x7000, 0x8000, 0x9000}):

| # | page | offset global | plane |
|---|---|---|---|
| 1-4 | A (geom1) | +0x1000 / +0x2000 / +0x3000 / +0x4000 | ch0-7 / ch8-15 / ch16-23 / ch24-31 |
| 5 | A | +0x9000 | flags |
| 6-9 | B (geom2) | +0x5000 / +0x6000 / +0x7000 / +0x8000 | ch0-7 / ch8-15 / ch16-23 / ch24-31 |
| 10 | B | +0x9000 | flags |

   (call sites 0x4ed6e6, 0x4ed73a, 0x4ed78e, 0x4ed7e2, 0x4ed833 on base A;
   0x4ed887, 0x4ed8db, 0x4ed92c, 0x4ed980, 0x4ed9d1 on base B; each
   `fildl base; faddl plane_global; fstpl; pushl chunk_len; calll 0x4cd218`.)
8. `usbReadWord(0x1000000)` -> trigger word (0x4ee72c; register global
   0x596bd8 = 0x1000000+0).
9. `usbReadByte(0x10000C)` -> status byte (0x4ee74e; register global
   0x596b50 = 0x100000+0xC).

### Corrections against the record and protocol.py

1. **The section 5b plane map is wrong.** It reads "+0x2000101 ch0-7,
   +0x2002000 ch8-15, +0x2003000, +0x2004000, +0x2009000 flags". Actual:
   page A's channel planes are +0x1000..+0x4000; page B's are
   +0x5000..+0x8000; flags +0x9000 for both; and +0x100/+0x101 are the
   GEOMETRY WORD registers (0x02000100 read at step 2), not sample planes.
2. **PAGE_STRIDE = 0x400 is not the model.** The app never computes the
   second page as page-one-plus-stride. It reads two independent geometry
   words (0x02000000 and 0x02000100, each mod 0x400) AND uses entirely
   different plane offsets for page B. protocol.py's
   `sample_plane_wire_address(plane, page_base + PAGE_STRIDE)` produces
   wrong registers for every page-B chunk even if the two geometry words
   happen to sit 0x400 apart; the fix is the two-geometry-word model with
   per-page plane offsets.
3. Confirmed as-is: trigger-position read 0x10000A, N = 0x7FF - tp_comp,
   chunk length (N+1)/2, trigger word 0x1000000, and the record's de-
   interleave/RLE story (the decode loops are host-side past the reads).

The SDR reader (FUN_004ef26e) also reads the +0x9000 flags plane
(0x4ef637, 0x4ef950), consistent with the flags plane being shared.

## 11. Why the 2026-10-28 session wedged (mechanism, high confidence)

The session's sequence was config → resync → select write → gate poll → count
read, all without a page select. Under this decode the select write went to
offset 0 of whatever page the device was in after the resync (not 0x40000000),
with a payload byte the device's default page does not expect, and the reply
counter expectations (if any were checked) could never line up. The EE reply
is the device's rejection dialect. The fix is not more patience at the gate;
it is `C3 00 40 00 00` (page 0x4000) before the first frequency register
access, with the counter handshake honored from zero.

## 12. The missing post-validation initialization: `C1 0x100000 <- 0x61`

The hardware session's stall decoded. Between the validation read and the
settings dispatcher, the app sends a ONE-SHOT initialization command that no
reconstruction has ever sent, and without it the register file stays dead
(the observed FF reads, the threshold write not sticking).

**The write: `usbWriteByte(0x100000, 0x61)`**, two sites, one latch:

1. Device-open path, FUN_004c47de: gated on an argument flag and the latch
   `0x596da4 == 0` (0x4c5340..0x4c5368); sets the latch after (0x4c536d).
   The latch is CLEARED at every device open (0x4c4c5c) and on the
   device-change/shutdown path (0x47b67b), so 0x61 goes out once per device
   session.
2. FPGA-config epilogue, FUN_004c8541 success path: after the validation
   read passes, resync flush (0x4ca1cf) -> wait 0.5 s (0x4ca219, const
   0x4029c0 = 0.5) -> FT_Close (0x4ca0a5) -> REOPEN via FUN_004c5c6b with
   5000 ms timeout (0x4ca2a4, `movl $0x1388` at 0x4ca260) -> latch check
   (0x4ca2ef) -> **`C1 0x100000 <- 0x61`** (0x4ca2fc..0x4ca317) -> latch
   set. The MISMATCH path instead writes `0x100000 <- 0`, which the
   hardware session proved soft-resets the command layer.

`0x61` = 0b01100001, the bit semantics are undecoded, but this is the
only write between validation and the dispatcher; it is the input-
conditioning/sample-engine enable the session was missing. Answers to the
three asks: (a) YES, the successful-validation write to 0x100000 exists,
value 0x61 (the 0x00 write is the failure path); (b) no other conditioning
or DAC-enable write exists between validation and the dispatcher, 0x61 is
it; (c) the startup chain: app start builds the register ladders and clock
table (FUN_00471af1, FUN_0047480e), the user action enters FUN_004f1453
which runs FUN_004c47de (open/init, may send 0x61), then FUN_004c8541
(FPGA load -> validate -> resync -> reopen -> 0x61), then FUN_004f2179
(dispatcher), then the first FUN_00504778 timer tick (0x4f1ce2, 0x4f1cf3).

## 13. Between ARM and the first plane read (2026-10-27, the four asks)

**(1) The timer's first tick after StartAcquisition.** Nothing enables a
sample path. The tick (FUN_00504778) runs, in order: the one-shot
`0x5963d6` flag, which the dispatcher SETS during every config send, 
fires `C1 0x100002 <- 1` (0x504870); the frequency block (75 ms cadence,
side surface); then the 100 ms acquisition block (504cff+): mode-5/7
timeout-advance checks, and for any mode != 4 the status read `C2 0x100000`
with the phase-bit dispatch (bit0 complete -> drain; 0x40 waiting; 0x20
triggered; else pre-fill, state messages only). No sample-clock write, no
FTDI change.

**(2) The drain's first operations.** FUN_004ed082 opens with pure VB6
string/array setup (0x4ed082..0x4ed2c0), no register writes, no FTDI
calls. Its first wire ops are the geometry/position READS. Same for the
SDR reader. No "unlock" write exists in either reader.

**(3) FTDI/D2XX near the acquisition path, nothing.** The full FTDI
surface (name strings at 0x42437c..0x424a14): ListDevices, ResetDevice,
Open, OpenEx, Close, Read, Write, SetBaudRate, Purge, GetStatus,
SetTimeouts, ResetPort, the five EEPROM calls, Get/SetLatencyTimer,
Set/GetBitMode. **FT_SetUSBParameters and FT_SetFlowControl do not exist
in the binary.** Every parameter call lives at open/config time only:
SetTimeouts 0x4c64f4, SetBaudRate 0x4c6520, SetLatencyTimer 0x4c654c (all
inside FUN_004c5c6b); SetBitMode only in FUN_004c8541's bit-bang load
(0x4c8b8d, 0x4c8c43) and its return-to-normal-mode reset (0x4ca0a5, this
is a SetBitMode(handle,0,0), NOT an FT_Close as first read); FT_ResetDevice
via FUN_004c687f (0x4c68e7) from the FPGA epilogue; FT_Purge only in the
resync flush and the FPGA epilogue. Nothing between ARM and drain.

**(4) Writes to 0x100000/0x100001/0x100002 after ARM.** 0x100001 is never
written by the app (wire-status is read-only). 0x100000's only writers are
the two one-shot 0x61 sites and the mismatch 0x00 (section 12). 0x100002
is written by the tick one-shot (0x504870, <- 1) and at completion
(0x5052e9, <- 1). That is all.

### The real suspect: at rate rows 2-19 the app drains through GetSamplesSDR, not the DDR reader

The reader choice (timer 0x504ec5..0x504f00): `0x59660c == 1` and the
clock-table type, **type 2 (row 1, 500 MHz) -> GetSamplesDDR; type 1
(rows 2..19) -> GetSamplesSDR (FUN_004ef26e); type 4 (row 0) -> a display
path.** The hardware session ran row 10 = type 1 = SDR. FUN_004ef26e's
drain, decoded (VAs 0x4ef434..0x4ef97a):

1. `usbReadWord(0x02000000)` -> geom (the ONE geometry word; 0x4ef42f).
2. `usbReadWord(0x10000A)` -> tp (0x4ef504).
3. tp_comp from the tp+1 variant math (-0xf0).
4. N = 0x7FF (fixed, 0x4ef452 -> global 0x596e38).
5. **base = (tp_comp + 1 + geom) mod 0x800** (0x4ef5f6..0x4ef61b, mod
   2048, the SDR bound; NOT the DDR's per-word mod-0x400, and no second
   geometry word, no ping-pong).
6. `usbReadByte(base + 0x9000)` -> the trailing-flag byte probe
   (0x4ef637..0x4ef658), the record's "trailing flag byte bit 3"
   column-count adjustment.
7. **FIVE bulk reads at base + {+0x1000, +0x2000, +0x3000, +0x4000,
   +0x9000}** (0x4ef815..0x4ef97a), count = N+1 = 0x800 bytes per plane
   (one byte per column).

So a session that drained with the DDR model (two geometry words, ten
chunks, (N+1)/2 counts) at a type-1 rate read a mix of wrong registers
consistent with static power-on SRAM. The discriminating check: drain
again with the SDR shape above and vary the input signal.

## 14. Comprehensive re-analysis (2026-10-27): mode/qual bits, full startup, complete write ledger

### (1) The mode byte (0x20000000): arithmetic-verified structure

The byte encodes the INTERNAL sample-clock divider ladder. Reading the 20
rows as (base-prescaler, fine-divide) reproduces the 1-2-5 sequence exactly:

- **High nibble = the decade prescaler stage.** 0x2_ = 10 MHz decade,
  0x3_ = 1 MHz, 0x4_ = 100 kHz, 0x5_ = 10 kHz, 0x6_ = 1 kHz, each +1 on
  the nibble divides by ten (the table's rows 7..19 walk these).
- **Low nibble bit 0 = the fine-divide enable** (rows 0-2, the >=250 MHz
  direct path, run with 0x00; rows 3+ carry 0x01).
- Rows 0-2 (1 GHz DDR, 500 MHz, 250 MHz) use mode 0x00, the raw fast
  path, where row 0's type-4/DDR handling differs.

What 0x31 (row 10, 1 MHz) configures: decade stage 3 (the 1 MHz decade
base) with the fine divide enabled and qualifier ÷1, i.e., the internal
sample clock divided down to 1 MHz. The bit-level silicon semantics beyond
this ladder reproduction (which FPGA block each nibble drives) are not
statically visible; the values ARE the app's, which is what the device
needs.

**Timing vs State:** the selector is global `0x5962fc` (persisted, no
compiled writer). **1 = Timing mode (internal clock)**, and ONLY in this
mode does the dispatcher send the mode/qualifier bytes (gate at 0x4f2223)
and take the table rate. **2..4 = State mode (external clock)**, the rate
comes from the user-entered `0x59741c` (0x4f23d7..0x4f23fb) and the
mode/qual divider writes are SKIPPED. There is no separate "enable sample
clock" write anywhere: in timing mode the clock is enabled by the
divider-ladder triple; in state mode it comes from the CLK1/CLK2 input.

### (2) The qualifier byte (0x20000001): the 1-2-5 fine divide

Within each decade triple, qual ∈ {0x00, 0x01, 0x04} = **÷1, ÷2, ÷5**:
rows 7-9 (10, 5, 2 MHz) = 10 x {1, 1/2, 1/5}; rows 10-12 (1 M, 500 k,
200 kHz) likewise; every group verifies arithmetically. The bit pattern is
{0, 1, 4} = binary {000, 001, 100}, likely a two-bit divider select with
values 1 and 4 encoding /2 and /5 taps, but the mapping to silicon is
inference; the VALUES are fact.

### (3) The complete startup-to-first-tick sequence

**Sub Main (FUN_00477xxx, calls at 0x477845..0x477868):**
1. Form_Load (FUN_0047ac43), builds module arrays; inside it, at
   0x47c8df, FUN_004c5a6d sets the USB_IO constants (ACK length 3, page
   cache -1) and at 0x47c8e4..0x47c8f6 sets capture mode 4 / arm byte 0.
   NO wire traffic.
2. `0x596ef0 = 1` (0x47784a), the gate that lets the next steps run.
3. FUN_00471af1, the register-address ladders (host-side constants).
4. FUN_00475035, form/UI setup (COM calls only, no protocol).
5. FUN_0047480e. DefineClockSettings (the clock table; host-side).
6. FUN_00480b84, FUN_00473a61, more host-side tables (the latter
   duplicates the rate constants for UI use).

**The GO action (FUN_004f1453), in order, with waits:**
1. Preamble (0x4f0bff..0x4f0d9b): clear reopen flag; if device not open:
   FUN_004c81ba (device search) then FUN_004c5c6b (OPEN: OpenEx, Reset,
   SetTimeouts 0x4c64f4, SetBaudRate 0x4c6520, SetLatencyTimer 0x4c654c);
   conditionally FUN_004c54f1 + FUN_004c687f (FT_ResetDevice).
2. If the FPGA needs loading: FUN_004c8541 (0x4f0d7c), bit-bang load,
   resync, validate (0x10000F), 0.5 s wait, reopen, **`C1 0x100000 <- 0x61`**
   (0x4ca317); set reopen-needed (0x4f0d9b).
3. **The previously-missed optional reset (0x4f1551..0x4f15aa): if the
   persisted setting `0x59632a` is set and reopen is pending
   `C1 0x100001 <- 0x00` then FT_ResetDevice (FUN_004c687f).** 0x100001 is
   a COMMAND register (the app also READS it as wire status), writing 0
   is a wire/capture-logic reset. The vendor default of 0x59632a is
   registry-loaded (unknown); this write has never been replicated.
4. FUN_004c47de (open path; EEPROM cal reads FT_ReadEE/FT_WriteEE via
   0x424790/0x42474c; may send the open-path 0x61 if the latch allows).
5. FUN_004c8541 again at 0x4f1c6e (validate/load branch).
6. FUN_004f2179 (dispatcher, the 17-step chain, section 3).
7. FUN_00504778 (first timer tick, 0x4f1cf3).

Timing: the only explicit waits are the 0.5 s post-validation pause
(const 0x4029c0 = 0.5) and the resync flush; everything else is
ACK-paced (1000/2000 ms timeouts per command).

### The complete write ledger (every usbWrite site, accounted)

0x61 init (2 sites: 0x4c5368, 0x4ca317); mismatch 0x00 (0x4c8a40);
**NEW: 0x100001 <- 0 pre-run reset (0x4f1584)**; mode/qual (0x4f225a,
0x4f228e); clock byte (0x4f2393); 0x1000002 disarm/enable (0x4f241b,
0x4f25cc); masks A/B 40-bit (0x4f2499, 0x4f255a); aux 0x1000001 (0x4f2659);
ARM-reg disarm 0x100003 (0x4f26c3); 0x100002 <- 1 (0x4f26eb); sequencer
byte/dword/40 writes (0x4f3410..0x4f3cd7, gated per section 9);
0x10000D (0x4f2741); threshold word (0x4f28a2); position words (0x4f2bee,
0x4f2c05); THE ARM (0x4f2c39); timer writes: 0x100002 one-shot (0x504870),
freq select (0x5049d1), timeout advances 0x10000A/B (0x504c19, 0x504ce3),
completion 0x100003<-0 / 0x100002<-1 (0x5052b9, 0x5052e9), phase advances
0x10000A/B/C (0x505375, 0x5053d3, 0x5053af). Nothing else writes.

## 15. The channel-enable masks: 0x1000003 / 0x1000008 (2026-10-27: the root cause)

The CHM's compression note ("the compression logic is only sensitive to
signals currently displayed... not displayed are also not sampled") maps
onto the two 40-bit writes in the dispatcher's clock block, the record's
"trigger masks" are, at minimum, ALSO the sample-channel sets:

- The sources are the waveform-table arrays: `[0x5962a4]` and `[0x5962a8]`,
  34-element WORD arrays maintained by UI handlers (FUN_004fe1eb writes
  [0x5962a4] at 0x4fe374; the startup FUN_00473a61 seeds [0x5962a8] at
  0x473169; the sequencer reads [0x5962a4] at 0x4f34e7/0x4f36f8).
- The packing loop (0x4f2436..0x4f248c): `for i = 33 downto 0: mask <<= 1;
  if (array[i] != 0) mask |= 1`, channel i occupies bit (33-i). D0 alone
  = 0x200000000; all 34 = 0x3FFFFFFFF.
- **The discriminator: in the trigger-disabled path the app SKIPS the mask-A
  write but ALWAYS writes mask B (0x1000008)** (branch 0x4f2429 -> the
  no-terms loop at 0x4f2525 still builds and sends mask B at 0x4f255a).
  A free-run capture has no trigger patterns, yet still needs the sample
  engine to know which channels to monitor, mask B is that set. Mask A
  (0x1000003) additionally scopes the trigger pattern channels.

**Our probe (and the hardware session's chain) wrote mask B = 0, zero
channels monitored: the compression engine detects no transitions on any
channel, collects nothing, and the buffer keeps its power-on pattern. The
B0/F8 filler bytes are consistent with valid "no transitions" compressed
records.** The fix: mask B = the channels to sample (all: 0x3FFFFFFFF;
D0 only: 0x200000000); mask A likewise when triggering.

Related unpinned byte: the aux byte to 0x1000001 (source word 0x5974bc,
no compiled writer) may carry a channel-count or compression flag, worth
bench A/B (0 vs 1 vs 0x22) if masks alone do not wake the engine.

The RLE decode for READING the buffer once sampling works is the record's
section 6.5 (protocol.py decode_rle_count): flag-bit-3 columns are
35-bit run counts; a constant signal legitimately produces long runs.

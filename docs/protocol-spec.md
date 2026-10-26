# LogicPort USB Protocol — Reverse Engineering Record

> Source: `LogicPort.exe` v3.4.0.2371 (Intronix Test Instruments), static analysis.
> Evidence addresses refer to the extracted `LogicPort.exe` (SHA-256 `343745f3...`).
> Register: ASD-STE100 Simplified Technical English. Technical names stay as is.
> Status: work in progress. Each section lists its evidence.

## 1. Transport Layer

The LogicPort uses an FTDI USB device. The host opens it through the FTDI D2XX driver.

- USB Vendor ID: `0x0403` (FTDI)
- USB Product ID: `0xDC48` (Intronix LogicPort)
- A second product uses PID `0xDC4A` (Intronix ATE). The same software supports it.
- Driver: `FtdiBus` + `FTD2XX.dll` (INF: `logicport.inf`, `logicport32.inf`)

Live check, 2026-10-12, on a connected device:

- USB Product Name: `LogicPort Logic Analyzer`
- USB Vendor Name: `Intronix`
- idVendor 1027 (`0x0403`), idProduct 56392 (`0xDC48`) — both match the INF data
- bcdDevice: `0x0400`
- USB Serial Number: present and unique per device (value omitted here)
- The EEPROM holds the Intronix strings. Open the device by serial number with `FT_OpenEx`.

A non-Windows host can use `libftdi` with VID `0x0403` and PID `0xDC48`.

The application calls these D2XX functions:

| Function | Use |
|---|---|
| `FT_OpenEx`, `FT_Close`, `FT_ListDevices` | Open and close the device |
| `FT_Write`, `FT_Read` | Send and receive protocol bytes |
| `FT_SetTimeouts`, `FT_GetStatus`, `FT_Purge` | Transfer control |
| `FT_ResetDevice`, `FT_ResetPort` | Device reset |
| `FT_SetBaudRate` | Serial interface speed (see section 3) |
| `FT_SetBitMode`, `FT_GetBitMode` | Interface mode |
| `FT_SetLatencyTimer`, `FT_GetLatencyTimer` | USB latency tuning |
| `FT_ReadEE`, `FT_WriteEE`, `FT_EraseEE` | EEPROM access |
| `FT_EE_Read`, `FT_EE_Program` | EEPROM image read and program |
| `FT_EE_UARead`, `FT_EE_UAWrite`, `FT_EE_UASize` | EEPROM user area (calibration data) |

Evidence: import strings in `LogicPort.exe`; `logicport.inf` hardware IDs.

## 2. Device Capabilities (Context for the Protocol)

- 34 sampled channels
- Buffer: 34 channels x 2048 samples
- Timing mode: 1 KHz to 500 MHz internal clock
- State mode: 0 to 200 MHz external clock, with qualifier clocks
- Sample compression up to 2^33 to 1
- Trigger sequencer: 4 levels, 250 MHz, 20-bit event counters
- Threshold range: +6 V to -6 V in 50 mV steps

Evidence: `LogicPort_en.chm` Specifications topic.

## 3. Wire Protocol

Every command is 5 bytes. The host writes them, then waits for the response. The wait loop polls `FT_GetStatus` until the receive queue holds enough bytes and the transmit queue is empty. Timeouts are 1000 ms or 2000 ms.

All multi-byte values below are little-endian, except where noted. Response VALUES are big-endian (section 3.5).

### 3.1 Command 0xC1 — Write (general form)

Wire: `C1 r0 r1 L0 L1 [payload...]`

| Byte | Field |
|---|---|
| 0 | `0xC1` opcode |
| 1 | Clock word, bits 0 to 7 |
| 2 | Clock word, bits 8 to 15 |
| 3 | L, bits 0 to 7 (payload length minus 1) |
| 4 | L, bits 8 to 15 |
| 5.. | Payload, L+1 bytes |

The app packs rate and clock select into one value. The low 16 bits are the rate value. The clock select is `Int(value / 65536.0)` (constant at VA `0x402418`). When the clock select changes, the host sends 0xC3 first.

Payload sizes come from wrapper functions in `USB_IO.bas`:

| Wrapper | Payload |
|---|---|
| `usbWriteByte` FUN_004ca679 | 1 byte |
| `usbWriteWord` FUN_004ca84e | 2 bytes, little-endian |
| `usbWriteDword` FUN_004caab4 | 4 bytes, little-endian |
| `usbWrite40` FUN_004cad4a | 5 bytes, little-endian 40-bit value |

The 40-bit form carries trigger masks (34 channel bits) and dwell values.

After each write the host drains pending response bytes and checks an ACK.

### 3.2 Command 0xC2 — Read Request

Wire: `C2 r0 r1 n0 n1`

| Byte | Field |
|---|---|
| 0 | `0xC2` opcode |
| 1 | Clock word, bits 0 to 7 |
| 2 | Clock word, bits 8 to 15 |
| 3 | N, bits 0 to 7 |
| 4 | N, bits 8 to 15 |

N tells the device how many bytes to send back. The host waits for N bytes (2000 ms), then reads N bytes into the global buffer. Read helpers take values from this buffer:

- `usbReadByte` FUN_004cb0c5: one byte at the cursor.
- `usbReadWord` FUN_004cb534: two bytes, big-endian.
- `usbReadDword` FUN_004cb2c8: four bytes, big-endian.

### 3.3 Command 0xC3 — Stop

Wire: `C3 r0 r1 00 00`

The rate bytes carry the current cached rate. The last two bytes are zero. The host sends this command before it changes the rate, and after capture errors.

### 3.4 Resync Flush (FUN_004c78c9)

The host uses this sequence when the USB stream loses sync. It retries up to 20 times:

1. Close the device, then open it again (section 4 init).
2. Purge TX, then purge RX.
3. Write 65535 zero bytes. Require at least 65534 sent.
4. Write 30000 zero bytes. Require at least 30000 sent.
5. Write one `0x01` byte.
6. Purge TX, then purge RX.

A single `0x01` byte is the resync marker. On total failure the software reports "Failed flushing USB interface".

## 4. Handshake Sequence — Device Open and Init

Function `FUN_004c5c6b`, FT_OpenEx call at VA `0x4c5eb7`.

1. `FT_ListDevices` loop. Match the description string `LogicPort Logic Analyzer`.
2. `FT_OpenEx` on the matched device.
3. `FT_ResetDevice`.
4. `FT_SetTimeouts(handle, 5000, 5000)`.
5. `FT_SetLatencyTimer(handle, 4)`.
6. `FT_SetBaudRate(handle, 460800)` (value `0x70800`).

The expected response length starts at 3. The software then configures the FPGA (section 5).

## 5. FPGA Configuration (FUN_004c8541)

The host loads the FPGA design through the FTDI bit-bang pins. The software runs this after device open when the FPGA needs configuration. It retries the whole sequence up to 20 times.

1. Purge RX.
2. `FT_SetBitMode(handle, 0x00, 0x00)` — reset the interface mode.
3. `FT_SetBitMode(handle, 0x07, 0x01)` — async bit-bang. Pins D0 to D2 are outputs.
4. Write one `0x00` byte. This pulses the FPGA config-start line.
5. Poll `FT_GetBitMode` until bit `0x10` is set. This is the FPGA DONE pin (input D4).
6. Prepare the bitstream. The software reads a text file of comma-separated numbers. It decodes each field with `value XOR 0x916662` and checks a running XOR-chain checksum. It scales the values by 16. It also loads a binary file, then rotates two 4096-byte pages within it.
7. Write one `0x04` byte. This marks bitstream start.
8. Poll `FT_GetBitMode` until bit `0x08` is set. This is the FPGA INIT pin (input D3). Timeout 1000 ms.
9. Send the bitstream in chunks of at most 10000 bytes. There is no response framing between chunks.
10. Poll `FT_GetBitMode` until bit `0x10` (DONE) again. Configuration is complete.
11. `FT_SetBitMode(handle, 0x00, 0x00)` — return the FTDI chip to normal mode.
12. Run the resync flush (section 3.4).
13. Send the 0xC1 rate command again (FUN_004ca679 path).

Bitstream source: `LogicPort.ccf` (8,387,769 bytes, SHA-256 `9298a5ea...`). The file starts with a line of nine comma-separated decimal values, each XOR-obfuscated with `0x916662`. The rest is the binary bitstream body with the page rotation. The file name string sits at VA `0x41e040`. A second file `36875214.ccf` holds the eight ASCII digits `35298750`. It is an application mode gate, not device data: `35298750` selects full mode, `35298751` selects variant mode, anything else selects degraded mode (reader FUN_004a345d).

## 5a. Configuration Encoding

The settings dispatcher FUN_004f2179 sends config when its dirty flags are set.

### Threshold (DAC)

The app stores the threshold in volts. It converts volts to a 10-bit DAC code, then sends it with `usbWriteWord`:

```
T = 1.315 + 0.4020381098624974 * (1.315 - V)
code = Round(((4.559 - T) / 5.875) * 1023)
```

Check points: V = +6 gives 893, V = 0 gives 473, V = -6 gives 53. One code is about 14.3 mV. The DAC spans about -6.75 V to +7.86 V.

Calibration values are read back from the FTDI EEPROM (Ordinal 535 path into globals `0x5967dc` and `0x5967d8`).

### Sample Mode and Clock

A sample-mode byte is computed from three state bits:

```
mode = 2 * ((NOT clkState) AND 2) + clkState + 8 * (otherState XOR 1)
```

The host sends this byte, followed by a qualifier byte.

### Trigger Masks

Each trigger mask is 34 bits, one bit per channel. The app builds it from an array of 34 integer terms (bit i for channel i). It sends each mask with `usbWrite40` (5 bytes). Two masks are sent: mask A and mask B.

### Trigger Sequencer (FUN_004f2dd4, segments 0 and 1)

The four-level sequencer sends about 15 writes per segment, in this order:

1. Two 40-bit masks (A and B) from per-channel term arrays.
2. Two dwords with segment parameters.
3. A match-any byte (`0xFF` when any term is a match, else 0).
4. Three control bytes from the segment struct.
5. A term-type byte.
6. A match-and-flag byte.
7. One more byte from the segment struct.
8. Three 40-bit dwell or timing masks.
9. Two more 40-bit masks for state mode.

Segment struct fields in use: enable `+0x46`, parameters `+0x20` and `+0x38`, mask `+0x3c`, control bytes `+0x69`, `+0x6a`, `+0x6c`, `+0x50`, selector `+0x54`.

### Trigger Position

The host sends the trigger position as two words, with pipeline offset adjustments (+7 and -7 for timing, +35 and -30 for some clock types).

### Status Words Read Back

Inside the sample reader FUN_004ed082, the host reads:

- A buffer geometry word, used modulo 0x400.
- A second geometry word.
- The trigger position word (times 2 prescale).
- A trigger word for the final column calc.
- A status byte, scaled by channel count.

Exact bit meanings are open. They need the `StartAcquisition` and `ValidateHardware` paths, not yet decoded.

## 5b. Device Register Map

The 16-bit address field in C1 and C2 frames selects a device register. Known registers:

| Register | Direction | Meaning |
|---|---|---|
| `0x100000` | read | Status byte (section 5c) |
| `0x100001` | read | Wire status, 9 bytes: LOW masks ch0-31, HIGH masks ch0-31, then ch32 and ch33 bits |
| `0x100002`, `0x100003` | write | Written at capture completion |
| `0x10000A` | read | Trigger position word |
| `0x10000B`, `0x10000C` | write | Recurring-acquisition re-arm |
| `0x1000000` | read | Trigger word, mask `0x7FF` |
| `0x2000000` | read | Device write pointer `tp1`, range 0 to 2047 |
| `0x02000000` page | read | Sample memory. Offsets: `+0x2000101` ch0-7, `+0x2002000` ch8-15, `+0x2003000` ch16-23, `+0x2004000` ch24-31, `+0x2009000` per-column flags |
| `0x40000000` page | read | Housekeeping counters (meanings partly open) |

### 5c. Status Byte

| Bits | Meaning |
|---|---|
| 0 | 0 = armed and running, 1 = capture complete. This bit ends the poll loop. |
| 1 | Immediate-trigger flag. When set, the host forces trigger position 0. |
| 6 and 5 | Pre-trigger fill phase: 00, then 10, then 11 as the buffer fills. |

The host polls this byte on an application timer (`FUN_00504778`). It drains the sample data only when bit 0 is 1.

## 6. Sample Data Upload and Format

All sample data arrives through 0xC2 read requests (section 3.2). There are two readers. The DDR reader FUN_004ed082 (`GetSamplesDDR`) performs ten chunk reads: five channel planes from each of two capture pages. The SDR reader FUN_004ef26e (`GetSamplesSDR`) reads one byte per column per plane and runs the compression decode of section 6.5. The two-page structure fits double-rate sampling: each page holds one clock edge.

### 6.1 Memory Layout

The device holds two interleaved capture pages (ping-pong RAM). Each page holds five planes:

| Plane | Channels |
|---|---|
| 0 | D0 to D7, one bit each |
| 1 | D8 to D15 |
| 2 | D16 to D23 |
| 3 | D24 to D31 |
| 4 | Flags. Even columns: bit 1 = CLK1 (ch32), bit 2 = CLK2 (ch33). Odd columns: bit 5 = CLK1, bit 6 = CLK2. All inverted. |

The host computes each page base from a trigger-position word: `base = (tp + 1 + prescale) mod 0x400`. The chunk size is `(N+1)/2` bytes per plane, with N up to 2047.

### 6.2 Column Interleave

Each plane byte `j` covers two output columns:

- Even column `2j` comes from page A byte `j`.
- Odd column `2j+1` comes from page B byte `j`.

The host de-interleaves the pages after the read.

### 6.3 Value Convention

A stored bit 0 means the channel is HIGH. A stored bit 1 means the channel is LOW. The storage is inverted. The flag plane bits are inverted again on read.

### 6.4 Trigger Column

```
triggerColumn = ((N - triggerWord + 1) mod (N+1)) - 21
```

The constant 21 compensates the pipeline delay. A second step adjusts for the active channel width (8, 16, 24, or 32 channels).

### 6.5 Wire Compression (Transitional Sampling on the Wire)

The flag byte bit 3 (`0x08`) marks the column type. The five plane buffers are dual use:

- Sample column (bit 3 clear): the planes hold channel data as above.
- Run column (bit 3 set): the planes hold one 35-bit repeat count:

```
count = (flag & 7) << 32 | plane4 << 24 | plane3 << 16 | plane2 << 8 | plane1
```

The count is big-endian across the four data planes. A run column repeats the previous column state for `count` sample periods. Counts reach 2^35 minus 1.

Decode loop, per column i of N:

```
if flag[i] & 0x08 == 0: decode channels; compressed[i] = 0
else: compressed[i] = count; carry the previous channel state; total += count
```

The host builds three arrays: `expanded` (34 channels by N columns), `compressed` (run counts, 0 for raw columns), and `colTime` (cumulative sample position). Total samples = N plus the sum of counts.

### 6.6 Reader Naming

`GetSamplesSDR` is FUN_004ef26e: one byte per column per plane, with the section 6.5 compression decode. `GetSamplesDDR` is FUN_004ed082: the two-page ping-pong scheme of sections 6.1 to 6.3, with a times-2 prescale on the trigger position. An earlier revision of this document swapped the two names. Column bounds are settled (section 5b): SDR buffers 4096 columns, DDR buffers 2048 columns.

### 6.7 Trigger Post-Processing

```
trigWord = usbReadWord(reg 0x1000000) & 0x7FF
triggerColumn = ((N - trigWord) + 1) mod (N + 1)
```

The column count N is `0x7FF - triggerPosition`, minus one more when the trailing flag byte has bit 3 set. The host adds a pipeline offset of +1, or -7 in one mode, then clamps to zero or more.

## 7. BenchWeave Plugin Requirements

Constraints from the decode:

- Transport: FTDI bulk pipes via libftdi (VID `0x0403`, PID `0xDC48`) or D2XX.
- Init: reset, timeouts 5000 ms, latency timer 4, baud 460800 (section 4).
- Write: 0xC1 frames with payload (section 3.1). Read: 0xC2 frames (section 3.2). Response values are big-endian.
- Threshold: 10-bit DAC code from volts (section 5a). Calibration data lives in the FTDI EEPROM user area.
- Trigger: 34-bit masks plus the four-level sequencer writes (section 5a).
- Sample transfer: plane reads per capture, ping-pong de-interleave in DDR mode, inverted bit storage, RLE run decode (section 6).
- Capture poll: read the status byte (register `0x100000`) until bit 0 is 1, then drain the sample planes (section 5c).
- Error recovery: the resync flush with the 0x01 marker byte (section 3.4).
- FPGA configuration: the bit-bang sequence plus `LogicPort.ccf` (section 5). The plugin must carry this 8.4 MB file, or read it from an installed LogicPort directory.
- The file `36875214.ccf` gates the Windows application mode, not the device. A plugin does not need it. Ship it unchanged only if the Windows app runs beside the plugin.

### Frequency Counter (static decode confirmed; not hardware probed)

The upper register page is the hardware frequency counter. It is multiplexed across four channels:

| Register | Access | Meaning |
|---|---|---|
| `0x40000000` | read byte | Ready gate. Value 0 means the count is ready. |
| `0x40000000` | write byte | Channel select (0 to 3). |
| `0x40000001` | read dword | Count. Frequency in Hz = count x 10. |
| `0x40000005` | read byte | Running maximum (fallback for channel 0). |

The host cycles the four channels on a round-robin basis with a one-iteration lag: select channel N+1, then read the count for channel N. It polls at intervals longer than 75 ms. The x10 factor is the 10 Hz resolution from the specifications.

### Reader Column Bounds (settled)

- `GetSamplesSDR` FUN_004ef26e: N = 0x7FF minus trigger position. Sample buffers hold 4096 columns (0xFFF bounds).
- `GetSamplesDDR` FUN_004ed082: N = 0x7FF minus trigger position. Sample buffers hold 2048 columns (0x7FF bounds). This matches the 34 x 2048 sample buffer in the specifications.

### Trigger Position Registers

The host writes two words to registers `0x100004` and `0x100006` (2 bytes each). The mode flag is the global `0x5962FC`. Branches:

- Mode flag 1 and clock type 2: offsets +35 and -30.
- Mode flag 1 and other clock types: offsets +7 and -7, then both values are divided by the clock type. Clock type 4 is not implemented.
- Mode flag not 1: offsets +7 and -7, no division.

### Sequencer Write Order (per segment)

Registers are `0x200000` plus the offsets below for segment 0, `0x600000` plus the same offsets for segment 1. Each write is a C1 frame to that register.

| # | Register offset | Payload | Source |
|---|---|---|---|
| 1 | +0x12 | byte | term-type byte |
| 2 | +0x1A | 40-bit | mask A accumulator |
| 3 | +0x1F | 40-bit | mask B accumulator |
| 4 | +0x24 | 40-bit | dwell value 1 |
| 5 | +0x00 | 40-bit | condition mask A (only when terms active) |
| 6 | +0x05 | 40-bit | condition mask B (only when terms active) |
| 7 | +0x29 | 40-bit | state mask A (only when state terms active) |
| 8 | +0x2E | 40-bit | state mask B (only when state terms active) |
| 9 | +0x18 | byte | control byte |
| 10 | +0x19 | byte | match mask |
| 11 | +0x0E | word or dword | segment parameter 1 |
| 12 | +0x0A | word or dword | segment parameter 2 |
| 13 | +0x14 | byte | flag byte |
| 14 | +0x16 | byte | match-and flag |
| 15 | +0x17 | byte | control byte 2 |

Per-channel term states pack into the mask values, most significant bit first over 34 channels: 0 = do not care, 1 = accumulate A, 2 = accumulate B, 3 = accumulate both.

## 8. Live Validation Log (2026-10-12)

Probe tool: `tools/live_probe.c` with libftdi 1.5.

- Device open by description: PASS (chip type 1).
- Init latency 4 and baud 460800: PASS (both accepted).
- C2 frame write to the STATUS register: PASS (5 bytes accepted on the wire).
- Response: none within 1.5 s. This is expected. The FPGA must receive its configuration before it answers commands (section 5). The device had no configuration since power-up, because the Windows software was not run.

Conclusion: the transport layer and init sequence are hardware-validated. The command layer is validated up to the FPGA-configuration gate, which matches the documented handshake order. The next live step for the plugin is the bit-bang FPGA load from `LogicPort.ccf`.

## 9. FPGA Loader Result (2026-10-12)

Tool: `tools/fpga_loader.c`, slot 0 of `LogicPort.ccf`. Result chain:

- Header checksum: chain 0x1FA2B matches the header value (exact).
- Slot 0: offset 73, length 1057840 — matches the decoded slot table.
- Page swap applied: [0..4095] with [4096..8191].
- Reset pulse cleared DONE, INIT asserted after the 0x04 byte.
- Bitstream streamed: 1,057,840 bytes in 10000-byte chunks.
- FPGA DONE asserted. Configuration complete.

Follow-up C2 probe on the configured device returned 4 bytes `E0 E0 E0 E0`. Decode: bit 0 = 0 (armed and running), bits 6 and 5 = 11 (pre-fill phase). This matches the status-byte map in section 5c.

Hardware-proven to this point: the installer extraction (header checksum and slot table), the FPGA load chain (page swap, INIT/DONE handshake, bitstream delivery, DONE asserted), the C2 read framing, and one status-register read on a configured device. NOT proven on hardware: the sample-plane reads and the section 6 decode (no sample byte has been read on a device — the format is static-decode confirmed and rehearsed on the offline mock with constructed fixtures only), the frequency-counter registers (static decode only), and the C1 configuration writes. An earlier revision of this section claimed the full decode chain was proven end to end on hardware; that claim overran the evidence above.

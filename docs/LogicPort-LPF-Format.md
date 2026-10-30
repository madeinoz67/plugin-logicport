# LogicPort `.LPF` project file format

> Derived 2026-10-29 from 18 shipped project files beside `LogicPort.exe`, plus a
> decode of the application's own reader. Companion to `LogicPort-Protocol.md`
> (wire and bench history) and `LogicPort-Sample-Format.md` (sample decoding).
>
> **Status:** the container structure and the configuration keys are established
> from the files themselves. Field types and the reader's validation are being
> confirmed against the binary; anything not yet confirmed is marked UNVERIFIED.

## 1. Container structure

The file is **text**, written by a VB6 `Print #` call per record, and read by a
`LineInput` loop.

- **Reader:** an unnamed private Sub at `0x48579c`, reachable only from
  `Main.bas->LoadValid` at `0x485110`. It uses six `__vbaLineInputStr` calls.
- **Writer:** `Main.bas->SaveProject` at `0x458fab`, with 202 `__vbaPrintFile`
  sites.
- **Grammar:** each record is one CRLF-stopped line. A record is a `KEY`
  followed by its values; values are separated by the byte `0x11` (DC1). So a
  line reads `KEY<DC1>value1<DC1>value2…<CRLF>`.
- A 151 KB CSV sample block sits early in the file, so a parser must locate it
  by its record key and row count, not by scanning for keys throughout.

### Free-text fields

Keys that carry arbitrary text are written with a **trailing slash**, and the
value is closed by a field containing only `/`:

```
NotesString/ <DC1> <text, may contain CRLF> <DC1> / CRLF
```

`NotesString` and `DebugString` use this form. A line-based parser must
accumulate lines until it sees a `DC1 /` terminator. That is why the reader has
six separate `__vbaLineInputStr` call sites rather than one loop.

A value may also be empty (`ExportFile` in Quickstart) or contain commas, so the
parser must not split on commas outside the sample block.

### A second confirmation of the field order

VB6 string literals live in a pool laid out as `[u32 length][UTF-16LE][NUL]` in
`.text` between `0x423360` and `0x424050`, and **their order mirrors the file
order**. That independently confirms the section order below, which was measured
from the samples.

## 2. Section order

Measured on `3. ADC Sine, Timing mode 500MHz.LPF` (215 key occurrences).
Field indices are 0x11-separated fields, shown to orient a parser.

| Field | Key | Content |
| --- | --- | --- |
| 0 | `Version` | three values: `2.17`, a build integer (`2361` or `2364`), and the 79-byte caution string |
| 3 | `DebugData` | two values; the second is a signed integer |
| 5 | `SampleData` | see the sample block section below |
| 9 | `ReadOnly` | boolean |
| 10 to 26 | display and save settings | `ShowGraticule`, `ShowTrigger`, `ShowCursors`, `LastVisibleCursor`, `UnitsTime`, `ColumnsVisible`, `ScaleRelativeToReference`, `ScaleFactor`, `ReferenceOffset`, `ReferencePosition`, `StatesOnly`, `CursorSnap`, `SaveOnAcq`, `SaveOnAcqAction`, `SaveOnAcqMaxFiles`, `SaveOnAcqHoldoff`, `InvertedChannelList` |
| 27 to 60 | channel inversion flags | 34 booleans, one per channel |
| 60 | `ExportFormat` | export settings |
| 63 | `CursorOffsetSamples` | six values |
| 69 | `CursorOffsetTime` | six values |
| 75 | `CursorInterlock` | |
| 81 | `ControlSelections` | the configurable-control selections |
| 93 | `ControlValues` | their values |
| 101 | `Signals` | signal names |
| 135 | `Group` | **36 values** each: name, numerics, then a comma-joined wire list inside one field, then padding |
| 243 | `Row` | waveform table rows, **9 values** each |
| 333 | `Column` | waveform table columns |
| 343 | `Pattern` | per-channel pattern column |
| 413 | `Edge` | per-channel edge column |
| 483 | `IsDemoData` | true when the project was produced by demo mode |
| 484 | `AcquiredSampleMode`, `AcquiredSamplePeriod`, `AcquiredChannelList` | describes the capture that is stored |
| 520 | print settings | |
| 525 | `Caption`, `CombineMode`, `Prequalify` | **trigger mode selector** |
| 528 to 560 | trigger terms | `EdgeTerm`, `EdgeCount`, `EdgeCountMode`, `PatternTerm`, `PatternMode`, `PatternCount`, `PatternCountMode`, `RangeTerm`, `RangeGroup`, `RangeMode`, `RangeLeft`, `RangeRight`, `DurationTerm`, `DurationMode`, `DurationLeft`, `DurationRight`, `DurationUnits` |
| 562 | `TimingMode`, `StateClockDelay`, `StateClockPolarity`, `StateClockSelect`, `StateClockRate`, `StateClockUnits` | **sample mode** |
| 568 | `UseCompression`, `PrefillTimeout`, `PostfillTimeout`, `QualifyStateSampling`, `QualifierPolarity` | **acquisition config** |
| 573 | `MeasurementType`, `MeasurementLeftTerm`, `MeasurementRightTerm` | measurement definitions |

## 2a. The sample block

`SampleData` carries four values before the CSV body:

| Value | Meaning |
| --- | --- |
| 1 | wire count, `34` |
| 2 | buffer depth, `2048` |
| 3 | **trigger sample position** (module globals `0x59686c` and `0x59748c`; the value `Scheduled.bas->AdjustTriggerSample` adjusts, and what every interpreter and `ExportData` reads) |
| 4 | a second anchor at `0x596874` and `0x596ef8`; Quickstart carries `1023` here as well as in field 3, so the two anchors usually hold the same value. Meaning UNVERIFIED |

Then the body: a `{` line, a header line `   D0,D1,…,D31,CLK1,CLK2,Count`, then
**exactly `depth` rows**, then a `}` line. Each row has 35 columns: 34 wire
states (`0`, `1` or `U`) plus a `Count`.

This resolves the earlier differential note: files 3 and 4 differ in that third
value (`999` versus `1023`), which is the trigger sample position, not a mode
or rate field.

## 3. Configuration keys, with observed values

Three files compared. Files 3 and 4 are a controlled pair by filename:
timing mode at 500 MHz, and state mode at 100 MHz.

| Key | File 3 | File 4 | Quickstart | Meaning |
| --- | --- | --- | --- | --- |
| `TimingMode` | True | **False** | True | timing (internal clock) versus state (external clock) |
| `StateClockRate` | 1.0E+7 | **1.0E+8** | 1.0E+7 | external clock frequency; file 4 matches its `100MHz` filename |
| `StateClockSelect` | 0 | 0 | 0 | which clock input, CLK1 or CLK2 |
| `StateClockPolarity` | 1 | 1 | 0 | which edge |
| `StateClockDelay` | 1 | 1 | 1 | setup delay |
| `StateClockUnits` | 1 | 1 | 1 | units for the rate or delay |
| `QualifyStateSampling` | False | False | False | the CLK2 qualifier checkbox |
| `QualifierPolarity` | 1 | 1 | 1 | qualifier active level |
| `UseCompression` | **0** | **0** | **1** | compression enable; note the shipped examples are uncompressed |
| `PrefillTimeout` | 2 | 2 | 2 | pre-fill time limit, encoded as an index |
| `PostfillTimeout` | 2 | 2 | 2 | post-fill time limit |
| `CombineMode` | 5 | 5 | **1** | the six-way trigger mode selector index |
| `Prequalify` | False | False | False | prequalify pattern terms |
| `EdgeTerm` | 0 | 0 | **1** | edge term enable |
| `EdgeCount` | 1 | 1 | 1 | occurrences needed |
| `EdgeCountMode` | 0 | 0 | 0 | cumulative or consecutive |
| `PatternTerm` | 0 | 0 | 0 | pattern term enable |
| `AcquiredSampleMode` | 0 | **3** | 0 | mode of the stored capture |
| `AcquiredSamplePeriod` | **2 ns** | **10 ns** | 10 ns | sample period; 2 ns is 500 MHz, 10 ns is 100 MHz |
| `IsDemoData` | False | False | **True** | the Quickstart project is demonstration data |

### What this settles

1. **`TimingMode` is the timing/state switch** the Sample Mode dialog presents,
   and `StateClockRate` carries the external clock frequency.
2. **`CombineMode` is the six-way trigger mode index.** Its values are small
   integers. Quickstart uses 1 with `EdgeTerm` enabled, which is the shape of a
   simple edge trigger.
3. **`UseCompression` is a plain flag**, and it is **0 in all three shipped
   examples**, including the two that were captured from real hardware. The
   product's own examples are uncompressed.
4. **`AcquiredSamplePeriod` ties the file to the wire**: 2 ns corresponds to the
   500 MHz timing file and 10 ns to the 100 MHz state file, so the period is
   stored in seconds and the clock table row follows from it.

## 4. Relationship to the wire

The file stores what the dispatcher later sends. The mapping a client needs:

| File key | Module variable (from the decode) | Wire destination |
| --- | --- | --- |
| `TimingMode`, `StateClockRate`, `StateClock*` | timing/state choice and clock fields | mode byte at `0x1000000`; state-mode clock fields UNVERIFIED |
| `UseCompression` | `UseCompression` | compression bit in the sample-mode config, register UNVERIFIED |
| `CombineMode` | trigger mode index | UNVERIFIED register |
| `EdgeTerm`, `EdgeCount`, `EdgeCountMode` | edge term record | trigger-term bank `0x200000` |
| `InvertedChannelList` | per-channel inversion | logic-sense configuration |

## 4a. How each key lands in the settings object

The loader (`0x48f7a1`) and the saver (`0x496828`) write these members in file
order at a uniform 4-byte stride, which makes the mapping authoritative:

| Offset | Key | Offset | Key |
| --- | --- | --- | --- |
| `+0x140` | `Pattern[]` (2 slots x 34) | `+0x144` | `Edge[]` (2 slots x 34) |
| `+0x148` | `CombineMode` | `+0x14C` | `Prequalify` |
| `+0x150` | `EdgeTerm` | `+0x154` | `EdgeCount` |
| `+0x158` | `EdgeCountMode` | `+0x15C` | `PatternTerm` |
| `+0x160` | `PatternMode` | `+0x164` | `PatternCount` |
| `+0x168` | `PatternCountMode` | `+0x16C` | `RangeTerm` |
| `+0x170` | `RangeGroup` | `+0x174` | `RangeMode` |
| `+0x178` | `RangeLeft` | `+0x17C` | `RangeRight` |
| `+0x180` | `DurationTerm` | `+0x184` | `DurationMode` |
| `+0x188` | `DurationLeft` | `+0x18C` | `DurationRight` |
| `+0x190` | `DurationUnits` | | |

A writer must emit the keys in file order; a reader can rely on the loader's
stride but should still parse by key.

## 4b. Trigger-term enumerations

Decoded from the ComboBox labels in the application's form binary.

| Key | Values |
| --- | --- |
| `RangeMode` | `0` equal to, `1` not equal to, `2` greater than, `3` less than, `4` in range, `5` not in range |
| `DurationMode` | `0` equal to, `1` greater than, `2` less than, `3` in range |
| `EdgeCountMode`, `PatternCountMode` | `0` cumulative, `1` consecutive |
| `DurationUnits` | `0` seconds, `1` samples |
| `CombineMode` | `0` immediately, `1` level A, `2` level B, `3` either, `4` A and B in any order, `5` A then B |

`RangeGroup` is a **zero-based index** into this file's `Group` records, in file
order. The selected group's channel-list length gives the comparison width, and
the `RangeLeft` and `RangeRight` bounds are hexadecimal strings with an `h`
suffix (`80h` is 128). `RangeLeft` carries the operative bound `A`; `RangeRight`
is used only by the two range operators.

The shipped projects use only a subset of these values. That does not mean the
unused values are absent from the product.

## 5. Versioning and validation

- `Version` is written as `2.17` with a build integer of `2361` or `2364` in the
  shipped files, followed by the caution string.
- **The reader never reads `Version` back.** Its literal is referenced only by
  `SaveProject` and `SaveGlobalSettings`. So there is no version gate.
- `LoadValid`'s only checks are that the file exists and that the `SampleData`
  array has `Ubound >= 4`.
- The caution string therefore states an intention rather than a mechanism: the
  application will load a file with a wrong or edited `Version`, and will only
  reject one whose sample block is too short.

## 6. Open items

1. The fourth `SampleData` value, a second anchor at `0x596874` and `0x596ef8`.
2. `AcquiredSampleMode` values: `0` for timing and `3` for state are observed;
   the full enumeration is unknown.
3. The exact `CombineMode` index-to-option mapping. The decode found no read site
   for it anywhere in the binary, so it may not reach the wire at all.

## 7. Status

Corrected and extended 2026-10-29 from the reader and writer decode. The
container grammar, the sample-block structure, the record sizes and the absence
of version validation are now code-derived rather than inferred from samples.


## Correction, 2026-10-29

Every trigger term key carries **two values**, one per term slot.
Quickstart has `EdgeTerm (1, 1)`, `EdgeCount (1, 1)`, `PatternTerm (0, 0)`,
and arms slot 0 on channel 33 (CLK2) and slot 1 on channel 0 (D0).

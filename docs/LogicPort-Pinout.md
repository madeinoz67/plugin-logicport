# LogicPort LA1034 pinout and channel map

> Reference for the input connector and for the channel-to-plane map. Built from
> the vendor help file's connector table, with every channel assignment that this
> project has measured marked as measured. Consult this before guessing at a pin.

## How to identify a pin in practice

**Use the wire and contact colours, not the pin number.** On the unit used for
this work the physical pin numbering did not match the help file's pin numbers:
a clip on the pin the help file calls 20 landed on `D16` and then `D17`, not on
`CLK1`. Counting positions produced three wrong answers in one session. Colour
produced the right one first time.

The pattern is regular. Every group of eight data wires shares one contact
colour, and the wires inside a group run Black, Brown, Red, Orange, Yellow,
Green, Blue, Violet.

## The input connector, 40 pins

| Pin | Wire | Wire colour | Contact colour |
| --- | --- | --- | --- |
| 1 | D0 | Black | White |
| 2 | D1 | Brown | White |
| 3 | D2 | Red | White |
| 4 | D3 | Orange | White |
| 5 | D4 | Yellow | White |
| 6 | D5 | Green | White |
| 7 | D6 | Blue | White |
| 8 | D7 | Violet | White |
| 9 | Ground | Gray | Black |
| 10 | Ground | not applicable | not applicable |
| 11 | D8 | Black | Blue |
| 12 | D9 | Brown | Blue |
| 13 | D10 | Red | Blue |
| 14 | D11 | Orange | Blue |
| 15 | D12 | Yellow | Blue |
| 16 | D13 | Green | Blue |
| 17 | D14 | Blue | Blue |
| 18 | D15 | Violet | Blue |
| 19 | Ground | Gray | Black |
| 20 | **CLK1** | **White** | **Blue** |
| 21 | D16 | Black | Yellow |
| 22 | D17 | Brown | Yellow |
| 23 | D18 | Red | Yellow |
| 24 | D19 | Orange | Yellow |
| 25 | D20 | Yellow | Yellow |
| 26 | D21 | Green | Yellow |
| 27 | D22 | Blue | Yellow |
| 28 | D23 | Violet | Yellow |
| 29 | Ground | Gray | Black |
| 30 | Ground | not applicable | not applicable |
| 31 | D24 | Black | Green |
| 32 | D25 | Brown | Green |
| 33 | D26 | Red | Green |
| 34 | D27 | Orange | Green |
| 35 | D28 | Yellow | Green |
| 36 | D29 | Green | Green |
| 37 | D30 | Blue | Green |
| 38 | D31 | Violet | Green |
| 39 | Ground | Gray | Black |
| 40 | **CLK2** | **White** | **Green** |

Ground leads connect to the host chassis through the USB cable.

## Colour groups

| Contact colour | Pins | Wires | Group |
| --- | --- | --- | --- |
| White | 1 to 8 | D0 to D7 | first eight data channels |
| Blue | 11 to 18 | D8 to D15 | second eight, plus CLK1 at pin 20 |
| Yellow | 21 to 28 | D16 to D23 | third eight |
| Green | 31 to 38 | D24 to D31 | fourth eight, plus CLK2 at pin 40 |
| Black or gray | 9, 19, 29, 39 | Ground | one ground closes each group |

The two clock inputs are the **white wires**. CLK1 carries a blue contact and
sits at the end of the blue group. CLK2 carries a green contact at the far end of
the connector. If you can find a white wire, it is a clock.

## Channel to plane and bit, measured

A channel `k` sits at bit `k mod 8` of its plane, and a stored bit is the
complement of the level, so a set bit means the channel reads low.

| Plane | Channels | Verified |
| --- | --- | --- |
| `0x1000` | D0 to D7 | D0 at bit 0, D1 at bit 1 |
| `0x2000` | D8 to D15 | by decode, consistent with the pattern |
| `0x3000` | D16 to D23 | D16 at bit 0, D17 at bit 1 |
| `0x4000` | D24 to D31 | by decode, consistent with the pattern |
| `0x9000` | flags, CLK1 and CLK2 | CLK1 at bit 1 |

Bits of the flags plane `0x9000`, measured:

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

## State mode needs two different frequencies

In State Mode the **sample clock is the external input**. That makes one test
invalid: driving the clock and the data signal from the same source at the same
frequency. Every sample then lands on the same phase of the waveform, so a flat
reading is the correct result rather than a fault.

To evaluate state mode, drive the clock and the data at different frequencies,
and avoid harmonics so the sampling walks through the waveform.

## Confirming a connection before trusting a capture

Clip the signal, capture, and check which channel shows transitions. A generator
on a working input produces about a thousand transitions in a 2048 sample capture
at 2 kHz with a 500 Hz input, at duty near 0.5. A channel with fewer than forty
transitions is not connected, and a plane reading a flat `0xFF` means every
channel in that group sits below threshold.

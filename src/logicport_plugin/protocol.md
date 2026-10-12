# LogicPort protocol (plugin evidence summary)

Evidence source: `LogicPort-Protocol.md` — the reverse-engineering record of
`LogicPort.exe` v3.4.0.2371 (Intronix) plus the 2026-10-12 hardware probes.
This file is the package-local summary the descriptor's provenance points
at. It states what the adapter implements and what remains open. It claims
no hardware qualification.

## Transport

FTDI USB device, VID `0x0403`, PID `0xDC48` (Intronix LogicPort; PID
`0xDC4A` is the Intronix ATE). The host owns the link: open by product
description or serial number, reset, timeouts 5000/5000 ms, latency timer
4, baud 460800 (record section 4; the live probe confirmed latency and
baud). The plugin declares a `custom` transport whose `protocol_reference`
names this framing; a pinned `otdp.transport.*` provider contract is the
long-term conformance home for the control dialect below.

## Wire framing (record section 3)

Every command is a 5-byte header plus payload, then a response wait.

- `C1 w0 w1 L0 L1 [payload…]` — write. The 16-bit little-endian word is
  the register address, or the packed rate+clock-select value for the rate
  command (low 16 bits rate, `Int(value/65536)` clock select). The payload
  is `L+1` bytes: `usbWriteByte` (1), `usbWriteWord` (2 LE),
  `usbWriteDword` (4 LE), `usbWrite40` (5 LE, carries 34-bit trigger
  masks and dwell values). After each write the host drains the ACK bytes
  — a quiet line is acceptable; the ACK's exact length is an open item.
- `C2 w0 w1 n0 n1` — read request; the device answers with `n` bytes.
  Response VALUES are big-endian (`usbReadWord`, `usbReadDword`).
- `C3 w0 w1 00 00` — stop; the rate bytes carry the cached rate. The host
  sends it before a rate change and after capture errors.

## FTDI control dialect

The FPGA load and the resync flush need D2XX/libftdi control calls around
the wire frames: device reset, timeouts, latency, baud, TX/RX purge,
bit-bang mode set, and pin polls (DONE = bit `0x10`, INIT = bit `0x08`).
The adapter sends these as `ftdi_control` transactions through the scoped
transport — the host executes them. They never shadow the reserved
section 8.1 stream kinds.

## Registers (record section 5b)

| Address | Dir | Meaning |
|---|---|---|
| `0x100000` | read | status byte (bit 0 capture complete, bit 1 immediate trigger, bits 6:5 pre-fill phase) |
| `0x10000A` | read | trigger position word |
| `0x02000000` page | read | sample memory: `+0x101` D0-7, `+0x2000` D8-15, `+0x3000` D16-23, `+0x4000` D24-31, `+0x9000` flags |

The frame carries a 16-bit address field. The one hardware-proven wire
mapping is status `0x100000` → word `0x0000` (the live C2 probe answered
`E0 E0 E0 E0`). Sample-plane addresses share one page whose offsets fit
the field; the adapter encodes them low-16. Three documented addresses
(`0x1000000`, `0x2000000`, `0x40000000`) collide on low-16 with other
pages — their wire encoding is not decoded and the adapter refuses them.

## FPGA configuration (record section 5; hardware-proven slot 0, 2026-10-12)

`LogicPort.ccf` (8,387,769 bytes, SHA-256
`9298a5eacb3cf1a791b8ce48460a19e031eb81baffa378bab3f94f2d78483f9c`) starts
with a CRLF-terminated line of nine comma-separated decimal values, each
XOR-obfuscated with `0x916662`. Values 0-7 are slot lengths
(`value * 16`); value 8 is an XOR-chain checksum (advisory — the real
file's chain is `0x1FA2B` and matches exactly). The body starts at offset
73; the eight slots tile the file exactly (slot 0: offset 73, length
1,057,840). The only body transform is the swap of the slot's first two
4096-byte pages (applied when the slot is longer than 8192 bytes).

Load sequence: purge RX; bit-mode reset; async bit-bang with D0-D2 as
outputs; write `0x00` (config-start pulse) and poll DONE low; write `0x04`
(bitstream start) and poll INIT high; stream the bitstream in chunks of at
most 10000 bytes; poll DONE high; return the FTDI chip to normal mode;
run the resync flush. The adapter locates the `.ccf` from the action input
`ccf_path` or the `LOGICPORT_CCF` environment variable, verifies the
SHA-256, and refuses any other bitstream. The file is never bundled.

## Sample data (record section 6)

Two ping-pong capture pages; each page holds five planes (D0-7, D8-15,
D16-23, D24-31, flags). Even output column `2j` comes from page A byte
`j`, odd column `2j+1` from page B byte `j`. Storage is inverted: a
stored 0 means the channel is HIGH. The flags plane carries CLK1/CLK2
(bits 1/2 on even columns, 5/6 on odd, inverted) and marks run columns
with bit 3. A run column carries one 35-bit big-endian-ish count across
the four data planes plus the flag's low three bits; it repeats the
previous column state for `count` sample periods. The adapter reads the
trigger-position word, computes the page base `(tp + 1 + prescale) mod
0x400`, reads ten plane chunks of `(N+1)/2` bytes, de-interleaves, and
runs the decode loop.

## Frequency counter (record: confirmed 2026-10-12)

The upper page is a four-channel multiplexed counter: `0x40000000` reads
the ready gate (0 = count ready) and writes the channel select (0-3);
`0x40000001` reads the count dword (Hz = count x 10); `0x40000005` reads
the running maximum (fallback for channel 0). The host cycles the four
channels with a one-iteration lag (select N+1, read N) at intervals
longer than 75 ms (adapter floor: 100 ms). The `read frequency_a..d`
operations select, wait ready, and read; the mux write is a device side
effect the verb-wide read policy cannot declare (disclosed in the
parameter descriptions; standards gap).

## Reader bounds, trigger position, sequencer (record: confirmed)

SDR buffers 4096 columns (0xFFF), DDR 2048 (0x7FF — the 34 x 2048
specification buffer); both derive N as `0x7FF - trigger position`. The
trigger position is written as two LE16 words to `0x100004` and
`0x100006`: +35/-30 for mode flag 1 with clock type 2, +7/-7 then divided
by the clock type for mode flag 1 with types 1 and 3 (type 4 not
implemented in the source), +7/-7 with no division when the mode flag is
not 1. The sequencer plan is fifteen ordered writes per segment at
`0x200000+` (segment 0) and `0x600000+` (segment 1); per-channel term
states (0 don't-care, 1 A, 2 B, 3 both) pack into the two 34-bit masks
most-significant-first; the condition rows drop without active terms and
the state rows without active state terms.

## Posture notes

The descriptor carries the plugin-authored action profile
`intronix.logicport/1.0.0` (pinned in `contracts`) alongside
`otdp.logic_analyser/1.0.0`. The class id is present because the
presentation contract binds dataset targets only to actions listed in a
corpus profile document; the plugin profile cannot back the digital-lanes
binding (standards gap). The schema's invoke coupling also requires the
`otdp.measurement/0.1.0` feature although this adapter publishes no
datasets — the fetch result is an inline dataset returned for host
admission, never published by the adapter. Per the coordinator's ruling,
the class configure refuses loudly while its destinations are open; the
standalone threshold write is served by `write threshold_v`.

## Open items (loud refusals, not guesses)

- Trigger-mask destination registers (record section 5a: two `usbWrite40`
  masks; the four-level sequencer beyond the masks is not decoded).
- C1 rate-command payload byte (record section 5 step 13 routes the rate
  command through `usbWriteByte`; the byte value is not decoded) and the
  Hz-to-rate-value conversion.
- Wire encoding of the trigger word, write pointer and housekeeping pages
  (low-16 collides with other documented pages).
- Acquisition arm/re-arm writes (record section 5a: StartAcquisition is
  not decoded) and the sample-mode byte's destination.
- The second ping-pong page's address uses an assumed `0x400` stride.
- The page bits of the register-address decomposition: the record's
  page-qualified addresses encode low-16 here, and several families
  collide on the low word (status and counter select both reduce to
  0x0000; sequencer segments share every offset word) — `wire_address`
  in `protocol.py` is the single place to fix when a live probe pins the
  decomposition.

Each open item is a named constant in `protocol.py`
(`REGISTER_THRESHOLD_DAC`, `REGISTER_TRIGGER_MASK_A/B`,
`RATE_COMMAND_PAYLOAD`) or a documented formula gap; the adapter refuses
the matching operation with `UNSUPPORTED` while the slot is unset.

## Rehearsal evidence

`vectors.json` scripts the wire conversation the offline mock can rehearse
(`send_receive`, hex): two status reads (the first response `E0 E0 E0 E0`
is the 2026-10-12 device capture), the trigger-position read, ten
plane reads for an 8-column capture (constructed decode fixtures
including one run column), and a stop. The FTDI control dialect is not
rehearseable on the mock — it needs a real transport.

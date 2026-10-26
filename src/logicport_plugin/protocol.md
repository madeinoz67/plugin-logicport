# LogicPort protocol (plugin evidence summary)

Evidence source: `LogicPort-Protocol.md` — the reverse-engineering record of
`LogicPort.exe` v3.4.0.2371 (Intronix) plus the 2026-10-12 loader probes
and the 2026-10-26 read-only reply-grammar probes (record section 10).
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
  (Both ACK sentences are superseded by the reply-grammar addendum
  below — dated 2026-10-26, issue #562: the ACK is exactly 3 bytes, no
  quiet-line drain.)
- `C2 w0 w1 n0 n1` — read request; the device answers with `n` bytes.
  Response VALUES are big-endian (`usbReadWord`, `usbReadDword`).
  (The answer length is superseded by the same addendum: the reply is
  `n + 4` bytes, header plus payload.)
- `C3 w0 w1 00 00` — stop; the rate bytes carry the cached rate. The host
  sends it before a rate change and after capture errors.

### Reply grammar (dated addendum, 2026-10-26, issue #562)

The 2026-10-26 hardware probes (`tools/addr_probe.c`,
`tools/write_probe.c`, issue #562) proved the response grammar. The
sentences above that it contradicts stay frozen and are superseded by
this block; the adapter implements this block:

- A C2 read reply is `count + 4` bytes: `C2 00 <counter> <aux>` plus
  the `count` payload bytes, values big-endian. Replies were observed
  at ~3.5 ms.
- A C1 write ACK is exactly 3 bytes: `C1 00 <counter>` — observed
  seven-for-seven at ~119 ms (the write to `0x0008` acked `C1 00 37`).
  The quiet-line drain is retired; the adapter receives and parses the
  exact frame (`parse_write_ack`).
- One command counter sequences reads and writes, +1 per exchange
  (observed continuous `0x35`→`0x4E` across both directions). A gap is
  recorded as evidence and refuses the exchange. The counter is one byte,
  so continuity is modular: `0xFF`→`0x00` continues, it does not gap
  (fold R1, 2026-10-26). The adapter's continuity is
  first-frame-sets-baseline: gaps before the first observed frame are
  invisible to it (fold R14) — a fresh session cannot judge any exchange
  that happened before it connected.
- The aux header byte's meaning is unproven: parsed, recorded as
  evidence, never interpreted. The `AUX_SEMANTICS` slot in `protocol.py`
  holds the register-slot `None` until a probe observes meaning (fold
  R10).
- The C3 stop frame's reply shape is UNPROVEN — the 2026-10-26 probe
  never sent C3, so the probes observed no C3 reply at all (fold R3,
  2026-10-26; open item recorded). After a C3 frame the adapter does not
  parse a reply: it performs a bounded quiet-tolerant drain (at most 8
  bytes, no exact byte count, budget = the transport's own receive
  timeout) and records only `logicport.c3_drain` byte-count evidence.
  The drain never advances the adapter's counter view — if the device
  does ACK C3 frames with a counter advance, the next parsed reply will
  register a counter gap. That gap evidence is how the true C3 reply
  shape will surface on hardware: honest by design, disclosed here and
  in the drain's code comment.
- The probe also fixed the init-order dependency: a bare device
  (unconfigured FPGA) answers a C2 read with zero bytes (three runs,
  deterministic); after the FPGA load a reply arrives in about 4 ms.
  A concurrent session's log shows the counter running across reads and
  writes (`0x35`–`0x3B` over seven writes, then `0x3C`–`0x4E` over
  seventeen reads). Poll loops pacing at the observed write latency
  remain a HYPOTHESIS, not a requirement.

### Disclosed, not folded (2026-10-26 review wave, issue #562)

Two review NITs were documented rather than fixed, with their reasons:

- `parse_write_ack` fabricates `aux=0` for the 3-byte ACK (the
  `ReplyFrame` requires the field). Harmless: ACKs carry no aux byte —
  inventing a null is the honest placeholder.
- The status read's receipt reports `value = <counter byte>` (fold R2's
  ruling). The register returns no other byte, a counter is device state,
  and the readings surface must render something true — so the receipt
  carries the counter rather than an empty value.

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
| `0x100000` | read | status read — the bit decode previously listed here is retracted (see below) |
| `0x10000A` | read | trigger position word |
| `0x02000000` page | read | sample memory: `+0x101` D0-7, `+0x2000` D8-15, `+0x3000` D16-23, `+0x4000` D24-31, `+0x9000` flags |

### Status retraction (dated addendum, 2026-10-26, issue #562)

The status-bit decode this table previously carried for `0x100000` —
bit 0 capture complete, bit 1 immediate trigger, bits 6:5 pre-fill
phase — is RETRACTED, not silently edited away. The 2026-10-26 hardware
probes (`tools/write_probe.c`, issue #562) proved the reply grammar: a
C2 read answers `count + 4` bytes, `C2 00 <counter> <aux>` plus the
payload, and the byte read where a "status byte" was expected advances
with every command — it is the command counter. No capture-complete,
immediate-trigger or pre-fill semantics are proven; the adapter treats
the capture gate as ungated (`complete: None`) until a proven gate
source lands, and a fetch dataset now carries
`context.completion_proven: false` so the dataset itself admits it
(fold R6, 2026-10-26: the gate hook's source is `None`; the field rides
the dataset's free-form context block because the OTDP dataset schema
closes its top-level keys). The aux header byte is recorded as evidence;
its meaning is unproven. The full dated addendum with the probe outputs
lands in the parent-dir record — `docs/LogicPort-Protocol.md` §5c in the
logiport workspace one directory above this plugin project (issue #562,
task 10).

The frame carries a 16-bit address field. The one hardware-proven wire
mapping is status `0x100000` → word `0x0000`. Sample-plane addresses
share one page whose offsets fit the field; the adapter encodes them
low-16. Three documented addresses (`0x1000000`, `0x2000000`,
`0x40000000`) collide on low-16 with other pages. Corrected 2026-10-26
(issue #562): `wire_address` MASKS the low 16 bits — every documented
address encodes and transmits as its low word (`0x40000000` → `0x0000`);
the adapter does not refuse the colliding addresses. The collision is
real: mode-based disambiguation is unproven and the page-bits part of
the decomposition remains the open item `wire_address` is the one place
to fix when a live probe pins it. The 2026-10-26 read-only probes could
not discriminate the decomposition (identical zero-data replies at every
probed address); the discriminating experiment is a register write
followed by a read-back, an owner-watched step that has not been taken.

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

## Sample data (record section 6; static decode plus mock rehearsal only)

Two ping-pong capture pages; each page holds five planes (D0-7, D8-15,
D16-23, D24-31, flags). Even output column `2j` comes from page A byte
`j`, odd column `2j+1` from page B byte `j`. Storage is inverted: a
stored 0 means the channel is HIGH. The flags plane carries CLK1/CLK2
(bits 1/2 on even columns, 5/6 on odd, inverted) and marks run columns
with bit 3. A run column carries one 35-bit big-endian-ish count across
the four data planes plus the flag's low three bits; it repeats the
previous column state for `count` sample periods. The adapter records
the capture-gate hook (source `None` — the status-bit gate is retracted,
see the status addendum) and then reads the trigger-position word,
computes the page base `(tp + 1 + prescale) mod 0x400`, reads ten plane
chunks of `(N+1)/2` bytes, de-interleaves, and runs the decode loop.
No sample byte has been read on hardware: the format is static-decode
confirmed and rehearsed on the offline mock with constructed fixtures
only (the hardware probes closed at the FPGA load and the reply grammar —
see the record's live validation log, loader result and section 10).

## Frequency counter (record section 7: static decode confirmed; not hardware probed)

The upper page is a four-channel multiplexed counter: `0x40000000` reads
the ready gate (0 = count ready) and writes the channel select (0-3);
`0x40000001` reads the count dword (Hz = count x 10); `0x40000005` reads
the running maximum (fallback for channel 0). The host cycles the four
channels with a one-iteration lag (select N+1, read N) at intervals
longer than 75 ms (adapter floor: 100 ms). The `read frequency_a..d`
operations hold a counter lock for the whole select/gate/count sequence
(an interleaved pair would return the wrong channel's count tagged
valid), re-poll a not-ready gate only after the 100 ms floor, and read;
the mux write is a device side effect each counter parameter declares
through `read_policy.side_effect: "state_change"`, which escalates the
read verb to `state_change` (OTDP 0.2.4 rule S20 — the corpus this
descriptor pins). The escalation is verb-wide: reads of side-effect-free
parameters inherit the `state_change` declaration too, a conservative
over-classification the schema permits; per-read granularity does not
exist, and that is the residual limit. Because the execution contract
excludes state-changing core reads from procedures, the same locked
sequences are also exposed as the typed `read_counter` invoke action —
one sweep of all four channels under one lock hold (the device's
round-robin unit), `side_effect: state_change` — the shape a bench
admits through a per-device allow rule; procedures use the action,
interactive hosts keep the read parameters. The counter registers were
never probed on hardware — the decode comes from the static analysis of
`LogicPort.exe`.

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

The `otdp.dmm/1.0.0` profile and its `measure`/`configure` actions are
NOT declared (issue #562 ruling, 2026-10-26). The class configure is a
preset control — function, range, aperture, autozero — and the counter
has no decoded destinations for any of them: implementing it would echo
an `effective_configuration` the device never received, the same
unevidenced-write lie the 2026-10-12 coordinator ruling refuses for the
logic-analyser class configure. Because the class contract requires both
actions and couples `measure` to a `configuration_id` only `configure`
can mint, declaring the profile while refusing either action left a
declared surface the adapter refuses — dead wire the host could schedule
against. The honest direction is de-advertisement: the four counter
channels stay declared (all four — the adapter serves slots 0-3) and are
served by the `read frequency_a..d` parameters (interactive hosts) and
the typed `read_counter` action (procedures; the execution contract
excludes state-changing core reads from procedures and admits the typed
action through an allow rule), all adapter-bound and tested against the
mock, with the mux-write side effect declared through
`read_policy.side_effect` on each parameter.

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
  decomposition. The 2026-10-26 read-only probes could not discriminate
  it (identical zero-data replies at every probed address); the
  discriminating experiment is a register write followed by a read-back,
  an owner-watched step that has not been taken.

Each open item is a named constant in `protocol.py`
(`REGISTER_TRIGGER_MASK_A/B`, `RATE_COMMAND_PAYLOAD`) or a documented
formula gap; the adapter refuses the matching operation with
`UNSUPPORTED` while the slot is unset. (Corrected 2026-10-26, issue
#562: `REGISTER_THRESHOLD_DAC` is no longer an open item — the decode
lane closed it at `0x100008` by static analysis and `write threshold_v`
is served. That destination has never been verified on hardware: no
probe has observed a threshold write land, so the address stays
provisional until a live probe confirms it.)

## Rehearsal evidence

(The exchange list this section previously described — two status reads
with an `E0 E0 E0 E0` first response, the trigger-position read, ten
plane reads for an 8-column capture, and a stop — described the
pre-#562 builder-generated corpus. The vectors were rebuilt
2026-10-26; that list is superseded by the addendum below. The ten
plane reads and the stop never existed in the hardware-derived corpus,
and the first response it cited was the truncated four-byte read the
parent record retracts.)

### Rehearsal coverage (dated addendum, 2026-10-26, issue #562; corpus rebuild same day)

`vectors.json` scripts THREE full poll cycles of 14 rows each
(`send_receive`, hex), in the descriptor's read-only walk order (fold
R5): a status poll, then the four-channel frequency-counter cycle —
select write with its 3-byte ACK, ready-gate read, count dword read —
for channels a through d, then a trigger-position read. Three cycles
mirror the probe's own observed counter window (the third runs
`0x35`→`0x42`, inside the recorded `0x35`→`0x4E` run) so a three-cycle
replay never self-wedges. The corpus contains no sample-plane reads and
no stop. Reply bytes follow the proven reply grammar (headered reads,
3-byte ACKs, one command counter `0x19`→`0x42` across the whole file);
row 1 carries the hand-recorded 2026-10-26 `addr_probe` reply
`C2 00 19 00 00 00 00 00` verbatim, and `tests/test_vectors.py` pins a
sha256 over the serialized exchanges array so regeneration is a
deliberate reviewed edit (fold R9). The trigger-position and
frequency-count payload values are the rehearsal scenario's
steady-signal fixtures, not probe recordings; the frames around them
are the hardware shapes. The FTDI control dialect is not rehearseable
on the mock — it needs a real transport.

## Wire facts addendum, 2026-10-28 (issue #562, hardware session)

Dated addendum; the frozen sections above are superseded only where named.
Full probe logs and analysis: the parent record's section 10 addendum and
issue #562's comment trail.

- The resync flush (3.4) is required after configuration for a stable command
  dialect; the 0x01 marker echoes back.
- A third dialect exists: the EE state (`EE 00 <counter>`, no payload), a
  four-slot round robin (~800 ms/cycle) after select writes or desync; it did
  not resolve to C2 within 15 s of gate polling.
- Line filler bursts (F8/EC/E0 class) follow configuration AND resync; ACKs
  queue behind filler — drain before every parse.
- The command counter survives USB reset; it restarts at zero on
  reconfiguration.
- No counter data appeared at any single-page register candidate with a 1 kHz
  signal present. The frequency-read frame sequence is an open item owned by
  static decode of the Windows application (with StartAcquisition).

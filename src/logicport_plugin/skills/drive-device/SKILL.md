---
name: logicport-plugin-device-operation
description: Drive the Intronix LogicPort logic analyser plugin - identify, init,
  FPGA configure, status and sample-plane reads over the adapter's evidenced
  operations.
---

# Driving the LogicPort device

Open the adapter via `create_plugin()`, then
`open(descriptor, services, context)`. The session needs a host that
speaks the plugin's dialect: section 8.1 `stream_send`/`stream_receive`
frames for the wire protocol, plus `ftdi_control` transactions for the
D2XX/libftdi calls (reset, timeouts, latency, baud, purge, bit-bang mode,
pin polls). Construction and open do no device I/O; the init sequence is
an operation.

Supported verbs and their shapes:

- `identify` (no arguments) — static identity: Intronix LogicPort,
  source `commissioned`. No device I/O.
- `reset` (no arguments) — the record section 4 handshake through the
  dialect: device reset, timeouts 5000/5000, latency 4, baud 460800.
  Returns `{acknowledged: true}`.
- `read` `{"parameter": "status"}` — C2 read of register 0x100000;
  the value is the raw status byte (bit 0 capture complete).
- `read` `{"parameter": "trigger_position"}` — C2 read of register
  0x10000A; the trigger position word.
- `write` `{"parameter": "rate_word", "value": <packed word>}` — the C1
  rate command with a C3 stop first when the clock select changes.
  REFUSES with `UNSUPPORTED` while the rate-command payload byte is an
  open item.
- `write` `{"parameter": "threshold_v", "value": <volts>}` — converts to
  the 10-bit DAC code (record section 5a) and sends it as a C1 word.
  REFUSES with `UNSUPPORTED` while the DAC destination register is an
  open item.
- `invoke` `{"action_id": "configure_fpga", "input": {"ccf_path": ...}}`
  — the record section 5 FPGA load. The `.ccf` path comes from the input
  or the `LOGICPORT_CCF` environment variable; the SHA-256 must be the
  hardware-proven digest. Optional input: `slot` (0-7, default 0),
  `attempts` (1-20, default 1), `init_timeout_ms` (default 1000).
- `invoke` `{"action_id": "set_trigger", "input": {"mask_a": ..., "mask_b": ...}}`
  — two 34-bit masks as `usbWrite40` C1 frames. REFUSES with
  `UNSUPPORTED` while the mask destination registers are open items.
- `invoke` `{"action_id": "stop", "input": {}}` — C3 with the cached
  rate word.
- `invoke` `{"action_id": "resync", "input": {"attempts": 1}}` — the
  record section 3.4 flush: re-init, purges, 65535 and 30000 zero bytes,
  the `0x01` marker, purges.
- `invoke` `{"action_id": "read_samples", "input": {"column_count": N}}`
  — reads the trigger-position word, then ten plane chunks (five planes,
  two ping-pong pages), de-interleaves and RLE-decodes. Returns packed
  34-bit column states, run counts and the total sample count. Optional
  input: `prescale`, `trigger_word` (adds the §6.7 trigger column).

- `read` `{"parameter": "frequency_a"}` (also b/c/d) — the multiplexed
  frequency counter: selects the slot's channel (0-3), waits the ready
  gate, reads the count dword; value in Hz (count x 10). The select is a
  device write; see the descriptor's disclosure on these parameters.
- `invoke` `{"action_id": "set_trigger_position", "input": {"position":
  P, "mode_flag": M, "clock_type": C}}` — the two LE16 trigger-position
  writes with the confirmed offset branches (clock type 4 refuses).
- `invoke` `{"action_id": "configure_sequencer", "input": {...}}` — the
  fifteen-row sequencer plan for segment 0 or 1; terms pack
  most-significant-first over 34 channels.
- `invoke` `{"action_id": "otdp.logic_analyser.configure/1.0.0", ...}` —
  stores the class configuration; device-side threshold and rate writes
  stay pending (open items) and the deferral rides the evidence record.
- `invoke` `{"action_id": "otdp.logic_analyser.arm/1.0.0", ...}` —
  registers the acquisition and reports the device's arm state
  (armed/running/complete) from the status byte.
- `invoke` `{"action_id": "otdp.logic_analyser.fetch/1.0.0", "input":
  {"acquisition_id": ..., "max_bytes": ..., "allow_partial": ...}}` —
  polls the capture, drains the planes, RLE-decodes, and returns an
  inline `digital_trace` dataset (34 logic lanes + a seconds time axis
  from the stored sample rate). The host must mint
  `context.dataset_id`; the dataset is for host admission — this
  adapter publishes nothing itself.
- `invoke` `{"action_id": "otdp.logic_analyser.abort/1.0.0", ...}` —
  C3 stop under the class abort shape.

Every result follows the adapter envelope: `status` `ok` / `error` /
`unknown` with `data` or `error`. An error after the first transmit
reports `unknown` — the dispatch state is uncertain, never a clean
failure. Deadlines are hard: an operation past its `deadline_monotonic`
fails rather than transmit.

Safety: driving stays behind the supplied scoped transport. Do not
contact hardware, flash firmware or energise outputs from plugin code,
and publish nothing without separate authority.

# LogicPort plugin — roadmap

Source of truth for phase state. `todo.md` carries the active section's rows;
completed rows move to `done.md`. Green = suite 138/2 + `benchweave-sdk check`
exit 0 + `check-ui` exit 0 + `verify-ui` clean.

## Phase 1 — C-tool parity fold ✅ (2026-11-06, green, uncommitted)

Whole-body load at 460800, image-7 default recorded, timed flush
(50/120/80 ms), no chip reset in flush (addendum 10), N=1 paced prime
(TR step 10), per-frame RX purge, 300 µs inter-write gaps, C3 as page
select with parsed ACK, purge-before-every-parse. Bench result: device
still answers filler — driver-layer difference (pyftdi vs libftdi) is the
surviving hypothesis. Evidence: `tools/session-2026-11-06-*.json`.

- [x] Purge-before-parse, prime, timings, whole-body load
- [x] UI preset/digest defect fixed (check-preset 4/4, check-ui green)
- [x] Land on `main` via PR — #10 open, green, awaiting review

## Phase 2 — Tracker fold: RedTeam + lane findings onto #589

wave-retention, config-channels, decoders-arming reports received; presets
row already fixed. Host/SDK defects (dead fetch on `context.dataset_id`,
preset homes mismatch `ui/presets/` vs `config/presets/`, no decoder
runner, no `invoke` op in standalone host) land as #589 rows, not plugin
rows. Non-blocking per principal ruling.

## Phase 3 — Vectors rebuild from a real capture

`vectors.json` rebuilds from a device journal (`--record`/`--replay`),
never hand-authored. Blocked on a plugin run that completes framed
exchanges (Phase 4 gate).

## Phase 4 — Driver fork resolution (the blocker)

Proven path is libftdi (`tools/full_load.c`); plugin rides pyftdi via the
SDK. Fork: libftdi-backed driver for the plugin vs pyftdi differential
probing (bitmode-switch purge, USB transfer size, latency handling).
Principal call pending.

## Phase 5 — First decoded capture end-to-end

configure_fpga → status in dialect → read_samples → dataset via d9's
PR-3 admission surface. Exit: a decoded capture through the plugin.

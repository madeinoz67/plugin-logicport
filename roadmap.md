# LogicPort plugin — roadmap

Source of truth for phase state. `todo.md` carries the active section's rows;
completed rows move to `done.md`. Green = suite 138/2 + `benchweave-sdk check`
exit 0 + `check-ui` exit 0 + `verify-ui` clean.

## Phase 1 — C-tool parity fold ✅ (2026-11-06 green; merged to main 2026-11-07, 58766b8)

Whole-body load at 460800, image-7 default recorded, timed flush
(50/120/80 ms), no chip reset in flush (addendum 10), N=1 paced prime
(TR step 10), per-frame RX purge, 300 µs inter-write gaps, C3 as page
select with parsed ACK, purge-before-every-parse. Bench result: device
still answers filler — driver-layer difference (pyftdi vs libftdi) is the
surviving hypothesis. Evidence: `tools/session-2026-11-06-*.json`.

- [x] Purge-before-parse, prime, timings, whole-body load
- [x] UI preset/digest defect fixed (check-preset 4/4, check-ui green)
- [x] Land on `main` via PR #10 — merged 2026-11-07 (58766b8) on the
      principal's merge-when-green; CI added to main after the merge
      (2f0a244 green: lint + tests)

## Phase 2 — Tracker fold: RedTeam + lane findings onto #589 ✅ (2026-11-07)

Folded: three lane reports on #589 (comment 6687015879), the ring-purge
defect (comment 6687332643, d9's PR-7 carrier), presets row closed at
d9b4ea2. Routing per principal directive: SDK defects to #589, plugin
issues local. Non-blocking per ruling.

## Phase 3 — Vectors rebuild from a real capture

`vectors.json` rebuilds from a device journal (`--record`/`--replay`),
never hand-authored. Blocked on a plugin run that completes framed
exchanges (Phase 4 gate).

## Phase 4 — SDK transport bisect ✅ (closed 2026-11-07)

Root cause: the receive ring survived the rx purge. Fixed SDK-side in
PR 239 (validated pre-merge on hardware). Plugin-side reply pacing landed
(40 x 25 ms). Two reads now complete through the transport.

## Phase 5 — First decoded capture end-to-end

configure_fpga → status in dialect → read_samples → dataset via d9's
PR-3 admission surface. Exit: a decoded capture through the plugin.

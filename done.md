# done — landed rows (moved from todo.md)

## 2026-11-06

- [x] C3 page-select decode: `segment_of`, `parse_c3_ack`, purge-before-
      every-parse; fold-R3 drain retired (premise disproven)
- [x] Flush timings + no-reset flush + N=1 paced prime + whole-body load
      at 460800; image-7 default per TR matrix (suite 138/2, check exit 0)
- [x] UI preset/digest defect: duplicate `$id` schemas deduplicated, pins
      repointed, scaffold preset removed — check-preset 4/4, check-ui and
      verify-ui green
- [x] Device-session recorder with transfer-layer instrumentation
      (`scripts/record_device_session.py`) + seven evidence sessions under
      `tools/session-2026-11-06-*.json`
- [x] Negative result banked: every adapter-level C-tool delta matched and
      the device still answers filler — driver layer (pyftdi vs libftdi)
      is the surviving hypothesis

- [x] Lane fold onto #589 (comment 6687015879): capture path, configuration,
      decoders; presets row closed at d9b4ea2 / PR #10
- [x] Protocol record addendum 77 (2026-11-06): C-parity lap measured

- [x] pyftdi_full_load.py: device answers in dialect through pyftdi alone
      (addendum 78) - driver-layer hypothesis overturned, fork dissolved

- [x] set_trigger decoded: mask A/B destinations pinned from full_load.c's
      proven chain; the writes now select-and-prime segment 0x0100

- [x] Rate decoded: the rate is the clock bank's two row bytes (TR rate
      table), not a packed word; rate_hz parameter lands in the descriptor

- [x] Capture engine proven outside the SDK: the Client Guide's full
      sequence (disarm/enable, fill-limit pages 0x0020/0x0040, tail 0x000D,
      exit-prefill 0x000A) runs the cursor; unconfigure-before-load added

- [x] Engine-no-start root-caused from the records: pre-fill phase bits
      (00/40/60) confirmed on hardware; the missing 0x61 enable identified
      with its addendum-76 conflict

- [x] 0x61 conflict reconciled from the record's own Part I: enable at
      0x0100_0000 (row-19 byte A), fault at 0x0010_0000 (status) - one
      register apart, both already bench-proven in their own direction

- [x] PR-7 pre-merge bench validation: trigger_position read OK through the
      SDK transport (session-2026-11-07-pr7-first-read.json) - the ring
      purge is proven on hardware

- [x] Reply pacing landed: two device reads (status, trigger_position)
      complete through the plugin against merged PR 239

## 2026-11-07 — Phase 1 landed + CI birth

- PR #10 merged (58766b8) under merge-when-green; issue #9 auto-closed.
  Fresh pre-merge rollup: pytest 138/2, descriptor check exit 0.
- Basic CI born on main: lint (ruff minimal set, docs/ excluded) + tests
  (pytest, 3.13, uv). First run RED — lint job lacked the lint extra —
  fixed in 2f0a244; run 42323637809 GREEN. Lint caught one real defect
  (duplicate mid-file import block in test_protocol.py) plus an unused
  import; suite stayed 138/2.
- Scaffold CI gap filed upstream: benchweave issue #596 (STE register).

## 2026-11-07 — session history folded from todo

- PR #10 merged 01:15Z (58766b8) under merge-when-green; issue #9
  auto-closed. Lane fold on #589 (6687015879, STE); addendum 77 appended.
- Driver fork dissolved by measurement (pyftdi talks, counters 00-08);
  ring-purge defect isolated and filed SDK-side (6687332643), carried by d9
  as PR-7; plugin drain loop reverted.
- Trigger masks pinned (0x01000003/0x01000008); rate model rebuilt
  (RATE_TABLE, rate_hz descriptor parameter, clock-bank select-and-prime);
  Client Guide §6 ported — exit-prefill was full_load's missing write.
- 0x61 two-registers fork resolved on paper, then proven on the bench.
- PR-7 (SDK #239) validated pre-merge on hardware (first plugin read
  through the transport, comment 6690919120); 239 merged; reply pacing
  landed; two reads OK (session-2026-11-07-two-reads-ok.json).
- Basic CI born (lint + tests, first run RED on the lint extra, green at
  2f0a244); scaffold gap filed as benchweave #596.
- Bench go executed (3 runs): reopen desyncs (ee0000 law), enable accepted
  live (c1000a), first D0 edges in the ring; pre-fill holds. Addendum 79 +
  plugin Part I updated; desync law banked to KNOWLEDGE.
- Recorder --read-only mode (resync preamble); two addendum 79 corollaries;
  adapter prime question answered (adapter.py:1011 — prime IS composed);
  the real gap is armed device state.

## 2026-11-07 — #596 delivered by the SDK lane

- benchweave #596 CLOSED (comment 6703191445): copier scaffold now ships
  .github/workflows/ci.yml (lint + tests, uv, 3.13); generated pyproject
  gains a lint extra + explicit ruff select; the lint-extra-before-ruff law
  is structural (install step at a lower index than the tool step, with a
  test asserting it); a Windows updater conflict-block defect was found and
  fixed in the same PR. Open obligation: gateway PINNED_FILE_SET gains the
  workflow file in the pointer PR after the next tagged SDK release.

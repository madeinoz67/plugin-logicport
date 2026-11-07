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

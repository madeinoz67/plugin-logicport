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

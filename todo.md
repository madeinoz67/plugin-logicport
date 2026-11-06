# todo — active section (Phase 1 close-out + Phase 2 fold)

- [x] Branch fix/c-tool-parity-fold committed (d9b4ea2); issue #9 + PR #10
      open; suite 138/2 + check + check-ui + verify-ui green; merge awaits
      review (self-merge denied by the harness — principal's word needed)
- [x] Lane fold posted to #589 (comment 6687015879, STE register): capture
      path, configuration, decoders rows + presets row closed at d9b4ea2
- [x] Addendum 77 appended to the protocol record: seven measured deltas,
      the once-only prime result, driver-layer hypothesis named
- [x] Fork DISSOLVED by measurement: pyftdi_full_load.py talks (counters
      00-08) - driver exonerated, fault is the SDK transport wrapping
- [x] Bisect round 1 DONE: ring-purge defect isolated (purge clears the
      FTDI buffer, not the SerialLink ring); filed SDK-side on #589
      (comment 6687332643); d9 carries it as PR-7 behind PR-1 with a
      reader-quiesce arm. Plugin-side drain loop reverted - grammar and
      mock both refuse a healthy starved drain; suite back to 138/2
- [x] Trigger-mask slots pinned from the proven chain (0x01000003/0x01000008,
      seg 0x0100); set_trigger un-refused through the selecting priming
      write; suite 138/2, check exit 0
- [ ] When PR-7 lands: re-run the recorder against it (no plugin change
      needed if purge semantics hold); then Phase 3 vectors rebuild

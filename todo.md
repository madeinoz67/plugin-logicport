# todo — active section (Phase 1 close-out + Phase 2 fold)

- [x] PR #10 MERGED 2026-11-07 01:15Z (merge commit 58766b8) under the
      principal's merge-when-green; fresh pre-merge rollup: 138/2 + check
      exit 0; issue #9 auto-closed COMPLETED. Branch fix/c-tool-parity-fold
      landed; local main synced.
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
- [x] Rate decode: packed-word model RETIRED; rate_hz parameter selects a
      row of the 20-row clock table (TR) and writes bytes A/B to the clock
      bank via select-and-prime; descriptor + all pins repinned; suite
      138/2, check/check-ui/verify-ui all 0
- [x] Client Guide section 6 ported to pyftdi_full_load.py chain: the
      engine RUNS (cursor advances and wraps, exit-prefill completes,
      cursor-relative fetch returns 256B) - full_load's static cursor was
      the missing 0x000A exit-prefill write
- [x] Flat-window root-cailed to the doc records: the engine never starts
      (cursor static at 6 BEFORE completion; full 2048B ring all-idle bf;
      status 00 = pre-fill per the lane's phase bits). The decode lane
      names the missing enable: close/reopen + C1 0x100000<-0x61 ("the
      input-conditioning/sample-engine enable the session was missing")
- [x] Fork RESOLVED on paper (no hardware risk): the lane's enable is
      0x61 to 0x0100_0000 (mode block byte 0 = rate row 19's byte A,
      vendor writes it after a close/reopen; proven 2026-10-28, ACK C1 00
      01, LED ON); addendum 76's fault is the SAME byte to 0x0010_0000
      (status). One register apart. Constants renamed in protocol.py
- [x] Bench go EXECUTED (2026-11-07, 3 runs, tee'd in ../tools/enable-run*):
      run 1+2 — the close/reopen DESYNCS the reopened pyftdi stream (both
      C3 select and enable ack ee0000); ALSO run 1's mode write 0x0C at
      0x0100_0000 clobbered the enable byte (tool now writes enable LAST on
      the mode page). Run 3 WITHOUT reopen: C3 ack c30009, 0x61 ack c1000a
      (ACCEPTED), first live D0 edges ever (0xfe/0xff in the ring). Engine
      still holds pre-fill: cursor static 1795, status 00, exit-prefill did
      not transition. NEXT PROBE (needs go): force trigger 0x0010_000B<-0x01,
      the vendor's pre-fill->trigger phase register.
- [x] PR-7 (SDK #239) validated on hardware PRE-MERGE from the pr7-builder
      worktree in a scratch venv: first plugin read through the transport
      EVER (trigger_position ok); stale-ring echo class gone; comment
      6690919120 on the PR carries the evidence. Remaining failures are
      reply-pacing shaped (plugin-side row below)
- [x] 239 MERGED; reply pacing landed (40 x 25 ms, the C tools' 900 ms
      window): status AND trigger_position both read OK through the plugin
      (session-2026-11-07-two-reads-ok.json); read_counter's refusal is an
      honest counter gap after configure's 60 s budget miss (#589 row);
      read_samples awaits the engine enable
- [x] Basic CI added (principal directive): .github/workflows/ci.yml, lint
      (ruff, minimal E4/E7/E9/F set, docs/ excluded) + tests (pytest 3.13,
      uv). Local: ruff clean, 138/2. First run RED (lint job spawned ruff
      without the lint extra), fixed 2f0a244, run 42323637809 GREEN
- [x] SDK-scaffold CI gap communicated: benchweave issue #596 (STE
      register) — copier template should ship a lint+tests workflow
- [ ] Stage `--force-trigger` in pyftdi_full_load.py (0x0010_000B<-0x01,
      the vendor's pre-fill->trigger phase register) behind a flag, ready
      for the go — local tool code only, no device write until the word
- [ ] Protocol addendum 79: reopened-pyftdi-stream desync (ee0000 on every
      frame post-reopen), enable accepted live (c30009/c1000a), first D0
      edges in the ring, pre-fill hold. Plugin protocol.md Part I edited in
      the same pass (living-protocol-docs directive)
- [ ] Bank the reopen-desync law to KNOWLEDGE — it governs every pyftdi
      session on this device, not just the enable path
- [ ] Phase 3 prep: read-only capture mode in scripts/record_device_session.py
      (cursor/status/plane reads, no writes) so a clean journal is one
      command once the engine runs
- [ ] d9 test-vehicle work when the #596 slice lands — PRIORITY OVERRIDES
      ALL LOCAL ROWS the moment a request arrives (principal directive
      2026-11-07); rows appear here as they come in
- [ ] Phase 3: vectors rebuild from a clean capture (needs configure inside
      its budget + the engine enable go)

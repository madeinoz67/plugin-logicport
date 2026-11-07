# todo — active section (train keeps running; deferrals named)

## Deferrals (owner named, not silent holds)

- [ ] DEFERRED → PRINCIPAL: force-trigger probe (staged, one run):
      `chain --enable --force-trigger` writes 0x0010_000B<-0x01 (vendor
      pre-fill->trigger phase register). The engine holds pre-fill; this is
      the likely phase-advancer. Awaits the word.
- [ ] DEFERRED → PRINCIPAL: disarm probe (0x0010_0003<-0x00) on a resynced
      armed device, then re-read status. Discriminates armed-state read
      refusal vs deeper desync.
- [ ] DEFERRED → PRINCIPAL: six iCloud sync duplicates (`* 2.*` files) —
      one rm pass when you say.

## Active

- [x] Standalone slice REHEARSED END TO END on the live server (2026-11-07,
      two serves): discover + connect + parameter_read PASS (status=25,
      quality valid). parameter_apply REFUSED at stale vectors row
      `freq-a-select` (adapter sends the rate rework's C3 page-select; the
      row predates it). capture_start RUNS (cap-db5d7561c36e, state
      capturing, count-bound 64) and aborts at finalise with
      `finalise_refused` — the same stale-vectors wall at the read path.
      MCP surface mapped too (23 bws_v1 tools; capture_start needs
      device_id + count/duration_s bound). One root cause for both legs:
      vectors freshness = Phase 3 = engine running = the force-trigger go.
      read_samples itself is adapter-proven against the mock exchange
      (green suite test).
- [ ] d9 test-vehicle work when a request arrives — PRIORITY OVERRIDES ALL
      LOCAL ROWS (principal directive); blocked-on-SDK/gateway items get
      messaged to the d9 session for THEIR todo, per the same directive.
- [ ] Phase 3: vectors rebuild from a clean capture (needs configure inside
      its budget + the engine running).

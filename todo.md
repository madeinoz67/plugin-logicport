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

- [x] Standalone slice REHEARSED on the live server (2026-11-07, port 8479):
      serve --transport mock + discover + connect + parameter_read all PASS
      (status=25, quality valid, bearer-authed REST). parameter_apply FAILS
      at vectors row `freq-a-select`: adapter now sends C3 02 00 (page
      select, the rate rework's select-and-prime) but the stale vectors row
      expects C1 00 00 00 00 00 — the vectors predate the rate-model
      rebuild. This is Phase 3's exact scope, with the first failing row
      now NAMED. Capture leg blocked behind apply. read_samples itself is
      adapter-proven against the mock exchange (test_invoke_read_samples_
      exact_exchange, green in suite).
- [ ] d9 test-vehicle work when a request arrives — PRIORITY OVERRIDES ALL
      LOCAL ROWS (principal directive); blocked-on-SDK/gateway items get
      messaged to the d9 session for THEIR todo, per the same directive.
- [ ] Phase 3: vectors rebuild from a clean capture (needs configure inside
      its budget + the engine running).

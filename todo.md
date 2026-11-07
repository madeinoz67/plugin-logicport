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

- [ ] Working standalone plugin, next ungated slice: read_samples
      end-to-end against the MOCK (rehearsal shape), so the standalone
      story (serve + connect + read + apply + capture) is complete on
      recorded bytes while the bench gates wait.
- [ ] d9 test-vehicle work when a request arrives — PRIORITY OVERRIDES ALL
      LOCAL ROWS (principal directive); blocked-on-SDK/gateway items get
      messaged to the d9 session for THEIR todo, per the same directive.
- [ ] Phase 3: vectors rebuild from a clean capture (needs configure inside
      its budget + the engine running).

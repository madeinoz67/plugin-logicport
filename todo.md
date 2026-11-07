# todo — active section (bench probes + SDK lane)

- [ ] Force-trigger probe (GATED, staged): `chain --enable --force-trigger`
      writes 0x0010_000B<-0x01 (vendor pre-fill->trigger phase register)
      after exit-prefill. Awaits the go.
- [ ] Armed-device read probe (GATED, device writes): disarm
      (0x0010_0003<-0x00) on a resynced armed device, then re-read status.
      Discriminates armed-state refusal vs deeper desync. Needs the go.
- [ ] d9 test-vehicle work when the #596 slice lands — PRIORITY OVERRIDES
      ALL LOCAL ROWS the moment a request arrives (principal directive
      2026-11-07); rows appear here as they come in.
- [ ] Phase 3: vectors rebuild from a clean capture (needs configure inside
      its budget + the engine running).

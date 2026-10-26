# SDK re-entry checkpoint

Run this after EVERY benchweave-sdk version bump, before any other work on
this plugin. The repo bumped the SDK four times in two weeks (0.12.0 ->
0.18.0 -> 0.21.0 -> 0.22.0) with no fixed re-entry procedure; each bump
re-answered the same questions ad hoc (which files move, which digests
re-pin, which gate proves the composition still holds). This checkpoint is
that procedure. It is software-only: no hardware access, no publishing.

Run it as:

```sh
scripts/sdk_reentry.sh
```

## The four steps

1. **Pin.** `uv sync --extra test` installs the SDK version pinned in
   `pyproject.toml` (`test` extra), then the running `benchweave-sdk
   --version` is compared against the pin.
   *Green:* the two match.
   *A failed step tells you:* every later result would come from an SDK
   that is not the pinned one — fix the environment (or the pin) first;
   nothing below is evidence until this passes.
2. **Migrate.** `benchweave-sdk upgrade .` refreshes the SDK-managed files
   (AGENTS.md, the skills, template docs) to the new version. The step
   first refuses a dirty tree — the upgrade itself refuses it too, but the
   checkpoint names it earlier and without half-running.
   *Green:* upgrade exits 0.
   *A failed step tells you:* either uncommitted work is in the tree
   (commit it first), or the upgrade wrote conflict markers because an
   author edit collided with a template change — resolve both sides,
   commit, and re-run from step 1.
3. **Derive.** `benchweave-sdk derive-ui .` re-derives the class-reference
   presentation set (`ui/`, `presentation.json`, `binding-catalogue.json`)
   from the descriptor under the NEW vendored class reference, re-pinning
   the descriptor digests.
   *Green:* derive exits 0 and the derived files' `descriptor_sha256`
   equals the current descriptor's SHA-256.
   *A failed step tells you:* the descriptor no longer composes under the
   new class reference (a deviation appeared, a contract version moved, a
   profile expectation changed) — read `ui/class-reference.json` for the
   reported deviations and fix the descriptor; never hand-edit the
   derived files to make the step pass.
   A green derive that leaves the tree dirty is NOT a failure: those are
   the derived-file motions the bump causes. Commit them together with
   the pin bump — that diff is the review artefact of the SDK change.
4. **Gates.** `benchweave-sdk check` on the descriptor, then the full
   `pytest` suite with `--junitxml`; counts are read from the XML
   attributes, never from a summary line.
   *Green:* every command exits 0, and the XML reports `failures="0"` and
   `errors="0"` (skips are permitted; the script prints each skip's
   reason). The one expected skip is the end-to-end FPGA-load rehearsal,
   and its reason must name what its absence means — no hardware capture
   in this environment — so a skipped run is never misread as evidence of
   the full load chain.
   *A failed check tells you:* the descriptor broke a schema or semantic
   rule under the new SDK — the message names the rule.
   *A failed suite tells you:* adapter behaviour the SDK mocks pin has
   moved (mock semantics, conformance checks, or the class contracts) —
   the failing test names the surface; fix the adapter or the descriptor,
   never the mock's expectation, unless the SDK changelog says the mock
   itself changed.

## What this checkpoint does not do

- It does not touch hardware, flash anything, or publish anywhere; a green
  checkpoint is offline evidence only, never a hardware qualification.
- It does not decide whether to bump: the pin change in `pyproject.toml`
  is an input, made by the operator, before the checkpoint runs.
- It does not replace review: the committed diff (pin + managed files +
  derived set + any adapter/descriptor repairs) is what gets reviewed.

## Run log

- 2026-10-26, SDK 0.22.0, branch `fix/562-machine-truth` (first run): all
  four steps green, exit 0. Pin 0.22.0 matched; migrate ran the copier
  update to template v0.22.0 (byte-identical no-op at the same version —
  clean tree after); derive re-pinned the descriptor digest
  `73d535e7…`; gates read tests=83 failures=0 errors=0 skipped=1 from
  the junit XML, the skip being the end-to-end rehearsal with its
  no-hardware-capture reason. Two earlier runs of this same checkpoint
  failed correctly and are part of the record: the migrate step refused
  an untracked `uv.lock` (the upgrade gate counts untracked files — the
  lock is now committed), and the migrate step surfaced
  `scaffold_extra_absent` (the documented test environment could not run
  the documented upgrade command — the test extra now carries
  `benchweave-sdk[scaffold]`).

#!/usr/bin/env bash
# SDK re-entry checkpoint (issue #562): pin -> migrate -> derive -> gates.
# See docs/sdk-reentry.md for what each step's failure means.
# Software-only: no hardware access, no publishing.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
export UV_PROJECT_ENVIRONMENT=venv
descriptor="src/logicport_plugin/descriptor.json"
junit="$(mktemp -t sdk-reentry-junit)"

run() { uv run --no-sync --extra test "$@"; }

step() { printf '\n== %s ==\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; exit 1; }
pass() { printf 'PASS  %s\n' "$1"; }

# -- 1. Pin ----------------------------------------------------------------
step "1/4 pin: install the pinned SDK and verify the running version"
uv sync --extra test >/dev/null
pin="$(sed -n 's/.*benchweave-sdk==\([0-9][^"]*\).*/\1/p' pyproject.toml | head -1)"
running="$(run benchweave-sdk --version | sed -n 's/^benchweave-sdk, version //p')"
if [ -z "$pin" ] || [ -z "$running" ]; then
  fail "could not read the pin (${pin:-absent}) or the running version (${running:-absent})"
fi
if [ "$pin" != "$running" ]; then
  fail "pin $pin != running $running — later steps would run an unpinned SDK; fix the environment first"
fi
pass "pin $pin is installed and running"

# -- 2. Migrate --------------------------------------------------------------
step "2/4 migrate: refresh SDK-managed files (benchweave-sdk upgrade)"
if [ -n "$(git status --porcelain)" ]; then
  fail "dirty tree: commit or stash before migrating — the upgrade refuses it too, later and louder"
fi
upgrade_out="$(run benchweave-sdk upgrade . 2>&1)" || {
  printf '%s\n' "$upgrade_out"
  fail "upgrade failed — resolve any conflict markers it wrote, commit, re-run from step 1"
}
printf '%s\n' "$upgrade_out"
pass "managed files refreshed (or already at $pin)"

# -- 3. Derive ---------------------------------------------------------------
step "3/4 derive: re-derive the presentation set under this SDK's class reference"
run benchweave-sdk derive-ui . >/dev/null
actual="$(run python -c "import hashlib,sys;print(hashlib.sha256(open('$descriptor','rb').read()).hexdigest())")"
pinned="$(run python -c "import json;print(json.load(open('src/logicport_plugin/presentation.json'))['descriptor_sha256'])")"
if [ "$actual" != "$pinned" ]; then
  fail "derived descriptor_sha256 $pinned != descriptor $actual — the derivation did not adopt this descriptor; read ui/class-reference.json deviations and fix the descriptor, never the derived files"
fi
pass "derived set pins this descriptor ($actual)"
derived_motion="$(git status --porcelain -- src/logicport_plugin/ui src/logicport_plugin/presentation.json src/logicport_plugin/binding-catalogue.json || true)"
if [ -n "$derived_motion" ]; then
  printf 'NOTE  derived files moved by this SDK bump — commit them with the pin bump:\n%s\n' "$derived_motion"
fi

# -- 4. Gates ----------------------------------------------------------------
step "4/4 gates: descriptor check, full suite (counts from the junit XML)"
run benchweave-sdk check "$descriptor" >/dev/null
pass "benchweave-sdk check $descriptor"
run pytest -q -p no:cacheprovider --junitxml="$junit"
run python - "$junit" <<'PY'
import sys
import xml.etree.ElementTree as ET

root = ET.parse(sys.argv[1]).getroot()
suite = root if root.tag == "testsuite" else root.find("testsuite")
counts = {name: int(suite.get(name, "0")) for name in ("tests", "failures", "errors", "skipped")}
print(f"PASS  pytest: tests={counts['tests']} failures={counts['failures']} "
      f"errors={counts['errors']} skipped={counts['skipped']} (from junitxml attributes)")
if counts["failures"] or counts["errors"]:
    sys.exit(f"FAIL  suite is red: failures={counts['failures']} errors={counts['errors']}")
for skipped in root.iter("skipped"):
    print(f"SKIP  {skipped.get('message', '(no reason recorded)')}")
PY
pass "gates green: no failures, no errors; skips printed above with reasons"

printf '\nSDK re-entry checkpoint GREEN at benchweave-sdk %s.\n' "$pin"
printf 'Offline evidence only — not a hardware qualification, not approval to operate.\n'

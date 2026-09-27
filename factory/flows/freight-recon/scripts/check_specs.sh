#!/usr/bin/env bash
# Script node after the spec stage: every carrier in the plan has an agreed (or
# cached) rate spec for the right carrier that passes speccheck on the current
# shipments. Newly agreed specs are then stored in the spec cache.
# Output: $FLOWSTATE_VAR_specs_check (JSON audit record).
set -euo pipefail
ROOT="${FACTORY_ROOT:?}"
abs() { [[ "$1" = /* ]] && echo "$1" || echo "$ROOT/$1"; }
PY="$ROOT/orchestrator/.venv/bin/python"
export SPEC_CACHE_DIR=""
[ "${FLOWSTATE_VAR_use_spec_cache:-yes}" = yes ] && export SPEC_CACHE_DIR="$(abs "${FLOWSTATE_VAR_spec_cache_dir:?}")"
cd "${FLOWSTATE_FLOW_DIR:?}"
"$PY" - <<'PY'
import json, os, subprocess, sys
from pathlib import Path
from reconlib import speccache
plan = json.loads(Path(os.environ["FLOWSTATE_VAR_contract_plan"]).read_text())
specs = json.loads(Path(os.environ["FLOWSTATE_VAR_rate_specs"]).read_text())
bad = []
for it in plan["contract_items"]:
    c, p = it["carrier"], specs.get(it["carrier"])
    if not p or not os.path.isfile(p):
        bad.append(f"{c}: no agreed rate spec"); continue
    spec = json.load(open(p))
    if spec.get("carrier") != c:
        bad.append(f"{c}: spec at {p} is for carrier {spec.get('carrier')!r}"); continue
    r = subprocess.run([sys.executable, "-m", "reconlib.speccheck", p, "--shipments", it["shipments_path"],
                        "--carrier", c], capture_output=True, text=True)
    if r.returncode:
        bad.append(f"{c}: {r.stdout.strip()[:400]}")
extra = set(specs) - {it["carrier"] for it in plan["contract_items"]}
if extra:
    bad.append(f"specs for unplanned carriers: {sorted(extra)}")
if bad:
    print("rate specs incomplete:\n  - " + "\n  - ".join(bad), file=sys.stderr); sys.exit(1)
stored = {}
if os.environ["SPEC_CACHE_DIR"]:
    for it in plan["contract_items"]:
        if it["carrier"] in plan["to_extract"]:
            stored[it["carrier"]] = str(speccache.store(Path(os.environ["SPEC_CACHE_DIR"]), it["carrier"],
                                                        Path(it["contract_path"]), Path(specs[it["carrier"]]),
                                                        os.environ["FLOWSTATE_RUN_DIR"]))
out = {"_session_id": "script:check_specs", "stage": "check_specs", "carriers": sorted(specs), "rate_specs": specs,
       "from_cache": sorted(plan["from_cache"]), "extracted": plan["to_extract"], "stored_in_cache": stored}
open(os.environ["FLOWSTATE_VAR_specs_check"], "w").write(json.dumps(out, indent=2) + "\n")
print(f"rate specs complete for {len(specs)} carriers ({len(plan['from_cache'])} from cache)", file=sys.stderr)
PY

"""Stage 1b — plan contract extraction: pair every carrier with one contract.

A carrier's contract is the file in the contracts dir whose name starts with
the carrier key (shipments.json ``carrier``) followed by '-' or '.'. Every
carrier that appears on a billing document or in shipments.json must map to
exactly one contract; anything else is a hard failure.

Carriers whose agreed spec is in the spec cache (see speccache.py) are not
re-extracted: the cached spec is copied into the run and recorded in
rate-specs.json straight away. The rest are emitted as the fanout list, as
bare carrier keys (flowstate variables are capped at 16 KB; details live in
the plan file and are resolved by each child's load_item node).

Stdout: FLOWSTATE_OUTPUT_contract_items=<json list of carrier keys to extract>
        FLOWSTATE_OUTPUT_extract_count=<n>
        FLOWSTATE_OUTPUT_rate_specs=<path of rate-specs.json>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from . import money, speccache


def plan(ingest_path: Path, shipments_path: Path, contracts_dir: Path, flow_dir: Path, python: str,
         run_dir: Path, cache_dir: Path | None) -> dict:
    ingest = money.loads(ingest_path.read_text())
    shipments = money.loads(shipments_path.read_text())
    carriers = sorted({d["carrier"] for d in ingest["documents"]} | {s["carrier"] for s in shipments})
    contracts = sorted(p for p in contracts_dir.iterdir()
                       if p.is_file() and p.suffix == ".md" and not p.name.endswith(":Zone.Identifier"))
    errors, items, cached = [], [], {}
    for c in carriers:
        hits = [p for p in contracts if p.name.startswith(c + "-") or p.name.startswith(c + ".")]
        if len(hits) != 1:
            errors.append(f"carrier {c!r}: expected exactly one contract file, found {[p.name for p in hits]}")
            continue
        contract = hits[0].resolve()
        mine = [s for s in shipments if s["carrier"] == c]
        items.append({
            "carrier": c,
            "contract_path": str(contract),
            "contract_file": hits[0].name,
            "contract_sha256": hashlib.sha256(contract.read_bytes()).hexdigest(),
            "spec_cache_key": speccache.key(c, contract),
            "shipments_path": str(shipments_path.resolve()),
            "recon_flow_dir": str(flow_dir.resolve()),
            "recon_python": python,
            "service_levels": ", ".join(sorted({s["service_level"] for s in shipments})),
            "handling_flags": ", ".join(sorted({f for s in shipments for f in s["special_handling"]})),
            "shipment_count": str(len(mine)),
        })
        if cache_dir is not None:
            hit = speccache.lookup(cache_dir, c, contract)
            if hit:
                cached[c] = str(speccache.copy_into_run(hit, run_dir, c))
    if errors:
        raise SystemExit("plan_contracts: " + "; ".join(errors))
    unused = sorted(set(p.name for p in contracts) - {i["contract_file"] for i in items})
    return {"stage": "plan_contracts", "contract_items": items,
            "to_extract": [i["carrier"] for i in items if i["carrier"] not in cached],
            "from_cache": cached, "unused_contracts": unused}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ingest", required=True, type=Path)
    ap.add_argument("--shipments", required=True, type=Path)
    ap.add_argument("--contracts-dir", required=True, type=Path)
    ap.add_argument("--flow-dir", required=True, type=Path)
    ap.add_argument("--python", required=True)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--cache-dir", type=Path, help="spec cache; omit to force fresh extraction")
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args(argv)
    result = plan(a.ingest, a.shipments, a.contracts_dir, a.flow_dir, a.python, a.run_dir, a.cache_dir)
    a.out.write_text(json.dumps({"_session_id": "script:plan_contracts", **result}, indent=2) + "\n")
    specs_file = a.run_dir / "rate-specs.json"
    specs_file.write_text(json.dumps(result["from_cache"], indent=2, sort_keys=True) + "\n")
    print("FLOWSTATE_OUTPUT_contract_items=" + json.dumps(result["to_extract"]))
    print(f"FLOWSTATE_OUTPUT_extract_count={len(result['to_extract'])}")
    print(f"FLOWSTATE_OUTPUT_rate_specs={specs_file}")
    print(f"plan_contracts: {len(result['contract_items'])} contracts; extract {result['to_extract']}; "
          f"from cache {sorted(result['from_cache'])}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Self-check a candidate rate spec (run by extraction workers, and as a gate).

    python -m reconlib.speccheck SPEC.json [--shipments shipments.json --carrier falcon]

Checks: JSON Schema (definitions/rate-spec.json), static references and tier
overlaps, and that pricing never crashes on this carrier's shipments. With
--shipments it also prints a small sample of priced shipments so the worker
can eyeball its encoding against the contract text. Exit 0 = clean.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import money
from .ratespec import SpecError, check_spec, price_shipment

SCHEMA = Path(__file__).resolve().parents[1] / "definitions" / "rate-spec.json"


def problems(spec: dict, shipments: list[dict]) -> list[str]:
    out = []
    try:
        import jsonschema
        v = jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text()))
        for err in sorted(v.iter_errors(json.loads(money.dumps(spec))), key=lambda e: list(e.path)):
            out.append(f"schema: {'/'.join(map(str, err.path)) or '<root>'}: {err.message[:300]}")
    except ImportError:
        out.append("schema: jsonschema not importable; run with the orchestrator venv python")
    if out:
        return out
    out += [f"static: {p}" for p in check_spec(spec)]
    for s in shipments:
        try:
            price_shipment(spec, s)
        except SpecError as e:
            out.append(f"pricing {s['shipment_id']}: {e}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", type=Path)
    ap.add_argument("--shipments", type=Path)
    ap.add_argument("--carrier")
    ap.add_argument("--sample", type=int, default=8)
    a = ap.parse_args(argv)
    try:
        spec = money.loads(a.spec.read_text())
    except (OSError, ValueError) as e:
        print(f"FAIL: cannot read spec: {e}")
        return 1
    ships = []
    if a.shipments:
        ships = [s for s in money.loads(a.shipments.read_text()) if s["carrier"] == (a.carrier or spec.get("carrier"))]
    probs = problems(spec, ships)
    if probs:
        print("FAIL:\n  - " + "\n  - ".join(probs))
        return 1
    print(f"OK: spec is valid; priced {len(ships)} shipments without error.")
    seen = set()
    for s in ships:
        key = (s["service_level"], tuple(s["special_handling"]))
        if key in seen and len(seen) >= a.sample:
            continue
        seen.add(key)
        r = price_shipment(spec, s)
        amt = r.amount if r.status == "determined" else f"UNDETERMINED ({'; '.join(x['reason'] for x in r.reasons)})"
        print(f"  {s['shipment_id']}: {s['billed_weight_kg']} kg, {s['distance_km']} km, {s['service_level']}, "
              f"{s['special_handling'] or '-'} -> {amt}  [{', '.join(c['id'] + '=' + str(c['amount']) for c in r.charges)}]")
        if len(seen) >= a.sample:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Behavioural comparison of independently extracted rate specs.

Two extractions of the same contract "agree" when they PRICE every probe
shipment identically: same determined/undetermined status and, when
determined, the same amount to the paisa. Naming, ordering and wording are
free to differ. Probes are the carrier's real shipments plus a synthetic grid
that crosses every service level and handling flag either spec mentions with
every numeric boundary either spec mentions (+/- small offsets), so an
off-by-one at a tier edge ("under 50" vs "up to 50") cannot hide.

CLI (script node):
    python -m reconlib.specdiff --carrier falcon --shipments S.json \
        --candidates a.json b.json [c.json] --out rate-spec.json --report R.json
Prints FLOWSTATE_OUTPUT_spec_agreed=true|false. Writes --out only on agreement
(majority of the candidates that pass static checks).
"""
from __future__ import annotations

import argparse
import itertools
import sys
from decimal import Decimal
from pathlib import Path

from . import money
from .ratespec import SpecError, _boundaries, check_spec, invoice_adjustments, price_shipment

OFFSETS = [Decimal("-1"), Decimal("-0.5"), Decimal("0"), Decimal("0.5"), Decimal("1")]


def _strings_in_conditions(obj, out=None) -> set[str]:
    out = set() if out is None else out
    if isinstance(obj, dict):
        if obj.get("op") in ("eq", "ne", "contains") and isinstance(obj.get("value"), str):
            out.add(obj["value"])
        if obj.get("op") == "in" and isinstance(obj.get("value"), list):
            out |= {v for v in obj["value"] if isinstance(v, str)}
        for v in obj.values():
            _strings_in_conditions(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _strings_in_conditions(v, out)
    return out


def probes(specs: list[dict], shipments: list[dict]) -> list[dict]:
    real = [dict(s) for s in shipments]
    if not real:
        raise SystemExit("specdiff: no shipments for this carrier to probe with")
    services = {s["service_level"] for s in real}
    flags = {f for s in real for f in s["special_handling"]}
    mentioned = set()
    bounds = set()
    for sp in specs:
        mentioned |= _strings_in_conditions(sp)
        services |= set((sp.get("services_offered") or {}).get("values") or [])
        bounds |= _boundaries(sp)
    flags |= {m for m in mentioned if m not in services}
    numeric = sorted({b + o for b in bounds for o in OFFSETS if b + o > 0}) or [Decimal(1)]
    base = real[0]
    synth = []
    handling_options = [[]] + [[f] for f in sorted(flags)]
    for svc, handling in itertools.product(sorted(services), handling_options):
        for x in numeric:
            synth.append({**base, "service_level": svc, "special_handling": handling, "billed_weight_kg": x})
            synth.append({**base, "service_level": svc, "special_handling": handling, "distance_km": x})
    return real + synth


def _outcome(spec: dict, shipment: dict):
    try:
        r = price_shipment(spec, shipment)
    except SpecError as e:  # a spec that breaks on some input cannot agree with anything
        return ("spec_error", str(e))
    return (r.status, r.amount)


def compare_pair(a: dict, b: dict, probe_set: list[dict], limit: int = 20) -> list[dict]:
    diffs = []
    for p in probe_set:
        oa, ob = _outcome(a, p), _outcome(b, p)
        if oa != ob:
            diffs.append({"probe": {k: p.get(k) for k in ("shipment_id", "billed_weight_kg", "distance_km",
                                                           "service_level", "special_handling", "ship_date")},
                          "a": {"status": oa[0], "amount": oa[1]}, "b": {"status": ob[0], "amount": ob[1]}})
            if len(diffs) >= limit:
                break
    # Invoice-level adjustments: sweep the monthly consignment count.
    top = int(max([*(_boundaries(a.get("invoice_adjustments", [])) | _boundaries(b.get("invoice_adjustments", []))),
                   Decimal(0)])) + 3
    for n in range(0, top + 1):
        m = {"consignments_in_calendar_month": n, "invoice_line_total": Decimal("10000.00")}
        try:
            ia = sorted(x["amount"] for x in invoice_adjustments(a, m))
            ib = sorted(x["amount"] for x in invoice_adjustments(b, m))
        except SpecError as e:
            diffs.append({"invoice_adjustments": f"error at n={n}: {e}"})
            break
        if ia != ib:
            diffs.append({"invoice_adjustments": {"consignments": n, "a": ia, "b": ib}})
            break
    for key in ("carrier",):
        if a.get(key) != b.get(key):
            diffs.append({"field": key, "a": a.get(key), "b": b.get(key)})
    if bool((a.get("accessorials_policy") or {}).get("closed_list")) != bool((b.get("accessorials_policy") or {}).get("closed_list")):
        diffs.append({"field": "accessorials_policy.closed_list",
                      "a": (a.get("accessorials_policy") or {}).get("closed_list"),
                      "b": (b.get("accessorials_policy") or {}).get("closed_list")})
    return diffs


def decide(candidates: dict[str, dict], shipments: list[dict]) -> dict:
    """Return {agreed, chosen, static_problems, pairs}. ``chosen`` is the name of
    a candidate that agrees with at least one other valid candidate (majority)."""
    static = {name: check_spec(sp) for name, sp in candidates.items()}
    valid = {n: sp for n, sp in candidates.items() if not static[n]}
    probe_set = probes(list(valid.values()) or list(candidates.values()), shipments)
    pairs = {}
    for (na, a), (nb, b) in itertools.combinations(valid.items(), 2):
        pairs[f"{na}~{nb}"] = compare_pair(a, b, probe_set)
    agreeing = [k for k, d in pairs.items() if not d]
    chosen = agreeing[0].split("~")[0] if agreeing else None
    return {"agreed": chosen is not None, "chosen": chosen, "probe_count": len(probe_set),
            "static_problems": static, "pairs": pairs}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--carrier", required=True)
    ap.add_argument("--shipments", required=True, type=Path)
    ap.add_argument("--candidates", required=True, nargs="+", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--report", required=True, type=Path)
    a = ap.parse_args(argv)

    shipments = [s for s in money.loads(a.shipments.read_text()) if s["carrier"] == a.carrier]
    candidates = {}
    for p in a.candidates:
        if p.exists():
            candidates[p.stem] = money.loads(p.read_text())
    result = decide(candidates, shipments)
    result.update(carrier=a.carrier, candidates=sorted(candidates))
    a.report.write_text(money.dumps({"_session_id": "script:specdiff", **result}, indent=2) + "\n")
    if result["agreed"]:
        spec = dict(candidates[result["chosen"]])
        spec["_session_id"] = "script:specdiff"
        spec["_provenance"] = {"chosen": result["chosen"], "agreed_with": [k for k, d in result["pairs"].items()
                                                                          if not d and result["chosen"] in k],
                               "probe_count": result["probe_count"]}
        a.out.write_text(money.dumps(spec, indent=2) + "\n")
    print(f"FLOWSTATE_OUTPUT_spec_agreed={'true' if result['agreed'] else 'false'}")
    print(f"specdiff[{a.carrier}]: candidates={sorted(candidates)} agreed={result['agreed']} "
          f"chosen={result['chosen']} probes={result['probe_count']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

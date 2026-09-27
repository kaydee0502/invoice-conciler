"""Stage 3 — match: invoice lines -> shipments, plus fact conflicts.

Matching is exact on (carrier, normalized consignment ref) against
shipments.json ``carrier_consignment_ref``. Nothing fuzzy: a line that does
not match exactly stays unmatched (shipment_id null). Near misses are
recorded as ``hints`` for the adjudicator but never used to match.

For matched lines, every fact the carrier printed is compared with BlueFin's
shipment record (the authority on facts). Conflicts are recorded, never
resolved here; each is tagged ``pricing`` when the shipment field feeds the
rate engine, ``informational`` otherwise.

Credit-note lines are matched to the shipment of the consignment they
correct AND linked to the invoice line(s) they correct.

Usage: python -m reconlib.match --ingest I.json --shipments S.json --out M.json
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

from . import money

# printed fact -> (shipment field path, comparison kind, materiality)
FACT_MAP = {
    "weight_kg":        (("billed_weight_kg",), "number", "pricing"),
    "actual_weight_kg": (("billed_weight_kg",), "number", "pricing"),
    "distance_km":      (("distance_km",), "number", "pricing"),
    "service_level":    (("service_level",), "text", "pricing"),
    "origin_city":      (("origin", "city"), "text", "informational"),
    "destination_city": (("destination", "city"), "text", "informational"),
    "booking_date":     (("ship_date",), "text", "informational"),
}
# Printed facts that are the carrier's own pricing inputs, not shipment facts;
# the price stage compares them with the rate spec instead.
NOT_SHIPMENT_FACTS = {"chargeable_weight_kg", "rate_per_kg"}


def norm_ref(ref: str) -> str:
    return "".join(ref.split()).upper()


def _get(d: dict, path: tuple):
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def _same(kind: str, a, b) -> bool:
    if kind == "number":
        return money.to_decimal(a) == money.to_decimal(b)
    return str(a).strip().casefold() == str(b).strip().casefold()


def fact_conflicts(line: dict, shipment: dict) -> tuple[list[dict], list[str]]:
    conflicts, unknown = [], []
    for fact, printed in line["facts"].items():
        if fact in NOT_SHIPMENT_FACTS:
            continue
        if fact not in FACT_MAP:
            unknown.append(fact)
            continue
        path, kind, materiality = FACT_MAP[fact]
        recorded = _get(shipment, path)
        if recorded is None:
            unknown.append(fact)
            continue
        if not _same(kind, printed, recorded):
            conflicts.append({"fact": fact, "invoice": printed, "shipment": recorded,
                              "shipment_field": ".".join(path), "materiality": materiality})
    return conflicts, unknown


def _near(a: str, b: str) -> bool:
    """One substitution, insertion, deletion, or adjacent transposition apart."""
    if a == b:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        return (len(diff) == 2 and diff[1] == diff[0] + 1
                and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    if abs(len(a) - len(b)) == 1:
        short, long_ = sorted((a, b), key=len)
        return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))
    return False


def build(ingest: dict, shipments: list[dict]) -> dict:
    by_ref: dict[tuple[str, str], list[dict]] = defaultdict(list)
    by_ref_any: dict[str, list[dict]] = defaultdict(list)
    for s in shipments:
        by_ref[(s["carrier"], norm_ref(s["carrier_consignment_ref"]))].append(s)
        by_ref_any[norm_ref(s["carrier_consignment_ref"])].append(s)
    dup_refs = {k: [s["shipment_id"] for s in v] for k, v in by_ref.items() if len(v) > 1}

    invoice_lines_by_ref: dict[tuple[str, str], list[str]] = defaultdict(list)
    for ln in ingest["lines"]:
        if ln["doc_type"] == "invoice":
            invoice_lines_by_ref[(ln["doc_id"], norm_ref(ln["consignment_ref"]))].append(ln["line_key"])

    out_lines, billed_shipments, unknown_facts = [], defaultdict(list), set()
    for ln in ingest["lines"]:
        ref = norm_ref(ln["consignment_ref"])
        cands = by_ref.get((ln["carrier"], ref), [])
        rec = {"line_key": ln["line_key"], "doc_id": ln["doc_id"], "doc_type": ln["doc_type"],
               "carrier": ln["carrier"], "consignment_ref": ln["consignment_ref"],
               "shipment_id": None, "match": None, "fact_conflicts": [], "hints": [],
               "shipment": None, "corrects_line_keys": None}
        if len(cands) == 1:
            s = cands[0]
            rec["shipment_id"] = s["shipment_id"]
            rec["match"] = "exact_ref"
            rec["shipment"] = s
            conflicts, unknown = fact_conflicts(ln, s)
            rec["fact_conflicts"] = conflicts
            unknown_facts |= set(unknown)
            if ln["doc_type"] == "invoice":
                billed_shipments[s["shipment_id"]].append(ln["line_key"])
            if s.get("delivery_status") != "delivered":
                rec["hints"].append({"kind": "not_delivered",
                                     "detail": f"shipment {s['shipment_id']} delivery_status is {s.get('delivery_status')!r}"})
        elif len(cands) > 1:
            rec["match"] = "ambiguous_ref"
            rec["hints"].append({"kind": "ambiguous_ref",
                                 "detail": f"ref matches several shipments: {[s['shipment_id'] for s in cands]}"})
        else:
            rec["match"] = "unmatched"
            other = [s for s in by_ref_any.get(ref, []) if s["carrier"] != ln["carrier"]]
            if other:
                rec["hints"].append({"kind": "ref_under_other_carrier",
                                     "detail": f"ref exists for carrier(s) {sorted({s['carrier'] for s in other})}"})
        if ln["corrects"]:
            tgt = ln["corrects"]
            keys = invoice_lines_by_ref.get((tgt["invoice"], norm_ref(tgt["consignment_ref"])), [])
            rec["corrects_line_keys"] = keys
        out_lines.append(rec)

    # Near-miss hints for unmatched lines (never used to match). Computed after
    # the pass so each candidate can say whether it was billed on its own.
    refs_by_carrier = defaultdict(list)
    for s in shipments:
        refs_by_carrier[s["carrier"]].append(s)
    for rec in out_lines:
        if rec["match"] != "unmatched":
            continue
        ref = norm_ref(rec["consignment_ref"])
        for s in refs_by_carrier.get(rec["carrier"], []):
            if _near(ref, norm_ref(s["carrier_consignment_ref"])):
                billed_on = billed_shipments.get(s["shipment_id"], [])
                rec["hints"].append({
                    "kind": "near_ref",
                    "detail": (f"ref is one edit from {s['carrier_consignment_ref']} ({s['shipment_id']}, "
                               f"delivery_status {s.get('delivery_status')!r}), which is "
                               + (f"billed on {billed_on}" if billed_on else "not billed on any document")),
                    "delivery_status": s.get("delivery_status"),
                    "shipment_id": s["shipment_id"], "billed_on": billed_on,
                })

    carriers_billed = {ln["carrier"] for ln in ingest["lines"]}
    unbilled = sorted(s["shipment_id"] for s in shipments
                      if s["carrier"] in carriers_billed and s["shipment_id"] not in billed_shipments)
    counts = defaultdict(int)
    for r in out_lines:
        counts[r["match"]] += 1
    return {
        "stage": "match",
        "line_count": len(out_lines),
        "counts": dict(counts),
        "lines": out_lines,
        "shipments_billed_more_than_once": {k: v for k, v in billed_shipments.items() if len(v) > 1},
        "unbilled_shipments": unbilled,
        "duplicate_shipment_refs": {f"{c}:{r}": ids for (c, r), ids in dup_refs.items()},
        "unmapped_facts": sorted(unknown_facts),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ingest", required=True, type=Path)
    ap.add_argument("--shipments", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args(argv)
    ingest = money.loads(a.ingest.read_text())
    result = build(ingest, money.loads(a.shipments.read_text()))
    if result["line_count"] != ingest["line_count"]:
        raise SystemExit(f"match: {result['line_count']} lines out, {ingest['line_count']} in")
    if result["unmapped_facts"]:
        # A printed fact we don't know how to compare is a system gap, not data: refuse.
        raise SystemExit(f"match: no comparison rule for printed facts {result['unmapped_facts']}; "
                         "add them to FACT_MAP or NOT_SHIPMENT_FACTS")
    tmp = a.out.with_suffix(a.out.suffix + ".tmp")
    tmp.write_text(money.dumps({"_session_id": "script:match", **result}, indent=2) + "\n")
    tmp.replace(a.out)
    print(f"match: {dict(result['counts'])}; unbilled shipments: {len(result['unbilled_shipments'])}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

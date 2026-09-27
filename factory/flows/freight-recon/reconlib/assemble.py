"""Stage 6 — assemble: price (+ adjudication) -> reconciliation-report.json.

The report is built only from what earlier stages computed; this stage adds
no judgement. It is also the last line of defence before anything is
published, so it refuses (exit 1, nothing written) unless every invariant
holds:

  I1  report.schema.json validates
  I2  every ingested line appears exactly once, in source order
  I3  delta == billed - expected exactly; both null together; null => escalate
  I4  invoice billed_total == the document's printed total
  I5  expected_total == sum(expected lines) + expected adjustment; null iff any part is
  I6  summary figures re-add from lines/findings/totals
  I7  no rupee disputed twice: a credit line and the line it corrects are never
      both disputed, and a disputed adjustment is never derivative
  I8  every ingest anomaly and every invoice-vs-shipment fact conflict is
      recorded on its line or on an invoice finding
  I9  adjudication, if any, only decides lines under review, only within
      their allowed dispositions, and covers all of them

Also writes report-index.json: one entry per non-accept item with a stable
item id, the memo filename it must get, and the facts a memo may quote.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

from . import money
from .money import q

TOL = Decimal("0.00")   # report amounts are paise-exact


class InvariantError(Exception):
    pass


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-")


def apply_adjudication(lines: list[dict], adjudication: dict | None) -> list[str]:
    """Apply adjudicated dispositions in place. Returns the list of line keys decided."""
    review = {r["line_key"]: r for r in lines if r["review"]}
    if not review:
        if adjudication and adjudication.get("decisions"):
            raise InvariantError("I9: adjudication supplied decisions but no line is under review")
        return []
    if adjudication is None:
        raise InvariantError(f"I9: {len(review)} line(s) under review but no adjudication supplied")
    seen = set()
    for d in adjudication["decisions"]:
        k = d["line_key"]
        if k not in review:
            raise InvariantError(f"I9: adjudication decides {k}, which is not under review")
        if k in seen:
            raise InvariantError(f"I9: adjudication decides {k} twice")
        allowed = review[k]["allowed_dispositions"] or []
        if d["disposition"] not in allowed:
            raise InvariantError(f"I9: {k}: disposition {d['disposition']!r} not in allowed {allowed}")
        seen.add(k)
        r = review[k]
        r["notes"] = list(r["notes"]) + [f"rule {r['rule']} proposed {r['disposition']}; adjudicated {d['disposition']}"]
        r["disposition"] = d["disposition"]
        r["justification"] = d["justification"]
        if d.get("contract_clause"):
            r["contract_clause"] = d["contract_clause"]
        r["rule"] = r["rule"] + "+ADJUDICATED"
    missing = sorted(set(review) - seen)
    if missing:
        raise InvariantError(f"I9: adjudication omits lines under review: {missing}")
    return sorted(seen)


def build(ingest: dict, price: dict, adjudication: dict | None) -> tuple[dict, dict]:
    lines = [dict(r) for r in price["lines"]]
    decided = apply_adjudication(lines, adjudication)
    ing_lines = {l["line_key"]: l for l in ingest["lines"]}

    # I2
    if [r["line_key"] for r in lines] != [l["line_key"] for l in ingest["lines"]]:
        raise InvariantError("I2: priced lines do not correspond 1:1, in order, to ingested lines")

    # I8: fold anomalies and fact conflicts into notes so nothing is dropped
    anomalies_by_line: dict[str, list[dict]] = {}
    for a in ingest["anomalies"]:
        if a["line_key"]:
            anomalies_by_line.setdefault(a["line_key"], []).append(a)
    finding_codes = {c for f in price["invoice_findings"] for c in f.get("anomaly_codes", [])}
    for a in ingest["anomalies"]:
        if not a["line_key"] and a["code"] not in finding_codes:
            raise InvariantError(f"I8: document anomaly {a['code']} on {a['doc_id']} is not carried by any finding")
    for r in lines:
        for a in anomalies_by_line.get(r["line_key"], []):
            r["notes"] = list(r["notes"]) + [f"anomaly {a['code']}: {a['detail']}"]
        for c in r.get("fact_conflicts", []):
            txt = f"{c['fact']} invoice {c['invoice']} vs shipment {c['shipment']}"
            if not any(txt in n for n in r["notes"]):
                r["notes"] = list(r["notes"]) + [f"fact conflict ({c['materiality']}): {txt}"]
        if r.get("hints") and r["shipment_id"] is None:
            for h in r["hints"]:
                if h["detail"] not in r["justification"] and not any(h["detail"] in n for n in r["notes"]):
                    r["notes"] = list(r["notes"]) + [f"hint: {h['detail']}"]

    # I3
    for r in lines:
        b, e, d = r["billed_amount"], r["expected_amount"], r["delta"]
        if (e is None) != (d is None):
            raise InvariantError(f"I3: {r['line_key']}: expected/delta nullness differs")
        if e is not None and q(b - e) != q(d):
            raise InvariantError(f"I3: {r['line_key']}: delta {d} != billed {b} - expected {e}")
        if e is None and r["disposition"] != "escalate":
            raise InvariantError(f"I3: {r['line_key']}: no expected amount but disposition {r['disposition']}")
        if not r["justification"].strip():
            raise InvariantError(f"I3: {r['line_key']}: empty justification")

    # I7 (lines)
    by_key = {r["line_key"]: r for r in lines}
    for r in lines:
        if r["doc_type"] == "credit_note" and r["disposition"] == "dispute":
            for k in r.get("corrects_line_keys") or []:
                if by_key.get(k, {}).get("disposition") == "dispute":
                    raise InvariantError(f"I7: {r['line_key']} and the line it corrects ({k}) are both disputed")
    for f in price["invoice_findings"]:
        if f["disposition"] == "dispute" and f["rule"] == "R_ADJ_DERIVATIVE":
            raise InvariantError(f"I7: derivative adjustment on {f['invoice']} is disputed")

    # Totals (I4, I5)
    findings_by_doc: dict[str, list[dict]] = {}
    for f in price["invoice_findings"]:
        findings_by_doc.setdefault(f["invoice"], []).append(f)
    totals = []
    for doc in ingest["documents"]:
        dl = [by_key[k] for k in doc["line_keys"]]
        billed_lines = sum((ing_lines[k]["billed_amount"] for k in doc["line_keys"]), Decimal(0))
        printed_adj = sum((a["amount"] for a in doc["header_adjustments"]), Decimal(0))
        billed_total = q(doc["printed_total"])
        has_total_finding = any(f["kind"] == "DOC_TOTAL_MISMATCH" for f in findings_by_doc.get(doc["doc_id"], []))
        if q(billed_lines + printed_adj) != billed_total and not has_total_finding:
            raise InvariantError(f"I4: {doc['doc_id']}: printed total {billed_total} != lines + adjustments "
                                 f"{q(billed_lines + printed_adj)} and no finding records it")
        notes = []
        undetermined = [r["line_key"] for r in dl if r["expected_amount"] is None]
        adj = [f for f in findings_by_doc.get(doc["doc_id"], []) if f["kind"] == "INVOICE_ADJUSTMENT"]
        if undetermined:
            expected_total = None
            notes.append(f"expected total undeterminable: no contract amount for {undetermined}")
        elif any(f.get("expected_adjustment") is None for f in adj):
            expected_total = None
            notes.append("expected total undeterminable: invoice-level adjustment could not be evaluated")
        else:
            exp_adj = sum((f["expected_adjustment"] for f in adj), Decimal(0))
            expected_total = q(sum((r["expected_amount"] for r in dl), Decimal(0)) + exp_adj)
            if exp_adj:
                notes.append(f"includes contract invoice-level adjustment {exp_adj}")
        if printed_adj:
            notes.append(f"billed total includes printed adjustment {q(printed_adj)}")
        t = {"invoice": doc["doc_id"], "billed_total": billed_total, "expected_total": expected_total}
        if notes:
            t["notes"] = "; ".join(notes)
        totals.append(t)

    # Report objects, schema-shaped
    report_lines = []
    for r in lines:
        o = {"invoice": r["invoice"], "consignment_ref": r["consignment_ref"], "shipment_id": r["shipment_id"],
             "billed_amount": q(r["billed_amount"]),
             "expected_amount": None if r["expected_amount"] is None else q(r["expected_amount"]),
             "delta": None if r["delta"] is None else q(r["delta"]),
             "disposition": r["disposition"], "justification": r["justification"],
             "contract_clause": r["contract_clause"]}
        if r["notes"]:
            o["notes"] = "; ".join(r["notes"])
        report_lines.append(o)
    report_findings = [{"invoice": f["invoice"], "description": f["description"],
                        "amount_impact": None if f["amount_impact"] is None else q(f["amount_impact"]),
                        "disposition": f["disposition"], "justification": f["justification"],
                        "contract_clause": f.get("contract_clause")} for f in price["invoice_findings"]]

    # Summary (I6)
    counts = {"accept": 0, "dispute": 0, "escalate": 0}
    for o in report_lines:
        counts[o["disposition"]] += 1
    in_dispute = sum((abs(o["delta"]) for o in report_lines if o["disposition"] == "dispute" and o["delta"] is not None),
                     Decimal(0))
    in_dispute += sum((abs(f["amount_impact"]) for f in report_findings
                       if f["disposition"] == "dispute" and f["amount_impact"] is not None), Decimal(0))
    total_expected = (None if any(t["expected_total"] is None for t in totals)
                      else q(sum((t["expected_total"] for t in totals), Decimal(0))))
    summary = {"total_billed": q(sum((t["billed_total"] for t in totals), Decimal(0))),
               "total_expected": total_expected, "total_in_dispute": q(in_dispute),
               "line_count": len(report_lines), "counts_by_disposition": counts}
    if summary["line_count"] != ingest["line_count"] or sum(counts.values()) != summary["line_count"]:
        raise InvariantError("I6: line counts do not re-add")

    report = {"lines": report_lines, "invoice_findings": report_findings, "invoice_totals": totals,
              "summary": summary}

    # Memo index: every non-accept line and finding, with a stable id and file name.
    items = []
    for r, o in zip(lines, report_lines):
        if o["disposition"] == "accept":
            continue
        items.append({"item_id": f"line:{r['line_key']}", "kind": "line", "carrier": r["carrier"],
                      "memo_file": f"{_slug(r['invoice'])}__{_slug(r['consignment_ref'])}.md",
                      "report_entry": o, "rule": r["rule"], "reasons": r["reasons"],
                      "expected_charges": r.get("expected_charges", []),
                      "billed_components": r.get("billed_components", []),
                      "corrects_line_keys": r.get("corrects_line_keys"), "hints": r.get("hints", [])})
    for f, o in zip(price["invoice_findings"], report_findings):
        if o["disposition"] == "accept":
            continue
        carrier = next(d["carrier"] for d in ingest["documents"] if d["doc_id"] == f["invoice"])
        items.append({"item_id": f"finding:{f['invoice']}:{f['kind']}", "kind": "finding", "carrier": carrier,
                      "memo_file": f"{_slug(f['invoice'])}__{_slug(f['kind'].lower())}.md",
                      "report_entry": o, "rule": f["rule"],
                      "details": {k: v for k, v in f.items() if k not in o}})
    files = [i["memo_file"] for i in items]
    if len(files) != len(set(files)):
        raise InvariantError(f"memo file names collide: {sorted(x for x in files if files.count(x) > 1)}")
    index = {"stage": "assemble", "adjudicated_lines": decided, "memo_items": items,
             "memo_item_count": len(items)}
    return report, index


def validate_schema(report: dict, schema_path: Path) -> None:
    import jsonschema
    schema = json.loads(schema_path.read_text())
    errs = sorted(jsonschema.Draft7Validator(schema).iter_errors(json.loads(money.dumps(report))),
                  key=lambda e: list(e.path))
    if errs:
        raise InvariantError("I1: report.schema.json: " + "; ".join(
            f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message[:200]}" for e in errs[:10]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ingest", required=True, type=Path)
    ap.add_argument("--price", required=True, type=Path)
    ap.add_argument("--adjudication", type=Path)
    ap.add_argument("--schema", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--index", required=True, type=Path)
    a = ap.parse_args(argv)
    ingest = money.loads(a.ingest.read_text())
    price = money.loads(a.price.read_text())
    adj = money.loads(a.adjudication.read_text()) if a.adjudication and a.adjudication.is_file() else None
    try:
        report, index = build(ingest, price, adj)
        validate_schema(report, a.schema)
    except InvariantError as e:
        raise SystemExit(f"assemble: invariant violated, report NOT written: {e}")
    for path, obj in ((a.out, report), (a.index, {"_session_id": "script:assemble", **index})):
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(money.dumps(obj, indent=2, ensure_ascii=False) + "\n")
        tmp.replace(path)
    s = report["summary"]
    print(f"assemble: {s['line_count']} lines {s['counts_by_disposition']}; billed {s['total_billed']}, "
          f"expected {s['total_expected']}, in dispute {s['total_in_dispute']}; "
          f"{index['memo_item_count']} memo items", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Stage 1 — ingest: every billing document -> normalized lines + anomalies.

Two kinds of problem, deliberately kept apart:

* **Hard failures** mean the SYSTEM could not account for the input (an
  unclaimed file, an unparseable line, a duplicate document id). Nothing is
  written and the process exits non-zero: downstream stages must never see a
  partial picture of what was billed.
* **Anomalies** mean the CARRIER's document is internally inconsistent (line
  charges that don't sum to the line total, lines that don't sum to the
  printed total, duplicate consignments, credit notes pointing at nothing).
  Those are findings, not crashes — they are recorded with the amounts
  involved and flow downstream to be adjudicated.

Usage: python -m reconlib.ingest --invoices-dir DIR --out FILE
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from . import money
from .adapters import ParseError, read_text, route

IGNORED_SUFFIXES = (":Zone.Identifier",)  # Windows download metadata streams


def line_key(doc_id: str, line_no: int) -> str:
    return f"{doc_id}#{line_no}"


def discover(invoices_dir: Path) -> tuple[list[Path], list[str]]:
    files, skipped = [], []
    for p in sorted(invoices_dir.iterdir()):
        if p.name.startswith(".") or p.name.endswith(IGNORED_SUFFIXES) or not p.is_file():
            skipped.append(p.name)
            continue
        files.append(p)
    return files, skipped


def parse_all(files: list[Path]) -> tuple[list[dict], list[str], dict]:
    docs, errors, fingerprints = [], [], {}
    for p in files:
        raw = p.read_bytes()
        fingerprints[p.name] = hashlib.sha256(raw).hexdigest()
        try:
            text = read_text(p)
            adapter = route(p, text)
            doc = adapter.parse(p, text)
            doc["adapter"] = adapter.name
            docs.append(doc)
        except ParseError as e:
            errors.append(str(e))
        except Exception as e:  # an adapter bug is still a hard failure, with context
            errors.append(f"{p.name}: adapter crashed: {type(e).__name__}: {e}")
    seen = defaultdict(list)
    for d in docs:
        seen[d["doc_id"]].append(d["source_file"])
    for doc_id, srcs in seen.items():
        if len(srcs) > 1:
            errors.append(f"document id {doc_id} appears in several files: {srcs}")
    return docs, errors, fingerprints


def find_anomalies(docs: list[dict]) -> list[dict]:
    out: list[dict] = []

    def add(code, doc_id, detail, line=None, **amounts):
        out.append({"code": code, "doc_id": doc_id, "line_key": line, "detail": detail,
                    **{k: v for k, v in amounts.items()}})

    by_id = {d["doc_id"]: d for d in docs}
    refs_by_invoice = {d["doc_id"]: {ln["consignment_ref"] for ln in d["lines"]}
                       for d in docs if d["doc_type"] == "invoice"}
    first_seen: dict[tuple[str, str], str] = {}

    for d in docs:
        lines = d["lines"]
        line_sum = sum((ln["billed_amount"] for ln in lines), Decimal(0))
        adj_sum = sum((a["amount"] for a in d["header_adjustments"]), Decimal(0))
        if not money.same_money(line_sum + adj_sum, d["printed_total"]):
            add("DOC_TOTAL_MISMATCH", d["doc_id"],
                "sum of line totals plus header adjustments differs from the printed document total",
                computed_total=money.q(line_sum + adj_sum), printed_total=d["printed_total"],
                difference=money.q(d["printed_total"] - (line_sum + adj_sum)))
        if d["stated_line_count"] is not None and d["stated_line_count"] != len(lines):
            add("STATED_COUNT_MISMATCH", d["doc_id"],
                f"document states {d['stated_line_count']} consignments but lists {len(lines)}")

        refs_in_doc = defaultdict(list)
        for ln in lines:
            key = line_key(d["doc_id"], ln["line_no"])
            refs_in_doc[ln["consignment_ref"]].append(key)
            comp_sum = sum((c["amount"] for c in ln["components"]), Decimal(0))
            if ln["components"] and not money.same_money(comp_sum, ln["billed_amount"]):
                add("LINE_COMPONENTS_MISMATCH", d["doc_id"],
                    "printed charge components do not sum to the printed line total", line=key,
                    components_sum=money.q(comp_sum), billed_amount=ln["billed_amount"],
                    difference=money.q(ln["billed_amount"] - comp_sum))
            if d["doc_type"] == "invoice":
                k = (d["carrier"], ln["consignment_ref"])
                if k in first_seen and first_seen[k].split("#")[0] != d["doc_id"]:
                    add("DUPLICATE_REF_ACROSS_DOCS", d["doc_id"],
                        f"consignment {ln['consignment_ref']} already billed on {first_seen[k]}",
                        line=key, first_billed_on=first_seen[k])
                first_seen.setdefault(k, key)
            if ln["corrects"]:
                tgt = ln["corrects"]["invoice"]
                if tgt not in by_id:
                    add("CREDIT_TARGET_UNKNOWN", d["doc_id"],
                        f"credit line corrects invoice {tgt}, which is not among the documents", line=key)
                elif ln["consignment_ref"] not in refs_by_invoice.get(tgt, set()):
                    add("CREDIT_TARGET_LINE_MISSING", d["doc_id"],
                        f"credit line corrects {ln['consignment_ref']} on {tgt}, but that invoice has no such consignment",
                        line=key)
        for ref, keys in refs_in_doc.items():
            if len(keys) > 1:
                add("DUPLICATE_REF_IN_DOC", d["doc_id"],
                    f"consignment {ref} appears {len(keys)} times on this document", line=keys[1],
                    all_lines=keys)
    return out


def build(invoices_dir: Path) -> dict:
    files, skipped = discover(invoices_dir)
    if not files:
        raise SystemExit(f"ingest: no billing documents found in {invoices_dir}")
    docs, errors, fingerprints = parse_all(files)
    if errors:
        raise SystemExit(f"ingest: {len(errors)} hard failure(s), refusing to produce a partial ingest:\n  - "
                         + "\n  - ".join(errors))

    anomalies = find_anomalies(docs)
    lines = []
    documents = []
    for d in sorted(docs, key=lambda d: (d["carrier"], d["doc_type"], d["doc_id"])):
        keys = []
        for ln in d["lines"]:
            key = line_key(d["doc_id"], ln["line_no"])
            keys.append(key)
            lines.append({"line_key": key, "carrier": d["carrier"], "doc_type": d["doc_type"], **ln})
        header = {k: v for k, v in d.items() if k != "lines"}
        header["line_keys"] = keys
        header["line_count"] = len(keys)
        documents.append(header)

    return {
        "stage": "ingest",
        "input_dir": str(invoices_dir),
        "input_fingerprints": fingerprints,
        "skipped_files": skipped,
        "document_count": len(documents),
        "line_count": len(lines),
        "documents": documents,
        "lines": lines,
        "anomalies": anomalies,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--invoices-dir", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--session-id", default="script:ingest")
    a = ap.parse_args(argv)
    try:
        result = {"_session_id": a.session_id, **build(a.invoices_dir)}
    except SystemExit as e:
        # Keep the full list next to the would-be output; stderr gets truncated upstream.
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.with_name("ingest-errors.txt").write_text(f"{e}\n")
        raise
    a.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = a.out.with_suffix(a.out.suffix + ".tmp")
    tmp.write_text(money.dumps(result, indent=2) + "\n")
    tmp.replace(a.out)  # atomic: downstream never sees a half-written file
    print(f"ingest: {result['document_count']} documents, {result['line_count']} lines, "
          f"{len(result['anomalies'])} anomalies -> {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

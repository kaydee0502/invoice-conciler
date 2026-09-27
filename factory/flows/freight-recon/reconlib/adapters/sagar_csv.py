"""Sagar Roadlines: CSV exports (CRLF), one row per c-note plus a TOTAL row.

Two layouts, told apart by the header row:
    invoice:     cnote_no,booking_dt,wt_kg,dist_km,freight_rs,chill_prem_rs,total_rs
    credit note: credit_note,against_invoice,cnote_no,credit_rs

The invoice layout carries no invoice number, so the invoice id is the file
stem (SAGAR-AUG-1). Sagar's credit notes cite invoices by exactly that stem,
which is what makes this safe; ``doc_id_source`` records the provenance.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from ..money import MoneyParseError, parse_amount
from . import Adapter, ParseError

CARRIER = "sagar"
INVOICE_COLS = ["cnote_no", "booking_dt", "wt_kg", "dist_km", "freight_rs", "chill_prem_rs", "total_rs"]
CREDIT_COLS = ["credit_note", "against_invoice", "cnote_no", "credit_rs"]


def _header(text: str) -> list[str]:
    first = text.lstrip().splitlines()[:1]
    return [c.strip() for c in first[0].split(",")] if first else []


def sniff(path: Path, text: str) -> bool:
    return _header(text) in (INVOICE_COLS, CREDIT_COLS)


def _amt(f, loc, raw):
    try:
        return parse_amount(raw)
    except MoneyParseError as e:
        raise ParseError(f, loc, str(e)) from e


def _rows(f: str, text: str, cols: list[str]):
    reader = csv.reader(io.StringIO(text))
    next(reader)  # header, already matched by sniff
    body, total = [], None
    for i, row in enumerate(reader, start=2):
        loc = f"row {i}"
        if not any(c.strip() for c in row):
            continue
        if len(row) != len(cols):
            raise ParseError(f, loc, f"expected {len(cols)} columns, got {len(row)}: {row}")
        if total is not None:
            raise ParseError(f, loc, "data after the TOTAL row")
        if row[0].strip().upper() == "TOTAL":
            if any(c.strip() for c in row[1:-1]):
                raise ParseError(f, loc, f"TOTAL row has unexpected values: {row}")
            total = _amt(f, loc, row[-1])
            continue
        body.append((loc, dict(zip(cols, (c.strip() for c in row)))))
    if total is None:
        raise ParseError(f, "eof", "no TOTAL row")
    return body, total


def parse(path: Path, text: str) -> dict:
    f = path.name
    cols = _header(text)
    doc = {
        "doc_id": None, "doc_type": None, "carrier": CARRIER, "source_file": f,
        "format": "sagar_csv", "period": None, "doc_date": None,
        "corrects_invoice": None, "agreement_ref": None, "printed_total": None,
        "header_adjustments": [], "stated_line_count": None, "lines": [],
    }
    body, doc["printed_total"] = _rows(f, text, cols)

    if cols == INVOICE_COLS:
        doc.update(doc_id=path.stem, doc_type="invoice", doc_id_source="filename")
        for n, (loc, r) in enumerate(body, start=1):
            doc["lines"].append({
                "doc_id": doc["doc_id"], "line_no": n, "consignment_ref": r["cnote_no"],
                "billed_amount": _amt(f, loc, r["total_rs"]),
                "components": [
                    {"label": "freight", "amount": _amt(f, loc, r["freight_rs"])},
                    {"label": "chill premium", "amount": _amt(f, loc, r["chill_prem_rs"])},
                ],
                "facts": {
                    "booking_date": r["booking_dt"],
                    "weight_kg": _amt(f, loc, r["wt_kg"]),
                    "distance_km": _amt(f, loc, r["dist_km"]),
                },
                "corrects": None, "text_notes": [],
                "source": {"file": f, "locator": loc},
            })
        return doc

    # credit note
    ids = {r["credit_note"] for _, r in body}
    if len(ids) != 1:
        raise ParseError(f, "rows", f"credit-note rows name {len(ids)} different credit notes: {sorted(ids)}")
    targets = {r["against_invoice"] for _, r in body}
    doc.update(doc_id=ids.pop(), doc_type="credit_note", doc_id_source="content",
               corrects_invoice=targets.pop() if len(targets) == 1 else None)
    for n, (loc, r) in enumerate(body, start=1):
        if not r["against_invoice"]:
            raise ParseError(f, loc, "credit-note row does not state the invoice it corrects")
        doc["lines"].append({
            "doc_id": doc["doc_id"], "line_no": n, "consignment_ref": r["cnote_no"],
            "billed_amount": _amt(f, loc, r["credit_rs"]),
            "components": [{"label": "credit", "amount": _amt(f, loc, r["credit_rs"])}],
            "facts": {},
            "corrects": {"invoice": r["against_invoice"], "consignment_ref": r["cnote_no"]},
            "text_notes": [], "source": {"file": f, "locator": loc},
        })
    return doc


ADAPTER = Adapter(name="sagar_csv", carrier=CARRIER, sniff=sniff, parse=parse)

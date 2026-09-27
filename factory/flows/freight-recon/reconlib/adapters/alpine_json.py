"""Alpine Express: monthly JSON invoices.

Invoice total = sum(line_amount) - discount. The discount is a document-level
adjustment, so it is carried in ``header_adjustments`` (as a negative amount)
and never spread over lines.

Unknown keys are a hard failure at both document and line level: a JSON
field nobody coded for is exactly where an unaccounted charge would hide.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from ..money import MoneyParseError, to_decimal
from . import Adapter, ParseError

CARRIER = "alpine"
DOC_KEYS = {"carrier", "customer", "invoice_no", "billing_period", "consignment_count",
            "lines", "discount", "invoice_total"}
DOC_REQUIRED = DOC_KEYS - {"discount"}
LINE_KEYS = {"sl", "consignment_no", "booking_date", "actual_weight_kg", "chargeable_weight_kg",
             "rate_per_kg", "handling_fee", "line_amount"}


def sniff(path: Path, text: str) -> bool:
    try:
        d = json.loads(text)
    except ValueError:
        return False
    return isinstance(d, dict) and str(d.get("carrier", "")).startswith("Alpine Express")


def _num(f, loc, v) -> Decimal:
    try:
        return to_decimal(v)
    except MoneyParseError as e:
        raise ParseError(f, loc, str(e)) from e


def parse(path: Path, text: str) -> dict:
    f = path.name
    d = json.loads(text, parse_float=Decimal)
    if unknown := set(d) - DOC_KEYS:
        raise ParseError(f, "document", f"unknown top-level keys: {sorted(unknown)}")
    if missing := DOC_REQUIRED - set(d):
        raise ParseError(f, "document", f"missing top-level keys: {sorted(missing)}")

    discount = _num(f, "discount", d.get("discount", 0))
    doc = {
        "doc_id": d["invoice_no"], "doc_type": "invoice", "carrier": CARRIER, "source_file": f,
        "format": "alpine_json", "period": d["billing_period"], "doc_date": None,
        "corrects_invoice": None, "agreement_ref": None,
        "printed_total": _num(f, "invoice_total", d["invoice_total"]),
        "header_adjustments": [{"label": "discount", "amount": -discount}] if discount else [],
        "stated_line_count": int(d["consignment_count"]), "lines": [],
    }
    for idx, ln in enumerate(d["lines"]):
        loc = f"lines[{idx}]"
        if set(ln) != LINE_KEYS:
            raise ParseError(f, loc, f"line keys differ from expected: extra={sorted(set(ln) - LINE_KEYS)} "
                                     f"missing={sorted(LINE_KEYS - set(ln))}")
        chargeable = _num(f, loc, ln["chargeable_weight_kg"])
        rate = _num(f, loc, ln["rate_per_kg"])
        handling = _num(f, loc, ln["handling_fee"])
        doc["lines"].append({
            "doc_id": doc["doc_id"], "line_no": int(ln["sl"]), "consignment_ref": ln["consignment_no"],
            "billed_amount": _num(f, loc, ln["line_amount"]),
            "components": [
                # Alpine prints weight x rate rather than a freight amount; the
                # product is derived from printed values and marked as such.
                {"label": "freight", "amount": chargeable * rate, "derived": True},
                {"label": "handling fee", "amount": handling},
            ],
            "facts": {
                "booking_date": ln["booking_date"],
                "actual_weight_kg": _num(f, loc, ln["actual_weight_kg"]),
                "chargeable_weight_kg": chargeable,
                "rate_per_kg": rate,
            },
            "corrects": None, "text_notes": [],
            "source": {"file": f, "locator": loc},
        })
    return doc


ADAPTER = Adapter(name="alpine_json", carrier=CARRIER, sniff=sniff, parse=parse)

"""Falcon Freight: fixed-layout plain-text tax invoices and credit notes.

Layout:
    <header lines>
    ====
    1. Consignment FF-8001
       Mumbai to Hyderabad, 650 km, 900 kg, standard     (route; absent on credit notes)
       <Charge label>: Rs 17,472.00                      (0..n charge lines)
       <free text>                                       (0..n note lines, credit notes)
       LINE TOTAL: Rs 17,472.00
    ...
    ====
    INVOICE TOTAL: Rs 181,637.60   |   CREDIT NOTE TOTAL: Rs -496.80
    <footer lines>
"""
from __future__ import annotations

import re
from pathlib import Path

from ..money import MoneyParseError, parse_amount
from . import Adapter, ParseError

CARRIER = "falcon"
_AMT = r"-?[\d,]+(?:\.\d+)?"

RULE = re.compile(r"^=+$")
HDR_NAME = re.compile(r"^FALCON FREIGHT PVT LTD$")
HDR_AGREEMENT = re.compile(r"^Servicing BlueFin Commerce under agreement (?P<ref>\S+)$")
HDR_INVOICE = re.compile(r"^TAX INVOICE (?P<id>\S+)\s+Period: (?P<period>.+)$")
HDR_CREDIT = re.compile(r"^CREDIT NOTE (?P<id>\S+)\s+Date: (?P<date>\d{4}-\d{2}-\d{2})$")
HDR_AGAINST = re.compile(r"^Against: TAX INVOICE (?P<id>\S+)$")
BLOCK_START = re.compile(r"^(?P<n>\d+)\. Consignment (?P<ref>\S+)$")
ROUTE = re.compile(
    r"^(?P<origin>.+?) to (?P<dest>.+?), (?P<km>\d+(?:\.\d+)?) km, "
    r"(?P<kg>\d+(?:\.\d+)?) kg, (?P<service>[a-z]+)$"
)
LINE_TOTAL = re.compile(rf"^LINE TOTAL: Rs (?P<amt>{_AMT})$")
CHARGE = re.compile(rf"^(?P<label>[A-Za-z][^:]*): Rs (?P<amt>{_AMT})$")
DOC_TOTAL = re.compile(rf"^(?P<kind>INVOICE|CREDIT NOTE) TOTAL: Rs (?P<amt>{_AMT})$")
FOOTER_OK = [re.compile(r"^Payment due .*$")]


def sniff(path: Path, text: str) -> bool:
    first = text.lstrip().splitlines()[:1]
    return bool(first) and HDR_NAME.match(first[0].strip()) is not None


def _amt(file: str, loc: str, raw: str):
    try:
        return parse_amount(raw)
    except MoneyParseError as e:
        raise ParseError(file, loc, str(e)) from e


def parse(path: Path, text: str) -> dict:
    f = path.name
    doc = {
        "doc_id": None, "doc_type": None, "carrier": CARRIER, "source_file": f,
        "format": "falcon_text", "period": None, "doc_date": None,
        "corrects_invoice": None, "agreement_ref": None, "printed_total": None,
        "header_adjustments": [], "stated_line_count": None, "lines": [],
    }
    lines = text.splitlines()
    section = "header"  # header -> body -> footer
    block = None

    def close_block(loc: str):
        nonlocal block
        if block is None:
            return
        if block["billed_amount"] is None:
            raise ParseError(f, loc, f"consignment {block['consignment_ref']} has no LINE TOTAL")
        doc["lines"].append(block)
        block = None

    for i, raw in enumerate(lines, start=1):
        loc = f"line {i}"
        s = raw.strip()
        if not s:
            continue
        if RULE.match(s):
            if section == "header":
                section = "body"
            elif section == "body":
                close_block(loc)
                section = "footer"
            else:
                raise ParseError(f, loc, "unexpected third rule line")
            continue

        if section == "header":
            if HDR_NAME.match(s):
                continue
            if m := HDR_AGREEMENT.match(s):
                doc["agreement_ref"] = m["ref"]
            elif m := HDR_INVOICE.match(s):
                doc.update(doc_id=m["id"], doc_type="invoice", period=m["period"].strip())
            elif m := HDR_CREDIT.match(s):
                doc.update(doc_id=m["id"], doc_type="credit_note", doc_date=m["date"])
            elif m := HDR_AGAINST.match(s):
                doc["corrects_invoice"] = m["id"]
            else:
                raise ParseError(f, loc, f"unrecognised header line: {s!r}")
            continue

        if section == "body":
            if m := BLOCK_START.match(s):
                close_block(loc)
                block = {
                    "doc_id": None, "line_no": int(m["n"]), "consignment_ref": m["ref"],
                    "billed_amount": None, "components": [], "facts": {},
                    "corrects": None, "text_notes": [],
                    "source": {"file": f, "locator": loc},
                }
                continue
            if block is None:
                raise ParseError(f, loc, f"text outside any consignment block: {s!r}")
            if block["billed_amount"] is not None:
                raise ParseError(f, loc, f"content after LINE TOTAL in consignment {block['consignment_ref']}: {s!r}")
            if m := LINE_TOTAL.match(s):
                block["billed_amount"] = _amt(f, loc, m["amt"])
            elif m := ROUTE.match(s):
                if block["facts"]:
                    raise ParseError(f, loc, "second route line in one consignment block")
                block["facts"] = {
                    "origin_city": m["origin"], "destination_city": m["dest"],
                    "distance_km": _amt(f, loc, m["km"]), "weight_kg": _amt(f, loc, m["kg"]),
                    "service_level": m["service"],
                }
            elif m := CHARGE.match(s):
                block["components"].append({"label": m["label"].strip(), "amount": _amt(f, loc, m["amt"])})
            elif "Rs" in s or "₹" in s:
                # Looks like money we could not parse; refusing beats dropping it.
                raise ParseError(f, loc, f"unparsed monetary text in consignment {block['consignment_ref']}: {s!r}")
            else:
                # Free text (e.g. a credit note's correction reason). Consecutive
                # lines are one wrapped sentence.
                if block.get("_note_open"):
                    block["text_notes"][-1] += " " + s
                else:
                    block["text_notes"].append(s)
                block["_note_open"] = True
                continue
            block["_note_open"] = False
            continue

        # footer
        if m := DOC_TOTAL.match(s):
            if doc["printed_total"] is not None:
                raise ParseError(f, loc, "second document total")
            doc["printed_total"] = _amt(f, loc, m["amt"])
        elif any(p.match(s) for p in FOOTER_OK):
            continue
        else:
            raise ParseError(f, loc, f"unrecognised footer line: {s!r}")

    if section != "footer":
        raise ParseError(f, "eof", "document never reached its closing rule / total")
    if doc["doc_id"] is None:
        raise ParseError(f, "header", "no TAX INVOICE / CREDIT NOTE identifier")
    if doc["printed_total"] is None:
        raise ParseError(f, "footer", "no document total")
    if doc["doc_type"] == "credit_note" and not doc["corrects_invoice"]:
        raise ParseError(f, "header", "credit note does not state the invoice it corrects")

    for ln in doc["lines"]:
        ln.pop("_note_open", None)
        ln["doc_id"] = doc["doc_id"]
        if doc["doc_type"] == "credit_note":
            ln["corrects"] = {"invoice": doc["corrects_invoice"], "consignment_ref": ln["consignment_ref"]}
    numbers = [ln["line_no"] for ln in doc["lines"]]
    if numbers != list(range(1, len(numbers) + 1)):
        raise ParseError(f, "body", f"consignment numbering is not 1..n: {numbers}")
    return doc


ADAPTER = Adapter(name="falcon_text", carrier=CARRIER, sniff=sniff, parse=parse)

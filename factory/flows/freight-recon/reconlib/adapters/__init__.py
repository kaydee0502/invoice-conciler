"""Carrier document adapters.

An adapter turns one billing document (invoice or credit note) in one
carrier's native format into the normalized model below. Adapters must be
total: every byte of meaning in the source either lands in the model or
raises ParseError. They never guess and never skip.

Adapters identify files by CONTENT (``sniff``), not by filename, so a
renamed or new file is still routed correctly. Exactly one adapter must
claim each file; zero or several is a hard failure.

Normalized document (dict):
    doc_id, doc_type ('invoice'|'credit_note'), carrier (shipments.json key),
    source_file, format, period, doc_date, corrects_invoice,
    printed_total, header_adjustments [{label, amount}], stated_line_count,
    lines [Line]

Normalized line (dict):
    doc_id, line_no, consignment_ref, billed_amount,
    components [{label, amount}]   -- charge breakdown as printed
    facts {...}                    -- carrier's claimed shipment facts, as printed
    corrects {invoice, consignment_ref} | None   -- credit-note lines only
    text_notes [str]               -- free text printed against the line
    source {file, locator}         -- where in the source file this came from

Amounts are Decimal. ``billed_amount`` is what the carrier claims for the
line (its printed line total), not a recomputation.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class ParseError(Exception):
    """The source contains something the adapter cannot account for."""

    def __init__(self, file: str, locator: str, message: str):
        super().__init__(f"{file} [{locator}]: {message}")
        self.file, self.locator, self.message = file, locator, message


@dataclass(frozen=True)
class Adapter:
    name: str
    carrier: str
    sniff: Callable[[Path, str], bool]
    parse: Callable[[Path, str], dict]


def registry() -> list[Adapter]:
    from . import alpine_json, falcon_text, sagar_csv

    return [falcon_text.ADAPTER, sagar_csv.ADAPTER, alpine_json.ADAPTER]


def read_text(path: Path) -> str:
    # utf-8-sig drops a BOM; universal newlines normalise CRLF/CR.
    with open(path, encoding="utf-8-sig", newline=None) as fh:
        return fh.read()


def route(path: Path, text: str) -> Adapter:
    claims = [a for a in registry() if a.sniff(path, text)]
    if len(claims) != 1:
        who = ", ".join(a.name for a in claims) or "no adapter"
        raise ParseError(path.name, "file", f"expected exactly one adapter to claim this file, got: {who}")
    return claims[0]

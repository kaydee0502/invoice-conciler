"""Stage 4 — price: expected amounts and rule-based dispositions.

For every line: expected amount from the carrier's agreed rate spec applied to
BlueFin's SHIPMENT record (the authority on facts), the delta, a provisional
disposition from an explicit rule (``rule`` id), and a structured explanation
of the delta. For every invoice: invoice-level findings (volume adjustments,
document-total and count anomalies).

Nothing here is a model call. Lines whose disposition genuinely needs
judgement are flagged ``review: true`` for the adjudicate stage, which may
choose among ``allowed_dispositions`` but can never change an amount.

Rules (judgement calls agreed for this system; see DESIGN.md):
  R_UNMATCHED       no shipment -> escalate, expected null
  R_AMBIGUOUS_MATCH several shipments share the ref -> escalate
  R_DUPLICATE       shipment already billed on an earlier line -> expected 0, dispute
  R_UNDETERMINED    contract does not determine the amount -> escalate, expected null
  R_MATCHES         billed == expected -> accept
  R_UNDERBILLED     billed < expected -> accept, noted (BlueFin pays what was billed)
  R_OVERBILLED      billed > expected, cause determined by contract -> dispute
  R_SILENT_CHARGE   an unexplained billed charge the contract neither lists nor
                    excludes (open schedule) -> escalate
  R_CREDIT_*        credit notes: see price_credit()
"""
from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from . import money
from .money import q
from .ratespec import invoice_adjustments, price_shipment

TOL = Decimal("0.01")
_STOP = {"charge", "charges", "fee", "at", "of", "the", "and", "incl", "including", "per", "on", "for", "to",
         "rs", "inr", "consignment", "consignee", "amount"}


def inr(x: Decimal | None) -> str:
    if x is None:
        return "n/a"
    sign = "-" if x < 0 else ""
    return f"{sign}₹{abs(q(x)):,.2f}"


def _words(label: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", label.lower()) if w not in _STOP and len(w) > 2}


def _eq(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= TOL


# --------------------------------------------------------------------------- explanation

def explain(line: dict, result, spec: dict, shipment: dict) -> list[dict]:
    """Account for each billed component against the contract charges.

    A billed component is *explained* when some subset of the applicable
    contract charges sums to it (label overlap breaks ties). Anything left
    over is classified. Returns a list of findings:
      {code, detail, amount?, clause?}
    """
    out: list[dict] = []
    charges = [dict(c) for c in result.charges]
    applied_ids = {c["id"] for c in charges}
    not_applied = [c for c in spec["charges"] if c["id"] not in applied_ids]
    closed = bool(spec["accessorials_policy"]["closed_list"])
    closed_clause = spec["accessorials_policy"].get("clause")

    comps = [c for c in line["components"] if c["amount"] != 0]
    comp_sum = sum((c["amount"] for c in line["components"]), Decimal(0))
    if line["components"] and not _eq(comp_sum, line["billed_amount"]):
        out.append({"code": "PRINTED_ARITHMETIC", "amount": q(line["billed_amount"] - comp_sum),
                    "detail": f"the printed charges sum to {inr(comp_sum)} but the line total is "
                              f"{inr(line['billed_amount'])}: {inr(line['billed_amount'] - comp_sum)} is not "
                              f"attributed to any charge"})

    unassigned = list(range(len(charges)))
    leftovers = []
    # Pass 1: a billed component equal to some subset of the applicable contract
    # charges is explained (label overlap breaks ties between subsets).
    for comp in sorted(comps, key=lambda c: -len(_words(c["label"]))):
        best = None
        for r in range(1, len(unassigned) + 1):
            for subset in itertools.combinations(unassigned, r):
                total = sum((charges[i]["amount"] for i in subset), Decimal(0))
                if _eq(total, comp["amount"]):
                    overlap = sum(len(_words(comp["label"]) & _words(charges[i]["label"] + " " + charges[i]["id"]))
                                  for i in subset)
                    if best is None or overlap > best[0]:
                        best = (overlap, subset)
            if best:
                break
        if best:
            for i in best[1]:
                unassigned.remove(i)
        else:
            leftovers.append(comp)

    def differs(comp, idxs):
        related = [charges[i] for i in idxs]
        exp = sum((c["amount"] for c in related), Decimal(0))
        for i in idxs:
            unassigned.remove(i)
        out.append({"code": "AMOUNT_DIFFERS", "amount": q(comp["amount"] - exp),
                    "clause": clause_list(c.get("clause") for c in related),
                    "detail": f"'{comp['label']}' billed at {inr(comp['amount'])}; the contract gives {inr(exp)} for "
                              + " + ".join(f"{c['label']} {inr(c['amount'])}" for c in related)})

    # Pass 2: a single leftover while contract charges remain unexplained is
    # those charges at the wrong amount, whatever its label (e.g. a carrier's
    # one "freight" column covering several contract components).
    if len(leftovers) == 1 and unassigned:
        differs(leftovers[0], list(unassigned))
        leftovers = []
    # Pass 3: with several leftovers, one that shares words with still-
    # unexplained contract charges is those charges at the wrong amount.
    rest = []
    for comp in leftovers:
        cw = _words(comp["label"])
        idxs = [i for i in unassigned if cw & _words(charges[i]["label"] + " " + charges[i]["id"])]
        if idxs:
            differs(comp, idxs)
        else:
            rest.append(comp)
    # Pass 4: what is left is a charge the contract does not provide for here.
    for comp in rest:
        cw = _words(comp["label"])
        related_other = [c for c in not_applied if cw & _words(c["label"] + " " + c["id"])]
        if related_other:
            c = related_other[0]
            out.append({"code": "CONDITION_NOT_MET", "amount": q(comp["amount"]), "clause": c.get("clause"),
                        "detail": f"'{comp['label']}' billed at {inr(comp['amount'])}, but the contract's "
                                  f"{c['label']} ({c['clause']}) does not apply to this shipment "
                                  f"(service {shipment.get('service_level')}, handling "
                                  f"{shipment.get('special_handling') or 'none'})"})
        elif closed:
            out.append({"code": "UNLISTED_NOT_PAYABLE", "amount": q(comp["amount"]), "clause": closed_clause,
                        "detail": f"'{comp['label']}' ({inr(comp['amount'])}) is not a charge in the contract's "
                                  f"schedule, and the contract excludes unlisted charges ({closed_clause})"})
        else:
            out.append({"code": "UNLISTED_CONTRACT_SILENT", "amount": q(comp["amount"]),
                        "detail": f"'{comp['label']}' ({inr(comp['amount'])}) is not a charge in the contract's "
                                  f"schedule, and the contract does not say whether unlisted charges are payable"})
    for i in unassigned:
        c = charges[i]
        out.append({"code": "CONTRACT_CHARGE_NOT_BILLED", "amount": q(-c["amount"]), "clause": c.get("clause"),
                    "detail": f"the contract's {c['label']} ({inr(c['amount'])}, {c['clause']}) does not appear "
                              f"among the billed charges"})
    return out


# --------------------------------------------------------------------------- lines

def _base(line: dict, m: dict) -> dict:
    return {"line_key": line["line_key"], "invoice": line["doc_id"], "doc_type": line["doc_type"],
            "carrier": line["carrier"], "consignment_ref": line["consignment_ref"],
            "shipment_id": m["shipment_id"], "billed_amount": q(line["billed_amount"]),
            "expected_amount": None, "delta": None, "disposition": None, "rule": None,
            "review": False, "allowed_dispositions": None, "reasons": [], "justification": "",
            "contract_clause": None, "notes": [], "expected_charges": [], "billed_components": line["components"],
            "corrects_line_keys": m.get("corrects_line_keys"), "hints": m.get("hints", []),
            "fact_conflicts": m.get("fact_conflicts", []), "anomaly_codes": []}


_CLAUSE = re.compile(r"(?P<file>[\w.-]+\.md)?\s*(?P<sec>§\s*\d+[a-z]?|header)")


def clause_list(items) -> str | None:
    """Normalise clause references: 'a.md §1, a.md §3, §1' -> 'a.md §1, §3'.
    Text without a recognisable '§N' is kept verbatim."""
    by_file: dict[str, list[str]] = {}
    extra: list[str] = []
    for item in items:
        if not item:
            continue
        current = None
        found = False
        for mm in _CLAUSE.finditer(str(item)):
            found = True
            current = mm.group("file") or current or ""
            sec = mm.group("sec").replace(" ", "")
            by_file.setdefault(current, [])
            if sec not in by_file[current]:
                by_file[current].append(sec)
        if not found and item not in extra:
            extra.append(str(item))
    parts = []
    for f, secs in by_file.items():
        secs = sorted(secs, key=lambda x: (not x.startswith("§"), int(re.sub(r"\D", "", x) or 0)))
        parts.append((f + " " if f else "") + ", ".join(secs))
    return "; ".join(parts + extra) or None


def _clauses(*groups) -> str | None:
    return clause_list(c for g in groups for c in (g or []))


def price_invoice_line(line: dict, m: dict, spec: dict, shipment: dict, anomalies: list[dict],
                       duplicate_of: str | None) -> dict:
    rec = _base(line, m)
    rec["anomaly_codes"] = [a["code"] for a in anomalies]
    conflicts = rec["fact_conflicts"]
    if conflicts:
        rec["notes"].append("invoice facts differ from the shipment record (priced from the shipment record): "
                            + "; ".join(f"{c['fact']} invoice {c['invoice']} vs shipment {c['shipment']}"
                                        for c in conflicts))
        if any(c["materiality"] == "pricing" for c in conflicts):
            rec["reasons"].append({"code": "FACT_CONFLICT", "detail": rec["notes"][-1]})

    if duplicate_of:
        rec.update(expected_amount=Decimal("0.00"), delta=rec["billed_amount"], disposition="dispute",
                   rule="R_DUPLICATE", contract_clause=None)
        rec["reasons"].append({"code": "DUPLICATE_BILLING",
                               "detail": f"shipment {m['shipment_id']} ({line['consignment_ref']}) is already billed "
                                         f"on {duplicate_of}; a consignment is payable once"})
        rec["justification"] = (f"Duplicate billing: {line['consignment_ref']} (shipment {m['shipment_id']}) was "
                                f"already billed on {duplicate_of.split('#')[0]} (line {duplicate_of}). The full "
                                f"{inr(rec['billed_amount'])} is disputed.")
        return rec

    result = price_shipment(spec, shipment)
    rec["expected_charges"] = result.charges
    if result.status == "undetermined":
        rec.update(disposition="escalate", rule="R_UNDETERMINED",
                   contract_clause=_clauses([r.get("clause") for r in result.reasons], result.clauses))
        for r in result.reasons:
            rec["reasons"].append({"code": "UNDETERMINED_" + r["reason"].upper(), "detail": r["detail"],
                                   "clause": r.get("clause")})
        rec["justification"] = ("The contract does not determine an amount for this consignment: "
                                + "; ".join(f"{r['detail']} ({r.get('clause') or 'contract'})" for r in result.reasons)
                                + f". Billed {inr(rec['billed_amount'])}; needs a rate decision before payment.")
        return rec

    expected = result.amount
    rec["expected_amount"] = expected
    rec["delta"] = q(rec["billed_amount"] - expected)
    expl = explain(line, result, spec, shipment)
    rec["reasons"] += expl
    rec["contract_clause"] = _clauses(result.clauses, [e.get("clause") for e in expl])
    breakdown = " + ".join(f"{c['label']} {inr(c['amount'])}" for c in result.charges)
    basis = (f"shipment {m['shipment_id']}: {shipment['billed_weight_kg']} kg, {shipment['distance_km']} km, "
             f"{shipment['service_level']}, handling {shipment['special_handling'] or 'none'}")

    if _eq(rec["delta"], Decimal(0)):
        rec.update(disposition="accept", rule="R_MATCHES")
        rec["justification"] = (f"Billed amount matches the contract: {breakdown} = {inr(expected)} "
                                f"({rec['contract_clause']}; {basis}).")
        return rec
    if rec["delta"] < 0:
        rec.update(disposition="accept", rule="R_UNDERBILLED")
        rec["justification"] = (f"Billed {inr(rec['billed_amount'])} is {inr(-rec['delta'])} below the contract "
                                f"amount {inr(expected)} ({breakdown}; {rec['contract_clause']}; {basis}). "
                                f"Accepted as billed; the underbilling is noted, not claimed.")
        rec["notes"].append(f"underbilled by {inr(-rec['delta'])}")
        return rec
    causes = [e for e in expl if e["code"] != "CONTRACT_CHARGE_NOT_BILLED"]
    cause_txt = "; ".join(e["detail"] for e in causes) or "the billed charges do not reconcile to the contract"
    if any(e["code"] == "UNLISTED_CONTRACT_SILENT" for e in expl):
        rec.update(disposition="escalate", rule="R_SILENT_CHARGE", review=True,
                   allowed_dispositions=["escalate", "dispute"])
        rec["justification"] = (f"Billed {inr(rec['billed_amount'])} vs contract {inr(expected)} ({breakdown}; "
                                f"{rec['contract_clause']}). {cause_txt}. The contract neither lists nor excludes "
                                f"this charge, so its payability needs a decision.")
        return rec
    rec.update(disposition="dispute", rule="R_OVERBILLED")
    rec["justification"] = (f"Overbilled by {inr(rec['delta'])}: billed {inr(rec['billed_amount'])}, contract amount "
                            f"{inr(expected)} ({breakdown}; {rec['contract_clause']}; {basis}). {cause_txt}.")
    return rec


def price_credit(credit: dict, m: dict, originals: list[dict], spec: dict) -> tuple[dict, dict | None]:
    """Price a credit-note line against the invoice line it corrects.

    The credit's expected amount is the correction the contract requires:
    minus the original line's overbilling (0 if the original was not
    overbilled). Whatever the credit leaves uncorrected is disputed ONCE, on
    the credit line; the original line is then accepted as corrected, so no
    rupee is counted twice. Returns (credit_rec, updated_original_or_None).
    """
    rec = _base(credit, m)
    full_rule = next((r for r in spec.get("document_rules", [])
                      if r["id"] == "credit_note_must_cover_full_correction"), None)
    if len(originals) != 1:
        rec.update(disposition="escalate", rule="R_CREDIT_TARGET")
        rec["reasons"].append({"code": "CREDIT_TARGET",
                               "detail": f"credit corrects {credit['corrects']}; matching invoice lines: "
                                         f"{[o['line_key'] for o in originals]}"})
        rec["justification"] = ("The credit note cannot be tied to exactly one invoice line "
                                f"({credit['corrects']['consignment_ref']} on {credit['corrects']['invoice']}); "
                                "its effect on the amount owed cannot be computed.")
        return rec, None
    orig = originals[0]
    rec["corrects_line_keys"] = [orig["line_key"]]
    if orig["expected_amount"] is None or orig["disposition"] == "escalate":
        rec.update(disposition="escalate", rule="R_CREDIT_ORIGINAL_UNDETERMINED",
                   contract_clause=orig["contract_clause"])
        rec["justification"] = (f"Credits {inr(-rec['billed_amount'])} against {orig['line_key']}, whose contract "
                                f"amount is itself undetermined; the credit can only be evaluated once that line "
                                f"is priced.")
        return rec, None

    over = max(orig["delta"], Decimal(0)) if orig["rule"] != "R_DUPLICATE" else orig["delta"]
    expected = q(-over)
    rec["expected_amount"] = expected
    rec["delta"] = q(rec["billed_amount"] - expected)       # >0: credit short of the correction owed
    remaining = rec["delta"]
    clause = _clauses([full_rule["clause"]] if full_rule else [], [orig["contract_clause"]])
    rec["contract_clause"] = clause
    note_txt = f" Credit note says: {' '.join(credit['text_notes'])}" if credit["text_notes"] else ""

    if over <= 0:
        rec.update(disposition="accept", rule="R_CREDIT_UNNEEDED")
        rec["notes"].append(f"credit of {inr(-rec['billed_amount'])} against a line that was not overbilled")
        rec["justification"] = (f"Credits {inr(-rec['billed_amount'])} against {orig['line_key']}, which already "
                                f"matches the contract; the credit is in BlueFin's favour and is accepted.{note_txt}")
        return rec, None

    new_orig = dict(orig)
    new_orig["notes"] = list(orig["notes"]) + [f"corrected by credit note {credit['doc_id']} "
                                               f"({inr(-credit['billed_amount'])})"]
    new_orig.update(disposition="accept", rule="R_CORRECTED_BY_CREDIT", review=False, allowed_dispositions=None,
                    justification=orig["justification"] + f" Corrected by credit note {credit['doc_id']} "
                                  f"({inr(-credit['billed_amount'])}); "
                                  + ("the credit covers the overbilling in full, so the line is accepted."
                                     if remaining <= TOL else
                                     f"the {inr(remaining)} still uncorrected is disputed on the credit note line "
                                     f"({rec['line_key']}), not here."))
    if remaining > TOL:
        rec.update(disposition="dispute", rule="R_CREDIT_SHORT")
        rec["reasons"].append({"code": "CREDIT_SHORT", "amount": remaining, "clause": clause,
                               "detail": f"credit of {inr(-rec['billed_amount'])} against an overbilling of "
                                         f"{inr(over)} on {orig['line_key']}"})
        rec["justification"] = (f"Partial credit: {orig['line_key']} was overbilled by {inr(over)} "
                                f"({orig['contract_clause']}), but this credit note returns only "
                                f"{inr(-rec['billed_amount'])}, leaving {inr(remaining)} uncorrected"
                                + (f"; the contract requires a credit note to cover the full corrected amount "
                                   f"({full_rule['clause']})" if full_rule else "") + f".{note_txt}")
    elif remaining < -TOL:
        rec.update(disposition="accept", rule="R_CREDIT_OVER")
        rec["notes"].append(f"credit exceeds the overbilling by {inr(-remaining)}")
        rec["justification"] = (f"Credits {inr(-rec['billed_amount'])} against an overbilling of {inr(over)} on "
                                f"{orig['line_key']}: {inr(-remaining)} more than required, in BlueFin's favour."
                                f"{note_txt}")
    else:
        rec.update(disposition="accept", rule="R_CREDIT_FULL")
        rec["justification"] = (f"Fully corrects the {inr(over)} overbilling on {orig['line_key']} "
                                f"({orig['contract_clause']}).{note_txt}")
    return rec, new_orig


# --------------------------------------------------------------------------- invoice findings

def _month(doc: dict, lines: list[dict]) -> str | None:
    if doc.get("period"):
        mm = re.search(r"\d{4}-\d{2}", str(doc["period"]))
        if mm:
            return mm.group(0)
    months = {str(ln["facts"].get("booking_date", ""))[:7] for ln in lines if ln["facts"].get("booking_date")}
    return months.pop() if len(months) == 1 else None


def invoice_findings(doc: dict, lines: list[dict], priced: list[dict], spec: dict,
                     shipments: list[dict], anomalies: list[dict]) -> list[dict]:
    out = []
    for a in anomalies:
        if a["code"] == "DOC_TOTAL_MISMATCH":
            diff = a["difference"]
            out.append({"invoice": doc["doc_id"], "kind": "DOC_TOTAL_MISMATCH", "anomaly_codes": [a["code"]],
                        "description": f"Printed total {inr(a['printed_total'])} differs from the sum of its lines "
                                       f"and adjustments {inr(a['computed_total'])}",
                        "amount_impact": q(diff), "disposition": "dispute" if diff > 0 else "accept",
                        "rule": "R_DOC_TOTAL", "contract_clause": None,
                        "justification": (f"The invoice total is {inr(abs(diff))} "
                                          + ("higher than its own lines support; the excess is disputed."
                                             if diff > 0 else "lower than its own lines; noted, in BlueFin's favour."))})
        elif a["code"] == "STATED_COUNT_MISMATCH":
            out.append({"invoice": doc["doc_id"], "kind": "STATED_COUNT_MISMATCH", "anomaly_codes": [a["code"]],
                        "description": a["detail"], "amount_impact": Decimal("0.00"), "disposition": "accept",
                        "rule": "R_COUNT", "contract_clause": None,
                        "justification": "Informational: the stated consignment count differs from the lines "
                                         "listed; every listed line is reconciled individually."})

    adjs = spec.get("invoice_adjustments") or []
    printed_adj = sum((a["amount"] for a in doc["header_adjustments"]), Decimal(0))
    if doc["doc_type"] != "invoice" or (not adjs and not printed_adj):
        return out
    month = _month(doc, lines)
    if adjs and month is None:
        out.append({"invoice": doc["doc_id"], "kind": "ADJUSTMENT_PERIOD_UNKNOWN", "anomaly_codes": [],
                    "description": "Cannot tell which calendar month this invoice covers, so the contract's "
                                   "invoice-level adjustment cannot be evaluated",
                    "amount_impact": None, "disposition": "escalate", "rule": "R_ADJ_PERIOD",
                    "contract_clause": ", ".join(a["clause"] for a in adjs),
                    "justification": "Invoice-level adjustment could not be evaluated: billing month unknown."})
        return out
    tendered = sum(1 for s in shipments if s["carrier"] == doc["carrier"] and str(s["ship_date"])[:7] == month)
    determined = [p for p in priced if p["expected_amount"] is not None]
    pending = [p for p in priced if p["expected_amount"] is None]
    line_total = sum((p["expected_amount"] for p in determined), Decimal(0))
    found = invoice_adjustments(spec, {"consignments_in_calendar_month": tendered,
                                       "invoice_line_total": line_total})
    exp_adj = sum((f["amount"] for f in found), Decimal(0))
    clause = clause_list(a["clause"] for a in adjs)
    billed_sum = sum((ln["billed_amount"] for ln in lines), Decimal(0))
    desc_rule = "; ".join(f"{a['label']} ({a['clause']})" for a in adjs) or "no invoice-level adjustment in contract"
    pending_txt = ""
    if pending:
        pending_txt = (f" Lines {[p['line_key'] for p in pending]} have no determinable contract amount; the "
                       f"adjustment on them is settled together with those escalated lines.")
    base = {"invoice": doc["doc_id"], "kind": "INVOICE_ADJUSTMENT", "anomaly_codes": [], "contract_clause": clause,
            "tendered_in_month": tendered, "month": month, "printed_adjustment": printed_adj,
            "expected_adjustment": None if pending else exp_adj,
            "expected_adjustment_on_determined_lines": exp_adj}
    impact = q(printed_adj - exp_adj)     # positive = BlueFin overbilled (discount short or missing)
    derivative = exp_adj != 0 and any(_eq(printed_adj, q(Decimal(str(a["percent"])) / 100 * billed_sum)) for a in adjs)
    desc = (f"{desc_rule}: {tendered} consignments tendered in {month}; contract adjustment {inr(exp_adj)} on the "
            f"contract line total{' of the determinable lines' if pending else ''}, printed adjustment "
            f"{inr(printed_adj)}")
    if abs(impact) <= TOL:
        disp, rule, just = "accept", "R_ADJ_OK", f"Invoice-level adjustment applied correctly ({clause}).{pending_txt}"
    elif derivative:
        disp, rule = "accept", "R_ADJ_DERIVATIVE"
        just = (f"The carrier applied the adjustment at the contract rate to its own line total ({clause}). Any "
                f"difference from the contract figure comes only from disputed or escalated line amounts and "
                f"resolves when those lines are settled, so it is not disputed separately.{pending_txt}")
        impact = Decimal("0.00") if pending else impact
    elif impact > 0:
        disp, rule = "dispute", "R_ADJ_SHORT"
        just = (f"{tendered} consignments were tendered in {month}, so the contract's invoice-level adjustment "
                f"applies ({clause}): {inr(exp_adj)} on the contract line total"
                f"{' of the determinable lines (a lower bound)' if pending else ''}. The invoice applies "
                f"{inr(printed_adj)}; {inr(impact)} is owed to BlueFin.{pending_txt}")
    else:
        disp, rule = "accept", "R_ADJ_EXCESS"
        just = (f"The invoice applies {inr(printed_adj)} against a contract adjustment of {inr(exp_adj)} "
                f"({clause}); the difference is in BlueFin's favour and is noted.{pending_txt}")
    out.append({**base, "description": desc, "amount_impact": impact, "disposition": disp, "rule": rule,
                "justification": just})
    return out


# --------------------------------------------------------------------------- driver

def build(ingest: dict, matched: dict, specs: dict[str, dict], shipments: list[dict]) -> dict:
    ships = {s["shipment_id"]: s for s in shipments}
    m_by_key = {r["line_key"]: r for r in matched["lines"]}
    anomalies_by_line, anomalies_by_doc = defaultdict(list), defaultdict(list)
    for a in ingest["anomalies"]:
        (anomalies_by_line[a["line_key"]] if a["line_key"] else anomalies_by_doc[a["doc_id"]]).append(a)

    first_billed: dict[str, str] = {}
    priced: dict[str, dict] = {}
    for ln in ingest["lines"]:
        if ln["doc_type"] != "invoice":
            continue
        m = m_by_key[ln["line_key"]]
        if m["match"] == "unmatched" or m["match"] == "ambiguous_ref":
            rec = _base(ln, m)
            rec["anomaly_codes"] = [a["code"] for a in anomalies_by_line[ln["line_key"]]]
            unm_rule = next((r for r in specs[ln["carrier"]].get("document_rules", [])
                             if r["id"] == "unmatched_lines_not_payable"), None)
            rec.update(disposition="escalate",
                       rule="R_UNMATCHED" if m["match"] == "unmatched" else "R_AMBIGUOUS_MATCH",
                       contract_clause=unm_rule["clause"] if unm_rule else None)
            hint_txt = "; ".join(h["detail"] for h in m["hints"])
            rec["reasons"].append({"code": m["match"].upper(), "detail": hint_txt or "no shipment with this reference"})
            rec["justification"] = (f"No BlueFin shipment matches {ln['consignment_ref']}, so no contract amount "
                                    f"can be computed"
                                    + (f"; {unm_rule['rule']} ({unm_rule['clause']})" if unm_rule else "")
                                    + (f". Possible intended reference: {hint_txt}." if hint_txt else ".")
                                    + f" Billed {inr(rec['billed_amount'])} is held pending the carrier's correction.")
            priced[ln["line_key"]] = rec
            continue
        sid = m["shipment_id"]
        dup_of = first_billed.get(sid)
        first_billed.setdefault(sid, ln["line_key"])
        priced[ln["line_key"]] = price_invoice_line(ln, m, specs[ln["carrier"]], ships[sid],
                                                    anomalies_by_line[ln["line_key"]], dup_of)

    for ln in ingest["lines"]:
        if ln["doc_type"] != "credit_note":
            continue
        m = m_by_key[ln["line_key"]]
        originals = [priced[k] for k in (m.get("corrects_line_keys") or []) if k in priced]
        rec, new_orig = price_credit(ln, m, originals, specs[ln["carrier"]])
        rec["anomaly_codes"] = [a["code"] for a in anomalies_by_line[ln["line_key"]]]
        priced[ln["line_key"]] = rec
        if new_orig is not None:
            priced[new_orig["line_key"]] = new_orig

    lines_out = [priced[ln["line_key"]] for ln in ingest["lines"]]
    by_doc = defaultdict(list)
    for ln in ingest["lines"]:
        by_doc[ln["doc_id"]].append(ln)
    findings = []
    for doc in ingest["documents"]:
        doc_lines = by_doc[doc["doc_id"]]
        findings += invoice_findings(doc, doc_lines, [priced[l["line_key"]] for l in doc_lines],
                                     specs[doc["carrier"]], shipments,
                                     [a for a in anomalies_by_doc.get(doc["doc_id"], []) if not a["line_key"]])
    return {"stage": "price", "line_count": len(lines_out), "lines": lines_out, "invoice_findings": findings,
            "rules": sorted({r["rule"] for r in lines_out} | {f["rule"] for f in findings})}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ingest", required=True, type=Path)
    ap.add_argument("--match", required=True, type=Path)
    ap.add_argument("--rate-specs-file", required=True, type=Path, help="JSON file {carrier: spec_path}")
    ap.add_argument("--shipments", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args(argv)
    ingest = money.loads(a.ingest.read_text())
    matched = money.loads(a.match.read_text())
    specs = {c: money.loads(Path(p).read_text()) for c, p in json.loads(a.rate_specs_file.read_text()).items()}
    missing = sorted({ln["carrier"] for ln in ingest["lines"]} - set(specs))
    if missing:
        raise SystemExit(f"price: no rate spec for carriers {missing}")
    result = build(ingest, matched, specs, money.loads(a.shipments.read_text()))
    for r in result["lines"]:
        if r["disposition"] is None:
            raise SystemExit(f"price: line {r['line_key']} left without a disposition")
    tmp = a.out.with_suffix(a.out.suffix + ".tmp")
    tmp.write_text(money.dumps({"_session_id": "script:price", **result}, indent=2) + "\n")
    tmp.replace(a.out)
    c = defaultdict(int)
    for r in result["lines"]:
        c[r["disposition"]] += 1
    # Routes the flow: adjudicate runs only when a rule left a real choice.
    print(f"FLOWSTATE_OUTPUT_review_count={sum(1 for r in result['lines'] if r['review'])}")
    print(f"price: {dict(c)}; {len(result['invoice_findings'])} invoice findings; "
          f"{sum(r['review'] for r in result['lines'])} lines flagged for review", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

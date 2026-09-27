"""Stage 4 price: rule outcomes on a fictional carrier (no real-contract specs in tests)."""
import copy
import sys
import unittest
from decimal import Decimal as D
from pathlib import Path

FLOW = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLOW))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from reconlib import price  # noqa: E402
from test_ratespec import KESTREL  # noqa: E402

SPEC = copy.deepcopy(KESTREL)
SPEC["document_rules"] = [{"id": "credit_note_must_cover_full_correction", "rule": "full credit", "clause": "kestrel.md §7"}]
OPEN_SPEC = copy.deepcopy(SPEC)
OPEN_SPEC["accessorials_policy"] = {"closed_list": False, "clause": "kestrel.md §5"}


def shipment(sid="K-1", **kw):
    s = {"shipment_id": sid, "carrier": "kestrel", "carrier_consignment_ref": "KC-1", "ship_date": "2026-03-02",
         "distance_km": 100, "billed_weight_kg": 50, "declared_value_inr": 1000, "service_level": "standard",
         "special_handling": [], "delivery_status": "delivered"}
    return {**s, **kw}


def line(key="INV-1#1", doc="INV-1", ref="KC-1", billed="1125.00", comps=None, doc_type="invoice", corrects=None):
    return {"line_key": key, "doc_id": doc, "doc_type": doc_type, "carrier": "kestrel", "line_no": 1,
            "consignment_ref": ref, "billed_amount": D(billed),
            "components": comps if comps is not None else [{"label": "Freight incl. fuel", "amount": D(billed)}],
            "facts": {}, "corrects": corrects, "text_notes": [], "source": {}}


M = {"shipment_id": "K-1", "match": "exact_ref", "fact_conflicts": [], "hints": [], "corrects_line_keys": None}


class LineRulesTest(unittest.TestCase):
    # standard 50 kg, 100 km: base 1000, fuel 125 -> 1125.00
    def test_matches(self):
        r = price.price_invoice_line(line(), M, SPEC, shipment(), [], None)
        self.assertEqual((r["disposition"], r["rule"], r["delta"]), ("accept", "R_MATCHES", D("0.00")))

    def test_rate_error_is_dispute_with_amount_differs(self):
        r = price.price_invoice_line(line(billed="1150.00"), M, SPEC, shipment(), [], None)
        self.assertEqual((r["disposition"], r["delta"]), ("dispute", D("25.00")))
        self.assertEqual([x["code"] for x in r["reasons"]], ["AMOUNT_DIFFERS"])

    def test_unlisted_charge_closed_list_is_dispute(self):
        comps = [{"label": "Freight incl. fuel", "amount": D("1125.00")}, {"label": "Detention", "amount": D("500")}]
        r = price.price_invoice_line(line(billed="1625.00", comps=comps), M, SPEC, shipment(), [], None)
        self.assertEqual(r["disposition"], "dispute")
        self.assertIn("UNLISTED_NOT_PAYABLE", [x["code"] for x in r["reasons"]])

    def test_unlisted_charge_open_schedule_is_escalated_for_review(self):
        comps = [{"label": "Freight incl. fuel", "amount": D("1125.00")}, {"label": "Detention", "amount": D("500")}]
        r = price.price_invoice_line(line(billed="1625.00", comps=comps), M, OPEN_SPEC, shipment(), [], None)
        self.assertEqual((r["disposition"], r["rule"], r["review"]), ("escalate", "R_SILENT_CHARGE", True))

    def test_charge_whose_condition_is_not_met(self):
        comps = [{"label": "Freight incl. fuel", "amount": D("1125.00")}, {"label": "Fragile handling", "amount": D("99")}]
        r = price.price_invoice_line(line(billed="1224.00", comps=comps), M, SPEC, shipment(), [], None)
        self.assertIn("CONDITION_NOT_MET", [x["code"] for x in r["reasons"]])

    def test_single_freight_column_covering_several_components(self):
        comps = [{"label": "freight", "amount": D("1200.00")}]
        r = price.price_invoice_line(line(billed="1200.00", comps=comps), M, SPEC, shipment(), [], None)
        self.assertEqual([x["code"] for x in r["reasons"]], ["AMOUNT_DIFFERS"])

    def test_printed_arithmetic_error(self):
        comps = [{"label": "Freight incl. fuel", "amount": D("1125.00")}]
        r = price.price_invoice_line(line(billed="1165.00", comps=comps), M, SPEC, shipment(), [], None)
        self.assertIn("PRINTED_ARITHMETIC", [x["code"] for x in r["reasons"]])
        self.assertEqual(r["delta"], D("40.00"))

    def test_underbilled_is_accepted_and_noted(self):
        r = price.price_invoice_line(line(billed="1100.00"), M, SPEC, shipment(), [], None)
        self.assertEqual((r["disposition"], r["rule"]), ("accept", "R_UNDERBILLED"))

    def test_out_of_card_is_escalated_with_null_amounts(self):
        r = price.price_invoice_line(line(billed="9999.00"), M, SPEC, shipment(billed_weight_kg=5000), [], None)
        self.assertEqual((r["disposition"], r["expected_amount"], r["delta"]), ("escalate", None, None))

    def test_duplicate_is_disputed_in_full(self):
        r = price.price_invoice_line(line(), M, SPEC, shipment(), [], "INV-0#4")
        self.assertEqual((r["disposition"], r["expected_amount"], r["delta"]), ("dispute", D("0.00"), D("1125.00")))


class CreditTest(unittest.TestCase):
    def orig(self, billed):
        return price.price_invoice_line(line(billed=billed), M, SPEC, shipment(), [], None)

    def credit(self, amount):
        return line(key="CN-1#1", doc="CN-1", billed=amount, comps=[], doc_type="credit_note",
                    corrects={"invoice": "INV-1", "consignment_ref": "KC-1"})

    def test_full_credit_accepts_both(self):
        c, o = price.price_credit(self.credit("-75.00"), M, [self.orig("1200.00")], SPEC)
        self.assertEqual((c["disposition"], c["rule"], c["delta"]), ("accept", "R_CREDIT_FULL", D("0.00")))
        self.assertEqual((o["disposition"], o["rule"]), ("accept", "R_CORRECTED_BY_CREDIT"))

    def test_partial_credit_disputes_remainder_once_on_credit_line(self):
        c, o = price.price_credit(self.credit("-50.00"), M, [self.orig("1200.00")], SPEC)
        self.assertEqual((c["disposition"], c["delta"]), ("dispute", D("25.00")))
        self.assertEqual(o["disposition"], "accept")          # not counted twice
        self.assertIn("§7", c["contract_clause"])                # the full-credit rule is cited

    def test_credit_against_correct_line(self):
        c, o = price.price_credit(self.credit("-10.00"), M, [self.orig("1125.00")], SPEC)
        self.assertEqual((c["disposition"], c["rule"], o), ("accept", "R_CREDIT_UNNEEDED", None))

    def test_credit_without_single_target_escalates(self):
        c, _ = price.price_credit(self.credit("-10.00"), M, [], SPEC)
        self.assertEqual(c["disposition"], "escalate")


class AdjustmentTest(unittest.TestCase):
    DOC = {"doc_id": "INV-1", "doc_type": "invoice", "carrier": "kestrel", "period": "2026-03",
           "header_adjustments": []}

    def run_adj(self, printed, n_ships=11, pending=False):
        doc = dict(self.DOC, header_adjustments=[{"label": "discount", "amount": D(printed)}] if printed else [])
        lines = [line(billed="1000.00")]
        priced = [{"expected_amount": None if pending else D("1000.00")}]
        ships = [shipment(sid=f"K-{i}") for i in range(n_ships)]
        return price.invoice_findings(doc, lines, priced, SPEC, ships, [])[0]

    def test_missing_discount_disputed(self):
        f = self.run_adj("0")
        self.assertEqual((f["disposition"], f["amount_impact"]), ("dispute", D("50.00")))

    def test_correct_discount_accepted(self):
        self.assertEqual(self.run_adj("-50.00")["rule"], "R_ADJ_OK")

    def test_not_due_when_below_threshold(self):
        f = self.run_adj("-50.00", n_ships=10)
        self.assertEqual((f["disposition"], f["rule"]), ("accept", "R_ADJ_EXCESS"))


class ClauseTest(unittest.TestCase):
    def test_normalises_and_dedupes(self):
        self.assertEqual(price.clause_list(["a.md §3", "a.md §1, §3", "a.md §1"]), "a.md §1, §3")


if __name__ == "__main__":
    unittest.main()

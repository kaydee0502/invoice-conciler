"""Rate-spec engine + extraction cross-check.

Fixtures describe a FICTIONAL carrier ("kestrel"). Specs for the real
contracts are only ever produced by extraction workers during a run; keeping
hand-written real specs out of the repo keeps the run honest.

Run: factory/.. orchestrator/.venv/bin/python -m unittest discover -s factory/flows/freight-recon/tests
"""
import copy
import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path

FLOW = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLOW))

from reconlib.ratespec import check_spec, invoice_adjustments, price_shipment  # noqa: E402
from reconlib.specdiff import decide  # noqa: E402

C = "kestrel.md"
KESTREL = {
    "_session_id": "fixture", "carrier": "kestrel", "contract_file": C, "agreement_ref": "K/1",
    "term": {"from": "2026-01-01", "to": "2026-12-31", "clause": f"{C} header"},
    "services_offered": {"values": ["standard", "express"], "clause": f"{C} §1"},
    "derived_quantities": [
        {"id": "chargeable_kg", "expr": {"max": [{"field": "billed_weight_kg"}, {"const": 20}]}, "clause": f"{C} §1"}
    ],
    "charges": [
        {"id": "base", "label": "Base freight", "clause": f"{C} §1",
         "amount": {"mul": [{"field": "distance_km"}, {"tiered": {"on": {"qty": "chargeable_kg"}, "tiers": [
             {"lte": 100, "value": {"const": "10.00"}},
             {"gt": 100, "lte": 1000, "value": {"const": "15.00"}},
             {"gt": 1000, "out_of_card": True}]}}]}},
        {"id": "express", "label": "Express premium", "clause": f"{C} §2",
         "when": {"field": "service_level", "op": "eq", "value": "express"},
         "amount": {"percent": 10, "of": {"charge": "base"}}},
        {"id": "fuel", "label": "Fuel surcharge", "clause": f"{C} §3",
         "amount": {"percent": "12.5", "of": {"add": [{"charge": "base"}, {"charge": "express"}]}}},
        {"id": "fragile", "label": "Fragile handling", "clause": f"{C} §4",
         "when": {"field": "special_handling", "op": "contains", "value": "fragile"},
         "amount": {"const": 99}},
    ],
    "accessorials_policy": {"closed_list": True, "clause": f"{C} §5"},
    "invoice_adjustments": [
        {"id": "volume", "label": "Volume discount", "kind": "percent_of_invoice_total", "percent": -5,
         "when": {"metric": "consignments_in_calendar_month", "op": "gt", "value": 10}, "clause": f"{C} §6"}],
    "document_rules": [],
    "ambiguities": [],
}


def ship(**kw):
    base = {"shipment_id": "K-1", "carrier": "kestrel", "ship_date": "2026-03-01", "distance_km": 100,
            "billed_weight_kg": 50, "declared_value_inr": 1000, "service_level": "standard",
            "special_handling": [], "delivery_status": "delivered"}
    return {**base, **kw}


class EngineTest(unittest.TestCase):
    def test_schema_accepts_fixture(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed (use orchestrator/.venv/bin/python)")
        schema = json.loads((FLOW / "definitions" / "rate-spec.json").read_text())
        jsonschema.validate(KESTREL, schema)
        bad = copy.deepcopy(KESTREL)
        bad["charges"][0]["amount"] = {"pow": [1, 2]}
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(bad, schema)

    def test_minimum_weight_and_tier(self):
        r = price_shipment(KESTREL, ship(billed_weight_kg=5))          # chargeable 20 -> 10/km
        self.assertEqual(r.amount, Decimal("1125.00"))                 # 1000 * 1.125
        r = price_shipment(KESTREL, ship(billed_weight_kg=101))        # 15/km
        self.assertEqual(r.amount, Decimal("1687.50"))

    def test_express_premium_feeds_fuel(self):
        r = price_shipment(KESTREL, ship(service_level="express"))     # 1000 +100, *1.125
        self.assertEqual(r.amount, Decimal("1237.50"))
        self.assertEqual([c["id"] for c in r.charges], ["base", "express", "fuel"])

    def test_flat_accessorial(self):
        r = price_shipment(KESTREL, ship(special_handling=["fragile"]))
        self.assertEqual(r.amount, Decimal("1224.00"))

    def test_out_of_card_is_undetermined_not_error(self):
        r = price_shipment(KESTREL, ship(billed_weight_kg=1500))
        self.assertEqual(r.status, "undetermined")
        self.assertIsNone(r.amount)
        self.assertEqual(r.reasons[0]["reason"], "out_of_card")

    def test_tier_gap_is_undetermined(self):
        gap = copy.deepcopy(KESTREL)
        gap["charges"][0]["amount"]["mul"][1]["tiered"]["tiers"][0] = {"lt": 100, "value": {"const": 10}}
        r = price_shipment(gap, ship(billed_weight_kg=100))
        self.assertEqual((r.status, r.reasons[0]["reason"]), ("undetermined", "tier_gap"))

    def test_service_not_offered_and_outside_term(self):
        self.assertEqual(price_shipment(KESTREL, ship(service_level="overnight")).reasons[0]["reason"],
                         "service_not_offered")
        self.assertEqual(price_shipment(KESTREL, ship(ship_date="2027-02-01")).reasons[0]["reason"],
                         "outside_term")

    def test_undetermined_derived_quantity_propagates(self):
        # Rate band held in a derived quantity (a legitimate encoding): a value in
        # the gap must price as undetermined, not raise a SpecError downstream.
        sp = copy.deepcopy(KESTREL)
        sp["derived_quantities"].append({"id": "rate", "clause": f"{C} §1", "expr": {"tiered": {
            "on": {"qty": "chargeable_kg"}, "tiers": [{"lte": 100, "value": {"const": 10}},
                                                       {"gte": 101, "value": {"const": 15}}]}}})
        sp["charges"][0]["amount"] = {"mul": [{"field": "distance_km"}, {"qty": "rate"}]}
        self.assertEqual(check_spec(sp), [])
        r = price_shipment(sp, ship(billed_weight_kg=Decimal("100.5")))
        self.assertEqual((r.status, r.reasons[0]["reason"]), ("undetermined", "tier_gap"))
        self.assertEqual(price_shipment(sp, ship(billed_weight_kg=101)).amount, Decimal("1687.50"))

    def test_declared_ambiguity_triggers(self):
        amb = copy.deepcopy(KESTREL)
        amb["ambiguities"] = [{"id": "edge", "clause": f"{C} §1", "description": "exactly 100 kg unclear",
                               "when": {"qty": "chargeable_kg", "op": "eq", "value": 100}}]
        self.assertEqual(price_shipment(amb, ship(billed_weight_kg=100)).status, "undetermined")
        self.assertEqual(price_shipment(amb, ship(billed_weight_kg=99)).status, "determined")

    def test_invoice_adjustment(self):
        m = {"consignments_in_calendar_month": 11, "invoice_line_total": Decimal("20000")}
        self.assertEqual(invoice_adjustments(KESTREL, m)[0]["amount"], Decimal("-1000.00"))
        m["consignments_in_calendar_month"] = 10
        self.assertEqual(invoice_adjustments(KESTREL, m), [])

    def test_static_check_flags_overlapping_tiers(self):
        bad = copy.deepcopy(KESTREL)
        bad["charges"][0]["amount"]["mul"][1]["tiered"]["tiers"][1]["gt"] = 90
        self.assertTrue(any("overlapping" in p for p in check_spec(bad)))
        self.assertEqual(check_spec(KESTREL), [])


class SpecDiffTest(unittest.TestCase):
    SHIPMENTS = [ship(shipment_id=f"K-{i}", billed_weight_kg=w, distance_km=d)
                 for i, (w, d) in enumerate([(5, 40), (100, 300), (700, 90)])]

    def test_identical_semantics_different_shape_agree(self):
        b = copy.deepcopy(KESTREL)
        b["charges"][0]["label"] = "Freight (distance)"          # wording differs
        b["charges"][2]["amount"]["percent"] = Decimal("12.50")  # same value, different form
        self.assertTrue(decide({"a": KESTREL, "b": b}, self.SHIPMENTS)["agreed"])

    def test_boundary_off_by_one_disagrees(self):
        b = copy.deepcopy(KESTREL)
        tiers = b["charges"][0]["amount"]["mul"][1]["tiered"]["tiers"]
        tiers[0] = {"lt": 100, "value": {"const": "10.00"}}
        tiers[1] = {"gte": 100, "lte": 1000, "value": {"const": "15.00"}}
        r = decide({"a": KESTREL, "b": b}, self.SHIPMENTS)   # no real shipment sits on 100 kg
        self.assertFalse(r["agreed"])

    def test_missed_accessorial_disagrees(self):
        b = copy.deepcopy(KESTREL)
        b["charges"] = b["charges"][:3]
        self.assertFalse(decide({"a": KESTREL, "b": b}, self.SHIPMENTS)["agreed"])

    def test_majority_of_three(self):
        wrong = copy.deepcopy(KESTREL)
        wrong["charges"][2]["amount"]["percent"] = 15
        r = decide({"a": KESTREL, "b": wrong, "c": copy.deepcopy(KESTREL)}, self.SHIPMENTS)
        self.assertTrue(r["agreed"])
        self.assertIn(r["chosen"], ("a", "c"))

    def test_static_invalid_candidate_excluded(self):
        broken = copy.deepcopy(KESTREL)
        broken["charges"][1]["amount"] = {"charge": "nope"}
        r = decide({"a": broken, "b": KESTREL}, self.SHIPMENTS)
        self.assertFalse(r["agreed"])
        self.assertTrue(r["static_problems"]["a"])


if __name__ == "__main__":
    unittest.main()

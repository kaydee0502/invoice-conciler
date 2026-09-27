"""Stage 3 match: exact-only matching, fact conflicts, credit links."""
import copy
import sys
import unittest
from decimal import Decimal
from pathlib import Path

FLOW = Path(__file__).resolve().parents[1]
REPO = FLOW.parents[2]
sys.path.insert(0, str(FLOW))

from reconlib import ingest, match, money  # noqa: E402

SHIPMENTS = money.loads((REPO / "data" / "shipments.json").read_text())


class MatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ing = ingest.build(REPO / "data" / "invoices")
        cls.res = match.build(cls.ing, SHIPMENTS)
        cls.by_key = {r["line_key"]: r for r in cls.res["lines"]}

    def test_every_line_exactly_once(self):
        self.assertEqual([r["line_key"] for r in self.res["lines"]], [l["line_key"] for l in self.ing["lines"]])

    def test_matched_lines_point_at_same_carrier_and_ref(self):
        ships = {s["shipment_id"]: s for s in SHIPMENTS}
        for r in self.res["lines"]:
            if r["shipment_id"]:
                s = ships[r["shipment_id"]]
                self.assertEqual(s["carrier"], r["carrier"])
                self.assertEqual(match.norm_ref(s["carrier_consignment_ref"]), match.norm_ref(r["consignment_ref"]))

    def test_near_miss_is_hint_not_match(self):
        unmatched = [r for r in self.res["lines"] if r["match"] == "unmatched"]
        self.assertTrue(unmatched)
        for r in unmatched:
            self.assertIsNone(r["shipment_id"])

    def test_credit_note_links_to_corrected_line(self):
        cn = [r for r in self.res["lines"] if r["doc_type"] == "credit_note"]
        self.assertTrue(cn)
        for r in cn:
            self.assertTrue(r["corrects_line_keys"], r)

    def test_fact_conflict_detected_and_tagged(self):
        ing = copy.deepcopy(self.ing)
        ln = next(l for l in ing["lines"] if l["carrier"] == "falcon" and l["doc_type"] == "invoice")
        ln["facts"]["weight_kg"] = ln["facts"]["weight_kg"] + 100
        ln["facts"]["origin_city"] = "Atlantis"
        rec = next(r for r in match.build(ing, SHIPMENTS)["lines"] if r["line_key"] == ln["line_key"])
        got = {c["fact"]: c["materiality"] for c in rec["fact_conflicts"]}
        self.assertEqual(got, {"weight_kg": "pricing", "origin_city": "informational"})

    def test_equal_numbers_in_different_forms_do_not_conflict(self):
        s = {"billed_weight_kg": 900, "distance_km": 650}
        line = {"facts": {"weight_kg": Decimal("900.0"), "distance_km": Decimal("650.00")}}
        self.assertEqual(match.fact_conflicts(line, s), ([], []))

    def test_unknown_printed_fact_is_reported(self):
        line = {"facts": {"pallet_count": 3}}
        self.assertEqual(match.fact_conflicts(line, {"distance_km": 1})[1], ["pallet_count"])

    def test_near(self):
        self.assertTrue(match._near("SG-7969", "SG-7069"))
        self.assertTrue(match._near("SG-7012", "SG-7021"))   # transposition
        self.assertFalse(match._near("SG-7012", "SG-7210"))


if __name__ == "__main__":
    unittest.main()

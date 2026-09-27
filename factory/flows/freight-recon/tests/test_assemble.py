"""Stage 6 assemble: each invariant must refuse a violating input.

Uses ingest + match of the real sample and price on FICTIONAL-carrier-free
data: price is built from the sample with specs stubbed so every line prices
at its billed amount, then individual lines are corrupted per test.
"""
import copy
import sys
import unittest
from decimal import Decimal as D
from pathlib import Path

FLOW = Path(__file__).resolve().parents[1]
REPO = FLOW.parents[2]
sys.path.insert(0, str(FLOW))

from reconlib import assemble, ingest  # noqa: E402


def stub_price(ing):
    """A price result where every line is accepted at its billed amount (no contract involved)."""
    lines = []
    for l in ing["lines"]:
        lines.append({"line_key": l["line_key"], "invoice": l["doc_id"], "doc_type": l["doc_type"],
                      "carrier": l["carrier"], "consignment_ref": l["consignment_ref"], "shipment_id": "S",
                      "billed_amount": l["billed_amount"], "expected_amount": l["billed_amount"], "delta": D("0.00"),
                      "disposition": "accept", "rule": "R_MATCHES", "review": False, "allowed_dispositions": None,
                      "reasons": [], "justification": "ok", "contract_clause": None, "notes": [],
                      "corrects_line_keys": None, "hints": [], "fact_conflicts": []})
    return {"lines": lines, "invoice_findings": []}


class AssembleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ing = ingest.build(REPO / "data" / "invoices")

    def setUp(self):
        self.price = stub_price(self.ing)

    def refuses(self, code, adjudication=None):
        with self.assertRaises(assemble.InvariantError) as cm:
            assemble.build(self.ing, self.price, adjudication)
        self.assertTrue(str(cm.exception).startswith(code), str(cm.exception))

    def test_clean_build_and_schema(self):
        report, index = assemble.build(self.ing, self.price, None)
        assemble.validate_schema(report, REPO / "report.schema.json")
        self.assertEqual(report["summary"]["line_count"], self.ing["line_count"])
        self.assertEqual(index["memo_item_count"], 0)

    def test_every_anomaly_lands_in_notes(self):
        report, _ = assemble.build(self.ing, self.price, None)
        notes = " ".join(l.get("notes", "") for l in report["lines"])
        for a in self.ing["anomalies"]:
            if a["line_key"]:
                self.assertIn(a["code"], notes)

    def test_I2_missing_line(self):
        self.price["lines"].pop()
        self.refuses("I2")

    def test_I3_delta_arithmetic(self):
        self.price["lines"][0]["delta"] = D("1.00")
        self.refuses("I3")

    def test_I3_null_expected_must_escalate(self):
        self.price["lines"][0].update(expected_amount=None, delta=None, disposition="dispute")
        self.refuses("I3")

    def test_I7_credit_and_original_both_disputed(self):
        by = {l["line_key"]: l for l in self.price["lines"]}
        cn = next(l for l in self.price["lines"] if l["doc_type"] == "credit_note")
        orig_key = next(l["line_key"] for l in self.ing["lines"]
                        if l["doc_type"] == "invoice" and l["doc_id"] == next(
                            x for x in self.ing["lines"] if x["line_key"] == cn["line_key"])["corrects"]["invoice"])
        cn.update(disposition="dispute", corrects_line_keys=[orig_key])
        by[orig_key]["disposition"] = "dispute"
        self.refuses("I7")

    def test_I8_document_anomaly_needs_a_finding(self):
        self.ing = copy.deepcopy(self.ing)
        self.ing["anomalies"].append({"code": "DOC_TOTAL_MISMATCH", "doc_id": "ALPINE-0726", "line_key": None,
                                      "detail": "x"})
        self.refuses("I8")

    def test_I9_adjudication_rules(self):
        l = self.price["lines"][0]
        l.update(review=True, allowed_dispositions=["escalate", "dispute"], disposition="escalate")
        self.refuses("I9")                                                          # none supplied
        dec = lambda k, d: {"decisions": [{"line_key": k, "disposition": d, "justification": "x" * 30,
                                           "contract_clause": None}]}
        self.refuses("I9", dec(l["line_key"], "accept"))                            # outside allowed
        self.refuses("I9", dec(self.price["lines"][1]["line_key"], "escalate"))     # not under review
        report, index = assemble.build(self.ing, self.price, dec(l["line_key"], "dispute"))
        self.assertEqual(index["adjudicated_lines"], [l["line_key"]])

    def test_memo_index_covers_exactly_non_accept(self):
        self.price["lines"][3].update(disposition="dispute", expected_amount=D("0.00"),
                                      delta=self.price["lines"][3]["billed_amount"])
        report, index = assemble.build(self.ing, self.price, None)
        self.assertEqual([i["item_id"] for i in index["memo_items"]], [f"line:{self.price['lines'][3]['line_key']}"])


if __name__ == "__main__":
    unittest.main()

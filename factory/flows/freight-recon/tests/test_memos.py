"""Stage 7 memos: the checker rejects invented numbers; publish enforces the exact memo set."""
import json
import shutil
import sys
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path

FLOW = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLOW))

from reconlib import memos  # noqa: E402

ITEM = {"item_id": "line:INV-9#2", "kind": "line", "carrier": "kestrel", "memo_file": "INV-9__KC-2.md",
        "report_entry": {"invoice": "INV-9", "consignment_ref": "KC-2", "billed_amount": D("1625.00"),
                         "expected_amount": D("1125.00"), "delta": D("500.00"), "disposition": "dispute",
                         "justification": "Overbilled by ₹500.00 ...", "contract_clause": "kestrel.md §5"},
        "reasons": [{"code": "UNLISTED_NOT_PAYABLE", "amount": D("500.00")}]}

GOOD = """# Dispute: INV-9 KC-2

The carrier added a detention charge to consignment KC-2 on invoice INV-9. The contract lists no
such charge and says unlisted charges are not payable (kestrel.md §5).

Billed ₹1,625.00 against a contract amount of ₹1,125.00: a difference of ₹500.00.

Action: raise a dispute and ask the carrier for a credit note of ₹500.00 against INV-9, or written
evidence of a pre-agreed rate confirmation. Disposition: dispute.
"""


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.brief = {"batch_id": "k-1", "items": [{**ITEM, "allowed_amounts": sorted(memos._amounts(ITEM, set()))}]}

    def tearDown(self):
        shutil.rmtree(self.dir)

    def write(self, text, name="INV-9__KC-2.md"):
        (self.dir / name).write_text(text)

    def test_good_memo_passes(self):
        self.write(GOOD)
        self.assertEqual(memos.check_batch(self.brief, self.dir), [])

    def test_invented_amount_rejected(self):
        self.write(GOOD.replace("Action:", "With GST that is ₹590.00. Action:"))
        self.assertTrue(any("₹590.00" in p for p in memos.check_batch(self.brief, self.dir)))

    def test_missing_key_amount_and_disposition(self):
        self.write(GOOD.replace("₹500.00", "the difference").replace("Disposition: dispute.", "")
                   .replace("# Dispute", "# Item").replace("raise a dispute", "follow up"))
        probs = " ".join(memos.check_batch(self.brief, self.dir))
        self.assertIn("key amount", probs)
        self.assertIn("disposition", probs)

    def test_missing_and_extra_files(self):
        self.write(GOOD, name="OTHER.md")
        probs = " ".join(memos.check_batch(self.brief, self.dir))
        self.assertIn("missing", probs)
        self.assertIn("OTHER.md", probs)

    def test_retry_then_fail(self):
        self.write(GOOD.replace("₹500.00", "₹499.00"))
        bp = self.dir / "brief.json"
        bp.write_text(memos.money.dumps(self.brief))
        self.assertEqual(memos.main(["check", "--brief", str(bp), "--memos-dir", str(self.dir), "--attempt", "1"]), 0)
        self.assertEqual(memos.main(["check", "--brief", str(bp), "--memos-dir", str(self.dir), "--attempt", "2"]), 1)


class ManifestSchemaTest(unittest.TestCase):
    def test_manifest_accepts_the_batch_ids_plan_produces(self):
        """Regression: plan switched to integer batch ids and the worker's manifest
        (which copies batch_id from the brief) failed schema validation."""
        import jsonschema
        d = Path(tempfile.mkdtemp())
        try:
            batches = memos.plan({"memo_items": [ITEM], "memo_item_count": 1}, d, {"kestrel": "k.md"}, d, "py")
            schema = json.loads((FLOW.parent / "memo-batch" / "definitions" / "memo-manifest.json").read_text())
            manifest = {"_session_id": "s", "batch_id": batches[0]["batch_id"],
                        "memos": [{"item_id": ITEM["item_id"], "memo_file": ITEM["memo_file"]}]}
            jsonschema.validate(manifest, schema)
        finally:
            shutil.rmtree(d)


class PublishTest(unittest.TestCase):
    def test_exact_memo_set_and_stale_cleanup(self):
        root = Path(tempfile.mkdtemp())
        try:
            b = root / "batch"; b.mkdir()
            (b / "INV-9__KC-2.md").write_text(GOOD)
            report = root / "run" / "reconciliation-report.json"; report.parent.mkdir()
            report.write_text("{}")
            pub = root / "pub"; (pub / "memos").mkdir(parents=True)
            (pub / "memos" / "stale.md").write_text("old")
            idx = {"memo_items": [ITEM]}
            m = memos.publish(idx, [b], report, report.parent, pub)
            self.assertEqual(sorted(m["memos"]), ["INV-9__KC-2.md"])
            self.assertFalse((pub / "memos" / "stale.md").exists())
            self.assertTrue((pub / "reconciliation-report.json").exists())
            with self.assertRaises(SystemExit):
                memos.publish({"memo_items": [ITEM, {**ITEM, "memo_file": "X.md"}]}, [b], report, report.parent, None)
        finally:
            shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main()

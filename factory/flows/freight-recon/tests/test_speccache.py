"""Spec cache: hit on an unchanged contract, miss when anything that shapes extraction changes."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

FLOW = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLOW))

from reconlib import items, speccache  # noqa: E402


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.contract = self.d / "kestrel-cargo.md"
        self.contract.write_text("1. Freight is 10 per km.\n")
        self.spec = self.d / "spec.json"
        self.spec.write_text(json.dumps({"carrier": "kestrel", "_session_id": "x"}))
        self.cache = self.d / "cache"

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_store_then_hit_with_provenance(self):
        self.assertIsNone(speccache.lookup(self.cache, "kestrel", self.contract))
        speccache.store(self.cache, "kestrel", self.contract, self.spec, "run-1")
        hit = speccache.lookup(self.cache, "kestrel", self.contract)
        self.assertIsNotNone(hit)
        self.assertEqual(json.loads(hit.read_text())["_provenance"]["cache"]["source_run"], "run-1")

    def test_contract_edit_invalidates(self):
        speccache.store(self.cache, "kestrel", self.contract, self.spec, "run-1")
        self.contract.write_text("1. Freight is 11 per km.\n")
        self.assertIsNone(speccache.lookup(self.cache, "kestrel", self.contract))

    def test_key_covers_schema_and_prompt(self):
        k = speccache.key("kestrel", self.contract)
        self.assertNotEqual(k, speccache.key("other", self.contract))
        orig = speccache.PROMPT
        try:
            alt = self.d / "prompt.md"
            alt.write_text("different prompt")
            speccache.PROMPT = alt
            self.assertNotEqual(k, speccache.key("kestrel", self.contract))
        finally:
            speccache.PROMPT = orig


class ItemsTest(unittest.TestCase):
    def test_resolves_one_item(self):
        d = Path(tempfile.mkdtemp())
        try:
            plan = d / "plan.json"
            plan.write_text(json.dumps({"batches": [{"batch_id": "a-1", "brief_path": "/x", "item_count": 3},
                                                    {"batch_id": "a-2", "brief_path": "/y", "item_count": 1}]}))
            from io import StringIO
            from contextlib import redirect_stdout
            buf = StringIO()
            with redirect_stdout(buf):
                items.main(["--plan", str(plan), "--list", "batches", "--key", "batch_id", "--id", "a-2"])
            self.assertEqual(buf.getvalue().split(), ["FLOWSTATE_OUTPUT_brief_path=/y", "FLOWSTATE_OUTPUT_item_count=1"])
            with self.assertRaises(SystemExit):
                items.main(["--plan", str(plan), "--list", "batches", "--key", "batch_id", "--id", "zzz"])
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()

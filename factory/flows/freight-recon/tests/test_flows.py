"""Flow-definition consistency checks that would otherwise only fail mid-run."""
import re
import unittest
from pathlib import Path

import yaml

FLOWS = Path(__file__).resolve().parents[2]
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ExtractionPromptTest(unittest.TestCase):
    D = FLOWS / "contract-spec"

    def test_three_prompts_differ_only_in_output_var(self):
        a = (self.D / "prompts/extract_a.md").read_text()
        for x in "bc":
            other = (self.D / f"prompts/extract_{x}.md").read_text()
            self.assertEqual(a.replace("{spec_a}", f"{{spec_{x}}}"), other, f"extract_{x}.md drifted from extract_a.md")

    def test_every_placeholder_is_a_declared_variable(self):
        for flow in ("contract-spec", "memo-batch", "freight-recon"):
            d = FLOWS / flow
            declared = set(yaml.safe_load((d / f"{flow}.flow.yml").read_text())["variables"])
            for p in (d / "prompts").glob("*.md"):
                used = {n for n in PLACEHOLDER.findall(p.read_text()) if not n.startswith("_")}
                self.assertFalse(used - declared, f"{flow}/{p.name} uses undeclared placeholders {used - declared}")

    def test_plan_items_supply_every_child_input(self):
        """Each child's load_item node must be able to resolve every variable it
        declares as required from the plan entry the parent writes."""
        import shutil, tempfile, sys
        sys.path.insert(0, str(FLOWS / "freight-recon"))
        from reconlib import ingest, memos, plan
        repo = FLOWS.parents[1]
        run = Path(tempfile.mkdtemp())
        try:
            ing = run / "ingest.json"
            ing.write_text(memos.money.dumps(ingest.build(repo / "data" / "invoices")))
            p = plan.plan(ing, repo / "data" / "shipments.json", repo / "data" / "contracts",
                          FLOWS / "freight-recon", "python3", run, None)
            need = yaml.safe_load((FLOWS / "contract-spec" / "contract-spec.flow.yml").read_text())
            required = need["output_schemas"]["load_outputs"]["required_variables"]
            for it in p["contract_items"]:
                self.assertFalse(set(required) - set(it), f"plan entry lacks {set(required) - set(it)}")
            self.assertEqual(p["to_extract"], sorted(i["carrier"] for i in p["contract_items"]))
        finally:
            shutil.rmtree(run)

    def test_memo_fanout_list_fits_at_scale(self):
        """flowstate caps variables at 16 KB: the memo fanout list must fit even for
        a very large run (100k non-accept items across 40 carriers)."""
        import sys, tempfile, shutil
        sys.path.insert(0, str(FLOWS / "freight-recon"))
        from reconlib import memos
        n = 100_000
        idx = {"memo_items": [{"item_id": f"line:X#{i}", "carrier": f"c{i % 40:02d}", "memo_file": f"m{i}.md",
                               "report_entry": {}} for i in range(n)], "memo_item_count": n}
        d = Path(tempfile.mkdtemp())
        try:
            orig = memos._amounts
            memos._amounts = lambda it, out: out      # speed: amounts are irrelevant here
            batches = memos.plan(idx, d, {f"c{i:02d}": "x" for i in range(40)}, d, "py")
        finally:
            memos._amounts = orig
            shutil.rmtree(d)
        ids = [b["batch_id"] for b in batches]
        self.assertLess(len(str(ids).encode()), 16384, f"{len(ids)} batch ids")
        self.assertEqual(sum(b["item_count"] for b in batches), n)


if __name__ == "__main__":
    unittest.main()

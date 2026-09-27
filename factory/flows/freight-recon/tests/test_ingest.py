"""Ingest must be total: account for every document and every rupee, or refuse.

Run: python3 -m unittest discover -s factory/flows/freight-recon/tests
"""
import shutil
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

FLOW = Path(__file__).resolve().parents[1]
REPO = FLOW.parents[2]
sys.path.insert(0, str(FLOW))

from reconlib import ingest  # noqa: E402

SAMPLE = REPO / "data" / "invoices"


class IngestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dir = self.tmp / "invoices"
        shutil.copytree(SAMPLE, self.dir)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def edit(self, name, old, new, count=1):
        p = self.dir / name
        text = p.read_bytes().decode()
        self.assertIn(old, text)
        p.write_bytes(text.replace(old, new, count).encode())

    def assertRefuses(self, needle):
        with self.assertRaises(SystemExit) as cm:
            ingest.build(self.dir)
        self.assertIn(needle, str(cm.exception))

    # --- the sample itself -------------------------------------------------
    def test_sample_is_fully_accounted_for(self):
        r = ingest.build(self.dir)
        self.assertEqual(r["document_count"], 17)
        self.assertEqual(r["line_count"], sum(d["line_count"] for d in r["documents"]))
        self.assertTrue(all(f.endswith(":Zone.Identifier") for f in r["skipped_files"]))
        keys = [ln["line_key"] for ln in r["lines"]]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertNotIn("DOC_TOTAL_MISMATCH", {a["code"] for a in r["anomalies"]})

    # --- hard failures: the system cannot account for the input -------------
    def test_unclaimed_file_refused(self):
        (self.dir / "mystery.pdf").write_text("%PDF-1.4 carrier invoice")
        self.assertRefuses("mystery.pdf")

    def test_falcon_unparseable_money_refused(self):
        self.edit("FALCON-2026-07A.txt", "   LINE TOTAL: Rs 17,472.00",
                  "   Loading charge Rs 300\n   LINE TOTAL: Rs 17,472.00")
        self.assertRefuses("unparsed monetary text")

    def test_falcon_unknown_header_refused(self):
        self.edit("FALCON-2026-07A.txt", "FALCON FREIGHT PVT LTD\n",
                  "FALCON FREIGHT PVT LTD\nGSTIN 27ABCDE1234F1Z5\n")
        self.assertRefuses("unrecognised header line")

    def test_alpine_unknown_key_refused(self):
        self.edit("ALPINE-0726.json", '"discount": 0.0', '"discount": 0.0, "fuel_levy": 812.5')
        self.assertRefuses("unknown top-level keys")

    def test_alpine_unknown_line_key_refused(self):
        self.edit("ALPINE-0726.json", '"handling_fee": 0,', '"handling_fee": 0, "oda_fee": 90,')
        self.assertRefuses("line keys differ")

    def test_sagar_short_row_refused(self):
        self.edit("SAGAR-JUL-1.csv", "SG-7001,2026-07-02,260,60,1680.00,0.00,1680.00",
                  "SG-7001,2026-07-02,260,60,1680.00,1680.00")
        self.assertRefuses("expected 7 columns")

    def test_duplicate_document_id_refused(self):
        shutil.copy(self.dir / "ALPINE-0726.json", self.dir / "ALPINE-0726-copy.json")
        self.assertRefuses("appears in several files")

    # --- anomalies: the carrier's document is inconsistent -----------------
    def test_total_mismatch_is_anomaly_not_failure(self):
        self.edit("FALCON-2026-07A.txt", "INVOICE TOTAL: Rs 181,637.60", "INVOICE TOTAL: Rs 181,737.60")
        r = ingest.build(self.dir)
        hit = [a for a in r["anomalies"] if a["code"] == "DOC_TOTAL_MISMATCH"]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["difference"], Decimal("100.00"))

    def test_credit_note_to_missing_invoice_is_anomaly(self):
        self.edit("SAGAR-CN-01.csv", "SAGAR-AUG-1,SG-7056", "SAGAR-AUG-9,SG-7056")
        r = ingest.build(self.dir)
        self.assertIn("CREDIT_TARGET_UNKNOWN", {a["code"] for a in r["anomalies"]})

    def test_crlf_and_lf_parse_identically(self):
        a = ingest.build(self.dir)
        p = self.dir / "SAGAR-JUL-1.csv"
        p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n"))
        b = ingest.build(self.dir)
        self.assertEqual([ln["billed_amount"] for ln in a["lines"]], [ln["billed_amount"] for ln in b["lines"]])


if __name__ == "__main__":
    unittest.main()

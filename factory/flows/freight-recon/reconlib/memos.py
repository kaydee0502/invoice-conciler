"""Stage 7 — memos: plan batches, check worker output, verify + publish.

plan     report-index.json -> per-carrier batches of at most N items, each with
         a brief file holding everything a memo may say, including the exact
         set of rupee amounts it may quote. Emits the fanout list.
check    one batch: a memo per item, naming the invoice + reference and the
         disposition, quoting the delta (or billed amount), and quoting NO
         rupee amount that is not in the item's brief. Emits memo_next =
         done | retry (with feedback); exits 1 when retries are exhausted.
publish  all batches: memo files == memo items exactly (no missing, no
         extra, none for accepted items); copies report + memos to the run
         dir and the publish dir, and writes a sha256 manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import money
from .money import q

BATCH_SIZE = 8
AMOUNT = re.compile(r"(?:₹|Rs\.?|INR)\s*(-?[\d,]+(?:\.\d+)?)")
WORDS = (60, 450)


# --------------------------------------------------------------------------- plan

def _amounts(obj, out: set[Decimal]) -> set[Decimal]:
    if isinstance(obj, Decimal):
        out.add(q(abs(obj)))
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out.add(q(abs(Decimal(str(obj)))))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k not in ("line_no", "tendered_in_month", "distance_km", "billed_weight_kg", "weight_kg",
                         "actual_weight_kg", "chargeable_weight_kg", "percent"):
                _amounts(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _amounts(v, out)
    elif isinstance(obj, str):
        for m in AMOUNT.finditer(obj):
            try:
                out.add(q(abs(Decimal(m.group(1).replace(",", "")))))
            except InvalidOperation:
                pass
    return out


MAX_BATCHES = 2500   # integer ids ~5 bytes each: the id list stays well under flowstate's 16 KB cap


def plan(index: dict, run_dir: Path, contracts: dict[str, str], flow_dir: Path, python: str) -> list[dict]:
    """Batch items per carrier. Batch ids are small integers so the fanout list
    stays tiny; above MAX_BATCHES * BATCH_SIZE items the batch size grows
    instead of the id list (larger batches rather than a failed run)."""
    items = index["memo_items"]
    size = max(BATCH_SIZE, -(-len(items) // MAX_BATCHES))
    by_carrier: dict[str, list[dict]] = {}
    for it in items:
        by_carrier.setdefault(it["carrier"], []).append(it)
    briefs_dir = run_dir / "memo-briefs"
    briefs_dir.mkdir(exist_ok=True)
    batches = []
    for carrier in sorted(by_carrier):
        group = by_carrier[carrier]
        for n in range(0, len(group), size):
            chunk = group[n:n + size]
            bid = len(batches) + 1
            name = f"{bid:05d}-{carrier}"
            brief_items = [{**it, "allowed_amounts": sorted(_amounts(it, set()))} for it in chunk]
            brief = {"batch_id": bid, "carrier": carrier, "contract_path": contracts[carrier], "items": brief_items}
            path = briefs_dir / f"{name}.json"
            path.write_text(money.dumps(brief, indent=2, ensure_ascii=False) + "\n")
            batches.append({"batch_id": bid, "carrier": carrier, "brief_path": str(path),
                            "memos_dir": str(run_dir / "memo-batches" / name), "contract_path": contracts[carrier],
                            "recon_flow_dir": str(flow_dir), "recon_python": python,
                            "item_count": len(chunk)})
    if len(batches) > MAX_BATCHES + len(by_carrier):
        raise SystemExit(f"plan_memos: {len(batches)} batches exceeds the fanout budget")
    return batches


# --------------------------------------------------------------------------- check

def check_batch(brief: dict, memos_dir: Path) -> list[str]:
    problems = []
    expected = {it["memo_file"]: it for it in brief["items"]}
    present = {p.name for p in memos_dir.glob("*.md")} if memos_dir.is_dir() else set()
    for extra in sorted(present - set(expected)):
        problems.append(f"{extra}: not a memo item in this batch (remove it)")
    for fname, it in expected.items():
        p = memos_dir / fname
        if not p.is_file():
            problems.append(f"{fname}: missing (item {it['item_id']})")
            continue
        text = p.read_text()
        entry = it["report_entry"]
        words = len(text.split())
        if not WORDS[0] <= words <= WORDS[1]:
            problems.append(f"{fname}: {words} words; keep it between {WORDS[0]} and {WORDS[1]}")
        if entry["invoice"] not in text:
            problems.append(f"{fname}: does not name invoice {entry['invoice']}")
        ref = entry.get("consignment_ref")
        if it["kind"] == "line" and ref not in text:
            problems.append(f"{fname}: does not name consignment {ref}")
        if entry["disposition"] not in text.lower():
            problems.append(f"{fname}: does not state the disposition '{entry['disposition']}'")
        allowed = {q(Decimal(str(a))) for a in it["allowed_amounts"]}
        quoted = set()
        for m in AMOUNT.finditer(text):
            try:
                v = q(abs(Decimal(m.group(1).replace(",", ""))))
            except InvalidOperation:
                problems.append(f"{fname}: unparseable amount {m.group(0)!r}")
                continue
            quoted.add(v)
            if v not in allowed:
                problems.append(f"{fname}: quotes {m.group(0)!r}, which is not an amount in this item's brief "
                                f"(quote only the brief's figures; do not recompute)")
        key = entry.get("delta") if entry.get("delta") is not None else (
            entry.get("amount_impact") if it["kind"] == "finding" else entry.get("billed_amount"))
        if key is not None and q(abs(Decimal(str(key)))) not in quoted:
            problems.append(f"{fname}: does not quote the key amount ₹{q(abs(Decimal(str(key)))):,.2f}")
    return list(dict.fromkeys(problems))


# --------------------------------------------------------------------------- publish

def publish(index: dict, batch_dirs: list[Path], report: Path, run_dir: Path, publish_dir: Path | None) -> dict:
    wanted = {it["memo_file"] for it in index["memo_items"]}
    found: dict[str, Path] = {}
    for d in batch_dirs:
        for p in sorted(d.glob("*.md")):
            if p.name in found:
                raise SystemExit(f"publish: memo {p.name} produced by two batches")
            found[p.name] = p
    missing, extra = sorted(wanted - set(found)), sorted(set(found) - wanted)
    if missing or extra:
        raise SystemExit(f"publish: memo set != non-accept items; missing={missing} extra={extra}")
    targets = [run_dir] + ([publish_dir] if publish_dir else [])
    manifest = {"report": None, "memos": {}}
    for t in targets:
        (t / "memos").mkdir(parents=True, exist_ok=True)
        for old in (t / "memos").glob("*.md"):
            if old.name not in wanted:
                old.unlink()          # stale memo from an earlier run must not survive
        for name, src in found.items():
            if src.resolve() != (t / "memos" / name).resolve():
                shutil.copyfile(src, t / "memos" / name)
        if report.resolve() != (t / "reconciliation-report.json").resolve():
            shutil.copyfile(report, t / "reconciliation-report.json")
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    manifest["report"] = sha(run_dir / "reconciliation-report.json")
    manifest["memos"] = {n: sha(run_dir / "memos" / n) for n in sorted(found)}
    manifest["published_to"] = [str(t) for t in targets]
    return manifest


# --------------------------------------------------------------------------- CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--index", required=True, type=Path)
    p.add_argument("--run-dir", required=True, type=Path)
    p.add_argument("--contracts", required=True, help="JSON {carrier: contract_path}")
    p.add_argument("--flow-dir", required=True, type=Path)
    p.add_argument("--python", required=True)
    p.add_argument("--out", required=True, type=Path)
    c = sub.add_parser("check")
    c.add_argument("--brief", required=True, type=Path)
    c.add_argument("--memos-dir", required=True, type=Path)
    c.add_argument("--attempt", type=int, default=1)
    c.add_argument("--max-attempts", type=int, default=2)
    c.add_argument("--report", type=Path)
    u = sub.add_parser("publish")
    u.add_argument("--index", required=True, type=Path)
    u.add_argument("--memo-plan", required=True, type=Path)
    u.add_argument("--report", required=True, type=Path)
    u.add_argument("--run-dir", required=True, type=Path)
    u.add_argument("--publish-dir", type=Path)
    u.add_argument("--out", required=True, type=Path)
    a = ap.parse_args(argv)

    if a.cmd == "plan":
        index = money.loads(a.index.read_text())
        batches = plan(index, a.run_dir, json.loads(a.contracts), a.flow_dir, a.python)
        a.out.write_text(json.dumps({"_session_id": "script:plan_memos", "stage": "plan_memos",
                                     "item_count": index["memo_item_count"], "batches": batches}, indent=2) + "\n")
        # Fanout carries batch ids only (flowstate caps variables at 16 KB);
        # each memo-batch child resolves its details from the plan file.
        print("FLOWSTATE_OUTPUT_memo_batches=" + json.dumps([b["batch_id"] for b in batches]))
        print(f"FLOWSTATE_OUTPUT_memo_item_count={index['memo_item_count']}")
        print(f"plan_memos: {index['memo_item_count']} items in {len(batches)} batches", file=sys.stderr)
        return 0

    if a.cmd == "check":
        brief = money.loads(a.brief.read_text())
        problems = check_batch(brief, a.memos_dir)
        rec = {"_session_id": "script:check_memos", "batch_id": brief["batch_id"], "attempt": a.attempt,
               "ok": not problems, "problems": problems}
        if a.report:
            a.report.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n")
        if not problems:
            print("FLOWSTATE_OUTPUT_memo_next=done")
            return 0
        if a.attempt >= a.max_attempts:
            print(f"memo check failed after {a.attempt} attempt(s):\n  - " + "\n  - ".join(problems), file=sys.stderr)
            return 1
        feedback = ("Your previous attempt failed the memo check. Fix exactly these problems and leave "
                    "passing memos as they are: " + " | ".join(problems))
        print("FLOWSTATE_OUTPUT_memo_next=retry")
        print("FLOWSTATE_OUTPUT_memo_feedback=" + json.dumps(feedback))
        print(f"FLOWSTATE_OUTPUT_memo_attempt={a.attempt + 1}")
        return 0

    index = money.loads(a.index.read_text())
    batch_dirs = [Path(b["memos_dir"]) for b in json.loads(a.memo_plan.read_text())["batches"]]
    manifest = publish(index, batch_dirs, a.report, a.run_dir, a.publish_dir)
    a.out.write_text(json.dumps({"_session_id": "script:publish", "stage": "publish", **manifest}, indent=2) + "\n")
    print(f"publish: report + {len(manifest['memos'])} memos -> {manifest['published_to']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

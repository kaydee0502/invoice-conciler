"""Resolve a fanout item id to its details (first node of each child flow).

flowstate caps every variable at 16 KB, so fanout lists carry short ids
(a carrier key, a batch id) and the details live in the plan file. This
prints one FLOWSTATE_OUTPUT_<field>=<value> per field of the matching item.

    python -m reconlib.items --plan PLAN.json --list contract_items --key carrier --id falcon
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True, type=Path)
    ap.add_argument("--list", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--id", required=True)
    a = ap.parse_args(argv)
    items = json.loads(a.plan.read_text())[a.list]
    hits = [it for it in items if str(it.get(a.key)) == a.id]
    if len(hits) != 1:
        raise SystemExit(f"items: expected one {a.list} entry with {a.key}={a.id!r}, found {len(hits)}")
    for k, v in hits[0].items():
        if k == a.key:
            continue
        print(f"FLOWSTATE_OUTPUT_{k}=" + (v if isinstance(v, str) else json.dumps(v)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

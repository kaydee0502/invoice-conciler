#!/usr/bin/env python3
"""Join reducer for memos_join: count completed memo batches (audit figure).

Also load-bearing: flowstate only marks a dynamic_fanout's join ready to fire
from inside its reducer hook, so a join without a reducer is never fired (see
DESIGN.md, machinery notes).
"""
import json
import os
import sys

try:
    n = int(json.loads(os.environ.get("FLOWSTATE_VAR_memo_batches_done") or "0"))
except (ValueError, TypeError):
    n = 0
sys.stdout.write(f"FLOWSTATE_OUTPUT_memo_batches_done={n + 1}\n")

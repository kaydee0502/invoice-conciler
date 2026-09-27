#!/usr/bin/env python3
"""Join reducer for memos_join: count completed memo batches (an audit figure
that should equal the number of batches in memo-plan.json)."""
import json
import os
import sys

try:
    n = int(json.loads(os.environ.get("FLOWSTATE_VAR_memo_batches_done") or "0"))
except (ValueError, TypeError):
    n = 0
sys.stdout.write(f"FLOWSTATE_OUTPUT_memo_batches_done={n + 1}\n")

#!/usr/bin/env python3
"""Join reducer: record each contract-spec branch's agreed spec in rate-specs.json.

The mapping lives in a FILE in the run dir, not in a variable: flowstate caps
variables at 16 KB and the mapping grows with the number of carriers.
Reducers run serially under flowstate's state lock, so the read-modify-write
is safe. Env: FLOWSTATE_VAR_carrier (child input, harvested), FLOWSTATE_VAR_item,
FLOWSTATE_VAR_rate_spec (child output), FLOWSTATE_VAR_rate_specs (the file path).
Stdout re-emits the (unchanged) path so the summary variable stays a reference.
"""
import json
import os
import sys
from pathlib import Path

path = Path(os.environ["FLOWSTATE_VAR_rate_specs"])
carrier = os.environ.get("FLOWSTATE_VAR_item") or ""
try:
    carrier = json.loads(carrier) if carrier.startswith('"') else carrier
except ValueError:
    pass
data = json.loads(path.read_text()) if path.is_file() else {}
data[carrier or f"unknown:{os.environ.get('FLOWSTATE_BRANCH_ID')}"] = os.environ.get("FLOWSTATE_VAR_rate_spec") or ""
tmp = path.with_suffix(".tmp")
tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
tmp.replace(path)
sys.stdout.write(f"FLOWSTATE_OUTPUT_rate_specs={path}\n")

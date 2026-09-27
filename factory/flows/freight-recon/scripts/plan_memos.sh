#!/usr/bin/env bash
# Stage 7a (script node): batch non-accept items per carrier; emit batch ids for the fanout.
set -euo pipefail
ROOT="${FACTORY_ROOT:?}"
PY="$ROOT/orchestrator/.venv/bin/python"
cd "${FLOWSTATE_FLOW_DIR:?}"
CONTRACTS="$("$PY" -c 'import json,os;p=json.load(open(os.environ["FLOWSTATE_VAR_contract_plan"]));print(json.dumps({i["carrier"]:i["contract_path"] for i in p["contract_items"]}))')"
exec "$PY" -m reconlib.memos plan --index "${FLOWSTATE_VAR_report_index:?}" --run-dir "${FLOWSTATE_RUN_DIR:?}" \
  --contracts "$CONTRACTS" --flow-dir "$FLOWSTATE_FLOW_DIR" --python "$PY" --out "${FLOWSTATE_VAR_memo_plan:?}"

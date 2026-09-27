#!/usr/bin/env bash
# Stage 6 (script node): build the report; refuse unless every invariant holds.
# The report is NOT a flowstate-declared output (flowstate would require a
# _session_id field in it, and the deliverable must match report.schema.json
# exactly); assemble validates it against report.schema.json itself and only
# then writes it. The declared, flowstate-validated output is report-index.json.
set -euo pipefail
ROOT="${FACTORY_ROOT:?}"
REPORT="${FLOWSTATE_RUN_DIR:?}/reconciliation-report.json"
cd "${FLOWSTATE_FLOW_DIR:?}"
ADJ=()
[ -n "${FLOWSTATE_VAR_adjudication:-}" ] && ADJ=(--adjudication "$FLOWSTATE_VAR_adjudication")
"$ROOT/orchestrator/.venv/bin/python" -m reconlib.assemble --ingest "${FLOWSTATE_VAR_ingest:?}" \
  --price "${FLOWSTATE_VAR_price:?}" "${ADJ[@]}" --schema "$ROOT/report.schema.json" \
  --out "$REPORT" --index "${FLOWSTATE_VAR_report_index:?}"
echo "FLOWSTATE_OUTPUT_report=$REPORT"

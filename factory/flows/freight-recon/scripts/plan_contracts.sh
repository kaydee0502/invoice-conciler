#!/usr/bin/env bash
# Stage 1b (script node): pair each carrier with its contract; reuse cached specs;
# emit the carriers still to extract as the fanout list.
set -euo pipefail
ROOT="${FACTORY_ROOT:?}"
abs() { [[ "$1" = /* ]] && echo "$1" || echo "$ROOT/$1"; }
PY="$ROOT/orchestrator/.venv/bin/python"
CACHE=()
[ "${FLOWSTATE_VAR_use_spec_cache:-yes}" = yes ] && CACHE=(--cache-dir "$(abs "${FLOWSTATE_VAR_spec_cache_dir:?}")")
cd "${FLOWSTATE_FLOW_DIR:?}"
exec "$PY" -m reconlib.plan --ingest "${FLOWSTATE_VAR_ingest:?}" \
  --shipments "$(abs "${FLOWSTATE_VAR_shipments_path:?}")" \
  --contracts-dir "$(abs "${FLOWSTATE_VAR_contracts_dir:?}")" \
  --flow-dir "$FLOWSTATE_FLOW_DIR" --python "$PY" --run-dir "${FLOWSTATE_RUN_DIR:?}" "${CACHE[@]}" \
  --out "${FLOWSTATE_VAR_contract_plan:?}"

#!/usr/bin/env bash
# Stage 4 (script node): expected amounts + rule-based dispositions + invoice findings.
set -euo pipefail
ROOT="${FACTORY_ROOT:?}"
abs() { [[ "$1" = /* ]] && echo "$1" || echo "$ROOT/$1"; }
cd "${FLOWSTATE_FLOW_DIR:?}"
exec "$ROOT/orchestrator/.venv/bin/python" -m reconlib.price --ingest "${FLOWSTATE_VAR_ingest:?}" \
  --match "${FLOWSTATE_VAR_match:?}" --rate-specs-file "${FLOWSTATE_VAR_rate_specs:?}" \
  --shipments "$(abs "${FLOWSTATE_VAR_shipments_path:?}")" --out "${FLOWSTATE_VAR_price:?}"

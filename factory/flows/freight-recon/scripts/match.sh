#!/usr/bin/env bash
# Stage 3 (script node): invoice lines -> shipments; record fact conflicts.
set -euo pipefail
ROOT="${FACTORY_ROOT:?}"
abs() { [[ "$1" = /* ]] && echo "$1" || echo "$ROOT/$1"; }
cd "${FLOWSTATE_FLOW_DIR:?}"
exec "$ROOT/orchestrator/.venv/bin/python" -m reconlib.match --ingest "${FLOWSTATE_VAR_ingest:?}" \
  --shipments "$(abs "${FLOWSTATE_VAR_shipments_path:?}")" --out "${FLOWSTATE_VAR_match:?}"

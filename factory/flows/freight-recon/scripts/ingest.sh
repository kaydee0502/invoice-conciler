#!/usr/bin/env bash
# Stage 1 (script node): parse every billing document into normalized lines.
# Inputs:  $FLOWSTATE_VAR_invoices_dir (relative to repo root unless absolute)
# Output:  $FLOWSTATE_VAR_ingest (path populated by flowstate from sets_variables)
set -euo pipefail
FLOW_DIR="${FLOWSTATE_FLOW_DIR:?}"
ROOT="${FACTORY_ROOT:?}"
OUT="${FLOWSTATE_VAR_ingest:?FLOWSTATE_VAR_ingest not set}"
IN="${FLOWSTATE_VAR_invoices_dir:?FLOWSTATE_VAR_invoices_dir not set}"
[[ "$IN" = /* ]] || IN="$ROOT/$IN"
cd "$FLOW_DIR"
exec python3 -m reconlib.ingest --invoices-dir "$IN" --out "$OUT" --session-id "script:ingest"

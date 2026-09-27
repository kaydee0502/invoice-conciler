#!/usr/bin/env bash
# First node: resolve this branch's id ($FLOWSTATE_VAR_batch_id) to its details in the
# parent's plan file. Fanout items are ids only: flowstate caps variables at 16 KB.
set -euo pipefail
ROOT="${FACTORY_ROOT:?}"
cd "$ROOT/factory/flows/freight-recon"
exec "$ROOT/orchestrator/.venv/bin/python" -m reconlib.items --plan "${FLOWSTATE_VAR_memo_plan:?}" \
  --list batches --key batch_id --id "${FLOWSTATE_VAR_batch_id:?}"

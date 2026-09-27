#!/usr/bin/env bash
# Final (script node): memo files == non-accept items exactly; copy report + memos to
# the run dir and the publish dir; write a sha256 manifest. Batch dirs come from the
# memo plan (deterministic), not from a collected variable.
set -euo pipefail
ROOT="${FACTORY_ROOT:?}"
PUB="${FLOWSTATE_VAR_publish_dir:-}"
[ -n "$PUB" ] && [[ "$PUB" != /* ]] && PUB="$ROOT/$PUB"
cd "${FLOWSTATE_FLOW_DIR:?}"
exec "$ROOT/orchestrator/.venv/bin/python" -m reconlib.memos publish --index "${FLOWSTATE_VAR_report_index:?}" \
  --memo-plan "${FLOWSTATE_VAR_memo_plan:?}" --report "${FLOWSTATE_VAR_report:?}" \
  --run-dir "${FLOWSTATE_RUN_DIR:?}" ${PUB:+--publish-dir "$PUB"} --out "${FLOWSTATE_VAR_publish_manifest:?}"

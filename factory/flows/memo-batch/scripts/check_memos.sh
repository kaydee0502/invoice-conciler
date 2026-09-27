#!/usr/bin/env bash
# Backstop check of the batch (the worker also self-checks). Routes the flow:
# memo_next=done | retry (with feedback); exit 1 when retries are exhausted.
set -euo pipefail
cd "${FLOWSTATE_VAR_recon_flow_dir:?}"
exec "${FLOWSTATE_VAR_recon_python:?}" -m reconlib.memos check --brief "${FLOWSTATE_VAR_brief_path:?}" \
  --memos-dir "${FLOWSTATE_VAR_memos_dir:?}" --attempt "${FLOWSTATE_VAR_memo_attempt:-1}" --max-attempts 2 \
  --report "${FLOWSTATE_VAR_memo_check:?}"

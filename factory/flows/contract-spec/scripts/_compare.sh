#!/usr/bin/env bash
# Compare extraction candidates behaviourally (reconlib.specdiff).
# Usage: compare.sh <report-var-name> <required:0|1> <candidate path vars...>
#   required=1: exit non-zero when no majority (the tie-break vote).
# Emits FLOWSTATE_OUTPUT_spec_agreed and, on agreement, FLOWSTATE_OUTPUT_rate_spec.
set -euo pipefail
REPORT_VAR="$1"; REQUIRED="$2"; shift 2
REPORT="$(printenv "FLOWSTATE_VAR_${REPORT_VAR}")"
: "${REPORT:?report path var $REPORT_VAR not set}"
OUT="$(dirname "$REPORT")/rate-spec.json"
CANDS=()
for v in "$@"; do CANDS+=("$(printenv "FLOWSTATE_VAR_${v}")"); done
cd "${FLOWSTATE_VAR_recon_flow_dir:?}"
STDOUT="$("${FLOWSTATE_VAR_recon_python:?}" -m reconlib.specdiff \
  --carrier "${FLOWSTATE_VAR_carrier:?}" --shipments "${FLOWSTATE_VAR_shipments_path:?}" \
  --candidates "${CANDS[@]}" --out "$OUT" --report "$REPORT")"
echo "$STDOUT"
if grep -q '^FLOWSTATE_OUTPUT_spec_agreed=true$' <<<"$STDOUT"; then
  echo "FLOWSTATE_OUTPUT_rate_spec=$OUT"
elif [ "$REQUIRED" = "1" ]; then
  echo "no majority among ${#CANDS[@]} independent extractions of ${FLOWSTATE_VAR_contract_file}; see $REPORT" >&2
  exit 1
fi

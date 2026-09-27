#!/usr/bin/env bash
# run-fanout.sh <parent_run_dir>
#
# Run every branch of the dynamic_fanout the parent is currently on, within
# flowstate's concurrency window (max_concurrent), driving each child with
# drive-branch.sh. Returns once flowstate reports the fanout complete; the
# caller (the orchestrator) then advances the parent past the join.
# Exit 0 = every branch reached its end; non-zero = at least one branch
# stopped (its log is under <parent_run_dir>/branch-logs/).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../../.." && pwd)"
cd "$ROOT"
RD="$(realpath "$1")"
FS=orchestrator/bin/flowstate
PY=orchestrator/.venv/bin/python
LOGS="$RD/branch-logs"; mkdir -p "$LOGS"
NODE="$($FS status --run-dir "$RD" | "$PY" -c "import sys,yaml;print(yaml.safe_load(sys.stdin)['payload']['current_phase'])")"
declare -A PIDS=()
failed=0
while :; do
  A="$($FS advance --run-dir "$RD")"
  START="$("$PY" -c "import sys,yaml;d=yaml.safe_load(sys.stdin)['payload'];print(' '.join(d.get('next_startable_branches') or []))" <<<"$A")"
  KIND="$("$PY" -c "import sys,yaml;print(yaml.safe_load(sys.stdin)['payload'].get('kind'))" <<<"$A")"
  for b in $START; do
    S="$($FS start-branch --run-dir "$RD" --node "$NODE" --branch "$b")"
    CHILD="$(grep -m1 'subflow_run_dir:' <<<"$S" | awk '{print $2}')"
    [ -n "$CHILD" ] || { echo "start-branch $b failed: $S"; failed=1; continue; }
    "$HERE/drive-branch.sh" "$CHILD" > "$LOGS/$b.log" 2>&1 &
    PIDS[$b]=$!
    echo "[$(date +%T)] started $b -> $(basename "$CHILD")"
  done
  # Nothing running and nothing new to start: every branch is terminal and
  # flowstate has moved the parent past the fanout (or reported why not).
  if [ ${#PIDS[@]} -eq 0 ]; then
    echo "[$(date +%T)] fanout $NODE finished: kind=$KIND"
    break
  fi
  # Wait for any branch to finish, then loop to let flowstate open the next slot.
  wait -n -p DONE_PID "${PIDS[@]}" ; rc=$?
  for b in "${!PIDS[@]}"; do
    if [ "${PIDS[$b]}" = "$DONE_PID" ]; then
      unset "PIDS[$b]"
      if [ $rc -ne 0 ]; then failed=1; echo "[$(date +%T)] branch $b STOPPED (rc=$rc); see $LOGS/$b.log"
      else echo "[$(date +%T)] branch $b done"; fi
    fi
  done
  [ $failed = 1 ] && [ ${#PIDS[@]} -eq 0 ] && break
done
exit $failed

#!/usr/bin/env bash
# drive-branch.sh <child_run_dir>
#
# Drive one subflow child run (contract-spec or memo-batch) to its end node:
# advance -> spawn the agent node's worker -> wait -> finish, repeatedly.
# Script nodes and conditional edges are chased by flowstate itself.
#
# Failure policy (freight-reconcile SKILL.md), applied mechanically. One retry
# per node, then stop:
#   validation fails      -> respawn with the validator's feedback appended to the
#                            prompt (agentctl's wrapper kills a worker as soon as it
#                            writes completion.yml, so there is no live worker to
#                            send feedback to).
#   worker died / stalled -> kill, respawn with a fresh render.
#   worker asks a question -> stop (exit 3): the operator must answer.
# Every decision is logged with `flowstate event` in the child run.
# Resumable: re-running on a stopped child respawns its unfinished agent node.
# Exit 0 = child reached its end node.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
cd "$ROOT"
RD="$(realpath "$1")"
FS=orchestrator/bin/flowstate
AC=orchestrator/bin/agentctl
PY=orchestrator/.venv/bin/python
TAG="$(basename "$RD" | cut -d_ -f1-2)"
log() { echo "[$(date +%T) $TAG] $*"; }
y() { "$PY" -c "import sys,yaml;d=yaml.safe_load(sys.stdin);print(eval(sys.argv[1]))" "$1"; }
event() { $FS event --run-dir "$RD" --kind "$1" --node "$2" --message "$3" >/dev/null 2>&1 || true; }

spawn() {   # spawn a worker for the node the run is on; $1 = optional feedback; prints agent_id
  local node cfg prompt wd tmp model desc
  node="$($FS status --run-dir "$RD" | y "d['payload']['current_phase']")"
  cfg="$($FS node-config "$node" --run-dir "$RD")"
  prompt="$($FS render-prompt "$node" --run-dir "$RD" | y "d['payload']['rendered_prompt']")"
  if [ -n "${1:-}" ]; then
    prompt="$prompt

## A previous attempt at this node failed validation

Fix exactly this and nothing else; the rest of the instructions above still apply:

$1"
  fi
  wd="$(y "d['payload']['working_dir']" <<<"$cfg")"; tmp="$(y "d['payload']['temp_dir']" <<<"$cfg")"
  model="$(y "d['payload'].get('model') or ''" <<<"$cfg")"
  rm -f "$tmp/completion.yml"
  $AC spawn --harness claude-code --working-dir "$wd" --phase "$node" --prompt "$prompt" --run-dir "$RD" \
    --temp-dir "$tmp" --run-descriptor "$TAG" --repo-root "$ROOT" ${model:+--model "$model"} \
    | y "d['payload']['agent_id'] if d['status']=='ok' else 'SPAWN_ERROR: '+str(d)"
}

wait_done() {   # prints the terminal outcome for agent $1
  local w o
  while :; do
    w="$($AC wait "$1" --max-seconds 120)"
    o="$(grep -m1 'outcome:' <<<"$w" | awk '{print $2}')"
    [ "$o" = timeout ] || { echo "$o"; return; }
  done
}

A="$($FS advance --run-dir "$RD")"
K="$(y "d['payload'].get('kind') if d['status']=='ok' else 'error'" <<<"$A")"
[ "$K" = end ] && { log "already at end"; exit 0; }
if [ "$K" = blocked ]; then
  # Resume: the run stopped on an agent node that never finished.
  CUR="$($FS status --run-dir "$RD" | y "d['payload']['current_phase']")"
  if [ "$($FS node-config "$CUR" --run-dir "$RD" | y "d['payload'].get('runner')")" = agent ]; then
    event run_resumed "$CUR" "resuming unfinished agent node"
    log "resuming unfinished $CUR"
  else
    log "initial advance blocked on $CUR"; echo "$A"; exit 1
  fi
elif [ "$K" != moved ]; then
  log "initial advance: $K"; echo "$A"; exit 1
fi

while :; do
  NODE="$($FS status --run-dir "$RD" | y "d['payload']['current_phase']")"
  AID="$(spawn)"
  [[ "$AID" =~ ^[0-9a-f-]{36}$ ]] || { log "spawn failed on $NODE: $AID"; exit 1; }
  log "spawned $NODE $AID"
  retried=0
  while :; do
    O="$(wait_done "$AID")"
    if [ "$O" = completed ]; then
      F="$($FS finish "$NODE" --run-dir "$RD" --agent-id "$AID")"
      if [ "$(grep -m1 'passed:' <<<"$F" | awk '{print $2}')" = true ]; then break; fi
      FB="$(y "d['payload']['validate']['feedback']" <<<"$F")"
      $AC kill "$AID" >/dev/null 2>&1 || true
      if [ $retried = 0 ]; then
        retried=1
        event worker_respawned "$NODE" "validation failed; respawning with feedback: ${FB:0:400}"
        log "$NODE failed validation; respawning once with the feedback"
        AID="$(spawn "$FB")"
        [[ "$AID" =~ ^[0-9a-f-]{36}$ ]] || { log "respawn failed: $AID"; exit 1; }
        continue
      fi
      event run_stopped "$NODE" "validation failed after retry: ${FB:0:400}"
      log "$NODE failed validation after retry: $FB"; exit 1
    fi
    if [ "$O" = awaiting_human ]; then
      event run_stopped "$NODE" "worker asked a question; needs the operator"
      log "$NODE is waiting on a human: see $AC status $AID"; exit 3
    fi
    # died / stalled
    $AC kill "$AID" >/dev/null 2>&1 || true
    if [ $retried = 0 ]; then
      retried=1
      event worker_respawned "$NODE" "worker $O; respawning once"
      log "$NODE worker $O; respawning once"
      AID="$(spawn)"
      [[ "$AID" =~ ^[0-9a-f-]{36}$ ]] || { log "respawn failed: $AID"; exit 1; }
      continue
    fi
    event run_stopped "$NODE" "worker $O after retry"
    log "$NODE worker $O after retry"; exit 1
  done
  K="$(sed -n '/^  advance:/,$p' <<<"$F" | grep -m1 'kind:' | awk '{print $2}')"
  log "$NODE passed; next: $K"
  case "$K" in
    end) log DONE; exit 0 ;;
    moved) ;;
    *) sed -n '/^  advance:/,$p' <<<"$F" | head -20; exit 1 ;;
  esac
done

#!/bin/bash
set -uo pipefail
cd /home/dax/stage2-kit/factory/graph_runs/contract-spec/B1_3_dakshdhama0502_20260927T095001606437Z_B1.3
export FLOWSTATE_WORKER=1
claude --model opus --session-id 431a244c-25ee-4922-be0f-d98d75207b9e --setting-sources user,project,local < /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_3_dakshdhama0502_20260927T095001606437Z/extract_a/prompt.txt &
claude_pid=$!
printf '%s\n' $claude_pid > /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_3_dakshdhama0502_20260927T095001606437Z/extract_a/pid.tmp && mv /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_3_dakshdhama0502_20260927T095001606437Z/extract_a/pid.tmp /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_3_dakshdhama0502_20260927T095001606437Z/extract_a/pid
while kill -0 $claude_pid 2>/dev/null && [ ! -f /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_3_dakshdhama0502_20260927T095001606437Z/extract_a/completion.yml ]; do sleep 2; done
kill $claude_pid 2>/dev/null || true

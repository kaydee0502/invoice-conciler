#!/bin/bash
set -uo pipefail
cd /home/dax/stage2-kit/factory/graph_runs/contract-spec/B1_3_dakshdhama0502_20260927T095001606437Z_B1.3
export FLOWSTATE_WORKER=1
claude --model sonnet --session-id c6b4b021-2f69-4499-a03b-35382e431a9b --setting-sources user,project,local < /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_3_dakshdhama0502_20260927T095001606437Z/extract_b/prompt.txt &
claude_pid=$!
printf '%s\n' $claude_pid > /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_3_dakshdhama0502_20260927T095001606437Z/extract_b/pid.tmp && mv /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_3_dakshdhama0502_20260927T095001606437Z/extract_b/pid.tmp /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_3_dakshdhama0502_20260927T095001606437Z/extract_b/pid
while kill -0 $claude_pid 2>/dev/null && [ ! -f /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_3_dakshdhama0502_20260927T095001606437Z/extract_b/completion.yml ]; do sleep 2; done
kill $claude_pid 2>/dev/null || true

#!/bin/bash
set -uo pipefail
cd /home/dax/stage2-kit/factory/graph_runs/contract-spec/B1_3_dakshdhama0502_20260927T140515800621Z_B1.3
export FLOWSTATE_WORKER=1
claude --model opus --session-id d60e1bb3-b9d1-4dd9-bf41-c57765cec183 --setting-sources user,project,local < /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_3_dakshdhama0502_20260927T140515800621Z/extract_a/prompt.txt &
claude_pid=$!
printf '%s\n' $claude_pid > /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_3_dakshdhama0502_20260927T140515800621Z/extract_a/pid.tmp && mv /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_3_dakshdhama0502_20260927T140515800621Z/extract_a/pid.tmp /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_3_dakshdhama0502_20260927T140515800621Z/extract_a/pid
while kill -0 $claude_pid 2>/dev/null && [ ! -f /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_3_dakshdhama0502_20260927T140515800621Z/extract_a/completion.yml ]; do sleep 2; done
kill $claude_pid 2>/dev/null || true

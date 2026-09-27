#!/bin/bash
set -uo pipefail
cd /home/dax/stage2-kit/factory/graph_runs/contract-spec/B1_2_dakshdhama0502_20260927T140514835694Z_B1.2
export FLOWSTATE_WORKER=1
claude --model opus --session-id 36b94e92-fa3e-4084-a668-614732874a2f --setting-sources user,project,local < /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_2_dakshdhama0502_20260927T140514835694Z/extract_a/prompt.txt &
claude_pid=$!
printf '%s\n' $claude_pid > /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_2_dakshdhama0502_20260927T140514835694Z/extract_a/pid.tmp && mv /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_2_dakshdhama0502_20260927T140514835694Z/extract_a/pid.tmp /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_2_dakshdhama0502_20260927T140514835694Z/extract_a/pid
while kill -0 $claude_pid 2>/dev/null && [ ! -f /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_2_dakshdhama0502_20260927T140514835694Z/extract_a/completion.yml ]; do sleep 2; done
kill $claude_pid 2>/dev/null || true

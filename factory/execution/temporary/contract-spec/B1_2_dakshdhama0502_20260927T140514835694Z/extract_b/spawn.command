#!/bin/bash
set -uo pipefail
cd /home/dax/stage2-kit/factory/graph_runs/contract-spec/B1_2_dakshdhama0502_20260927T140514835694Z_B1.2
export FLOWSTATE_WORKER=1
claude --model sonnet --session-id a977398e-816e-4ec9-adde-7fa8af3c568f --setting-sources user,project,local < /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_2_dakshdhama0502_20260927T140514835694Z/extract_b/prompt.txt &
claude_pid=$!
printf '%s\n' $claude_pid > /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_2_dakshdhama0502_20260927T140514835694Z/extract_b/pid.tmp && mv /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_2_dakshdhama0502_20260927T140514835694Z/extract_b/pid.tmp /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_2_dakshdhama0502_20260927T140514835694Z/extract_b/pid
while kill -0 $claude_pid 2>/dev/null && [ ! -f /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_2_dakshdhama0502_20260927T140514835694Z/extract_b/completion.yml ]; do sleep 2; done
kill $claude_pid 2>/dev/null || true

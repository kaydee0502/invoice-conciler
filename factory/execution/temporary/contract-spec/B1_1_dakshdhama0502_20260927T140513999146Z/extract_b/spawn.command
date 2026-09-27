#!/bin/bash
set -uo pipefail
cd /home/dax/stage2-kit/factory/graph_runs/contract-spec/B1_1_dakshdhama0502_20260927T140513999146Z_B1.1
export FLOWSTATE_WORKER=1
claude --model sonnet --session-id ebead607-5ddc-4078-99ef-2e453a23860f --setting-sources user,project,local < /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_1_dakshdhama0502_20260927T140513999146Z/extract_b/prompt.txt &
claude_pid=$!
printf '%s\n' $claude_pid > /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_1_dakshdhama0502_20260927T140513999146Z/extract_b/pid.tmp && mv /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_1_dakshdhama0502_20260927T140513999146Z/extract_b/pid.tmp /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_1_dakshdhama0502_20260927T140513999146Z/extract_b/pid
while kill -0 $claude_pid 2>/dev/null && [ ! -f /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_1_dakshdhama0502_20260927T140513999146Z/extract_b/completion.yml ]; do sleep 2; done
kill $claude_pid 2>/dev/null || true

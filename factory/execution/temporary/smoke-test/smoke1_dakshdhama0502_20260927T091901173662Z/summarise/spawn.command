#!/bin/bash
set -uo pipefail
cd /home/dax/stage2-kit/factory/graph_runs/smoke-test/smoke1_dakshdhama0502_20260927T091901173662Z
export FLOWSTATE_WORKER=1
claude --model sonnet --session-id 5461b0e6-8b3e-4b2a-87ea-893c7909e277 --setting-sources user,project,local < /home/dax/stage2-kit/factory/execution/temporary/smoke-test/smoke1_dakshdhama0502_20260927T091901173662Z/summarise/prompt.txt &
claude_pid=$!
printf '%s\n' $claude_pid > /home/dax/stage2-kit/factory/execution/temporary/smoke-test/smoke1_dakshdhama0502_20260927T091901173662Z/summarise/pid.tmp && mv /home/dax/stage2-kit/factory/execution/temporary/smoke-test/smoke1_dakshdhama0502_20260927T091901173662Z/summarise/pid.tmp /home/dax/stage2-kit/factory/execution/temporary/smoke-test/smoke1_dakshdhama0502_20260927T091901173662Z/summarise/pid
while kill -0 $claude_pid 2>/dev/null && [ ! -f /home/dax/stage2-kit/factory/execution/temporary/smoke-test/smoke1_dakshdhama0502_20260927T091901173662Z/summarise/completion.yml ]; do sleep 2; done
kill $claude_pid 2>/dev/null || true

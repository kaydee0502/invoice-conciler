#!/bin/bash
set -uo pipefail
cd /home/dax/stage2-kit/factory/graph_runs/smoke-test/smoke1_dakshdhama0502_20260927T091901173662Z
export FLOWSTATE_WORKER=1
claude --model sonnet --session-id 17ec4699-71ea-4af9-baa7-4716c1410575 --setting-sources user,project,local < /home/dax/stage2-kit/factory/execution/temporary/smoke-test/smoke1_dakshdhama0502_20260927T091901173662Z/research/prompt.txt &
claude_pid=$!
printf '%s\n' $claude_pid > /home/dax/stage2-kit/factory/execution/temporary/smoke-test/smoke1_dakshdhama0502_20260927T091901173662Z/research/pid.tmp && mv /home/dax/stage2-kit/factory/execution/temporary/smoke-test/smoke1_dakshdhama0502_20260927T091901173662Z/research/pid.tmp /home/dax/stage2-kit/factory/execution/temporary/smoke-test/smoke1_dakshdhama0502_20260927T091901173662Z/research/pid
while kill -0 $claude_pid 2>/dev/null && [ ! -f /home/dax/stage2-kit/factory/execution/temporary/smoke-test/smoke1_dakshdhama0502_20260927T091901173662Z/research/completion.yml ]; do sleep 2; done
kill $claude_pid 2>/dev/null || true

#!/bin/bash
set -uo pipefail
cd /home/dax/stage2-kit/factory/graph_runs/contract-spec/B1_3_dakshdhama0502_20260927T112005916041Z_B1.3
export FLOWSTATE_WORKER=1
claude --model opus --session-id deb62311-2419-4e2e-a80e-e138eaa1c9f5 --setting-sources user,project,local < /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_3_dakshdhama0502_20260927T112005916041Z/extract_c/prompt.txt &
claude_pid=$!
printf '%s\n' $claude_pid > /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_3_dakshdhama0502_20260927T112005916041Z/extract_c/pid.tmp && mv /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_3_dakshdhama0502_20260927T112005916041Z/extract_c/pid.tmp /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_3_dakshdhama0502_20260927T112005916041Z/extract_c/pid
while kill -0 $claude_pid 2>/dev/null && [ ! -f /home/dax/stage2-kit/factory/execution/temporary/contract-spec/B1_3_dakshdhama0502_20260927T112005916041Z/extract_c/completion.yml ]; do sleep 2; done
kill $claude_pid 2>/dev/null || true

#!/bin/bash
set -uo pipefail
cd /home/dax/stage2-kit/factory/graph_runs/memo-batch/B1_5_dakshdhama0502_20260927T112344848603Z_B1.5
export FLOWSTATE_WORKER=1
claude --model opus --session-id 1d7ba052-b165-4292-9495-e27c31c5d374 --setting-sources user,project,local < /home/dax/stage2-kit/factory/execution/temporary/memo-batch/B1_5_dakshdhama0502_20260927T112344848603Z/write_memos/prompt.txt &
claude_pid=$!
printf '%s\n' $claude_pid > /home/dax/stage2-kit/factory/execution/temporary/memo-batch/B1_5_dakshdhama0502_20260927T112344848603Z/write_memos/pid.tmp && mv /home/dax/stage2-kit/factory/execution/temporary/memo-batch/B1_5_dakshdhama0502_20260927T112344848603Z/write_memos/pid.tmp /home/dax/stage2-kit/factory/execution/temporary/memo-batch/B1_5_dakshdhama0502_20260927T112344848603Z/write_memos/pid
while kill -0 $claude_pid 2>/dev/null && [ ! -f /home/dax/stage2-kit/factory/execution/temporary/memo-batch/B1_5_dakshdhama0502_20260927T112344848603Z/write_memos/completion.yml ]; do sleep 2; done
kill $claude_pid 2>/dev/null || true

#!/bin/bash
set -uo pipefail
cd /home/dax/stage2-kit/factory/graph_runs/memo-batch/B1_4_dakshdhama0502_20260927T140823725735Z_B1.4
export FLOWSTATE_WORKER=1
claude --model opus --session-id d8e5c4d8-7831-41e8-a93f-2053106b815a --setting-sources user,project,local < /home/dax/stage2-kit/factory/execution/temporary/memo-batch/B1_4_dakshdhama0502_20260927T140823725735Z/write_memos/prompt.txt &
claude_pid=$!
printf '%s\n' $claude_pid > /home/dax/stage2-kit/factory/execution/temporary/memo-batch/B1_4_dakshdhama0502_20260927T140823725735Z/write_memos/pid.tmp && mv /home/dax/stage2-kit/factory/execution/temporary/memo-batch/B1_4_dakshdhama0502_20260927T140823725735Z/write_memos/pid.tmp /home/dax/stage2-kit/factory/execution/temporary/memo-batch/B1_4_dakshdhama0502_20260927T140823725735Z/write_memos/pid
while kill -0 $claude_pid 2>/dev/null && [ ! -f /home/dax/stage2-kit/factory/execution/temporary/memo-batch/B1_4_dakshdhama0502_20260927T140823725735Z/write_memos/completion.yml ]; do sleep 2; done
kill $claude_pid 2>/dev/null || true

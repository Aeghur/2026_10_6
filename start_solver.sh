#!/bin/sh
set -eu
cd /home/radxa/vision_sorter

if pgrep -f '[v]ision_calibrator.py' >/dev/null 2>&1; then
    echo "ERROR solver already running"
    pgrep -f '[v]ision_calibrator.py'
    exit 2
fi

nohup .venv/bin/python vision_calibrator.py > run_current.log 2>&1 &
solver_pid=$!
echo "$solver_pid" > solver.pid
echo "STARTED $solver_pid"

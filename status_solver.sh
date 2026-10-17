#!/bin/sh
set -eu
pid=$(cat /home/radxa/vision_sorter/solver.pid)
if kill -0 "$pid" 2>/dev/null; then
    echo "RUNNING $pid"
else
    echo "FINISHED $pid"
fi

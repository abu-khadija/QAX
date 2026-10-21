#!/usr/bin/env bash
# Stop everything demo/prepare_demo.sh (and the corpus start-all.sh) started.
CORPUS="${QAX_CORPUS:-$HOME/Downloads/qax-corpus}"
[ -f "$CORPUS/.bench.pids" ] && xargs kill < "$CORPUS/.bench.pids" 2>/dev/null; : > "$CORPUS/.bench.pids"
echo "stopped"

#!/bin/bash
# Run processor and test using venv
set -e
cd "$(dirname "$0")"
source venv/bin/activate

# Kill any existing processor
pkill -f "processor_main" 2>/dev/null || true
sleep 2

# Start processor in background
python processor_main.py --port 8765 &
PID=$!
sleep 6

# Run test
python test_unified_mental_state.py --url http://localhost:8765 --timeout 90
EXIT=$?

# Leave processor running (or: kill $PID 2>/dev/null)
exit $EXIT

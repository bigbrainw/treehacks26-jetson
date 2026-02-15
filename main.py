#!/usr/bin/env python3
"""
Focus Agent - Receives activity + EEG, processes and helps user.

Run processor_main.py to receive data via WebSocket/HTTP and run the agent.
This main.py is deprecated; use processor_main.py for the full pipeline.

  python processor_main.py --port 8765
"""

import sys

print("Use processor_main.py to run the agent.")
print("  python processor_main.py --port 8765")
print("Data arrives via WebSocket or POST /eeg (context + streams).")
sys.exit(1)

#!/usr/bin/env python3
"""
EEG Collector - Sends Emotiv metrics to Jetson via WebSocket.

Activity/context data is received by the processor from an external source
(e.g. StreamToJetson, HTTP POST /eeg). This script only streams EEG.

Usage on Mac:
  python collector.py --url ws://JETSON_IP:8765
"""

import argparse
import json
import signal
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import websocket
except ImportError:
    websocket = None

import config
from data_schema import CollectorPayload, EEGMetricsSnapshot, MentalCommandSnapshot


def run_collector(jetson_url: str):
    """Stream EEG metrics and mental commands to Jetson via WebSocket."""
    if not websocket:
        print("Error: pip install websocket-client")
        sys.exit(1)

    ws_ref = {"ws": None}

    if not config.EMOTIV_CLIENT_ID or not config.EMOTIV_CLIENT_SECRET:
        print("Error: Set EMOTIV_CLIENT_ID and EMOTIV_CLIENT_SECRET in .env")
        sys.exit(1)

    def send_payload(payload: CollectorPayload):
        if ws_ref["ws"] and ws_ref["ws"].sock and ws_ref["ws"].sock.connected:
            try:
                ws_ref["ws"].send(json.dumps(payload.to_dict()))
            except Exception as e:
                print("  Send error:", e)

    try:
        from eeg import EmotivCortexClient

        streams = ["met", "com"]

        def on_eeg_metrics(metrics: dict):
            send_payload(CollectorPayload(
                type="eeg",
                eeg=EEGMetricsSnapshot(metrics=metrics),
            ))

        def on_mental_command(action: str, power: float):
            send_payload(CollectorPayload(
                type="mental_command",
                mental_command=MentalCommandSnapshot(action=action, power=power),
            ))

        emotiv_client = EmotivCortexClient(
            client_id=config.EMOTIV_CLIENT_ID,
            client_secret=config.EMOTIV_CLIENT_SECRET,
            on_metrics=on_eeg_metrics,
            on_mental_command=on_mental_command,
            streams=streams,
        )
        emotiv_client.connect()
        print("  Emotiv Cortex: connecting... (met + com)")
    except Exception as e:
        print(f"  Emotiv: {e}")
        sys.exit(1)

    running = True

    def stop(_=None, __=None):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    def connect_ws():
        def on_open(ws):
            print("  Connected to Jetson")

        def on_close(ws, close_status, close_msg):
            print("  Disconnected from Jetson")

        def on_error(ws, err):
            print("  WebSocket error:", err)

        ws = websocket.WebSocketApp(
            jetson_url,
            on_open=on_open,
            on_close=on_close,
            on_error=on_error,
        )
        ws_ref["ws"] = ws
        return ws

    print("EEG Collector starting...")
    print(f"  Target: {jetson_url}")
    print("  Sending: EEG + mental commands (Emotiv) → Jetson")
    print("  Activity data: received via processor's HTTP/WebSocket endpoints\n")

    ws = connect_ws()
    ws_thread = threading.Thread(target=lambda: ws.run_forever())
    ws_thread.daemon = True
    ws_thread.start()

    time.sleep(2)

    while running:
        time.sleep(config.POLL_INTERVAL)

    if emotiv_client:
        emotiv_client.close()
    print("\nStopped.")


def main():
    p = argparse.ArgumentParser(description="EEG sender → Jetson (activity from external source)")
    p.add_argument("--url", default=config.JETSON_WS_URL, help="WebSocket URL of Jetson")
    args = p.parse_args()
    run_collector(args.url)


if __name__ == "__main__":
    main()
    sys.exit(0)

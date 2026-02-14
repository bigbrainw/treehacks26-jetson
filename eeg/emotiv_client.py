"""
Emotiv Cortex API client - connects to Cortex service and streams performance metrics.

Requires:
- EMOTIV Launcher running (Cortex service on localhost:6868)
- Client ID and Client Secret from https://www.emotiv.com/developer
- User must approve access in Launcher on first run
"""

import json
import ssl
import threading
import time
from pathlib import Path
from typing import Callable, Optional

# Optional: use websocket-client
try:
    import websocket
except ImportError:
    websocket = None


CERT_PATH = Path(__file__).resolve().parent.parent / "certificates" / "rootCA.pem"


class EmotivCortexClient:
    """
    Minimal Cortex client for performance metrics ("met") stream.
    eng=engagement, attention, str=stress, rel=relaxation, int=interest
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        on_metrics: Optional[Callable[[dict], None]] = None,
        url: str = "wss://localhost:6868",
    ):
        if not websocket:
            raise ImportError("Install websocket-client: pip install websocket-client")
        self.client_id = client_id
        self.client_secret = client_secret
        self.on_metrics = on_metrics or (lambda _: None)
        self.url = url
        self.ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._auth_token: Optional[str] = None
        self._session_id: Optional[str] = None
        self._headset_id: Optional[str] = None
        self._met_labels: Optional[list] = None
        self._req_id = 0
        self._pending: dict = {}
        self._lock = threading.Lock()

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _send(self, method: str, params: dict, callback=None):
        if not self.ws or not self.ws.sock or not self.ws.sock.connected:
            return
        rid = self._next_id()
        msg = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        if callback:
            with self._lock:
                self._pending[rid] = callback
        self.ws.send(json.dumps(msg))

    def _handle_response(self, data: dict):
        rid = data.get("id")
        if rid is not None and rid in self._pending:
            with self._lock:
                cb = self._pending.pop(rid, None)
            if cb:
                cb(data)

    def _handle_stream(self, data: dict):
        if "met" not in data:
            return
        vals = data["met"]
        labels = self._met_labels or []
        if len(labels) != len(vals):
            return
        metrics = dict(zip(labels, vals))
        metrics["time"] = data.get("time")
        self.on_metrics(metrics)

    def _on_message(self, _ws, message):
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return
        if "sid" in data:
            self._handle_stream(data)
        elif "result" in data:
            self._handle_response(data)
        elif "error" in data:
            print("[Emotiv] Error:", data.get("error", {}).get("message", data))

    def _on_open(self, _ws):
        self._send("hasAccessRight", {
            "clientId": self.client_id,
            "clientSecret": self.client_secret,
        }, self._on_has_access)

    def _on_has_access(self, data: dict):
        result = data.get("result", {})
        if result.get("accessGranted"):
            self._send("authorize", {
                "clientId": self.client_id,
                "clientSecret": self.client_secret,
                "license": "",
                "debit": 10,
            }, self._on_authorize)
        else:
            self._send("requestAccess", {
                "clientId": self.client_id,
                "clientSecret": self.client_secret,
            }, self._on_request_access)

    def _on_request_access(self, data: dict):
        result = data.get("result", {})
        if result.get("accessGranted"):
            self._on_has_access({"result": result})
        else:
            print("[Emotiv] Please approve access in EMOTIV Launcher, then retry.")

    def _on_authorize(self, data: dict):
        result = data.get("result", {})
        token = result.get("cortexToken")
        if not token:
            return
        self._auth_token = token
        self._send("controlDevice", {"command": "refresh"}, self._on_refresh)

    def _on_refresh(self, _data: dict):
        time.sleep(1)
        self._send("queryHeadsets", {}, self._on_headsets)

    def _on_headsets(self, data: dict):
        headsets = data.get("result", [])
        if not headsets:
            print("[Emotiv] No headset found. Connect headset and ensure Cortex is running.")
            return
        hs = headsets[0]
        hid = hs["id"]
        status = hs.get("status", "")
        if status == "discovered":
            self._send("controlDevice", {"command": "connect", "headset": hid}, self._on_connect)
        elif status == "connected":
            self._headset_id = hid
            self._create_session()
        elif status == "connecting":
            time.sleep(2)
            self._send("queryHeadsets", {}, self._on_headsets)

    def _on_connect(self, _data: dict):
        time.sleep(2)
        self._send("queryHeadsets", {}, lambda d: self._set_headset_and_session(d))

    def _set_headset_and_session(self, data: dict):
        headsets = data.get("result", [])
        for hs in headsets or []:
            if hs.get("status") == "connected":
                self._headset_id = hs["id"]
                self._create_session()
                return
        time.sleep(1)
        self._send("queryHeadsets", {}, self._set_headset_and_session)

    def _create_session(self):
        if not self._auth_token or not self._headset_id:
            return
        self._send("createSession", {
            "cortexToken": self._auth_token,
            "headset": self._headset_id,
            "status": "active",
        }, self._on_session)

    def _on_session(self, data: dict):
        result = data.get("result", {})
        self._session_id = result.get("id")
        if self._session_id:
            self._subscribe_met()

    def _on_subscribe(self, data: dict):
        result = data.get("result", {})
        for s in result.get("success", []):
            if s.get("streamName") == "met":
                self._met_labels = s.get("cols", [])
                print("[Emotiv] Subscribed to performance metrics (met)")

    def _subscribe_met(self):
        if not self._auth_token or not self._session_id:
            return
        self._send("subscribe", {
            "cortexToken": self._auth_token,
            "session": self._session_id,
            "streams": ["met"],
        }, self._on_subscribe)

    def connect(self):
        """Connect and start streaming. Runs in background thread."""
        sslopt = {}
        if CERT_PATH.exists():
            sslopt = {"ca_certs": str(CERT_PATH), "cert_reqs": ssl.CERT_REQUIRED}
        else:
            sslopt = {"cert_reqs": ssl.CERT_NONE}
        self.ws = websocket.WebSocketApp(
            self.url,
            on_message=self._on_message,
            on_open=self._on_open,
        )
        self._thread = threading.Thread(target=lambda: self.ws.run_forever(sslopt=sslopt))
        self._thread.daemon = True
        self._thread.start()

    def close(self):
        if self.ws:
            self.ws.close()
            self.ws = None

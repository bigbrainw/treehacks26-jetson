#!/usr/bin/env python3
"""
Jetson Processor - Receives activity + EEG from Mac, runs agent.

Runs on Jetson Nano (Linux). Does NOT monitor locally.
All data from Mac via:
  - WebSocket (collector.py): activity + EEG
  - HTTP POST /eeg (StreamToJetson): {timestamp, streams: {met, pow, mot, dev}, context?: {...}}

Usage on Jetson:
  python processor_main.py --port 8765
  # Mac: python collector.py --url ws://JETSON_IP:8765 (sends activity + EEG)
"""

import argparse
import asyncio
import concurrent.futures
import json
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from activity_tracker import ActivityContext
from agent import FocusAssistant, MultiTurnAssistant
from data_schema import CollectorPayload
from eeg import EEGBridge
from storage import Storage
from time_tracker import SessionTracker, SessionEvent, SessionEventType


def _met_to_metrics(met) -> dict:
    """Convert met stream to metrics dict for EEGBridge. Handles dict, list, or nested {met: [...], time: ...}."""
    if met is None:
        return {}
    # Nested format: {"met": [0.5, 0.3, ...], "time": ...}
    if isinstance(met, dict) and "met" in met:
        met = met["met"]
    if isinstance(met, dict):
        return {k: float(v) for k, v in met.items() if isinstance(v, (int, float))}
    # Cortex SDK may pass list: [eng, interest, relaxation, stress, attention, ...] (bools coerced to float)
    if isinstance(met, (list, tuple)):
        labels = ["eng", "interest", "relaxation", "stress", "attention", "focus"]
        result = {}
        for i, v in enumerate(met):
            if i >= len(labels):
                break
            if isinstance(v, (int, float)):
                result[labels[i]] = float(v)
            elif isinstance(v, bool):
                result[labels[i]] = 1.0 if v else 0.0
        return result
    return {}


def run_processor(port: int):
    """Run Jetson: receive activity + EEG from Mac (WebSocket) → agent. No local monitoring."""
    storage = Storage(config.DB_PATH)
    AssistantClass = MultiTurnAssistant if config.MULTITURN_AGENT else FocusAssistant
    assistant = AssistantClass(
        api_key=config.ANTHROPIC_API_KEY,
        model=config.ANTHROPIC_MODEL,
    )
    session_tracker = SessionTracker(
        warn_threshold_sec=config.WARN_SESSION_THRESHOLD,
        long_threshold_sec=config.LONG_SESSION_THRESHOLD,
        follow_up_interval_sec=config.FOLLOW_UP_INTERVAL,
    )
    eeg_bridge = EEGBridge()

    current_session_id = None
    prev_context_id = None
    current_context_from_mac: ActivityContext | None = None
    prefetch_cache: dict[str, str] = {}  # context_id -> prepared_resources
    _prefetch_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

    def _prefetch_worker(ctx: ActivityContext):
        """Background: PDF page analysis. Web search via Agent SDK when stuck."""
        try:
            from agent.pdf_pipeline import build_pdf_prepared_resources

            section = getattr(ctx, "reading_section", None)
            page_content = getattr(ctx, "page_content", None) or ""

            if ctx.context_type == "pdf" and page_content:
                from activity_tracker.pdf_context import parse_pdf_window_title
                parsed = parse_pdf_window_title(ctx.app_name, ctx.window_title or "")
                doc_name = parsed.get("doc_name", ctx.window_title or "") if parsed else ctx.window_title or ""
                page_num = parsed.get("page_num", 1) if parsed else 1
                result = build_pdf_prepared_resources(
                    page_content, doc_name, page_num, section, web_related=None,
                    api_key=config.ANTHROPIC_API_KEY, model=config.ANTHROPIC_MODEL,
                )
            else:
                # Non-PDF: Agent SDK WebSearch/WebFetch when decide() is called
                result = None

            if result:
                prefetch_cache[ctx.context_id] = result
        except Exception:
            pass

    def _start_prefetch(ctx: ActivityContext):
        """Start background prefetch for this context."""
        _prefetch_executor.submit(_prefetch_worker, ctx)

    def on_context_change(new_ctx: ActivityContext, prev_ctx: ActivityContext | None):
        nonlocal current_session_id, prev_context_id
        if hasattr(assistant, "clear_conversation") and prev_context_id:
            assistant.clear_conversation(prev_context_id)
        if current_session_id:
            prev_session = session_tracker.get_current_session()
            duration = prev_session.duration_seconds if prev_session else 0
            storage.end_session(current_session_id, duration)
        current_session_id = storage.start_session(new_ctx)
        prev_context_id = new_ctx.context_id
        # Prefetch in background so it's ready when EEG triggers
        _start_prefetch(new_ctx)

    def on_session_event(event: SessionEvent):
        print(f"  [Event] {event.event_type.value}: {event.context.display_name} ({event.duration_seconds:.0f}s)")
        if event.event_type in (SessionEventType.LONG_THRESHOLD, SessionEventType.FOLLOW_UP):
            print("    -> Mental state check (EEG)")
            eeg_bridge.handle_session_event(event, storage)

    def on_mental_state(ctx: ActivityContext, duration: float, state, *, is_follow_up: bool = False):
        state_val = state.value
        print(f"  [Mental State] {state_val}: {ctx.display_name} (on page {duration:.0f}s)")
        if state_val in ("stuck", "distracted", "unknown"):
            recent = storage.get_recent_sessions(limit=8)
            prepared = prefetch_cache.get(ctx.context_id, "")
            kwargs = dict(
                app_name=ctx.app_name,
                window_title=ctx.window_title,
                context_type=ctx.context_type,
                duration_seconds=duration,
                mental_state=state_val,
                recent_sessions=recent,
                activity_context=ctx,
                prepared_resources=prepared or None,
            )
            if isinstance(assistant, MultiTurnAssistant):
                kwargs["user_feedback"] = (
                    "(Still on this - try a different angle)" if is_follow_up else None
                )
            decision = assistant.decide(**kwargs)
            if decision.should_help and decision.message:
                print(f"\n  >>> Agent: {decision.message}")

    session_tracker.on_session_event(on_session_event)
    eeg_bridge.on_mental_state_detected(on_mental_state)

    def handle_streams_payload(body: dict):
        """Handle StreamToJetson format: {timestamp, streams: {met, pow, mot, dev}, context?: {...}}."""
        nonlocal current_context_from_mac
        # Context (activity) - optional
        ctx_data = body.get("context")
        if ctx_data:
            from data_schema import ActivitySnapshot
            from activity_tracker.pdf_context import infer_context_type
            app = ctx_data.get("app_name", "")
            title = ctx_data.get("window_title", "")
            ctx_type = ctx_data.get("context_type") or infer_context_type(app, title)
            act = ActivitySnapshot(
                app_name=app,
                window_title=title,
                context_type=ctx_type,
                context_id=ctx_data.get("context_id", ""),
                detected_at=body.get("timestamp", time.time()),
                reading_section=ctx_data.get("reading_section"),
                page_content=ctx_data.get("page_content"),
            )
            ctx = act.to_activity_context()
            prev_ctx = current_context_from_mac
            if prev_ctx is None or prev_ctx.context_id != ctx.context_id:
                on_context_change(ctx, prev_ctx)
            current_context_from_mac = ctx
            session_tracker.update(ctx)
        # EEG streams
        streams = body.get("streams", {})
        met = streams.get("met")
        metrics = _met_to_metrics(met)
        if metrics:
            eeg_bridge.store_metrics(metrics)
        # Mental command (com stream) - act + pow
        com = streams.get("com")
        if isinstance(com, (list, tuple)) and len(com) >= 2:
            act = com[0] if isinstance(com[0], str) else "neutral"
            pow_val = float(com[1]) if isinstance(com[1], (int, float)) else 0.0
            if act and act != "neutral":
                _handle_mental_command(act, pow_val)

    def _handle_mental_command(action: str, power: float):
        """When mental command matches pizza trigger, order pizza."""
        cmd = getattr(config, "MENTAL_COMMAND_PIZZA", "push")
        thresh = getattr(config, "MENTAL_COMMAND_POWER_THRESHOLD", 0.5)
        if action == cmd and power >= thresh:
            print(f"\n  [Mental Command] {action} (power={power:.2f}) → Ordering pizza!")
            try:
                from agent.pizza_order import order_pizza
                msg = order_pizza()
                print(f"\n  >>> Pizza: {msg[:500]}{'...' if len(msg) > 500 else ''}")
            except Exception as e:
                print(f"  Pizza order error: {e}")

    def handle_ws_payload(payload: CollectorPayload):
        nonlocal current_context_from_mac
        if payload.activity:
            ctx = payload.activity.to_activity_context()
            prev_ctx = current_context_from_mac
            if prev_ctx is None or prev_ctx.context_id != ctx.context_id:
                on_context_change(ctx, prev_ctx)
            current_context_from_mac = ctx
            session_tracker.update(ctx)
        if payload.eeg:
            eeg_bridge.store_metrics(payload.eeg.metrics)
        if payload.mental_command:
            _handle_mental_command(
                payload.mental_command.action,
                payload.mental_command.power,
            )

    try:
        from aiohttp import web
        from aiohttp import WSMsgType
    except ImportError:
        print("Error: pip install aiohttp")
        sys.exit(1)

    running = True

    def stop(_=None, __=None):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    # HTTP POST /eeg - StreamToJetson format
    async def http_eeg(req):
        try:
            body = await req.json()
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
        handle_streams_payload(body)
        return web.json_response({"ok": True})

    # HTTP POST /suggest_snack - preferences + budget, agent suggests (no order)
    async def http_suggest_snack(req):
        try:
            body = await req.json()
            preferences = body.get("preferences", "").strip()
            budget = body.get("budget", 0)
            address = body.get("address")
            if not preferences:
                return web.json_response({"error": "preferences required"}, status=400)
            from agent.snack_suggestion import suggest_snack
            result = suggest_snack(preferences=preferences, budget=budget, address=address)
            return web.json_response({"suggestion": result})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    # WebSocket - legacy collector.py
    async def websocket_handler(req):
        ws = web.WebSocketResponse()
        await ws.prepare(req)
        print("  Mac (activity + EEG) connected via WebSocket")
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        payload = CollectorPayload.from_dict(data)
                        handle_ws_payload(payload)
                    except (json.JSONDecodeError, KeyError):
                        pass
        finally:
            print("  Mac disconnected")
        return ws

    app = web.Application()
    app.router.add_post("/eeg", http_eeg)
    app.router.add_post("/suggest_snack", http_suggest_snack)
    app.router.add_get("/", websocket_handler)
    app.router.add_get("/ws", websocket_handler)

    async def main_loop():
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        print(f"Processor on 0.0.0.0:{port}")
        print(f"  WebSocket / or /ws - activity + EEG from Mac collector")
        print(f"  POST /eeg  - streams + optional context (StreamToJetson)")
        print(f"  POST /suggest_snack - preferences + budget → snack suggestions (no order)\n")

        while running:
            ctx = current_context_from_mac
            session = session_tracker.update(ctx)
            if session and running:
                mins = session.duration_seconds / 60
                st = (
                    "LONG"
                    if session.duration_seconds >= config.LONG_SESSION_THRESHOLD
                    else "WARN"
                    if session.duration_seconds >= config.WARN_SESSION_THRESHOLD
                    else ""
                )
                print(
                    f"\r  {session.context.display_name[:50]} ... {mins:.1f}m {st}    ",
                    end="",
                    flush=True,
                )
            await asyncio.sleep(config.POLL_INTERVAL)

        await runner.cleanup()

    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        pass
    print("\nShutting down.")


def main():
    p = argparse.ArgumentParser(description="Jetson processor - receives activity + EEG from Mac via WebSocket")
    p.add_argument("--port", type=int, default=config.JETSON_WS_PORT, help="Port for HTTP + WebSocket")
    args = p.parse_args()
    run_processor(args.port)


if __name__ == "__main__":
    main()
    sys.exit(0)

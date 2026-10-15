#!/usr/bin/env python3
"""Mosaic brain stub: loopback receiver for bridge WhatsApp events.

This week the brain only acknowledges events on POST /event so the
bridge's POST 127.0.0.1:7000/event hook has somewhere to land.
Worker B owns self-chat resolution and real replies; nothing here
creates groups or sends messages.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

HOST = "127.0.0.1"
PORT = int(os.getenv("BRAIN_PORT", "7000"))


def route_event(chat_jid: str, sender: str, content: str,
                msg_id=None) -> str:
    """One bridge event → outcome string. Slash first, then worker-f.

    Pure dispatch (no HTTP): do_POST calls this, and tests call it
    directly with stubbed engines.
    """
    outcome = "ignored"
    try:  # Worker C: slash commands answered privately to self-chat.
        from hermes_bot.slash import get_engine
        outcome = get_engine().handle_event(
            chat_jid or "", sender or "", content or "", msg_id,
        )
        print(f"[brain] slash outcome={outcome}", flush=True)
    except Exception as exc:
        print(f"[brain] slash dispatch failed: {exc}", flush=True)
    if outcome == "ignored":
        try:  # Worker F: draft/send/pings from the owner's self-chat.
            from hermes_bot import worker_f
            outcome = worker_f.handle_event(
                chat_jid or "", sender or "", content or "",
            )
            print(f"[brain] worker-f outcome={outcome}", flush=True)
        except Exception as exc:
            print(f"[brain] worker-f dispatch failed: {exc}", flush=True)
    return outcome


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):  # noqa: N802
        if self.path != "/event":
            self._json(404, {"ok": False, "error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            event = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"ok": False, "error": "invalid json"})
            return
        print(
            "[brain] event chat={} sender={} content={!r}".format(
                event.get("chat_jid"), event.get("sender"), event.get("content")
            ),
            flush=True,
        )
        route_event(event.get("chat_jid", ""), event.get("sender", ""),
                    event.get("content", ""), event.get("msg_id"))
        self._json(200, {"ok": True})

    def log_message(self, *args):  # keep supervisord logs quiet
        pass


def main() -> None:
    server = HTTPServer((HOST, PORT), Handler)
    print(f"[brain] stub listening on {HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

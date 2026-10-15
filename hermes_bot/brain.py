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
        try:  # Worker C: slash commands answered privately to self-chat.
            from hermes_bot.slash import get_engine
            outcome = get_engine().handle_event(
                event.get("chat_jid", ""),
                event.get("sender", ""),
                event.get("content", ""),
                event.get("msg_id"),
            )
            print(f"[brain] slash outcome={outcome}", flush=True)
        except Exception as exc:
            print(f"[brain] slash dispatch failed: {exc}", flush=True)
        self._json(200, {"ok": True})

    def log_message(self, *args):  # keep supervisord logs quiet
        pass


def main() -> None:
    server = HTTPServer((HOST, PORT), Handler)
    print(f"[brain] stub listening on {HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import os
import sys
import signal
import threading
import time
import json
from pathlib import Path

from hermes_bot import supervisor, config
from hermes_bot.sender import enqueue_to_mechat, start_flush_thread
from hermes_bot.db import get_own_phone
from hermes_bot.onboarding import send_welcome
from hermes_bot import hardening, store as _store

_running = True
_bridge_watch = None


def _bridge_up() -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"{config.BRIDGE_URL}/health",
                                    timeout=1) as resp:
            return resp.status == 200
    except Exception:
        return False


def _startup_prune() -> None:
    try:
        conn = _store.connect()
        _store.init_db(conn)
        counts = hardening.prune_retention(conn)
        conn.close()
        if sum(counts.values()):
            print(f"[mosaic] retention prune: {counts}", flush=True)
    except Exception as exc:
        print(f"[mosaic] retention prune skipped ({exc})", flush=True)


def _drain_bridge_output(proc):
    def _run():
        try:
            for line in iter(proc.stdout.readline, ""):
                stripped = line.rstrip()
                if stripped:
                    print(f"[bridge] {stripped}", flush=True)
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()


def _shutdown(signum, frame):
    global _running
    print("\n[mosaic] Shutting down...")
    _running = False
    try:
        enqueue_to_mechat("⚠️ *Mosaic* · Offline")
    except Exception:
        pass
    sys.exit(0)


def _read_setup_state() -> str:
    path = Path(config.SETUP_FILE)
    if path.exists():
        try:
            data = json.loads(path.read_text())
            return data.get("state", "")
        except Exception:
            pass
    return ""


def main():
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print("[mosaic] Starting Mosaic...")
    print(f"[mosaic] Bridge URL: {config.BRIDGE_URL}")
    print(f"[mosaic] Messages DB: {config.MESSAGES_DB}")
    print(f"[mosaic] Store Dir: {config.STORE_DIR}")

    proc = supervisor.start_bridge()
    _drain_bridge_output(proc)

    if not supervisor.wait_for_readiness(timeout=180):
        proc.terminate()
        print("[mosaic] Bridge failed to start. Exiting.")
        sys.exit(1)

    print("[mosaic] Bridge ready. Starting services...")
    start_flush_thread()
    _startup_prune()

    global _bridge_watch
    _bridge_watch = hardening.BridgeWatch(probe=_bridge_up,
                                          notify=enqueue_to_mechat)

    was_ready = False
    print("[mosaic] All systems go. Waiting for setup to complete...\n")

    while _running:
        try:
            state_now = _bridge_watch.tick()
            if state_now in ("down-notice-sent", "recovered-notice-sent"):
                print(f"[mosaic] bridge watch: {state_now}", flush=True)
        except Exception:
            pass

        if proc.poll() is not None:
            print("[mosaic] Bridge exited. Restarting...")
            time.sleep(2)
            proc = supervisor.start_bridge()
            _drain_bridge_output(proc)
            supervisor.wait_for_readiness(timeout=60)
            continue

        state = _read_setup_state()
        
        if state == "READY" and not was_ready:
            was_ready = True
            print("[mosaic] Setup complete! Mosaic is READY.")
            try:
                phone = get_own_phone()
                send_welcome(text=f"🤖 *Mosaic* · Ready\nID: `{phone}`\n/help for commands")
            except Exception as e:
                print(f"[mosaic] Welcome message failed: {e}")
        
        elif state == "RESETTING":
            print("[mosaic] Reset in progress, waiting for restart...")
        
        time.sleep(2)

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


if __name__ == "__main__":
    main()
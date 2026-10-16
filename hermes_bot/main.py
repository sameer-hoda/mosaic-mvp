#!/usr/bin/env python3
import os
import sys
import signal
import threading
import time
import json
from pathlib import Path

from hermes_bot import supervisor, config
from hermes_bot import cycle as _cycle
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


def _owner_names() -> list:
    raw = os.getenv("OWNER_NAMES", "")
    return [n.strip() for n in raw.split(",") if n.strip()]


def _start_brain_server():
    """Serve the bridge event hook in-process (fix 4).

    Railway runs the brain as a separate supervisord process; if the
    port is already owned there, binding fails and we skip quietly.
    """
    from http.server import HTTPServer
    from hermes_bot import brain as _brain
    try:
        port = int(os.getenv("BRAIN_PORT", "7000") or 7000)
    except ValueError:
        port = 7000
    try:
        server = HTTPServer(("127.0.0.1", port), _brain.Handler)
    except OSError as exc:
        print(f"[mosaic] brain hook not started ({exc}); "
              f"assuming a separate brain owns :{port}.", flush=True)
        return None
    threading.Thread(target=server.serve_forever, daemon=True,
                     name="mosaic-brain").start()
    print(f"[mosaic] brain event hook on 127.0.0.1:{port}/event.", flush=True)
    return server


def _run_cycle_once():
    """One scan → extract → brief pass plus a cycle heartbeat row.

    Returns the cycle report, or None when the pass was skipped.
    """
    try:
        conn = _store.connect()
        _store.init_db(conn)
        try:
            live = os.getenv("MOSAIC_LIVE", "") == "1"
            report = _cycle.run_cycle(
                conn, owner_names=_owner_names(), dry_run=not live,
                deliver=enqueue_to_mechat if live else None)
            print(
                "[mosaic] cycle: "
                f"{report['messages']} msgs, {report['items']} items, "
                f"brief #{report['brief_no']} "
                f"({'delivered' if report['delivered'] else 'dry-run'})",
                flush=True)
            _store.log_event(
                conn, "cycle",
                detail=(f"msgs={report['messages']} "
                        f"items={report['items']} "
                        f"brief={report['brief_no']}"))
            return report
        finally:
            conn.close()
    except Exception as exc:
        print(f"[mosaic] cycle skipped ({exc})", flush=True)
        return None


def _cycle_loop():
    """Periodic scan → extract → brief → draft pass.

    Runs once immediately, then every interval. Dry-run unless
    MOSAIC_LIVE=1: the brief text is only enqueued to self-chat when
    live, otherwise it is logged print-only. Any failure is logged and
    the loop continues on the next tick.
    """
    while _running:
        _run_cycle_once()
        if not _running:
            break
        time.sleep(_cycle.cycle_interval_s())
        if not _running:
            break


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
    _start_brain_server()
    _startup_prune()

    if _cycle.cycle_enabled():
        threading.Thread(target=_cycle_loop, daemon=True,
                         name="mosaic-cycle").start()
        print("[mosaic] Pipeline cycle enabled "
              f"(every {_cycle.cycle_interval_s():.0f}s).")

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
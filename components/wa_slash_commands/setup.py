#!/usr/bin/env python3
"""
setup.py — Interactive setup wizard for wa-slash-commands
Run me once to configure everything:  python3 setup.py
"""

import os
import sys
import subprocess
import getpass
import platform
import time
import threading
import sqlite3

# ── ANSI helpers ──────────────────────────────────────────────────────────────
class C:
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    CYAN   = "\033[96m"
    RED    = "\033[91m"
    RESET  = "\033[0m"

def ok(msg):   print(f"  {C.GREEN}✓{C.RESET} {msg}")
def err(msg):  print(f"  {C.RED}✗{C.RESET} {msg}")
def info(msg): print(f"  {C.CYAN}→{C.RESET} {msg}")
def warn(msg): print(f"  {C.YELLOW}!{C.RESET} {msg}")

def header(step, title):
    print(f"\n{C.BOLD}{C.CYAN}── Step {step}: {title} {'─' * (48 - len(title))}{C.RESET}")

def prompt(label, default=None, secret=False):
    hint = f"{C.DIM}[{default}]{C.RESET} " if default else ""
    full_label = f"  {C.BOLD}{label}{C.RESET} {hint}» "
    if secret:
        val = getpass.getpass(full_label)
    else:
        val = input(full_label).strip()
    return val or default or ""

def pause(secs=0.4):
    time.sleep(secs)

# ── Banner ─────────────────────────────────────────────────────────────────────
def banner():
    print(f"""
{C.BOLD}{C.CYAN}
  ██╗    ██╗ █████╗       ███████╗██╗      █████╗ ███████╗██╗  ██╗
  ██║    ██║██╔══██╗      ██╔════╝██║     ██╔══██╗██╔════╝██║  ██║
  ██║ █╗ ██║███████║█████╗███████╗██║     ███████║███████╗███████║
  ██║███╗██║██╔══██║╚════╝╚════██║██║     ██╔══██║╚════██║██╔══██║
  ╚███╔███╔╝██║  ██║      ███████║███████╗██║  ██║███████║██║  ██║
   ╚══╝╚══╝ ╚═╝  ╚═╝      ╚══════╝╚══════╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝
{C.RESET}{C.DIM}  WhatsApp Slash Commands · Setup Wizard{C.RESET}
""")

# ── Step 1: Gemini API Key ─────────────────────────────────────────────────────
def step_gemini_key():
    header(1, "Gemini API Key")
    info("Get your free key at: https://aistudio.google.com/app/apikey")
    while True:
        key = prompt("Paste your Gemini API key", secret=True)
        if key and key.startswith("AI") and len(key) > 20:
            ok("API key looks good.")
            return key
        elif key:
            warn("Key doesn't look right (should start with 'AI...'). Try again.")
            skip = input("  Skip for now? [y/N] ").strip().lower()
            if skip == "y":
                warn("Skipping — AI commands will be unavailable until you set GEMINI_API_KEY in .env")
                return key
        else:
            warn("No key entered — AI commands will be unavailable.")
            return ""

# ── Step 2: Auto-detect Owner ──────────────────────────────────────────────────
def step_detect_owner():
    header(2, "Owner Detection")
    db = os.path.join(_db_store_dir(), "whatsapp.db")
    if not os.path.exists(db):
        warn("No WhatsApp database found. Scan QR first.")
        return None

    try:
        conn = sqlite3.connect(db)
        # whatsmeow stores the paired user JID in the whatsmeow_device table
        res = conn.execute("SELECT jid FROM whatsmeow_device LIMIT 1").fetchone()
        conn.close()
        if res and res[0]:
            full_jid = res[0]
            # Format is usually 919876543210.0:86@s.whatsapp.net
            phone = full_jid.split('@')[0].split('.')[0].split(':')[0]
            ok(f"Detected owner: {C.BOLD}{phone}{C.RESET}")
            return phone
    except Exception as e:
        warn(f"Could not auto-detect owner from DB: {e}")
    
    info("Could not auto-detect. Please enter manually:")
    while True:
        phone = prompt("Your WhatsApp number (country code first)")
        phone = phone.replace("+", "").replace(" ", "").replace("-", "")
        if phone.isdigit() and len(phone) >= 10:
            return phone
        warn("Invalid format. Try again.")

# ── Bridge helpers ─────────────────────────────────────────────────────────────
def _find_bridge_binary():
    """Locate the whatsmeow bridge binary relative to this script."""
    _dir = os.path.dirname(os.path.abspath(__file__))
    bridge_dir = os.path.join(_dir, "bridge")
    binary_path = os.path.join(bridge_dir, "wa-bridge")

    # If the binary exists and is executable, return it
    if os.path.isfile(binary_path) and os.access(binary_path, os.X_OK):
        return binary_path

    # If not, try to build it
    if os.path.isdir(bridge_dir) and os.path.isfile(os.path.join(bridge_dir, "main.go")):
        info("Bridge binary not found. Compiling from source...")
        try:
            subprocess.run(["go", "build", "-o", "wa-bridge", "main.go"], cwd=bridge_dir, check=True)
            ok("Bridge compiled successfully.")
            return binary_path
        except FileNotFoundError:
            err("Go compiler not found. Please install Go (https://golang.org/doc/install) to build the bridge.")
        except subprocess.CalledProcessError as e:
            err(f"Failed to compile bridge: {e}")

    return None

def _db_store_dir():
    """Where the bridge creates its databases — local to this project."""
    _dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(_dir, "store")

def _has_valid_session():
    """Check if whatsapp.db exists AND contains a device session."""
    db = os.path.join(_db_store_dir(), "whatsapp.db")
    if not os.path.exists(db):
        return False
    try:
        conn = sqlite3.connect(db)
        # The whatsmeow_device table holds the authenticated session
        count = conn.execute("SELECT COUNT(*) FROM whatsmeow_device").fetchone()[0]
        conn.close()
        return count > 0
    except Exception:
        return False

# ── Step 3: WhatsApp Authentication (QR) ──────────────────────────────────────
def step_whatsapp_auth(reset=False):
    header(3, "WhatsApp Authentication")

    store_dir = _db_store_dir()

    # If DBs already exist AND we're not forcing a reset, skip
    if _has_valid_session() and not reset:
        ok("WhatsApp session already authenticated. Skipping QR scan.")
        return

    bridge = _find_bridge_binary()
    if not bridge:
        warn("Could not find the whatsmeow bridge binary.")
        warn("Make sure the '../whatsapp-bridge/' directory contains the bridge executable.")
        return

    # ── Kill any conflicting bridge before launching ─────────────
    info("Ensuring no other bridge processes are running...")
    try:
        subprocess.run(["pkill", "-f", "wa-bridge"], capture_output=True)
        subprocess.run(["pkill", "-f", "whatsapp-bridge"], capture_output=True)
        time.sleep(1)
    except Exception:
        pass

    # ── Wipe any existing session so the bridge shows a fresh QR ─────────────
    if os.path.exists(store_dir):
        info("Clearing existing session data for a fresh authentication...")
        import shutil
        shutil.rmtree(store_dir)
    os.makedirs(store_dir, exist_ok=True)

    print(f"\n  {C.BOLD}Starting WhatsApp bridge…{C.RESET}")
    info("Watch for the QR code below, then open WhatsApp on your phone:")
    info("   Linked Devices  →  Link a Device  →  Scan QR code")
    print(f"\n  {C.DIM}(Ctrl+C after scanning to continue, or wait — it auto-continues){C.RESET}\n")
    print("  " + "─" * 56)

    _dir = os.path.dirname(os.path.abspath(__file__))
    try:
        proc = subprocess.Popen(
            [bridge],
            cwd=_dir,
            stdout=sys.stdout,
            stderr=sys.stdout,
            bufsize=1,
            universal_newlines=True,
        )

        # Poll until a valid session appears in the DB
        try:
            while proc.poll() is None:
                if _has_valid_session():
                    time.sleep(3)  # let bridge finish initial sync burst
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

    except Exception as e:
        err(f"Failed to start bridge: {e}")
        return

    print("\n  " + "─" * 56)
    if _has_valid_session():
        ok("WhatsApp connected. Databases synced ✨")
    else:
        warn("Valid session not found — scan the QR and wait for 'Connected' message.")
        warn("Re-run: python3 setup.py  to retry.")


# ── Step 4: Write .env ────────────────────────────────────────────────────────
def step_write_env(gemini_key, owner_phone):
    header(4, "Writing .env")
    _dir = os.path.dirname(os.path.abspath(__file__))
    store_dir  = _db_store_dir()
    messages_db = os.path.join(store_dir, "messages.db")
    whatsapp_db = os.path.join(store_dir, "whatsapp.db")
    env_path    = os.path.join(_dir, ".env")

    content = f"""# wa-slash-commands configuration — generated by setup.py

GEMINI_API_KEY="{gemini_key}"
OWNER_PHONE_NUMBER="{owner_phone}"

MESSAGES_DB_PATH="{messages_db}"
WHATSAPP_DB_PATH="{whatsapp_db}"

# HTTP endpoint for your whatsmeow bridge (default is fine for local use)
WA_API_URL="http://localhost:8080/api/send"
"""
    with open(env_path, "w") as f:
        f.write(content)

    ok(f".env written.")
    return env_path

# ── Step 5: Run local test ────────────────────────────────────────────────────
def step_run_test():
    header(5, "Smoke Test")
    info("Running /help command to confirm everything is wired up.")
    print()

    _dir = os.path.dirname(os.path.abspath(__file__))
    test_path = os.path.join(_dir, "test_local.py")
    result = subprocess.run([sys.executable, test_path, "--cmd", "/help"], cwd=_dir)
    if result.returncode == 0:
        ok("Smoke test passed ✨")
    else:
        err("Test exited with errors — check output above.")

# ── Done ──────────────────────────────────────────────────────────────────────
def done_screen(phone):
    print(f"""
{C.BOLD}{C.GREEN}  ✅  You're all set!{C.RESET}

  1. {C.BOLD}Start the bridge{C.RESET} (keep this running in a separate terminal):
     {C.CYAN}./start.sh{C.RESET}

  2. {C.BOLD}Send a command{C.RESET} from {C.BOLD}{phone}{C.RESET} in any WhatsApp chat:
     {C.CYAN}/help{C.RESET}       — list all commands
     {C.CYAN}/recap{C.RESET}      — 24h timeline of key events
     {C.CYAN}/pending{C.RESET}    — open threads & next steps
     {C.CYAN}/sotu{C.RESET}       — 30-day state of the union
     {C.CYAN}/stats{C.RESET}      — team personas & activity
""")


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    banner()
    print(f"  {C.DIM}This wizard configures your .env and authenticates WhatsApp.{C.RESET}")
    print(f"  {C.DIM}You only need to do this once.{C.RESET}\n")

    try:
        gemini_key  = step_gemini_key()
        pause()
        
        # WhatsApp Auth MUST happen before detection
        step_whatsapp_auth()
        pause()

        # Now we can detect the owner from the session we just created
        owner_phone = step_detect_owner()
        pause()
        
        step_write_env(gemini_key, owner_phone)
        pause()
        step_run_test()
        done_screen(owner_phone)
    except KeyboardInterrupt:
        print(f"\n\n  {C.YELLOW}Setup cancelled.{C.RESET} Run {C.CYAN}python3 setup.py{C.RESET} to restart.\n")
        sys.exit(0)

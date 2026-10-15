import os
import sys
import time
import subprocess
from pathlib import Path

from hermes_bot import config

BRIDGE_DIR = config.PROJECT_ROOT / "components" / "wa_bridge"


def _bridge_port() -> int:
    port = os.getenv("BRIDGE_PORT", "").strip()
    if port:
        return int(port)
    try:
        from urllib.parse import urlparse
        parsed = urlparse(config.BRIDGE_URL)
        if parsed.port:
            return parsed.port
    except Exception:
        pass
    return 8081


def _bridge_binary() -> Path:
    return BRIDGE_DIR / "wa-bridge"


def start_bridge() -> subprocess.Popen:
    binary = _bridge_binary()
    if not binary.exists():
        print("[supervisor] Bridge binary not found. Run build first: cd components/wa_bridge && go build -o wa-bridge .")
        sys.exit(1)

    env = os.environ.copy()
    if config.OWNER_PHONE:
        env["OWNER_PHONE_NUMBER"] = config.OWNER_PHONE
    if config.MECHAT_JID:
        env["MECHAT_JID"] = config.MECHAT_JID
    bridge_port = os.getenv("BRIDGE_PORT", "").strip()
    if bridge_port:
        env["BRIDGE_PORT"] = bridge_port

    print("[supervisor] Starting Go bridge...")
    proc = subprocess.Popen(
        [str(binary)],
        cwd=str(BRIDGE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    return proc


def wait_for_readiness(timeout=180) -> bool:
    import urllib.request
    import urllib.error

    public_port = os.getenv("PORT", "8080")
    
    start = time.time()
    while time.time() - start < timeout:
        try:
            url = f"http://127.0.0.1:{public_port}/health"
            resp = urllib.request.urlopen(url, timeout=3)
            if resp.status == 200:
                print("[supervisor] Bridge health check passed.")
                return True
        except Exception:
            pass
        time.sleep(2)
    
    print("[supervisor] Bridge did not become ready within timeout.")
    return False
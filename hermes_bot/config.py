import os
import json
import datetime
from pathlib import Path

import pytz
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

load_dotenv(SCRIPT_DIR / ".env")

STORE_DIR = Path(os.getenv("STORE_DIR", SCRIPT_DIR / "store"))

SETUP_FILE = STORE_DIR / "setup.json"
CONFIG_FILE = STORE_DIR / "config.json"


def _read_setup_json():
    path = Path(SETUP_FILE)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except:
            pass
    return {}


def _read_config_json():
    path = Path(CONFIG_FILE)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except:
            pass
    return {}


_config = _read_config_json()
_setup = _read_setup_json()

# ── Timezone ───────────────────────────────────────────────────────────────────
HERMES_TIMEZONE = os.getenv("TZ", os.getenv("HERMES_TIMEZONE", "Asia/Kolkata"))
TIMEZONE = pytz.timezone(HERMES_TIMEZONE)

# ── Databases (produced by the Go bridge) ────────────────────────────────────
MESSAGES_DB = os.getenv("MESSAGES_DB_PATH") or str(STORE_DIR / "messages.db")
WHATSAPP_DB = os.getenv("WHATSAPP_DB_PATH") or str(STORE_DIR / "whatsapp.db")

# ── Bridge HTTP API ──────────────────────────────────────────────────────────
BRIDGE_URL = os.getenv("WA_API_URL", "http://127.0.0.1:8081")

# ── Owner ────────────────────────────────────────────────────────────────────
OWNER_PHONE = (
    os.getenv("OWNER_PHONE_NUMBER", "").strip().replace("+", "")
    or _setup.get("own_phone", "")
)

# ── MeChat (owner's self-chat / personal notes group) ───────────────────────
MECHAT_JID = os.getenv("MECHAT_JID", "").strip() or _setup.get("mechat_jid", "")

# ── LLM ──────────────────────────────────────────────────────────────────────
GEMINI_API_KEY = (
    os.getenv("GEMINI_API_KEY", "")
    or _config.get("gemini_api_key", "")
)
GEMINI_MODEL_FAST = os.getenv("GEMINI_MODEL_FAST", "gemini-3.1-flash-lite-preview")
GEMINI_MODEL_PRO = os.getenv("GEMINI_MODEL_PRO", "gemini-3.1-flash-lite-preview")

# ── Session ──────────────────────────────────────────────────────────────────
SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", "60"))
SESSION_FILE = os.getenv("SESSION_FILE") or str(STORE_DIR / "session.json")

# ── Deep Dive Pipeline ──────────────────────────────────────────────────────
MAX_GROUPS_PER_SEARCH = int(os.getenv("MAX_GROUPS_PER_SEARCH", "10"))
SEARCH_LOOKBACK_DAYS = int(os.getenv("SEARCH_LOOKBACK_DAYS", "14"))
SEARCH_MESSAGES_PER_GROUP = int(os.getenv("SEARCH_MESSAGES_PER_GROUP", "200"))

# ── Cron ─────────────────────────────────────────────────────────────────────
CRON_ENABLED = os.getenv("CRON_ENABLED", "1") != "0"
HERMES_DB_PATH = os.getenv("HERMES_DB_PATH") or str(STORE_DIR / "hermes.db")

# ── Pending Messages ─────────────────────────────────────────────────────────
PENDING_MESSAGES_FILE = str(STORE_DIR / "pending_messages.json")
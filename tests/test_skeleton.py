"""Skeleton acceptance: one-container shape, no banned paths, safe bridge.

Red/green TDD: written before the bridge rework; passes only when the
fork is gutted to the skeleton (no wa_pull/cron/OKF, no per-message
exec python3, loopback+token send API, POST-to-brain event hook).
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE_GO = ROOT / "components" / "wa_bridge" / "main.go"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_banned_directories_absent():
    for banned in ("components/wa_pull", "hermes_bot/cron"):
        assert not (ROOT / banned).exists(), f"banned path still present: {banned}"


def test_banned_code_files_absent():
    hits = [
        p for p in list(ROOT.rglob("okf_builder.py")) + list(ROOT.rglob("okf_reader.py"))
        if ".git" not in p.parts
    ]
    assert hits == [], f"OKF/knowledge-file code still present: {hits}"
    electron = [
        p for p in ROOT.rglob("*electron*")
        if ".git" not in p.parts
    ]
    assert electron == [], f"Electron remnants still present: {electron}"


def test_no_per_message_exec_python():
    src = _read(BRIDGE_GO)
    assert "exec python3" not in src
    assert 'exec.Command("python3"' not in src
    assert "mechat_handler.py" not in src


def test_bridge_posts_events_to_brain():
    src = _read(BRIDGE_GO)
    assert "127.0.0.1:7000/event" in src


def test_send_api_binds_localhost_only():
    src = _read(BRIDGE_GO)
    assert "127.0.0.1:%d" in src
    assert "0.0.0.0" not in src


def test_send_api_requires_token():
    src = _read(BRIDGE_GO)
    assert "MOSAIC_BRIDGE_TOKEN" in src
    assert "StatusUnauthorized" in src


def test_no_group_creation():
    for path in list(ROOT.rglob("*.go")) + list(ROOT.rglob("*.py")):
        if ".git" in path.parts or "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        assert "creategroup" not in text, f"group-create in {path}"
        assert "groupcreate" not in text, f"group-create in {path}"


def test_supervisor_config_one_container_two_processes():
    conf = ROOT / "supervisord.conf"
    assert conf.exists(), "supervisord.conf missing"
    text = _read(conf)
    assert "program:bridge" in text
    assert "program:brain" in text
    assert "/data" in text

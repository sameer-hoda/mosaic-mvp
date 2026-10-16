"""Start script must provision the local runtime (fixes 4/5/7/8)."""

from pathlib import Path

SCRIPT = Path("scripts/hermes_start.sh")


def _src():
    return SCRIPT.read_text(encoding="utf-8")


def test_brain_port_provisioned():
    src = _src()
    assert "BRAIN_PORT" in src and "BRAIN_EVENT_URL" in src


def test_local_data_dir_provisioned():
    src = _src()
    assert "MOSAIC_DATA_DIR" in src


def test_bridge_token_provisioned():
    src = _src()
    assert "MOSAIC_BRIDGE_TOKEN" in src


def test_live_snapshot_and_cycle_defaults():
    src = _src()
    assert "MOSAIC_SNAPSHOT_PATH" in src
    assert "MOSAIC_CYCLE" in src

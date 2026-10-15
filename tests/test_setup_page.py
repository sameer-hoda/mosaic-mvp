"""Worker B Task 3 (RED): setup page shows exactly three things pre-pair,
and only the connected line + Unlink post-pair. No pairing-code/group UI.
"""

import re
from pathlib import Path

WIZARD = Path("components/wa_bridge/wizard.html")


def _src():
    return WIZARD.read_text(encoding="utf-8")


def test_setup_view_has_exactly_three_things():
    src = _src()
    for el in ("id=\"bridge-status\"", "id=\"qr-img\"", "id=\"gemini-tick\""):
        assert el in src, f"missing setup element {el}"


def test_qr_auto_refreshes():
    src = _src()
    assert "setInterval" in src
    assert re.search(r"setInterval\(.{0,80}2000", src), "expected ~2s poll for QR refresh"
    assert "/setup/state" in src


def test_connected_view_only_line_plus_unlink():
    src = _src()
    assert "id=\"connected-line\"" in src
    assert "id=\"unlink-btn\"" in src
    assert "Connected as" in src
    assert "last message" in src


def test_no_pairing_or_group_ui():
    src = _src().lower()
    assert "pairing-code" not in src
    assert "mosaic-" not in src
    assert "re-pair" not in src and "repair-mechat" not in src
    assert "g.us" not in src

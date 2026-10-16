"""Session fixture: guarantee a test snapshot exists (fix 1).

On a fresh clone /tmp/mosaic-test/messages.db is absent, so any test
opening the snapshot builds the synthetic one first. Never touches a
snapshot that already holds messages.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from ensure_test_snapshot import ensure_test_snapshot


@pytest.fixture(scope="session", autouse=True)
def _test_snapshot():
    ensure_test_snapshot()
    yield

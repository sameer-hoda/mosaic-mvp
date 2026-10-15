"""
ops/tests/test_api.py -- Phase 4 backend tests (T4.1--T4.4)
Run with: pytest ops/tests/test_api.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from fastapi.testclient import TestClient

from ops.api import app
from ops.db import init_schema, delete_card
from ops.card_engine import create_manual_card

client = TestClient(app)


class TestAPIGroups:
    """T4.1: GET /api/groups returns groups with whitelisted flag."""

    def test_list_groups(self):
        init_schema()
        resp = client.get("/api/groups")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        if len(data) > 0:
            assert "jid" in data[0]
            assert "name" in data[0]
            assert "whitelisted" in data[0]


class TestAPIWhitelist:
    """T4.2: POST /api/groups/whitelist updates groups."""

    def test_whitelist(self):
        groups = client.get("/api/groups").json()
        if not groups:
            return
        jid = groups[0]["jid"]
        resp = client.post("/api/groups/whitelist", json={"jids": [jid]})
        assert resp.status_code == 200
        assert resp.json()["whitelisted"] == 1

    def test_whitelist_too_many(self):
        resp = client.post("/api/groups/whitelist", json={"jids": ["a", "b", "c", "d", "e", "f"]})
        assert resp.status_code == 400


class TestAPICards:
    """T4.3: CRUD for cards."""

    def test_create_card(self):
        resp = client.get("/api/groups")
        groups = resp.json()
        if not groups:
            return
        gid = groups[0]["jid"]
        resp = client.post("/api/cards", json={
            "group_id": gid,
            "headline": "Test issue from API",
            "description": "Testing manual card creation",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Test issue from API"
        assert data["status"] == "backlog"
        delete_card(data["id"])

    def test_list_cards(self):
        resp = client.get("/api/cards")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_card_detail(self):
        groups = client.get("/api/groups").json()
        if not groups:
            return
        created = client.post("/api/cards", json={
            "group_id": groups[0]["jid"],
            "headline": "Detail test",
        }).json()
        resp = client.get(f"/api/cards/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Detail test"
        delete_card(created["id"])


class TestAPILifecycle:
    """T4.4: Play/pause/move."""

    def test_play_card(self):
        groups = client.get("/api/groups").json()
        if not groups:
            return
        created = client.post("/api/cards", json={
            "group_id": groups[0]["jid"],
            "headline": "Lifecycle test",
        }).json()
        resp = client.put(f"/api/cards/{created['id']}/play")
        assert resp.status_code == 200
        assert resp.json()["status"] == "in_progress"
        assert resp.json()["mode"] == "active"
        delete_card(created["id"])

    def test_move_card_to_done(self):
        groups = client.get("/api/groups").json()
        if not groups:
            return
        created = client.post("/api/cards", json={
            "group_id": groups[0]["jid"],
            "headline": "Move test",
        }).json()
        resp = client.put(f"/api/cards/{created['id']}/move", json={"target_status": "done"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "done"
        delete_card(created["id"])


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])

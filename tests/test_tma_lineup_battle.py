"""
Phase 4 API tests: lineup battles through the TMA router (SQLite, no network).
Run: python -m pytest tests/test_tma_lineup_battle.py -v
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["GENRE_RESOLVE_ON_CREATE"] = "false"  # never hit Last.fm in tests

from database import get_db, DatabaseManager  # noqa: E402
from models import Base  # noqa: E402

TG_A = {"id": 5001, "username": "alpha", "first_name": "A"}
TG_B = {"id": 5002, "username": "bravo", "first_name": "B"}
H = {"Authorization": "tma fake"}


def _card(cid, name, fam, stat=70, rarity="rare"):
    return {"card_id": cid, "name": name, "artist_name": name, "title": f"{name} song",
            "rarity": rarity, "impact": stat, "skill": stat, "longevity": stat, "culture": stat,
            "hype": stat, "genre_family": fam, "youtube_url": f"https://youtube.com/watch?v={cid}"}


@pytest.fixture
def db():
    test_db = DatabaseManager(test_database_url="sqlite:///:memory:")
    Base.metadata.create_all(test_db.engine)
    # Seed both players with 4 cards each
    a = test_db.get_or_create_telegram_user(TG_A["id"], TG_A["username"])
    b = test_db.get_or_create_telegram_user(TG_B["id"], TG_B["username"])
    for cid, name, fam, stat in [("a1", "A Hip", "HIP_HOP", 80), ("a2", "A Pop", "POP", 75),
                                 ("a3", "A Rock", "ROCK", 70), ("a4", "A Pop2", "POP", 60)]:
        assert test_db.add_card_to_master(_card(cid, name, fam, stat))
        test_db.add_card_to_collection(a["user_id"], cid)
    for cid, name, fam, stat in [("b1", "B Pop", "POP", 90), ("b2", "B Rock", "ROCK", 85),
                                 ("b3", "B Elec", "ELECTRONIC", 80), ("b4", "B Soul", "SOUL", 50)]:
        assert test_db.add_card_to_master(_card(cid, name, fam, stat))
        test_db.add_card_to_collection(b["user_id"], cid)
    test_db.register_battle_player(TG_A["id"], TG_A["username"])
    test_db.register_battle_player(TG_B["id"], TG_B["username"])
    yield test_db
    Base.metadata.drop_all(test_db.engine)


@pytest.fixture
def app(db):
    from tma.api.main import app as _app
    # Routers call get_db() directly (not via Depends), so swap the module singleton.
    import tma.api.routers.battle as battle_router
    prev = battle_router.get_db
    battle_router.get_db = lambda: db  # router calls get_db() directly, not via Depends
    _app.dependency_overrides[get_db] = lambda: db
    yield _app
    _app.dependency_overrides.clear()
    battle_router.get_db = prev


class _As:
    """TestClient bound to one Telegram identity; sets the auth override per request
    so two clients on the same app don't clobber each other."""
    def __init__(self, app, tg):
        self.app, self.tg, self.client = app, tg, TestClient(app)

    def _bind(self):
        from tma.api.auth import get_tg_user
        tg = self.tg
        self.app.dependency_overrides[get_tg_user] = lambda: tg

    def get(self, *a, **k):
        self._bind(); return self.client.get(*a, **k)

    def post(self, *a, **k):
        self._bind(); return self.client.post(*a, **k)


def _as(app, tg):
    return _As(app, tg)


def test_lineup_cards_endpoint_lists_genres_and_suggestion(app):
    r = _as(app, TG_A).get("/api/battle/lineup/cards", headers=H)
    assert r.status_code == 200, r.text
    data = r.json()
    assert {c["card_id"] for c in data["cards"]} == {"a1", "a2", "a3", "a4"}
    assert data["cards"][0]["genre_family"] == "HIP_HOP"
    assert data["rules"]["size"] == 3 and data["rules"]["max_same_family"] == 2
    assert len(data["suggested"]) == 3


def test_explicit_lineup_battle_end_to_end(app, db):
    a, b = _as(app, TG_A), _as(app, TG_B)
    r = a.post("/api/battle/challenge", headers=H, json={
        "opponent_telegram_id": TG_B["id"], "wager_tier": "casual",
        "lineup": ["a1", "a2", "a3"], "ability": "amp", "ability_slot": 0})
    assert r.status_code == 200, r.text
    bid = r.json()["battle_id"]

    r = b.post(f"/api/battle/{bid}/accept", headers=H, json={
        "lineup": ["b1", "b2", "b3"], "ability": "swap"})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert res["format"] == "lineup_bo3"
    assert res["winner"] in (0, 1, 2)
    assert 2 <= len(res["rounds"]) <= 3
    assert res["rounds"][0]["player1"]["family"] == "HIP_HOP"
    assert res["rounds"][0]["player1"]["amped"] is True
    assert res["rounds"][0]["player2"]["genre_effect"] == "countered"  # POP vs HIP_HOP
    assert res["challenger"]["lineup"][0]["card_id"] == "a1"
    assert "seed" in res

    # persisted + readable by either side
    g = a.get(f"/api/battle/{bid}", headers=H).json()
    assert g["status"] == "complete" and g["result"]["winner"] == res["winner"]

    # rewards distributed
    eco_a = db.get_user_economy(db.get_or_create_telegram_user(TG_A["id"])["user_id"])
    assert eco_a["gold"] >= res["player1"]["gold_reward"]


def test_lineup_rules_enforced_by_api(app):
    a = _as(app, TG_A)
    base = {"opponent_telegram_id": TG_B["id"], "wager_tier": "casual"}
    # wrong size
    r = a.post("/api/battle/challenge", headers=H, json={**base, "lineup": ["a1", "a2"]})
    assert r.status_code == 400 and "exactly 3" in r.text
    # unowned card
    r = a.post("/api/battle/challenge", headers=H, json={**base, "lineup": ["a1", "a2", "b1"]})
    assert r.status_code == 403
    # ability slot required for amp
    r = a.post("/api/battle/challenge", headers=H, json={**base, "lineup": ["a1", "a2", "a3"], "ability": "amp"})
    assert r.status_code == 400 and "slot" in r.text
    # unknown ability
    r = a.post("/api/battle/challenge", headers=H, json={**base, "lineup": ["a1", "a2", "a3"], "ability": "nuke"})
    assert r.status_code == 400


def test_family_cap_enforced_by_api(app, db):
    # give A a third POP card → 3×POP lineup must be rejected
    assert db.add_card_to_master(_card("a5", "A Pop3", "POP", 65))
    db.add_card_to_collection(db.get_or_create_telegram_user(TG_A["id"])["user_id"], "a5")
    r = _as(app, TG_A).post("/api/battle/challenge", headers=H, json={
        "opponent_telegram_id": TG_B["id"], "lineup": ["a2", "a4", "a5"]})
    assert r.status_code == 400 and "share a family" in r.text


def test_legacy_pack_selection_auto_builds_lineup(app):
    """Old clients that send card_id/pack_id still work: server auto-picks a legal lineup."""
    a, b = _as(app, TG_A), _as(app, TG_B)
    r = a.post("/api/battle/challenge", headers=H, json={
        "opponent_telegram_id": TG_B["id"], "card_id": "a3", "wager_tier": "standard"})
    assert r.status_code == 200, r.text
    bid = r.json()["battle_id"]
    r = b.post(f"/api/battle/{bid}/accept", headers=H, json={"card_id": "b1"})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert len(res["challenger"]["lineup"]) == 3
    assert res["challenger"]["lineup"][0]["card_id"] == "a3"  # focus card leads
    assert len(res["opponent"]["lineup"]) == 3
    assert res["wager_tier"] == "standard"


def test_scout_reveals_family_and_locks_ability(app):
    a, b = _as(app, TG_A), _as(app, TG_B)
    bid = a.post("/api/battle/challenge", headers=H, json={
        "opponent_telegram_id": TG_B["id"], "lineup": ["a1", "a2", "a3"]}).json()["battle_id"]

    # challenger can't scout own battle
    assert a.post(f"/api/battle/{bid}/scout", headers=H, json={"slot": 1}).status_code == 403

    r = b.post(f"/api/battle/{bid}/scout", headers=H, json={"slot": 1})
    assert r.status_code == 200, r.text
    assert r.json()["family"] == "POP"  # a2
    # single use
    assert b.post(f"/api/battle/{bid}/scout", headers=H, json={"slot": 2}).status_code == 400
    assert b.get(f"/api/battle/{bid}", headers=H).json()["scout"] == {"slot": 1, "family": "POP"}

    # accept: even if the client asks for amp, scout is forced (single ability per battle)
    r = b.post(f"/api/battle/{bid}/accept", headers=H, json={
        "lineup": ["b1", "b2", "b3"], "ability": "amp", "ability_slot": 0})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert res["opponent"]["ability"] == "scout" and res["opponent"]["ability_slot"] == 1
    assert res["reveals"] == [{"player": 2, "slot": 1, "family": "POP"}]
    assert not any(rd["player2"]["amped"] for rd in res["rounds"])


def test_wrong_opponent_cannot_accept(app):
    a = _as(app, TG_A)
    bid = a.post("/api/battle/challenge", headers=H, json={
        "opponent_telegram_id": TG_B["id"], "lineup": ["a1", "a2", "a3"]}).json()["battle_id"]
    r = a.post(f"/api/battle/{bid}/accept", headers=H, json={"lineup": ["a1", "a2", "a3"]})
    assert r.status_code == 400  # own challenge

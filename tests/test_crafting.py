"""
Phase 4.5 — crafting / duplicate fusion. Rules, atomic consumption, floor on
failure, boost charged-before-roll + refunded-on-failure, caps, inherit rule, API.
Run: python -m pytest tests/test_crafting.py -v
"""
import asyncio
import os
import random
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["GENRE_RESOLVE_ON_CREATE"] = "false"

from core.crafting import (  # noqa: E402
    BASE_SUCCESS_PCT, BOOST_PCT, CRAFT_CAPS, CraftError, cap_remaining, next_rarity, output_family,
    roll, success_pct, validate_inputs,
)
from database import DatabaseManager, get_db  # noqa: E402
from models import Base, CraftEvent, StarsOrder  # noqa: E402
from services import crafting_service, stars_service  # noqa: E402

TG = {"id": 8001, "username": "smith", "first_name": "Smith"}
H = {"Authorization": "tma fake"}


class FixedRng:
    def __init__(self, v): self.v = v
    def random(self): return self.v
    def randrange(self, n): return 0
    def choice(self, seq): return seq[0]


WIN, LOSE = FixedRng(0.0), FixedRng(0.999)


# ── pure rules ─────────────────────────────────────────────────────────────

def test_rarity_ladder_and_validation():
    assert next_rarity("common") == "rare" and next_rarity("legendary") == "mythic" and next_rarity("mythic") is None
    assert validate_inputs(["rare", "Rare", "RARE", "rare"]) == ("rare", "epic")
    with pytest.raises(CraftError):
        validate_inputs(["rare"] * 3)
    with pytest.raises(CraftError):
        validate_inputs(["rare", "rare", "epic", "rare"])
    with pytest.raises(CraftError):
        validate_inputs(["mythic"] * 4)


def test_success_scales_inversely_with_target_and_boost_caps():
    assert BASE_SUCCESS_PCT["rare"] > BASE_SUCCESS_PCT["epic"] > BASE_SUCCESS_PCT["legendary"] > BASE_SUCCESS_PCT["mythic"]
    for t, base in BASE_SUCCESS_PCT.items():
        assert success_pct(t) == base
        assert success_pct(t, True) == min(95, base + BOOST_PCT)


def test_inherit_rule():
    assert output_family(["POP", "pop", "POP", "POP"]) == "POP"
    assert output_family(["POP", "ROCK", "POP", "POP"]) is None
    assert output_family(["NEUTRAL"] * 4) is None


def test_roll_is_deterministic():
    assert roll(FixedRng(0.49), 50) == (True, 0.49)
    assert roll(FixedRng(0.50), 50) == (False, 0.50)


def test_caps():
    assert cap_remaining("mythic", 0) == CRAFT_CAPS["mythic"]
    assert cap_remaining("mythic", CRAFT_CAPS["mythic"] + 5) == 0


# ── fixtures ───────────────────────────────────────────────────────────────

def _card(cid, rarity, fam="POP", stat=60):
    return {"card_id": cid, "name": f"Card {cid}", "artist_name": f"Card {cid}", "title": "t", "rarity": rarity,
            "impact": stat, "skill": stat, "longevity": stat, "culture": stat, "hype": stat, "genre_family": fam}


@pytest.fixture
def db():
    d = DatabaseManager(test_database_url="sqlite:///:memory:")
    Base.metadata.create_all(d.engine)
    d.init_database()
    # master pool: rares of two families, one epic per family, legendaries
    for cid, r, f in [("r_pop1", "rare", "POP"), ("r_pop2", "rare", "POP"), ("r_rock", "rare", "ROCK"),
                      ("e_pop", "epic", "POP"), ("e_rock", "epic", "ROCK"), ("l_pop", "legendary", "POP")]:
        d.add_card_to_master(_card(cid, r, f))
    u = d.get_or_create_telegram_user(TG["id"], TG["username"])
    d.add_card_to_collection(u["user_id"], "r_pop1", quantity=3)
    d.add_card_to_collection(u["user_id"], "r_pop2", quantity=2)
    d.add_card_to_collection(u["user_id"], "r_rock", quantity=1)

    async def fake_link(title, description, payload, stars):
        return f"https://t.me/$fake_{payload}"
    stars_service.set_invoice_link_factory(fake_link)
    yield d
    stars_service.set_invoice_link_factory(None)
    Base.metadata.drop_all(d.engine)


def _uid(db):
    return db.get_or_create_telegram_user(TG["id"], TG["username"])["user_id"]


def _qty(db, uid, cid):
    return {c["card_id"]: int(c["quantity"]) for c in db.get_user_collection(uid)}.get(cid, 0)


# ── service ────────────────────────────────────────────────────────────────

def test_preview_reports_target_rate_and_inherited_family(db):
    uid = _uid(db)
    p = crafting_service.preview(db, uid, ["r_pop1", "r_pop1", "r_pop1", "r_pop2"])
    assert p["input_rarity"] == "rare" and p["target_rarity"] == "epic"
    assert p["base_pct"] == 50 and p["boosted_pct"] == 75 and p["inherited_family"] == "POP"
    p2 = crafting_service.preview(db, uid, ["r_pop1", "r_pop1", "r_pop2", "r_rock"])
    assert p2["inherited_family"] is None


def test_ownership_and_quantity_enforced(db):
    uid = _uid(db)
    with pytest.raises(CraftError):
        crafting_service.preview(db, uid, ["r_pop1", "r_pop1", "r_pop1", "r_pop1"])  # only 3 owned
    with pytest.raises(CraftError):
        crafting_service.preview(db, uid, ["r_pop1", "r_pop1", "r_pop1", "ghost"])
    with pytest.raises(CraftError):
        crafting_service.preview(db, uid, ["r_pop1", "r_pop1", "r_pop1"])


def test_successful_craft_consumes_inputs_and_grants_inherited_family(db):
    uid = _uid(db)
    res = crafting_service.craft(db, uid, ["r_pop1", "r_pop1", "r_pop1", "r_pop2"], rng=WIN, seed=1)
    assert res["success"] is True and res["target_rarity"] == "epic"
    assert res["output"]["card_id"] == "e_pop"          # POP inherited, never e_rock
    assert _qty(db, uid, "r_pop1") == 0 and _qty(db, uid, "r_pop2") == 1
    assert _qty(db, uid, "e_pop") == 1
    s = db.get_session()
    try:
        ev = s.query(CraftEvent).one()
        assert ev.success and ev.output_card_id == "e_pop" and ev.inherited_family == "POP" and ev.seed == 1
    finally:
        s.close()


def test_failed_craft_returns_one_input_as_floor(db):
    uid = _uid(db)
    before = sum(c["quantity"] for c in db.get_user_collection(uid))
    res = crafting_service.craft(db, uid, ["r_pop1", "r_pop1", "r_pop1", "r_pop2"], rng=LOSE, seed=2)
    assert res["success"] is False
    assert res["output"]["card_id"] == "r_pop1"  # floor index 0 → first input comes back
    after = sum(c["quantity"] for c in db.get_user_collection(uid))
    assert after == before - 3                      # 4 consumed, 1 returned
    assert _qty(db, uid, "r_pop1") == 1 and _qty(db, uid, "r_pop2") == 1


def test_mixed_family_success_uses_random_family_pool(db):
    uid = _uid(db)
    res = crafting_service.craft(db, uid, ["r_pop1", "r_pop1", "r_pop2", "r_rock"], rng=WIN)
    assert res["success"] and res["output"]["rarity"] == "epic" and res["inherited_family"] is None


def test_boost_charged_before_roll_and_consumed_once(db):
    uid = _uid(db)
    user = db.get_or_create_telegram_user(TG["id"], TG["username"])
    order = asyncio.run(stars_service.create_order(db, user, "craft_boost", "epic"))
    assert order["stars"] == 30
    # unpaid boost is rejected and nothing consumed
    with pytest.raises(CraftError):
        crafting_service.craft(db, uid, ["r_pop1", "r_pop1", "r_pop1", "r_pop2"], boost_order_id=order["order_id"], rng=WIN)
    assert _qty(db, uid, "r_pop1") == 3
    # pay it
    stars_service.handle_successful_payment(db, telegram_id=TG["id"], payload=order["order_id"],
                                            charge_id="chg_boost", total_amount=30, currency="XTR")
    res = crafting_service.craft(db, uid, ["r_pop1", "r_pop1", "r_pop1", "r_pop2"], boost_order_id=order["order_id"], rng=WIN)
    assert res["boosted"] and res["success_pct"] == 75
    s = db.get_session()
    try:
        assert s.query(StarsOrder).filter_by(order_id=order["order_id"]).first().consumed_at is not None
    finally:
        s.close()
    # cannot be reused
    db.add_card_to_collection(uid, "r_pop1", quantity=4)
    with pytest.raises(CraftError, match="already used"):
        crafting_service.craft(db, uid, ["r_pop1"] * 4, boost_order_id=order["order_id"], rng=WIN)


def test_boost_for_wrong_target_rejected(db):
    uid = _uid(db)
    user = db.get_or_create_telegram_user(TG["id"], TG["username"])
    order = asyncio.run(stars_service.create_order(db, user, "craft_boost", "mythic"))
    stars_service.handle_successful_payment(db, telegram_id=TG["id"], payload=order["order_id"],
                                            charge_id="chg_m", total_amount=120, currency="XTR")
    with pytest.raises(CraftError, match="different craft"):
        crafting_service.craft(db, uid, ["r_pop1", "r_pop1", "r_pop1", "r_pop2"], boost_order_id=order["order_id"], rng=WIN)


def test_transaction_failure_releases_boost_and_consumes_nothing(db, monkeypatch):
    """No epic cards in the pool → craft cannot complete → inputs untouched, boost released for refund."""
    uid = _uid(db)
    user = db.get_or_create_telegram_user(TG["id"], TG["username"])
    order = asyncio.run(stars_service.create_order(db, user, "craft_boost", "epic"))
    stars_service.handle_successful_payment(db, telegram_id=TG["id"], payload=order["order_id"],
                                            charge_id="chg_t", total_amount=30, currency="XTR")
    from models import Card
    s = db.get_session()
    try:
        for c in s.query(Card).filter(Card.rarity == "epic").all():
            s.delete(c)
        s.commit()
    finally:
        s.close()
    with pytest.raises(crafting_service.CraftTransactionError) as ei:
        crafting_service.craft(db, uid, ["r_pop1", "r_pop1", "r_pop1", "r_pop2"], boost_order_id=order["order_id"], rng=WIN)
    assert ei.value.boost_order_id == order["order_id"]
    assert _qty(db, uid, "r_pop1") == 3 and _qty(db, uid, "r_pop2") == 2   # atomic: nothing consumed
    crafting_service.release_boost(db, order["order_id"])
    s = db.get_session()
    try:
        assert s.query(StarsOrder).filter_by(order_id=order["order_id"]).first().consumed_at is None
    finally:
        s.close()


def test_cap_blocks_when_allocation_exhausted(db, monkeypatch):
    uid = _uid(db)
    monkeypatch.setitem(CRAFT_CAPS, "epic", 0)
    with pytest.raises(CraftError, match="exhausted"):
        crafting_service.craft(db, uid, ["r_pop1", "r_pop1", "r_pop1", "r_pop2"], rng=WIN)
    assert _qty(db, uid, "r_pop1") == 3


def test_seeded_craft_is_replayable(db):
    uid = _uid(db)
    db.add_card_to_collection(uid, "r_pop1", quantity=5)
    a = crafting_service.craft(db, uid, ["r_pop1"] * 4, seed=777)
    b = crafting_service.craft(db, uid, ["r_pop1"] * 4, seed=777)
    assert a["success"] == b["success"] and a["output"]["card_id"] == b["output"]["card_id"]


# ── API ────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(db):
    import tma.api.routers.craft as craft_router
    from tma.api.auth import get_tg_user
    from tma.api.main import app
    prev = craft_router.get_db
    craft_router.get_db = lambda: db
    app.dependency_overrides[get_tg_user] = lambda: TG
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()
    craft_router.get_db = prev


def test_api_options_preview_craft_history(client):
    r = client.get("/api/craft/options", headers=H)
    assert r.status_code == 200
    d = r.json()
    assert d["inputs_required"] == 4 and d["targets"]["mythic"]["boost_stars"] == 120
    assert {g["card_id"] for g in d["groups"]["rare"]} == {"r_pop1", "r_pop2", "r_rock"}

    r = client.post("/api/craft/preview", headers=H, json={"card_ids": ["r_pop1", "r_pop1", "r_pop1", "r_pop2"]})
    assert r.status_code == 200 and r.json()["target_rarity"] == "epic"
    assert client.post("/api/craft/preview", headers=H, json={"card_ids": ["r_pop1", "r_pop1"]}).status_code == 400

    r = client.post("/api/craft", headers=H, json={"card_ids": ["r_pop1", "r_pop1", "r_pop1", "r_pop2"]})
    assert r.status_code == 200, r.text
    assert r.json()["target_rarity"] == "epic" and "output" in r.json()

    h = client.get("/api/craft/history", headers=H).json()["events"]
    assert len(h) == 1 and h[0]["target_rarity"] == "epic"

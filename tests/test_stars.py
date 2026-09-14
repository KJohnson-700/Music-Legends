"""
Phase 5 — Telegram Stars: economics, order lifecycle, webhook idempotency, API.
No network: the invoice-link factory is stubbed and the bot is never touched.
Run: python -m pytest tests/test_stars.py -v
"""
import asyncio
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["GENRE_RESOLVE_ON_CREATE"] = "false"

from core.payments.stars import (  # noqa: E402
    NET_CENTS_PER_100_STARS, gross_cents_from_stars, net_cents_from_stars, quote, split_net, stars_for_usd_cents,
)
from database import DatabaseManager, get_db  # noqa: E402
from models import Base, RevenueEvent, StarsOrder  # noqa: E402
from services import stars_service  # noqa: E402

TG = {"id": 7001, "username": "buyer", "first_name": "Buyer"}
H = {"Authorization": "tma fake"}


# ── economics ──────────────────────────────────────────────────────────────

def test_price_parity_usd_to_stars():
    assert stars_for_usd_cents(299) == 150
    assert stars_for_usd_cents(499) == 250
    assert stars_for_usd_cents(999) == 500
    assert stars_for_usd_cents(1) == 5          # floor at a friendly minimum
    assert stars_for_usd_cents(10 ** 9) == 10_000  # Telegram ceiling


def test_plan_numbers_1000_stars():
    """Plan: 1,000 Stars costs the user ~$20 and nets the developer ~$13.30."""
    assert gross_cents_from_stars(1000) == 2000
    assert net_cents_from_stars(1000) == 1330


def test_host_share_is_on_net_not_gross():
    q = quote(1000, host_share_bps=1000)  # 10%
    assert q["net_cents"] == 1330
    assert q["host_cents"] == 133           # 10% of net
    assert q["platform_cents"] == 1197
    assert q["host_cents"] + q["platform_cents"] == q["net_cents"]
    assert q["host_cents"] < 200            # would be 200 on gross — the plan's explicit warning


def test_split_bounds():
    assert split_net(1000, None) == {"net_cents": 1000, "host_cents": 0, "platform_cents": 1000}
    assert split_net(1000, 20_000)["host_cents"] == 1000  # capped at 100%
    assert split_net(-5, 500)["net_cents"] == 0


# ── fixtures ───────────────────────────────────────────────────────────────

def _card(cid, rarity, stat=60):
    return {"card_id": cid, "name": f"Card {cid}", "artist_name": f"Card {cid}", "title": "t", "rarity": rarity,
            "impact": stat, "skill": stat, "longevity": stat, "culture": stat, "hype": stat, "genre_family": "POP"}


@pytest.fixture
def db():
    d = DatabaseManager(test_database_url="sqlite:///:memory:")
    Base.metadata.create_all(d.engine)
    d.init_database()
    # master cards so tier packs can be generated
    for i, r in enumerate(["common"] * 6 + ["rare"] * 4 + ["epic"] * 3 + ["legendary"] * 2 + ["mythic"]):
        d.add_card_to_master(_card(f"m{i}", r))
    # a public creator pack
    from models import CreatorPacks
    s = d.get_session()
    try:
        creator = d.get_or_create_telegram_user(7999, "creator")
        s.add(CreatorPacks(pack_id="cp1", name="Creator Pack One", creator_id=creator["user_id"], price=800,
                           card_count=5, cards_data=[_card(f"c{i}", "rare") for i in range(5)],
                           pack_tier="gold", is_public=True))
        s.commit()
    finally:
        s.close()
    # a host with 20% share, and the buyer referred by it
    d.get_or_create_telegram_user(TG["id"], TG["username"])
    from models import TelegramHost, User
    s = d.get_session()
    try:
        s.add(TelegramHost(host_token="host_abc", owner_telegram_id=4242, share_bps=2000, label="Test Host"))
        u = s.query(User).filter_by(user_id=str(9_000_000_000 + TG["id"])).first()
        u.referrer_host_token = "host_abc"
        s.commit()
    finally:
        s.close()
    links = []

    async def fake_link(title, description, payload, stars):
        links.append((title, payload, stars))
        return f"https://t.me/$fake_{payload}"
    stars_service.set_invoice_link_factory(fake_link)
    d._test_links = links
    yield d
    stars_service.set_invoice_link_factory(None)
    Base.metadata.drop_all(d.engine)


@pytest.fixture
def client(db):
    import tma.api.routers.stars as stars_router
    from tma.api.auth import get_tg_user
    from tma.api.main import app
    prev = stars_router.get_db
    stars_router.get_db = lambda: db
    app.dependency_overrides[get_tg_user] = lambda: TG
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()
    stars_router.get_db = prev


def _buyer(db):
    return db.get_or_create_telegram_user(TG["id"], TG["username"])


# ── order lifecycle ────────────────────────────────────────────────────────

def test_catalog_prices(db):
    cat = stars_service.catalog(db)
    assert cat["currency"] == "XTR"
    assert cat["tiers"]["community"]["stars"] == 150
    assert cat["tiers"]["gold"]["stars"] == 250
    assert cat["packs"]["cp1"] == 250  # gold-tier creator pack → gold list price


def test_tier_pack_end_to_end_with_host_share_on_net(db):
    user = _buyer(db)
    order = asyncio.run(stars_service.create_order(db, user, "tier_pack", "community"))
    assert order["stars"] == 150 and order["invoice_link"].endswith(order["order_id"])
    assert db._test_links[-1][2] == 150

    ok, msg = stars_service.validate_pre_checkout(db, order["order_id"], 150, "XTR")
    assert ok, msg

    gold_before = db.get_user_economy(user["user_id"])["gold"]
    res = stars_service.handle_successful_payment(
        db, telegram_id=TG["id"], payload=order["order_id"], charge_id="chg_1", total_amount=150, currency="XTR")
    assert res["status"] == "fulfilled"
    assert len(res["cards"]) == 5
    assert db.get_user_economy(user["user_id"])["gold"] == gold_before + 100  # community bonus gold
    assert {c["card_id"] for c in db.get_user_collection(user["user_id"])} >= {c["card_id"] for c in res["cards"]}

    s = db.get_session()
    try:
        o = s.query(StarsOrder).filter_by(order_id=order["order_id"]).first()
        assert o.status == "fulfilled" and o.telegram_payment_charge_id == "chg_1"
        ev = s.query(RevenueEvent).filter_by(stripe_session_id="stars:chg_1").first()
        assert ev.rail == "stars" and ev.gross_stars == 150
        assert ev.net_cents == net_cents_from_stars(150) == 199
        assert ev.host_cents == 199 * 2000 // 10_000 == 39     # 20% of NET
        assert ev.platform_cents == 160
        assert ev.host_token == "host_abc"
    finally:
        s.close()


def test_successful_payment_is_idempotent(db):
    user = _buyer(db)
    order = asyncio.run(stars_service.create_order(db, user, "tier_pack", "gold"))
    first = stars_service.handle_successful_payment(
        db, telegram_id=TG["id"], payload=order["order_id"], charge_id="chg_dup", total_amount=250, currency="XTR")
    n_before = len(db.get_user_collection(user["user_id"]))
    gold_before = db.get_user_economy(user["user_id"])["gold"]
    again = stars_service.handle_successful_payment(
        db, telegram_id=TG["id"], payload=order["order_id"], charge_id="chg_dup", total_amount=250, currency="XTR")
    assert first["status"] == "fulfilled" and again.get("duplicate") is True
    assert len(db.get_user_collection(user["user_id"])) == n_before
    assert db.get_user_economy(user["user_id"])["gold"] == gold_before
    s = db.get_session()
    try:
        assert s.query(RevenueEvent).filter_by(stripe_session_id="stars:chg_dup").count() == 1
    finally:
        s.close()


def test_creator_pack_creates_unopened_pack_purchase(db):
    user = _buyer(db)
    order = asyncio.run(stars_service.create_order(db, user, "creator_pack", "cp1"))
    assert order["stars"] == 250
    res = stars_service.handle_successful_payment(
        db, telegram_id=TG["id"], payload=order["order_id"], charge_id="chg_cp", total_amount=250, currency="XTR")
    assert res["status"] == "fulfilled" and res["cards"] == []
    owned = db.get_user_purchased_packs(user["user_id"])
    assert any(str(p.get("pack_id")) == "cp1" for p in owned)


def test_pre_checkout_rejections(db):
    user = _buyer(db)
    order = asyncio.run(stars_service.create_order(db, user, "tier_pack", "community"))
    assert stars_service.validate_pre_checkout(db, "nope", 150, "XTR")[0] is False
    assert stars_service.validate_pre_checkout(db, order["order_id"], 149, "XTR")[0] is False
    assert stars_service.validate_pre_checkout(db, order["order_id"], 150, "USD")[0] is False
    stars_service.handle_successful_payment(
        db, telegram_id=TG["id"], payload=order["order_id"], charge_id="chg_x", total_amount=150, currency="XTR")
    ok, msg = stars_service.validate_pre_checkout(db, order["order_id"], 150, "XTR")
    assert not ok and "already" in msg


def test_unknown_order_payment_is_recorded_not_dropped(db):
    res = stars_service.handle_successful_payment(
        db, telegram_id=TG["id"], payload="so_missing", charge_id="chg_ghost", total_amount=150, currency="XTR")
    assert res["status"] == "unknown_order"


def test_invalid_products(db):
    user = _buyer(db)
    with pytest.raises(ValueError):
        asyncio.run(stars_service.create_order(db, user, "tier_pack", "diamond"))
    with pytest.raises(ValueError):
        asyncio.run(stars_service.create_order(db, user, "creator_pack", "nope"))
    with pytest.raises(ValueError):
        asyncio.run(stars_service.create_order(db, user, "subscription", "x"))


# ── API ────────────────────────────────────────────────────────────────────

def test_api_catalog_invoice_and_order_status(client, db):
    r = client.get("/api/stars/catalog", headers=H)
    assert r.status_code == 200 and r.json()["tiers"]["community"]["stars"] == 150

    r = client.post("/api/stars/invoice", headers=H, json={"product_type": "tier_pack", "ref": "community"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stars"] == 150 and body["invoice_link"].startswith("https://t.me/")

    r = client.get(f"/api/stars/orders/{body['order_id']}", headers=H)
    assert r.status_code == 200 and r.json()["status"] == "pending"

    # webhook fulfils, then the client sees cards
    stars_service.handle_successful_payment(
        db, telegram_id=TG["id"], payload=body["order_id"], charge_id="chg_api", total_amount=150, currency="XTR")
    r = client.get(f"/api/stars/orders/{body['order_id']}", headers=H)
    assert r.json()["status"] == "fulfilled" and len(r.json()["cards"]) == 5

    assert client.get("/api/stars/orders", headers=H).json()["orders"][0]["order_id"] == body["order_id"]
    assert client.post("/api/stars/invoice", headers=H, json={"product_type": "tier_pack", "ref": "diamond"}).status_code == 400
    assert client.get("/api/stars/orders/so_nope", headers=H).status_code == 404


def test_api_refund_requires_admin_key(client):
    assert client.post("/api/stars/orders/so_x/refund").status_code == 401


def test_stripe_checkout_is_gone_from_mini_app(client):
    assert client.post("/api/checkout/tier-pack", headers=H, json={"tier": "gold"}).status_code in (404, 405)

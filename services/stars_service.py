"""
Telegram Stars purchases for the Mini App (Phase 5).

Flow
----
1. Mini App → POST /api/stars/invoice           → StarsOrder(pending) + invoice link
2. Mini App → WebApp.openInvoice(link)          → user pays inside Telegram
3. Telegram → bot webhook: pre_checkout_query   → we confirm the order is still valid
4. Telegram → bot webhook: successful_payment   → fulfil, ledger (host share on NET), mark fulfilled
5. Mini App → GET /api/stars/orders/{id}        → shows cards / unopened pack

Idempotent on telegram_payment_charge_id and on order status, so a replayed
webhook can never double-grant.
"""
import json
import logging
import os
import secrets
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from core.payments.stars import quote, stars_for_usd_cents
from services.pack_fulfillment import fulfill_creator_pack, fulfill_tier_pack

logger = logging.getLogger(__name__)

PRODUCT_TIER = "tier_pack"
PRODUCT_CREATOR = "creator_pack"
PRODUCT_CRAFT_BOOST = "craft_boost"  # Phase 4.5: +25% on one craft
TIER_LABELS = {"community": "Community Pack", "gold": "Gold Pack", "platinum": "Platinum Pack"}

# Test seam: (title, description, payload, stars) -> invoice url
InvoiceLinkFactory = Callable[[str, str, str, int], Awaitable[str]]
_invoice_link_factory: Optional[InvoiceLinkFactory] = None


def set_invoice_link_factory(factory: Optional[InvoiceLinkFactory]) -> None:
    global _invoice_link_factory
    _invoice_link_factory = factory


async def _default_invoice_link(title: str, description: str, payload: str, stars: int) -> str:
    from telegram import LabeledPrice
    from tma.api.bot.handlers import get_bot

    bot = get_bot()
    if bot is None:
        raise RuntimeError("Telegram bot is not initialised (TELEGRAM_BOT_TOKEN missing)")
    return await bot.create_invoice_link(
        title=title[:32],
        description=description[:255],
        payload=payload,
        provider_token="",          # empty for Stars
        currency="XTR",
        prices=[LabeledPrice(label=title[:32], amount=int(stars))],
    )


# ── pricing ────────────────────────────────────────────────────────────────

def tier_price_stars(tier: str) -> Optional[int]:
    from config.economy import PACK_PRICING
    p = PACK_PRICING.get((tier or "").lower())
    if not p or not p.get("buy_usd_cents"):
        return None
    return stars_for_usd_cents(int(p["buy_usd_cents"]))


def creator_pack_price_stars(pack) -> int:
    """Creator packs carry a gold price; Stars price derives from the tier's USD list price."""
    from config.economy import PACK_PRICING
    tier_key = (getattr(pack, "pack_tier", None) or "community").strip().lower()
    cents = int((PACK_PRICING.get(tier_key) or PACK_PRICING["community"]).get("buy_usd_cents") or 299)
    return stars_for_usd_cents(cents)


def catalog(db) -> Dict[str, Any]:
    from models import CreatorPacks
    tiers = {}
    for tier in ("community", "gold", "platinum"):
        s = tier_price_stars(tier)
        if s:
            from config.economy import PACK_PRICING
            tiers[tier] = {"stars": s, "label": TIER_LABELS[tier],
                           "description": PACK_PRICING[tier].get("description", ""),
                           "cards": int(PACK_PRICING[tier].get("cards_per_pack", 5))}
    packs: Dict[str, int] = {}
    session = db.get_session()
    try:
        for p in session.query(CreatorPacks).filter(CreatorPacks.is_public.is_(True)).all():
            packs[p.pack_id] = creator_pack_price_stars(p)
    finally:
        session.close()
    from core.crafting import BOOST_STARS
    return {"currency": "XTR", "tiers": tiers, "packs": packs, "craft_boosts": dict(BOOST_STARS)}


# ── host attribution ───────────────────────────────────────────────────────

def host_context(db, user_id: str) -> Tuple[Optional[str], int]:
    from models import User
    session = db.get_session()
    try:
        user = session.query(User).filter_by(user_id=str(user_id)).first()
        token = (getattr(user, "referrer_host_token", None) or "").strip() if user else ""
    finally:
        session.close()
    if not token:
        return None, 0
    host = db.get_telegram_host_by_token(token)
    if host:
        return token, int(host.get("share_bps") or 0)
    return token, int(os.getenv("DEFAULT_HOST_SHARE_BPS", "1000"))


# ── orders ─────────────────────────────────────────────────────────────────

async def create_order(db, user: Dict[str, Any], product_type: str, ref: str) -> Dict[str, Any]:
    """Create a pending order and its Telegram invoice link."""
    from models import CreatorPacks, StarsOrder

    uid = str(user["user_id"])
    tg_id = int(user.get("telegram_id") or 0)
    ref = (ref or "").strip()

    if product_type == PRODUCT_TIER:
        tier = ref.lower()
        stars = tier_price_stars(tier)
        if not stars:
            raise ValueError(f"Tier {ref!r} is not available for Stars checkout")
        title = TIER_LABELS[tier]
        from config.economy import PACK_PRICING
        description = PACK_PRICING[tier].get("description") or f"{title} — 5 cards"
        usd_cents = int(PACK_PRICING[tier]["buy_usd_cents"])
        product_ref = tier
    elif product_type == PRODUCT_CREATOR:
        session = db.get_session()
        try:
            pack = session.query(CreatorPacks).filter_by(pack_id=ref).first()
            if not pack:
                raise ValueError("Pack not found")
            if not pack.is_public and (pack.card_count or 0) <= 0:
                raise ValueError("Pack is not available for purchase")
            title = pack.name or ref
            description = f"{title} — {int(pack.card_count or 0)} cards"
            stars = creator_pack_price_stars(pack)
            from config.economy import PACK_PRICING
            tier_key = (pack.pack_tier or "community").strip().lower()
            usd_cents = int((PACK_PRICING.get(tier_key) or PACK_PRICING["community"]).get("buy_usd_cents") or 299)
            product_ref = ref
        finally:
            session.close()
    elif product_type == PRODUCT_CRAFT_BOOST:
        from core.crafting import BOOST_PCT, BOOST_STARS, normalize_rarity
        target = normalize_rarity(ref)
        if target not in BOOST_STARS:
            raise ValueError("Boost target must be rare, epic, legendary or mythic")
        stars = BOOST_STARS[target]
        title = f"Craft Boost ({target.title()})"
        description = f"+{BOOST_PCT}% success on one {target} craft"
        usd_cents = stars * 2
        product_ref = target
    else:
        raise ValueError("product_type must be tier_pack, creator_pack or craft_boost")

    host_token, host_bps = host_context(db, uid)
    order_id = "so_" + secrets.token_hex(8)
    factory = _invoice_link_factory or _default_invoice_link
    link = await factory(title, description, order_id, stars)

    session = db.get_session()
    try:
        session.add(StarsOrder(
            order_id=order_id, user_id=uid, telegram_id=tg_id,
            product_type=product_type, product_ref=product_ref, title=title,
            stars_amount=int(stars), usd_cents_ref=usd_cents,
            host_token=host_token, host_share_bps=int(host_bps),
            status="pending", invoice_link=link, created_at=datetime.utcnow(),
        ))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    q = quote(stars, host_bps)
    logger.info(f"[STARS] order {order_id} {product_type}:{product_ref} {stars}⭐ user={uid} host={host_token}")
    return {"order_id": order_id, "invoice_link": link, "stars": int(stars), "title": title,
            "product_type": product_type, "ref": product_ref, "net_cents": q["net_cents"]}


def get_order(db, order_id: str) -> Optional[Dict[str, Any]]:
    from models import StarsOrder
    session = db.get_session()
    try:
        o = session.query(StarsOrder).filter_by(order_id=order_id).first()
        return _order_dict(o) if o else None
    finally:
        session.close()


def list_orders(db, user_id: str, limit: int = 20):
    from models import StarsOrder
    session = db.get_session()
    try:
        rows = (session.query(StarsOrder).filter_by(user_id=str(user_id))
                .order_by(StarsOrder.created_at.desc()).limit(limit).all())
        return [_order_dict(o) for o in rows]
    finally:
        session.close()


def _order_dict(o) -> Dict[str, Any]:
    return {
        "order_id": o.order_id, "user_id": o.user_id, "product_type": o.product_type,
        "ref": o.product_ref, "title": o.title, "stars": int(o.stars_amount or 0),
        "status": o.status, "invoice_link": o.invoice_link,
        "charge_id": o.telegram_payment_charge_id,
        "cards": json.loads(o.cards_json) if o.cards_json else [],
        "result": json.loads(o.result_json) if getattr(o, "result_json", None) else None,
        "error": o.error,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "paid_at": o.paid_at.isoformat() if o.paid_at else None,
        "fulfilled_at": o.fulfilled_at.isoformat() if o.fulfilled_at else None,
    }


# ── webhook side ───────────────────────────────────────────────────────────

def validate_pre_checkout(db, payload: str, total_amount: int, currency: str) -> Tuple[bool, str]:
    """Answer for pre_checkout_query. Must be fast (<10s) and never raise."""
    from models import StarsOrder
    try:
        if currency != "XTR":
            return False, "Only Telegram Stars are accepted"
        session = db.get_session()
        try:
            o = session.query(StarsOrder).filter_by(order_id=payload).first()
        finally:
            session.close()
        if not o:
            return False, "Order not found. Please start the purchase again."
        if o.status not in ("pending",):
            return False, f"Order already {o.status}"
        if int(total_amount) != int(o.stars_amount or 0):
            return False, "Price changed. Please start the purchase again."
        return True, ""
    except Exception as e:
        logger.error(f"[STARS] pre_checkout error: {e}")
        return False, "Temporary error, please try again"


def handle_successful_payment(db, *, telegram_id: int, payload: str, charge_id: str,
                              total_amount: int, currency: str,
                              provider_charge_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Fulfil a paid order. Idempotent: a second call with the same charge_id (or an
    already-fulfilled order) returns the stored result without granting again.
    """
    from models import StarsOrder

    session = db.get_session()
    try:
        o = session.query(StarsOrder).filter_by(order_id=payload).first()
        if not o:
            # Paid but unknown order: record for manual refund, never silently drop money.
            logger.error(f"[STARS] payment for unknown order payload={payload} charge={charge_id}")
            return {"status": "unknown_order", "charge_id": charge_id}
        if o.telegram_payment_charge_id and o.telegram_payment_charge_id != charge_id:
            logger.warning(f"[STARS] order {payload} already paid with another charge")
            return {"status": o.status, "order_id": payload, "duplicate": True}
        if o.status in ("fulfilled", "paid") and o.telegram_payment_charge_id == charge_id:
            return {"status": o.status, "order_id": payload, "duplicate": True,
                    "cards": json.loads(o.cards_json) if o.cards_json else []}
        # claim the order
        o.status = "paid"
        o.telegram_payment_charge_id = charge_id
        o.provider_payment_charge_id = provider_charge_id
        o.paid_at = datetime.utcnow()
        if int(total_amount) != int(o.stars_amount or 0) or currency != "XTR":
            o.error = f"amount mismatch: got {total_amount} {currency}, expected {o.stars_amount} XTR"
        session.commit()
        buyer_id, product_type, ref, stars, host_token, host_bps = (
            o.user_id, o.product_type, o.product_ref, int(o.stars_amount or 0),
            o.host_token, int(o.host_share_bps or 0))
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    q = quote(stars, host_bps)
    ext = f"stars:{charge_id}"
    try:
        if product_type == PRODUCT_TIER:
            result = fulfill_tier_pack(db, buyer_id, ref, payment_method="stars",
                                       external_id=ext, amount_cents=q["net_cents"])
        elif product_type == PRODUCT_CRAFT_BOOST:
            # Nothing to deliver: the fulfilled order itself is the credit, spent by crafting_service.
            result = {"product_type": PRODUCT_CRAFT_BOOST, "boost_target": ref, "cards": []}
        else:
            result = fulfill_creator_pack(db, buyer_id, ref, payment_method="stars",
                                          external_id=ext, amount_cents=q["net_cents"])
    except Exception as e:
        logger.error(f"[STARS] fulfilment FAILED for {payload}: {e}")
        _set_status(db, payload, "failed", error=str(e))
        return {"status": "failed", "order_id": payload, "error": str(e)}

    # Ledger: host share on NET (Phase 5 rule).
    try:
        db.record_revenue_event(
            stripe_session_id=ext, user_id=buyer_id, product_type=f"{product_type}_stars",
            gross_cents=q["gross_cents"], platform_cents=q["platform_cents"],
            host_cents=q["host_cents"], creator_cents=0, host_token=host_token,
            rail="stars", gross_stars=stars, net_cents=q["net_cents"],
        )
    except TypeError:
        # older record_revenue_event signature (no rail/net columns)
        db.record_revenue_event(ext, buyer_id, f"{product_type}_stars", q["gross_cents"],
                                q["platform_cents"], q["host_cents"], 0, host_token)
    except Exception as e:
        logger.error(f"[STARS] ledger write failed for {payload}: {e}")

    _set_status(db, payload, "fulfilled", cards=result.get("cards", []), result=result)
    logger.info(f"[STARS] fulfilled {payload}: {product_type}:{ref} {stars}⭐ net={q['net_cents']}c host={q['host_cents']}c")
    return {"status": "fulfilled", "order_id": payload, "cards": result.get("cards", []), "result": result,
            "quote": q}


def _set_status(db, order_id: str, status: str, *, error: Optional[str] = None,
                cards: Optional[list] = None, result: Optional[dict] = None) -> None:
    from models import StarsOrder
    session = db.get_session()
    try:
        o = session.query(StarsOrder).filter_by(order_id=order_id).first()
        if not o:
            return
        o.status = status
        if error:
            o.error = error[:500]
        if cards is not None:
            o.cards_json = json.dumps(cards, default=str)
        if result is not None:
            o.result_json = json.dumps({k: v for k, v in result.items() if k != "cards"}, default=str)
        if status == "fulfilled":
            o.fulfilled_at = datetime.utcnow()
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"[STARS] status update failed for {order_id}: {e}")
    finally:
        session.close()


async def refund_order(db, order_id: str) -> Dict[str, Any]:
    """Admin: refund a paid/fulfilled/failed order via refundStarPayment. Cards are NOT clawed back."""
    from models import StarsOrder
    from tma.api.bot.handlers import get_bot
    bot = get_bot()
    if bot is None:
        raise RuntimeError("Telegram bot is not initialised")
    session = db.get_session()
    try:
        o = session.query(StarsOrder).filter_by(order_id=order_id).first()
        if not o or not o.telegram_payment_charge_id:
            raise ValueError("Order has no Telegram charge to refund")
        tg_id, charge = int(o.telegram_id), o.telegram_payment_charge_id
    finally:
        session.close()
    ok = await bot.refund_star_payment(user_id=tg_id, telegram_payment_charge_id=charge)
    if ok:
        _set_status(db, order_id, "refunded")
    return {"order_id": order_id, "refunded": bool(ok)}

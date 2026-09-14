"""
Rail-agnostic pack fulfillment (Phase 5).

Both payment rails end here once money has cleared:
  * Telegram Stars (Mini App) → services/stars_service.py
  * Stripe (Discord)          → webhooks/stripe_hook.py (untouched; Discord is bugfix-only)

Tier packs grant cards immediately (there is no creator pack row to "open").
Creator packs create an unopened PackPurchase so the player gets the normal
pack-opening reveal in My Packs — same as buying with gold.
"""
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _record_purchase_row(db, buyer_id: str, pack_ref: str, amount_cents: int,
                         payment_method: str, external_id: str) -> str:
    """Audit row in the legacy `purchases` table (same shape the Stripe hook writes)."""
    purchase_id = str(uuid.uuid4())
    try:
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO purchases
                   (purchase_id, user_id, pack_id, amount_cents, payment_method, stripe_session_id, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'completed')""",
                (purchase_id, str(buyer_id), pack_ref, int(amount_cents), payment_method, external_id),
            )
            conn.commit()
    except Exception as e:  # audit only — never fail fulfillment over it
        logger.warning(f"[FULFILL] purchases audit insert skipped: {e}")
    return purchase_id


def fulfill_tier_pack(db, buyer_id: str, tier: str, *, payment_method: str,
                      external_id: str, amount_cents: int = 0) -> Dict[str, Any]:
    """Generate + grant a built-in tier pack (community/gold/platinum) and its bonuses."""
    from config.economy import PACK_PRICING

    tier = (tier or "").strip().lower()
    if tier not in PACK_PRICING:
        raise ValueError(f"Unknown tier {tier!r}")

    cards = db.generate_tier_pack_cards(str(buyer_id), tier)
    if not cards:
        raise RuntimeError("Card generation failed — no cards available in master table")

    pricing = PACK_PRICING[tier]
    bonus_gold = int(pricing.get("bonus_gold", 0) or 0)
    bonus_tickets = int(pricing.get("bonus_tickets", 0) or 0)
    if bonus_gold or bonus_tickets:
        db.update_user_economy(str(buyer_id), gold_change=bonus_gold, tickets_change=bonus_tickets)

    purchase_id = _record_purchase_row(db, buyer_id, f"tier_{tier}", amount_cents, payment_method, external_id)
    logger.info(f"[FULFILL] tier={tier} buyer={buyer_id} cards={len(cards)} via {payment_method}")
    return {
        "product_type": "tier_pack", "tier": tier, "purchase_id": purchase_id,
        "cards": cards, "bonus_gold": bonus_gold, "bonus_tickets": bonus_tickets,
    }


def fulfill_creator_pack(db, buyer_id: str, pack_id: str, *, payment_method: str,
                         external_id: str, amount_cents: int = 0) -> Dict[str, Any]:
    """Grant an unopened creator pack (opened later in My Packs, like a gold purchase)."""
    from datetime import datetime
    from models import CreatorPacks, PackPurchase

    session = db.get_session()
    try:
        pack = session.query(CreatorPacks).filter_by(pack_id=pack_id).first()
        if not pack:
            raise ValueError("Pack not found")
        purchase = PackPurchase(buyer_id=str(buyer_id), pack_id=pack_id,
                                purchased_at=datetime.utcnow(), cards_received=None)
        session.add(purchase)
        try:
            pack.total_purchases = (getattr(pack, "total_purchases", 0) or 0) + 1
        except Exception:
            pass
        session.commit()
        pack_purchase_id = str(purchase.purchase_id)
        name = pack.name or pack_id
        card_count = int(pack.card_count or 0)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    purchase_id = _record_purchase_row(db, buyer_id, pack_id, amount_cents, payment_method, external_id)
    logger.info(f"[FULFILL] creator pack={pack_id} buyer={buyer_id} via {payment_method}")
    return {
        "product_type": "creator_pack", "pack_id": pack_id, "pack_name": name,
        "card_count": card_count, "purchase_id": purchase_id, "pack_purchase_id": pack_purchase_id,
        "cards": [],  # revealed on open
    }

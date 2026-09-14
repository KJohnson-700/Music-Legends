"""
Crafting service (Phase 4.5): validates, consumes inputs atomically, rolls,
grants the result, and records a craft_events row. Stars boosts are Stars
orders of product_type 'craft_boost' (see stars_service) consumed here.

All money-relevant decisions are server-side; the client only sends card ids
and an optional boost order id.
"""
import json
import logging
import random
import secrets
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from core.crafting import (
    BOOST_PCT, BOOST_STARS, CRAFT_CAPS, INPUTS_REQUIRED, RARITY_ORDER, CraftError,
    cap_remaining, input_counts, output_family, pick_floor_index, roll, success_pct,
    validate_inputs,
)

logger = logging.getLogger(__name__)

PRODUCT_CRAFT_BOOST = "craft_boost"


class CraftTransactionError(RuntimeError):
    """The craft could not be completed AFTER a boost was consumed; caller must refund."""
    def __init__(self, message: str, boost_order_id: Optional[str]):
        super().__init__(message)
        self.boost_order_id = boost_order_id


# ── read side ──────────────────────────────────────────────────────────────

def crafted_count(db, target_rarity: str) -> int:
    from models import CraftEvent
    session = db.get_session()
    try:
        return int(session.query(CraftEvent)
                   .filter(CraftEvent.target_rarity == target_rarity, CraftEvent.success.is_(True))
                   .count())
    finally:
        session.close()


def options(db) -> Dict[str, Any]:
    targets = {}
    for r in RARITY_ORDER[1:]:
        targets[r] = {
            "base_pct": success_pct(r), "boosted_pct": success_pct(r, True),
            "boost_stars": BOOST_STARS[r], "cap": CRAFT_CAPS[r],
            "remaining": cap_remaining(r, crafted_count(db, r)),
        }
    return {"inputs_required": INPUTS_REQUIRED, "boost_pct": BOOST_PCT, "rarity_order": list(RARITY_ORDER),
            "targets": targets, "genre_rule": "inherit"}


def _owned_map(db, user_id: str) -> Dict[str, Dict[str, Any]]:
    from cards_config import compute_card_power
    out: Dict[str, Dict[str, Any]] = {}
    for c in db.get_user_collection(user_id) or []:
        cid = str(c.get("card_id") or "")
        if not cid:
            continue
        c = dict(c)
        c["power"] = compute_card_power(c)
        out[cid] = c
    return out


def _resolve_inputs(owned: Dict[str, Dict[str, Any]], card_ids: Sequence[str]) -> List[Dict[str, Any]]:
    counts = input_counts(card_ids)
    for cid, n in counts.items():
        card = owned.get(cid)
        if not card:
            raise CraftError(f"You don't own card {cid}")
        if int(card.get("quantity") or 1) < n:
            raise CraftError(f"You need {n} copies of {card.get('name') or cid} (you have {card.get('quantity') or 1})")
    return [owned[str(cid)] for cid in card_ids]


def preview(db, user_id: str, card_ids: Sequence[str]) -> Dict[str, Any]:
    owned = _owned_map(db, user_id)
    cards = _resolve_inputs(owned, card_ids)
    input_rarity, target = validate_inputs([c.get("rarity") for c in cards])
    fam = output_family([c.get("genre_family") for c in cards])
    remaining = cap_remaining(target, crafted_count(db, target))
    return {
        "input_rarity": input_rarity, "target_rarity": target,
        "base_pct": success_pct(target), "boosted_pct": success_pct(target, True),
        "boost_stars": BOOST_STARS[target], "boost_pct": BOOST_PCT,
        "inherited_family": fam, "cap_remaining": remaining,
        "inputs": [{"card_id": c["card_id"], "name": c.get("name"), "rarity": c.get("rarity"),
                    "genre_family": c.get("genre_family"), "power": c.get("power"), "image_url": c.get("image_url")}
                   for c in cards],
    }


def craftable_groups(db, user_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """Cards grouped by rarity with quantities, for the picker (mythic excluded — nothing above it)."""
    owned = _owned_map(db, user_id)
    groups: Dict[str, List[Dict[str, Any]]] = {r: [] for r in RARITY_ORDER[:-1]}
    for c in owned.values():
        r = (c.get("rarity") or "common").lower()
        if r in groups:
            groups[r].append({"card_id": c["card_id"], "name": c.get("name"), "rarity": r,
                              "quantity": int(c.get("quantity") or 1), "genre_family": c.get("genre_family"),
                              "power": c.get("power"), "image_url": c.get("image_url")})
    for r in groups:
        groups[r].sort(key=lambda x: (-x["quantity"], -(x["power"] or 0)))
    return groups


# ── boost orders ───────────────────────────────────────────────────────────

def _consume_boost(db, user_id: str, boost_order_id: str, target: str) -> None:
    """Mark a fulfilled craft_boost order as consumed (charged before the roll)."""
    from models import StarsOrder
    session = db.get_session()
    try:
        o = session.query(StarsOrder).filter_by(order_id=boost_order_id).first()
        if not o or str(o.user_id) != str(user_id):
            raise CraftError("Boost order not found")
        if o.product_type != PRODUCT_CRAFT_BOOST or (o.product_ref or "").lower() != target:
            raise CraftError(f"That boost is for a different craft (needs {target})")
        if o.status != "fulfilled":
            raise CraftError(f"Boost is not paid yet (status {o.status})")
        if getattr(o, "consumed_at", None):
            raise CraftError("Boost already used")
        o.consumed_at = datetime.utcnow()
        session.commit()
    finally:
        session.close()


def release_boost(db, boost_order_id: str) -> None:
    """Undo consumption after a failed transaction (refund is handled by the caller)."""
    from models import StarsOrder
    session = db.get_session()
    try:
        o = session.query(StarsOrder).filter_by(order_id=boost_order_id).first()
        if o:
            o.consumed_at = None
            session.commit()
    finally:
        session.close()


# ── the craft ──────────────────────────────────────────────────────────────

def craft(db, user_id: str, card_ids: Sequence[str], boost_order_id: Optional[str] = None,
          rng: Optional[random.Random] = None, seed: Optional[int] = None) -> Dict[str, Any]:
    """
    Atomic: inputs are consumed, the result granted and the event recorded in
    ONE transaction. If the boost was consumed and the transaction fails, a
    CraftTransactionError carries the boost id so the router can refund it.
    """
    from models import Card, CraftEvent, UserCard

    user_id = str(user_id)
    owned = _owned_map(db, user_id)
    inputs = _resolve_inputs(owned, card_ids)
    input_rarity, target = validate_inputs([c.get("rarity") for c in inputs])
    if cap_remaining(target, crafted_count(db, target)) <= 0:
        raise CraftError(f"The crafted {target} allocation for this season is exhausted")

    boosted = False
    if boost_order_id:
        _consume_boost(db, user_id, boost_order_id, target)  # charged before the roll
        boosted = True

    if seed is None:
        seed = secrets.randbits(63)
    if rng is None:
        rng = random.Random(seed)
    pct = success_pct(target, boosted)
    fam = output_family([c.get("genre_family") for c in inputs])
    counts = input_counts(card_ids)

    session = db.get_session()
    try:
        # 1. consume inputs (re-check quantities inside the transaction)
        for cid, n in counts.items():
            q = session.query(UserCard).filter_by(user_id=user_id, card_id=cid)
            if getattr(db, "_db_type", "") == "postgresql":
                q = q.with_for_update()  # row lock: two crafts can't spend the same copies
            uc = q.first()
            if not uc or int(uc.quantity or 0) < n:
                raise CraftError(f"Not enough copies of {cid}")
            uc.quantity = int(uc.quantity) - n
            if uc.quantity == 0:
                session.delete(uc)
        session.flush()

        # 2. roll
        success, value = roll(rng, pct)

        # 3. pick output
        if success:
            q = session.query(Card).filter(Card.rarity.ilike(target))
            pool = q.filter(Card.genre_family == fam).limit(500).all() if fam else []
            if not pool:
                pool = q.limit(500).all()
            if not pool:
                raise CraftTransactionError(f"No {target} cards exist to craft yet", boost_order_id)
            out_card = rng.choice(pool)
            out_id, out_fam = out_card.card_id, (out_card.genre_family or "NEUTRAL")
        else:
            floor = inputs[pick_floor_index(rng, len(inputs))]
            out_id, out_fam = str(floor["card_id"]), (floor.get("genre_family") or "NEUTRAL")

        # 4. grant
        uc = session.query(UserCard).filter_by(user_id=user_id, card_id=out_id).first()
        if uc:
            uc.quantity = int(uc.quantity or 0) + 1
        else:
            session.add(UserCard(user_id=user_id, card_id=out_id, quantity=1,
                                 acquired_from="craft" if success else "craft_floor"))

        # 5. record
        ev = CraftEvent(
            user_id=user_id, input_card_ids=json.dumps([str(c) for c in card_ids]),
            input_rarity=input_rarity, target_rarity=target, success=bool(success),
            output_card_id=out_id, output_family=out_fam, inherited_family=fam,
            boost_order_id=boost_order_id, success_pct=int(pct), roll_value=float(value),
            seed=int(seed), created_at=datetime.utcnow(),
        )
        session.add(ev)
        session.commit()
        event_id = ev.id
    except CraftError:
        session.rollback()
        if boost_order_id:
            release_boost(db, boost_order_id)
        raise
    except CraftTransactionError:
        session.rollback()
        raise
    except Exception as e:
        session.rollback()
        logger.error(f"[CRAFT] transaction failed for {user_id}: {e}")
        raise CraftTransactionError(str(e), boost_order_id)
    finally:
        session.close()

    out = _owned_map(db, user_id).get(out_id) or {"card_id": out_id}
    logger.info(f"[CRAFT] user={user_id} {input_rarity}x4 -> {target} pct={pct} success={success} out={out_id} boost={boost_order_id}")
    return {
        "event_id": event_id, "success": bool(success), "target_rarity": target, "input_rarity": input_rarity,
        "success_pct": pct, "boosted": boosted, "inherited_family": fam, "seed": seed,
        "output": {"card_id": out_id, "name": out.get("name"), "rarity": out.get("rarity"),
                   "genre_family": out.get("genre_family", out_fam), "power": out.get("power"),
                   "image_url": out.get("image_url"), "quantity": out.get("quantity")},
        "consumed": [{"card_id": cid, "count": n} for cid, n in counts.items()],
    }


def history(db, user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    from models import CraftEvent
    session = db.get_session()
    try:
        rows = (session.query(CraftEvent).filter_by(user_id=str(user_id))
                .order_by(CraftEvent.created_at.desc()).limit(limit).all())
        return [{
            "event_id": r.id, "input_rarity": r.input_rarity, "target_rarity": r.target_rarity,
            "success": bool(r.success), "output_card_id": r.output_card_id, "output_family": r.output_family,
            "boosted": bool(r.boost_order_id), "success_pct": r.success_pct,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in rows]
    finally:
        session.close()

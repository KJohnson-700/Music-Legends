"""Crafting / duplicate fusion (Phase 4.5)."""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.crafting import CraftError
from database import get_db
from services import crafting_service
from tma.api.auth import get_tg_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/craft", tags=["craft"])


class PreviewBody(BaseModel):
    card_ids: List[str] = Field(..., description="Exactly four owned card ids (duplicates allowed)")


class CraftBody(PreviewBody):
    boost_order_id: Optional[str] = None


def _user(db, tg):
    return db.get_or_create_telegram_user(tg["id"], tg.get("username", ""), tg.get("first_name", ""))


@router.get("/options")
def craft_options(tg: dict = Depends(get_tg_user)):
    """Rates, boost prices and remaining crafted allocation per target rarity, plus the player's craftable cards."""
    db = get_db()
    user = _user(db, tg)
    return {**crafting_service.options(db), "groups": crafting_service.craftable_groups(db, user["user_id"])}


@router.post("/preview")
def craft_preview(body: PreviewBody, tg: dict = Depends(get_tg_user)):
    db = get_db()
    user = _user(db, tg)
    try:
        return crafting_service.preview(db, user["user_id"], body.card_ids)
    except CraftError as e:
        raise HTTPException(400, str(e))


@router.post("")
async def do_craft(body: CraftBody, tg: dict = Depends(get_tg_user)):
    """Consume four cards, roll, grant the result. Boost is charged before the roll and refunded on failure."""
    db = get_db()
    user = _user(db, tg)
    try:
        return crafting_service.craft(db, user["user_id"], body.card_ids, body.boost_order_id)
    except CraftError as e:
        raise HTTPException(400, str(e))
    except crafting_service.CraftTransactionError as e:
        if e.boost_order_id:
            crafting_service.release_boost(db, e.boost_order_id)
            try:
                from services.stars_service import refund_order
                await refund_order(db, e.boost_order_id)
                logger.info(f"[CRAFT] refunded boost {e.boost_order_id} after failed craft")
            except Exception as re:
                logger.error(f"[CRAFT] boost refund FAILED for {e.boost_order_id}: {re}")
        raise HTTPException(500, f"Craft failed and nothing was consumed: {e}")


@router.get("/history")
def craft_history(tg: dict = Depends(get_tg_user)):
    db = get_db()
    user = _user(db, tg)
    return {"events": crafting_service.history(db, user["user_id"])}

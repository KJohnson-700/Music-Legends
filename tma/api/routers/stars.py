"""Telegram Stars checkout for the Mini App (Phase 5). Replaces Stripe inside Telegram."""
import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from database import get_db
from services import stars_service
from tma.api.auth import get_tg_user

router = APIRouter(prefix="/api/stars", tags=["stars"])


class InvoiceBody(BaseModel):
    product_type: str = Field(..., description="tier_pack | creator_pack")
    ref: str = Field(..., description="tier name (community/gold/platinum) or pack_id")


@router.get("/catalog")
def stars_catalog(tg: dict = Depends(get_tg_user)):
    """Stars prices for tier packs and public creator packs."""
    return stars_service.catalog(get_db())


@router.post("/invoice")
async def create_invoice(body: InvoiceBody, tg: dict = Depends(get_tg_user)):
    """Create a pending order + Telegram invoice link. Client opens it with WebApp.openInvoice()."""
    db = get_db()
    user = db.get_or_create_telegram_user(tg["id"], tg.get("username", ""), tg.get("first_name", ""))
    try:
        return await stars_service.create_order(db, user, body.product_type.strip().lower(), body.ref)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:  # Telegram API errors
        raise HTTPException(502, f"Could not create invoice: {e}")


@router.get("/orders")
def my_orders(tg: dict = Depends(get_tg_user)):
    db = get_db()
    user = db.get_or_create_telegram_user(tg["id"], tg.get("username", ""))
    return {"orders": stars_service.list_orders(db, user["user_id"])}


@router.get("/orders/{order_id}")
def order_status(order_id: str, tg: dict = Depends(get_tg_user)):
    """Poll after openInvoice() resolves 'paid'. Cards appear once the webhook has fulfilled."""
    db = get_db()
    user = db.get_or_create_telegram_user(tg["id"], tg.get("username", ""))
    order = stars_service.get_order(db, order_id)
    if not order:
        raise HTTPException(404, "Order not found")
    if str(order["user_id"]) != str(user["user_id"]):
        raise HTTPException(403, "Not your order")
    return order


@router.post("/orders/{order_id}/refund")
async def refund(order_id: str, x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key")):
    """Admin-only refund via refundStarPayment (TELEGRAM_HOST_ADMIN_KEY)."""
    expected = (os.getenv("TELEGRAM_HOST_ADMIN_KEY") or "").strip()
    if not expected or (x_admin_key or "") != expected:
        raise HTTPException(401, "Invalid admin key")
    try:
        return await stars_service.refund_order(get_db(), order_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))

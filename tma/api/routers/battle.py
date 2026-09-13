"""Battle router — in-app PvP battles."""
import json
import os
import secrets
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import desc
from tma.api.auth import get_tg_user
from tma.api.telegram_identity import extract_telegram_id_from_user
from database import get_db
from cards_config import compute_card_power, compute_team_power
from core.battle import CardRef, resolve_match
from models import PendingTmaBattle, User

router = APIRouter(prefix="/api/battle", tags=["battle"])


class ChallengeRequest(BaseModel):
    opponent_telegram_id: int
    pack_id: str | None = None
    card_id: str | None = None
    wager_tier: str = "casual"


class AcceptRequest(BaseModel):
    pack_id: str | None = None
    card_id: str | None = None


def _make_battle_id() -> str:
    return secrets.token_hex(3).upper()


def _build_bot_start_link(battle_id: str) -> str:
    """Fallback bot-chat deep link for challenge notifications."""
    bot_username = (os.environ.get("TELEGRAM_BOT_USERNAME") or "MusicLegendsBot").lstrip("@")
    return f"https://t.me/{bot_username}?start=battle_{battle_id}"


def _card_to_ref(card: dict) -> CardRef:
    """Platform-neutral card for the resolver (power supplied via p1/p2_override)."""
    return CardRef.from_db_card(card)


def _build_collection_pack(db, user_id: str, focus_card_id: str | None = None) -> dict | None:
    """
    Build a synthetic battle pack from a user's collection.
    If focus_card_id is provided, it is forced as champion and surrounded by strongest supports.
    """
    cards = db.get_user_collection(user_id) or []
    if not cards:
        return None

    # Deduplicate by card_id and sort strongest-first.
    unique = {}
    for c in cards:
        cid = str(c.get("card_id") or "")
        if cid and cid not in unique:
            unique[cid] = c
    ordered = sorted(unique.values(), key=compute_card_power, reverse=True)
    if not ordered:
        return None

    selected = None
    if focus_card_id:
        selected = next((c for c in ordered if str(c.get("card_id")) == str(focus_card_id)), None)
        if not selected:
            return None
        supports = [c for c in ordered if str(c.get("card_id")) != str(focus_card_id)][:4]
        squad = [selected] + supports
        name = selected.get("name") or "Selected Card"
        pack_id = f"card:{focus_card_id}"
        pack_name = f"{name} Squad"
    else:
        squad = ordered[:5]
        pack_id = f"collection:{user_id}"
        pack_name = "My Collection Squad"

    return {
        "pack_id": pack_id,
        "pack_name": pack_name,
        "pack_tier": "community",
        "genre": "Music",
        "cards": squad,
    }


def _resolve_selected_pack(db, user_id: str, pack_id: str | None, card_id: str | None) -> tuple[str, dict]:
    """
    Resolve battle selection from either purchased pack_id or selected card_id.
    Returns (selection_ref, pack_payload).
    """
    if pack_id:
        pack_str = str(pack_id)
        # Backward-compatible card-first selection for older clients that send card ids via pack_id.
        if pack_str.startswith("card:"):
            parsed_card_id = pack_str.split(":", 1)[1]
            pack = _build_collection_pack(db, user_id, focus_card_id=parsed_card_id)
            if pack:
                return pack_str, pack
            if card_id:
                # Fallback to explicit card_id when both fields are present.
                explicit = _build_collection_pack(db, user_id, focus_card_id=str(card_id))
                if explicit:
                    return f"card:{card_id}", explicit
            raise HTTPException(403, "You don't own that card")
        if pack_str.startswith("collection:"):
            pack = _build_collection_pack(db, user_id)
            if pack:
                return pack_str, pack
            raise HTTPException(403, "You don't have cards for battle")

        packs = db.get_user_purchased_packs(user_id)
        resolved = next((p for p in packs if str(p.get("pack_id")) == pack_str), None)
        if resolved and (resolved.get("cards") or []):
            return pack_str, resolved

        # Some legacy clients send raw card_id in pack_id.
        as_card = _build_collection_pack(db, user_id, focus_card_id=pack_str)
        if as_card:
            return f"card:{pack_str}", as_card
        # If pack exists but has empty card payload, fallback to collection squad.
        collection_pack = _build_collection_pack(db, user_id)
        if collection_pack:
            return f"collection:{user_id}", collection_pack
        if card_id:
            explicit = _build_collection_pack(db, user_id, focus_card_id=str(card_id))
            if explicit:
                return f"card:{card_id}", explicit
        raise HTTPException(403, "You don't own that pack or card")

    if card_id:
        pack = _build_collection_pack(db, user_id, focus_card_id=card_id)
        if not pack:
            raise HTTPException(403, "You don't own that card")
        return f"card:{card_id}", pack

    raise HTTPException(400, "Select a pack or a card")


def _resolve_pack_from_ref(db, user_id: str, selection_ref: str) -> dict | None:
    """Resolve a stored battle selection reference into a concrete pack payload."""
    if selection_ref.startswith("card:"):
        card_id = selection_ref.split(":", 1)[1]
        return _build_collection_pack(db, user_id, focus_card_id=card_id)
    if selection_ref.startswith("collection:"):
        return _build_collection_pack(db, user_id)

    packs = db.get_user_purchased_packs(user_id)
    return next((p for p in packs if str(p.get("pack_id")) == str(selection_ref)), None)


def _run_battle(db, challenger_id: int, opponent_id: int,
                c_pack: dict, o_pack: dict, wager_tier: str) -> dict:
    """Execute battle and distribute rewards. Rewards are distributed first —
    they must never be orphaned by a downstream failure."""
    c_cards = sorted(c_pack.get("cards", []), key=compute_card_power, reverse=True)
    o_cards = sorted(o_pack.get("cards", []), key=compute_card_power, reverse=True)

    if not c_cards or not o_cards:
        return {"error": "Empty pack", "winner": 0}

    c_champ, o_champ = c_cards[0], o_cards[0]
    c_power = compute_team_power(
        compute_card_power(c_champ), [compute_card_power(c) for c in c_cards[1:5]]
    )
    o_power = compute_team_power(
        compute_card_power(o_champ), [compute_card_power(c) for c in o_cards[1:5]]
    )

    result = resolve_match(
        _card_to_ref(c_champ),
        _card_to_ref(o_champ),
        wager_tier,
        p1_override=c_power,
        p2_override=o_power,
    )

    p1, p2 = result["player1"], result["player2"]

    # Distribute BEFORE returning — gold must not be orphaned
    db.update_user_economy(challenger_id, gold_change=p1["gold_reward"])
    db.update_user_economy(opponent_id,   gold_change=p2["gold_reward"])

    return {
        "winner":      result["winner"],
        "is_critical": result.get("is_critical", False),
        "challenger": {
            "name":        c_champ.get("name"),
            "power":       c_power,
            "gold_reward": p1["gold_reward"],
            "xp_reward":   p1.get("xp_reward", 0),
            "image_url":   c_champ.get("image_url"),
            "youtube_url": c_champ.get("youtube_url"),
            "rarity":      c_champ.get("rarity"),
        },
        "opponent": {
            "name":        o_champ.get("name"),
            "power":       o_power,
            "gold_reward": p2["gold_reward"],
            "xp_reward":   p2.get("xp_reward", 0),
            "image_url":   o_champ.get("image_url"),
            "youtube_url": o_champ.get("youtube_url"),
            "rarity":      o_champ.get("rarity"),
        },
    }


@router.post("/challenge")
async def create_challenge(body: ChallengeRequest, tg: dict = Depends(get_tg_user)):
    """Challenger picks pack and creates a pending battle for in-app acceptance."""
    db = get_db()
    challenger = db.get_or_create_telegram_user(tg["id"], tg.get("username", ""))

    selection_ref, _challenger_pack = _resolve_selected_pack(
        db, challenger["user_id"], body.pack_id, body.card_id
    )
    print(
        f"[BATTLE] challenge selection challenger_tg={tg['id']} "
        f"resolved_via={selection_ref}"
    )

    battle_id = _make_battle_id()
    expires = datetime.utcnow() + timedelta(hours=24)

    if body.opponent_telegram_id <= 0:
        raise HTTPException(400, "Select a valid opponent")
    if int(body.opponent_telegram_id) == int(tg["id"]):
        raise HTTPException(400, "You can't challenge yourself")

    # Fallback flow: opponent must explicitly register as battle-ready.
    opponent_user = db.get_registered_battle_player(body.opponent_telegram_id)
    if not opponent_user:
        print(
            f"[BATTLE] create_challenge opponent_not_registered "
            f"challenger_tg={tg['id']} opponent_tg={body.opponent_telegram_id}"
        )
        raise HTTPException(404, "Opponent is not registered for battle yet")

    session = db.get_session()
    try:
        battle_row = PendingTmaBattle(
            battle_id=battle_id,
            challenger_id=challenger["user_id"],
            opponent_id=opponent_user["user_id"],
            challenger_pack=selection_ref,
            wager_tier=body.wager_tier,
            status="waiting",
            expires_at=expires,
        )
        session.add(battle_row)
        session.commit()
    except Exception as e:
        session.rollback()
        raise HTTPException(500, f"Failed to create battle: {e}")
    finally:
        session.close()

    # Best-effort Telegram DM notification with an accept button.
    try:
        from tma.api.bot.handlers import notify_battle_challenge
        await notify_battle_challenge(
            opponent_telegram_id=int(body.opponent_telegram_id),
            challenger_name=tg.get("username") or tg.get("first_name", "Someone"),
            battle_id=battle_id,
            wager_tier=body.wager_tier,
            link=_build_bot_start_link(battle_id),
        )
    except Exception as e:
        print(f"[BATTLE] Challenge notify failed (non-critical): {e}")

    return {
        "battle_id": battle_id,
        "status": "waiting",
        "expires_at": expires.isoformat(),
        "in_app_only": True,
    }


@router.post("/register")
def register_for_battle(tg: dict = Depends(get_tg_user)):
    """Register current Telegram user as available battle opponent."""
    db = get_db()
    result = db.register_battle_player(
        telegram_id=tg["id"],
        username=tg.get("username", ""),
        first_name=tg.get("first_name", ""),
    )
    print(
        f"[BATTLE] register success tg={tg['id']} "
        f"user_id={result.get('user_id')} username={result.get('username')}"
    )
    return result


@router.get("/opponents")
def list_registered_opponents(tg: dict = Depends(get_tg_user)):
    """List users who manually registered as available for battle."""
    db = get_db()
    # Keep caller's user profile fresh, but don't auto-register for battle.
    db.get_or_create_telegram_user(tg["id"], tg.get("username", ""), tg.get("first_name", ""))
    players = db.list_registered_battle_players(exclude_telegram_id=tg["id"], limit=100)
    print(f"[BATTLE] opponents caller_tg={tg['id']} count={len(players)}")
    return {"players": players}


@router.get("/opponents/search")
def search_registered_opponents(
    q: str = Query(default="", max_length=64),
    tg: dict = Depends(get_tg_user),
):
    """Search battle-registered opponents by username/Telegram ID."""
    db = get_db()
    db.get_or_create_telegram_user(tg["id"], tg.get("username", ""), tg.get("first_name", ""))
    term = (q or "").strip().lower().lstrip("@")
    rows = db.list_registered_battle_players(exclude_telegram_id=tg["id"], limit=200)
    if not term:
        return {"players": rows[:50]}

    out = []
    for p in rows:
        uname = str(p.get("username") or "").lower()
        tid = str(p.get("telegram_id") or "")
        if term in uname or term in tid:
            out.append(p)
    exact = [p for p in out if str(p.get("username") or "").lower() == term or str(p.get("telegram_id") or "") == term]
    partial = [p for p in out if p not in exact]
    return {"players": (exact + partial)[:50]}


@router.get("/incoming")
def list_incoming_challenges(tg: dict = Depends(get_tg_user)):
    """List waiting battle challenges for the current Telegram user."""
    db = get_db()
    me = db.get_or_create_telegram_user(tg["id"], tg.get("username", ""))
    now = datetime.utcnow()

    session = db.get_session()
    try:
        rows = (
            session.query(PendingTmaBattle)
            .filter(PendingTmaBattle.opponent_id == str(me["user_id"]))
            .filter(PendingTmaBattle.status == "waiting")
            .order_by(desc(PendingTmaBattle.created_at))
            .limit(50)
            .all()
        )
        valid = [r for r in rows if not r.expires_at or r.expires_at > now]
        challenger_ids = list({str(r.challenger_id) for r in valid})
        users = (
            session.query(User).filter(User.user_id.in_(challenger_ids)).all()
            if challenger_ids else []
        )
        u_map = {str(u.user_id): u for u in users}

        out = []
        for r in valid:
            cu = u_map.get(str(r.challenger_id))
            challenger_name = (cu.username if cu and cu.username else f"user_{r.challenger_id}")
            out.append({
                "battle_id": r.battle_id,
                "challenger_user_id": str(r.challenger_id),
                "challenger_username": challenger_name,
                "challenger_telegram_id": extract_telegram_id_from_user(db, cu) if cu else None,
                "wager_tier": r.wager_tier or "casual",
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            })
        return {"challenges": out}
    finally:
        session.close()


@router.get("/updates")
def battle_updates(tg: dict = Depends(get_tg_user)):
    """
    Return in-app battle updates for current user:
    - incoming waiting challenges
    - outgoing waiting challenges
    - recent completed battles with result payload
    """
    db = get_db()
    me = db.get_or_create_telegram_user(tg["id"], tg.get("username", ""))
    me_id = str(me["user_id"])
    now = datetime.utcnow()

    session = db.get_session()
    try:
        rows = (
            session.query(PendingTmaBattle)
            .filter(
                (PendingTmaBattle.challenger_id == me_id) |
                (PendingTmaBattle.opponent_id == me_id)
            )
            .order_by(desc(PendingTmaBattle.created_at))
            .limit(100)
            .all()
        )
        user_ids = {
            str(r.challenger_id) for r in rows
        } | {
            str(r.opponent_id) for r in rows if r.opponent_id
        }
        users = (
            session.query(User).filter(User.user_id.in_(list(user_ids))).all()
            if user_ids else []
        )
        u_map = {str(u.user_id): u for u in users}

        incoming = []
        outgoing = []
        completed = []
        for r in rows:
            expired = bool(r.expires_at and r.expires_at <= now)
            status = "expired" if (r.status == "waiting" and expired) else r.status

            challenger_user = u_map.get(str(r.challenger_id))
            opponent_user = u_map.get(str(r.opponent_id)) if r.opponent_id else None
            challenger_name = challenger_user.username if challenger_user and challenger_user.username else f"user_{r.challenger_id}"
            opponent_name = opponent_user.username if opponent_user and opponent_user.username else (f"user_{r.opponent_id}" if r.opponent_id else "unknown")

            payload = {
                "battle_id": r.battle_id,
                "status": status,
                "wager_tier": r.wager_tier or "casual",
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                "challenger_user_id": str(r.challenger_id),
                "opponent_user_id": str(r.opponent_id) if r.opponent_id else None,
                "challenger_username": challenger_name,
                "opponent_username": opponent_name,
                "challenger_telegram_id": extract_telegram_id_from_user(db, challenger_user) if challenger_user else None,
                "opponent_telegram_id": extract_telegram_id_from_user(db, opponent_user) if opponent_user else None,
            }

            if str(r.opponent_id) == me_id and status == "waiting":
                incoming.append(payload)
            if str(r.challenger_id) == me_id and status == "waiting":
                outgoing.append(payload)
            if status == "complete":
                result = json.loads(r.result_json) if r.result_json else None
                completed.append({**payload, "result": result})

        return {
            "incoming": incoming[:30],
            "outgoing": outgoing[:30],
            "completed": completed[:30],
        }
    finally:
        session.close()


@router.post("/{battle_id}/accept")
async def accept_challenge(battle_id: str, body: AcceptRequest,
                           tg: dict = Depends(get_tg_user)):
    """Opponent selects pack and battle executes immediately."""
    db = get_db()
    opponent = db.get_or_create_telegram_user(tg["id"], tg.get("username", ""))

    session = db.get_session()
    try:
        battle_row = session.query(PendingTmaBattle).filter_by(battle_id=battle_id).first()
        if not battle_row:
            raise HTTPException(404, "Battle not found")
        battle = {
            "battle_id":      battle_row.battle_id,
            "challenger_id":  battle_row.challenger_id,
            "challenger_pack": battle_row.challenger_pack,
            "wager_tier":     battle_row.wager_tier,
            "status":         battle_row.status,
            "expires_at":     battle_row.expires_at,
        }
    finally:
        session.close()

    if battle["status"] != "waiting":
        raise HTTPException(400, f"Battle is already {battle['status']}")
    if battle["expires_at"] and datetime.utcnow() > battle["expires_at"]:
        raise HTTPException(400, "Battle link has expired")

    opponent_ref, o_pack = _resolve_selected_pack(
        db, opponent["user_id"], body.pack_id, body.card_id
    )
    print(
        f"[BATTLE] accept selection opponent_tg={tg['id']} "
        f"resolved_via={opponent_ref}"
    )
    c_pack = _resolve_pack_from_ref(db, battle["challenger_id"], str(battle["challenger_pack"]))

    if not c_pack or not o_pack:
        raise HTTPException(400, "Could not resolve battle squads")

    result = _run_battle(db, battle["challenger_id"], opponent["user_id"],
                         c_pack, o_pack, battle["wager_tier"])

    session = db.get_session()
    try:
        battle_row = session.query(PendingTmaBattle).filter_by(battle_id=battle_id).first()
        if battle_row:
            battle_row.status = "complete"
            battle_row.opponent_id = opponent["user_id"]
            battle_row.opponent_pack = opponent_ref
            battle_row.result_json = json.dumps(result)
            session.commit()
    except Exception as e:
        session.rollback()
    finally:
        session.close()

    return {"battle_id": battle_id, "result": result}


@router.post("/{battle_id}/cancel")
def cancel_challenge(battle_id: str, tg: dict = Depends(get_tg_user)):
    """Cancel a waiting challenge (challenger or opponent)."""
    db = get_db()
    actor = db.get_or_create_telegram_user(tg["id"], tg.get("username", ""))
    actor_id = str(actor["user_id"])

    session = db.get_session()
    try:
        row = session.query(PendingTmaBattle).filter_by(battle_id=battle_id).first()
        if not row:
            raise HTTPException(404, "Battle not found")
        if row.status != "waiting":
            raise HTTPException(400, f"Battle is already {row.status}")
        if actor_id not in {str(row.challenger_id), str(row.opponent_id)}:
            raise HTTPException(403, "Not allowed to cancel this battle")
        row.status = "cancelled"
        session.commit()
        return {"success": True, "battle_id": battle_id, "status": "cancelled"}
    except HTTPException:
        session.rollback()
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(500, f"Failed to cancel battle: {e}")
    finally:
        session.close()


@router.get("/{battle_id}")
def get_battle(battle_id: str, tg: dict = Depends(get_tg_user)):
    """Poll for battle status/result."""
    db = get_db()
    session = db.get_session()
    try:
        row = session.query(PendingTmaBattle).filter_by(battle_id=battle_id).first()
        if not row:
            raise HTTPException(404, "Battle not found")
        status = row.status
        if status == "waiting" and row.expires_at and datetime.utcnow() > row.expires_at:
            status = "expired"
        return {
            "battle_id": battle_id,
            "status":    status,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "result":    json.loads(row.result_json) if row.result_json else None,
        }
    finally:
        session.close()

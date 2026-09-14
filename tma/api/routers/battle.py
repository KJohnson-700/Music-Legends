"""Battle router — in-app PvP battles."""
import json
import os
import random
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
from core.battle import Ability, CardRef, Lineup, LineupError, auto_lineup, resolve_lineup_match
from core.battle.config import LINEUP_SIZE, MAX_SAME_FAMILY
from core.battle.genre import GenreFamily
from models import PendingTmaBattle, User

router = APIRouter(prefix="/api/battle", tags=["battle"])


class ChallengeRequest(BaseModel):
    opponent_telegram_id: int
    pack_id: str | None = None
    card_id: str | None = None
    wager_tier: str = "casual"
    # Phase 4: ordered 3-card lineup + one optional ability (0-based slot)
    lineup: list[str] | None = None
    ability: str | None = None
    ability_slot: int | None = None


class AcceptRequest(BaseModel):
    pack_id: str | None = None
    card_id: str | None = None
    lineup: list[str] | None = None
    ability: str | None = None
    ability_slot: int | None = None


class ScoutRequest(BaseModel):
    slot: int


def _make_battle_id() -> str:
    return secrets.token_hex(3).upper()


def _build_bot_start_link(battle_id: str) -> str:
    """Fallback bot-chat deep link for challenge notifications."""
    bot_username = (os.environ.get("TELEGRAM_BOT_USERNAME") or "MusicLegendsBot").lstrip("@")
    return f"https://t.me/{bot_username}?start=battle_{battle_id}"


def _selection_to_json(ref: str | None, lineup: list[str] | None,
                       ability: str | None, ability_slot: int | None) -> str:
    """Stored in pending_tma_battles.challenger_pack / opponent_pack (Text)."""
    return json.dumps({"ref": ref, "lineup": lineup, "ability": ability, "ability_slot": ability_slot})


def _parse_selection(text: str | None) -> dict:
    """Back-compat: pre-Phase-4 rows hold a bare selection ref string."""
    if not text:
        return {"ref": None, "lineup": None, "ability": None, "ability_slot": None}
    t = str(text).strip()
    if t.startswith("{"):
        try:
            d = json.loads(t)
            return {"ref": d.get("ref"), "lineup": d.get("lineup"),
                    "ability": d.get("ability"), "ability_slot": d.get("ability_slot")}
        except Exception:
            pass
    return {"ref": t, "lineup": None, "ability": None, "ability_slot": None}


def _card_ref(card: dict) -> CardRef:
    """DB card dict → battle CardRef with computed (compressed) power."""
    return CardRef.from_db_card(card, power=compute_card_power(card))


def _user_card_refs(db, user_id: str) -> dict[str, CardRef]:
    refs: dict[str, CardRef] = {}
    for c in db.get_user_collection(user_id) or []:
        cid = str(c.get("card_id") or "")
        if cid and cid not in refs:
            refs[cid] = _card_ref(c)
    return refs


def _lineup_payload(ref: CardRef) -> dict:
    d = ref.to_dict()
    d.pop("extra", None)
    return d


def _build_lineup(db, user_id: str, sel: dict, forced_scout: dict | None = None) -> Lineup:
    """
    Turn a stored/submitted selection into a validated Lineup.
    Explicit lineup ids must all be owned. Otherwise auto-pick the strongest
    legal three from the referenced pack (or whole collection).
    """
    refs = _user_card_refs(db, user_id)
    ability = sel.get("ability")
    slot = sel.get("ability_slot")
    if forced_scout:
        ability, slot = "scout", int(forced_scout.get("slot", 0))

    ids = sel.get("lineup") or []
    try:
        if ids:
            missing = [i for i in ids if str(i) not in refs]
            if missing:
                raise HTTPException(403, f"You don't own card {missing[0]}")
            cards = [refs[str(i)] for i in ids]
        else:
            pool: list[CardRef]
            ref = sel.get("ref")
            pack = _resolve_pack_from_ref(db, user_id, str(ref)) if ref else None
            if pack and pack.get("cards"):
                pool = []
                for c in pack["cards"]:
                    cid = str(c.get("card_id") or "")
                    pool.append(refs.get(cid) or _card_ref(c))
            else:
                pool = list(refs.values())
            # A "card:<id>" selection is a chosen champion — it leads the lineup.
            lead = None
            if ref and str(ref).startswith("card:"):
                focus = str(ref).split(":", 1)[1]
                lead = next((c for c in pool if str(c.card_id) == focus), None)
                if lead is not None:
                    pool = [c for c in pool if str(c.card_id) != focus]
            cards = auto_lineup(pool, lead=lead)
        return Lineup(cards, ability, slot)
    except LineupError as e:
        raise HTTPException(400, str(e))


def _summary(side: dict, lineup: Lineup, total: int) -> dict:
    """Legacy card-shaped summary so older clients still render a result screen."""
    first = lineup.cards[0]
    return {
        "name":        first.name,
        "power":       int(total),
        "gold_reward": side["gold_reward"],
        "xp_reward":   side.get("xp_reward", 0),
        "image_url":   first.image_url,
        "youtube_url": first.youtube_url,
        "rarity":      first.rarity,
        "lineup":      [_lineup_payload(c) for c in lineup.cards],
        "ability":     lineup.ability.value if lineup.ability else None,
        "ability_slot": lineup.ability_slot,
    }


def _run_lineup_battle(db, challenger_id, opponent_id, l1: Lineup, l2: Lineup, wager_tier: str) -> dict:
    """Resolve a best-of-three and distribute rewards BEFORE returning — gold must never be orphaned."""
    seed = secrets.randbits(63)
    result = resolve_lineup_match(l1, l2, wager_tier, rng=random.Random(seed))
    p1, p2 = result["player1"], result["player2"]

    db.update_user_economy(challenger_id, gold_change=p1["gold_reward"], xp_change=p1.get("xp_reward", 0))
    db.update_user_economy(opponent_id,   gold_change=p2["gold_reward"], xp_change=p2.get("xp_reward", 0))

    result["seed"] = seed
    result["is_critical"] = any(r["player1"]["critical_hit"] or r["player2"]["critical_hit"] for r in result["rounds"])
    result["challenger"] = _summary(p1, l1, result["total_power"][0])
    result["opponent"] = _summary(p2, l2, result["total_power"][1])
    return result


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


@router.post("/challenge")
async def create_challenge(body: ChallengeRequest, tg: dict = Depends(get_tg_user)):
    """Challenger picks pack and creates a pending battle for in-app acceptance."""
    db = get_db()
    challenger = db.get_or_create_telegram_user(tg["id"], tg.get("username", ""))

    if body.lineup:
        selection_ref = None
    else:
        selection_ref, _challenger_pack = _resolve_selected_pack(
            db, challenger["user_id"], body.pack_id, body.card_id
        )
    challenger_sel = {"ref": selection_ref, "lineup": body.lineup,
                      "ability": body.ability, "ability_slot": body.ability_slot}
    _build_lineup(db, challenger["user_id"], challenger_sel)  # validate now: ownership, size, family cap, ability
    print(
        f"[BATTLE] challenge selection challenger_tg={tg['id']} "
        f"resolved_via={selection_ref} lineup={bool(body.lineup)} ability={body.ability}"
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
            challenger_pack=_selection_to_json(selection_ref, body.lineup, body.ability, body.ability_slot),
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


@router.get("/lineup/cards")
def lineup_cards(tg: dict = Depends(get_tg_user)):
    """Cards the player can put in a lineup, with genre family, momentum and a suggested order."""
    db = get_db()
    me = db.get_or_create_telegram_user(tg["id"], tg.get("username", ""))
    refs = sorted(_user_card_refs(db, me["user_id"]).values(), key=lambda c: c.power, reverse=True)
    try:
        suggested = [c.card_id for c in auto_lineup(refs)]
    except LineupError:
        suggested = []
    return {
        "cards": [_lineup_payload(c) for c in refs],
        "suggested": suggested,
        "rules": {"size": LINEUP_SIZE, "max_same_family": MAX_SAME_FAMILY,
                  "families": [f.value for f in GenreFamily if f != GenreFamily.NEUTRAL],
                  "ring": {"HIP_HOP": "POP", "POP": "ROCK", "ROCK": "ELECTRONIC",
                           "ELECTRONIC": "SOUL", "SOUL": "HIP_HOP"},
                  "abilities": [a.value for a in Ability]},
    }


@router.post("/{battle_id}/scout")
def scout_challenger(battle_id: str, body: ScoutRequest, tg: dict = Depends(get_tg_user)):
    """
    Acceptor's SCOUT ability: reveal the FAMILY of one of the challenger's slots
    before committing a lineup. Single use; locks the acceptor's ability to scout.
    """
    if not (0 <= int(body.slot) < LINEUP_SIZE):
        raise HTTPException(400, f"slot must be between 0 and {LINEUP_SIZE - 1}")
    db = get_db()
    me = db.get_or_create_telegram_user(tg["id"], tg.get("username", ""))
    session = db.get_session()
    try:
        row = session.query(PendingTmaBattle).filter_by(battle_id=battle_id).first()
        if not row:
            raise HTTPException(404, "Battle not found")
        if row.status != "waiting":
            raise HTTPException(400, f"Battle is already {row.status}")
        if row.opponent_id and str(row.opponent_id) != str(me["user_id"]):
            raise HTTPException(403, "This challenge was sent to another player")
        if str(row.challenger_id) == str(me["user_id"]):
            raise HTTPException(403, "Only the challenged player can scout")
        if getattr(row, "opponent_scout_json", None):
            prev = json.loads(row.opponent_scout_json)
            raise HTTPException(400, f"Scout already used on slot {int(prev.get('slot', 0)) + 1}")
        challenger_lineup = _build_lineup(db, row.challenger_id, _parse_selection(row.challenger_pack))
        family = challenger_lineup.families()[int(body.slot)].value
        row.opponent_scout_json = json.dumps({"slot": int(body.slot), "family": family})
        session.commit()
        return {"battle_id": battle_id, "slot": int(body.slot), "family": family, "ability_locked": "scout"}
    except HTTPException:
        session.rollback()
        raise
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
            "opponent_id":    battle_row.opponent_id,
            "challenger_pack": battle_row.challenger_pack,
            "wager_tier":     battle_row.wager_tier,
            "status":         battle_row.status,
            "expires_at":     battle_row.expires_at,
            "scout":          json.loads(battle_row.opponent_scout_json) if getattr(battle_row, "opponent_scout_json", None) else None,
        }
    finally:
        session.close()

    if battle["status"] != "waiting":
        raise HTTPException(400, f"Battle is already {battle['status']}")
    if battle["expires_at"] and datetime.utcnow() > battle["expires_at"]:
        raise HTTPException(400, "Battle link has expired")

    if str(battle["challenger_id"]) == str(opponent["user_id"]):
        raise HTTPException(400, "You can't accept your own challenge")
    if battle["opponent_id"] and str(battle["opponent_id"]) != str(opponent["user_id"]):
        raise HTTPException(403, "This challenge was sent to another player")

    if body.lineup:
        opponent_ref = None
    else:
        opponent_ref, _o_pack = _resolve_selected_pack(
            db, opponent["user_id"], body.pack_id, body.card_id
        )
    opponent_sel = {"ref": opponent_ref, "lineup": body.lineup,
                    "ability": body.ability, "ability_slot": body.ability_slot}
    print(
        f"[BATTLE] accept selection opponent_tg={tg['id']} "
        f"resolved_via={opponent_ref} lineup={bool(body.lineup)} ability={body.ability}"
    )

    l1 = _build_lineup(db, battle["challenger_id"], _parse_selection(battle["challenger_pack"]))
    l2 = _build_lineup(db, opponent["user_id"], opponent_sel, forced_scout=battle["scout"])

    result = _run_lineup_battle(db, battle["challenger_id"], opponent["user_id"],
                                l1, l2, battle["wager_tier"])
    opponent_ref = _selection_to_json(opponent_ref, body.lineup,
                                      l2.ability.value if l2.ability else None, l2.ability_slot)

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
            "wager_tier": row.wager_tier or "casual",
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "result":    json.loads(row.result_json) if row.result_json else None,
            "scout":     json.loads(row.opponent_scout_json) if getattr(row, "opponent_scout_json", None) else None,
        }
    finally:
        session.close()

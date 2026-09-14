"""
Battle resolver — pure functions, deterministic given an explicit RNG.

    rng = random.Random(seed)
    result = resolve_match(card1, card2, "casual", rng=rng)            # legacy 1v1
    result = resolve_lineup_match(lineup1, lineup2, "casual", rng=rng)  # Phase 4 best-of-3

Never touches the global `random` module, so the same seed always replays the
same match. Cards are duck-typed: anything with `.power` works, or pass
p1_override / p2_override to supply computed team power directly.
"""
import random
from typing import Any, Dict, List, Optional

from core.battle.config import (
    AMP_MULT,
    BattleWagerConfig,
    CRITICAL_HIT_CHANCE,
    CRITICAL_MULTIPLIER,
    LINEUP_SIZE,
    MIN_POWER_ADVANTAGE,
    MOMENTUM_MULT,
    RESOLUTION_ORDER,
    ROUNDS_TO_WIN,
    TIE_GOLD,
    TIE_XP,
)
from core.battle.genre import GenreFamily, genre_multiplier
from core.battle.lineup import Ability, Lineup, card_family


def roll_crit(rng: random.Random, chance: float = CRITICAL_HIT_CHANCE) -> bool:
    """One crit roll. Isolated so tests and future abilities can hook it."""
    return rng.random() < chance


def resolve_round(
    power1: int,
    power2: int,
    rng: random.Random,
    *,
    crit_chance: float = CRITICAL_HIT_CHANCE,
    crit_multiplier: float = CRITICAL_MULTIPLIER,
    min_advantage: int = MIN_POWER_ADVANTAGE,
) -> Dict[str, Any]:
    """
    Resolve one head-to-head power comparison (no genre/abilities).
    Returns winner (0 tie / 1 / 2), final powers, crit flags, and the gap.
    """
    crit1 = roll_crit(rng, crit_chance)
    crit2 = roll_crit(rng, crit_chance)

    final1 = int(power1 * crit_multiplier) if crit1 else int(power1)
    final2 = int(power2 * crit_multiplier) if crit2 else int(power2)

    diff = abs(final1 - final2)
    if diff < min_advantage:
        winner = 0
    elif final1 > final2:
        winner = 1
    else:
        winner = 2

    return {
        "winner": winner,
        "final_power1": final1,
        "final_power2": final2,
        "critical_hit1": crit1,
        "critical_hit2": crit2,
        "power_difference": diff,
    }


def _rewards(winner: int, tier: Dict) -> Dict[str, int]:
    if winner == 1:
        return {"gold1": tier["winner_gold"], "gold2": tier["loser_gold"],
                "xp1": tier["winner_xp"], "xp2": tier["loser_xp"]}
    if winner == 2:
        return {"gold1": tier["loser_gold"], "gold2": tier["winner_gold"],
                "xp1": tier["loser_xp"], "xp2": tier["winner_xp"]}
    return {"gold1": TIE_GOLD, "gold2": TIE_GOLD, "xp1": TIE_XP, "xp2": TIE_XP}


def resolve_match(
    card1: Any,
    card2: Any,
    wager_tier: str = "casual",
    p1_override: Optional[int] = None,
    p2_override: Optional[int] = None,
    rng: Optional[random.Random] = None,
) -> Dict[str, Any]:
    """
    Legacy single-card match. Result shape is unchanged from the old
    BattleEngine.execute_battle() so existing callers keep working.
    """
    if rng is None:
        rng = random.Random()

    base1 = int(p1_override if p1_override is not None else getattr(card1, "power", 0))
    base2 = int(p2_override if p2_override is not None else getattr(card2, "power", 0))

    rnd = resolve_round(base1, base2, rng)
    tier = BattleWagerConfig.get_tier(wager_tier)
    rewards = _rewards(rnd["winner"], tier)

    return {
        "winner": rnd["winner"],
        "player1": {
            "card": card1,
            "base_power": base1,
            "final_power": rnd["final_power1"],
            "critical_hit": rnd["critical_hit1"],
            "gold_reward": rewards["gold1"],
            "xp_reward": rewards["xp1"],
        },
        "player2": {
            "card": card2,
            "base_power": base2,
            "final_power": rnd["final_power2"],
            "critical_hit": rnd["critical_hit2"],
            "gold_reward": rewards["gold2"],
            "xp_reward": rewards["xp2"],
        },
        "power_difference": rnd["power_difference"],
        "wager": tier["wager_cost"],
        "wager_tier": wager_tier,
        "rounds": [rnd],
    }


# Backward-compatible name
execute_battle = resolve_match


# ══════════════════════════════════════════════════════════════════════════
# Phase 4 — lineup matches (best of three, genre ring, abilities, momentum)
# ══════════════════════════════════════════════════════════════════════════

def _side_breakdown(lineup: Lineup, slot: int, opp_family: GenreFamily, rng: random.Random) -> Dict[str, Any]:
    """
    Apply the locked order base -> momentum -> genre -> ability -> crit for one
    card and record every intermediate value.
    """
    card = lineup.cards[slot]
    fam = card_family(card)
    base = lineup.base_power(slot)

    m_mom = MOMENTUM_MULT if getattr(card, "momentum", False) else 1.0
    after_momentum = base * m_mom

    m_genre = genre_multiplier(fam, opp_family)
    after_genre = after_momentum * m_genre

    amped = lineup.ability == Ability.AMP and lineup.ability_slot == slot
    m_ability = AMP_MULT if amped else 1.0
    after_ability = after_genre * m_ability

    crit = roll_crit(rng)
    m_crit = CRITICAL_MULTIPLIER if crit else 1.0
    final = int(after_ability * m_crit)

    return {
        "card_id": getattr(card, "card_id", None),
        "name": getattr(card, "name", getattr(card, "artist", "Unknown")),
        "rarity": getattr(card, "rarity", "common"),
        "image_url": getattr(card, "image_url", ""),
        "youtube_url": getattr(card, "youtube_url", ""),
        "family": fam.value,
        "base": int(base),
        "after_momentum": int(after_momentum),
        "after_genre": int(after_genre),
        "after_ability": int(after_ability),
        "final": final,
        "momentum": m_mom != 1.0,
        "genre_effect": "counter" if m_genre > 1 else ("countered" if m_genre < 1 else "neutral"),
        "amped": amped,
        "critical_hit": crit,
        "multipliers": {"momentum": m_mom, "genre": m_genre, "ability": m_ability, "crit": m_crit},
    }


def _copy_lineup(src: Lineup) -> Lineup:
    return Lineup(list(src.cards), src.ability, src.ability_slot,
                  list(src.powers) if src.powers is not None else None, dict(src.meta))


def resolve_lineup_match(
    lineup1: Lineup,
    lineup2: Lineup,
    wager_tier: str = "casual",
    rng: Optional[random.Random] = None,
) -> Dict[str, Any]:
    """
    Best-of-three between two ordered lineups. Round N faces slot N.

    * Genre multiplier per round; crit rolled per round (P1 rolls first).
    * SWAP: a player who declared it and LOSES round 1 has slots 2/3 exchanged.
    * SCOUT is informational (handled before commit); recorded in `reveals`.
    * First to ROUNDS_TO_WIN. If nobody gets there after LINEUP_SIZE rounds
      (ties involved), round wins then total modified power decide; equal -> tie.
    * Rewards use the same tier table as the legacy single-card match.

    Deterministic given `rng`. The per-round breakdown is meant to be
    persisted: it drives the reveal animation and settles disputes.
    """
    if rng is None:
        rng = random.Random()

    # Work on copies so SWAP never mutates the caller's lineups.
    l1, l2 = _copy_lineup(lineup1), _copy_lineup(lineup2)

    reveals: List[Dict[str, Any]] = []
    for who, me, opp in ((1, l1, l2), (2, l2, l1)):
        if me.ability == Ability.SCOUT and me.ability_slot is not None:
            reveals.append({"player": who, "slot": me.ability_slot,
                            "family": card_family(opp.cards[me.ability_slot]).value})

    rounds: List[Dict[str, Any]] = []
    wins = [0, 0]
    totals = [0, 0]
    swaps: List[Dict[str, Any]] = []

    for slot in range(LINEUP_SIZE):
        fam1, fam2 = card_family(l1.cards[slot]), card_family(l2.cards[slot])
        b1 = _side_breakdown(l1, slot, fam2, rng)
        b2 = _side_breakdown(l2, slot, fam1, rng)
        diff = abs(b1["final"] - b2["final"])
        if diff < MIN_POWER_ADVANTAGE:
            winner = 0
        else:
            winner = 1 if b1["final"] > b2["final"] else 2
        if winner:
            wins[winner - 1] += 1
        totals[0] += b1["final"]
        totals[1] += b2["final"]
        rounds.append({"round": slot + 1, "winner": winner, "power_difference": diff,
                       "player1": b1, "player2": b2})

        if wins[0] >= ROUNDS_TO_WIN or wins[1] >= ROUNDS_TO_WIN:
            break

        if slot == 0 and LINEUP_SIZE >= 3:
            for who, me in ((1, l1), (2, l2)):
                if me.ability == Ability.SWAP and winner not in (0, who):
                    me.cards[1], me.cards[2] = me.cards[2], me.cards[1]
                    if me.powers is not None:
                        me.powers[1], me.powers[2] = me.powers[2], me.powers[1]
                    swaps.append({"player": who, "after_round": 1})

    if wins[0] >= ROUNDS_TO_WIN:
        match_winner, decided_by = 1, "rounds"
    elif wins[1] >= ROUNDS_TO_WIN:
        match_winner, decided_by = 2, "rounds"
    elif wins[0] != wins[1]:
        match_winner, decided_by = (1 if wins[0] > wins[1] else 2), "rounds"
    elif totals[0] != totals[1]:
        match_winner, decided_by = (1 if totals[0] > totals[1] else 2), "total_power"
    else:
        match_winner, decided_by = 0, "tie"

    tier = BattleWagerConfig.get_tier(wager_tier)
    rewards = _rewards(match_winner, tier)

    return {
        "format": "lineup_bo3",
        "winner": match_winner,
        "decided_by": decided_by,
        "rounds_won": wins,
        "total_power": totals,
        "rounds": rounds,
        "swaps": swaps,
        "reveals": reveals,
        "resolution_order": list(RESOLUTION_ORDER),
        "player1": {
            "lineup": l1.to_dict(),
            "gold_reward": rewards["gold1"], "xp_reward": rewards["xp1"],
        },
        "player2": {
            "lineup": l2.to_dict(),
            "gold_reward": rewards["gold2"], "xp_reward": rewards["xp2"],
        },
        "wager": tier["wager_cost"],
        "wager_tier": wager_tier,
    }

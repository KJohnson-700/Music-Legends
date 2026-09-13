"""
Battle resolver — pure functions, deterministic given an explicit RNG.

    rng = random.Random(seed)
    result = resolve_match(card1, card2, "casual", rng=rng)

Never touches the global `random` module, so the same seed always replays the
same match. Cards are duck-typed: anything with `.power` works, or pass
p1_override / p2_override to supply computed team power directly.
"""
import random
from typing import Any, Dict, Optional

from core.battle.config import (
    BattleWagerConfig,
    CRITICAL_HIT_CHANCE,
    CRITICAL_MULTIPLIER,
    MIN_POWER_ADVANTAGE,
    TIE_GOLD,
    TIE_XP,
)


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
    Resolve one head-to-head power comparison.

    Returns winner (0 tie / 1 / 2), final powers, crit flags, and the gap.
    Phase 4 will call this once per lineup slot; today a match is one round.
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
    Resolve a full match. Result shape is unchanged from the legacy
    BattleEngine.execute_battle() so existing callers keep working.

    `rng` should be passed explicitly (random.Random(seed)). If omitted a fresh
    unseeded Random is used — fine for production, useless for tests.
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

"""Battle tuning constants and wager tiers. Pure data — no imports beyond typing."""
from typing import Dict

# Resolution tuning
CRITICAL_HIT_CHANCE = 0.15   # 15% chance per card per round
CRITICAL_MULTIPLIER = 1.5    # crit = +50% power
MIN_POWER_ADVANTAGE = 5      # gaps smaller than this are a tie

# Tie consolation (both players)
TIE_GOLD = 25
TIE_XP = 10

# Phase 4 — lineups, genre ring, abilities, momentum
LINEUP_SIZE = 3            # ordered cards per player
MAX_SAME_FAMILY = 2        # deck-building constraint (NEUTRAL exempt)
ROUNDS_TO_WIN = 2          # best of three
GENRE_COUNTER_MULT = 1.35  # attacker's family beats defender's
GENRE_COUNTERED_MULT = 0.85
MOMENTUM_MULT = 1.10       # weekly top movers
MOMENTUM_TOP_FRACTION = 0.10
AMP_MULT = 1.20            # Amp ability: +20% to one declared slot

# Locked resolution order (4f). Persisted per round for disputes + reveal animation.
RESOLUTION_ORDER = ("base", "momentum", "genre", "ability", "crit")


class BattleWagerConfig:
    """Wager tiers: entry cost and rewards for winner / loser."""

    TIERS: Dict[str, Dict] = {
        "casual": {
            "name": "Casual", "wager_cost": 50,
            "winner_gold": 100, "loser_gold": 10,
            "winner_xp": 25, "loser_xp": 5,
            "emoji": "🎮",
        },
        "standard": {
            "name": "Standard", "wager_cost": 100,
            "winner_gold": 175, "loser_gold": 10,
            "winner_xp": 40, "loser_xp": 8,
            "emoji": "⚔️",
        },
        "high": {
            "name": "High Stakes", "wager_cost": 250,
            "winner_gold": 350, "loser_gold": 15,
            "winner_xp": 60, "loser_xp": 12,
            "emoji": "🔥",
        },
        "extreme": {
            "name": "Extreme", "wager_cost": 500,
            "winner_gold": 650, "loser_gold": 20,
            "winner_xp": 100, "loser_xp": 20,
            "emoji": "💀",
        },
    }

    @classmethod
    def get_tier(cls, tier_name: str) -> Dict:
        return cls.TIERS.get((tier_name or "").lower(), cls.TIERS["casual"])

    @classmethod
    def get_wager_cost(cls, tier_name: str) -> int:
        return cls.get_tier(tier_name)["wager_cost"]

    @classmethod
    def get_winner_reward(cls, tier_name: str) -> Dict:
        tier = cls.get_tier(tier_name)
        return {"gold": tier["winner_gold"], "xp": tier["winner_xp"]}

    @classmethod
    def get_loser_reward(cls, tier_name: str) -> Dict:
        tier = cls.get_tier(tier_name)
        return {"gold": tier["loser_gold"], "xp": tier["loser_xp"]}

"""Shared card constants used across cogs."""

# Canonical rarity emoji map — use this everywhere instead of defining inline
RARITY_EMOJI = {
    "common": "⚪",      # ⚪
    "rare": "🔵",     # 🔵
    "epic": "🟣",     # 🟣
    "legendary": "⭐",    # ⭐
    "mythic": "🔴",   # 🔴
}

# Battle power bonuses by rarity — canonical values live in core.power
from core.power import RARITY_BONUS, battle_power as _battle_power, team_power as _team_power  # noqa: E402

# Tier emoji map
TIER_EMOJI = {
    "community": "📦",  # 📦
    "gold": "🥇",       # 🥇
    "platinum": "💎",   # 💎
}


def compute_card_power(card: dict) -> int:
    """Battle power from card DB stats. Delegates to core.power.battle_power
    (Phase 3: log-compressed, range 70-135, display scale unchanged)."""
    return _battle_power(card)


def compute_team_power(champ_power: int, support_powers: list) -> int:
    """Weighted team power: champion counts double. Delegates to core.power."""
    return _team_power(champ_power, support_powers)

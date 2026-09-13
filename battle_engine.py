"""
battle_engine — backward-compatibility shim.

The battle system now lives in:
  core/battle/        pure logic (config, types, resolver, manager) — no discord
  adapters/           Discord embeds (adapters.discord_battle), TMA JSON (adapters.tma_battle)

Importing this module does NOT import discord. Discord-only helpers are
resolved lazily on attribute access so the Telegram backend never loads
discord.py through here.

Prefer importing from core.battle / adapters directly in new code.
"""
from core.battle import (  # noqa: F401
    BattleCard,
    BattleManager,
    BattleStatus,
    BattleWagerConfig,
    CardRef,
    MatchState,
    PlayerState,
    resolve_match,
    resolve_round,
    roll_crit,
)
from core.battle.config import (  # noqa: F401
    CRITICAL_HIT_CHANCE,
    CRITICAL_MULTIPLIER,
    MIN_POWER_ADVANTAGE,
)

execute_battle = resolve_match

_DISCORD_ONLY = {"create_battle_embed", "BattleHistory"}


def __getattr__(name: str):
    if name in _DISCORD_ONLY:
        from adapters import discord_battle
        return getattr(discord_battle, name)
    raise AttributeError(f"module 'battle_engine' has no attribute {name!r}")


__all__ = [
    "BattleCard", "BattleManager", "BattleStatus", "BattleWagerConfig", "CardRef",
    "MatchState", "PlayerState", "resolve_match", "resolve_round", "roll_crit",
    "execute_battle", "CRITICAL_HIT_CHANCE", "CRITICAL_MULTIPLIER", "MIN_POWER_ADVANTAGE",
    "create_battle_embed", "BattleHistory",
]

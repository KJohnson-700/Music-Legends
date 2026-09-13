"""Battle domain: config, state types, resolver, and match manager (pure logic)."""
from core.battle.config import (
    BattleWagerConfig,
    CRITICAL_HIT_CHANCE,
    CRITICAL_MULTIPLIER,
    MIN_POWER_ADVANTAGE,
    TIE_GOLD,
    TIE_XP,
)
from core.battle.types import BattleCard, BattleStatus, CardRef, MatchState, PlayerState
from core.battle.resolver import resolve_match, resolve_round, roll_crit
from core.battle.manager import BattleManager

__all__ = [
    "BattleWagerConfig", "CRITICAL_HIT_CHANCE", "CRITICAL_MULTIPLIER",
    "MIN_POWER_ADVANTAGE", "TIE_GOLD", "TIE_XP",
    "BattleCard", "BattleStatus", "CardRef", "MatchState", "PlayerState",
    "resolve_match", "resolve_round", "roll_crit",
    "BattleManager",
]

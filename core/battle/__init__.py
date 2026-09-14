"""Battle domain: config, state types, genre ring, lineups, resolver, manager (pure logic)."""
from core.battle.config import (
    BattleWagerConfig,
    CRITICAL_HIT_CHANCE,
    CRITICAL_MULTIPLIER,
    MIN_POWER_ADVANTAGE,
    TIE_GOLD,
    TIE_XP,
)
from core.battle.types import BattleCard, BattleStatus, CardRef, MatchState, PlayerState
from core.battle.genre import GenreFamily, GENRE_RING, TAG_TO_FAMILY, genre_multiplier, resolve_family
from core.battle.lineup import Ability, Lineup, LineupError, auto_lineup, validate_lineup
from core.battle.momentum import hot_card_ids, momentum_threshold, view_delta
from core.battle.resolver import resolve_lineup_match, resolve_match, resolve_round, roll_crit
from core.battle.manager import BattleManager

__all__ = [
    "BattleWagerConfig", "CRITICAL_HIT_CHANCE", "CRITICAL_MULTIPLIER",
    "MIN_POWER_ADVANTAGE", "TIE_GOLD", "TIE_XP",
    "BattleCard", "BattleStatus", "CardRef", "MatchState", "PlayerState",
    "GenreFamily", "GENRE_RING", "TAG_TO_FAMILY", "genre_multiplier", "resolve_family",
    "Ability", "Lineup", "LineupError", "auto_lineup", "validate_lineup",
    "hot_card_ids", "momentum_threshold", "view_delta",
    "resolve_match", "resolve_round", "roll_crit", "resolve_lineup_match",
    "BattleManager",
]

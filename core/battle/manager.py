"""
In-memory match registry.

Known limitation (Phase 2 fixes it): state lives in process memory, so it is
lost on restart and not shared across uvicorn workers.
"""
from typing import Dict, Optional

from core.battle.types import MatchState, PlayerState


class BattleManager:
    def __init__(self):
        self.active_matches: Dict[str, MatchState] = {}
        self.user_to_match: Dict[str, str] = {}

    def create_match(self, match_id: str, player1_id: str, player1_name: str,
                     player2_id: str, player2_name: str,
                     wager_tier: str = "casual") -> MatchState:
        match = MatchState(match_id, PlayerState(player1_id, player1_name),
                           PlayerState(player2_id, player2_name), wager_tier)
        self.active_matches[match_id] = match
        self.user_to_match[player1_id] = match_id
        self.user_to_match[player2_id] = match_id
        return match

    def get_match(self, match_id: str) -> Optional[MatchState]:
        return self.active_matches.get(match_id)

    def get_user_match(self, user_id: str) -> Optional[MatchState]:
        match_id = self.user_to_match.get(user_id)
        return self.active_matches.get(match_id) if match_id else None

    def is_user_in_battle(self, user_id: str) -> bool:
        return user_id in self.user_to_match

    def complete_match(self, match_id: str):
        match = self.active_matches.pop(match_id, None)
        if match:
            self.user_to_match.pop(match.player1.user_id, None)
            self.user_to_match.pop(match.player2.user_id, None)

    def get_active_count(self) -> int:
        return len(self.active_matches)

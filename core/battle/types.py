"""
Battle state types. Pure data — no platform imports.

CardRef is the minimal card shape the resolver needs. Anything with a `.power`
attribute works as a card (duck-typed), so discord_cards.ArtistCard still fits
without core/ importing it.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from core.battle.config import BattleWagerConfig, TIE_GOLD, TIE_XP

_RARITY_EMOJI = {
    "common": "⚪", "rare": "🔵", "epic": "🟣",
    "legendary": "🟡", "mythic": "🔴", "ultra_mythic": "💎",
}


@dataclass
class CardRef:
    """Platform-neutral card reference used by battle code and adapters."""
    card_id: str
    name: str
    title: str = ""
    rarity: str = "common"
    power: int = 0
    image_url: str = ""
    youtube_url: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.rarity = (self.rarity or "common").lower()

    # Aliases so adapters written against ArtistCard keep working
    @property
    def artist(self) -> str:
        return self.name

    @property
    def song(self) -> str:
        return self.title

    def get_rarity_emoji(self) -> str:
        return _RARITY_EMOJI.get(self.rarity, "⚪")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_db_card(cls, card: Dict[str, Any], power: Optional[int] = None) -> "CardRef":
        """Build from a DB card dict (as returned by DatabaseManager)."""
        return cls(
            card_id=str(card.get("card_id", "") or ""),
            name=card.get("name", "Unknown") or "Unknown",
            title=card.get("title", "") or "",
            rarity=card.get("rarity", "common") or "common",
            power=int(power if power is not None else card.get("power", 0) or 0),
            image_url=card.get("image_url", "") or "",
            youtube_url=card.get("youtube_url", "") or "",
        )


class BattleCard:
    """Wraps any card-like object (has `.power`) with per-battle mutable state."""

    def __init__(self, card: Any, owner_id: str, owner_name: str):
        self.card = card
        self.owner_id = owner_id
        self.owner_name = owner_name
        self.base_power = int(getattr(card, "power", 0))
        self.final_power = self.base_power
        self.critical_hit = False
        self.power_modifier = 1.0

    def apply_critical_hit(self, multiplier: float = 1.5):
        self.critical_hit = True
        self.final_power = int(self.base_power * multiplier)

    def apply_power_modifier(self, modifier: float):
        self.power_modifier = modifier
        self.final_power = int(self.base_power * modifier)

    def reset(self):
        self.final_power = self.base_power
        self.critical_hit = False
        self.power_modifier = 1.0

    def to_dict(self) -> Dict:
        card = self.card
        card_dict = card.to_dict() if hasattr(card, "to_dict") else {"power": self.base_power}
        return {
            "card": card_dict,
            "owner_id": self.owner_id,
            "owner_name": self.owner_name,
            "base_power": self.base_power,
            "final_power": self.final_power,
            "critical_hit": self.critical_hit,
            "power_modifier": self.power_modifier,
        }

    @classmethod
    def from_artist_card(cls, artist_card: Any, owner_id: str, owner_name: str) -> "BattleCard":
        return cls(artist_card, owner_id, owner_name)

    def __repr__(self):
        crit_tag = " [CRIT]" if self.critical_hit else ""
        name = getattr(self.card, "artist", getattr(self.card, "name", "?"))
        song = getattr(self.card, "song", getattr(self.card, "title", ""))
        return f"<BattleCard: {name} - {song} ({self.final_power} PWR{crit_tag})>"


class PlayerState:
    """A participant's state within a match."""

    def __init__(self, user_id: str, username: str, card: Optional[BattleCard] = None):
        self.user_id = user_id
        self.username = username
        self.card = card
        self.is_ready = False
        self.has_accepted = False
        self.gold_wagered = 0
        self.gold_reward = 0
        self.xp_reward = 0
        self.won = False

    def set_card(self, card: BattleCard):
        self.card = card
        self.is_ready = True

    def accept_battle(self, wager_amount: int):
        self.has_accepted = True
        self.gold_wagered = wager_amount

    def set_rewards(self, gold: int, xp: int, won: bool):
        self.gold_reward = gold
        self.xp_reward = xp
        self.won = won

    def to_dict(self) -> Dict:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "card": self.card.to_dict() if self.card else None,
            "is_ready": self.is_ready,
            "has_accepted": self.has_accepted,
            "gold_wagered": self.gold_wagered,
            "gold_reward": self.gold_reward,
            "xp_reward": self.xp_reward,
            "won": self.won,
        }

    def __repr__(self):
        ready = "ready" if self.is_ready else "not-ready"
        return f"<PlayerState: {self.username} [{ready}]>"


class BattleStatus(Enum):
    PENDING = "pending"
    SELECTING = "selecting"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class MatchState:
    """Complete state of one match between two players."""

    def __init__(self, match_id: str, player1: PlayerState, player2: PlayerState,
                 wager_tier: str = "casual"):
        self.match_id = match_id
        self.player1 = player1
        self.player2 = player2
        self.wager_tier = wager_tier
        self.status = BattleStatus.PENDING
        self.created_at = datetime.now()
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self.winner_id: Optional[str] = None
        self.is_tie = False
        self.power_difference = 0
        self.wager_config = BattleWagerConfig.get_tier(wager_tier)

    def accept_battle(self, user_id: str) -> bool:
        """Returns True once both players have accepted."""
        if user_id == self.player1.user_id:
            self.player1.accept_battle(self.wager_config["wager_cost"])
        elif user_id == self.player2.user_id:
            self.player2.accept_battle(self.wager_config["wager_cost"])
        else:
            return False
        if self.player1.has_accepted and self.player2.has_accepted:
            self.status = BattleStatus.SELECTING
            return True
        return False

    def set_player_card(self, user_id: str, card: BattleCard) -> bool:
        """Returns True once both players have a card."""
        if user_id == self.player1.user_id:
            self.player1.set_card(card)
        elif user_id == self.player2.user_id:
            self.player2.set_card(card)
        else:
            return False
        if self.player1.is_ready and self.player2.is_ready:
            self.status = BattleStatus.IN_PROGRESS
            self.started_at = datetime.now()
            return True
        return False

    def complete_battle(self, winner_id: Optional[str], is_tie: bool, power_diff: int):
        self.status = BattleStatus.COMPLETED
        self.completed_at = datetime.now()
        self.winner_id = winner_id
        self.is_tie = is_tie
        self.power_difference = power_diff

        if is_tie:
            self.player1.set_rewards(TIE_GOLD, TIE_XP, False)
            self.player2.set_rewards(TIE_GOLD, TIE_XP, False)
            return

        win = BattleWagerConfig.get_winner_reward(self.wager_tier)
        lose = BattleWagerConfig.get_loser_reward(self.wager_tier)
        if winner_id == self.player1.user_id:
            winner, loser = self.player1, self.player2
        else:
            winner, loser = self.player2, self.player1
        winner.set_rewards(win["gold"], win["xp"], True)
        loser.set_rewards(lose["gold"], lose["xp"], False)

    def cancel(self):
        """Cancel — refund wagers."""
        self.status = BattleStatus.CANCELLED
        self.player1.gold_reward = self.player1.gold_wagered
        self.player2.gold_reward = self.player2.gold_wagered

    def expire(self):
        """Timed out — refund wagers."""
        self.status = BattleStatus.EXPIRED
        self.player1.gold_reward = self.player1.gold_wagered
        self.player2.gold_reward = self.player2.gold_wagered

    def get_winner(self) -> Optional[PlayerState]:
        if self.winner_id == self.player1.user_id:
            return self.player1
        if self.winner_id == self.player2.user_id:
            return self.player2
        return None

    def get_loser(self) -> Optional[PlayerState]:
        if self.winner_id == self.player1.user_id:
            return self.player2
        if self.winner_id == self.player2.user_id:
            return self.player1
        return None

    def to_dict(self) -> Dict:
        return {
            "match_id": self.match_id,
            "player1": self.player1.to_dict(),
            "player2": self.player2.to_dict(),
            "wager_tier": self.wager_tier,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "winner_id": self.winner_id,
            "is_tie": self.is_tie,
            "power_difference": self.power_difference,
        }

    def __repr__(self):
        return f"<MatchState: {self.player1.username} vs {self.player2.username} ({self.status.value})>"

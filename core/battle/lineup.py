"""
Lineups and abilities (Phase 4c/4d). Pure logic.

A lineup is three ordered cards. Round N faces slot N. The ordering guess
under incomplete information is the game.

Constraint: at most MAX_SAME_FAMILY cards from one family. NEUTRAL is exempt
(it means "no genre data", and blocking players over missing data would be
punishing the wrong thing).

Abilities — one per battle, chosen at lineup time, single use:
  SWAP  — if you LOSE round 1, slots 2 and 3 are exchanged before round 2.
          (Reactive by design: an unconditional swap is just a different order.)
  AMP   — +20% to one declared slot, applied before crit.
  SCOUT — reveals the FAMILY (not the card) of one of the opponent's slots
          before you commit. No numeric effect; the reveal is recorded.
"""
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

from core.battle.config import LINEUP_SIZE, MAX_SAME_FAMILY
from core.battle.genre import GenreFamily
from core.battle.types import CardRef


class Ability(str, Enum):
    SWAP = "swap"
    AMP = "amp"
    SCOUT = "scout"

    @classmethod
    def parse(cls, value: Optional[str]) -> Optional["Ability"]:
        if value is None or value == "":
            return None
        if isinstance(value, Ability):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            raise LineupError(f"Unknown ability {value!r}; choose one of swap, amp, scout")


class LineupError(ValueError):
    """Raised when a lineup violates the rules. Message is safe to show to players."""


def card_family(card: Any) -> GenreFamily:
    return GenreFamily.parse(getattr(card, "genre_family", None))


def validate_lineup(cards: Sequence[Any], ability: Optional[Ability] = None,
                    ability_slot: Optional[int] = None) -> None:
    if len(cards) != LINEUP_SIZE:
        raise LineupError(f"A lineup needs exactly {LINEUP_SIZE} cards (got {len(cards)})")
    ids = [str(getattr(c, "card_id", "")) for c in cards]
    if len(set(ids)) != len(ids) or "" in ids:
        raise LineupError("Each lineup slot must hold a different card")
    fams = Counter(card_family(c) for c in cards)
    fams.pop(GenreFamily.NEUTRAL, None)
    for fam, n in fams.items():
        if n > MAX_SAME_FAMILY:
            raise LineupError(f"At most {MAX_SAME_FAMILY} cards may share a family ({fam.value} has {n})")
    if ability in (Ability.AMP, Ability.SCOUT):
        if ability_slot is None or not (0 <= int(ability_slot) < LINEUP_SIZE):
            raise LineupError(f"{ability.value} needs a slot between 1 and {LINEUP_SIZE}")


@dataclass
class Lineup:
    cards: List[CardRef]
    ability: Optional[Ability] = None
    ability_slot: Optional[int] = None  # 0-based; AMP = own slot, SCOUT = opponent slot
    powers: Optional[List[int]] = None  # optional overrides (e.g. team-weighted power)
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.ability = Ability.parse(self.ability) if not isinstance(self.ability, Ability) else self.ability
        validate_lineup(self.cards, self.ability, self.ability_slot)
        if self.powers is not None and len(self.powers) != len(self.cards):
            raise LineupError("powers must match the number of cards")

    def base_power(self, slot: int) -> int:
        if self.powers is not None:
            return int(self.powers[slot])
        return int(getattr(self.cards[slot], "power", 0))

    def families(self) -> List[GenreFamily]:
        return [card_family(c) for c in self.cards]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cards": [c.to_dict() if hasattr(c, "to_dict") else {"card_id": getattr(c, "card_id", None)}
                      for c in self.cards],
            "ability": self.ability.value if self.ability else None,
            "ability_slot": self.ability_slot,
        }


def auto_lineup(cards: Sequence[CardRef], size: int = LINEUP_SIZE,
                lead: Optional[CardRef] = None) -> List[CardRef]:
    """
    Greedy default for clients that don't pick: strongest cards first, skipping
    any that would break the family constraint. `lead` (a chosen champion) is
    pinned to slot 1. Raises LineupError if fewer than `size` eligible cards exist.
    """
    ordered = sorted(cards, key=lambda c: int(getattr(c, "power", 0)), reverse=True)
    chosen: List[CardRef] = []
    seen_ids = set()
    fams: Counter = Counter()
    if lead is not None:
        chosen.append(lead)
        seen_ids.add(str(getattr(lead, "card_id", "")))
        fams[card_family(lead)] += 1
    for c in ordered:
        cid = str(getattr(c, "card_id", ""))
        if not cid or cid in seen_ids:
            continue
        fam = card_family(c)
        if fam != GenreFamily.NEUTRAL and fams[fam] >= MAX_SAME_FAMILY:
            continue
        chosen.append(c)
        seen_ids.add(cid)
        fams[fam] += 1
        if len(chosen) == size:
            break
    if len(chosen) < size:
        raise LineupError(f"Need at least {size} distinct cards to battle")
    return chosen

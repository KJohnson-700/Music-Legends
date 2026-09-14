"""
Crafting / duplicate fusion (Phase 4.5). Pure rules, no I/O.

Combine four cards of the same rarity for a probabilistic roll at the next
rarity up. Inputs are consumed win or lose. On failure one card of the INPUT
rarity comes back (a floor, so crafting never feels like pure theft).

Genre: **Inherit** — if all four inputs share a family, the output is
guaranteed that family (a deck-building tool that pairs with the 2-per-family
lineup rule). Mixed inputs → random family.

Supply: crafted cards draw from a separate *crafted allocation* per rarity
(CRAFT_CAPS), not the season pack pool, so fusion can never quietly inflate
the top of the rarity curve past a known ceiling.

The Stars boost (+BOOST_PCT points, capped at MAX_SUCCESS_PCT) is the second
Stars sink besides packs. It is charged before the roll and refunded if the
craft transaction itself fails.
"""
import random
from collections import Counter
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

RARITY_ORDER: Tuple[str, ...] = ("common", "rare", "epic", "legendary", "mythic")
INPUTS_REQUIRED = 4

# Base success by TARGET rarity — scales inversely with how good the prize is.
BASE_SUCCESS_PCT: Dict[str, int] = {"rare": 65, "epic": 50, "legendary": 35, "mythic": 20}
BOOST_PCT = 25
MAX_SUCCESS_PCT = 95

# Stars price of a boost, by target rarity (the sink).
BOOST_STARS: Dict[str, int] = {"rare": 15, "epic": 30, "legendary": 60, "mythic": 120}

# Crafted allocation per rarity per season (separate from pack supply).
CRAFT_CAPS: Dict[str, int] = {"rare": 20_000, "epic": 5_000, "legendary": 1_000, "mythic": 150}


class CraftError(ValueError):
    """Rule violation; message is safe to show to players."""


def normalize_rarity(r: Optional[str]) -> str:
    r = (r or "common").strip().lower()
    return r if r in RARITY_ORDER else "common"


def next_rarity(rarity: str) -> Optional[str]:
    r = normalize_rarity(rarity)
    i = RARITY_ORDER.index(r)
    return RARITY_ORDER[i + 1] if i + 1 < len(RARITY_ORDER) else None


def validate_inputs(rarities: Sequence[str]) -> Tuple[str, str]:
    """Returns (input_rarity, target_rarity) or raises CraftError."""
    if len(rarities) != INPUTS_REQUIRED:
        raise CraftError(f"Crafting needs exactly {INPUTS_REQUIRED} cards (got {len(rarities)})")
    norm = {normalize_rarity(r) for r in rarities}
    if len(norm) != 1:
        raise CraftError("All four cards must be the same rarity")
    input_rarity = next(iter(norm))
    target = next_rarity(input_rarity)
    if target is None:
        raise CraftError(f"{input_rarity.title()} is already the highest rarity")
    return input_rarity, target


def success_pct(target_rarity: str, boosted: bool = False) -> int:
    base = BASE_SUCCESS_PCT.get(normalize_rarity(target_rarity))
    if base is None:
        raise CraftError(f"No craft rate for target {target_rarity!r}")
    return min(MAX_SUCCESS_PCT, base + (BOOST_PCT if boosted else 0))


def boost_stars(target_rarity: str) -> int:
    return BOOST_STARS[normalize_rarity(target_rarity)]


def output_family(families: Iterable[Optional[str]]) -> Optional[str]:
    """Inherit rule: one shared non-NEUTRAL family → that family; otherwise None (random)."""
    fams = {(f or "NEUTRAL").upper() for f in families}
    if len(fams) == 1:
        fam = next(iter(fams))
        return None if fam == "NEUTRAL" else fam
    return None


def roll(rng: random.Random, pct: int) -> Tuple[bool, float]:
    """One craft roll. Deterministic given rng. Returns (success, roll_value in [0,1))."""
    value = rng.random()
    return value < (max(0, min(100, int(pct))) / 100.0), value


def pick_floor_index(rng: random.Random, n: int = INPUTS_REQUIRED) -> int:
    return rng.randrange(n)


def cap_remaining(target_rarity: str, crafted_so_far: int) -> int:
    cap = CRAFT_CAPS.get(normalize_rarity(target_rarity), 0)
    return max(0, cap - max(0, int(crafted_so_far)))


def input_counts(card_ids: Sequence[str]) -> Dict[str, int]:
    """How many copies of each card the craft consumes (duplicates are the point)."""
    return dict(Counter(str(c) for c in card_ids))

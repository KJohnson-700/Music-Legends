"""
Card power — the single source of truth for how a card's numbers become
battle power. Pure functions, no I/O, no platform imports.

Phase 3 (log-compressed power)
------------------------------
Before: power = mean(5 stats) + rarity bonus, range 10–135. Real cards spanned
roughly 13x from weakest to strongest, so any percentage multiplier (genre
counters, abilities) was decoration.

Now: power = POWER_FLOOR + STAT_SCALE * mean(5 stats) + rarity bonus.
Range 70–135. Strongest/weakest ratio < 2x, and a 35% multiplier flips a
matchup between cards two rarity tiers apart but not between the extremes.
The display scale stays 0–135 so no UI changes are needed.

View counts map to stats on a log scale (stat_from_views) instead of four
random bands, so a 5M-view song and a 2B-view song differ meaningfully but
not absurdly.
"""
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional

STAT_KEYS = ("impact", "skill", "longevity", "culture", "hype")
STAT_DEFAULT = 50
STAT_MIN = 0
STAT_MAX = 100

# Battle-power shape (display scale 0–135 preserved)
POWER_FLOOR = 70          # an all-zero common still scores this
STAT_SCALE = 0.40         # 100 stat points → +40 power
RARITY_BONUS: Dict[str, int] = {
    "common": 0,
    "rare": 4,
    "epic": 8,
    "legendary": 15,
    "mythic": 25,
}
POWER_MAX = POWER_FLOOR + round(STAT_SCALE * STAT_MAX) + max(RARITY_BONUS.values())  # 135
RARITY_ORDER = ("common", "rare", "epic", "legendary", "mythic")

# Tier labels on the compressed scale (used by Discord/TMA presentation)
POWER_TIER_THRESHOLDS = (
    (125, "S"),
    (115, "A"),
    (105, "B"),
    (95, "C"),
    (85, "D"),
)

# View-count → stat (0–100), log10 scale.
#   5M views  → ~35    100M → ~65    1B → ~88    2B → ~95
_VIEW_LOG_SLOPE = 23.0
_VIEW_LOG_OFFSET = -119.0
VIEW_STAT_MIN = 20


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def normalize_rarity(rarity: Optional[str]) -> str:
    r = (rarity or "common").strip().lower()
    return r if r in RARITY_BONUS else "common"


def avg_stat(card: Mapping[str, Any]) -> int:
    """Integer mean of the five stats; missing/None stats count as STAT_DEFAULT."""
    total = 0
    for key in STAT_KEYS:
        v = card.get(key, STAT_DEFAULT)
        total += int(v) if v is not None else STAT_DEFAULT
    return total // len(STAT_KEYS)


def power_from_avg(avg: int, rarity: Optional[str]) -> int:
    """Compressed battle power from a mean stat and rarity."""
    avg = int(clamp(avg, STAT_MIN, STAT_MAX))
    return POWER_FLOOR + round(STAT_SCALE * avg) + RARITY_BONUS[normalize_rarity(rarity)]


def battle_power(card: Mapping[str, Any]) -> int:
    """Battle power for a card dict as stored in the DB."""
    return power_from_avg(avg_stat(card), card.get("rarity"))


def team_power(champ_power: int, support_powers: Iterable[int]) -> int:
    """Weighted team power: champion counts double."""
    supports: List[int] = list(support_powers)
    if not supports:
        return int(champ_power)
    return (int(champ_power) * 2 + sum(supports)) // (2 + len(supports))


def power_tier(power: int) -> str:
    """Letter tier for a compressed power value (S/A/B/C/D/E)."""
    for threshold, label in POWER_TIER_THRESHOLDS:
        if power >= threshold:
            return label
    return "E"


def stat_from_views(views: int) -> int:
    """
    Map a YouTube view count to a 0–100 stat on a log scale.
    Deterministic; callers may add small jitter for variety.
    """
    v = max(int(views or 0), 1)
    raw = _VIEW_LOG_SLOPE * math.log10(v) + _VIEW_LOG_OFFSET
    return int(clamp(round(raw), VIEW_STAT_MIN, STAT_MAX))


def power_spread(cards: Iterable[Mapping[str, Any]]) -> Dict[str, float]:
    """Diagnostics: min/max/ratio of battle power across a card set."""
    powers = [battle_power(c) for c in cards]
    if not powers:
        return {"count": 0, "min": 0, "max": 0, "ratio": 0.0}
    lo, hi = min(powers), max(powers)
    return {"count": len(powers), "min": lo, "max": hi, "ratio": hi / lo if lo else float("inf")}

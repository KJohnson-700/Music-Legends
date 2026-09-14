"""
Momentum (Phase 4e). Pure helpers; the weekly view refresh lives in the
scheduler, not here.

Each card stores a weekly `view_delta`. The top MOMENTUM_TOP_FRACTION of
movers are "hot" and get MOMENTUM_MULT in battle. The meta shifts on its own
when a song blows up.
"""
import math
from typing import Dict, Iterable, Mapping, Optional, Set, Tuple

from core.battle.config import MOMENTUM_TOP_FRACTION


def momentum_threshold(deltas: Iterable[int], fraction: float = MOMENTUM_TOP_FRACTION) -> Optional[int]:
    """
    Smallest delta that still counts as "top fraction" of movers.
    Only positive deltas qualify. Returns None when nothing qualifies.
    """
    positive = sorted((int(d) for d in deltas if d is not None and int(d) > 0), reverse=True)
    if not positive:
        return None
    k = max(1, math.ceil(len(positive) * fraction))
    return positive[k - 1]


def hot_card_ids(deltas_by_card: Mapping[str, int], fraction: float = MOMENTUM_TOP_FRACTION) -> Set[str]:
    thr = momentum_threshold(deltas_by_card.values(), fraction)
    if thr is None:
        return set()
    return {cid for cid, d in deltas_by_card.items() if d is not None and int(d) >= thr}


def view_delta(previous_views: Optional[int], current_views: Optional[int]) -> int:
    if previous_views is None or current_views is None:
        return 0
    return max(0, int(current_views) - int(previous_views))

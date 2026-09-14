"""
Momentum refresh (Phase 4e).

Weekly job: fetch current YouTube view counts for every card with a video,
store the delta against last week's count, and flag the top 10% movers as
"hot" (they get +10% power in battle, see core.battle.momentum).

Run manually:  python scripts/refresh_momentum.py
Scheduled by:  tma/api/main.py when ENABLE_MOMENTUM_JOB=true (Mondays 03:00 UTC)
"""
import logging
from datetime import datetime
from typing import Dict, List

from core.battle.momentum import hot_card_ids, view_delta

logger = logging.getLogger(__name__)

BATCH = 50  # YouTube videos.list accepts up to 50 ids per call


def _video_id(youtube_url: str) -> str:
    try:
        from youtube_integration import YouTubeIntegration
        return YouTubeIntegration.extract_video_id_from_url(YouTubeIntegration(api_key="x"), youtube_url) or ""
    except Exception:
        return ""


def refresh_momentum(db, limit: int = 5000, dry_run: bool = False) -> Dict[str, int]:
    """Returns summary counts. Safe to re-run; deltas are relative to the last stored count."""
    from youtube_integration import YouTubeIntegration
    from config import settings

    api_key = getattr(settings, "YOUTUBE_API_KEY", None)
    if not api_key:
        logger.warning("[MOMENTUM] YOUTUBE_API_KEY missing — skipping refresh")
        return {"skipped": 1}

    yt = YouTubeIntegration(api_key=api_key)
    cards = db.get_cards_with_youtube(limit=limit)
    by_video: Dict[str, List[dict]] = {}
    for c in cards:
        vid = _video_id(c.get("youtube_url") or "")
        if vid:
            by_video.setdefault(vid, []).append(c)

    ids = list(by_video)
    fetched: Dict[str, int] = {}
    for i in range(0, len(ids), BATCH):
        fetched.update(yt.get_video_view_counts(ids[i:i + BATCH]))

    now = datetime.utcnow()
    updated = 0
    deltas: Dict[str, int] = {}
    for vid, views in fetched.items():
        for c in by_video[vid]:
            delta = view_delta(c.get("view_count"), views)
            deltas[c["card_id"]] = delta
            if not dry_run:
                db.update_card_views(c["card_id"], views, delta, now)
            updated += 1

    # Cards not fetched this run keep their stored delta so one API hiccup
    # doesn't wipe the meta.
    for c in cards:
        deltas.setdefault(c["card_id"], int(c.get("view_delta") or 0))

    hot = hot_card_ids(deltas)
    if not dry_run:
        db.set_momentum_flags(hot)
    logger.info(f"[MOMENTUM] cards={len(cards)} fetched={len(fetched)} updated={updated} hot={len(hot)}")
    return {"cards": len(cards), "fetched": len(fetched), "updated": updated, "hot": len(hot)}

"""
Genre family resolution (Phase 4a).

Resolved ONCE at card creation (or by the backfill job), never at battle time.
Chain: Last.fm track top tags → Last.fm artist tags → AudioDB strGenre → NEUTRAL.

Network calls are rate-limited (Last.fm asks for ~5 req/s) and cached per
artist within the process so a 5-card pack from one artist costs one lookup.
Everything degrades to NEUTRAL when keys are missing or the APIs fail — a card
must never fail to be created because a genre lookup did.
"""
import logging
import os
import threading
import time
from typing import Dict, Optional, Tuple

from core.battle.genre import GenreFamily, resolve_family

logger = logging.getLogger(__name__)

MIN_INTERVAL_SECONDS = float(os.environ.get("GENRE_LOOKUP_MIN_INTERVAL", "0.25"))
RESOLVE_ON_CREATE = os.environ.get("GENRE_RESOLVE_ON_CREATE", "true").lower() in ("1", "true", "yes")

_lock = threading.Lock()
_last_call = 0.0
_artist_cache: Dict[str, Tuple[GenreFamily, str]] = {}


def _throttle():
    global _last_call
    with _lock:
        wait = MIN_INTERVAL_SECONDS - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


def _lastfm():
    try:
        from lastfm_integration import LastFmIntegration
        client = LastFmIntegration()
        return client if client.api_key else None
    except Exception as e:  # pragma: no cover - import/env issues
        logger.debug(f"[GENRE] Last.fm unavailable: {e}")
        return None


def _audiodb():
    try:
        from audiodb_integration import AudioDBIntegration
        return AudioDBIntegration()
    except Exception as e:  # pragma: no cover
        logger.debug(f"[GENRE] AudioDB unavailable: {e}")
        return None


def _track_tags(lastfm, artist: str, title: str):
    if not (lastfm and artist and title):
        return []
    _throttle()
    info = lastfm.get_track_info(title, artist) or {}
    return info.get("tags") or []


def _artist_tags(lastfm, artist: str):
    if not (lastfm and artist):
        return []
    _throttle()
    info = lastfm.get_artist_info(artist) or {}
    return info.get("tags") or []


def _audiodb_genre(audiodb, artist: str) -> Optional[str]:
    if not (audiodb and artist):
        return None
    _throttle()
    try:
        results = audiodb.search_artist(artist, limit=1) or []
    except Exception as e:
        logger.debug(f"[GENRE] AudioDB search failed for {artist!r}: {e}")
        return None
    return (results[0].get("genre") if results else None) or None


def resolve_card_genre(artist: Optional[str], title: Optional[str] = None,
                       use_cache: bool = True) -> Tuple[GenreFamily, str]:
    """
    Returns (family, source) where source is one of
    'lastfm_track', 'lastfm_artist', 'audiodb', 'none'.
    """
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist:
        return GenreFamily.NEUTRAL, "none"

    lastfm = _lastfm()
    audiodb = _audiodb()

    try:
        fam = resolve_family(track_tags=_track_tags(lastfm, artist, title))
        if fam != GenreFamily.NEUTRAL:
            return fam, "lastfm_track"
    except Exception as e:
        logger.debug(f"[GENRE] track lookup failed for {artist!r}/{title!r}: {e}")

    key = artist.lower()
    if use_cache and key in _artist_cache:
        return _artist_cache[key]

    result = (GenreFamily.NEUTRAL, "none")
    try:
        fam = resolve_family(artist_tags=_artist_tags(lastfm, artist))
        if fam != GenreFamily.NEUTRAL:
            result = (fam, "lastfm_artist")
        else:
            fam = resolve_family(audiodb_genre=_audiodb_genre(audiodb, artist))
            if fam != GenreFamily.NEUTRAL:
                result = (fam, "audiodb")
    except Exception as e:
        logger.debug(f"[GENRE] artist lookup failed for {artist!r}: {e}")

    if use_cache:
        _artist_cache[key] = result
    return result


def backfill_genres(db, limit: int = 200, dry_run: bool = False) -> Dict[str, int]:
    """
    Resolve genre for cards still marked NEUTRAL with no recorded source.
    Rate-limited by the throttle above; ~200 cards ≈ 1–2 minutes.
    Returns counts per resolved family for logging.
    """
    cards = db.get_cards_missing_genre(limit=limit)
    counts: Dict[str, int] = {}
    for card in cards:
        fam, source = resolve_card_genre(card.get("artist_name") or card.get("name"), card.get("title"))
        counts[fam.value] = counts.get(fam.value, 0) + 1
        if not dry_run:
            db.set_card_genre(card["card_id"], fam.value, source)
    logger.info(f"[GENRE] backfill processed={len(cards)} counts={counts}")
    return counts

"""
Backfill genre_family for cards that have never been resolved.
Rate-limited (Last.fm ~4 req/s). Run in batches; safe to re-run.

    python scripts/backfill_genres.py --limit 200
    python scripts/backfill_genres.py --limit 50 --dry-run
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from database import get_db
    from services.genre_resolver import backfill_genres

    db = get_db()
    counts = backfill_genres(db, limit=args.limit, dry_run=args.dry_run)
    print("resolved:", counts)
    print("distribution now:", db.genre_distribution())


if __name__ == "__main__":
    main()

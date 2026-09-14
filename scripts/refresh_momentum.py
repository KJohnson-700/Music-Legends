"""
Weekly momentum refresh: pull YouTube view counts, store deltas, flag top 10% movers.

    python scripts/refresh_momentum.py
    python scripts/refresh_momentum.py --dry-run
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5000)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from database import get_db
    from services.momentum_service import refresh_momentum

    print(refresh_momentum(get_db(), limit=args.limit, dry_run=args.dry_run))


if __name__ == "__main__":
    main()

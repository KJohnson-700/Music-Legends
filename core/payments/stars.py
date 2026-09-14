"""
Telegram Stars economics (Phase 5). Pure functions, integer math.

Why Stars: Apple/Google policy — digital goods sold inside a Mini App must go
through Stars; fiat can't be charged directly. Stripe stays for Discord.

Numbers (docs/REFACTOR_PLAN.md, Phase 5):
  * a user pays roughly 2¢ per Star (iOS list price: 1,000 Stars ≈ $20)
  * the developer nets roughly 1.33¢ per Star after Telegram/Apple/Google
    (1,000 Stars → about $13.30, withdrawn via Fragment as TON)

**Host revenue share is computed on NET, not gross.** The Stars haircut is
shared proportionally, never taken from the platform side alone.
"""
from typing import Dict, Optional

# Price parity: USD list price → Stars at 2¢/Star, rounded to a friendly number.
USER_CENTS_PER_STAR = 2
# Developer net per 100 Stars, in cents (1.33¢/Star).
NET_CENTS_PER_100_STARS = 133
MIN_STARS = 1
MAX_STARS = 10_000  # Telegram's per-invoice ceiling


def stars_for_usd_cents(usd_cents: int) -> int:
    """$2.99 → 150 ⭐, $4.99 → 250 ⭐, $9.99 → 500 ⭐ (rounded to nearest 5)."""
    raw = max(1, int(round(int(usd_cents) / USER_CENTS_PER_STAR)))
    rounded = int(round(raw / 5.0)) * 5 or 5
    return max(MIN_STARS, min(MAX_STARS, rounded))


def net_cents_from_stars(stars: int) -> int:
    """What the developer actually receives, in USD cents."""
    return max(0, int(stars) * NET_CENTS_PER_100_STARS // 100)


def gross_cents_from_stars(stars: int) -> int:
    """What the user paid at list price, in USD cents (informational)."""
    return max(0, int(stars) * USER_CENTS_PER_STAR)


def split_net(net_cents: int, host_share_bps: Optional[int]) -> Dict[str, int]:
    """Platform / host split of NET revenue. bps = basis points (1000 = 10%)."""
    net = max(0, int(net_cents))
    bps = max(0, min(10_000, int(host_share_bps or 0)))
    host = net * bps // 10_000
    return {"net_cents": net, "host_cents": host, "platform_cents": net - host}


def quote(stars: int, host_share_bps: Optional[int] = None) -> Dict[str, int]:
    """Full breakdown for one purchase of `stars` Stars."""
    s = max(MIN_STARS, min(MAX_STARS, int(stars)))
    split = split_net(net_cents_from_stars(s), host_share_bps)
    return {"stars": s, "gross_cents": gross_cents_from_stars(s), **split}

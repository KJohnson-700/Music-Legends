# ui/brand.py
"""
Music Legends Brand Constants
Single source of truth for colors, assets, and embed decoration.
"""
import discord

# ── Color palette (from logo) ─────────────────────────────────────
GOLD   = 0xF4A800   # Crown / main text
PURPLE = 0x6B2EBE   # Outer glow halo
BLUE   = 0x4488FF   # Neon ring highlight
NAVY   = 0x0D0B2E   # Dark interior
PINK   = 0xFF4E9A   # Sparkle accent / warnings
GREEN  = 0x2ECC71   # Success
RED    = 0xE74C3C   # Error / crit

# ── Assets ───────────────────────────────────────────────────────
LOGO_URL   = "https://olive-generous-kangaroo-378.mypinata.cloud/ipfs/bafybeiehxk5zhdxidab4qtuxg6lblrasxcxb2bkj6a3ipyjue5f7pzo3qi"
BANNER_URL = "https://olive-generous-kangaroo-378.mypinata.cloud/ipfs/bafybeibftmlnx5rwio4jvxjimzvk3q5lq5vm2jl5aggmzaws5qgzzpsq6i"
VIDEO_URL  = "https://olive-generous-kangaroo-378.mypinata.cloud/ipfs/bafybeifesjs5yfsvqh4vyoeqzkok7ixp3pljm3myd7ou3qibztsobyp24i"

# ── Rarity system ────────────────────────────────────────────────
RARITY = {
    "common":    {"color": 0x95A5A6, "emoji": "⚪", "badge": "⚪ Common",    "bar_color": "░"},
    "rare":      {"color": BLUE,     "emoji": "🔵", "badge": "🔵 Rare",      "bar_color": "▒"},
    "epic":      {"color": PURPLE,   "emoji": "🟣", "badge": "🟣 Epic",      "bar_color": "▓"},
    "legendary": {"color": GOLD,     "emoji": "👑", "badge": "👑 Legendary", "bar_color": "█"},
    "mythic":    {"color": PINK,     "emoji": "💎", "badge": "💎 Mythic",    "bar_color": "█"},
}

POWER_TIERS = [  # compressed scale (core.power): 70-135
    (125, "💫 S-Tier"),
    (115, "⚡ A-Tier"),
    (105, "🔥 B-Tier"),
    (95,  "🎵 C-Tier"),
    (0,   "🎶 D-Tier"),
]


# ── Helpers ──────────────────────────────────────────────────────
def stat_bar(value: int, max_val: int = 100, length: int = 10) -> str:
    """Return emoji progress bar: ████████░░ 80"""
    try:
        filled = round((int(value) / max_val) * length)
        filled = max(0, min(length, filled))
    except (TypeError, ZeroDivisionError):
        filled = 0
    return f"{'█' * filled}{'░' * (length - filled)} {value}"


def power_tier(power: int) -> str:
    """Return tier label for a power value."""
    for threshold, label in POWER_TIERS:
        if power >= threshold:
            return label
    return "🎶 D-Tier"


def rarity_color(rarity: str) -> int:
    """Return brand hex color int for a rarity string."""
    return RARITY.get((rarity or "common").lower(), RARITY["common"])["color"]


def rarity_badge(rarity: str) -> str:
    """Return emoji badge string for a rarity."""
    return RARITY.get((rarity or "common").lower(), RARITY["common"])["badge"]


def rarity_emoji(rarity: str) -> str:
    """Return single emoji for a rarity."""
    return RARITY.get((rarity or "common").lower(), RARITY["common"])["emoji"]


def brand_embed(title: str, description: str = "", color: int = PURPLE) -> discord.Embed:
    """Create a pre-branded embed with logo author and footer."""
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_author(name="Music Legends", icon_url=LOGO_URL)
    embed.set_footer(text="🎵 Music Legends")
    return embed


def apply_branding(embed: discord.Embed) -> discord.Embed:
    """Add author + footer to an existing embed in-place."""
    embed.set_author(name="Music Legends", icon_url=LOGO_URL)
    if not embed.footer.text:
        embed.set_footer(text="🎵 Music Legends")
    return embed

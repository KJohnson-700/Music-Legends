# cogs/gameplay.py
import discord
from discord.ext import commands
from discord import Interaction, app_commands, ui
import random
from card_economy import CardEconomyManager
from database import DatabaseManager, get_db
from ui.brand import GOLD, PURPLE, BLUE, PINK, GREEN, NAVY, LOGO_URL, BANNER_URL, power_tier
from cards_config import compute_card_power

from config import settings




class CardDropView(discord.ui.View):
    """Button view for pack drops — first click claims the full pack."""

    TIER_COLORS = {
        "community": discord.Color.light_gray(),
        "gold": discord.Color.gold(),
        "platinum": discord.Color.purple(),
    }
    TIER_EMOJI = {"community": "⚪", "gold": "🟡", "platinum": "🟣"}

    def __init__(self, pack: dict, db, timeout: int = 300):
        super().__init__(timeout=timeout)
        self.pack = pack
        self.db = db
        self.claimed_by = None
        self.message = None

    @discord.ui.button(label="🎁 Claim Pack!", style=discord.ButtonStyle.success)
    async def claim_button(self, interaction: Interaction, button: discord.ui.Button):
        if self.claimed_by is not None:
            await interaction.response.send_message("Already claimed!", ephemeral=True)
            return

        self.claimed_by = interaction.user.id
        button.disabled = True
        button.label = f"Claimed by {interaction.user.display_name}!"
        button.style = discord.ButtonStyle.secondary

        result = self.db.open_pack_for_drop(self.pack["pack_id"], interaction.user.id)

        tier_emoji = self.TIER_EMOJI.get(self.pack["tier"], "⚪")
        if result.get("success"):
            embed = discord.Embed(
                title=f"{tier_emoji} Pack Claimed!",
                description=f"{interaction.user.mention} grabbed **{self.pack['name']}**!\nOpening {len(result['cards'])} cards...",
                color=self.TIER_COLORS.get(self.pack["tier"], discord.Color.green())
            )
            embed.set_footer(text="Check /collection for your new cards!")
        else:
            # Reset claim so another user can try, and report the error
            self.claimed_by = None
            button.disabled = False
            button.label = "🎁 Claim Pack!"
            button.style = discord.ButtonStyle.success
            error_msg = result.get("error", "Unknown error")
            print(f"[DROP] Pack open failed for user {interaction.user.id}: {error_msg}")
            await interaction.response.edit_message(view=self)
            await interaction.followup.send(
                f"❌ Could not open pack: `{error_msg}`\nPlease try again or contact a dev.",
                ephemeral=True
            )
            return

        await interaction.response.edit_message(embed=embed, view=self)

        # Run pack opening animation as followup
        if result.get("success") and result.get("cards"):
            try:
                from views.pack_opening import open_pack_with_animation
                pack_type = self.pack["tier"] if self.pack["tier"] in ("community", "gold", "platinum") else "community"
                await open_pack_with_animation(
                    interaction=interaction,
                    pack_name=self.pack["name"],
                    pack_type=pack_type,
                    cards=result["cards"],
                    pack_id=self.pack["pack_id"],
                    delay=1.5
                )
            except Exception as e:
                print(f"[DROP] Animation error: {e}")

        self.stop()

    async def on_timeout(self):
        if self.claimed_by is None and self.message:
            for child in self.children:
                child.disabled = True
                child.label = "Expired"
                child.style = discord.ButtonStyle.secondary
            try:
                await self.message.edit(content="⏰ Drop expired — nobody claimed it.", view=self)
            except Exception:
                pass


class CollectionView(discord.ui.View):
    """
    Pack-organized collection browser.
    Row 0: pack select dropdown
    Row 1: ← Prev | Card X/Y | Next →  (only when a pack is open)
    Row 2: ▶ YouTube link + ↩ Overview  (only when a pack is open)
    """

    RARITY_EMOJI  = {"common": "⚪", "rare": "🔵", "epic": "🟣", "legendary": "⭐", "mythic": "🔴"}
    RARITY_COLOR  = {"common": 0x95a5a6, "rare": 0x3498db, "epic": 0x9b59b6,
                     "legendary": 0xf39c12, "mythic": 0xe74c3c}
    TIER_EMOJI    = {"community": "📦", "gold": "🥇", "platinum": "💎", "all": "🎴"}

    # YouTube thumbnail quality variants — cycling through these gives Discord a
    # genuinely different URL path each time, beating its embed image cache.
    # Only hqdefault and mqdefault are reliably available for all videos;
    # sddefault returns 404 for many videos which breaks Discord embeds.
    _YT_QUALITIES = ["hqdefault.jpg", "mqdefault.jpg", "0.jpg", "1.jpg", "2.jpg", "3.jpg"]

    # Fallback placeholder for cards with no image or YouTube URL.
    # Uses a public-domain music note image so embeds aren't blank.
    _FALLBACK_IMAGES = [
        "https://img.icons8.com/fluency/512/musical-notes.png",
        "https://img.icons8.com/fluency/512/music.png",
        "https://img.icons8.com/fluency/512/headphones.png",
        "https://img.icons8.com/fluency/512/guitar.png",
        "https://img.icons8.com/fluency/512/microphone.png",
        "https://img.icons8.com/fluency/512/piano.png",
    ]

    @staticmethod
    def _resolve_image(card: dict, card_index: int = 0) -> str | None:
        """Return the best available image URL for a card.
        Cycles YouTube thumbnail quality variants based on card_index so Discord's
        CDN sees a different path on each page flip instead of serving a cached image.
        Falls back to a music-themed icon if no image or YouTube URL exists."""
        img = card.get('image_url') or ''
        # Reject obvious placeholder/example URLs
        if img and 'example.com' not in img and '_example' not in img:
            # Still append card_index as a query param for non-YT images
            sep = '&' if '?' in img else '?'
            return f"{img}{sep}_c={card_index}"
        yt = card.get('youtube_url') or ''
        if yt:
            import re
            m = re.search(r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})', yt)
            if m:
                # Rotate quality level by card index — different URL path each time
                quality = CollectionView._YT_QUALITIES[card_index % len(CollectionView._YT_QUALITIES)]
                return f"https://img.youtube.com/vi/{m.group(1)}/{quality}"
        # Fallback: cycle through music-themed placeholder icons
        return CollectionView._FALLBACK_IMAGES[card_index % len(CollectionView._FALLBACK_IMAGES)]

    def __init__(self, user_id: int, user_display: str, packs: list, all_cards: list):
        super().__init__(timeout=600)
        self.user_id       = user_id
        self._user_display = user_display
        self.all_cards     = all_cards   # full flat collection (fallback)
        self.packs         = packs       # from get_user_purchased_packs()
        self.current_cards = []
        self.card_index    = 0
        self.pack_name     = None
        self._rebuild()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _virtual_packs(self) -> list:
        """'All My Cards' is always the first option so no card is ever hidden."""
        result = []
        if self.all_cards:
            result.append({
                'purchase_id': '__all__',
                'pack_name':   'All My Cards',
                'pack_tier':   'all',
                'cards':       self.all_cards,
            })
        result.extend(self.packs)
        return result

    def _rebuild(self):
        """Rebuild all child items to reflect current state."""
        self.clear_items()
        vpacks = self._virtual_packs()

        # Row 0 — pack select (always present if user has any cards)
        if vpacks:
            options = []
            for p in vpacks[:25]:
                pid   = p.get('purchase_id') or p.get('pack_id') or ''
                name  = p.get('pack_name') or 'Pack'
                tier  = (p.get('pack_tier') or 'community').lower()
                count = len(p.get('cards', []))
                options.append(discord.SelectOption(
                    label       = f"{self.TIER_EMOJI.get(tier, '📦')} {name}"[:100],
                    value       = pid,
                    description = f"{count} card(s)",
                ))
            sel = discord.ui.Select(placeholder="Browse a pack...", options=options, row=0)
            sel.callback = self._on_pack_select
            self.add_item(sel)

        # Rows 1-2 — card navigation (only when a pack is open)
        if self.current_cards:
            n = len(self.current_cards)

            prev = discord.ui.Button(label="← Prev", style=discord.ButtonStyle.secondary,
                                     disabled=(self.card_index == 0), row=1)
            prev.callback = self._on_prev
            self.add_item(prev)

            counter = discord.ui.Button(label=f"Card {self.card_index + 1} of {n}",
                                        style=discord.ButtonStyle.secondary, disabled=True, row=1)
            self.add_item(counter)

            nxt = discord.ui.Button(label="Next →", style=discord.ButtonStyle.secondary,
                                    disabled=(self.card_index >= n - 1), row=1)
            nxt.callback = self._on_next
            self.add_item(nxt)

            # YouTube link button (row 2) — only if URL exists
            yt_url = (self.current_cards[self.card_index].get('youtube_url') or '').strip()
            if yt_url:
                yt_btn = discord.ui.Button(label="▶️ Watch on YouTube",
                                           style=discord.ButtonStyle.link, url=yt_url, row=2)
                self.add_item(yt_btn)

            back = discord.ui.Button(label="↩️ Overview", style=discord.ButtonStyle.primary, row=2)
            back.callback = self._on_back
            self.add_item(back)

    # ------------------------------------------------------------------
    # Embed builders
    # ------------------------------------------------------------------

    def _overview_embed(self, user_display: str) -> discord.Embed:
        total = len(self.all_cards)
        counts = {"common": 0, "rare": 0, "epic": 0, "legendary": 0, "mythic": 0}
        for c in self.all_cards:
            r = (c.get('rarity') or 'common').lower()
            if r in counts:
                counts[r] += 1

        embed = discord.Embed(
            title=f"🎴 {user_display}'s Collection",
            description=f"**{total}** card(s) total — select a pack below to browse",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="📊 By Rarity",
            value=(f"⚪ Common: {counts['common']}\n"
                   f"🔵 Rare: {counts['rare']}\n"
                   f"🟣 Epic: {counts['epic']}\n"
                   f"⭐ Legendary: {counts['legendary']}\n"
                   f"🔴 Mythic: {counts['mythic']}"),
            inline=True
        )
        packs_owned = len(self.packs)
        embed.add_field(
            name="📦 Packs",
            value=f"{packs_owned} pack(s) in collection\nUse dropdown to browse by pack",
            inline=True
        )
        embed.set_footer(text="Select a pack from the dropdown • ▶️ buttons open YouTube")
        return embed

    def _card_embed(self) -> discord.Embed:
        card    = self.current_cards[self.card_index]
        rarity  = (card.get('rarity') or 'common').lower()
        r_emoji = self.RARITY_EMOJI.get(rarity, "⚪")
        name    = card.get('name', 'Unknown')
        title   = card.get('title') or ''
        color   = self.RARITY_COLOR.get(rarity, 0x95a5a6)

        embed = discord.Embed(
            title=f"{r_emoji} {name}",
            description=f'**"{title}"**' if title else None,
            color=color
        )
        embed.add_field(
            name="Stats",
            value=(f"Impact: **{card.get('impact', '?')}** | "
                   f"Skill: **{card.get('skill', '?')}** | "
                   f"Longevity: **{card.get('longevity', '?')}**\n"
                   f"Culture: **{card.get('culture', '?')}** | "
                   f"Hype: **{card.get('hype', '?')}**"),
            inline=False
        )
        embed.add_field(name="Rarity", value=f"{r_emoji} {rarity.title()}", inline=True)
        embed.add_field(name="Pack",   value=self.pack_name or "Unknown",   inline=True)

        img = self._resolve_image(card, self.card_index)
        if img:
            embed.set_image(url=img)

        n = len(self.current_cards)
        embed.set_footer(text=f"Card {self.card_index + 1} of {n} • {self.pack_name}")
        return embed

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    async def interaction_check(self, interaction: Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def on_error(self, interaction: Interaction, error: Exception, item) -> None:
        print(f"[COLLECTION] View error on {item}: {error}")
        import traceback; traceback.print_exc()
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except Exception:
            pass

    async def _safe_update(self, interaction: Interaction, embed: discord.Embed):
        """Edit the message with the new embed+view; always acknowledge the interaction."""
        try:
            self._rebuild()
            await interaction.response.edit_message(embed=embed, view=self)
        except Exception as e:
            print(f"[COLLECTION] _safe_update error (index={self.card_index}): {e}")
            import traceback; traceback.print_exc()
            # Still acknowledge so Discord doesn't show "interaction failed"
            try:
                await interaction.response.defer()
            except Exception:
                pass

    async def _on_pack_select(self, interaction: Interaction):
        pack_id = interaction.data['values'][0]
        vpacks  = {(p.get('purchase_id') or p.get('pack_id')): p for p in self._virtual_packs()}
        pack    = vpacks.get(pack_id)
        if pack:
            self.current_cards = pack.get('cards', [])
            self.pack_name     = pack.get('pack_name', 'Pack')
            self.card_index    = 0
        await self._safe_update(interaction, self._card_embed())

    async def _on_prev(self, interaction: Interaction):
        self.card_index = max(0, self.card_index - 1)
        await self._safe_update(interaction, self._card_embed())

    async def _on_next(self, interaction: Interaction):
        self.card_index = min(len(self.current_cards) - 1, self.card_index + 1)
        await self._safe_update(interaction, self._card_embed())

    async def _on_back(self, interaction: Interaction):
        self.current_cards = []
        self.card_index    = 0
        self.pack_name     = None
        await self._safe_update(interaction, self._overview_embed(self._user_display))


class GameplayCommands(commands.Cog):
    """Main gameplay commands - drops, collection, viewing, trading"""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = get_db()
        self.economy = CardEconomyManager()
        self.economy.initialize_economy_tables()
        
    def _get_power_tier(self, power: int) -> str:
        """Get power tier description based on power level (compressed 70-135 scale)"""
        if power >= 125:
            return "🔥 **GOD TIER** - Untouchable!"
        elif power >= 115:
            return "⭐ **LEGENDARY** - Elite Status!"
        elif power >= 105:
            return "🟣 **EPIC** - Powerful Force!"
        elif power >= 95:
            return "🔵 **RARE** - Above Average!"
        elif power >= 85:
            return "⚪ **COMMON** - Standard Power!"
        else:
            return "🌱 **ROOKIE** - Growing Potential!"

    @app_commands.command(name="drop", description="[DEV] Drop a pack in this channel")
    @app_commands.describe(tier="Pack tier to drop")
    @app_commands.choices(tier=[
        app_commands.Choice(name="Community (5 cards)", value="community"),
        app_commands.Choice(name="Gold (5 cards, Rare+)", value="gold"),
        app_commands.Choice(name="Platinum (10 cards, Epic+)", value="platinum"),
    ])
    async def drop_command(self, interaction: Interaction, tier: str = "community"):
        """Dev-only: drop a random LIVE pack into this channel."""
        if not settings.ENABLE_DEV_COMMANDS:
            await interaction.response.send_message("❌ /drop is disabled in this environment.", ephemeral=True)
            return
        if settings.TEST_SERVER_ID and interaction.guild_id != int(settings.TEST_SERVER_ID):
            await interaction.response.send_message("❌ /drop is restricted to the test server.", ephemeral=True)
            return
        if interaction.user.id not in settings.DEV_USER_IDS:
            await interaction.response.send_message("❌ Dev only.", ephemeral=True)
            return

        pack = self.db.get_random_live_pack_by_tier(tier)
        if not pack:
            await interaction.response.send_message(
                f"❌ No LIVE packs found for **{tier}** tier. Create and publish packs first.",
                ephemeral=True
            )
            return

        tier_colors = {
            "community": BLUE,
            "gold":      GOLD,
            "platinum":  PURPLE,
        }
        tier_emoji = {"community": "⚪", "gold": "👑", "platinum": "💎"}.get(tier, "⚪")

        # ── Phase 1: incoming drop alert ──────────────────────────────
        alert_embed = discord.Embed(
            title=f"📦 PACK DROP INCOMING! {tier_emoji}",
            description=f"**{pack['name']}**\n{pack['pack_size']} cards up for grabs!\n\n**Dropping in...**",
            color=tier_colors.get(tier, GOLD),
        )
        alert_embed.set_author(name="Music Legends", icon_url=LOGO_URL)
        alert_embed.set_image(url=BANNER_URL)
        alert_embed.set_footer(text="🎵 Music Legends • Be ready!")
        await interaction.response.send_message(embed=alert_embed)
        msg = await interaction.original_response()

        # ── Countdown 3 → 2 → 1 ──────────────────────────────────
        for count in ["3️⃣", "2️⃣", "1️⃣"]:
            await asyncio.sleep(1.0)
            cd = discord.Embed(
                title=f"📦 PACK DROP! {count}",
                description=f"**{pack['name']}**\n{pack['pack_size']} cards • First to click wins!",
                color=tier_colors.get(tier, GOLD),
            )
            cd.set_author(name="Music Legends", icon_url=LOGO_URL)
            cd.set_footer(text="🎵 Music Legends • Get ready!")
            await msg.edit(embed=cd)

        # ── Phase 2: drop live ─────────────────────────────────────
        await asyncio.sleep(0.5)
        drop_embed = discord.Embed(
            title=f"{tier_emoji} DROP IS LIVE! {tier_emoji}",
            description=f"**{pack['name']}**\nFirst to click claims all **{pack['pack_size']}** cards!",
            color=tier_colors.get(tier, GOLD),
        )
        drop_embed.set_author(name="Music Legends", icon_url=LOGO_URL)
        drop_embed.set_thumbnail(url=LOGO_URL)
        if pack.get("genre"):
            drop_embed.add_field(name="🎵 Genre", value=pack["genre"], inline=True)
        drop_embed.add_field(name="📦 Tier", value=tier.title(), inline=True)
        drop_embed.add_field(name="🎴 Cards", value=str(pack["pack_size"]), inline=True)
        drop_embed.set_footer(text=f"🎵 Music Legends • Dropped by {interaction.user.display_name} • Expires in 5 min")

        view = CardDropView(pack=pack, db=self.db, timeout=300)
        await msg.edit(embed=drop_embed, view=view)
        view.message = msg

    @app_commands.command(name="collection", description="View your card collection")
    async def collection_command(self, interaction: Interaction, user: discord.User = None):
        """Pack-organized collection browser with card stats and YouTube links."""
        target = user or interaction.user
        await interaction.response.defer()

        try:
            all_cards = self.db.get_user_collection(target.id)
            packs     = self.db.get_user_purchased_packs(target.id, limit=25)
        except Exception as e:
            print(f"[COLLECTION] DB error for {target.id}: {e}")
            return await interaction.followup.send("❌ Could not load your collection. Please try again.", ephemeral=True)

        if not all_cards:
            embed = discord.Embed(
                title=f"🎴 {target.display_name}'s Collection",
                description="No cards yet!\n\n🛒 Use `/packs` to browse the marketplace\n🎁 Use `/daily` for a free daily card\n✨ Watch for drops in channels",
                color=discord.Color.blue()
            )
            return await interaction.followup.send(embed=embed)

        view  = CollectionView(target.id, target.display_name, packs, all_cards)
        # Auto-open "All My Cards" so users see their full collection immediately
        view.current_cards = all_cards
        view.pack_name = "All My Cards"
        view.card_index = 0
        view._rebuild()
        embed = view._card_embed()
        await interaction.followup.send(embed=embed, view=view)

    @app_commands.command(name="view", description="View details of a specific card")
    @app_commands.describe(card_identifier="Card ID or serial number to view")
    async def view_command(self, interaction: Interaction, card_identifier: str):
        """View detailed information about a specific card with full visual display"""
        await interaction.response.defer()
        await self._show_card(interaction, card_identifier)

    async def _show_card(self, interaction: Interaction, card_identifier: str):
        """Internal helper — DB lookup + embed. Called from /view and card buttons."""
        try:
            # Find card by card_id or serial_number in user's collection
            with self.db._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT c.*, uc.acquired_at, uc.acquired_from, uc.is_favorite
                    FROM cards c
                    JOIN user_cards uc ON c.card_id = uc.card_id
                    WHERE (c.card_id = ? OR c.serial_number = ? OR LOWER(c.name) LIKE LOWER(?)) AND uc.user_id = ?
                """, (card_identifier, card_identifier, f"%{card_identifier}%", interaction.user.id))
                row = cursor.fetchone()

                if row:
                    columns = [desc[0] for desc in cursor.description]
                    card = dict(zip(columns, row))
                else:
                    card = None
        except Exception as e:
            import traceback
            print(f"[VIEW] DB error: {e}")
            traceback.print_exc()
            await interaction.followup.send("❌ Database error while looking up card. Please try again.", ephemeral=True)
            return

        if not card:
            await interaction.followup.send("❌ Card not found in your collection! Use `/collection` to see your cards.", ephemeral=True)
            return
        
        # Extract card data with safe defaults
        card_id = card.get('card_id', 'Unknown')
        card_name = card.get('name', 'Unknown Artist')
        card_title = card.get('title', '')
        rarity = card.get('rarity', 'Common')
        image_url = card.get('image_url', '')
        youtube_url = card.get('youtube_url', '')
        variant = card.get('variant', 'Classic')
        era = card.get('era', '')
        
        # Stats
        impact = card.get('impact', 50) or 50
        skill = card.get('skill', 50) or 50
        longevity = card.get('longevity', 50) or 50
        culture = card.get('culture', 50) or 50
        hype = card.get('hype', 50) or 50
        total_power = impact + skill + longevity + culture + hype
        
        # Acquisition info
        acquired_at = card.get('acquired_at', '')
        acquired_from = card.get('acquired_from', 'Unknown')
        is_favorite = card.get('is_favorite', False)
        
        # Rarity styling
        RARITY_COLORS = {
            "Common": discord.Color.light_grey(),
            "common": discord.Color.light_grey(),
            "Rare": discord.Color.blue(),
            "rare": discord.Color.blue(),
            "Epic": discord.Color.purple(),
            "epic": discord.Color.purple(),
            "Legendary": discord.Color.gold(),
            "legendary": discord.Color.gold(),
            "Mythic": discord.Color.red(),
            "mythic": discord.Color.red(),
        }
        
        RARITY_EMOJI = {
            "Common": "⚪", "common": "⚪",
            "Rare": "�", "rare": "🔵",
            "Epic": "🟣", "epic": "🟣",
            "Legendary": "⭐", "legendary": "⭐",
            "Mythic": "🔴", "mythic": "🔴",
        }
        
        rarity_emoji = RARITY_EMOJI.get(rarity, "🎴")
        rarity_color = RARITY_COLORS.get(rarity, discord.Color.blurple())
        
        # Build the card embed
        display_title = f"{card_name} — \"{card_title}\"" if card_title else card_name
        
        # Create clean, professional card design
        rarity_display = {
            "common": "⚪ COMMON",
            "rare": "🔵 RARE", 
            "epic": "🟣 EPIC",
            "legendary": "⭐ LEGENDARY",
            "mythic": "🔴 MYTHIC"
        }.get(rarity.lower(), "⚪ COMMON")
        
        embed = discord.Embed(
            title=f"{rarity_display} CARD",
            description=f"**{display_title}**",
            color=rarity_color
        )
        
        # Clean separator
        embed.add_field(
            name="━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            value="**� ARTIST STATS** �",
            inline=False
        )
        
        # Card info with branding
        info_value = f"**🆔 Card ID:** `{card_id}`\n"
        if variant and variant != 'Classic':
            info_value += f"**🎨 Variant:** {variant}\n"
        if era:
            info_value += f"**⏰ Era:** {era}\n"
        info_value += f"**⭐ Favorite:** {'⭐ Yes' if is_favorite else 'No'}"
        
        embed.add_field(
            name="📋 **CARD DETAILS**",
            value=info_value,
            inline=True
        )
        
        # Stats with visual design
        stats_value = f"⚔️ **Impact:** `{impact:02d}`\n"
        stats_value += f"🛡️ **Skill:** `{skill:02d}`\n"
        stats_value += f"⚡ **Longevity:** `{longevity:02d}`\n"
        stats_value += f"🎭 **Culture:** `{culture:02d}`\n"
        stats_value += f"🔥 **Hype:** `{hype:02d}`"
        
        embed.add_field(
            name="📊 **BATTLE STATS**",
            value=stats_value,
            inline=True
        )
        
        # Power rating: use same formula as battle (stats avg + rarity bonus → 0–135)
        total_power = compute_card_power(card)
        filled = round((total_power / 135) * 10)
        power_bar = "█" * filled + "░" * (10 - filled)

        embed.add_field(
            name="💪 **POWER LEVEL**",
            value=f"```{power_bar}```\n**{total_power}** / 135 **POWER**\n{power_tier(total_power)}",
            inline=False
        )
        
        # Clean footer
        embed.add_field(
            name="━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            value="**Music Legends** • Artist Battle Game",
            inline=False
        )
        
        # Links
        links = []
        if youtube_url:
            links.append(f"[▶️ YouTube]({youtube_url})")
        if links:
            embed.add_field(name="🔗 **STREAMING LINKS**", value=" • ".join(links), inline=False)
        
        # Acquisition info
        acq_date = acquired_at[:10] if acquired_at and len(acquired_at) >= 10 else 'Unknown'
        serial = card.get('serial_number') or card.get('card_id', 'Unknown')
        embed.add_field(
            name="📅 **ACQUISITION**",
            value=f"**Date:** {acq_date}\n**Source:** {acquired_from.replace('_', ' ').title()}\n**Serial:** {serial}",
            inline=False
        )
        
        # Set card image (thumbnail for small, image for large display)
        if image_url:
            embed.set_image(url=image_url)  # Large image display
        
        embed.set_footer(text="Music Legends • Use /collection to see all your cards")

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="card", description="Preview any card in the database")
    @app_commands.describe(card_id="Card ID to preview")
    async def card_preview_command(self, interaction: Interaction, card_id: str):
        """Preview any card in the database (not just owned ones)"""
        with self.db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM cards WHERE card_id = ?", (card_id,))
            row = cursor.fetchone()
            
            if row:
                columns = [desc[0] for desc in cursor.description]
                card = dict(zip(columns, row))
            else:
                card = None
            
            # Check if user owns this card
            cursor.execute("""
                SELECT acquired_at, acquired_from FROM user_cards 
                WHERE card_id = ? AND user_id = ?
            """, (card_id, interaction.user.id))
            ownership = cursor.fetchone()
        
        if not card:
            await interaction.response.send_message("❌ Card not found in database!", ephemeral=True)
            return
        
        # Extract card data
        card_name = card.get('name', 'Unknown Artist')
        card_title = card.get('title', '')
        rarity = card.get('rarity', 'Common')
        image_url = card.get('image_url', '')
        youtube_url = card.get('youtube_url', '')
        variant = card.get('variant', 'Classic')
        era = card.get('era', '')
        
        # Stats
        impact = card.get('impact', 50) or 50
        skill = card.get('skill', 50) or 50
        longevity = card.get('longevity', 50) or 50
        culture = card.get('culture', 50) or 50
        hype = card.get('hype', 50) or 50
        total_power = impact + skill + longevity + culture + hype
        
        # Rarity styling
        RARITY_COLORS = {
            "Common": discord.Color.light_grey(), "common": discord.Color.light_grey(),
            "Rare": discord.Color.blue(), "rare": discord.Color.blue(),
            "Epic": discord.Color.purple(), "epic": discord.Color.purple(),
            "Legendary": discord.Color.gold(), "legendary": discord.Color.gold(),
            "Mythic": discord.Color.red(), "mythic": discord.Color.red(),
        }
        RARITY_EMOJI = {
            "Common": "⚪", "common": "⚪", "Rare": "🔵", "rare": "🔵",
            "Epic": "🟣", "epic": "🟣", "Legendary": "⭐", "legendary": "⭐",
            "Mythic": "🔴", "mythic": "🔴",
        }
        
        rarity_emoji = RARITY_EMOJI.get(rarity, "🎴")
        rarity_color = RARITY_COLORS.get(rarity, discord.Color.blurple())
        
        # Build embed
        display_title = f"{card_name} — \"{card_title}\"" if card_title else card_name
        
        embed = discord.Embed(
            title=f"{rarity_emoji} {rarity.upper()} — ARTIST CARD",
            description=f"**{display_title}**\n`ID: {card_id}`",
            color=rarity_color
        )
        
        # Stats
        stats_text = (
            f"**Impact:** {impact}\n"
            f"**Skill:** {skill}\n"
            f"**Longevity:** {longevity}\n"
            f"**Culture:** {culture}\n"
            f"**Hype:** {hype}\n"
            f"───────────\n"
            f"**Total Power:** {total_power}"
        )
        embed.add_field(name="📊 Battle Stats", value=stats_text, inline=True)
        
        # Ownership status
        if ownership:
            owned_text = f"✅ **You own this card!**\nAcquired: {str(ownership[0])[:10] if ownership[0] else 'Unknown'}"
        else:
            owned_text = "❌ You don't own this card"
        embed.add_field(name="📋 Ownership", value=owned_text, inline=True)
        
        # Links
        links = []
        if youtube_url:
            links.append(f"[▶️ YouTube]({youtube_url})")
        if links:
            embed.add_field(name="🔗 Links", value=" • ".join(links), inline=False)
        
        # Image
        if image_url:
            embed.set_image(url=image_url)
        
        embed.set_footer(text="Music Legends • Card Preview")
        
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="lookup", description="Lookup cards by artist name")
    async def lookup_command(self, interaction: Interaction, artist_name: str):
        """Lookup all cards for a specific artist in the database"""
        with self.db._get_connection() as conn:
            cursor = conn.cursor()
            # Search in both name and artist_name columns (with safe defaults for missing columns)
            cursor.execute("""
                SELECT c.card_id, c.name, c.rarity,
                       COALESCE(c.tier, CASE
                           WHEN LOWER(c.rarity) = 'common' THEN 'community'
                           WHEN LOWER(c.rarity) = 'rare' THEN 'gold'
                           WHEN LOWER(c.rarity) = 'epic' THEN 'platinum'
                           ELSE 'legendary'
                       END) as tier,
                       COALESCE(c.serial_number, c.card_id) as serial_number,
                       COALESCE(c.print_number, 1) as print_number,
                       COALESCE(c.quality, 'standard') as quality,
                       COALESCE(c.artist_name, c.name) as artist_name,
                       CASE WHEN uc.user_id = ? THEN 1 ELSE 0 END as owned
                FROM cards c
                LEFT JOIN user_cards uc ON c.card_id = uc.card_id AND uc.user_id = ?
                WHERE c.name LIKE ? OR c.artist_name LIKE ?
                ORDER BY tier DESC, print_number ASC
                LIMIT 50
            """, (interaction.user.id, interaction.user.id, f"%{artist_name}%", f"%{artist_name}%"))
            cards = cursor.fetchall()
        
        if not cards:
            await interaction.response.send_message(f"❌ No cards found for '{artist_name}'!", ephemeral=True)
            return
        
        # Create embed showing all cards
        owned_count = sum(1 for card in cards if card[8] == 1)
        embed = discord.Embed(
            title=f"🔍 Cards for '{artist_name}'",
            description=f"Found {len(cards)} card(s) | You own: {owned_count}",
            color=discord.Color.blue()
        )
        
        # Group by tier
        tier_groups = {}
        for card in cards:
            tier = card[3] if card[3] else 'community'
            if tier not in tier_groups:
                tier_groups[tier] = []
            tier_groups[tier].append(card)
        
        # Display by tier
        tier_order = ['legendary', 'platinum', 'gold', 'community']
        for tier in tier_order:
            if tier in tier_groups:
                tier_emoji = {"community": "⚪", "gold": "🟡", "platinum": "🟣", "legendary": "🔴"}.get(tier, "⚪")
                cards_text = ""
                for card in tier_groups[tier][:5]:  # Limit to 5 per tier
                    owned_marker = "✅" if card[8] == 1 else "⬜"
                    serial = card[4] if card[4] else 'N/A'
                    print_num = card[5] if card[5] else 0
                    quality = card[6] if card[6] else 'standard'
                    cards_text += f"{owned_marker} {tier_emoji} `{serial}` - Print #{print_num} ({quality})\n"
                
                if len(tier_groups[tier]) > 5:
                    cards_text += f"... and {len(tier_groups[tier]) - 5} more\n"
                
                embed.add_field(
                    name=f"{tier.title()} Cards ({len(tier_groups[tier])})",
                    value=cards_text or "None",
                    inline=False
                )
        
        embed.set_footer(text="✅ = Owned | ⬜ = Not owned")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="burn", description="Burn a card for dust")
    async def burn_command(self, interaction: Interaction, serial_number: str):
        """Burn a card to receive dust"""
        result = self.economy.burn_card_for_dust(interaction.user.id, serial_number)
        
        if result['success']:
            embed = discord.Embed(
                title="🔥 Card Burned!",
                description=f"You burned {serial_number} and received {result['dust_earned']} dust!",
                color=discord.Color.orange()
            )
            
            embed.add_field(
                name="💰 New Dust Total",
                value=f"Check your collection with `/collection`",
                inline=False
            )
            
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(f"❌ {result['error']}", ephemeral=True)

    @app_commands.command(name="upgrade", description="Upgrade cards to higher tiers")
    async def upgrade_command(self, interaction: Interaction, upgrade_type: str):
        """Upgrade cards to higher tier"""
        valid_types = ['community_to_gold', 'gold_to_platinum', 'platinum_to_legendary']
        
        if upgrade_type not in valid_types:
            await interaction.response.send_message("❌ Invalid upgrade type!", ephemeral=True)
            return
        
        # Create upgrade view for card selection
        view = CardUpgradeView(self.economy, interaction.user.id, upgrade_type, db=self.db)
        
        embed = discord.Embed(
            title="⬆️ Card Upgrade",
            description=f"Select {self.economy.upgrade_costs[upgrade_type]} cards to upgrade",
            color=discord.Color.purple()
        )
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="daily", description="Claim daily rewards")
    async def daily_command(self, interaction: Interaction):
        """Claim daily rewards with streak bonuses, free card, and audio feedback"""
        from pathlib import Path

        # Defer immediately — DB + card lookup can exceed 3s interaction timeout
        await interaction.response.defer(ephemeral=True)

        # Use the new database method that includes free card
        result = self.db.claim_daily_reward(interaction.user.id)

        if not result.get('success'):
            # 'message' is the key used by claim_daily_reward; 'error' is a legacy fallback
            msg = result.get('message') or result.get('error') or 'Already claimed today!'
            await interaction.followup.send(f"❌ {msg}", ephemeral=True)
            return

        gold_reward = result.get('gold', 0)
        ticket_reward = result.get('tickets', 0)
        current_streak = result.get('streak', 1)
        daily_cards = result.get('cards', [])
        daily_pack_name = result.get('pack_name')
        # backward-compat: single card field (old callers)
        daily_card = result.get('card')
        
        # Check for audio file
        audio_path = Path('assets/sounds/daily_claim.mp3')
        audio_file = None
        if audio_path.exists():
            audio_file = discord.File(str(audio_path), filename='daily_claim.mp3')
        
        # Create reward embed
        is_milestone = current_streak in [3, 7, 14, 30]
        
        embed = discord.Embed(
            title="🎁 Daily Reward Claimed!" if not is_milestone else f"🎉 DAY {current_streak} MILESTONE! 🎉",
            description=f"**Day {current_streak} Streak!** {'🔥' * min(current_streak, 10)}",
            color=discord.Color.gold()
        )
        
        # Add GIF for milestones
        if is_milestone:
            celebration_gif = 'https://media.tenor.com/Cvx2qeKmAOEAAAAC/fireworks-celebration.gif'
            embed.set_image(url=celebration_gif)
        
        rewards_text = f"💰 Gold: +{gold_reward}"
        if ticket_reward > 0:
            rewards_text += f"\n🎫 Tickets: +{ticket_reward}"

        embed.add_field(
            name="Today's Rewards",
            value=rewards_text,
            inline=False
        )

        # Display daily free pack if received
        rarity_emoji_map = {'common': '⚪', 'rare': '🔵', 'epic': '🟣', 'legendary': '⭐', 'mythic': '🔴'}
        if daily_cards:
            pack_label = daily_pack_name or "Daily Pack"
            card_lines = []
            for c in daily_cards[:5]:
                r_emoji = rarity_emoji_map.get((c.get('rarity') or 'common').lower(), '⚪')
                card_lines.append(f"{r_emoji} **{c.get('name', 'Unknown')}** ({(c.get('rarity') or 'common').title()})")
            embed.add_field(
                name=f"🎴 Daily Pack — {pack_label} ({len(daily_cards)} cards)",
                value="\n".join(card_lines) if card_lines else "Cards added to your collection!",
                inline=False
            )
        elif daily_card:
            # Legacy single-card fallback
            r_emoji = rarity_emoji_map.get((daily_card.get('rarity') or 'common').lower(), '⚪')
            embed.add_field(
                name="🎴 Daily Card",
                value=f"{r_emoji} **{daily_card.get('name', 'Unknown')}** ({(daily_card.get('rarity') or 'common').title()})",
                inline=False
            )
        
        # Show next milestone
        next_milestones = {3: "Day 3: 150 gold", 7: "Day 7: 300 gold + 1 ticket", 
                          14: "Day 14: 600 gold + 2 tickets", 30: "Day 30: 1,100 gold + 5 tickets"}
        
        next_milestone = None
        for day, reward in next_milestones.items():
            if current_streak < day:
                next_milestone = reward
                days_until = day - current_streak
                break
        
        if next_milestone:
            embed.add_field(
                name="🎯 Next Milestone",
                value=f"{next_milestone}\n({days_until} day{'s' if days_until > 1 else ''} away)",
                inline=False
            )
        
        embed.set_footer(text="Come back tomorrow to keep your streak!")
        
        # Send with audio if available (use followup since we deferred)
        if audio_file:
            await interaction.followup.send(embed=embed, file=audio_file, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="rank", description="View your rank and XP progress")
    async def rank_command(self, interaction: Interaction, user: discord.User = None):
        """View rank and XP progression"""
        from config.economy import RANKS, get_rank, get_next_rank
        
        target_user = user or interaction.user
        
        with self.db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT xp, gold, tickets FROM user_inventory WHERE user_id = ?
            """, (target_user.id,))
            inv_row = cursor.fetchone()
            
            cursor.execute("""
                SELECT wins, total_battles FROM users WHERE user_id = ?
            """, (target_user.id,))
            stats_row = cursor.fetchone()
        
        xp = inv_row[0] if inv_row and inv_row[0] else 0
        gold = inv_row[1] if inv_row and inv_row[1] else 0
        tickets = inv_row[2] if inv_row and inv_row[2] else 0
        wins = stats_row[0] if stats_row and stats_row[0] else 0
        total_battles = stats_row[1] if stats_row and stats_row[1] else 0
        
        # Calculate rank
        current_rank = get_rank(xp, wins)
        rank_info = RANKS[current_rank]
        next_rank_info = get_next_rank(current_rank)
        
        embed = discord.Embed(
            title=f"{rank_info['emoji']} {target_user.display_name}'s Profile",
            color=rank_info['color']
        )
        
        embed.add_field(
            name="🏅 Rank",
            value=f"**{current_rank}** {rank_info['emoji']}",
            inline=True
        )
        
        embed.add_field(
            name="⭐ XP",
            value=f"{xp:,}",
            inline=True
        )
        
        embed.add_field(
            name="⚔️ Battle Record",
            value=f"Wins: {wins}\nTotal: {total_battles}",
            inline=True
        )
        
        embed.add_field(
            name="💰 Currency",
            value=f"Gold: {gold:,}\nTickets: {tickets}",
            inline=True
        )
        
        # Progress to next rank
        if next_rank_info:
            xp_progress = min(xp / next_rank_info['xp_required'] * 100, 100) if next_rank_info['xp_required'] > 0 else 100
            wins_progress = min(wins / next_rank_info['wins_required'] * 100, 100) if next_rank_info['wins_required'] > 0 else 100
            
            progress_bar_xp = "█" * int(xp_progress / 10) + "░" * (10 - int(xp_progress / 10))
            progress_bar_wins = "█" * int(wins_progress / 10) + "░" * (10 - int(wins_progress / 10))
            
            embed.add_field(
                name=f"📈 Progress to {next_rank_info['name']}",
                value=f"XP: {xp}/{next_rank_info['xp_required']} [{progress_bar_xp}]\n"
                      f"Wins: {wins}/{next_rank_info['wins_required']} [{progress_bar_wins}]",
                inline=False
            )
        else:
            embed.add_field(
                name="👑 Max Rank Achieved!",
                value="You've reached the highest rank: Legend!",
                inline=False
            )
        
        embed.set_thumbnail(url=target_user.display_avatar.url)
        
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="quicksell", description="Instantly sell a card for gold")
    async def quicksell_command(self, interaction: Interaction, serial_number: str):
        """Instantly sell a card for gold (marketplace /sell lists for other players)"""
        from config.economy import get_card_sell_price, CARD_SELL_PRICES
        
        with self.db._get_connection() as conn:
            cursor = conn.cursor()
            
            # Find the card in user's collection (match by serial_number or card_id)
            cursor.execute("""
                SELECT c.card_id, c.name, c.rarity, uc.user_id
                FROM cards c
                JOIN user_cards uc ON c.card_id = uc.card_id
                WHERE (c.serial_number = ? OR c.card_id = ?) AND uc.user_id = ?
            """, (serial_number, serial_number, interaction.user.id))
            card = cursor.fetchone()
            
            if not card:
                await interaction.response.send_message("❌ Card not found in your collection!", ephemeral=True)
                return
            
            card_id, card_name, rarity, owner_id = card
            
            # Check if user has duplicate of this card (same name)
            cursor.execute("""
                SELECT COUNT(*) FROM user_cards uc
                JOIN cards c ON uc.card_id = c.card_id
                WHERE uc.user_id = ? AND c.name = ?
            """, (interaction.user.id, card_name))
            count = cursor.fetchone()[0]
            is_duplicate = count > 1
            
            # Calculate sell price
            sell_price = get_card_sell_price(rarity, is_duplicate)
            
            # Remove card from collection
            cursor.execute("""
                DELETE FROM user_cards WHERE user_id = ? AND card_id = ?
            """, (interaction.user.id, card_id))
            
            # Add gold to user
            cursor.execute("""
                INSERT INTO user_inventory (user_id, gold)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET gold = user_inventory.gold + EXCLUDED.gold
            """, (interaction.user.id, sell_price))
            
            conn.commit()
        
        # Create embed
        embed = discord.Embed(
            title="💰 Card Sold!",
            description=f"You sold **{card_name}** ({rarity})",
            color=discord.Color.gold()
        )
        
        price_text = f"+{sell_price} gold"
        if is_duplicate:
            base_price = CARD_SELL_PRICES.get(rarity, 10)
            price_text += f" (includes +50% duplicate bonus!)"
        
        embed.add_field(name="💵 Earned", value=price_text, inline=False)
        embed.set_footer(text=f"Serial: {serial_number}")
        
        await interaction.response.send_message(embed=embed)


class CardUpgradeView(ui.View):
    """View that lets users select cards to upgrade to the next tier."""

    # Map upgrade_type -> (source rarity, target rarity)
    RARITY_MAP = {
        'community_to_gold': ('common', 'rare'),
        'gold_to_platinum': ('rare', 'epic'),
        'platinum_to_legendary': ('epic', 'legendary'),
    }

    def __init__(self, economy_manager: CardEconomyManager, user_id: int, upgrade_type: str, db: DatabaseManager = None):
        super().__init__(timeout=180)
        self.economy = economy_manager
        self.db = db
        self.user_id = user_id
        self.upgrade_type = upgrade_type
        self.selected_cards = []
        self.required = economy_manager.upgrade_costs.get(upgrade_type, 3)
        self.source_rarity, self.target_rarity = self.RARITY_MAP.get(
            upgrade_type, ('common', 'rare'))

        # Load eligible cards from DB
        self._eligible_cards = self._load_eligible_cards()

        # Card select dropdown
        if self._eligible_cards:
            options = []
            for card in self._eligible_cards[:25]:
                name = card.get('name', 'Unknown')
                cid = card.get('card_id', '')
                label = f"{name}" if len(name) <= 50 else name[:47] + "..."
                options.append(discord.SelectOption(label=label, value=cid))

            select = ui.Select(
                placeholder=f"Select {self.required} {self.source_rarity} cards...",
                options=options,
                min_values=self.required,
                max_values=min(self.required, len(options)),
                custom_id="upgrade_card_select",
            )
            select.callback = self._on_select
            self.add_item(select)

        # Upgrade button (starts disabled)
        self.upgrade_btn = ui.Button(
            label=f"Upgrade (0/{self.required} selected)",
            style=discord.ButtonStyle.primary,
            disabled=True,
            custom_id="upgrade_button",
        )
        self.upgrade_btn.callback = self._on_upgrade
        self.add_item(self.upgrade_btn)

    def _load_eligible_cards(self) -> list:
        try:
            with self.db._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT c.card_id, c.name, c.rarity
                    FROM cards c
                    JOIN user_cards uc ON c.card_id = uc.card_id
                    WHERE uc.user_id = ? AND LOWER(c.rarity) = ?
                """, (self.user_id, self.source_rarity))
                cols = [d[0] for d in cursor.description]
                return [dict(zip(cols, r)) for r in cursor.fetchall()]
        except Exception:
            return []

    async def _on_select(self, interaction: Interaction):
        self.selected_cards = interaction.data.get('values', [])
        count = len(self.selected_cards)
        self.upgrade_btn.label = f"Upgrade ({count}/{self.required} selected)"
        self.upgrade_btn.disabled = count < self.required
        await interaction.response.edit_message(view=self)

    async def _on_upgrade(self, interaction: Interaction):
        if len(self.selected_cards) < self.required:
            return await interaction.response.send_message(
                f"Select {self.required} cards first.", ephemeral=True)

        # Remove consumed cards
        with self.db._get_connection() as conn:
            cursor = conn.cursor()
            for cid in self.selected_cards:
                cursor.execute(
                    "DELETE FROM user_cards WHERE user_id = ? AND card_id = ?",
                    (self.user_id, cid))

            # Award one card of the target rarity (random from DB)
            cursor.execute(
                "SELECT card_id, name FROM cards WHERE LOWER(rarity) = ? ORDER BY RANDOM() LIMIT 1",
                (self.target_rarity,))
            new_card = cursor.fetchone()
            if new_card:
                cursor.execute(
                    "INSERT OR IGNORE INTO user_cards (user_id, card_id, acquired_from) VALUES (?, ?, 'upgrade')",
                    (self.user_id, new_card[0]))
            conn.commit()

        if new_card:
            embed = discord.Embed(
                title="⬆️ Upgrade Successful!",
                description=f"You sacrificed {self.required} {self.source_rarity} cards and received:\n"
                            f"**{new_card[1]}** ({self.target_rarity.title()})",
                color=discord.Color.green(),
            )
        else:
            embed = discord.Embed(
                title="⬆️ Upgrade Failed",
                description=f"No {self.target_rarity} cards available in the database.",
                color=discord.Color.red(),
            )

        self.stop()
        await interaction.response.edit_message(embed=embed, view=None)

    async def interaction_check(self, interaction: Interaction) -> bool:
        return interaction.user.id == self.user_id

async def setup(bot):
    await bot.add_cog(GameplayCommands(bot))

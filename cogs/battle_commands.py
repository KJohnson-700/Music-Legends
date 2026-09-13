# cogs/battle_commands.py
"""
Battle Commands - /battle, /battle_stats
Lets users challenge each other to card battles with wagers
"""

import asyncio
import discord
import random
import traceback
import uuid
from discord.ext import commands
from discord import Interaction, app_commands, ui
from typing import Optional

from core.battle import BattleManager, BattleWagerConfig, resolve_match
from discord_cards import ArtistCard
from database import get_db
from config.economy import BATTLE_WAGERS, calculate_battle_rewards
from cards_config import RARITY_EMOJI as CARD_RARITY_EMOJI, RARITY_BONUS, compute_card_power, compute_team_power
from ui.brand import GOLD, PURPLE, BLUE, PINK, GREEN, LOGO_URL


# Shared battle manager (per-bot instance)
_battle_manager = BattleManager()


class WagerSelect(ui.Select):
    """Dropdown to pick wager tier"""

    def __init__(self):
        options = []
        for key, tier in BattleWagerConfig.TIERS.items():
            options.append(discord.SelectOption(
                label=f"{tier['emoji']} {tier['name']} — {tier['wager_cost']}g",
                value=key,
                description=f"Win: {tier['winner_gold']}g | Lose: {tier['loser_gold']}g"
            ))
        super().__init__(placeholder="Choose wager tier...", options=options, custom_id="wager_select")

    async def callback(self, interaction: Interaction):
        self.view.selected_tier = self.values[0]
        tier = BattleWagerConfig.get_tier(self.values[0])
        # Acknowledge the select interaction FIRST, then stop the view.
        # If stop() fires before edit_message is awaited, _run_battle resumes
        # and could call followup methods before Discord has acknowledged this interaction.
        await interaction.response.edit_message(
            content=(
                f"✅ **{tier['emoji']} {tier['name']} wager selected!** ({tier['wager_cost']}g)\n\n"
                f"Challenge sent! **Watch the channel** — your opponent has 60s to accept."
            ),
            view=None
        )
        self.view.stop()


class WagerSelectView(ui.View):
    """View containing wager dropdown"""

    def __init__(self, challenger_id: int):
        super().__init__(timeout=30)
        self.challenger_id = challenger_id
        self.selected_tier = None
        self.add_item(WagerSelect())

    async def interaction_check(self, interaction: Interaction) -> bool:
        return interaction.user.id == self.challenger_id


class AcceptBattleView(ui.View):
    """Accept / Decline buttons shown to the opponent"""

    def __init__(self, opponent_id: int, match_id: str):
        super().__init__(timeout=60)
        self.opponent_id = opponent_id
        self.match_id = match_id
        self.accepted = None
        self.interaction = None  # Saved from accept callback for ephemeral followup

    async def interaction_check(self, interaction: Interaction) -> bool:
        return interaction.user.id == self.opponent_id

    @ui.button(label="Accept", style=discord.ButtonStyle.green, emoji="⚔️")
    async def accept(self, interaction: Interaction, button: ui.Button):
        self.accepted = True
        self.interaction = interaction  # Save for ephemeral champion-select prompt
        # MUST defer BEFORE stop() so the interaction is acknowledged before
        # _run_battle continues and calls followup.send() on this interaction.
        await interaction.response.defer()
        self.stop()

    @ui.button(label="Decline", style=discord.ButtonStyle.red, emoji="❌")
    async def decline(self, interaction: Interaction, button: ui.Button):
        self.accepted = False
        await interaction.response.edit_message(
            content="❌ Battle declined.", embed=None, view=None
        )
        self.stop()


class PackSelectView(ui.View):
    """Let a player pick which pack (deck) to battle with"""

    RARITY_EMOJI = {"common": "⚪", "rare": "🔵", "epic": "🟣", "legendary": "⭐", "mythic": "🔴"}
    TIER_EMOJI   = {"community": "📦", "gold": "🥇", "platinum": "💎"}

    @staticmethod
    def _pack_key(pack: dict) -> str:
        """Consistent key for a pack — used in both the option value and the lookup dict."""
        return str(pack.get('purchase_id') or pack.get('pack_id') or '')

    def __init__(self, user_id: int, packs: list):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.selected_pack = None   # The full pack dict chosen
        self.selected_cards = []    # Cards inside the chosen pack

        options = []
        for pack in packs[:25]:
            cards = pack.get('cards', [])
            card_count = len(cards)
            tier = (pack.get('pack_tier') or 'community').lower()
            tier_e = self.TIER_EMOJI.get(tier, "📦")
            name = pack.get('pack_name') or pack.get('name') or 'Pack'
            label = name[:50]
            key = self._pack_key(pack)
            if not key:
                print(f"[BATTLE] Warning: pack has no purchase_id or pack_id — skipping: {pack}")
                continue
            options.append(discord.SelectOption(
                label=f"{tier_e} {label}",
                value=key,
                description=f"{card_count} card(s) | {(pack.get('genre') or 'Music').title()}",
            ))

        if not options:
            return

        self._packs_by_id = {self._pack_key(p): p for p in packs if self._pack_key(p)}

        select = ui.Select(
            placeholder="Select your deck...",
            options=options,
            custom_id="pack_select"
        )
        select.callback = self._select_callback
        self.add_item(select)

    async def _select_callback(self, interaction: Interaction):
        try:
            # Hard guard: should never fire for the wrong user, but log loudly if it does
            if interaction.user.id != self.user_id:
                print(f"[BATTLE] ⚠️ ROLE SWAP CAUGHT: _select_callback fired for wrong user! "
                      f"view_owner={self.user_id} actual_clicker={interaction.user.id}({interaction.user.display_name})")
                await interaction.response.send_message(
                    "⚠️ Pack selection error — please try `/battle` again.", ephemeral=True
                )
                return  # do NOT set selected_pack; view will time out

            pack_id = interaction.data['values'][0]
            self.selected_pack = self._packs_by_id.get(pack_id)
            self.selected_cards = self.selected_pack.get('cards', []) if self.selected_pack else []
            first_card = self.selected_cards[0].get('name', '?') if self.selected_cards else 'none'
            print(f"[BATTLE] Pack selected: view_owner={self.user_id} clicker={interaction.user.id}({interaction.user.display_name}) pack={pack_id} cards={len(self.selected_cards)} first={first_card}")
            # Acknowledge BEFORE stop() so Discord confirms the select interaction
            # before _run_battle resumes and fires more messages.
            await interaction.response.defer()
        except Exception as e:
            import traceback
            print(f"[BATTLE] _select_callback error: {e}")
            traceback.print_exc()
            try:
                await interaction.response.send_message("Pack selection failed. The battle will time out.", ephemeral=True)
            except Exception:
                pass
        finally:
            self.stop()

    async def interaction_check(self, interaction: Interaction) -> bool:
        return interaction.user.id == self.user_id


class BattleCommands(commands.Cog):
    """Battle system commands"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = get_db()
        self.manager = _battle_manager

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ensure_user(self, user: discord.User):
        """Make sure user row and inventory row both exist; always refresh username."""
        ph = self.db._get_placeholder()
        uid = str(user.id)
        with self.db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"INSERT INTO users (user_id, username, discord_tag) VALUES ({ph}, {ph}, {ph}) "
                f"ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username, discord_tag = EXCLUDED.discord_tag",
                (uid, user.display_name, str(user))
            )
            cursor.execute(
                f"INSERT INTO user_inventory (user_id, gold) VALUES ({ph}, 500) ON CONFLICT (user_id) DO NOTHING",
                (uid,)
            )
            conn.commit()

    def _get_gold(self, user_id: int) -> int:
        ph = self.db._get_placeholder()
        with self.db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT gold FROM user_inventory WHERE user_id = {ph}", (str(user_id),))
            row = cursor.fetchone()
            return row[0] if row and row[0] else 0

    def _add_gold(self, user_id: int, amount: int):
        if amount <= 0:
            return
        ph = self.db._get_placeholder()
        with self.db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"INSERT INTO user_inventory (user_id, gold) VALUES ({ph}, {ph}) "
                f"ON CONFLICT(user_id) DO UPDATE SET gold = user_inventory.gold + EXCLUDED.gold",
                (str(user_id), amount))
            conn.commit()

    def _remove_gold(self, user_id: int, amount: int) -> bool:
        """Atomic gold deduction — deducts only if balance is sufficient (no TOCTOU)."""
        ph = self.db._get_placeholder()
        with self.db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE user_inventory SET gold = gold - {ph} WHERE user_id = {ph} AND gold >= {ph}",
                (amount, str(user_id), amount)
            )
            conn.commit()
            return cursor.rowcount > 0

    def _deduct_both_wagers(self, user1_id: int, user2_id: int, amount: int) -> tuple[bool, bool]:
        """Deduct wager from both players in one transaction.
        Returns (user1_ok, user2_ok). Rolls back both if either fails."""
        ph = self.db._get_placeholder()
        with self.db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE user_inventory SET gold = gold - {ph} WHERE user_id = {ph} AND gold >= {ph}",
                (amount, str(user1_id), amount)
            )
            u1_ok = cursor.rowcount > 0
            cursor.execute(
                f"UPDATE user_inventory SET gold = gold - {ph} WHERE user_id = {ph} AND gold >= {ph}",
                (amount, str(user2_id), amount)
            )
            u2_ok = cursor.rowcount > 0
            if u1_ok and u2_ok:
                conn.commit()
            else:
                conn.rollback()
            return u1_ok, u2_ok

    def _add_xp(self, user_id: int, xp: int):
        if xp <= 0:
            return
        ph = self.db._get_placeholder()
        with self.db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"INSERT INTO user_inventory (user_id, xp) VALUES ({ph}, {ph}) "
                f"ON CONFLICT(user_id) DO UPDATE SET xp = COALESCE(user_inventory.xp, 0) + EXCLUDED.xp",
                (str(user_id), xp))
            conn.commit()

    def _update_battle_stats(self, winner_id: int, loser_id: int):
        ph = self.db._get_placeholder()
        with self.db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE users SET total_battles = total_battles + 1, wins = wins + 1 WHERE user_id = {ph}",
                (str(winner_id),)
            )
            cursor.execute(
                f"UPDATE users SET total_battles = total_battles + 1, losses = losses + 1 WHERE user_id = {ph}",
                (str(loser_id),)
            )
            conn.commit()

    def _compute_card_power(self, card: dict) -> int:
        return compute_card_power(card)

    def _get_support_cards(self, cards: list, champion_card_id: str, n: int = 4) -> list:
        """Return top-n cards by power, excluding the chosen champion."""
        others = [c for c in cards if c.get('card_id') != champion_card_id]
        others.sort(key=lambda c: self._compute_card_power(c), reverse=True)
        return others[:n]

    def _compute_team_power(self, champ_power: int, support_powers: list) -> int:
        return compute_team_power(champ_power, support_powers)

    def _card_dict_to_artist(self, card: dict) -> ArtistCard:
        """Convert a DB card dict to an ArtistCard for display metadata in the battle engine.
        Power is overridden via _compute_card_power() — view_count is a placeholder only."""
        return ArtistCard(
            card_id=card.get('card_id', ''),
            artist=card.get('name', 'Unknown'),
            song=card.get('title', '') or card.get('name', 'Unknown'),
            youtube_url=card.get('youtube_url', '') or '',
            youtube_id='',
            view_count=10_000_000,  # placeholder — power is overridden in execute_battle()
            thumbnail=card.get('image_url', '') or '',
            rarity=(card.get('rarity') or 'common').lower(),
        )

    # ------------------------------------------------------------------
    # /battle
    # ------------------------------------------------------------------

    @app_commands.command(name="battle", description="Challenge another player to a card battle")
    @app_commands.describe(opponent="The player you want to battle")
    @app_commands.checks.cooldown(1, 15.0, key=lambda i: i.user.id)
    async def battle_command(self, interaction: Interaction, opponent: discord.User):
        """Full battle flow: choose wager -> opponent accepts -> both pick cards -> resolve"""

        # Validation
        if opponent.id == interaction.user.id:
            return await interaction.response.send_message("You can't battle yourself!", ephemeral=True)
        if opponent.bot:
            return await interaction.response.send_message("You can't battle a bot!", ephemeral=True)
        if self.manager.is_user_in_battle(str(interaction.user.id)):
            return await interaction.response.send_message("You're already in a battle!", ephemeral=True)
        if self.manager.is_user_in_battle(str(opponent.id)):
            return await interaction.response.send_message(f"{opponent.display_name} is already in a battle!", ephemeral=True)

        try:
            self._ensure_user(interaction.user)
            self._ensure_user(opponent)
        except Exception as e:
            import traceback
            print(f"[BATTLE] _ensure_user failed: {e}")
            traceback.print_exc()
            await interaction.response.send_message(
                "❌ Battle setup failed. Please try again.", ephemeral=True)
            return

        try:
            await self._run_battle(interaction, opponent)
        except Exception as e:
            import traceback
            print(f"[BATTLE] Unhandled error: {e}")
            traceback.print_exc()
            try:
                await interaction.followup.send(
                    "⚔️ Something went wrong during the battle. Any gold wagered has been refunded.",
                    ephemeral=True
                )
            except Exception:
                pass

    async def _run_battle(self, interaction: Interaction, opponent: discord.User):
        """Inner battle logic — wrapped by battle_command for error handling"""

        # Step 1 — Challenger picks wager tier
        print(f"[BATTLE] Starting battle: {interaction.user} vs {opponent}")
        wager_view = WagerSelectView(interaction.user.id)
        await interaction.response.send_message("Choose a wager tier:", view=wager_view, ephemeral=True)
        timed_out = await wager_view.wait()
        if timed_out or wager_view.selected_tier is None:
            return await interaction.followup.send("Battle cancelled — no tier selected.", ephemeral=True)

        tier_key = wager_view.selected_tier
        tier = BattleWagerConfig.get_tier(tier_key)
        wager_cost = tier["wager_cost"]
        print(f"[BATTLE] Tier selected: {tier_key} (wager={wager_cost}g)")

        # Check both players have enough gold
        challenger_gold = self._get_gold(interaction.user.id)
        opponent_gold = self._get_gold(opponent.id)
        print(f"[BATTLE] Gold check: challenger={challenger_gold}g, opponent={opponent_gold}g, need={wager_cost}g")
        if challenger_gold < wager_cost:
            return await interaction.followup.send(
                f"You don't have enough gold! Need {wager_cost}g, you have {challenger_gold}g.", ephemeral=True)
        if opponent_gold < wager_cost:
            return await interaction.followup.send(
                f"{opponent.display_name} doesn't have enough gold ({wager_cost}g required).", ephemeral=True)

        # Step 2 — Send challenge to opponent
        match_id = f"battle_{uuid.uuid4().hex[:8]}"
        accept_view = AcceptBattleView(opponent.id, match_id)

        challenge_embed = discord.Embed(
            title=f"{tier['emoji']} Battle Challenge!",
            description=(
                f"**{interaction.user.display_name}** challenges **{opponent.display_name}**!\n\n"
                f"**Wager:** {wager_cost}g ({tier['name']})\n"
                f"**Winner gets:** {tier['winner_gold']}g + {tier['winner_xp']} XP\n"
                f"**Loser gets:** {tier['loser_gold']}g + {tier['loser_xp']} XP"
            ),
            color=0xf39c12
        )
        challenge_embed.set_footer(text=f"{opponent.display_name} — accept or decline within 60s")

        await interaction.followup.send(
            content=f"{opponent.mention}",
            embed=challenge_embed,
            view=accept_view
        )

        timed_out = await accept_view.wait()
        if timed_out or not accept_view.accepted:
            await interaction.followup.send(
                f"Battle {'timed out' if timed_out else 'declined'} by {opponent.display_name}.")
            return

        # Deduct wager from both players atomically — either both succeed or neither does
        c_ok, o_ok = self._deduct_both_wagers(interaction.user.id, opponent.id, wager_cost)
        if not c_ok:
            await interaction.followup.send("Insufficient gold — battle cancelled.")
            return
        if not o_ok:
            await interaction.followup.send(f"{opponent.display_name} doesn't have enough gold — battle cancelled.")
            return

        # Register both players in the battle manager so is_user_in_battle() works
        self.manager.create_match(
            match_id=match_id,
            player1_id=str(interaction.user.id),
            player1_name=interaction.user.display_name,
            player2_id=str(opponent.id),
            player2_name=opponent.display_name,
            wager_tier=tier_key,
        )
        # Persist to DB — if bot restarts before this battle completes,
        # on_ready will refund both wagers automatically.
        self.db.persist_active_battle(
            match_id, interaction.user.id, opponent.id, wager_cost, tier_key
        )

        rewards_distributed = False
        try:
            rewards_distributed = await self._run_card_selection_and_battle(
                interaction, opponent, match_id, tier_key, tier, wager_cost,
                accept_view
            )
        except Exception as e:
            print(f"[BATTLE] CRASH in _run_card_selection_and_battle: {e}")
            traceback.print_exc()
            # Unexpected crash after gold was deducted — refund both players
            if not rewards_distributed:
                self._add_gold(interaction.user.id, wager_cost)
                self._add_gold(opponent.id, wager_cost)
                # Clear DB row BEFORE raise — prevents double refund if bot dies
                # between here and the finally block.
                self.db.clear_active_battle(match_id)
                try:
                    await interaction.followup.send(
                        f"{interaction.user.mention} {opponent.mention} An unexpected error occurred. Wagers have been refunded."
                    )
                except Exception:
                    pass
            raise
        finally:
            self.manager.complete_match(match_id)
            self.db.clear_active_battle(match_id)

    async def _run_card_selection_and_battle(
        self,
        interaction: Interaction,
        opponent: discord.User,
        match_id: str,
        tier_key: str,
        tier: dict,
        wager_cost: int,
        accept_view: 'AcceptBattleView',
    ):
        """Card selection + battle resolution.
        Returns True once rewards have been distributed (used by caller to skip emergency refund)."""

        # Step 3 — Both players select a deck; best cards are auto-picked
        print(f"[BATTLE] === ROLE MAP === challenger={interaction.user.id}({interaction.user.display_name}) opponent={opponent.id}({opponent.display_name})")
        challenger_packs = self.db.get_user_purchased_packs(interaction.user.id)
        opponent_packs = self.db.get_user_purchased_packs(opponent.id)

        # Fallback: if no pack_purchases records, build a synthetic pack from their collection
        def _synth_packs(user_id):
            cards = self.db.get_user_collection(user_id)
            if not cards:
                return []
            return [{"purchase_id": f"collection_{user_id}", "pack_id": "collection",
                     "pack_name": "My Collection", "pack_tier": "community",
                     "genre": "Music", "cards": cards}]

        if not challenger_packs:
            challenger_packs = _synth_packs(interaction.user.id)
        if not opponent_packs:
            opponent_packs = _synth_packs(opponent.id)

        print(f"[BATTLE] Packs: challenger={len(challenger_packs)}, opponent={len(opponent_packs)}")

        if not challenger_packs:
            self._add_gold(interaction.user.id, wager_cost)
            self._add_gold(opponent.id, wager_cost)
            await interaction.followup.send(f"{interaction.user.display_name} has no cards or packs! Battle cancelled, wagers refunded.")
            return True
        if not opponent_packs:
            self._add_gold(interaction.user.id, wager_cost)
            self._add_gold(opponent.id, wager_cost)
            await interaction.followup.send(f"{opponent.display_name} has no cards or packs! Battle cancelled, wagers refunded.")
            return True

        # Build pack-select views
        c_view = PackSelectView(interaction.user.id, challenger_packs)
        o_view = PackSelectView(opponent.id, opponent_packs)

        # Verify both views have items (pack picker works only if cards/packs are non-empty)
        if not c_view.children:
            self._add_gold(interaction.user.id, wager_cost)
            self._add_gold(opponent.id, wager_cost)
            await interaction.followup.send(
                f"{interaction.user.display_name} has no valid packs to battle with! Wagers refunded."
            )
            return True
        if not o_view.children:
            self._add_gold(interaction.user.id, wager_cost)
            self._add_gold(opponent.id, wager_cost)
            await interaction.followup.send(
                f"{opponent.display_name} has no valid packs to battle with! Wagers refunded."
            )
            return True

        # Send BOTH pack pickers as public channel messages.
        # Using interaction.followup.send() (the challenger's token) for both is most
        # reliable — ephemeral sends from stale interaction tokens can fail silently.
        # interaction_check on each view ensures only the right player can select.
        print(f"[BATTLE] Sending pack pickers to channel")
        try:
            await interaction.followup.send(
                f"{interaction.user.mention} 📦 **Select your deck!** "
                "Your strongest card will be auto-picked as champion. (60s)",
                view=c_view,
            )
        except Exception as e:
            print(f"[BATTLE] Failed to send challenger pack picker: {e}")
            traceback.print_exc()
            self._add_gold(interaction.user.id, wager_cost)
            self._add_gold(opponent.id, wager_cost)
            await interaction.followup.send("Battle setup failed — couldn't send pack picker. Wagers refunded.")
            return True

        try:
            await interaction.followup.send(
                f"{opponent.mention} 📦 **Select your deck!** "
                "Your strongest card will be auto-picked as champion. (60s)",
                view=o_view,
            )
        except Exception as e:
            print(f"[BATTLE] Failed to send opponent pack picker: {e}")
            traceback.print_exc()
            self._add_gold(interaction.user.id, wager_cost)
            self._add_gold(opponent.id, wager_cost)
            await interaction.followup.send("Battle setup failed — couldn't send opponent pack picker. Wagers refunded.")
            return True

        # Wait for BOTH players to pick simultaneously
        print(f"[BATTLE] Waiting for both players to select packs (60s each)")
        c_timed_out, o_timed_out = await asyncio.gather(c_view.wait(), o_view.wait())
        print(f"[BATTLE] Pack selection done: c_timed_out={c_timed_out}, o_timed_out={o_timed_out}, "
              f"c_selected={c_view.selected_pack is not None}, o_selected={o_view.selected_pack is not None}")

        # Check selections — refund if either player didn't pick
        if c_timed_out or c_view.selected_pack is None:
            self._add_gold(interaction.user.id, wager_cost)
            self._add_gold(opponent.id, wager_cost)
            await interaction.followup.send("Battle cancelled — challenger didn't pick a pack. Wagers refunded.")
            return True
        if o_timed_out or o_view.selected_pack is None:
            self._add_gold(interaction.user.id, wager_cost)
            self._add_gold(opponent.id, wager_cost)
            await interaction.followup.send("Battle cancelled — opponent didn't pick a pack. Wagers refunded.")
            return True

        # Resolve cards from each chosen pack
        challenger_cards = c_view.selected_cards
        opponent_cards   = o_view.selected_cards
        print(f"[BATTLE] CARDS RESOLVED: {interaction.user.display_name}(c) first={challenger_cards[0].get('name','?') if challenger_cards else 'none'} | {opponent.display_name}(o) first={opponent_cards[0].get('name','?') if opponent_cards else 'none'}")

        # Fallback: if pack has no card data, pull from full collection
        if not challenger_cards:
            challenger_cards = self.db.get_user_collection(interaction.user.id)
        if not opponent_cards:
            opponent_cards = self.db.get_user_collection(opponent.id)

        if not challenger_cards or not opponent_cards:
            self._add_gold(interaction.user.id, wager_cost)
            self._add_gold(opponent.id, wager_cost)
            await interaction.followup.send("Battle cancelled — a pack had no cards. Wagers refunded.")
            return True

        # Pick champion = strongest card in each pack; rest are squad
        challenger_cards_sorted = sorted(challenger_cards, key=self._compute_card_power, reverse=True)
        opponent_cards_sorted   = sorted(opponent_cards,   key=self._compute_card_power, reverse=True)

        c_card_data = challenger_cards_sorted[0]
        o_card_data = opponent_cards_sorted[0]

        card1 = self._card_dict_to_artist(c_card_data)
        card2 = self._card_dict_to_artist(o_card_data)

        # Squad = remaining pack cards (up to 4), sorted by power
        c_supports = challenger_cards_sorted[1:5]
        o_supports = opponent_cards_sorted[1:5]

        c_champ_power = self._compute_card_power(c_card_data)
        o_champ_power = self._compute_card_power(o_card_data)
        c_support_powers = [self._compute_card_power(c) for c in c_supports]
        o_support_powers = [self._compute_card_power(c) for c in o_supports]

        # Team power: champion counts double, supports contribute to weighted average
        c_power = self._compute_team_power(c_champ_power, c_support_powers)
        o_power = self._compute_team_power(o_champ_power, o_support_powers)

        # Step 4 — Execute battle (instant; animation below is theatrical reveal)
        print(f"[BATTLE] Executing: {c_card_data.get('name')} (champ={c_champ_power}, team={c_power}) vs {o_card_data.get('name')} (champ={o_champ_power}, team={o_power})")
        result = resolve_match(card1, card2, tier_key, p1_override=c_power, p2_override=o_power)
        p1 = result["player1"]
        p2 = result["player2"]
        print(f"[BATTLE] Result: winner={result['winner']}, p1_final={p1['final_power']}, p2_final={p2['final_power']}")

        # Step 5 — Distribute rewards BEFORE animation so a Discord API failure
        # can't orphan rewards after gold has already been deducted.
        self._add_gold(interaction.user.id, p1["gold_reward"])
        self._add_gold(opponent.id, p2["gold_reward"])
        self._add_xp(interaction.user.id, p1["xp_reward"])
        self._add_xp(opponent.id, p2["xp_reward"])
        rewards_distributed = True  # Signal to caller: do NOT refund on any later exception

        try:
            if result["winner"] == 1:
                self._update_battle_stats(interaction.user.id, opponent.id)
            elif result["winner"] == 2:
                self._update_battle_stats(opponent.id, interaction.user.id)
            else:
                ph = self.db._get_placeholder()
                with self.db._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        f"UPDATE users SET total_battles = total_battles + 1 WHERE user_id IN ({ph}, {ph})",
                        (str(interaction.user.id), str(opponent.id)))
                    conn.commit()
        except Exception as e:
            print(f"[BATTLE] Warning: battle stats update failed (non-critical): {e}")

        # --- Battle Animation ---
        c_rarity_e = CARD_RARITY_EMOJI.get((c_card_data.get('rarity') or 'common').lower(), "⚪")
        o_rarity_e = CARD_RARITY_EMOJI.get((o_card_data.get('rarity') or 'common').lower(), "⚪")
        c_name = c_card_data.get('name', 'Unknown')
        o_name = o_card_data.get('name', 'Unknown')
        c_title = c_card_data.get('title', '') or ''
        o_title = o_card_data.get('title', '') or ''
        c_pack_name = c_view.selected_pack.get('pack_name') or 'Pack' if c_view.selected_pack else 'Pack'
        o_pack_name = o_view.selected_pack.get('pack_name') or 'Pack' if o_view.selected_pack else 'Pack'

        def _power_bar(power: int, max_power: int = 135, width: int = 10) -> str:
            """Visual power bar: █████░░░░░ 87/135"""
            filled = round((power / max_power) * width) if max_power > 0 else 0
            filled = max(0, min(width, filled))
            empty = width - filled
            return f"{'█' * filled}{'░' * empty} {power}/{max_power}"

        def _squad_lines(supports: list) -> str:
            lines = []
            for s in supports:
                r_e = CARD_RARITY_EMOJI.get((s.get('rarity') or 'common').lower(), "\u26aa")
                lines.append(f"{r_e} {s.get('name', 'Unknown')} ({self._compute_card_power(s)})")
            return "\n".join(lines) if lines else "_No squad members_"

        # Phase 1 — Battle Start
        start_embed = discord.Embed(
            title="⚔️ Battle Commencing!",
            description=f"**{interaction.user.display_name}** vs **{opponent.display_name}**",
            color=GOLD,
        )
        start_embed.set_author(name="Music Legends", icon_url=LOGO_URL)
        start_embed.add_field(name=f"{tier['emoji']} Wager", value=f"{wager_cost}g ({tier['name']})", inline=True)
        start_embed.add_field(name="📦 Packs", value=f"{c_pack_name} vs {o_pack_name}", inline=True)
        start_embed.set_footer(text="⚔️ Champions stepping forward...")
        # wait=True required — followup.send() is a Webhook call, returns None by default
        anim_msg = await interaction.followup.send(embed=start_embed, wait=True)
        await asyncio.sleep(1.5)

        # Phase 2a — Champion Reveal
        champ_embed = discord.Embed(title="🏆 Champions Revealed!", color=PURPLE)
        champ_embed.set_author(name="Music Legends", icon_url=LOGO_URL)
        champ_embed.add_field(
            name=f"🔵 {interaction.user.display_name} — {c_pack_name}",
            value=f"{c_rarity_e} **{c_name}**" + (f" — {c_title}" if c_title else "") + f"\nPower: **{c_champ_power}**",
            inline=True,
        )
        champ_embed.add_field(name="⚡", value="**VS**", inline=True)
        champ_embed.add_field(
            name=f"🔴 {opponent.display_name} — {o_pack_name}",
            value=f"{o_rarity_e} **{o_name}**" + (f" — {o_title}" if o_title else "") + f"\nPower: **{o_champ_power}**",
            inline=True,
        )
        champ_embed.set_footer(text="Squads assembling...")
        await anim_msg.edit(embed=champ_embed)
        await asyncio.sleep(1.5)

        # Phase 2b — Full Squad Reveal + Power Bars
        squad_embed = discord.Embed(title="🎵 Full Squads!", color=BLUE)
        squad_embed.set_author(name="Music Legends", icon_url=LOGO_URL)
        squad_embed.add_field(
            name=f"🔵 {interaction.user.display_name} [{c_pack_name}]",
            value=(
                f"**Champion:** {c_rarity_e} {c_name} ({c_champ_power})\n"
                f"**Squad:**\n{_squad_lines(c_supports)}\n\n"
                f"🔵 {_power_bar(c_power)}"
            ),
            inline=True,
        )
        squad_embed.add_field(name="⚡", value="**VS**", inline=True)
        squad_embed.add_field(
            name=f"🔴 {opponent.display_name} [{o_pack_name}]",
            value=(
                f"**Champion:** {o_rarity_e} {o_name} ({o_champ_power})\n"
                f"**Squad:**\n{_squad_lines(o_supports)}\n\n"
                f"🔴 {_power_bar(o_power)}"
            ),
            inline=True,
        )
        squad_embed.set_footer(text="Powers clashing...")
        await anim_msg.edit(embed=squad_embed)
        await asyncio.sleep(2.0)

        # Phase 3 — Critical Hit reveal (if any)
        if p1['critical_hit'] or p2['critical_hit']:
            crit_embed = discord.Embed(title="💥 CRITICAL HIT!", color=PINK)
            crit_embed.set_author(name="Music Legends", icon_url=LOGO_URL)
            crit_lines = []
            if p1['critical_hit']:
                crit_lines.append(f"🔵 **{interaction.user.display_name}**'s team lands a CRIT! {c_power} → **{p1['final_power']}** ⚡")
            if p2['critical_hit']:
                crit_lines.append(f"🔴 **{opponent.display_name}**'s team lands a CRIT! {o_power} → **{p2['final_power']}** ⚡")
            crit_embed.description = "\n".join(crit_lines)
            crit_embed.set_footer(text="Calculating winner...")
            await anim_msg.edit(embed=crit_embed)
            await asyncio.sleep(1.5)

        # Phase 4 — Suspense: Powers Clashing
        clash_embed = discord.Embed(
            title="🔥 Powers Clashing...",
            description=(
                f"🔵 {interaction.user.display_name}: {_power_bar(p1['final_power'])}\n"
                f"🔴 {opponent.display_name}: {_power_bar(p2['final_power'])}"
            ),
            color=0xe74c3c,
        )
        clash_embed.set_footer(text="Who will emerge victorious?")
        await anim_msg.edit(embed=clash_embed)
        await asyncio.sleep(1.2)

        # Phase 5 — Countdown
        countdown_embed = discord.Embed(
            title="⚡ 3... 2... 1...",
            description="**The winner is...**",
            color=0xe67e22,
        )
        await anim_msg.edit(embed=countdown_embed)
        await asyncio.sleep(1.5)

        # Phase 6 — Final Result (custom embed with power bars)
        if result["winner"] == 1:
            winner_name = interaction.user.display_name
            winner_color = 0x2ecc71
        elif result["winner"] == 2:
            winner_name = opponent.display_name
            winner_color = 0xe74c3c
        else:
            winner_name = None
            winner_color = 0xf39c12

        result_embed = discord.Embed(
            title=f"🏆 {winner_name.upper()} WINS!" if winner_name else "🤝 TIE!",
            color=winner_color,
        )
        # Player 1 — champion + squad
        p1_crit = " 💥 CRIT!" if p1['critical_hit'] else ""
        p1_squad = "\n".join(
            f"  {CARD_RARITY_EMOJI.get((s.get('rarity') or 'common').lower(), '⚪')} {s.get('name', '?')} ({self._compute_card_power(s)})"
            for s in c_supports
        ) or "  _No squad_"
        result_embed.add_field(
            name=f"🔵 {interaction.user.display_name} {'🏆' if result['winner'] == 1 else ''}",
            value=(
                f"{c_rarity_e} **{c_name}** ({c_champ_power}){p1_crit}\n"
                f"{p1_squad}\n"
                f"{_power_bar(p1['final_power'])}"
            ),
            inline=True,
        )
        result_embed.add_field(name="⚡", value="**VS**", inline=True)
        # Player 2 — champion + squad
        p2_crit = " 💥 CRIT!" if p2['critical_hit'] else ""
        p2_squad = "\n".join(
            f"  {CARD_RARITY_EMOJI.get((s.get('rarity') or 'common').lower(), '⚪')} {s.get('name', '?')} ({self._compute_card_power(s)})"
            for s in o_supports
        ) or "  _No squad_"
        result_embed.add_field(
            name=f"🔴 {opponent.display_name} {'🏆' if result['winner'] == 2 else ''}",
            value=(
                f"{o_rarity_e} **{o_name}** ({o_champ_power}){p2_crit}\n"
                f"{p2_squad}\n"
                f"{_power_bar(p2['final_power'])}"
            ),
            inline=True,
        )
        # Rewards summary
        result_embed.add_field(
            name="━━━━━━━━━━━━━━━━━━━━",
            value=(
                f"🔵 {interaction.user.display_name}: **+{p1['gold_reward']}g** | +{p1['xp_reward']} XP\n"
                f"🔴 {opponent.display_name}: **+{p2['gold_reward']}g** | +{p2['xp_reward']} XP"
            ),
            inline=False,
        )
        await anim_msg.edit(embed=result_embed)

        # Phase 7 — Winner's YouTube video (separate message, auto-embeds)
        winning_card = c_card_data if result["winner"] == 1 else (o_card_data if result["winner"] == 2 else None)
        if winning_card:
            yt_url = winning_card.get('youtube_url') or ''
            if yt_url:
                # Normalize to canonical https://www.youtube.com/watch?v=ID format
                # so Discord reliably auto-embeds it as a playable video
                import re
                yt_match = re.search(r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})', yt_url)
                if yt_match:
                    canonical_url = f"https://www.youtube.com/watch?v={yt_match.group(1)}"
                else:
                    canonical_url = yt_url  # fallback — send as-is
                try:
                    await interaction.followup.send(
                        f"🏆 **Winner's Track:**\n{canonical_url}"
                    )
                except Exception as e:
                    print(f"[BATTLE] Could not send winner YouTube link: {e}")

        # Record match in DB
        winner_id = interaction.user.id if result["winner"] == 1 else (
            opponent.id if result["winner"] == 2 else None)
        try:
            self.db.record_match({
                'match_id': match_id,
                'player_a_id': interaction.user.id,
                'player_b_id': opponent.id,
                'winner_id': winner_id or 0,
                'final_score_a': p1["final_power"],
                'final_score_b': p2["final_power"],
                'match_type': tier_key
            })
        except Exception as e:
            print(f"Warning: could not record match: {e}")

        # Record to battle_history table
        try:
            battle_id = f"battle_{uuid.uuid4().hex[:8]}"
            self.db.record_battle({
                'battle_id': battle_id,
                'player1_id': interaction.user.id,
                'player2_id': opponent.id,
                'player1_card_id': c_card_data.get('card_id', ''),
                'player2_card_id': o_card_data.get('card_id', ''),
                'winner': result['winner'],  # 0=tie, 1=player1, 2=player2
                'player1_power': p1['final_power'],
                'player2_power': p2['final_power'],
                'player1_critical': p1.get('critical_hit', False),
                'player2_critical': p2.get('critical_hit', False),
                'wager_tier': tier_key,
                'wager_amount': wager_cost,
                'player1_gold_reward': p1['gold_reward'],
                'player2_gold_reward': p2['gold_reward'],
                'player1_xp_reward': p1['xp_reward'],
                'player2_xp_reward': p2['xp_reward'],
            })
        except Exception as e:
            print(f"Warning: could not record battle history: {e}")

        # Log to changelog
        try:
            from services.changelog_manager import log_user_action
            log_user_action(
                f"battle_{tier_key}",
                interaction.user.id,
                {'opponent': opponent.id, 'winner': winner_id, 'tier': tier_key}
            )
        except Exception:
            pass

        return rewards_distributed  # True — tells caller not to issue an emergency refund

    # ------------------------------------------------------------------
    # /battle_stats
    # ------------------------------------------------------------------

    @app_commands.command(name="battle_stats", description="View your battle record")
    @app_commands.describe(user="Player to view stats for (default: yourself)")
    async def battle_stats_command(self, interaction: Interaction, user: Optional[discord.User] = None):
        target = user or interaction.user
        stats = self.db.get_user_stats(target.id)

        if not stats:
            return await interaction.response.send_message(
                f"{target.display_name} hasn't played yet!", ephemeral=True)

        wins = stats.get('wins', 0)
        losses = stats.get('losses', 0)
        total = stats.get('total_battles', 0)
        win_rate = stats.get('win_rate', 0)

        embed = discord.Embed(
            title=f"⚔️ Battle Stats — {target.display_name}",
            color=BLUE,
        )
        embed.set_author(name="Music Legends", icon_url=LOGO_URL)
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="🏆 Wins", value=str(wins), inline=True)
        embed.add_field(name="💀 Losses", value=str(losses), inline=True)
        embed.add_field(name="🎮 Total", value=str(total), inline=True)
        embed.add_field(name="📈 Win Rate", value=f"{win_rate:.1f}%", inline=True)
        embed.set_footer(text="🎵 Music Legends")

        await interaction.response.send_message(embed=embed)

    async def on_app_command_error(self, interaction: Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(
                f"⏱️ Slow down! You can battle again in **{error.retry_after:.0f}s**.",
                ephemeral=True
            )
        else:
            raise error


async def setup(bot):
    await bot.add_cog(BattleCommands(bot))

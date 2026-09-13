"""Dev pack supply commands — view inventory and grant packs to users."""

import discord
from discord import Interaction, app_commands
from discord.ext import commands
from database import get_db
from cards_config import RARITY_EMOJI, RARITY_BONUS, compute_card_power, compute_team_power

from config import settings


def _is_dev(user_id: int) -> bool:
    return user_id in settings.DEV_USER_IDS


def _is_admin(interaction: Interaction) -> bool:
    """True if user is a server admin/owner OR in DEV_USER_IDS."""
    if _is_dev(interaction.user.id):
        return True
    if isinstance(interaction.user, discord.Member):
        return (
            interaction.user.guild_permissions.administrator
            or interaction.guild.owner_id == interaction.user.id
        )
    return False


class DevSupplyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = get_db()

    @app_commands.command(name="dev_supply", description="[DEV] View dev pack supply inventory")
    async def dev_supply(self, interaction: Interaction):
        if not _is_admin(interaction):
            await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        supply = self.db.get_dev_supply()

        if not supply:
            await interaction.followup.send("📦 Dev supply is empty.", ephemeral=True)
            return

        embed = discord.Embed(title="📦 Dev Pack Supply", color=discord.Color.blue())
        lines = []
        for item in supply:
            tier = item.get("pack_tier") or "?"
            lines.append(f"**{item['pack_name']}** (`{item['pack_id']}`)\n  Tier: {tier} | Qty: **{item['quantity']}**")
        embed.description = "\n".join(lines)
        embed.set_footer(text="Use /dev_grant_pack to send a pack to a user")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="dev_grant_pack", description="[DEV] Grant a pack from dev supply to a user")
    @app_commands.describe(pack_id="Pack ID to grant", user="Target user")
    async def dev_grant_pack(self, interaction: Interaction, pack_id: str, user: discord.Member):
        if not _is_admin(interaction):
            await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        result = self.db.grant_pack_to_user(pack_id, user.id)

        if not result["success"]:
            await interaction.followup.send(f"❌ {result['error']}", ephemeral=True)
            return

        cards = result["cards"]
        pack_name = result["pack_name"]
        rarity_counts = {}
        for c in cards:
            r = c.get("rarity", "common")
            rarity_counts[r] = rarity_counts.get(r, 0) + 1

        rarity_text = " | ".join(f"{r.title()}: {n}" for r, n in sorted(rarity_counts.items()))
        embed = discord.Embed(
            title="🎁 Pack Granted!",
            description=f"**{pack_name}** sent to {user.mention}\n{len(cards)} cards: {rarity_text}",
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


    @app_commands.command(name="dev_reset_daily", description="[DEV] Reset a user's daily claim for testing")
    @app_commands.describe(user="User to reset (defaults to yourself)")
    async def dev_reset_daily(self, interaction: Interaction, user: discord.Member = None):
        if not _is_admin(interaction):
            await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
            return

        target = user or interaction.user
        ph = self.db._get_placeholder()
        with self.db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE user_inventory SET last_daily_claim = NULL, daily_streak = 0 WHERE user_id = {ph}",
                (target.id,)
            )
            changed = cursor.rowcount

        if changed:
            await interaction.response.send_message(
                f"✅ Daily claim reset for {target.mention}. They can claim again now.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"⚠️ No inventory row found for {target.mention}. They may not have played yet.", ephemeral=True
            )


    @app_commands.command(name="give_gold", description="Give gold to a user (server admin only)")
    @app_commands.describe(user="Target user", amount="Amount of gold to give")
    async def give_gold(self, interaction: Interaction, user: discord.Member, amount: int):
        if not _is_admin(interaction):
            await interaction.response.send_message("❌ Server admins only.", ephemeral=True)
            return

        if amount <= 0:
            await interaction.response.send_message("❌ Amount must be positive.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        ph = self.db._get_placeholder()
        with self.db._get_connection() as conn:
            cursor = conn.cursor()
            # Ensure user_inventory row exists first
            cursor.execute(
                f"INSERT INTO users (user_id, username, discord_tag) VALUES ({ph}, {ph}, {ph}) ON CONFLICT (user_id) DO NOTHING",
                (user.id, user.display_name, str(user))
            )
            cursor.execute(
                f"INSERT INTO user_inventory (user_id, gold) VALUES ({ph}, 0) ON CONFLICT (user_id) DO NOTHING",
                (user.id,)
            )
            # Add gold
            cursor.execute(
                f"UPDATE user_inventory SET gold = gold + {ph} WHERE user_id = {ph}",
                (amount, user.id)
            )
            # Get new balance
            cursor.execute(
                f"SELECT gold FROM user_inventory WHERE user_id = {ph}",
                (user.id,)
            )
            row = cursor.fetchone()
            new_balance = row[0] if row else amount

        embed = discord.Embed(
            title="💰 Gold Given",
            description=f"Gave **{amount:,}** gold to {user.mention}\nNew balance: **{new_balance:,}** gold",
            color=discord.Color.gold()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


    @app_commands.command(name="dev_debug", description="[DEV] Diagnose why cards aren't showing in /collection")
    @app_commands.describe(user="User to check (defaults to yourself)")
    async def dev_debug(self, interaction: discord.Interaction, user: discord.Member = None):
        if not _is_admin(interaction):
            await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        target = user or interaction.user
        ph = self.db._get_placeholder()

        with self.db._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*), COUNT(CASE WHEN pack_tier='community' THEN 1 END) FROM creator_packs WHERE status='LIVE'")
            total_live, community_live = cursor.fetchone()

            cursor.execute(f"SELECT COUNT(*) FROM user_cards WHERE user_id = {ph}", (target.id,))
            user_cards_count = cursor.fetchone()[0]

            cursor.execute(f"""
                SELECT COUNT(*) FROM user_cards uc
                JOIN cards c ON c.card_id = uc.card_id
                WHERE uc.user_id = {ph}
            """, (target.id,))
            matched_count = cursor.fetchone()[0]

            cursor.execute(f"""
                SELECT uc.card_id FROM user_cards uc
                LEFT JOIN cards c ON c.card_id = uc.card_id
                WHERE uc.user_id = {ph} AND c.card_id IS NULL
                LIMIT 5
            """, (target.id,))
            orphans = [r[0] for r in cursor.fetchall()]

            cursor.execute("SELECT COUNT(*) FROM cards")
            total_cards = cursor.fetchone()[0]

            cursor.execute(f"SELECT COUNT(*) FROM pack_purchases WHERE buyer_id = {ph}", (target.id,))
            pack_purchases = cursor.fetchone()[0]

        lines = [
            f"**Target:** {target.mention}",
            f"",
            f"**Live Packs:** {total_live} total, {community_live} community tier",
            f"**Master cards table:** {total_cards} cards",
            f"",
            f"**{target.display_name}'s Data:**",
            f"• `user_cards` rows: **{user_cards_count}**",
            f"• Cards matched via JOIN: **{matched_count}**",
            f"• Orphaned (in user_cards, missing from cards): **{user_cards_count - matched_count}**",
            f"• Pack purchases recorded: **{pack_purchases}**",
        ]
        if orphans:
            lines.append(f"• Orphaned card IDs: `{'`, `'.join(orphans)}`")

        if user_cards_count == 0:
            lines.append(f"\n⚠️ **No cards in user_cards at all** — pack grants are failing or no packs exist")
        elif matched_count == 0:
            lines.append(f"\n⚠️ **Cards in user_cards but none in master cards table** — orphan bug")
        elif matched_count < user_cards_count:
            lines.append(f"\n⚠️ **Some orphans** — partial data issue")
        else:
            lines.append(f"\n✅ Data looks correct — check /collection again")

        embed = discord.Embed(title="🔍 Card Debug Info", description="\n".join(lines), color=discord.Color.orange())
        await interaction.followup.send(embed=embed, ephemeral=True)


    @app_commands.command(name="test_battle", description="[DEV] Simulate a battle between two users using their real cards")
    @app_commands.describe(user1="First player", user2="Second player")
    async def test_battle(self, interaction: Interaction, user1: discord.Member, user2: discord.Member):
        if not _is_admin(interaction):
            await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
            return

        await interaction.response.defer()

        from core.battle import BattleWagerConfig, resolve_match
        from discord_cards import ArtistCard

        def card_to_artist(card: dict) -> ArtistCard:
            return ArtistCard(
                card_id=card.get('card_id', ''),
                artist=card.get('name', 'Unknown'),
                song=card.get('title', '') or card.get('name', 'Unknown'),
                youtube_url='', youtube_id='',
                view_count=10_000_000,
                thumbnail=card.get('image_url', '') or '',
                rarity=(card.get('rarity') or 'common').lower(),
            )

        def format_card(card: dict, power: int) -> str:
            icon = RARITY_EMOJI.get((card.get('rarity') or 'common').lower(), "\u26aa")
            name = card.get('name', 'Unknown')
            title = card.get('title', '') or ''
            return f"{icon} **{name}**" + (f" — {title}" if title else "") + f" | Power: **{power}**"

        # Fetch both collections
        u1_cards = self.db.get_user_collection(user1.id)
        u2_cards = self.db.get_user_collection(user2.id)

        if not u1_cards:
            await interaction.followup.send(f"❌ {user1.display_name} has no cards.", ephemeral=True)
            return
        if not u2_cards:
            await interaction.followup.send(f"❌ {user2.display_name} has no cards.", ephemeral=True)
            return

        # Auto-pick strongest card as champion for each
        u1_sorted = sorted(u1_cards, key=compute_card_power, reverse=True)
        u2_sorted = sorted(u2_cards, key=compute_card_power, reverse=True)

        u1_champ = u1_sorted[0]
        u2_champ = u2_sorted[0]
        u1_supports = u1_sorted[1:5]
        u2_supports = u2_sorted[1:5]

        u1_cp = compute_card_power(u1_champ)
        u2_cp = compute_card_power(u2_champ)
        u1_tp = compute_team_power(u1_cp, u1_supports)
        u2_tp = compute_team_power(u2_cp, u2_supports)

        card1 = card_to_artist(u1_champ)
        card2 = card_to_artist(u2_champ)

        # Run the battle engine
        result = resolve_match(card1, card2, "bronze", p1_override=u1_tp, p2_override=u2_tp)
        p1 = result["player1"]
        p2 = result["player2"]

        winner_name = user1.display_name if result["winner"] == 1 else (
            user2.display_name if result["winner"] == 2 else "TIE")

        # Build result embed
        embed = discord.Embed(
            title=f"⚔️ TEST BATTLE: {user1.display_name} vs {user2.display_name}",
            color=discord.Color.gold() if result["winner"] == 0 else
                  discord.Color.blue() if result["winner"] == 1 else discord.Color.red()
        )

        def squad_text(supports):
            if not supports:
                return "_No squad_"
            lines = []
            for s in supports:
                icon = RARITY_EMOJI.get((s.get('rarity') or 'common').lower(), "\u26aa")
                lines.append(f"{icon} {s.get('name','?')} ({compute_card_power(s)})")
            return "\n".join(lines)

        embed.add_field(
            name=f"🔵 {user1.display_name}",
            value=(
                f"**Champion:** {format_card(u1_champ, u1_cp)}\n"
                f"**Squad:**\n{squad_text(u1_supports)}\n"
                f"**Team Power:** {u1_tp}"
                + (" 💥 CRIT!" if p1['critical_hit'] else "") +
                f"\n**Final Power:** {p1['final_power']}"
            ),
            inline=True
        )
        embed.add_field(name="⚡", value="**VS**", inline=True)
        embed.add_field(
            name=f"🔴 {user2.display_name}",
            value=(
                f"**Champion:** {format_card(u2_champ, u2_cp)}\n"
                f"**Squad:**\n{squad_text(u2_supports)}\n"
                f"**Team Power:** {u2_tp}"
                + (" 💥 CRIT!" if p2['critical_hit'] else "") +
                f"\n**Final Power:** {p2['final_power']}"
            ),
            inline=True
        )

        if result["winner"] == 0:
            embed.add_field(name="🤝 Result", value="**IT'S A TIE!**", inline=False)
        elif result["winner"] == 1:
            embed.add_field(name="🏆 Winner", value=f"**{user1.display_name}** wins! (+{p1['gold_reward']}g, +{p1['xp_reward']} XP)", inline=False)
        else:
            embed.add_field(name="🏆 Winner", value=f"**{user2.display_name}** wins! (+{p2['gold_reward']}g, +{p2['xp_reward']} XP)", inline=False)

        embed.set_footer(text="TEST ONLY — no gold or XP was actually awarded")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(DevSupplyCog(bot))

"""
Discord presentation for battle results. This is the ONLY file in the battle
path that imports discord.
"""
from datetime import datetime, timezone
from typing import Dict, List

import discord


def _card_line(card) -> str:
    emoji = card.get_rarity_emoji() if hasattr(card, "get_rarity_emoji") else "⚪"
    name = getattr(card, "artist", getattr(card, "name", "Unknown"))
    song = getattr(card, "song", getattr(card, "title", ""))
    return f"{emoji} {name} - {song}" if song else f"{emoji} {name}"


def create_battle_embed(result: Dict, player1_name: str, player2_name: str) -> discord.Embed:
    """Render a resolve_match() result as a Discord embed."""
    p1, p2 = result["player1"], result["player2"]
    color = {1: 0x2ecc71, 2: 0xe74c3c}.get(result["winner"], 0xf39c12)
    embed = discord.Embed(title="⚔️ BATTLE RESULTS ⚔️", color=color)

    for label, name, p in (("🔵 Player 1", player1_name, p1), ("🔴 Player 2", player2_name, p2)):
        text = f"**{name}**\n{_card_line(p['card'])}\n**Power:** {p['base_power']} → {p['final_power']}"
        if p["critical_hit"]:
            text += " 💥 **CRIT!**"
        embed.add_field(name=label, value=text, inline=True)
        if label.startswith("🔵"):
            embed.add_field(name="⚡", value="**VS**", inline=True)

    embed.add_field(name="━━━━━━━━━━━━━━━━━━━━", value="\u200b", inline=False)

    if result["winner"] == 0:
        result_text = (f"🤝 **TIE!**\nPower difference too small ({result['power_difference']})\n"
                       "Both players get consolation rewards")
    else:
        winner_name = player1_name if result["winner"] == 1 else player2_name
        result_text = f"🏆 **{winner_name.upper()} WINS!**\nVictory by +{result['power_difference']} power!"
    embed.add_field(name="🎯 Result", value=result_text, inline=False)

    if result["winner"] == 0:
        rewards_text = f"**Both:** +{p1['gold_reward']} gold, +{p1['xp_reward']} XP\nWagers returned"
    else:
        rewards_text = (f"**{player1_name}:** +{p1['gold_reward']} gold, +{p1['xp_reward']} XP\n"
                        f"**{player2_name}:** +{p2['gold_reward']} gold, +{p2['xp_reward']} XP")
    embed.add_field(name="💰 Rewards", value=rewards_text, inline=False)
    embed.set_footer(text=f"Wager: {result['wager']} gold ({result['wager_tier']})")
    return embed


class BattleHistory:
    """Per-user in-memory battle record with a Discord stats embed."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.battles: List[Dict] = []
        self.wins = 0
        self.losses = 0
        self.ties = 0

    def add_battle(self, result: Dict, was_player1: bool):
        self.battles.append({
            "result": result,
            "was_player1": was_player1,
            "timestamp": datetime.now(timezone.utc),
        })
        w = result["winner"]
        if w == 0:
            self.ties += 1
        elif (w == 1 and was_player1) or (w == 2 and not was_player1):
            self.wins += 1
        else:
            self.losses += 1

    def win_rate(self) -> float:
        total = self.wins + self.losses
        return self.wins / total if total else 0.0

    def total_battles(self) -> int:
        return len(self.battles)

    def get_stats_embed(self, username: str) -> discord.Embed:
        embed = discord.Embed(title=f"⚔️ Battle Stats - {username}", color=0x3498db)
        embed.add_field(name="📊 Record",
                        value=f"**Wins:** {self.wins}\n**Losses:** {self.losses}\n**Ties:** {self.ties}",
                        inline=True)
        embed.add_field(name="📈 Win Rate", value=f"**{self.win_rate() * 100:.1f}%**", inline=True)
        embed.add_field(name="🎮 Total Battles", value=f"**{self.total_battles()}**", inline=True)
        return embed

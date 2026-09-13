"""
Telegram Mini App presentation for battle results: JSON-safe dict for
/api/battle/* responses. No framework imports needed here.
"""
from typing import Any, Dict


def _card_payload(card: Any) -> Dict[str, Any]:
    if card is None:
        return {}
    if hasattr(card, "to_dict"):
        d = card.to_dict()
        # ArtistCard.to_dict may include non-JSON types; keep known scalar keys
        return {k: v for k, v in d.items() if isinstance(v, (str, int, float, bool, type(None)))}
    return {"name": getattr(card, "name", getattr(card, "artist", "Unknown"))}


def serialize_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Strip card objects out of a resolve_match() result so it can be returned as JSON."""
    def side(p: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "card": _card_payload(p.get("card")),
            "base_power": p["base_power"],
            "final_power": p["final_power"],
            "critical_hit": p["critical_hit"],
            "gold_reward": p["gold_reward"],
            "xp_reward": p["xp_reward"],
        }
    return {
        "winner": result["winner"],
        "player1": side(result["player1"]),
        "player2": side(result["player2"]),
        "power_difference": result["power_difference"],
        "wager": result["wager"],
        "wager_tier": result["wager_tier"],
        "rounds": result.get("rounds", []),
    }

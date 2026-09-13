"""
Seeded tests for core.battle.resolver — no Discord, no DB, no network.
Run: python -m pytest tests/test_resolver.py -v --noconftest
"""
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.battle import (  # noqa: E402
    BattleWagerConfig, CardRef, CRITICAL_MULTIPLIER, MIN_POWER_ADVANTAGE,
    TIE_GOLD, TIE_XP, resolve_match, resolve_round,
)


class FixedRng:
    """Stand-in for random.Random that always returns one value from random()."""
    def __init__(self, value: float):
        self.value = value

    def random(self) -> float:
        return self.value


NO_CRIT = 0.99   # >= CRITICAL_HIT_CHANCE → never crits
ALWAYS_CRIT = 0.0


def card(cid="c", power=50, rarity="common"):
    return CardRef(card_id=cid, name=f"Artist {cid}", title="Song", rarity=rarity, power=power)


# ── determinism ────────────────────────────────────────────────────────────

def test_same_seed_same_result():
    a = resolve_match(card("a", 80), card("b", 75), "standard", rng=random.Random(42))
    b = resolve_match(card("a", 80), card("b", 75), "standard", rng=random.Random(42))
    a.pop("player1")["card"]; b.pop("player1")["card"]  # cards are objects; compare the rest
    a.pop("player2"); b.pop("player2")
    assert a == b


def test_global_random_not_used(monkeypatch):
    """Patching the global random module must not change the outcome."""
    monkeypatch.setattr(random, "random", lambda: 0.0)  # would force crits if used
    r = resolve_match(card("a", 100), card("b", 50), rng=FixedRng(NO_CRIT))
    assert r["player1"]["critical_hit"] is False
    assert r["player2"]["critical_hit"] is False


# ── round resolution ───────────────────────────────────────────────────────

def test_round_higher_power_wins():
    rnd = resolve_round(100, 50, FixedRng(NO_CRIT))
    assert rnd["winner"] == 1
    assert rnd["power_difference"] == 50


def test_round_tie_under_min_advantage():
    rnd = resolve_round(50, 50 + MIN_POWER_ADVANTAGE - 1, FixedRng(NO_CRIT))
    assert rnd["winner"] == 0


def test_round_exact_min_advantage_is_not_tie():
    rnd = resolve_round(50, 50 + MIN_POWER_ADVANTAGE, FixedRng(NO_CRIT))
    assert rnd["winner"] == 2


def test_round_crit_multiplies_power():
    rnd = resolve_round(60, 60, FixedRng(ALWAYS_CRIT))
    assert rnd["critical_hit1"] and rnd["critical_hit2"]
    assert rnd["final_power1"] == int(60 * CRITICAL_MULTIPLIER)
    assert rnd["winner"] == 0  # both crit → still equal


def test_crit_can_flip_outcome():
    """Weaker card crits, stronger does not → weaker wins."""
    class Seq:
        def __init__(self, vals): self.vals = list(vals)
        def random(self): return self.vals.pop(0)
    rnd = resolve_round(70, 90, Seq([ALWAYS_CRIT, NO_CRIT]))
    assert rnd["critical_hit1"] and not rnd["critical_hit2"]
    assert rnd["final_power1"] == 105
    assert rnd["winner"] == 1


# ── match resolution ───────────────────────────────────────────────────────

def test_override_ignores_card_power():
    r = resolve_match(card("a", 10, "common"), card("b", 200, "mythic"),
                      p1_override=130, p2_override=10, rng=FixedRng(NO_CRIT))
    assert r["player1"]["base_power"] == 130
    assert r["player2"]["base_power"] == 10
    assert r["winner"] == 1


def test_card_power_used_when_no_override():
    r = resolve_match(card("a", 90), card("b", 30), rng=FixedRng(NO_CRIT))
    assert r["player1"]["base_power"] == 90
    assert r["winner"] == 1


@pytest.mark.parametrize("tier", list(BattleWagerConfig.TIERS))
def test_winner_and_loser_rewards_per_tier(tier):
    t = BattleWagerConfig.get_tier(tier)
    r = resolve_match(card("a", 90), card("b", 30), tier, rng=FixedRng(NO_CRIT))
    assert r["player1"]["gold_reward"] == t["winner_gold"]
    assert r["player1"]["xp_reward"] == t["winner_xp"]
    assert r["player2"]["gold_reward"] == t["loser_gold"]
    assert r["player2"]["xp_reward"] == t["loser_xp"]
    assert r["wager"] == t["wager_cost"]


def test_tie_rewards():
    r = resolve_match(card("a", 50), card("b", 52), rng=FixedRng(NO_CRIT))
    assert r["winner"] == 0
    for side in ("player1", "player2"):
        assert r[side]["gold_reward"] == TIE_GOLD
        assert r[side]["xp_reward"] == TIE_XP


def test_unknown_tier_falls_back_to_casual():
    r = resolve_match(card("a", 90), card("b", 30), "bronze", rng=FixedRng(NO_CRIT))
    assert r["wager"] == BattleWagerConfig.TIERS["casual"]["wager_cost"]


def test_result_shape_is_stable():
    r = resolve_match(card("a", 90), card("b", 30), rng=random.Random(0))
    assert set(r) == {"winner", "player1", "player2", "power_difference", "wager", "wager_tier", "rounds"}
    assert set(r["player1"]) == {"card", "base_power", "final_power", "critical_hit", "gold_reward", "xp_reward"}
    assert len(r["rounds"]) == 1


# ── platform isolation ─────────────────────────────────────────────────────

def test_core_has_no_discord_import():
    import importlib, pkgutil
    import core
    for mod in pkgutil.walk_packages(core.__path__, "core."):
        src = open(importlib.import_module(mod.name).__file__, encoding="utf-8").read()
        assert "import discord" not in src and "from discord" not in src, mod.name


def test_tma_adapter_serializes_json_safe():
    import json
    from adapters.tma_battle import serialize_result
    r = resolve_match(card("a", 90), card("b", 30), rng=random.Random(0))
    payload = serialize_result(r)
    json.dumps(payload)  # must not raise
    assert payload["player1"]["card"]["name"] == "Artist a"

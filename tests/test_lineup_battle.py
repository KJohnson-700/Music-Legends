"""
Phase 4 seeded tests: genre ring, lineup constraint, abilities, momentum,
round resolution, locked order, and the acceptance simulation
("correct genre reads beat stronger cards with poor ordering").

Run: python -m pytest tests/test_lineup_battle.py -v --noconftest
"""
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.battle import (  # noqa: E402
    Ability, CardRef, GenreFamily, GENRE_RING, Lineup, LineupError, TAG_TO_FAMILY,
    auto_lineup, genre_multiplier, hot_card_ids, momentum_threshold, resolve_family,
    resolve_lineup_match, validate_lineup, view_delta,
)
from core.battle.config import (  # noqa: E402
    AMP_MULT, CRITICAL_MULTIPLIER, GENRE_COUNTER_MULT, GENRE_COUNTERED_MULT,
    MOMENTUM_MULT, RESOLUTION_ORDER,
)
from core.battle.genre import normalize_tag  # noqa: E402


class FixedRng:
    def __init__(self, v): self.v = v
    def random(self): return self.v


NO_CRIT = FixedRng(0.99)


def card(cid, power=100, fam="NEUTRAL", momentum=False):
    return CardRef(card_id=str(cid), name=f"Card {cid}", power=power, genre_family=fam, momentum=momentum)


def lineup(fams, power=100, ability=None, slot=None, start=1):
    return Lineup([card(start + i, power, f) for i, f in enumerate(fams)], ability, slot)


# ── genre ring ─────────────────────────────────────────────────────────────

def test_ring_is_a_single_five_cycle():
    seen, cur = [], GenreFamily.HIP_HOP
    for _ in range(5):
        seen.append(cur)
        cur = GENRE_RING[cur]
    assert cur == GenreFamily.HIP_HOP and len(set(seen)) == 5


@pytest.mark.parametrize("a,b", list(GENRE_RING.items()))
def test_counter_and_countered_multipliers(a, b):
    assert genre_multiplier(a, b) == GENRE_COUNTER_MULT
    assert genre_multiplier(b, a) == GENRE_COUNTERED_MULT


def test_neutral_and_mirror_are_neutral():
    assert genre_multiplier(GenreFamily.POP, GenreFamily.POP) == 1.0
    assert genre_multiplier(GenreFamily.NEUTRAL, GenreFamily.POP) == 1.0
    assert genre_multiplier(GenreFamily.POP, GenreFamily.NEUTRAL) == 1.0
    assert genre_multiplier(GenreFamily.HIP_HOP, GenreFamily.ROCK) == 1.0  # two apart


def test_multiplier_accepts_strings():
    assert genre_multiplier("hip-hop", "pop") == GENRE_COUNTER_MULT
    assert genre_multiplier("garbage", "pop") == 1.0


# ── tag mapping ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tag", ["hip hop", "Hip-Hop", "hip_hop", "rap", "Trap", "HIPHOP"])
def test_messy_hiphop_tags_land_on_one_family(tag):
    assert resolve_family(track_tags=[tag]) == GenreFamily.HIP_HOP


def test_resolution_chain_track_then_artist_then_audiodb():
    assert resolve_family(["seen live", "rock"], ["pop"], "Soul") == GenreFamily.ROCK
    assert resolve_family(["seen live"], ["pop"], "Soul") == GenreFamily.POP
    assert resolve_family(["seen live"], ["favorites"], "Soul") == GenreFamily.SOUL
    assert resolve_family(["seen live"], ["favorites"], "Polka") == GenreFamily.NEUTRAL
    assert resolve_family() == GenreFamily.NEUTRAL


def test_audiodb_compound_genre_strings():
    assert resolve_family(audiodb_genre="Alternative Rock") == GenreFamily.ROCK
    assert resolve_family(audiodb_genre="Pop/Rock") == GenreFamily.POP
    assert resolve_family(audiodb_genre="R&B") == GenreFamily.SOUL


def test_every_mapping_key_is_normalised():
    for key in TAG_TO_FAMILY:
        assert key == normalize_tag(key), key


# ── lineup rules ───────────────────────────────────────────────────────────

def test_lineup_needs_exactly_three_distinct_cards():
    with pytest.raises(LineupError):
        validate_lineup([card(1), card(2)])
    with pytest.raises(LineupError):
        validate_lineup([card(1), card(1), card(2)])
    validate_lineup([card(1), card(2), card(3)])


def test_max_two_per_family_neutral_exempt():
    with pytest.raises(LineupError):
        lineup(["POP", "POP", "POP"])
    lineup(["POP", "POP", "ROCK"])
    lineup(["NEUTRAL", "NEUTRAL", "NEUTRAL"])


def test_amp_and_scout_need_a_valid_slot():
    with pytest.raises(LineupError):
        lineup(["POP", "ROCK", "SOUL"], 100, "amp")
    with pytest.raises(LineupError):
        lineup(["POP", "ROCK", "SOUL"], 100, "scout", 3)
    lineup(["POP", "ROCK", "SOUL"], 100, "swap")
    with pytest.raises(LineupError):
        lineup(["POP", "ROCK", "SOUL"], 100, "teleport")


def test_auto_lineup_respects_family_cap_and_strength():
    cards = [card(1, 130, "POP"), card(2, 129, "POP"), card(3, 128, "POP"),
             card(4, 90, "ROCK"), card(5, 80, "SOUL")]
    picked = auto_lineup(cards)
    assert [c.card_id for c in picked] == ["1", "2", "4"]
    with pytest.raises(LineupError):
        auto_lineup(cards[:2])


# ── round resolution & locked order ────────────────────────────────────────

def test_locked_order_and_breakdown_persisted():
    l1 = lineup(["HIP_HOP", "POP", "ROCK"], 100, "amp", 0)
    l1.cards[0].momentum = True
    l2 = lineup(["POP", "ROCK", "ELECTRONIC"], 100, start=10)
    r = resolve_lineup_match(l1, l2, rng=FixedRng(0.0))  # everyone crits
    b = r["rounds"][0]["player1"]
    assert r["resolution_order"] == list(RESOLUTION_ORDER)
    assert b["base"] == 100
    assert b["after_momentum"] == int(100 * MOMENTUM_MULT)
    assert b["after_genre"] == int(100 * MOMENTUM_MULT * GENRE_COUNTER_MULT)
    assert b["after_ability"] == int(100 * MOMENTUM_MULT * GENRE_COUNTER_MULT * AMP_MULT)
    assert b["final"] == int(100 * MOMENTUM_MULT * GENRE_COUNTER_MULT * AMP_MULT * CRITICAL_MULTIPLIER)
    assert b["critical_hit"] and b["amped"] and b["momentum"] and b["genre_effect"] == "counter"
    assert r["rounds"][0]["player2"]["genre_effect"] == "countered"


def test_first_to_two_stops_early():
    l1 = lineup(["HIP_HOP", "HIP_HOP", "NEUTRAL"], 100)
    l2 = lineup(["POP", "POP", "NEUTRAL"], 100, start=10)
    r = resolve_lineup_match(l1, l2, rng=NO_CRIT)
    assert r["winner"] == 1 and r["decided_by"] == "rounds"
    assert r["rounds_won"] == [2, 0] and len(r["rounds"]) == 2


def test_weaker_card_can_take_a_round_via_counter():
    l1 = lineup(["HIP_HOP", "NEUTRAL", "NEUTRAL"], 90)
    l2 = lineup(["POP", "NEUTRAL", "NEUTRAL"], 105, start=10)
    r = resolve_lineup_match(l1, l2, rng=NO_CRIT)
    assert r["rounds"][0]["winner"] == 1  # 90*1.35=121 vs 105*0.85=89


def test_one_one_tie_resolves_on_total_power():
    # R1: p1 counters; R2: p2 counters; R3: identical → tie round. Totals decide.
    l1 = lineup(["HIP_HOP", "ROCK", "NEUTRAL"], 100)
    l2 = lineup(["POP", "POP", "NEUTRAL"], 100, start=10)
    r = resolve_lineup_match(l1, l2, rng=NO_CRIT)
    assert r["rounds_won"] == [1, 1]
    assert r["rounds"][2]["winner"] == 0
    assert r["decided_by"] in ("total_power", "tie")
    assert r["total_power"][0] == r["total_power"][1]  # symmetric → tie
    assert r["winner"] == 0


def test_swap_triggers_only_after_losing_round_one():
    # p2 loses R1 (POP countered by HIP_HOP). With SWAP, its slots 2/3 exchange:
    # slot 3 (SOUL) now faces p1's HIP_HOP in R2, and SOUL beats HIP_HOP.
    l1 = lineup(["HIP_HOP", "HIP_HOP", "ROCK"], 100)
    l2 = lineup(["POP", "ROCK", "SOUL"], 100, "swap", start=10)
    r = resolve_lineup_match(l1, l2, rng=NO_CRIT)
    assert r["swaps"] == [{"player": 2, "after_round": 1}]
    assert r["rounds"][1]["player2"]["family"] == "SOUL"
    assert r["rounds"][1]["player2"]["genre_effect"] == "counter"
    # Winner of R1 never swaps
    l3 = lineup(["POP", "ROCK", "SOUL"], 100, "swap", start=20)
    l4 = lineup(["ROCK", "ELECTRONIC", "HIP_HOP"], 100, start=30)
    r2 = resolve_lineup_match(l3, l4, rng=NO_CRIT)
    assert r2["swaps"] == []


def test_swap_does_not_mutate_caller_lineup():
    l1 = lineup(["HIP_HOP", "HIP_HOP", "ROCK"], 100)
    l2 = lineup(["POP", "ROCK", "SOUL"], 100, "swap", start=10)
    before = [c.card_id for c in l2.cards]
    resolve_lineup_match(l1, l2, rng=NO_CRIT)
    assert [c.card_id for c in l2.cards] == before


def test_scout_reveals_family_only():
    l1 = lineup(["POP", "ROCK", "SOUL"], 100, "scout", 1)
    l2 = lineup(["ROCK", "ELECTRONIC", "HIP_HOP"], 100, start=10)
    r = resolve_lineup_match(l1, l2, rng=NO_CRIT)
    assert r["reveals"] == [{"player": 1, "slot": 1, "family": "ELECTRONIC"}]


def test_same_seed_same_match():
    l1 = lineup(["HIP_HOP", "POP", "ROCK"], 100, "amp", 2)
    l2 = lineup(["SOUL", "ROCK", "POP"], 102, "swap", start=10)
    a = resolve_lineup_match(l1, l2, "high", rng=random.Random(7))
    b = resolve_lineup_match(l1, l2, "high", rng=random.Random(7))
    assert a == b


def test_rewards_follow_tier():
    from core.battle import BattleWagerConfig
    l1 = lineup(["HIP_HOP", "HIP_HOP", "NEUTRAL"], 100)
    l2 = lineup(["POP", "POP", "NEUTRAL"], 100, start=10)
    r = resolve_lineup_match(l1, l2, "extreme", rng=NO_CRIT)
    t = BattleWagerConfig.get_tier("extreme")
    assert r["player1"]["gold_reward"] == t["winner_gold"]
    assert r["player2"]["gold_reward"] == t["loser_gold"]
    assert r["wager"] == t["wager_cost"]


# ── momentum helpers ───────────────────────────────────────────────────────

def test_momentum_top_ten_percent():
    deltas = {str(i): i * 1000 for i in range(1, 101)}  # 1k..100k
    hot = hot_card_ids(deltas)
    assert hot == {str(i) for i in range(91, 101)}
    assert momentum_threshold([0, -5, None]) is None
    assert hot_card_ids({"a": 0}) == set()
    assert view_delta(100, 150) == 50 and view_delta(150, 100) == 0 and view_delta(None, 5) == 0


# ── acceptance: skill beats stats ──────────────────────────────────────────

def test_correct_genre_reads_beat_stronger_cards_at_better_than_chance():
    """
    A: 95-power cards ordered to counter B's slots. B: 105-power cards, poorly
    ordered (every slot countered). Over many seeded matches A must win far more
    than half. Control: with all-NEUTRAL families B's raw power wins most.
    """
    rng = random.Random(2026)
    wins_a = wins_b_control = 0
    n = 1500
    for _ in range(n):
        a = lineup(["HIP_HOP", "POP", "ROCK"], 95)
        b = lineup(["POP", "ROCK", "ELECTRONIC"], 105, start=10)
        if resolve_lineup_match(a, b, rng=rng)["winner"] == 1:
            wins_a += 1
        a0 = lineup(["NEUTRAL"] * 3, 95)
        b0 = lineup(["NEUTRAL"] * 3, 105, start=10)
        if resolve_lineup_match(a0, b0, rng=rng)["winner"] == 2:
            wins_b_control += 1
    assert wins_a / n > 0.80, wins_a / n
    assert wins_b_control / n > 0.70, wins_b_control / n

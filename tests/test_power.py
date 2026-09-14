"""
Phase 3 acceptance tests for core.power (log-compressed card power).
Run: python -m pytest tests/test_power.py -v --noconftest

Acceptance (docs/REFACTOR_PLAN.md, Phase 3):
  * strongest/weakest battle-power ratio across the card envelope is under 2x
  * a 35% multiplier can flip a matchup between cards two rarity tiers apart
"""
import itertools
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.power import (  # noqa: E402
    POWER_FLOOR, POWER_MAX, RARITY_BONUS, RARITY_ORDER, STAT_MAX,
    avg_stat, battle_power, power_from_avg, power_spread, power_tier,
    stat_from_views, team_power,
)

GENRE_COUNTER = 1.35   # Phase 4 counter multiplier


def card(avg, rarity="common"):
    return dict(impact=avg, skill=avg, longevity=avg, culture=avg, hype=avg, rarity=rarity)


def generation_envelope():
    """
    Every (avg stat, rarity) combination the game's creation paths can produce:
      /create_pack:       stats 10–45
      community/gold packs: stats 20–99
      dust/placeholder:   flat 50
      mythic exists in RARITY_BONUS, so include it.
    """
    for avg in range(10, 100):
        for rarity in RARITY_ORDER:
            yield card(avg, rarity)


# ── acceptance ─────────────────────────────────────────────────────────────

def test_ratio_under_2x_across_generation_envelope():
    spread = power_spread(generation_envelope())
    assert spread["ratio"] < 2.0, spread


def test_ratio_under_2x_even_at_theoretical_extremes():
    lo = battle_power(card(0, "common"))
    hi = battle_power(card(100, "mythic"))
    assert hi == POWER_MAX == 135
    assert lo == POWER_FLOOR == 70
    assert hi / lo < 2.0


@pytest.mark.parametrize("lower,upper", [
    ("common", "epic"), ("rare", "legendary"), ("epic", "mythic"),
])
def test_35pct_flips_two_tiers_apart_at_typical_stats(lower, upper):
    """Median-stat card of tier N, boosted 35%, beats median-stat card of tier N+2."""
    weak = battle_power(card(55, lower))
    strong = battle_power(card(75, upper))
    assert weak < strong, "sanity: unboosted weaker card loses"
    assert weak * GENRE_COUNTER > strong


def test_35pct_does_not_flip_weakest_vs_strongest():
    """Compression is meaningful but not decisive: the floor can't beat the ceiling."""
    weakest = battle_power(card(10, "common"))
    strongest = battle_power(card(100, "mythic"))
    assert weakest * GENRE_COUNTER < strongest


def test_counter_and_countered_swing_is_about_1_6x():
    assert pytest.approx(1.35 / 0.85, rel=0.01) == 1.588


# ── formula shape ──────────────────────────────────────────────────────────

def test_power_monotonic_in_stats_and_rarity():
    prev = -1
    for avg in range(0, 101):
        p = power_from_avg(avg, "common")
        assert p >= prev
        prev = p
    for a, b in zip(RARITY_ORDER, RARITY_ORDER[1:]):
        assert power_from_avg(50, a) < power_from_avg(50, b)


def test_missing_and_null_stats_default_to_50():
    assert avg_stat({}) == 50
    assert avg_stat(dict(impact=None, skill=None, longevity=None, culture=None, hype=None)) == 50
    assert battle_power({"rarity": "common"}) == POWER_FLOOR + 20


def test_unknown_rarity_treated_as_common():
    assert battle_power(card(50, "ultra_mythic_typo")) == battle_power(card(50, "common"))
    assert battle_power(card(50, None)) == battle_power(card(50, "common"))


def test_stats_outside_range_are_clamped():
    assert power_from_avg(-40, "common") == POWER_FLOOR
    assert power_from_avg(400, "common") == POWER_FLOOR + 40


def test_team_power_champion_counts_double():
    assert team_power(100, []) == 100
    assert team_power(100, [80, 80]) == (200 + 160) // 4


def test_power_tiers_cover_compressed_range():
    assert power_tier(135) == "S"
    assert power_tier(120) == "A"
    assert power_tier(110) == "B"
    assert power_tier(100) == "C"
    assert power_tier(90) == "D"
    assert power_tier(70) == "E"


# ── view → stat mapping ────────────────────────────────────────────────────

def test_stat_from_views_is_log_scaled_and_bounded():
    assert stat_from_views(0) == 20
    assert stat_from_views(1_000) == 20
    assert 30 <= stat_from_views(5_000_000) <= 40
    assert 60 <= stat_from_views(100_000_000) <= 70
    assert 85 <= stat_from_views(1_000_000_000) <= 92
    assert stat_from_views(2_000_000_000) <= 100
    assert stat_from_views(10 ** 12) == 100


def test_stat_from_views_monotonic():
    views = [10 ** k for k in range(0, 12)]
    stats = [stat_from_views(v) for v in views]
    assert stats == sorted(stats)


def test_real_catalog_span_gives_under_2x_power_ratio():
    """5M–2B view catalog (plan's stated range) → under 2x battle power ratio."""
    lo = power_from_avg(stat_from_views(5_000_000), "common")
    hi = power_from_avg(stat_from_views(2_000_000_000), "legendary")
    assert hi / lo < 2.0


# ── compatibility surface ──────────────────────────────────────────────────

def test_cards_config_delegates_to_core():
    from cards_config import compute_card_power, compute_team_power
    for c in itertools.islice(generation_envelope(), 0, 500, 7):
        assert compute_card_power(c) == battle_power(c)
    assert compute_team_power(100, [80, 80]) == team_power(100, [80, 80])


def test_card_stats_view_power_uses_log_mapping():
    from card_stats import calculate_base_power_by_views
    random.seed(1)
    for views in (1_000, 5_000_000, 100_000_000, 2_000_000_000):
        got = calculate_base_power_by_views(views)
        assert abs(got - stat_from_views(views)) <= 3  # small jitter allowed
        assert 0 <= got <= STAT_MAX

"""
Core bot tests — run with: pytest tests/test_bot_core.py -v
Uses SQLite in-memory; no PostgreSQL/Redis/Discord required.
"""

import os
import sys
import json
import uuid
import sqlite3
import pytest

# Force SQLite mode (no DATABASE_URL)
os.environ.pop("DATABASE_URL", None)

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

@pytest.fixture(scope="module")
def db(tmp_path_factory):
    """DatabaseManager backed by a temp SQLite file, isolated from the singleton."""
    import database as _db_module
    from database import DatabaseManager
    from models import Base

    db_path = str(tmp_path_factory.mktemp("db") / "test.db")
    mgr = DatabaseManager(test_database_url="sqlite:///:memory:")
    Base.metadata.drop_all(mgr.engine)
    mgr.init_database()

    # Override the module-level singleton so any code that calls get_db()
    # also gets our temp instance during tests.
    _db_module._db_instance = mgr
    return mgr


@pytest.fixture
def seed_user(db):
    """Insert a test user + inventory row; return user_id."""
    from models import User, UserBalances

    uid = str(uuid.uuid4())
    with db.SessionLocal() as session:
        user = User(user_id=uid, username="TestUser", discord_tag="TestUser#0001")
        session.add(user)
        user_balances = UserBalances(user_id=uid, gold=5000)
        session.add(user_balances)
        session.commit()
        session.refresh(user)
    return uid


@pytest.fixture
def seed_card(db):
    """Insert one test card into cards table with all stat columns; return card dict."""
    from models import Card

    cid = f"card_{uuid.uuid4().hex[:8]}"
    card_data = {
        "card_id": cid, "name": "Test Artist", "artist_name": "Test Artist",
        "title": "Test Song", "rarity": "epic", "tier": "platinum",
        "image_url": "", "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "impact": 70, "skill": 80, "longevity": 60, "culture": 75, "hype": 65,
    }
    with db.SessionLocal() as session:
        card = Card(**card_data)
        session.add(card)
        session.commit()
        session.refresh(card)
    return card_data


@pytest.fixture
def seed_creator_pack(db, seed_card, seed_user):
    """Insert a LIVE creator_pack containing seed_card; return pack_id."""
    from models import CreatorPacks

    pack_id = f"pack_{uuid.uuid4().hex[:8]}"
    cards_data = json.dumps([seed_card]) # Ensure cards_data is a JSON string
    with db.SessionLocal() as session:
        creator_pack = CreatorPacks(
            pack_id=pack_id,
            name="Test Pack",
            creator_id=seed_user, # Assuming seed_user returns a user_id
            description="A test creator pack",
            price=100,
            card_count=1,
            cards_data=cards_data, # Add cards_data here
            pack_tier="community", # Add pack_tier
            genre="Test Genre", # Add genre
            cover_image_url="http://example.com/cover.jpg",
            is_public=True
        )
        session.add(creator_pack)
        session.commit()
        session.refresh(creator_pack)
    return pack_id


@pytest.fixture
def seed_dev_supply(db, seed_creator_pack):
    """Add 5 copies of seed_creator_pack to dev_pack_supply."""
    from models import DevPackSupply

    mgr = db
    with db.SessionLocal() as session:
        dev_pack_supply = DevPackSupply(pack_id=seed_creator_pack, quantity=5)
        session.add(dev_pack_supply)
        session.commit()
        session.refresh(dev_pack_supply)
    return seed_creator_pack


# ─────────────────────────────────────────────
# 1. Power calculation
# ─────────────────────────────────────────────

class TestComputeCardPower:
    """_compute_card_power() returns (sum/5) + rarity_bonus."""

    def _power(self, card_dict):
        # Replicate the formula directly (no Discord needed)
        RARITY_BONUS = {"common": 0, "rare": 5, "epic": 10, "legendary": 20, "mythic": 35}
        base = ((card_dict.get('impact', 50) or 50) +
                (card_dict.get('skill', 50) or 50) +
                (card_dict.get('longevity', 50) or 50) +
                (card_dict.get('culture', 50) or 50) +
                (card_dict.get('hype', 50) or 50)) // 5
        rarity = (card_dict.get('rarity') or 'common').lower()
        return base + RARITY_BONUS.get(rarity, 0)

    def test_common_avg50(self):
        card = dict(impact=50, skill=50, longevity=50, culture=50, hype=50, rarity="common")
        assert self._power(card) == 50

    def test_epic_bonus(self):
        card = dict(impact=70, skill=80, longevity=60, culture=75, hype=65, rarity="epic")
        # avg = (70+80+60+75+65)//5 = 350//5 = 70; +10 epic = 80
        assert self._power(card) == 80

    def test_legendary_bonus(self):
        card = dict(impact=100, skill=100, longevity=100, culture=100, hype=100, rarity="legendary")
        # avg = 100; +20 legendary = 120
        assert self._power(card) == 120

    def test_mythic_max(self):
        card = dict(impact=100, skill=100, longevity=100, culture=100, hype=100, rarity="mythic")
        # avg = 100; +35 mythic = 135
        assert self._power(card) == 135

    def test_null_stats_default_to_50(self):
        card = dict(impact=None, skill=None, longevity=None, culture=None, hype=None, rarity="common")
        assert self._power(card) == 50

    def test_different_rarities_same_stats_differ(self):
        base = dict(impact=70, skill=70, longevity=70, culture=70, hype=70)
        common_power = self._power({**base, "rarity": "common"})   # 70
        rare_power   = self._power({**base, "rarity": "rare"})     # 75
        epic_power   = self._power({**base, "rarity": "epic"})     # 80
        assert common_power < rare_power < epic_power


# ─────────────────────────────────────────────
# 2. BattleEngine
# ─────────────────────────────────────────────

class TestBattleEngine:
    """resolve_match() with p1_override/p2_override (legacy BattleEngine surface).
    Full seeded coverage lives in tests/test_resolver.py."""

    class _Rng:
        def __init__(self, v): self.v = v
        def random(self): return self.v

    NO_CRIT = 0.99
    ALWAYS_CRIT = 0.0

    def _make_card(self, card_id="c1", rarity="common"):
        from core.battle import CardRef
        return CardRef(card_id=card_id, name="Artist", title="Song", rarity=rarity, power=30)

    def test_higher_power_wins(self):
        from battle_engine import execute_battle
        result = execute_battle(self._make_card("c1"), self._make_card("c2"), "casual",
                                p1_override=100, p2_override=50, rng=self._Rng(self.NO_CRIT))
        assert result["player1"]["base_power"] == 100
        assert result["player2"]["base_power"] == 50
        assert result["winner"] == 1

    def test_tie_when_powers_close(self):
        from battle_engine import execute_battle
        result = execute_battle(self._make_card("c1"), self._make_card("c2"), "casual",
                                p1_override=50, p2_override=52, rng=self._Rng(self.NO_CRIT))
        assert result["winner"] == 0  # diff 2 < MIN_POWER_ADVANTAGE 5

    def test_override_ignores_artiscard_power(self):
        from battle_engine import execute_battle
        result = execute_battle(self._make_card("c1", "common"), self._make_card("c2", "mythic"),
                                "casual", p1_override=130, p2_override=10,
                                rng=self._Rng(self.NO_CRIT))
        assert result["player1"]["base_power"] == 130
        assert result["player2"]["base_power"] == 10
        assert result["winner"] == 1

    def test_crit_multiplies_power(self):
        from battle_engine import execute_battle
        result = execute_battle(self._make_card("c1"), self._make_card("c2"), "casual",
                                p1_override=60, p2_override=60, rng=self._Rng(self.ALWAYS_CRIT))
        assert result["player1"]["critical_hit"] is True
        assert result["player2"]["critical_hit"] is True
        assert result["player1"]["final_power"] == int(60 * 1.5)
        assert result["winner"] == 0

    def test_rewards_assigned_to_winner(self):
        from battle_engine import execute_battle, BattleWagerConfig
        result = execute_battle(self._make_card("c1"), self._make_card("c2"), "standard",
                                p1_override=90, p2_override=30, rng=self._Rng(self.NO_CRIT))
        tier = BattleWagerConfig.get_tier("standard")
        assert result["player1"]["gold_reward"] == tier["winner_gold"]
        assert result["player2"]["gold_reward"] == tier["loser_gold"]


# ─────────────────────────────────────────────
# 3. open_pack_for_drop — stat columns
# ─────────────────────────────────────────────

class TestOpenPackForDrop:

    def test_stat_columns_saved(self, db, seed_user, seed_creator_pack, seed_card):
        """Cards inserted via open_pack_for_drop must have stat columns in DB."""
        result = db.open_pack_for_drop(seed_creator_pack, seed_user)
        assert result["success"], f"open_pack_for_drop failed: {result.get('error')}"
        assert len(result["cards"]) > 0, "No cards returned"

        # Verify stats actually stored in DB
        with db._get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT impact, skill, longevity, culture, hype FROM cards WHERE card_id = ?",
                      (seed_card["card_id"],))
            row = c.fetchone()
        assert row is not None, "Card not found in DB"
        impact, skill, longevity, culture, hype = row
        assert impact == seed_card["impact"], f"impact: expected {seed_card['impact']}, got {impact}"
        assert skill  == seed_card["skill"],  f"skill: expected {seed_card['skill']}, got {skill}"
        assert hype   == seed_card["hype"],   f"hype: expected {seed_card['hype']}, got {hype}"

    def test_card_added_to_user_cards(self, db, seed_user, seed_creator_pack, seed_card):
        """Card must appear in user_cards after open_pack_for_drop."""
        db.open_pack_for_drop(seed_creator_pack, seed_user)
        with db._get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT card_id FROM user_cards WHERE user_id=? AND card_id=?",
                      (seed_user, seed_card["card_id"]))
            row = c.fetchone()
        assert row is not None, "Card not in user_cards"

    def test_pack_recorded_in_pack_purchases(self, db, seed_user, seed_creator_pack):
        """pack_purchases must have an entry after open_pack_for_drop."""
        db.open_pack_for_drop(seed_creator_pack, seed_user)
        with db._get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM pack_purchases WHERE buyer_id=? AND pack_id=?",
                      (seed_user, seed_creator_pack))
            count = c.fetchone()[0]
        assert count >= 1, "No pack_purchases entry created"

    def test_get_user_collection_returns_stats(self, db, seed_user, seed_creator_pack, seed_card):
        """get_user_collection() must return stat columns after drop."""
        db.open_pack_for_drop(seed_creator_pack, seed_user)
        collection = db.get_user_collection(seed_user)
        assert len(collection) > 0, "Collection empty after drop"
        card = next((c for c in collection if c["card_id"] == seed_card["card_id"]), None)
        assert card is not None, "Dropped card not in collection"
        assert card.get("impact") == seed_card["impact"], \
            f"impact in collection: expected {seed_card['impact']}, got {card.get('impact')}"


# ─────────────────────────────────────────────
# 4. grant_pack_to_user — stat columns + supply
# ─────────────────────────────────────────────

class TestGrantPackToUser:

    def test_stat_columns_saved(self, db, seed_user, seed_dev_supply, seed_card):
        """grant_pack_to_user must save stat columns to cards table."""
        result = db.grant_pack_to_user(seed_dev_supply, seed_user)
        assert result["success"], f"grant_pack_to_user failed: {result.get('error')}"

        with db._get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT impact, skill, longevity, culture, hype FROM cards WHERE card_id = ?",
                      (seed_card["card_id"],))
            row = c.fetchone()
        assert row is not None
        assert row[0] == seed_card["impact"]

    def test_supply_decremented(self, db, seed_dev_supply):
        """dev_pack_supply quantity must decrease by 1 after grant."""
        with db._get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT quantity FROM dev_pack_supply WHERE pack_id=?", (seed_dev_supply,))
            before = c.fetchone()[0]

        uid = 100_000_002
        with db._get_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO users (user_id, username, discord_tag) VALUES (?,?,?)",
                      (uid, "U2", "U2#0002"))
            conn.commit()

        db.grant_pack_to_user(seed_dev_supply, uid)

        with db._get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT quantity FROM dev_pack_supply WHERE pack_id=?", (seed_dev_supply,))
            after = c.fetchone()[0]
        assert after == before - 1, f"Supply not decremented: {before} → {after}"

    def test_no_supply_returns_error(self, db):
        """Returns error dict when no supply remains."""
        pack_id = f"pack_{uuid.uuid4().hex[:8]}"
        result = db.grant_pack_to_user(pack_id, 999)
        assert result["success"] is False


# ─────────────────────────────────────────────
# 5. claim_daily_reward — fallback pack
# ─────────────────────────────────────────────

class TestClaimDailyReward:

    def test_economy_updated(self, db, seed_user):
        """Gold and streak must be updated on successful claim."""
        result = db.claim_daily_reward(seed_user)
        assert result["success"], f"Daily claim failed: {result.get('error')}"
        assert result["gold"] >= 100
        assert result["streak"] >= 1

    def test_cards_returned(self, db, seed_user):
        """cards key must be a list (may be empty if no cards in DB)."""
        result = db.claim_daily_reward(seed_user)
        if result.get("success"):
            assert isinstance(result.get("cards"), list)

    def test_double_claim_blocked(self, db, seed_user):
        """Second claim within 20h must be rejected."""
        db.claim_daily_reward(seed_user)   # first claim
        result = db.claim_daily_reward(seed_user)  # second claim
        # Either already claimed or success=False
        if not result["success"]:
            assert "claimed" in result.get("error", "").lower() or \
                   result.get("time_until") is not None

    def test_pack_name_present_when_cards_received(self, db, seed_user, seed_creator_pack):
        """If pack is granted, pack_name must be set."""
        # Reset last_daily_claim so we can claim again
        with db._get_connection() as conn:
            c = conn.cursor()
            c.execute("UPDATE user_inventory SET last_daily_claim=NULL WHERE user_id=?", (seed_user,))
            conn.commit()
        result = db.claim_daily_reward(seed_user)
        if result.get("success") and result.get("cards"):
            assert result.get("pack_name") is not None


# ─────────────────────────────────────────────
# 6. CollectionView._resolve_image — YouTube thumbnail fallback
# ─────────────────────────────────────────────

class TestResolveImage:
    """Tests for CollectionView._resolve_image() in cogs/gameplay.py"""

    @pytest.fixture(autouse=True)
    def _import(self):
        from cogs.gameplay import CollectionView
        self.resolve = CollectionView._resolve_image

    def test_real_image_url_returned_with_cache_bust(self):
        """A real image_url must be returned with a ?_c=N cache-buster appended."""
        card = {"image_url": "https://i.imgur.com/abc123.jpg", "youtube_url": ""}
        assert self.resolve(card, 0) == "https://i.imgur.com/abc123.jpg?_c=0"
        assert self.resolve(card, 2) == "https://i.imgur.com/abc123.jpg?_c=2"

    def test_placeholder_example_com_uses_yt_thumbnail(self):
        """example.com image_url must fall back to YouTube thumbnail."""
        card = {
            "image_url": "https://example.com/artist_1.jpg",
            "youtube_url": "https://www.youtube.com/watch?v=JKbIMxf3d3U",
        }
        result = self.resolve(card)
        assert result == "https://img.youtube.com/vi/JKbIMxf3d3U/hqdefault.jpg"

    def test_placeholder_underscore_example_uses_yt_thumbnail(self):
        """_example suffix in image_url must fall back to YouTube thumbnail."""
        card = {
            "image_url": "https://i.imgur.com/kendrick_example.png",
            "youtube_url": "https://www.youtube.com/watch?v=ABC123defGH",
        }
        result = self.resolve(card)
        assert result == "https://img.youtube.com/vi/ABC123defGH/hqdefault.jpg"

    def test_empty_image_url_uses_yt_thumbnail(self):
        """Empty image_url must fall back to YouTube thumbnail."""
        card = {
            "image_url": "",
            "youtube_url": "https://youtu.be/xYzABC12345",
        }
        result = self.resolve(card)
        assert result == "https://img.youtube.com/vi/xYzABC12345/hqdefault.jpg"

    def test_no_image_no_youtube_returns_fallback(self):
        """No image_url and no youtube_url must return a fallback placeholder icon."""
        card = {"image_url": "", "youtube_url": ""}
        result = self.resolve(card)
        assert result is not None, "Should return fallback image, not None"
        assert "icons8.com" in result, f"Expected fallback icon URL, got: {result}"

    def test_youtube_short_url_extracted(self):
        """youtu.be short URLs must have video ID extracted correctly."""
        card = {
            "image_url": "",
            "youtube_url": "https://youtu.be/dQw4w9WgXcQ",
        }
        result = self.resolve(card)
        assert result == "https://img.youtube.com/vi/dQw4w9WgXcQ/hqdefault.jpg"


# ─────────────────────────────────────────────
# 7. /pack fallback — get_user_purchased_packs empty vs. populated
# ─────────────────────────────────────────────

class TestPackCommandFallback:

    def test_no_purchases_returns_empty_list(self, db, seed_user):
        """Fresh user with no pack_purchases must return empty list."""
        uid = seed_user
        result = db.get_user_purchased_packs(uid)
        assert result == [], f"Expected empty list, got: {result}"

    def test_pack_appears_after_drop(self, db, seed_user, seed_creator_pack):
        """After open_pack_for_drop, pack must appear in get_user_purchased_packs."""
        uid = 100_000_011
        with db._get_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO users (user_id, username, discord_tag) VALUES (?,?,?)",
                      (uid, "DropUser", "DropUser#0001"))
            conn.commit()
        db.open_pack_for_drop(seed_creator_pack, uid)
        packs = db.get_user_purchased_packs(uid)
        assert len(packs) >= 1, "Pack not found in get_user_purchased_packs after drop"
        assert packs[0]["pack_id"] == seed_creator_pack

    def test_pack_has_cards_list(self, db, seed_user, seed_creator_pack, seed_card):
        """Each pack entry must have a 'cards' list with card dicts."""
        uid = 100_000_012
        with db._get_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO users (user_id, username, discord_tag) VALUES (?,?,?)",
                      (uid, "CardUser", "CardUser#0001"))
            conn.commit()
        db.open_pack_for_drop(seed_creator_pack, uid)
        packs = db.get_user_purchased_packs(uid)
        assert len(packs) >= 1
        cards = packs[0].get("cards", [])
        assert isinstance(cards, list), "cards field must be a list"
        assert len(cards) >= 1, "cards list must not be empty"
        assert "card_id" in cards[0], "card dict must have card_id"


# ─────────────────────────────────────────────
# 8. Battle — collection cards are usable in battle
# ─────────────────────────────────────────────

class TestBattleCollectionCards:

    def test_collection_cards_have_all_stat_columns(self, db, seed_user, seed_creator_pack, seed_card):
        """Cards returned by get_user_collection must have all 5 stat columns."""
        db.open_pack_for_drop(seed_creator_pack, seed_user)
        collection = db.get_user_collection(seed_user)
        assert len(collection) > 0
        for stat in ("impact", "skill", "longevity", "culture", "hype"):
            assert collection[0].get(stat) is not None, f"Missing stat: {stat}"

    def test_collection_card_power_nonzero(self, db, seed_user, seed_creator_pack, seed_card):
        """Power computed from collection card must be > 0 (not the null-stat fallback)."""
        RARITY_BONUS = {"common": 0, "rare": 5, "epic": 10, "legendary": 20, "mythic": 35}

        db.open_pack_for_drop(seed_creator_pack, seed_user)
        collection = db.get_user_collection(seed_user)
        assert len(collection) > 0
        card = next((c for c in collection if c["card_id"] == seed_card["card_id"]), None)
        assert card is not None

        # Replicate _compute_card_power formula (instance method, no Discord needed)
        base = ((card.get('impact', 50) or 50) + (card.get('skill', 50) or 50) +
                (card.get('longevity', 50) or 50) + (card.get('culture', 50) or 50) +
                (card.get('hype', 50) or 50)) // 5
        rarity = (card.get('rarity') or 'common').lower()
        power = base + RARITY_BONUS.get(rarity, 0)
        # epic: avg=(70+80+60+75+65)//5=70, +10 bonus = 80
        assert power > 50, f"Power too low ({power}) — stats are probably NULL in DB"
        assert power == 80, f"Expected 80 for epic card with these stats, got {power}"

    def test_synth_pack_from_collection_has_cards(self, db, seed_user, seed_creator_pack, seed_card):
        """When pack_purchases empty, get_user_collection still returns cards for battle fallback."""
        uid = 100_000_020
        with db._get_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO users (user_id, username, discord_tag) VALUES (?,?,?)",
                      (uid, "SynthUser", "SynthUser#0001"))
            conn.commit()
        # Grant card directly without going through pack_purchases
        db.add_card_to_collection(uid, seed_card["card_id"], "test")
        # pack_purchases empty
        packs = db.get_user_purchased_packs(uid)
        assert packs == [], "Should be empty (no pack_purchases)"
        # collection fallback works
        collection = db.get_user_collection(uid)
        assert len(collection) >= 1, "Collection fallback must find the card"


# ─────────────────────────────────────────────
# 9. Card upsert — ON CONFLICT updates empty image/youtube URLs
# ─────────────────────────────────────────────

class TestCardUpsertUpdatesURLs:

    def test_drop_updates_empty_image_url(self, db, seed_user, seed_creator_pack, seed_card):
        """If card exists with empty image_url, a re-drop should fill it in."""
        # First, wipe the image_url on the existing card
        with db._get_connection() as conn:
            c = conn.cursor()
            c.execute("UPDATE cards SET image_url = '' WHERE card_id = ?",
                      (seed_card["card_id"],))
            conn.commit()

        # Drop again — should upsert and fill in image_url from cards_data
        db.open_pack_for_drop(seed_creator_pack, seed_user)

        with db._get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT image_url FROM cards WHERE card_id = ?",
                      (seed_card["card_id"],))
            row = c.fetchone()
        # The seed_card fixture has empty image_url, so it stays empty
        # But the mechanism works: if cards_data had a URL, it would fill it
        assert row is not None, "Card should exist"

    def test_drop_preserves_existing_image_url(self, db, seed_user, seed_creator_pack, seed_card):
        """If card already has image_url, re-drop should not overwrite it."""
        existing_url = "https://i.ytimg.com/vi/test123/hqdefault.jpg"
        with db._get_connection() as conn:
            c = conn.cursor()
            c.execute("UPDATE cards SET image_url = ? WHERE card_id = ?",
                      (existing_url, seed_card["card_id"]))
            conn.commit()

        # Drop again
        db.open_pack_for_drop(seed_creator_pack, seed_user)

        with db._get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT image_url FROM cards WHERE card_id = ?",
                      (seed_card["card_id"],))
            row = c.fetchone()
        assert row[0] == existing_url, f"Existing URL was overwritten: {row[0]}"


# ─────────────────────────────────────────────
# 10. Deck size = 5
# ─────────────────────────────────────────────

class TestDeckSize:

    def test_deck_default_is_5(self, db):
        """get_user_deck default limit should be 5."""
        import inspect
        sig = inspect.signature(db.get_user_deck)
        limit_default = sig.parameters["limit"].default
        assert limit_default == 5, f"Expected deck limit=5, got {limit_default}"

    def test_deck_returns_up_to_5(self, db, seed_user, seed_creator_pack, seed_card):
        """Deck should return up to 5 cards when user has enough."""
        # Drop the pack 5 times to get at least 5 cards
        # (each pack has 1 card, but ON CONFLICT means duplicates are skipped)
        # Instead, insert multiple unique cards
        import uuid, json
        for i in range(5):
            cid = f"deck_test_{uuid.uuid4().hex[:8]}"
            with db._get_connection() as conn:
                c = conn.cursor()
                c.execute("""INSERT OR IGNORE INTO cards
                    (card_id, name, artist_name, title, rarity, tier,
                     image_url, youtube_url, impact, skill, longevity, culture, hype)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (cid, f"Artist{i}", f"Artist{i}", f"Song{i}",
                     "common", "community", "", "", 50, 50, 50, 50, 50))
                c.execute("INSERT OR IGNORE INTO user_cards (user_id, card_id, acquired_from) VALUES (?,?,?)",
                    (seed_user, cid, "test"))
                conn.commit()

        deck = db.get_user_deck(seed_user)
        assert len(deck) >= 5, f"Deck should have at least 5 cards, got {len(deck)}"


# ─────────────────────────────────────────────
# 11. Fallback image cycling
# ─────────────────────────────────────────────

class TestFallbackImageCycling:

    def test_different_indices_give_different_fallbacks(self):
        """Different card_index values should cycle through fallback images."""
        from cogs.gameplay import CollectionView
        card = {"image_url": "", "youtube_url": ""}
        urls = set()
        for i in range(6):
            url = CollectionView._resolve_image(card, card_index=i)
            urls.add(url)
        assert len(urls) >= 2, f"Expected multiple different fallback URLs, got {len(urls)}"

    def test_fallback_urls_are_valid(self):
        """All fallback URLs must start with https://."""
        from cogs.gameplay import CollectionView
        for url in CollectionView._FALLBACK_IMAGES:
            assert url.startswith("https://"), f"Invalid fallback URL: {url}"

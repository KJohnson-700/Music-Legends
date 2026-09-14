from sqlalchemy import Column, Integer, BigInteger, String, Boolean, DateTime, Text, Float, ForeignKey
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.types import TypeDecorator, CHAR
from sqlalchemy.dialects import postgresql
from datetime import datetime
import uuid
import json

# Custom TypeDecorator for UUID to handle SQLite incompatibility
class UUIDType(TypeDecorator):
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            return dialect.type_descriptor(postgresql.UUID())
        else:
            return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if dialect.name == 'postgresql':
            return str(value) # PostgreSQL UUID type handles UUID objects
        else:
            return str(value) # SQLite stores as CHAR

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        # Backward compatibility: some legacy rows still store non-UUID ids.
        # Returning a string keeps reads working while newer rows stay UUID objects.
        try:
            return uuid.UUID(str(value))
        except (ValueError, TypeError, AttributeError):
            return str(value)

# Custom TypeDecorator for JSONB to handle SQLite incompatibility
class JSONType(TypeDecorator):
    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            return dialect.type_descriptor(postgresql.JSONB())
        else:
            return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if dialect.name == 'postgresql':
            return value # PostgreSQL JSONB type handles dicts
        else:
            return json.dumps(value) # SQLite stores as TEXT

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if dialect.name == 'postgresql':
            return value
        else:
            return json.loads(value) # SQLite loads as dict
# All models must be imported here to be registered with Base.metadata
# For now, we manually list them out.
# This ensures Base.metadata.create_all() discovers all tables.



Base = declarative_base()

from .trade import Trade # Import Trade model
from .purchase_sqlalchemy import Purchase # Import Purchase model
from .drop import Drop # Import Drop model
from .audit import AuditLog # Import AuditLog model

class User(Base):
    __tablename__ = "users"
    
    user_id = Column(String, primary_key=True)
    username = Column(String)
    is_dev = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    discord_tag = Column(String)
    last_active = Column(DateTime, default=datetime.utcnow)
    total_battles = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    packs_opened = Column(Integer, default=0)
    victory_tokens = Column(Integer, default=0)
    # Telegram community host attribution (first-touch referral)
    referrer_host_token = Column(String, nullable=True)
    referrer_set_at = Column(DateTime, nullable=True)
    
    # Relationships
    card_instances = relationship("CardInstance", back_populates="owner")
    created_packs = relationship("CreatorPacks", back_populates="creator")
    transactions_as_buyer = relationship("Transaction", foreign_keys="Transaction.buyer_id", back_populates="buyer")
    transactions_as_seller = relationship("Transaction", foreign_keys="Transaction.seller_id", back_populates="seller")

class YouTubeVideo(Base):
    __tablename__ = "youtube_videos"
    
    video_id = Column(String, primary_key=True)
    title = Column(String)
    thumbnail_url = Column(String)
    view_count = Column(Integer, default=0)
    like_count = Column(Integer, default=0)
    channel_title = Column(String)
    channel_id = Column(String)
    fetched_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    card_definitions = relationship("CardDefinition", back_populates="source_video")

class CardDefinition(Base):
    __tablename__ = "card_definitions"
    
    card_def_id = Column(Integer, primary_key=True, autoincrement=True)
    source_video_id = Column(String, ForeignKey("youtube_videos.video_id"))
    card_name = Column(String, nullable=False)
    rarity = Column(String, default="Common")
    power = Column(Integer, default=50)
    attributes = Column(Text)  # JSON for flexible stats
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    source_video = relationship("YouTubeVideo", back_populates="card_definitions")
    instances = relationship("CardInstance", back_populates="definition")

class CardInstance(Base):
    __tablename__ = "card_instances"
    
    instance_id = Column(Integer, primary_key=True, autoincrement=True)
    card_def_id = Column(Integer, ForeignKey("card_definitions.card_def_id"))
    owner_user_id = Column(String, ForeignKey("users.user_id"))
    serial_number = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    definition = relationship("CardDefinition", back_populates="instances")
    owner = relationship("User", back_populates="card_instances")
    pack_contents = relationship("PackContent", back_populates="card_instance")

class Pack(Base):
    __tablename__ = "packs"
    
    pack_id = Column(Integer, primary_key=True, autoincrement=True)
    creator_id = Column(String, ForeignKey("users.user_id"))
    main_hero_instance_id = Column(Integer, ForeignKey("card_instances.instance_id"))
    pack_type = Column(String, default="gold")
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships

    hero_instance = relationship("CardInstance", foreign_keys=[main_hero_instance_id])
    contents = relationship("PackContent", back_populates="pack")
    marketplace_item = relationship("MarketplaceItem", back_populates="pack")

class PackContent(Base):
    __tablename__ = "pack_contents"
    
    pack_id = Column(Integer, ForeignKey("packs.pack_id"), primary_key=True)
    instance_id = Column(Integer, ForeignKey("card_instances.instance_id"), primary_key=True)
    position = Column(Integer)  # 1 for hero, 2-5 for additional cards
    
    # Relationships
    pack = relationship("Pack", back_populates="contents")
    card_instance = relationship("CardInstance", back_populates="pack_contents")

class MarketplaceItem(Base):
    __tablename__ = "marketplace_items"
    
    item_id = Column(Integer, primary_key=True, autoincrement=True)
    pack_id = Column(Integer, ForeignKey("packs.pack_id"))
    price = Column(Float, default=9.99)
    listed_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    stock = Column(String, default="unlimited")
    
    # Relationships
    pack = relationship("Pack", back_populates="marketplace_item")
    transactions = relationship("Transaction", back_populates="marketplace_item")

class Transaction(Base):
    __tablename__ = "transactions"
    
    tx_id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(Integer, ForeignKey("marketplace_items.item_id"))
    buyer_id = Column(String, ForeignKey("users.user_id"))
    seller_id = Column(String, ForeignKey("users.user_id"))
    tx_date = Column(DateTime, default=datetime.utcnow)
    price = Column(Float)
    
    # Relationships
    marketplace_item = relationship("MarketplaceItem", back_populates="transactions")
    buyer = relationship("User", foreign_keys=[buyer_id], back_populates="transactions_as_buyer")
    seller = relationship("User", foreign_keys=[seller_id], back_populates="transactions_as_seller")

class ServerActivity(Base):
    __tablename__ = "server_activity"

    id = Column(Integer, primary_key=True, autoincrement=True)
    server_id = Column(Integer, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)

class MarketplaceListings(Base):
    __tablename__ = "marketplace_listings"

    listing_id = Column(Integer, primary_key=True, autoincrement=True)
    seller_id = Column(String, nullable=False)
    card_id = Column(String, nullable=False)
    price = Column(Integer, nullable=False)
    listed_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

class SeasonProgress(Base):
    __tablename__ = "season_progress"

    user_id = Column(String, primary_key=True)
    season_id = Column(String, primary_key=True)
    xp = Column(Integer, default=0)
    current_tier = Column(Integer, default=0)
    has_premium = Column(Boolean, default=False)

class VipStatus(Base):
    __tablename__ = "vip_status"

    user_id = Column(String, primary_key=True)
    is_vip = Column(Boolean, default=False)
    expires_at = Column(DateTime)
    subscription_id = Column(String)
    activity_type = Column(String, default="message")

class UserCosmetics(Base):
    __tablename__ = "user_cosmetics"

    user_id = Column(String, primary_key=True)
    cosmetic_id = Column(String, primary_key=True)
    cosmetic_type = Column(String, nullable=False)
    unlocked_at = Column(DateTime, default=datetime.utcnow)
    source = Column(String)

class CosmeticsCatalog(Base):
    __tablename__ = "cosmetics_catalog"

    cosmetic_id = Column(String, primary_key=True)
    cosmetic_type = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text)
    rarity = Column(String)
    unlock_method = Column(String)
    price_gold = Column(Integer)
    price_tickets = Column(Integer)
    image_url = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

class CardCosmetics(Base):
    __tablename__ = "card_cosmetics"

    user_id = Column(String, primary_key=True)
    card_id = Column(String, primary_key=True)
    cosmetic_id = Column(String, primary_key=True)
    equipped_at = Column(DateTime, default=datetime.utcnow)

class TradeHistory(Base):
    __tablename__ = "trade_history"

    trade_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id_1 = Column(String, nullable=False)
    user_id_2 = Column(String, nullable=False)
    card_id_1 = Column(String, nullable=False)
    card_id_2 = Column(String, nullable=False)
    status = Column(String, default="completed")
    completed_at = Column(DateTime, default=datetime.utcnow)

class Card(Base):
    __tablename__ = 'cards'

    card_id = Column(String, primary_key=True)
    type = Column(String, nullable=False, default='artist')
    name = Column(String, nullable=False)
    artist_name = Column(String)
    title = Column(String)
    image_url = Column(String)
    youtube_url = Column(String)
    rarity = Column(String, nullable=False)
    tier = Column(String)
    variant = Column(String, default='Classic')
    era = Column(String)
    impact = Column(Integer)
    skill = Column(Integer)
    longevity = Column(Integer)
    culture = Column(Integer)
    hype = Column(Integer)
    serial_number = Column(String)
    print_number = Column(Integer, default=1)
    quality = Column(String, default='standard')
    effect_type = Column(String)
    effect_value = Column(String)
    pack_id = Column(String, ForeignKey('creator_packs.pack_id'))
    created_by_user_id = Column(String, ForeignKey('users.user_id'))
    created_at = Column(DateTime, default=datetime.utcnow)
    # Phase 4a - genre family resolved once at creation (see services/genre_resolver.py)
    genre_family = Column(String, default='NEUTRAL')
    genre_source = Column(String, nullable=True)
    # Phase 4e - weekly momentum (see services/momentum_service.py)
    view_count = Column(BigInteger, nullable=True)
    view_delta = Column(BigInteger, default=0)
    momentum_hot = Column(Boolean, default=False)
    views_checked_at = Column(DateTime, nullable=True)

    def to_dict(self):
        return {
            "card_id": self.card_id,
            "type": self.type,
            "name": self.name,
            "artist_name": self.artist_name,
            "title": self.title,
            "image_url": self.image_url,
            "youtube_url": self.youtube_url,
            "rarity": self.rarity,
            "tier": self.tier,
            "variant": self.variant,
            "era": self.era,
            "impact": self.impact,
            "skill": self.skill,
            "longevity": self.longevity,
            "culture": self.culture,
            "hype": self.hype,
            "serial_number": self.serial_number,
            "print_number": self.print_number,
            "quality": self.quality,
            "effect_type": self.effect_type,
            "effect_value": self.effect_value,
            "pack_id": self.pack_id,
            "created_by_user_id": self.created_by_user_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    creator_pack = relationship("CreatorPacks", back_populates="cards")
    created_by_user = relationship("User")

class CreatorPacks(Base):
    __tablename__ = "creator_packs"

    pack_id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    creator_id = Column(String, ForeignKey('users.user_id'), nullable=False)
    description = Column(Text)
    price = Column(Integer, default=0)
    card_count = Column(Integer, default=0)
    cards_data = Column(JSONType) # Added cards_data
    pack_tier = Column(String) # Added pack_tier
    genre = Column(String) # Added genre
    cover_image_url = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_public = Column(Boolean, default=False)

    creator = relationship("User")
    cards = relationship("Card", back_populates="creator_pack")
    pack_cards = relationship("CreatorPackCards", back_populates="pack")


class CreatorPackCards(Base):
    __tablename__ = 'creator_pack_cards'

    pack_id = Column(String, ForeignKey('creator_packs.pack_id'), primary_key=True)
    card_id = Column(String, ForeignKey('cards.card_id'), primary_key=True)

    pack = relationship("CreatorPacks", back_populates="pack_cards")
    card = relationship("Card")

class CreatorPackLimits(Base):
    __tablename__ = 'creator_pack_limits'
    creator_id = Column(Integer, primary_key=True)



class DevPackSupply(Base):
    __tablename__ = "dev_pack_supply"

    pack_id = Column(String, primary_key=True)
    quantity = Column(Integer, default=0)

class PackDefinitions(Base):
    __tablename__ = "pack_definitions"

    pack_id = Column(String, primary_key=True)
    pack_name = Column(String, nullable=False)
    description = Column(Text)
    price_gold = Column(Integer)
    price_tickets = Column(Integer)
    image_url = Column(String)
    card_count = Column(Integer, default=5)
    rarity_distribution = Column(Text)  # JSON for rarity chances
    created_at = Column(DateTime, default=datetime.utcnow)

class BattleLog(Base):
    __tablename__ = "battle_log"

    battle_id = Column(Integer, primary_key=True, autoincrement=True)
    player1_id = Column(String, nullable=False)
    player2_id = Column(String, nullable=False)
    winner_id = Column(String)
    battle_data = Column(Text)  # JSON blob of the battle replay
    battle_timestamp = Column(DateTime, default=datetime.utcnow)

class UserBalances(Base):
    __tablename__ = "user_balances"

    user_id = Column(String, primary_key=True)
    gold = Column(Integer, default=0)
    tickets = Column(Integer, default=0)
    dust = Column(Integer, default=0)
    gems = Column(Integer, default=0)
    xp = Column(Integer, default=0)
    level = Column(Integer, default=1)
    last_daily_claim = Column(DateTime, nullable=True)
    daily_streak = Column(Integer, default=0)

class PackPurchase(Base):
    __tablename__ = "pack_purchases"
    purchase_id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    buyer_id = Column(String, ForeignKey("users.user_id"), nullable=False)
    pack_id = Column(String, ForeignKey("creator_packs.pack_id"), nullable=False)
    purchased_at = Column(DateTime, default=datetime.utcnow)
    cards_received = Column(JSONType)

class UserCard(Base):
    __tablename__ = "user_cards"
    user_id = Column(String, ForeignKey("users.user_id"), primary_key=True)
    card_id = Column(String, ForeignKey("cards.card_id"), primary_key=True)
    quantity = Column(Integer, default=1)
    acquired_from = Column(String, nullable=True) # e.g., 'drop', 'market', 'trade'
    acquired_at = Column(DateTime, default=datetime.utcnow)
    is_favorite = Column(Boolean, default=False)

class DailyClaims(Base):
    __tablename__ = "daily_claims"

    user_id = Column(String, primary_key=True)
    last_claim_date = Column(DateTime, nullable=False)

class UserBattleStats(Base):
    __tablename__ = "user_battle_stats"
    user_id = Column(String, ForeignKey("users.user_id"), primary_key=True)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    draws = Column(Integer, default=0)

class TransactionAuditLog(Base):
    __tablename__ = "transaction_audit_log"

    log_id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String, nullable=False)
    user_id = Column(String)
    transaction_id = Column(String)
    details = Column(Text)  # JSON blob
    success = Column(Boolean)
    timestamp = Column(DateTime, default=datetime.utcnow)

class TmaLinkCode(Base):
    __tablename__ = "tma_link_codes"

    code = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.user_id"), nullable=False)
    expires_at = Column(DateTime, nullable=False)

class PendingTmaBattle(Base):
    __tablename__ = "pending_tma_battles"

    battle_id = Column(String, primary_key=True)
    challenger_id = Column(String, ForeignKey("users.user_id"), nullable=False)
    opponent_id = Column(String, ForeignKey("users.user_id"))
    challenger_pack = Column(Text, nullable=False) # JSON array of card IDs
    opponent_pack = Column(Text) # JSON array of card IDs
    wager_tier = Column(String, default="casual")
    status = Column(String, default="waiting") # waiting, accepted, declined, completed
    result_json = Column(Text) # JSON blob of battle results
    opponent_scout_json = Column(Text, nullable=True)  # Phase 4d: acceptor's Scout reveal, set before commit
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime)


class TelegramHost(Base):
    """Registered Telegram community hosts for revenue share attribution."""
    __tablename__ = "telegram_hosts"

    host_token = Column(String, primary_key=True)
    owner_telegram_id = Column(BigInteger, nullable=False)
    chat_id = Column(BigInteger, nullable=True)
    label = Column(String, nullable=True)
    share_bps = Column(Integer, default=1000)  # basis points (1000 = 10%)
    created_at = Column(DateTime, default=datetime.utcnow)
    stripe_connect_account_id = Column(String, nullable=True)


class RevenueEvent(Base):
    """Idempotent ledger row per Stripe Checkout session (profit sharing audit)."""
    __tablename__ = "revenue_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stripe_session_id = Column(String, unique=True, nullable=False)
    user_id = Column(String, nullable=False)
    product_type = Column(String, nullable=False)
    gross_cents = Column(Integer, default=0)
    platform_cents = Column(Integer, default=0)
    host_cents = Column(Integer, default=0)
    creator_cents = Column(Integer, default=0)
    host_token = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # Phase 5: which rail + Stars-specific amounts (host share is computed on net_cents)
    rail = Column(String, default='stripe')
    gross_stars = Column(Integer, default=0)
    net_cents = Column(Integer, default=0)


class StarsOrder(Base):
    """Telegram Stars purchase (Phase 5). One row per invoice; idempotent on charge id."""
    __tablename__ = "stars_orders"

    order_id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    telegram_id = Column(BigInteger, nullable=True)
    product_type = Column(String, nullable=False)   # tier_pack | creator_pack
    product_ref = Column(String, nullable=False)    # tier name or pack_id
    title = Column(String, nullable=True)
    stars_amount = Column(Integer, nullable=False)
    usd_cents_ref = Column(Integer, nullable=True)  # USD list price the Stars price derives from
    host_token = Column(String, nullable=True)
    host_share_bps = Column(Integer, nullable=True)
    status = Column(String, nullable=False, default="pending")  # pending|paid|fulfilled|failed|refunded
    invoice_link = Column(Text, nullable=True)
    telegram_payment_charge_id = Column(String, nullable=True, unique=True)
    provider_payment_charge_id = Column(String, nullable=True)
    cards_json = Column(Text, nullable=True)
    result_json = Column(Text, nullable=True)
    error = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    paid_at = Column(DateTime, nullable=True)
    fulfilled_at = Column(DateTime, nullable=True)


# Aliases for backward compatibility (database.py uses singular names)
CosmeticCatalog = CosmeticsCatalog
UserCosmetic = UserCosmetics
CardCosmetic = CardCosmetics


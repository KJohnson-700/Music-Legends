import json
import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Union

from sqlalchemy import (
    Boolean,
    Column,
    create_engine,
    DateTime,
    desc,
    ForeignKey,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.sql import exists

from models import (
    Base,
    User, UserBalances, PackPurchase, CreatorPacks, Card, UserCard,
    DevPackSupply, CardInstance, CreatorPackLimits, TradeHistory,
    UserBattleStats, CosmeticCatalog, UserCosmetic, CardCosmetic, BattleLog,
    TmaLinkCode, MarketplaceListings, VipStatus, PendingTmaBattle,
    CreatorPackCards, TelegramHost, RevenueEvent,
)
from models.trade import Trade

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _iso(value):
    """ISO string for datetime OR pass-through for SQLite's text timestamps."""
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


class Database:
    _instance = None
    _engine = None
    _Session = None
    _database_url: str = None
    _db_type: str = None

    def __new__(cls, database_url: Optional[str] = None, test_database_url: Optional[str] = None):
        # Test instances bypass the singleton — each call gets a fresh object
        if test_database_url is not None:
            instance = super(Database, cls).__new__(cls)
            instance._engine = None
            instance._Session = None
            instance._database_url = None
            instance._db_type = None
            instance._initialize(test_database_url)
            return instance

        if cls._instance is None:
            cls._instance = super(Database, cls).__new__(cls)
            if database_url:
                cls._instance._initialize(database_url)
        return cls._instance

    @property
    def engine(self):
        """Public access to the SQLAlchemy engine (used by tests)."""
        return self._engine

    @property
    def SessionLocal(self):
        """Backward-compatible handle used by older tests/helpers."""
        return self._Session

    def init_database(self):
        """Alias for _create_tables_if_not_exists (used by test fixtures)."""
        self._create_tables_if_not_exists()

    def _initialize(self, database_url: str):
        if self._engine is not None:
            return  # Already initialized

        self._database_url = database_url
        if database_url.startswith("sqlite"):
            self._db_type = "sqlite"
            db_path = database_url.replace("sqlite:///", "")
            if db_path in (":memory:", ""):
                # In-memory SQLite: use StaticPool so all sessions share the same connection
                from sqlalchemy.pool import StaticPool
                self._engine = create_engine(
                    database_url,
                    connect_args={"check_same_thread": False},
                    poolclass=StaticPool,
                )
            else:
                # File-based SQLite: ensure directory exists
                dir_path = os.path.dirname(db_path)
                if dir_path:
                    os.makedirs(dir_path, exist_ok=True)
                self._engine = create_engine(
                    database_url, connect_args={"check_same_thread": False}
                )
        else:
            self._db_type = "postgresql"
            self._engine = create_engine(database_url)

        self._Session = sessionmaker(bind=self._engine)
        self._create_tables_if_not_exists()

    def _migrate_user_id_to_varchar(self, conn, sa_text):
        """One-time migration: convert users.user_id from BIGINT → VARCHAR on Railway.
        Handles FK constraints automatically."""
        try:
            row = conn.execute(sa_text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name='users' AND column_name='user_id' AND table_schema='public'"
            )).fetchone()
            if not row or row[0] != 'bigint':
                return  # Already VARCHAR or not PostgreSQL
            logger.info("[MIGRATE] users.user_id is BIGINT — migrating to VARCHAR...")

            # Find FK constraints referencing users.user_id
            fk_rows = conn.execute(sa_text("""
                SELECT kcu.constraint_name, kcu.table_name AS fk_table, kcu.column_name AS fk_col
                FROM information_schema.key_column_usage kcu
                JOIN information_schema.referential_constraints rc
                     ON kcu.constraint_name = rc.constraint_name
                JOIN information_schema.key_column_usage kcu2
                     ON rc.unique_constraint_name = kcu2.constraint_name
                WHERE kcu2.table_name = 'users' AND kcu2.column_name = 'user_id'
            """)).fetchall()

            # Drop FK constraints
            for fk_name, fk_table, fk_col in fk_rows:
                conn.execute(sa_text(f'ALTER TABLE "{fk_table}" DROP CONSTRAINT IF EXISTS "{fk_name}"'))
                logger.info(f"[MIGRATE] Dropped FK {fk_name} on {fk_table}.{fk_col}")

            # Alter FK columns in referencing tables to VARCHAR
            for fk_name, fk_table, fk_col in fk_rows:
                conn.execute(sa_text(
                    f'ALTER TABLE "{fk_table}" ALTER COLUMN "{fk_col}" TYPE VARCHAR USING "{fk_col}"::text'
                ))
                logger.info(f"[MIGRATE] Altered {fk_table}.{fk_col} → VARCHAR")

            # Alter users.user_id itself
            conn.execute(sa_text('ALTER TABLE users ALTER COLUMN user_id TYPE VARCHAR USING user_id::text'))
            logger.info("[MIGRATE] Altered users.user_id → VARCHAR")

            # Re-add FK constraints
            for fk_name, fk_table, fk_col in fk_rows:
                conn.execute(sa_text(
                    f'ALTER TABLE "{fk_table}" ADD CONSTRAINT "{fk_name}" '
                    f'FOREIGN KEY ("{fk_col}") REFERENCES users(user_id)'
                ))
                logger.info(f"[MIGRATE] Re-added FK {fk_name}")

            conn.commit()
            logger.info("[MIGRATE] users.user_id → VARCHAR migration complete")
        except Exception as e:
            conn.rollback()
            logger.warning(f"[MIGRATE] user_id type migration failed (may already be VARCHAR): {e}")

    def _create_tables_if_not_exists(self):
        """Create tables that don't exist, then add any missing columns to existing tables."""
        from sqlalchemy import inspect, text as sa_text
        Base.metadata.create_all(self._engine)
        # Auto-migrate: add columns present in models but missing from the DB
        inspector = inspect(self._engine)
        with self._engine.connect() as conn:
            # Create active_battles table (not in ORM models, managed via raw SQL)
            conn.execute(sa_text("""
                CREATE TABLE IF NOT EXISTS active_battles (
                    match_id TEXT PRIMARY KEY,
                    player1_id TEXT NOT NULL,
                    player2_id TEXT NOT NULL,
                    wager_amount INTEGER NOT NULL DEFAULT 0,
                    tier_key TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            # Backward-compat: older deployments may have active_battles without tier_key.
            try:
                active_cols = {col["name"] for col in inspector.get_columns("active_battles")}
                if "tier_key" not in active_cols:
                    conn.execute(sa_text('ALTER TABLE active_battles ADD COLUMN tier_key TEXT'))
                    conn.commit()
                    logger.info("[MIGRATE] Added column active_battles.tier_key")
            except Exception as e:
                conn.rollback()
                logger.warning(f"[MIGRATE] Skipped active_battles.tier_key migration: {e}")
            # Legacy economy table still used by several Discord cogs via raw SQL.
            conn.execute(sa_text("""
                CREATE TABLE IF NOT EXISTS user_inventory (
                    user_id TEXT PRIMARY KEY,
                    gold INTEGER DEFAULT 0,
                    dust INTEGER DEFAULT 0,
                    tickets INTEGER DEFAULT 0,
                    gems INTEGER DEFAULT 0,
                    xp INTEGER DEFAULT 0,
                    level INTEGER DEFAULT 1,
                    total_cards INTEGER DEFAULT 0,
                    last_daily_claim TIMESTAMP,
                    daily_streak INTEGER DEFAULT 0
                )
            """))
            conn.execute(sa_text("""
                CREATE TABLE IF NOT EXISTS battle_registry (
                    user_id TEXT PRIMARY KEY,
                    telegram_id BIGINT UNIQUE NOT NULL,
                    username TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(sa_text(
                "CREATE INDEX IF NOT EXISTS idx_battle_registry_active_seen "
                "ON battle_registry(is_active, last_seen DESC)"
            ))
            conn.commit()
            # PostgreSQL only: fix users.user_id type from BIGINT → VARCHAR
            if self._engine.dialect.name == 'postgresql':
                self._migrate_user_id_to_varchar(conn, sa_text)

            for table in Base.metadata.sorted_tables:
                if not inspector.has_table(table.name):
                    continue
                existing = {col["name"] for col in inspector.get_columns(table.name)}
                for col in table.columns:
                    if col.name in existing:
                        continue
                    # Build ADD COLUMN statement
                    col_type = col.type.compile(dialect=self._engine.dialect)
                    nullable = "" if col.nullable else " NOT NULL"
                    default = ""
                    if col.default is not None and col.default.is_scalar:
                        val = col.default.arg
                        if isinstance(val, bool):
                            default = " DEFAULT TRUE" if val else " DEFAULT FALSE"
                        elif isinstance(val, (int, float)):
                            default = f" DEFAULT {val}"
                        elif isinstance(val, str):
                            default = f" DEFAULT '{val}'"
                    try:
                        conn.execute(sa_text(
                            f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type}{default}'
                        ))
                        conn.commit()
                        logger.info(f"[MIGRATE] Added column {table.name}.{col.name}")
                    except Exception as e:
                        conn.rollback()
                        logger.warning(f"[MIGRATE] Skipped {table.name}.{col.name}: {e}")

    def get_session(self) -> Session:
        """Returns a new SQLAlchemy session."""
        if self._Session is None:
            raise Exception("Database not initialized. Call Database(database_url) first.")
        return self._Session()

    def _get_placeholder(self) -> str:
        """Return the SQL parameter placeholder for the current dialect."""
        return "%s" if self._db_type == "postgresql" else "?"

    def _get_connection(self):
        """Return a raw DBAPI connection wrapped as a context manager."""
        return _PgConnectionWrapper(self._engine.raw_connection())

    @staticmethod
    def _user_id_variants(user_id) -> list:
        """Return compatible user_id TEXT variants for historical schemas."""
        base = str(user_id)
        values = [base]
        try:
            n = int(base)
            tg_offset = 9_000_000_000
            if n >= tg_offset:
                values.append(str(n - tg_offset))
            else:
                values.append(str(n + tg_offset))
        except (TypeError, ValueError):
            pass
        # Preserve order while deduplicating
        seen = set()
        out = []
        for v in values:
            if v in seen:
                continue
            seen.add(v)
            out.append(v)
        return out





    def _init_postgresql(self):
        """
        Initializes PostgreSQL-specific database schema and functions.
        This includes creating the `transactions` table.
        """
        try:
            # Enable uuid-ossp for UUID generation
            # This is a PostgreSQL-specific command, so we'll execute it via SQLAlchemy's connection
            with self._engine.connect() as connection:
                connection.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'))
                connection.commit()

            logger.info("PostgreSQL database initialized successfully.")
        except Exception as e:
            logger.error(f"Error initializing PostgreSQL database: {e}")
            raise # Re-raise the exception after logging

    def _init_sqlite(self):
        """Initializes SQLite-specific database schema."""
        # For now, all SQLite initialization is handled by SQLAlchemy's create_all
        pass

    def get_user(self, user_id: str) -> Optional[User]:
        session = self.get_session()
        try:
            return session.query(User).filter_by(user_id=user_id).first()
        finally:
            session.close()

    def create_user(self, user_id: str, username: str) -> User:
        session = self.get_session()
        try:
            user = User(user_id=user_id, username=username)
            session.add(user)
            session.commit()
            return user
        except IntegrityError:
            session.rollback()
            raise ValueError(f"User with ID {user_id} or username {username} already exists.")
        except Exception as e:
            session.rollback()
            logger.error(f"Error creating user {username}: {e}")
            raise
        finally:
            session.close()

    def update_username(self, user_id: str, new_username: str) -> bool:
        session = self.get_session()
        try:
            user = session.query(User).filter_by(user_id=user_id).first()
            if user:
                user.username = new_username
                session.commit()
                return True
            return False
        except IntegrityError:
            session.rollback()
            raise ValueError(f"Username {new_username} is already taken.")
        except Exception as e:
            session.rollback()
            logger.error(f"Error updating username for {user_id}: {e}")
            raise
        finally:
            session.close()

    def get_user_balance(self, user_id: str) -> Optional[UserBalances]:
        session = self.get_session()
        try:
            return session.query(UserBalances).filter_by(user_id=user_id).first()
        finally:
            session.close()

    def update_user_balance(self, user_id: str, currency: int = 0, dust: int = 0) -> bool:
        session = self.get_session()
        try:
            balance = session.query(UserBalances).filter_by(user_id=user_id).first()
            if balance:
                balance.currency += currency
                balance.dust += dust
                session.commit()
                return True
            return False
        except Exception as e:
            session.rollback()
            logger.error(f"Error updating balance for user {user_id}: {e}")
            raise
        finally:
            session.close()

    def record_pack_purchase(
        self, user_id: str, pack_id: str, cost: int, currency_type: str, cards_pulled: List[str]
    ) -> bool:
        session = self.get_session()
        try:
            purchase = PackPurchase(
                user_id=user_id,
                pack_id=pack_id,
                cost=cost,
                currency_type=currency_type,
                cards_pulled=json.dumps(cards_pulled),
            )
            session.add(purchase)
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"Error recording pack purchase for user {user_id}: {e}")
            raise
        finally:
            session.close()

    def get_all_cards(self) -> List[Card]:
        session = self.get_session()
        try:
            return session.query(Card).all()
        finally:
            session.close()

    def get_card_by_id(self, card_id: str) -> Optional[Card]:
        session = self.get_session()
        try:
            return session.query(Card).filter_by(card_id=card_id).first()
        finally:
            session.close()

    def add_card_to_collection(
        self, user_id: str, card_id: str, quantity: int = 1, acquired_from: str = "pack"
    ) -> bool:
        session = self.get_session()
        try:
            user_card = (
                session.query(UserCard)
                .filter_by(user_id=user_id, card_id=card_id)
                .first()
            )
            if user_card:
                user_card.quantity += quantity
            else:
                user_card = UserCard(
                    user_id=user_id,
                    card_id=card_id,
                    quantity=quantity,
                    acquired_from=acquired_from,
                )
                session.add(user_card)
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logger.error(
                f"Error adding card {card_id} to user {user_id}'s collection: {e}"
            )
            raise
        finally:
            session.close()

    def remove_card_from_collection(self, user_id: str, card_id: str, quantity: int = 1) -> bool:
        session = self.get_session()
        try:
            user_card = (
                session.query(UserCard)
                .filter_by(user_id=user_id, card_id=card_id)
                .first()
            )
            if user_card and user_card.quantity >= quantity:
                user_card.quantity -= quantity
                if user_card.quantity == 0:
                    session.delete(user_card)
                session.commit()
                return True
            return False
        except Exception as e:
            session.rollback()
            logger.error(
                f"Error removing card {card_id} from user {user_id}'s collection: {e}"
            )
            raise
        finally:
            session.close()

    def get_user_cards(self, user_id: str) -> List[Dict]:
        session = self.get_session()
        try:
            user_cards = (
                session.query(UserCard, Card)
                .join(Card, UserCard.card_id == Card.card_id)
                .filter(UserCard.user_id == user_id)
                .all()
            )
            return [
                {
                    "card_id": uc.card_id,
                    "quantity": uc.quantity,
                    "acquired_from": uc.acquired_from,
                    "acquired_at": uc.acquired_at.isoformat(),
                    "is_favorite": uc.is_favorite,
                    "card_name": c.card_name,
                    "card_set": c.card_set,
                    "card_type": c.card_type,
                    "rarity": c.rarity,
                    "metadata": c.metadata,
                }
                for uc, c in user_cards
            ]
        finally:
            session.close()

    def get_user_card_count(self, user_id: str, card_id: str) -> int:
        session = self.get_session()
        try:
            user_card = (
                session.query(UserCard)
                .filter_by(user_id=user_id, card_id=card_id)
                .first()
            )
            return user_card.quantity if user_card else 0
        finally:
            session.close()

    def update_card_favorite_status(self, user_id: str, card_id: str, is_favorite: bool) -> bool:
        session = self.get_session()
        try:
            user_card = (
                session.query(UserCard)
                .filter_by(user_id=user_id, card_id=card_id)
                .first()
            )
            if user_card:
                user_card.is_favorite = is_favorite
                session.commit()
                return True
            return False
        except Exception as e:
            session.rollback()
            logger.error(
                f"Error updating favorite status for card {card_id} for user {user_id}: {e}"
            )
            raise
        finally:
            session.close()

    def get_all_creator_packs(self) -> List[CreatorPacks]:
        session = self.get_session()
        try:
            return session.query(CreatorPacks).all()
        finally:
            session.close()

    def get_creator_pack_by_id(self, pack_id: str) -> Optional[CreatorPacks]:
        session = self.get_session()
        try:
            return session.query(CreatorPacks).filter_by(pack_id=pack_id).first()
        finally:
            session.close()

    def get_dev_pack_supply(self, pack_id: str) -> Optional[DevPackSupply]:
        session = self.get_session()
        try:
            return session.query(DevPackSupply).filter_by(pack_id=pack_id).first()
        finally:
            session.close()

    def update_dev_pack_supply(self, pack_id: str, quantity_change: int) -> bool:
        session = self.get_session()
        try:
            supply = session.query(DevPackSupply).filter_by(pack_id=pack_id).first()
            if supply:
                supply.quantity += quantity_change
                session.commit()
                return True
            return False
        except Exception as e:
            session.rollback()
            logger.error(f"Error updating dev pack supply for {pack_id}: {e}")
            raise
        finally:
            session.close()

    def get_creator_pack_limit(self, pack_id: str) -> Optional[CreatorPackLimits]:
        session = self.get_session()
        try:
            return session.query(CreatorPackLimits).filter_by(pack_id=pack_id).first()
        finally:
            session.close()

    def update_creator_pack_limit(self, pack_id: str, new_limit: int) -> bool:
        session = self.get_session()
        try:
            limit = session.query(CreatorPackLimits).filter_by(pack_id=pack_id).first()
            if limit:
                limit.max_supply = new_limit
                session.commit()
                return True
            return False
        except Exception as e:
            session.rollback()
            logger.error(f"Error updating creator pack limit for {pack_id}: {e}")
            raise
        finally:
            session.close()

    def get_card_instance(self, instance_id: str) -> Optional[CardInstance]:
        session = self.get_session()
        try:
            return session.query(CardInstance).filter_by(instance_id=instance_id).first()
        finally:
            session.close()

    def create_card_instance(
        self,
        card_id: str,
        owner_id: str,
        metadata: Dict,
        is_for_sale: bool = False,
        sale_price: Optional[int] = None,
    ) -> CardInstance:
        session = self.get_session()
        try:
            instance = CardInstance(
                card_id=card_id,
                owner_id=owner_id,
                metadata=json.dumps(metadata),
                is_for_sale=is_for_sale,
                sale_price=sale_price,
            )
            session.add(instance)
            session.commit()
            return instance
        except Exception as e:
            session.rollback()
            logger.error(f"Error creating card instance for card {card_id}: {e}")
            raise
        finally:
            session.close()

    def update_card_instance_sale_status(
        self, instance_id: str, is_for_sale: bool, sale_price: Optional[int] = None
    ) -> bool:
        session = self.get_session()
        try:
            instance = session.query(CardInstance).filter_by(instance_id=instance_id).first()
            if instance:
                instance.is_for_sale = is_for_sale
                instance.sale_price = sale_price
                session.commit()
                return True
            return False
        except Exception as e:
            session.rollback()
            logger.error(f"Error updating sale status for card instance {instance_id}: {e}")
            raise
        finally:
            session.close()

    def transfer_card_instance(
        self, instance_id: str, new_owner_id: str, transfer_price: Optional[int] = None
    ) -> bool:
        session = self.get_session()
        try:
            instance = session.query(CardInstance).filter_by(instance_id=instance_id).first()
            if instance:
                original_owner_id = instance.owner_id
                instance.owner_id = new_owner_id
                instance.is_for_sale = False
                instance.sale_price = None

                trade = TradeHistory(
                    instance_id=instance_id,
                    old_owner_id=original_owner_id,
                    new_owner_id=new_owner_id,
                    trade_price=transfer_price,
                )
                session.add(trade)
                session.commit()
                return True
            return False
        except Exception as e:
            session.rollback()
            logger.error(
                f"Error transferring card instance {instance_id} to {new_owner_id}: {e}"
            )
            raise
        finally:
            session.close()

    def get_user_card_instances(self, user_id: str) -> List[CardInstance]:
        session = self.get_session()
        try:
            return session.query(CardInstance).filter_by(owner_id=user_id).all()
        finally:
            session.close()

    def get_market_listings(self) -> List[CardInstance]:
        session = self.get_session()
        try:
            return session.query(CardInstance).filter_by(is_for_sale=True).all()
        finally:
            session.close()

    def unlock_cosmetic_for_user(self, user_id: str, cosmetic_id: str) -> bool:
        session = self.get_session()
        try:
            # Check if cosmetic already unlocked
            exists_query = session.query(exists().where(
                UserCosmetic.user_id == user_id and UserCosmetic.cosmetic_id == cosmetic_id
            )).scalar()

            if not exists_query:
                user_cosmetic = UserCosmetic(user_id=user_id, cosmetic_id=cosmetic_id)
                session.add(user_cosmetic)
                session.commit()
                return True
            return False # Cosmetic already unlocked
        except Exception as e:
            session.rollback()
            logger.error(f"Error unlocking cosmetic {cosmetic_id} for user {user_id}: {e}")
            return False
        finally:
            session.close()

    def get_user_cosmetics(self, user_id: str) -> List[CosmeticCatalog]:
        session = self.get_session()
        try:
            cosmetics = session.query(CosmeticCatalog).join(
                UserCosmetic, CosmeticCatalog.cosmetic_id == UserCosmetic.cosmetic_id
            ).filter(UserCosmetic.user_id == user_id).all()
            return cosmetics
        finally:
            session.close()

    def apply_cosmetic_to_card(self, user_id: str, card_instance_id: str, cosmetic_id: str) -> bool:
        session = self.get_session()
        try:
            # Ensure user owns the card instance and the cosmetic
            card_instance = session.query(CardInstance).filter_by(
                instance_id=card_instance_id, owner_id=user_id
            ).first()
            user_cosmetic = session.query(UserCosmetic).filter_by(
                user_id=user_id, cosmetic_id=cosmetic_id
            ).first()

            if not card_instance or not user_cosmetic:
                logger.warning(f"User {user_id} does not own card instance {card_instance_id} or cosmetic {cosmetic_id}.")
                return False

            # Apply cosmetic
            card_cosmetic = CardCosmetic(
                instance_id=card_instance_id,
                cosmetic_id=cosmetic_id,
                applied_at=datetime.utcnow()
            )
            session.add(card_cosmetic)
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"Error applying cosmetic {cosmetic_id} to card instance {card_instance_id}: {e}")
            return False
        finally:
            session.close()

    def get_card_cosmetics(self, card_instance_id: str) -> List[CosmeticCatalog]:
        session = self.get_session()
        try:
            cosmetics = session.query(CosmeticCatalog).join(
                CardCosmetic, CosmeticCatalog.cosmetic_id == CardCosmetic.cosmetic_id
            ).filter(CardCosmetic.instance_id == card_instance_id).all()
            return cosmetics
        finally:
            session.close()

    def get_available_cosmetics(self) -> List[CosmeticCatalog]:
        session = self.get_session()
        try:
            return session.query(CosmeticCatalog).all()
        finally:
            session.close()

    def get_user_battle_stats(self, user_id: str) -> Optional[UserBattleStats]:
        session = self.get_session()
        try:
            return session.query(UserBattleStats).filter_by(user_id=user_id).first()
        finally:
            session.close()

    def update_user_battle_stats(
        self, user_id: str, wins: int = 0, losses: int = 0, draws: int = 0
    ) -> bool:
        session = self.get_session()
        try:
            stats = session.query(UserBattleStats).filter_by(user_id=user_id).first()
            if stats:
                stats.wins += wins
                stats.losses += losses
                stats.draws += draws
            else:
                stats = UserBattleStats(user_id=user_id, wins=wins, losses=losses, draws=draws)
                session.add(stats)
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"Error updating battle stats for user {user_id}: {e}")
            raise
        finally:
            session.close()

    def get_all_battle_logs(self) -> List[BattleLog]:
        session = self.get_session()
        try:
            return session.query(BattleLog).order_by(desc(BattleLog.completed_at)).all()
        finally:
            session.close()

    def get_battle_logs_for_user(self, user_id: str) -> List[BattleLog]:
        session = self.get_session()
        try:
            return (
                session.query(BattleLog)
                .filter(
                    (BattleLog.player_a_id == user_id) | (BattleLog.player_b_id == user_id)
                )
                .order_by(desc(BattleLog.completed_at))
                .all()
            )
        finally:
            session.close()

    def record_match(self, match_data: Dict) -> bool:
            """Record a completed match"""
            try:
                session = self.get_session()

                new_match = BattleLog(
                    match_id=match_data['match_id'],
                    player_a_id=match_data['player_a_id'],
                    player_b_id=match_data['player_b_id'],
                    winner_id=match_data['winner_id'],
                    final_score_a=match_data['final_score_a'],
                    final_score_b=match_data['final_score_b'],
                    completed_at=datetime.utcnow(),
                    match_type=match_data.get('match_type', 'casual')
                )
                session.add(new_match)
                session.commit()
                return True
            except Exception as e:
                session.rollback()
                print(f"Error recording match: {e}")
                return False
            finally:
                session.close()

    def get_match_history(self, player_id: str, limit: int = 10) -> List[Dict]:
        """Retrieves match history for a given player."""
        session = self.get_session()
        try:
            matches = (
                session.query(BattleLog)
                .filter(
                    (BattleLog.player_a_id == player_id) | (BattleLog.player_b_id == player_id)
                )
                .order_by(desc(BattleLog.completed_at))
                .limit(limit)
                .all()
            )

            history = []
            for match in matches:
                history.append({
                    "match_id": match.match_id,
                    "player_a_id": match.player_a_id,
                    "player_b_id": match.player_b_id,
                    "winner_id": match.winner_id,
                    "final_score_a": match.final_score_a,
                    "final_score_b": match.final_score_b,
                    "completed_at": match.completed_at.isoformat() if match.completed_at else None,
                    "match_type": match.match_type,
                })
            return history
        except Exception as e:
            logger.error(f"Error retrieving match history for player {player_id}: {e}")
            return []
        finally:
            session.close()

    def get_leaderboard(self, limit: int = 10) -> List[Dict]:
        """Retrieves top players based on battle statistics."""
        session = self.get_session()
        try:
            leaderboard = (
                session.query(UserBattleStats, User.username)
                .join(User, UserBattleStats.user_id == User.user_id)
                .order_by(
                    desc(UserBattleStats.wins),
                    UserBattleStats.losses,
                    desc(UserBattleStats.draws),
                )
                .limit(limit)
                .all()
            )
            return [
                {
                    "user_id": stats.user_id,
                    "username": username,
                    "wins": stats.wins,
                    "losses": stats.losses,
                    "draws": stats.draws,
                    "win_rate": (
                        stats.wins / (stats.wins + stats.losses + stats.draws)
                        if (stats.wins + stats.losses + stats.draws) > 0
                        else 0
                    ),
                }
                for stats, username in leaderboard
            ]
        finally:
            session.close()

    def get_transactions_for_user(self, user_id: str, limit: int = 10) -> List[Dict]:
        """Retrieves transaction history for a user."""
        session = self.get_session()
        try:
            transactions = (
                session.query(Transaction)
                .filter(
                    (Transaction.buyer_id == user_id) | (Transaction.seller_id == user_id)
                )
                .order_by(desc(Transaction.tx_date))
                .limit(limit)
                .all()
            )

            history = []
            for tx in transactions:
                transaction_type = ""
                if tx.buyer_id == user_id:
                    transaction_type = "purchase"
                elif tx.seller_id == user_id:
                    transaction_type = "sale"

                history.append({
                    "transaction_id": tx.tx_id,
                    "user_id": user_id, # Can be buyer_id or seller_id
                    "type": transaction_type,
                    "amount": tx.price,
                    "currency_type": "USD", # Assuming USD as a default, since no currency_type in model
                    "timestamp": tx.tx_date.isoformat() if tx.tx_date else None,
                    "metadata": None, # No direct metadata in Transaction model
                })
            return history
        except Exception as e:
            session.rollback() # Add rollback for consistency
            logger.error(f"Error retrieving transactions for user {user_id}: {e}")
            return []
        finally:
            session.close()

    def record_transaction(
        self,
        user_id: str,
        transaction_type: str, # This parameter will be ignored for now due to model limitations
        amount: int,
        currency_type: str, # This parameter will be ignored for now due to model limitations
        metadata: Optional[Dict] = None, # This parameter will be ignored for now due to model limitations
    ) -> int: # Changed return type to int for tx_id
        """Records a financial transaction."""
        session = self.get_session()
        try:
            new_transaction = Transaction(
                buyer_id=user_id,
                price=float(amount), # Convert amount to float for price
                tx_date=datetime.utcnow()
                # item_id and seller_id are not directly provided by current parameters
            )
            session.add(new_transaction)
            session.commit()
            return new_transaction.tx_id
        except Exception as e:
            session.rollback()
            logger.error(f"Error recording transaction for user {user_id}: {e}")
            raise
        finally:
            session.close()

    def get_top_card_collectors(self, limit: int = 10) -> List[Dict]:
        """Retrieves top users based on the total number of unique cards collected."""
        session = self.get_session()
        try:
            top_collectors = (
                session.query(
                    User.user_id,
                    User.username,
                    func.sum(UserCard.quantity).label("total_cards_collected"),
                )
                .join(UserCard, User.user_id == UserCard.user_id)
                .group_by(User.user_id, User.username)
                .order_by(desc("total_cards_collected"))
                .limit(limit)
                .all()
            )

            collectors = []
            for user_id, username, total_cards_collected in top_collectors:
                collectors.append(
                    {
                        "user_id": user_id,
                        "username": username,
                        "total_cards_collected": total_cards_collected,
                    }
                )
            return collectors
        except Exception as e:
            logger.error(f"Error retrieving top card collectors: {e}")
            return []
        finally:
            session.close()

    def get_most_traded_cards(self, limit: int = 10) -> List[Dict]:
        """Retrieves cards that have been traded the most."""
        session = self.get_session()
        try:
            most_traded_cards = (
                session.query(
                    Card.card_id,
                    Card.name,
                    func.count(TradeHistory.trade_id).label("trade_count"),
                )
                .join(CardInstance, Card.card_id == CardInstance.card_id)
                .join(TradeHistory, CardInstance.instance_id == TradeHistory.instance_id)
                .group_by(Card.card_id, Card.name)
                .order_by(desc("trade_count"))
                .limit(limit)
                .all()
            )

            traded_cards = []
            for card_id, card_name, trade_count in most_traded_cards:
                traded_cards.append(
                    {"card_id": card_id, "card_name": card_name, "trade_count": trade_count}
                )
            return traded_cards
        except Exception as e:
            logger.error(f"Error retrieving most traded cards: {e}")
            return []
        finally:
            session.close()

    def check_database_integrity(self) -> Dict:
        """
        Performs a basic integrity check on the database, verifying critical tables exist.
        """
        results = {"valid": True, "tables_checked": 0, "errors": []}
        session = self.get_session()
        try:
            if self._db_type == "postgresql":
                critical_tables = ['users', 'cards', 'creator_packs', 'user_cards', 'transactions', 'pack_purchases', 'dev_pack_supply', 'creator_pack_limits', 'card_instances', 'trade_history', 'user_battle_stats', 'cosmetics_catalog', 'user_cosmetics', 'card_cosmetics', 'battle_log']
                for table_name in critical_tables:
                    results["tables_checked"] += 1
                    count = session.execute(
                        text("SELECT COUNT(*) FROM information_schema.tables "
                             "WHERE table_schema = 'public' AND table_name = :tname"),
                        {"tname": table_name}
                    ).scalar()
                    if not count:
                        results["valid"] = False
                        results["errors"].append(f"Critical PostgreSQL table missing: {table_name}")
            elif self._db_type == "sqlite":
                critical_tables = ['users', 'cards', 'creator_packs', 'user_cards', 'transactions', 'pack_purchases', 'dev_pack_supply', 'creator_pack_limits', 'card_instances', 'trade_history', 'user_battle_stats', 'cosmetics_catalog', 'user_cosmetics', 'card_cosmetics', 'battle_log']
                for table_name in critical_tables:
                    results["tables_checked"] += 1
                    # Check if table exists in SQLite master table
                    table_exists = session.query(
                        exists().where(
                            text(f"name='{table_name}'")
                        )
                    ).select_from(text("sqlite_master")).filter(text("type='table'")).scalar()

                    if not table_exists:
                        results["valid"] = False
                        results["errors"].append(f"Critical SQLite table missing: {table_name}")
            else:
                results["valid"] = False
                results["errors"].append("Unsupported database type for integrity check.")
        except Exception as e:
            results["valid"] = False
            results["errors"].append(f"Database integrity check failed with error: {e}")
            logger.error(f"Database integrity check error: {e}")
        finally:
            session.close()
        return results



    # =========================================================
    # Active Battle tracking (wager crash recovery)
    # =========================================================

    def persist_active_battle(self, match_id: str, player1_id, player2_id, wager_amount: int, tier_key: str = ""):
        """Record a battle as active so wagers can be refunded on crash."""
        with self._engine.connect() as conn:
            try:
                conn.execute(text(
                    "INSERT INTO active_battles (match_id, player1_id, player2_id, wager_amount, tier_key) "
                    "VALUES (:mid, :p1, :p2, :wa, :tk) "
                    "ON CONFLICT (match_id) DO NOTHING"
                ), {"mid": match_id, "p1": str(player1_id), "p2": str(player2_id),
                    "wa": wager_amount, "tk": tier_key})
                conn.commit()
            except Exception as e:
                # Runtime fallback for legacy schemas that still don't have tier_key.
                conn.rollback()
                logger.warning(f"[BATTLE] active_battles.tier_key unavailable, using legacy insert: {e}")
                conn.execute(text(
                    "INSERT INTO active_battles (match_id, player1_id, player2_id, wager_amount) "
                    "VALUES (:mid, :p1, :p2, :wa) "
                    "ON CONFLICT (match_id) DO NOTHING"
                ), {"mid": match_id, "p1": str(player1_id), "p2": str(player2_id),
                    "wa": wager_amount})
                conn.commit()

    def clear_active_battle(self, match_id: str):
        """Remove a battle from active tracking after it completes or is cancelled."""
        with self._engine.connect() as conn:
            conn.execute(text("DELETE FROM active_battles WHERE match_id = :mid"), {"mid": match_id})
            conn.commit()

    def get_all_active_battles(self) -> list:
        """Return all active battles as dicts (used for startup wager refunds)."""
        with self._engine.connect() as conn:
            rows = conn.execute(text("SELECT match_id, player1_id, player2_id, wager_amount FROM active_battles")).fetchall()
        return [{"match_id": r[0], "player1_id": r[1], "player2_id": r[2], "wager_amount": r[3]} for r in rows]

    def _add_gold_direct(self, user_id, amount: int):
        """Directly add gold to a user (used for crash-recovery wager refunds)."""
        self.update_user_economy(str(user_id), gold_change=amount)

    # =========================================================
    # TMA (Telegram Mini App) methods
    # =========================================================

    # Dust values per rarity (disenchant → craft)
    _DUST_VALUE = {"common": 10, "rare": 25, "epic": 50, "legendary": 100, "mythic": 250}
    _CRAFT_COST = {"common": 40, "rare": 100, "epic": 200, "legendary": 400, "mythic": 1000}

    # Offset added to Telegram IDs so they never collide with Discord snowflakes
    _TG_OFFSET = 9_000_000_000

    def get_or_create_telegram_user(
        self, telegram_id: int, telegram_username: str = "", first_name: str = ""
    ) -> dict:
        """Return (or create) the internal user for a Telegram user.
        user_id = str(9_000_000_000 + telegram_id) — pure digits, fits BIGINT.
        Returns dict: user_id, username, telegram_id, is_new."""
        offset_user_id = str(self._TG_OFFSET + telegram_id)
        legacy_user_id = str(telegram_id)
        username = telegram_username or first_name or f"user_{telegram_id}"
        session = self.get_session()
        try:
            # Prefer offset ID, but support legacy telegram-id rows for backward compatibility.
            user = session.query(User).filter_by(user_id=offset_user_id).first()
            resolved_user_id = offset_user_id
            if not user:
                legacy = session.query(User).filter_by(user_id=legacy_user_id).first()
                if legacy:
                    user = legacy
                    resolved_user_id = legacy_user_id

            if user:
                # Keep Telegram identity fresh so partner search can prioritize active players.
                if username:
                    user.username = username
                if not (user.discord_tag or "").startswith("telegram:"):
                    user.discord_tag = f"telegram:{telegram_id}"
                user.last_active = datetime.utcnow()
                session.commit()
                # Use pre-computed user_id string (not user.user_id which may be int if DB column is BIGINT)
                return {"user_id": resolved_user_id, "username": user.username,
                        "telegram_id": telegram_id, "is_new": False}
            # Create user + balance row
            user = User(user_id=offset_user_id, username=username, discord_tag=f"telegram:{telegram_id}")
            session.add(user)
            session.flush()
            balances = UserBalances(user_id=offset_user_id)
            session.add(balances)
            session.commit()
            return {"user_id": offset_user_id, "username": username,
                    "telegram_id": telegram_id, "is_new": True}
        except IntegrityError:
            session.rollback()
            user = session.query(User).filter(User.user_id.in_([offset_user_id, legacy_user_id])).first()
            resolved_user_id = str(user.user_id) if user else offset_user_id
            if user:
                try:
                    if username:
                        user.username = username
                    if not (user.discord_tag or "").startswith("telegram:"):
                        user.discord_tag = f"telegram:{telegram_id}"
                    user.last_active = datetime.utcnow()
                    session.commit()
                except Exception:
                    session.rollback()
            return {"user_id": resolved_user_id, "username": user.username if user else username,
                    "telegram_id": telegram_id, "is_new": False}
        except Exception as e:
            session.rollback()
            logger.error(f"[TMA] get_or_create_telegram_user error: {e}")
            raise
        finally:
            session.close()

    def get_telegram_user_by_id(self, telegram_id: int) -> Optional[dict]:
        """Look up an internal user by their Telegram ID."""
        offset_user_id = str(self._TG_OFFSET + telegram_id)
        legacy_user_id = str(telegram_id)
        session = self.get_session()
        try:
            user = session.query(User).filter_by(user_id=offset_user_id).first()
            resolved_user_id = offset_user_id
            if not user:
                user = session.query(User).filter_by(user_id=legacy_user_id).first()
                resolved_user_id = legacy_user_id
            if not user:
                return None
            return {"user_id": resolved_user_id, "username": user.username}
        finally:
            session.close()

    def register_battle_player(self, telegram_id: int, username: str = "", first_name: str = "") -> dict:
        """Register (or refresh) a Telegram user as available for battle matchmaking."""
        user = self.get_or_create_telegram_user(telegram_id, username, first_name)
        uname = user.get("username") or username or first_name or f"user_{telegram_id}"
        now = datetime.utcnow()
        with self._engine.connect() as conn:
            conn.execute(text(
                """
                INSERT INTO battle_registry (user_id, telegram_id, username, is_active, registered_at, last_seen)
                VALUES (:uid, :tid, :uname, TRUE, :now, :now)
                ON CONFLICT (telegram_id)
                DO UPDATE SET
                    user_id = excluded.user_id,
                    username = excluded.username,
                    is_active = TRUE,
                    last_seen = excluded.last_seen
                """
            ), {"uid": str(user["user_id"]), "tid": int(telegram_id), "uname": uname, "now": now})
            conn.commit()
        return {
            "success": True,
            "user_id": str(user["user_id"]),
            "telegram_id": int(telegram_id),
            "username": uname,
            "registered_at": now.isoformat(),
        }

    def get_registered_battle_player(self, telegram_id: int) -> Optional[dict]:
        with self._engine.connect() as conn:
            row = conn.execute(text(
                """
                SELECT user_id, telegram_id, username, is_active, last_seen
                FROM battle_registry
                WHERE telegram_id = :tid AND is_active = TRUE
                LIMIT 1
                """
            ), {"tid": int(telegram_id)}).fetchone()
        if not row:
            return None
        return {
            "user_id": str(row[0]),
            "telegram_id": int(row[1]),
            "username": row[2],
            "is_active": bool(row[3]),
            "last_seen": _iso(row[4]),
        }

    def list_registered_battle_players(self, exclude_telegram_id: Optional[int] = None, limit: int = 50) -> List[dict]:
        query = (
            "SELECT user_id, telegram_id, username, last_seen "
            "FROM battle_registry WHERE is_active = TRUE "
        )
        params: Dict[str, Union[int, str]] = {"lim": int(limit)}
        if exclude_telegram_id is not None:
            query += "AND telegram_id != :exclude_tid "
            params["exclude_tid"] = int(exclude_telegram_id)
        query += "ORDER BY last_seen DESC LIMIT :lim"

        with self._engine.connect() as conn:
            rows = conn.execute(text(query), params).fetchall()

        out = []
        for r in rows:
            out.append({
                "user_id": str(r[0]),
                "telegram_id": int(r[1]),
                "username": r[2] or f"user_{r[1]}",
                "last_seen": _iso(r[3]),
            })
        return out

    def generate_tier_pack_cards(self, user_id, tier: str) -> List[Dict]:
        """Generate random cards for a tier pack, grant to user, return card dicts for UI."""
        import random

        from config.economy import PACK_PRICING

        pricing = PACK_PRICING.get(tier, {})
        n = int(pricing.get("cards_per_pack", 5))
        user_id_str = str(user_id)
        rarity_pool = {
            "community": ["common"] * 4 + ["rare"] * 1,
            "gold": ["rare"] * 2 + ["epic"] * 2 + ["legendary"] * 1,
            "platinum": ["epic"] * 5 + ["legendary"] * 4 + ["mythic"] * 1,
        }.get(tier, ["common"] * 5)

        session = self.get_session()
        try:
            card_ids: List[str] = []
            out: List[Dict] = []
            for _ in range(n):
                target = random.choice(rarity_pool).lower()
                rows = (
                    session.query(Card)
                    .filter(func.lower(Card.rarity) == target)
                    .limit(300)
                    .all()
                )
                if not rows:
                    rows = session.query(Card).limit(300).all()
                if not rows:
                    logger.warning("[tier_pack] No cards in master table")
                    return []
                pick = random.choice(rows)
                card_ids.append(pick.card_id)
                out.append(pick.to_dict())
        except Exception as e:
            logger.error(f"[tier_pack] generate_tier_pack_cards error: {e}")
            return []
        finally:
            session.close()

        for cid in card_ids:
            self.add_card_to_collection(
                user_id_str, cid, quantity=1, acquired_from="tier_pack"
            )
        return out

    def set_user_referrer_host_if_unset(self, user_id: str, host_token: str) -> bool:
        """First-touch: store referrer host token on user if empty."""
        if not host_token or not str(host_token).strip():
            return False
        token = str(host_token).strip()[:128]
        session = self.get_session()
        try:
            user = session.query(User).filter_by(user_id=str(user_id)).first()
            if not user:
                return False
            if user.referrer_host_token:
                return False
            user.referrer_host_token = token
            user.referrer_set_at = datetime.utcnow()
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"[host] set_user_referrer_host_if_unset: {e}")
            return False
        finally:
            session.close()

    def get_telegram_host_by_token(self, host_token: Optional[str]) -> Optional[dict]:
        if not host_token:
            return None
        session = self.get_session()
        try:
            row = session.query(TelegramHost).filter_by(host_token=str(host_token).strip()).first()
            if not row:
                return None
            return {
                "host_token": row.host_token,
                "owner_telegram_id": int(row.owner_telegram_id),
                "share_bps": int(row.share_bps or 0),
                "label": row.label,
            }
        finally:
            session.close()

    def record_revenue_event(
        self,
        stripe_session_id: str,
        user_id: str,
        product_type: str,
        gross_cents: int,
        platform_cents: int,
        host_cents: int,
        creator_cents: int = 0,
        host_token: Optional[str] = None,
        rail: str = "stripe",
        gross_stars: int = 0,
        net_cents: Optional[int] = None,
    ) -> bool:
        """Insert revenue_events row if session id not seen (idempotent).
        For Stars, `net_cents` is what the developer actually receives; host share is on net."""
        session = self.get_session()
        try:
            exists = session.query(RevenueEvent).filter_by(stripe_session_id=stripe_session_id).first()
            if exists:
                return False
            session.add(
                RevenueEvent(
                    stripe_session_id=stripe_session_id,
                    user_id=str(user_id),
                    product_type=product_type,
                    gross_cents=gross_cents,
                    platform_cents=platform_cents,
                    host_cents=host_cents,
                    creator_cents=creator_cents,
                    host_token=host_token,
                    rail=rail,
                    gross_stars=int(gross_stars or 0),
                    net_cents=int(net_cents if net_cents is not None else (platform_cents + host_cents)),
                )
            )
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"[revenue] record_revenue_event: {e}")
            return False
        finally:
            session.close()

    def generate_tma_link_code(self, user_id: str) -> str:
        """Generate a 6-char uppercase code for Telegram↔Discord linking (10 min TTL)."""
        import random, string
        from datetime import timedelta
        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        expires = datetime.utcnow() + timedelta(minutes=10)
        session = self.get_session()
        try:
            # Delete any existing unexpired code for this user
            session.query(TmaLinkCode).filter_by(user_id=user_id).delete()
            session.add(TmaLinkCode(code=code, user_id=user_id, expires_at=expires))
            session.commit()
            return code
        except Exception as e:
            session.rollback()
            logger.error(f"[TMA] generate_tma_link_code error: {e}")
            raise
        finally:
            session.close()

    def consume_tma_link_code(self, code: str, discord_id: int) -> dict:
        """Consume a TMA link code and associate the discord_id with the user.
        Returns {"success": bool, "user_id": str | None}."""
        session = self.get_session()
        try:
            row = session.query(TmaLinkCode).filter_by(code=code).first()
            if not row:
                return {"success": False, "user_id": None, "error": "Invalid code"}
            if row.expires_at and datetime.utcnow() > row.expires_at:
                session.delete(row)
                session.commit()
                return {"success": False, "user_id": None, "error": "Code expired"}
            user_id = row.user_id
            # Store discord link on the user row
            user = session.query(User).filter_by(user_id=user_id).first()
            if user:
                user.discord_tag = f"discord:{discord_id}"
            session.delete(row)
            session.commit()
            return {"success": True, "user_id": user_id, "discord_id": discord_id}
        except Exception as e:
            session.rollback()
            logger.error(f"[TMA] consume_tma_link_code error: {e}")
            return {"success": False, "user_id": None, "error": str(e)}
        finally:
            session.close()

    def get_user_collection(self, user_id) -> List[dict]:
        """Return all cards owned by a user as enrichable dicts."""
        user_ids = self._user_id_variants(user_id)
        session = self.get_session()
        try:
            rows = (
                session.query(UserCard, Card)
                .join(Card, UserCard.card_id == Card.card_id)
                .filter(UserCard.user_id.in_(user_ids))
                .all()
            )
            return [
                {
                    "card_id":     c.card_id,
                    "name":        c.name,
                    "artist_name": c.artist_name,
                    "title":       c.title,
                    "image_url":   c.image_url,
                    "youtube_url": c.youtube_url,
                    "rarity":      c.rarity,
                    "tier":        c.tier,
                    "variant":     c.variant,
                    "era":         c.era,
                    "impact":      c.impact or 50,
                    "skill":       c.skill or 50,
                    "longevity":   c.longevity or 50,
                    "culture":     c.culture or 50,
                    "hype":        c.hype or 50,
                    "genre_family": (getattr(c, "genre_family", None) or "NEUTRAL"),
                    "momentum":    bool(getattr(c, "momentum_hot", False)),
                    "view_delta":  int(getattr(c, "view_delta", 0) or 0),
                    "quantity":    uc.quantity,
                    "is_favorite": uc.is_favorite,
                    "acquired_at": uc.acquired_at.isoformat() if uc.acquired_at else None,
                }
                for uc, c in rows
            ]
        except Exception as e:
            logger.error(f"[TMA] get_user_collection error: {e}")
            return []
        finally:
            session.close()

    def _pack_to_dict(self, pack: CreatorPacks, session) -> dict:
        """Convert a CreatorPacks ORM object into a dict including its card list."""
        # Get cards via CreatorPackCards join
        rows = (
            session.query(Card)
            .join(CreatorPackCards, Card.card_id == CreatorPackCards.card_id)
            .filter(CreatorPackCards.pack_id == pack.pack_id)
            .all()
        )
        cards = [
            {
                "card_id":     c.card_id,
                "name":        c.name,
                "artist_name": c.artist_name,
                "title":       c.title,
                "image_url":   c.image_url,
                "youtube_url": c.youtube_url,
                "rarity":      c.rarity,
                "tier":        c.tier,
                "impact":      c.impact or 50,
                "skill":       c.skill or 50,
                "longevity":   c.longevity or 50,
                "culture":     c.culture or 50,
                "hype":        c.hype or 50,
            }
            for c in rows
        ]
        return {
            "pack_id":         pack.pack_id,
            "name":            pack.name,
            "description":     pack.description,
            "cover_image_url": pack.cover_image_url,
            "pack_tier":       pack.pack_tier,
            "genre":           pack.genre,
            "cards":           cards,
            "card_count":      len(cards),
        }

    def get_user_purchased_packs(self, user_id, limit: int = 50) -> List[dict]:
        """Return packs the user has purchased (opened and unopened)."""
        user_ids = self._user_id_variants(user_id)
        session = self.get_session()
        try:
            purchases = (
                session.query(PackPurchase, CreatorPacks)
                .join(CreatorPacks, PackPurchase.pack_id == CreatorPacks.pack_id)
                .filter(PackPurchase.buyer_id.in_(user_ids))
                .order_by(desc(PackPurchase.purchased_at))
                .limit(limit)
                .all()
            )
            result = []
            for purchase, pack in purchases:
                d = self._pack_to_dict(pack, session)
                d["purchase_id"] = str(purchase.purchase_id)
                result.append(d)
            return result
        except Exception as e:
            logger.error(f"[TMA] get_user_purchased_packs error: {e}")
            return []
        finally:
            session.close()

    # --- Compatibility helpers for Discord cogs / legacy flows ---

    def get_or_create_user(self, user_id, username: str = "", discord_tag: str = "") -> dict:
        """Ensure user + balance rows exist. Returns a compact user dict."""
        user_id = str(user_id)
        username = username or f"user_{user_id}"
        session = self.get_session()
        try:
            user = session.query(User).filter_by(user_id=user_id).first()
            created = False
            if not user:
                user = User(user_id=user_id, username=username, discord_tag=discord_tag)
                session.add(user)
                created = True
            else:
                if username:
                    user.username = username
                if discord_tag:
                    user.discord_tag = discord_tag

            balances = session.query(UserBalances).filter_by(user_id=user_id).first()
            if not balances:
                session.add(UserBalances(user_id=user_id))
                created = True

            session.commit()
            return {"user_id": user_id, "username": user.username, "created": created}
        except Exception as e:
            session.rollback()
            logger.error(f"[DB] get_or_create_user error: {e}")
            return {"user_id": user_id, "username": username, "created": False}
        finally:
            session.close()

    def ensure_user_exists(self, user_id, username: str = "") -> bool:
        """Legacy helper used by Discord cogs."""
        result = self.get_or_create_user(user_id, username=username, discord_tag=str(username))
        return bool(result.get("user_id"))

    def create_creator_pack(
        self,
        creator_id,
        name: str,
        description: str = "",
        pack_size: int = 5,
        pack_tier: str = "community",
        genre: str = "music",
        cover_image_url: str = "",
    ) -> str:
        """Create a creator pack and return pack_id."""
        session = self.get_session()
        try:
            pack_id = f"pack_{uuid.uuid4().hex[:8]}"
            pack = CreatorPacks(
                pack_id=pack_id,
                name=name,
                creator_id=str(creator_id),
                description=description,
                card_count=pack_size,
                pack_tier=pack_tier,
                genre=genre,
                cover_image_url=cover_image_url,
                is_public=True,
            )
            session.add(pack)
            session.commit()
            return pack_id
        except Exception as e:
            session.rollback()
            logger.error(f"[DB] create_creator_pack error: {e}")
            return ""
        finally:
            session.close()

    def add_card_to_master(self, card_data: Dict) -> bool:
        """Insert/update a card in the master `cards` table."""
        session = self.get_session()
        try:
            card_id = card_data.get("card_id")
            if not card_id:
                return False

            card = session.query(Card).filter_by(card_id=card_id).first()
            if not card:
                card = Card(card_id=card_id)
                session.add(card)

            # Preserve existing non-empty URLs when new payload is empty.
            new_image = card_data.get("image_url")
            new_yt = card_data.get("youtube_url")

            card.name = card_data.get("name") or card.name or "Unknown"
            card.artist_name = card_data.get("artist_name") or card_data.get("name") or card.artist_name
            card.title = card_data.get("title") or card.title
            if new_image:
                card.image_url = new_image
            if new_yt:
                card.youtube_url = new_yt
            card.rarity = (card_data.get("rarity") or card.rarity or "common")
            card.tier = card_data.get("tier") or card.tier
            card.variant = card_data.get("variant") or card.variant or "Classic"
            card.era = card_data.get("era") or card.era
            card.impact = card_data.get("impact", card.impact)
            card.skill = card_data.get("skill", card.skill)
            card.longevity = card_data.get("longevity", card.longevity)
            card.culture = card_data.get("culture", card.culture)
            card.hype = card_data.get("hype", card.hype)
            card.pack_id = card_data.get("pack_id") or card.pack_id
            card.created_by_user_id = str(card_data.get("created_by_user_id") or card.created_by_user_id or "")
            # Phase 4a: genre family resolved once at creation, never at battle time.
            explicit_family = card_data.get("genre_family")
            if explicit_family:
                card.genre_family = str(explicit_family).upper()
                card.genre_source = card_data.get("genre_source") or "provided"
            elif not getattr(card, "genre_source", None):
                card.genre_family, card.genre_source = self._resolve_genre_for_card(
                    card.artist_name or card.name, card.title
                )
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"[DB] add_card_to_master error: {e}")
            return False
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Phase 4 - genre + momentum helpers
    # ------------------------------------------------------------------
    def _resolve_genre_for_card(self, artist: str, title: str):
        """Best-effort external lookup; never raises. Returns (family, source)."""
        try:
            from services.genre_resolver import RESOLVE_ON_CREATE, resolve_card_genre
            if not RESOLVE_ON_CREATE:
                return "NEUTRAL", None
            fam, source = resolve_card_genre(artist, title)
            return fam.value, source
        except Exception as e:
            logger.warning(f"[GENRE] resolve failed for {artist!r}: {e}")
            return "NEUTRAL", None

    def get_cards_missing_genre(self, limit: int = 200) -> List[dict]:
        """Cards whose genre has never been resolved (no recorded source)."""
        session = self.get_session()
        try:
            rows = (
                session.query(Card)
                .filter(Card.genre_source.is_(None))
                .order_by(Card.created_at.desc())
                .limit(limit)
                .all()
            )
            return [{"card_id": c.card_id, "name": c.name, "artist_name": c.artist_name, "title": c.title}
                    for c in rows]
        finally:
            session.close()

    def set_card_genre(self, card_id: str, family: str, source: Optional[str]) -> bool:
        session = self.get_session()
        try:
            card = session.query(Card).filter_by(card_id=card_id).first()
            if not card:
                return False
            card.genre_family = (family or "NEUTRAL").upper()
            card.genre_source = source or "none"
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"[GENRE] set_card_genre error: {e}")
            return False
        finally:
            session.close()

    def genre_distribution(self) -> Dict[str, int]:
        session = self.get_session()
        try:
            rows = session.query(Card.genre_family, func.count(Card.card_id)).group_by(Card.genre_family).all()
            return {(fam or "NEUTRAL"): int(n) for fam, n in rows}
        finally:
            session.close()

    def get_cards_with_youtube(self, limit: int = 5000) -> List[dict]:
        session = self.get_session()
        try:
            rows = (
                session.query(Card)
                .filter(Card.youtube_url.isnot(None), Card.youtube_url != "")
                .limit(limit)
                .all()
            )
            return [{"card_id": c.card_id, "youtube_url": c.youtube_url,
                     "view_count": c.view_count, "view_delta": c.view_delta or 0} for c in rows]
        finally:
            session.close()

    def update_card_views(self, card_id: str, views: int, delta: int, checked_at) -> bool:
        session = self.get_session()
        try:
            card = session.query(Card).filter_by(card_id=card_id).first()
            if not card:
                return False
            card.view_count = int(views)
            card.view_delta = int(delta)
            card.views_checked_at = checked_at
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"[MOMENTUM] update_card_views error: {e}")
            return False
        finally:
            session.close()

    def set_momentum_flags(self, hot_card_ids) -> int:
        """Flag exactly the given cards as hot; clear everything else."""
        hot = list({str(x) for x in hot_card_ids})
        session = self.get_session()
        try:
            session.query(Card).filter(Card.momentum_hot.is_(True)).update(
                {Card.momentum_hot: False}, synchronize_session=False)
            n = 0
            if hot:
                n = session.query(Card).filter(Card.card_id.in_(hot)).update(
                    {Card.momentum_hot: True}, synchronize_session=False)
            session.commit()
            return int(n)
        except Exception as e:
            session.rollback()
            logger.error(f"[MOMENTUM] set_momentum_flags error: {e}")
            return 0
        finally:
            session.close()

    def get_hot_card_ids(self) -> set:
        session = self.get_session()
        try:
            return {c.card_id for c in session.query(Card.card_id).filter(Card.momentum_hot.is_(True)).all()}
        finally:
            session.close()

    def add_card_to_pack(self, pack_id: str, card_data: Dict) -> bool:
        """Link a card to a creator pack."""
        session = self.get_session()
        try:
            card_id = card_data.get("card_id")
            if not card_id:
                return False

            link = session.query(CreatorPackCards).filter_by(pack_id=pack_id, card_id=card_id).first()
            if not link:
                session.add(CreatorPackCards(pack_id=pack_id, card_id=card_id))

            # Best-effort: keep card_count synced to number of links.
            pack = session.query(CreatorPacks).filter_by(pack_id=pack_id).first()
            if pack:
                count = session.query(CreatorPackCards).filter_by(pack_id=pack_id).count()
                pack.card_count = count + (0 if link else 1)

            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"[DB] add_card_to_pack error: {e}")
            return False
        finally:
            session.close()

    def add_to_dev_supply(self, pack_id: str, quantity: int = 1) -> bool:
        """Increment dev supply for a pack."""
        session = self.get_session()
        try:
            row = session.query(DevPackSupply).filter_by(pack_id=pack_id).first()
            if row:
                row.quantity = (row.quantity or 0) + quantity
            else:
                session.add(DevPackSupply(pack_id=pack_id, quantity=quantity))
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"[DB] add_to_dev_supply error: {e}")
            return False
        finally:
            session.close()

    def get_random_live_pack_by_tier(self, tier: str = "community") -> Optional[dict]:
        """Return a random public pack for a tier in drop/start-game format."""
        tier = (tier or "community").lower()
        session = self.get_session()
        try:
            # Preferred modern path (ORM fields).
            pack = (
                session.query(CreatorPacks)
                .filter(CreatorPacks.is_public == True)
                .filter(func.lower(func.coalesce(CreatorPacks.pack_tier, "community")) == tier)
                .order_by(func.random())
                .first()
            )
            # Fallback for legacy schemas where "live" is represented by status='LIVE'
            # and/or pack_tier is NULL.
            if not pack:
                with self._engine.connect() as conn:
                    row = conn.execute(text("""
                        SELECT pack_id, name, COALESCE(pack_tier, :tier) AS pack_tier,
                               COALESCE(pack_size, card_count, 0) AS pack_size,
                               COALESCE(genre, 'music') AS genre
                        FROM creator_packs
                        WHERE (LOWER(COALESCE(pack_tier, :tier)) = :tier)
                          AND (
                                COALESCE(is_public, 0) = 1
                                OR UPPER(COALESCE(status, '')) = 'LIVE'
                              )
                        ORDER BY RANDOM()
                        LIMIT 1
                    """), {"tier": tier}).fetchone()
                if row:
                    return {
                        "pack_id": row[0],
                        "name": row[1],
                        "tier": row[2] or tier,
                        "pack_size": row[3] or 0,
                        "genre": row[4] or "music",
                    }
            if not pack:
                return None
            return {
                "pack_id": pack.pack_id,
                "name": pack.name,
                "tier": (pack.pack_tier or tier),
                "pack_size": pack.card_count or 0,
                "genre": pack.genre or "music",
            }
        except Exception as e:
            logger.error(f"[DB] get_random_live_pack_by_tier error: {e}")
            return None
        finally:
            session.close()

    def grant_pack_to_user(self, pack_id: str, user_id) -> dict:
        """Legacy helper: grant/open a pack from dev supply to a user."""
        user_id = str(user_id)
        session = self.get_session()
        try:
            supply = session.query(DevPackSupply).filter_by(pack_id=pack_id).first()
            if not supply or (supply.quantity or 0) <= 0:
                return {"success": False, "error": "No supply available"}
            supply.quantity = (supply.quantity or 0) - 1
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"[DB] grant_pack_to_user supply update error: {e}")
            return {"success": False, "error": str(e)}
        finally:
            session.close()
        return self.open_pack_for_drop(pack_id, user_id)

    def get_user_deck(self, user_id, limit: int = 5) -> List[dict]:
        """Return strongest `limit` cards from collection."""
        cards = self.get_user_collection(user_id)
        cards.sort(
            key=lambda c: (
                (c.get("impact") or 50)
                + (c.get("skill") or 50)
                + (c.get("longevity") or 50)
                + (c.get("culture") or 50)
                + (c.get("hype") or 50)
            ),
            reverse=True,
        )
        return cards[:limit]

    def record_battle(self, battle_data: Dict) -> bool:
        """Persist battle history row (best-effort; table auto-created if missing)."""
        try:
            with self._engine.connect() as conn:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS battle_history (
                        battle_id TEXT PRIMARY KEY,
                        player1_id TEXT NOT NULL,
                        player2_id TEXT NOT NULL,
                        player1_card_id TEXT,
                        player2_card_id TEXT,
                        winner INTEGER,
                        player1_power INTEGER,
                        player2_power INTEGER,
                        player1_critical BOOLEAN DEFAULT FALSE,
                        player2_critical BOOLEAN DEFAULT FALSE,
                        wager_tier TEXT,
                        wager_amount INTEGER DEFAULT 0,
                        player1_gold_reward INTEGER DEFAULT 0,
                        player2_gold_reward INTEGER DEFAULT 0,
                        player1_xp_reward INTEGER DEFAULT 0,
                        player2_xp_reward INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                conn.execute(text("""
                    INSERT INTO battle_history (
                        battle_id, player1_id, player2_id, player1_card_id, player2_card_id,
                        winner, player1_power, player2_power, player1_critical, player2_critical,
                        wager_tier, wager_amount, player1_gold_reward, player2_gold_reward,
                        player1_xp_reward, player2_xp_reward
                    ) VALUES (
                        :battle_id, :player1_id, :player2_id, :player1_card_id, :player2_card_id,
                        :winner, :player1_power, :player2_power, :player1_critical, :player2_critical,
                        :wager_tier, :wager_amount, :player1_gold_reward, :player2_gold_reward,
                        :player1_xp_reward, :player2_xp_reward
                    )
                """), {
                    "battle_id": battle_data.get("battle_id"),
                    "player1_id": str(battle_data.get("player1_id", "")),
                    "player2_id": str(battle_data.get("player2_id", "")),
                    "player1_card_id": battle_data.get("player1_card_id"),
                    "player2_card_id": battle_data.get("player2_card_id"),
                    "winner": battle_data.get("winner"),
                    "player1_power": battle_data.get("player1_power"),
                    "player2_power": battle_data.get("player2_power"),
                    "player1_critical": bool(battle_data.get("player1_critical", False)),
                    "player2_critical": bool(battle_data.get("player2_critical", False)),
                    "wager_tier": battle_data.get("wager_tier"),
                    "wager_amount": battle_data.get("wager_amount", 0),
                    "player1_gold_reward": battle_data.get("player1_gold_reward", 0),
                    "player2_gold_reward": battle_data.get("player2_gold_reward", 0),
                    "player1_xp_reward": battle_data.get("player1_xp_reward", 0),
                    "player2_xp_reward": battle_data.get("player2_xp_reward", 0),
                })
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"[DB] record_battle error: {e}")
            return False

    def get_live_packs(self, limit: int = 20) -> List[dict]:
        """Return packs available in Telegram store.

        Includes public packs plus legacy Discord-created packs that have cards.
        """
        session = self.get_session()
        try:
            packs = (
                session.query(CreatorPacks)
                .filter(
                    (CreatorPacks.is_public == True) |  # noqa: E712
                    (CreatorPacks.card_count > 0)
                )
                .order_by(desc(CreatorPacks.created_at))
                .limit(limit)
                .all()
            )
            return [self._pack_to_dict(p, session) for p in packs]
        except Exception as e:
            logger.error(f"[TMA] get_live_packs error: {e}")
            return []
        finally:
            session.close()

    def purchase_live_pack(self, user_id: str, pack_id: str) -> dict:
        """Purchase a store pack with gold and add it to owned purchases."""
        session = self.get_session()
        try:
            uid = str(user_id)
            pack = session.query(CreatorPacks).filter_by(pack_id=pack_id).first()
            if not pack:
                return {"success": False, "error": "Pack not found in store"}

            # Default tier prices when pack-specific price is unset.
            tier_defaults = {
                "community": 500,
                "gold": 1000,
                "platinum": 2500,
            }
            tier_key = (pack.pack_tier or "").strip().lower()
            price = int(pack.price or 0)
            if price <= 0:
                price = tier_defaults.get(tier_key, 500)

            bal = session.query(UserBalances).filter_by(user_id=uid).first()
            if not bal:
                bal = UserBalances(user_id=uid)
                session.add(bal)
                session.flush()
            if (bal.gold or 0) < price:
                return {"success": False, "error": f"Not enough gold ({price} required)"}

            bal.gold = (bal.gold or 0) - price
            purchase = PackPurchase(
                buyer_id=uid,
                pack_id=pack_id,
                purchased_at=datetime.utcnow(),
                cards_received=None,
            )
            session.add(purchase)
            session.commit()
            return {
                "success": True,
                "pack_id": pack_id,
                "price": price,
                "remaining_gold": bal.gold or 0,
                "purchase_id": str(purchase.purchase_id),
            }
        except Exception as e:
            session.rollback()
            logger.error(f"[TMA] purchase_live_pack error: {e}")
            return {"success": False, "error": str(e)}
        finally:
            session.close()

    def open_pack_for_drop(self, pack_id: str, user_id) -> dict:
        """Open a purchased pack: award its cards and mark the purchase as opened."""
        user_id_str = str(user_id)
        user_ids = self._user_id_variants(user_id)
        session = self.get_session()
        try:
            purchase = (
                session.query(PackPurchase)
                .filter(PackPurchase.pack_id == pack_id, PackPurchase.buyer_id.in_(user_ids))
                .filter(PackPurchase.cards_received.is_(None))
                .first()
            )

            pack = session.query(CreatorPacks).filter_by(pack_id=pack_id).first()
            if not pack:
                return {"success": False, "error": "Pack definition not found"}

            # Get cards in this pack (normalized link table first).
            card_rows = (
                session.query(Card)
                .join(CreatorPackCards, Card.card_id == CreatorPackCards.card_id)
                .filter(CreatorPackCards.pack_id == pack_id)
                .all()
            )
            # Fallback: older packs may store cards only in creator_packs.cards_data JSON.
            if not card_rows and pack.cards_data:
                cards_data = pack.cards_data
                if isinstance(cards_data, str):
                    try:
                        cards_data = json.loads(cards_data)
                    except Exception:
                        cards_data = []
                if isinstance(cards_data, list):
                    for raw in cards_data:
                        if not isinstance(raw, dict):
                            continue
                        card_id = raw.get("card_id") or f"{pack_id}_{uuid.uuid4().hex[:8]}"
                        payload = dict(raw)
                        payload["card_id"] = card_id
                        payload["pack_id"] = pack_id
                        payload.setdefault("name", raw.get("artist_name") or "Unknown")
                        payload.setdefault("rarity", "common")
                        self.add_card_to_master(payload)
                        if not session.query(CreatorPackCards).filter_by(pack_id=pack_id, card_id=card_id).first():
                            session.add(CreatorPackCards(pack_id=pack_id, card_id=card_id))
                    session.flush()
                    card_rows = (
                        session.query(Card)
                        .join(CreatorPackCards, Card.card_id == CreatorPackCards.card_id)
                        .filter(CreatorPackCards.pack_id == pack_id)
                        .all()
                    )
            cards_out = []
            for c in card_rows:
                # Add to user collection
                existing = session.query(UserCard).filter_by(
                    user_id=user_id_str, card_id=c.card_id
                ).first()
                if existing:
                    existing.quantity += 1
                else:
                    session.add(UserCard(
                        user_id=user_id_str, card_id=c.card_id,
                        quantity=1, acquired_from="pack_open",
                        acquired_at=datetime.utcnow(),
                    ))
                cards_out.append({
                    "card_id":   c.card_id,
                    "name":      c.name,
                    "title":     c.title,
                    "image_url": c.image_url,
                    "rarity":    c.rarity,
                    "tier":      c.tier,
                    "impact":    c.impact or 50,
                    "skill":     c.skill or 50,
                    "longevity": c.longevity or 50,
                    "culture":   c.culture or 50,
                    "hype":      c.hype or 50,
                })

            # Mark pack as opened if this came from purchases.
            # For direct channel drops, create a synthetic purchase history row.
            received_ids = [c["card_id"] for c in cards_out]
            if purchase:
                purchase.cards_received = received_ids
            else:
                session.add(PackPurchase(
                    buyer_id=user_id_str,
                    pack_id=pack_id,
                    purchased_at=datetime.utcnow(),
                    cards_received=received_ids,
                ))
            session.commit()
            return {"success": True, "cards": cards_out}
        except Exception as e:
            session.rollback()
            logger.error(f"[TMA] open_pack_for_drop error: {e}")
            return {"success": False, "error": str(e)}
        finally:
            session.close()

    def get_user_economy(self, user_id) -> dict:
        """Return user's economy stats (gold, tickets, dust, xp, level, streak)."""
        user_id = str(user_id)
        session = self.get_session()
        try:
            b = session.query(UserBalances).filter_by(user_id=user_id).first()
            if not b:
                return {"gold": 0, "tickets": 0, "dust": 0, "xp": 0,
                        "level": 1, "last_daily_claim": None, "daily_streak": 0,
                        "can_claim_daily": True, "daily_cooldown_seconds": 0}
            now = datetime.utcnow()
            last_claim_at = b.last_daily_claim
            cooldown_seconds = 0
            can_claim = True
            # Heal obviously invalid future timestamps so users aren't locked out.
            if last_claim_at and last_claim_at > (now + timedelta(minutes=5)):
                logger.warning(
                    f"[TMA] get_user_economy correcting future last_daily_claim "
                    f"user_id={user_id} last_claim_at={last_claim_at.isoformat()}"
                )
                b.last_daily_claim = None
                b.daily_streak = 0
                last_claim_at = None
                session.commit()
            if last_claim_at:
                elapsed = (now - last_claim_at).total_seconds()
                if elapsed < 24 * 3600:
                    cooldown_seconds = max(0, int((24 * 3600) - elapsed))
                    can_claim = cooldown_seconds <= 0
            return {
                "gold":             b.gold or 0,
                "tickets":          b.tickets or 0,
                "dust":             b.dust or 0,
                "xp":               b.xp or 0,
                "level":            b.level or 1,
                "last_daily_claim": last_claim_at.isoformat() if last_claim_at else None,
                "daily_streak":     b.daily_streak or 0,
                "can_claim_daily":  can_claim,
                "daily_cooldown_seconds": cooldown_seconds,
            }
        finally:
            session.close()

    def update_user_economy(
        self, user_id,
        gold_change: int = 0,
        xp_change: int = 0,
        tickets_change: int = 0,
    ) -> bool:
        """Apply delta changes to a user's economy. Creates balance row if missing."""
        user_id = str(user_id)
        session = self.get_session()
        try:
            b = session.query(UserBalances).filter_by(user_id=user_id).first()
            if not b:
                b = UserBalances(user_id=user_id)
                session.add(b)
                session.flush()
            if gold_change:
                b.gold = max(0, (b.gold or 0) + gold_change)
            if xp_change:
                b.xp = (b.xp or 0) + xp_change
                b.level = max(1, ((b.xp or 0) // 1000) + 1)
            if tickets_change:
                b.tickets = max(0, (b.tickets or 0) + tickets_change)
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"[TMA] update_user_economy error: {e}")
            return False
        finally:
            session.close()

    def claim_daily_reward(self, user_id) -> dict:
        """Claim the daily reward. Returns success/failure with gold and cards."""
        user_id = str(user_id)
        import random
        from datetime import timedelta
        _DAILY_GOLD = {0: 100, 3: 150, 7: 300, 14: 600, 30: 1100}

        session = self.get_session()
        try:
            now = datetime.utcnow()

            # Use user_balances.last_daily_claim as canonical source.
            # This avoids hard failure if a legacy daily_claims table is missing.
            b = session.query(UserBalances).filter_by(user_id=user_id).first()
            if not b:
                b = UserBalances(user_id=user_id)
                session.add(b)
                session.flush()

            last_claim_at = b.last_daily_claim
            if last_claim_at:
                # Heal obviously invalid future timestamps so users aren't locked out.
                if last_claim_at > (now + timedelta(minutes=5)):
                    logger.warning(
                        f"[TMA] claim_daily_reward correcting future last_daily_claim "
                        f"user_id={user_id} last_claim_at={last_claim_at.isoformat()}"
                    )
                    b.last_daily_claim = None
                    b.daily_streak = 0
                    last_claim_at = None
                    session.flush()
                # Small clock skew tolerance (few minutes) still clamps to now.
                effective_last_claim = (
                    last_claim_at if (last_claim_at and last_claim_at <= (now + timedelta(minutes=5))) else now
                )
                hours_since = (now - effective_last_claim).total_seconds() / 3600
                if hours_since < 24:
                    next_claim = effective_last_claim + timedelta(hours=24)
                    remaining_seconds = max(0, int((24 - hours_since) * 3600))
                    return {
                        "success": False,
                        "message": "Already claimed today",
                        "error": "Already claimed today",
                        "next_claim_at": next_claim.isoformat(),
                        "time_until": remaining_seconds,
                    }

            streak = b.daily_streak or 0
            if last_claim_at and (now - last_claim_at).total_seconds() / 3600 < 48:
                streak += 1
            else:
                streak = 1

            # Gold reward based on streak
            gold = next((v for k, v in sorted(_DAILY_GOLD.items(), reverse=True) if streak >= k), 100)

            # Random common/rare cards (1-3)
            all_cards = session.query(Card).filter(
                Card.rarity.in_(["common", "Common", "rare", "Rare"])
            ).limit(50).all()
            awarded_cards = []
            if all_cards:
                sample = random.sample(all_cards, min(random.randint(1, 3), len(all_cards)))
                for c in sample:
                    existing = session.query(UserCard).filter_by(
                        user_id=user_id, card_id=c.card_id
                    ).first()
                    if existing:
                        existing.quantity += 1
                    else:
                        session.add(UserCard(
                            user_id=user_id, card_id=c.card_id,
                            quantity=1, acquired_from="daily",
                            acquired_at=now,
                        ))
                    awarded_cards.append({
                        "card_id":   c.card_id,
                        "name":      c.name,
                        "title":     c.title,
                        "image_url": c.image_url,
                        "rarity":    c.rarity,
                    })

            # Update balance and claim record
            b.gold = (b.gold or 0) + gold
            b.daily_streak = streak
            b.last_daily_claim = now

            # Legacy compatibility: keep old user_inventory table in sync when present.
            try:
                session.execute(text("""
                    INSERT INTO user_inventory (user_id, gold, tickets, last_daily_claim, daily_streak)
                    VALUES (:uid, :gold, :tickets, :last_claim, :streak)
                    ON CONFLICT(user_id) DO UPDATE SET
                        gold = COALESCE(user_inventory.gold, 0) + :gold,
                        tickets = COALESCE(user_inventory.tickets, 0) + :tickets,
                        last_daily_claim = :last_claim,
                        daily_streak = :streak
                """), {
                    "uid": user_id,
                    "gold": gold,
                    "tickets": 0,
                    "last_claim": now,
                    "streak": streak,
                })
            except Exception:
                # Some environments no longer maintain user_inventory.
                pass

            session.commit()
            return {
                "success": True,
                "gold": gold,
                "cards": awarded_cards,
                "streak": streak,
                "message": f"Claimed {gold} gold + {len(awarded_cards)} cards! (Streak: {streak})",
            }
        except Exception as e:
            session.rollback()
            logger.error(f"[TMA] claim_daily_reward error: {e}")
            return {"success": False, "message": str(e)}
        finally:
            session.close()

    def get_user_stats(self, user_id) -> dict:
        """Return battle stats for a user."""
        user_id = str(user_id)
        session = self.get_session()
        try:
            user = session.query(User).filter_by(user_id=user_id).first()
            stats = session.query(UserBattleStats).filter_by(user_id=user_id).first()
            total = (user.total_battles if user else 0) or 0
            wins  = (stats.wins if stats else 0) or (user.wins if user else 0) or 0
            losses = (stats.losses if stats else 0) or (user.losses if user else 0) or 0
            return {"total_battles": total, "wins": wins, "losses": losses}
        finally:
            session.close()

    def get_leaderboard(self, metric: str = "wins", limit: int = 10) -> List[dict]:
        """Return top players by wins, gold, or total_battles."""
        session = self.get_session()
        try:
            if metric == "gold":
                rows = (
                    session.query(UserBalances, User.username)
                    .join(User, UserBalances.user_id == User.user_id)
                    .order_by(desc(UserBalances.gold))
                    .limit(limit)
                    .all()
                )
                return [
                    {"user_id": b.user_id, "username": u, "gold": b.gold or 0}
                    for b, u in rows
                ]
            elif metric == "total_battles":
                rows = (
                    session.query(User)
                    .order_by(desc(User.total_battles))
                    .limit(limit)
                    .all()
                )
                return [
                    {"user_id": u.user_id, "username": u.username, "total_battles": u.total_battles or 0}
                    for u in rows
                ]
            else:  # wins
                rows = (
                    session.query(UserBattleStats, User.username)
                    .join(User, UserBattleStats.user_id == User.user_id)
                    .order_by(desc(UserBattleStats.wins))
                    .limit(limit)
                    .all()
                )
                return [
                    {
                        "user_id": s.user_id,
                        "username": u,
                        "wins": s.wins or 0,
                        "losses": s.losses or 0,
                        "win_rate": round(
                            (s.wins or 0) / max(1, (s.wins or 0) + (s.losses or 0)), 2
                        ),
                    }
                    for s, u in rows
                ]
        except Exception as e:
            logger.error(f"[TMA] get_leaderboard error: {e}")
            return []
        finally:
            session.close()

    # --- Marketplace ---

    def get_marketplace_listings(self) -> List[dict]:
        """Return all active marketplace listings enriched with card info."""
        session = self.get_session()
        try:
            rows = (
                session.query(MarketplaceListings, Card)
                .join(Card, MarketplaceListings.card_id == Card.card_id)
                .filter(MarketplaceListings.is_active == True)
                .order_by(desc(MarketplaceListings.listed_at))
                .all()
            )
            return [
                {
                    "listing_id": ml.listing_id,
                    "seller_id":  ml.seller_id,
                    "card_id":    ml.card_id,
                    "price":      ml.price,
                    "listed_at":  ml.listed_at.isoformat() if ml.listed_at else None,
                    "card_name":  c.name,
                    "rarity":     c.rarity,
                    "image_url":  c.image_url,
                }
                for ml, c in rows
            ]
        except Exception as e:
            logger.error(f"[TMA] get_marketplace_listings error: {e}")
            return []
        finally:
            session.close()

    def create_marketplace_listing(self, user_id: str, card_id: str, price: int) -> dict:
        """List a card for sale. Card must be in user's collection."""
        session = self.get_session()
        try:
            uc = session.query(UserCard).filter_by(user_id=user_id, card_id=card_id).first()
            if not uc or uc.quantity < 1:
                return {"success": False, "error": "Card not in collection"}
            if price < 1:
                return {"success": False, "error": "Price must be at least 1 gold"}

            listing = MarketplaceListings(
                seller_id=user_id, card_id=card_id, price=price, is_active=True,
            )
            session.add(listing)
            # Remove one copy from collection
            if uc.quantity > 1:
                uc.quantity -= 1
            else:
                session.delete(uc)
            session.commit()
            return {"success": True, "listing_id": listing.listing_id}
        except Exception as e:
            session.rollback()
            logger.error(f"[TMA] create_marketplace_listing error: {e}")
            return {"success": False, "error": str(e)}
        finally:
            session.close()

    def purchase_marketplace_listing(self, buyer_id: str, listing_id: int) -> dict:
        """Buy a card from the marketplace. Transfers gold + card atomically."""
        session = self.get_session()
        try:
            listing = session.query(MarketplaceListings).filter_by(
                listing_id=listing_id, is_active=True
            ).first()
            if not listing:
                return {"success": False, "error": "Listing not found or already sold"}
            if listing.seller_id == buyer_id:
                return {"success": False, "error": "Cannot buy your own listing"}

            buyer_bal = session.query(UserBalances).filter_by(user_id=buyer_id).first()
            if not buyer_bal or (buyer_bal.gold or 0) < listing.price:
                return {"success": False, "error": "Insufficient gold"}

            # Transfer gold
            buyer_bal.gold = (buyer_bal.gold or 0) - listing.price
            seller_bal = session.query(UserBalances).filter_by(user_id=listing.seller_id).first()
            if seller_bal:
                seller_bal.gold = (seller_bal.gold or 0) + listing.price

            # Transfer card to buyer
            existing = session.query(UserCard).filter_by(
                user_id=buyer_id, card_id=listing.card_id
            ).first()
            if existing:
                existing.quantity += 1
            else:
                session.add(UserCard(
                    user_id=buyer_id, card_id=listing.card_id,
                    quantity=1, acquired_from="marketplace",
                    acquired_at=datetime.utcnow(),
                ))

            listing.is_active = False
            session.commit()
            return {"success": True, "card_id": listing.card_id, "price": listing.price}
        except Exception as e:
            session.rollback()
            logger.error(f"[TMA] purchase_marketplace_listing error: {e}")
            return {"success": False, "error": str(e)}
        finally:
            session.close()

    # --- P2P Trades ---

    @staticmethod
    def _tg_int(user_id: str) -> int:
        """Convert user_id string to integer stored in Trade (numeric offset format)."""
        try:
            # New format: "9000123456" (offset-based) — just parse as int
            # Legacy format: "tg_123456" — strip prefix (backward compat for old DB rows)
            return int(user_id[3:]) if user_id.startswith("tg_") else int(user_id)
        except (ValueError, TypeError):
            return 0

    def create_trade(
        self,
        initiator_id: str = None,
        partner_id: int = None,
        offered_cards: List[str] = None,
        requested_cards: List[str] = None,
        offered_gold: int = 0,
        requested_gold: int = 0,
        **kwargs,
    ):
        """Create a pending trade.

        Supports two call styles:
        - TMA: create_trade(initiator_id, partner_id, offered_cards, requested_cards, ...)
        - Discord legacy: create_trade(initiator_user_id=..., receiver_user_id=..., ...)
        """
        # Discord legacy signature compatibility
        if kwargs.get("initiator_user_id") is not None or kwargs.get("receiver_user_id") is not None:
            initiator_id = str(kwargs.get("initiator_user_id"))
            partner_id = int(kwargs.get("receiver_user_id"))
            offered_cards = kwargs.get("initiator_cards") or []
            requested_cards = kwargs.get("receiver_cards") or []
            offered_gold = int(kwargs.get("gold_from_initiator") or 0)
            requested_gold = int(kwargs.get("gold_from_receiver") or 0)
            discord_mode = True
        else:
            offered_cards = offered_cards or []
            requested_cards = requested_cards or []
            discord_mode = False

        """Create a pending P2P trade."""
        session = self.get_session()
        try:
            # Verify initiator owns offered cards
            for card_id in offered_cards:
                uc = session.query(UserCard).filter_by(
                    user_id=initiator_id, card_id=card_id
                ).first()
                if not uc:
                    return {"success": False, "error": f"You don't own card {card_id}"}

            # Resolve partner user_id (create placeholder if needed)
            partner_user_id = str(partner_id) if discord_mode else str(self._TG_OFFSET + partner_id)
            if str(initiator_id) == str(partner_user_id):
                return {"success": False, "error": "You can't trade with yourself"}
            if not session.query(User).filter_by(user_id=partner_user_id).first():
                session.add(User(user_id=partner_user_id, username=f"user_{partner_id}"))
                session.add(UserBalances(user_id=partner_user_id))
                session.flush()

            if int(offered_gold or 0) > 0:
                ib = session.query(UserBalances).filter_by(user_id=str(initiator_id)).first()
                have = int((ib.gold if ib else 0) or 0)
                if have < int(offered_gold or 0):
                    return {"success": False, "error": "You don't have enough gold"}

            trade = Trade(
                trade_id=Trade._next_trade_id(session),
                user_a=int(initiator_id) if discord_mode else self._tg_int(initiator_id),
                user_b=int(partner_id) if discord_mode else self._TG_OFFSET + partner_id,
                cards_a=offered_cards,
                cards_b=requested_cards,
                gold_a=offered_gold,
                gold_b=requested_gold,
                status="pending",
                expires_at=datetime.utcnow() + __import__("datetime").timedelta(minutes=10),
            )
            session.add(trade)
            session.commit()
            if discord_mode:
                return str(trade.id)
            return {"success": True, "trade_id": str(trade.id)}
        except Exception as e:
            session.rollback()
            logger.error(f"[TMA] create_trade error: {e}")
            if discord_mode:
                return None
            return {"success": False, "error": str(e)}
        finally:
            session.close()

    def complete_trade(self, trade_id: str) -> bool:
        """Legacy Discord trade finalize path (atomic swap + gold exchange)."""
        import uuid as _uuid

        def _find_pending_trade(session, raw_trade_id: str):
            """Find pending trade across legacy id formats safely."""
            tid = str(raw_trade_id)
            # Fast path for UUID-shaped ids.
            try:
                return session.query(Trade).filter_by(id=_uuid.UUID(tid), status="pending").first()
            except (ValueError, TypeError, AttributeError):
                pass
            # Compatibility path: scan pending rows and compare normalized string ids.
            pending_rows = (
                session.query(Trade)
                .filter_by(status="pending")
                .order_by(desc(Trade.created_at))
                .limit(500)
                .all()
            )
            for row in pending_rows:
                if str(row.id) == tid:
                    return row
            return None

        session = self.get_session()
        try:
            trade = _find_pending_trade(session, trade_id)
            if not trade:
                return False
            if trade.is_expired():
                trade.status = "expired"
                session.commit()
                return False

            initiator_id = str(trade.user_a)
            receiver_id = str(trade.user_b)

            # Verify ownership before mutating
            for card_id in (trade.cards_a or []):
                if not session.query(UserCard).filter_by(user_id=initiator_id, card_id=card_id).first():
                    return False
            for card_id in (trade.cards_b or []):
                if not session.query(UserCard).filter_by(user_id=receiver_id, card_id=card_id).first():
                    return False

            # Swap A -> B
            for card_id in (trade.cards_a or []):
                uc = session.query(UserCard).filter_by(user_id=initiator_id, card_id=card_id).first()
                uc.quantity = (uc.quantity or 1) - 1
                if uc.quantity <= 0:
                    session.delete(uc)
                ex = session.query(UserCard).filter_by(user_id=receiver_id, card_id=card_id).first()
                if ex:
                    ex.quantity = (ex.quantity or 0) + 1
                else:
                    session.add(UserCard(
                        user_id=receiver_id, card_id=card_id, quantity=1,
                        acquired_from="trade", acquired_at=datetime.utcnow(),
                    ))

            # Swap B -> A
            for card_id in (trade.cards_b or []):
                uc = session.query(UserCard).filter_by(user_id=receiver_id, card_id=card_id).first()
                uc.quantity = (uc.quantity or 1) - 1
                if uc.quantity <= 0:
                    session.delete(uc)
                ex = session.query(UserCard).filter_by(user_id=initiator_id, card_id=card_id).first()
                if ex:
                    ex.quantity = (ex.quantity or 0) + 1
                else:
                    session.add(UserCard(
                        user_id=initiator_id, card_id=card_id, quantity=1,
                        acquired_from="trade", acquired_at=datetime.utcnow(),
                    ))

            if trade.gold_a or trade.gold_b:
                self.update_user_economy(initiator_id, gold_change=-(trade.gold_a or 0) + (trade.gold_b or 0))
                self.update_user_economy(receiver_id,  gold_change=-(trade.gold_b or 0) + (trade.gold_a or 0))

            trade.status = "completed"
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"[DB] complete_trade error: {e}")
            return False
        finally:
            session.close()

    def execute_trade_direct(
        self,
        initiator_user_id: str,
        receiver_user_id: str,
        initiator_cards: List[str],
        receiver_cards: List[str],
        gold_from_initiator: int = 0,
        gold_from_receiver: int = 0,
    ) -> dict:
        """Direct Discord trade path that does not depend on trade-record creation."""
        session = self.get_session()
        try:
            initiator_id = str(initiator_user_id)
            receiver_id = str(receiver_user_id)
            cards_a = initiator_cards or []
            cards_b = receiver_cards or []
            gold_a = int(gold_from_initiator or 0)
            gold_b = int(gold_from_receiver or 0)

            # Verify ownership before any mutations.
            for card_id in cards_a:
                if not session.query(UserCard).filter_by(user_id=initiator_id, card_id=card_id).first():
                    return {"success": False, "error": f"Initiator no longer owns {card_id}"}
            for card_id in cards_b:
                if not session.query(UserCard).filter_by(user_id=receiver_id, card_id=card_id).first():
                    return {"success": False, "error": f"Receiver no longer owns {card_id}"}

            bal_a = session.query(UserBalances).filter_by(user_id=initiator_id).first()
            bal_b = session.query(UserBalances).filter_by(user_id=receiver_id).first()
            if not bal_a:
                bal_a = UserBalances(user_id=initiator_id)
                session.add(bal_a)
                session.flush()
            if not bal_b:
                bal_b = UserBalances(user_id=receiver_id)
                session.add(bal_b)
                session.flush()

            if gold_a > (bal_a.gold or 0):
                return {"success": False, "error": "Initiator no longer has enough gold"}
            if gold_b > (bal_b.gold or 0):
                return {"success": False, "error": "Receiver no longer has enough gold"}

            # Swap A -> B
            for card_id in cards_a:
                uc = session.query(UserCard).filter_by(user_id=initiator_id, card_id=card_id).first()
                uc.quantity = (uc.quantity or 1) - 1
                if uc.quantity <= 0:
                    session.delete(uc)
                ex = session.query(UserCard).filter_by(user_id=receiver_id, card_id=card_id).first()
                if ex:
                    ex.quantity = (ex.quantity or 0) + 1
                else:
                    session.add(UserCard(
                        user_id=receiver_id,
                        card_id=card_id,
                        quantity=1,
                        acquired_from="trade",
                        acquired_at=datetime.utcnow(),
                    ))

            # Swap B -> A
            for card_id in cards_b:
                uc = session.query(UserCard).filter_by(user_id=receiver_id, card_id=card_id).first()
                uc.quantity = (uc.quantity or 1) - 1
                if uc.quantity <= 0:
                    session.delete(uc)
                ex = session.query(UserCard).filter_by(user_id=initiator_id, card_id=card_id).first()
                if ex:
                    ex.quantity = (ex.quantity or 0) + 1
                else:
                    session.add(UserCard(
                        user_id=initiator_id,
                        card_id=card_id,
                        quantity=1,
                        acquired_from="trade",
                        acquired_at=datetime.utcnow(),
                    ))

            # Gold swap (same transaction for atomicity)
            bal_a.gold = max(0, (bal_a.gold or 0) - gold_a + gold_b)
            bal_b.gold = max(0, (bal_b.gold or 0) - gold_b + gold_a)

            session.commit()
            # Best-effort history logging in a separate transaction so legacy
            # schema issues do not fail the completed trade swap.
            try:
                log_sess = self.get_session()
                try:
                    log_sess.add(TradeHistory(
                        user_id_1=initiator_id,
                        user_id_2=receiver_id,
                        card_id_1=cards_a[0] if cards_a else "gold_only",
                        card_id_2=cards_b[0] if cards_b else "gold_only",
                        status="completed",
                    ))
                    log_sess.commit()
                except Exception as history_err:
                    log_sess.rollback()
                    logger.warning(f"[DB] trade completed but history insert failed: {history_err}")
                finally:
                    log_sess.close()
            except Exception:
                pass

            return {"success": True}
        except Exception as e:
            session.rollback()
            logger.error(f"[DB] execute_trade_direct error: {e}")
            return {"success": False, "error": str(e)}
        finally:
            session.close()

    def accept_trade(self, trade_id: str, user_id: str) -> dict:
        """Accept a trade: swap cards and gold atomically."""
        import uuid as _uuid

        def _find_pending_trade(session, raw_trade_id: str):
            tid = str(raw_trade_id)
            try:
                return session.query(Trade).filter_by(id=_uuid.UUID(tid), status="pending").first()
            except (ValueError, TypeError, AttributeError):
                pass
            pending_rows = (
                session.query(Trade)
                .filter_by(status="pending")
                .order_by(desc(Trade.created_at))
                .limit(500)
                .all()
            )
            for row in pending_rows:
                if str(row.id) == tid:
                    return row
            return None

        session = self.get_session()
        try:
            trade = _find_pending_trade(session, trade_id)
            if not trade:
                return {"success": False, "error": "Trade not found or already closed"}

            partner_tg_id = self._tg_int(user_id)
            if trade.user_b != partner_tg_id:
                return {"success": False, "error": "Not the intended recipient"}
            if trade.is_expired():
                trade.status = "expired"
                session.commit()
                return {"success": False, "error": "Trade has expired"}

            initiator_id = str(trade.user_a)
            receiver_id = str(trade.user_b)
            bal_a = session.query(UserBalances).filter_by(user_id=initiator_id).first()
            bal_b = session.query(UserBalances).filter_by(user_id=receiver_id).first()
            if not bal_a:
                bal_a = UserBalances(user_id=initiator_id)
                session.add(bal_a)
                session.flush()
            if not bal_b:
                bal_b = UserBalances(user_id=receiver_id)
                session.add(bal_b)
                session.flush()
            if int(trade.gold_a or 0) > int(bal_a.gold or 0):
                return {"success": False, "error": "Initiator no longer has enough gold"}
            if int(trade.gold_b or 0) > int(bal_b.gold or 0):
                return {"success": False, "error": "You no longer have enough gold"}

            # Swap cards A → B
            for card_id in (trade.cards_a or []):
                uc = session.query(UserCard).filter_by(user_id=initiator_id, card_id=card_id).first()
                if not uc:
                    return {"success": False, "error": f"Initiator no longer owns {card_id}"}
                uc.quantity -= 1
                if uc.quantity <= 0:
                    session.delete(uc)
                ex = session.query(UserCard).filter_by(user_id=receiver_id, card_id=card_id).first()
                if ex:
                    ex.quantity += 1
                else:
                    session.add(UserCard(user_id=receiver_id, card_id=card_id, quantity=1,
                                         acquired_from="trade", acquired_at=datetime.utcnow()))

            # Swap cards B → A
            for card_id in (trade.cards_b or []):
                uc = session.query(UserCard).filter_by(user_id=receiver_id, card_id=card_id).first()
                if not uc:
                    return {"success": False, "error": f"You no longer own {card_id}"}
                uc.quantity -= 1
                if uc.quantity <= 0:
                    session.delete(uc)
                ex = session.query(UserCard).filter_by(user_id=initiator_id, card_id=card_id).first()
                if ex:
                    ex.quantity += 1
                else:
                    session.add(UserCard(user_id=initiator_id, card_id=card_id, quantity=1,
                                         acquired_from="trade", acquired_at=datetime.utcnow()))

            # Swap gold
            if trade.gold_a or trade.gold_b:
                bal_a.gold = max(0, int(bal_a.gold or 0) - int(trade.gold_a or 0) + int(trade.gold_b or 0))
                bal_b.gold = max(0, int(bal_b.gold or 0) - int(trade.gold_b or 0) + int(trade.gold_a or 0))

            trade.status = "completed"
            session.commit()
            return {"success": True, "trade_id": trade_id}
        except Exception as e:
            session.rollback()
            logger.error(f"[TMA] accept_trade error: {e}")
            return {"success": False, "error": str(e)}
        finally:
            session.close()

    def cancel_trade(self, trade_id: str, user_id: str = None, reason: str = None) -> dict:
        """Cancel a pending trade (only initiator or recipient can cancel)."""
        import uuid as _uuid

        def _find_pending_trade(session, raw_trade_id: str):
            tid = str(raw_trade_id)
            try:
                return session.query(Trade).filter_by(id=_uuid.UUID(tid), status="pending").first()
            except (ValueError, TypeError, AttributeError):
                pass
            pending_rows = (
                session.query(Trade)
                .filter_by(status="pending")
                .order_by(desc(Trade.created_at))
                .limit(500)
                .all()
            )
            for row in pending_rows:
                if str(row.id) == tid:
                    return row
            return None

        session = self.get_session()
        try:
            trade = _find_pending_trade(session, trade_id)
            if not trade:
                return {"success": False, "error": "Trade not found or already closed"}
            if user_id is not None:
                tg_id = self._tg_int(user_id)
                if tg_id not in (trade.user_a, trade.user_b) and str(user_id) not in (str(trade.user_a), str(trade.user_b)):
                    return {"success": False, "error": "Not a participant in this trade"}
            trade.status = "cancelled"
            session.commit()
            return {"success": True}
        except Exception as e:
            session.rollback()
            logger.error(f"[TMA] cancel_trade error: {e}")
            return {"success": False, "error": str(e)}
        finally:
            session.close()

    def get_user_trades(self, user_id: str) -> List[dict]:
        """Return all trades the user is involved in."""
        from sqlalchemy import or_
        session = self.get_session()
        try:
            tg_id = self._tg_int(user_id)
            trades = (
                session.query(Trade)
                .filter(or_(Trade.user_a == tg_id, Trade.user_b == tg_id))
                .order_by(desc(Trade.created_at))
                .limit(50)
                .all()
            )
            # Enrich with usernames for better Telegram UI rendering.
            user_ids = set()
            for t in trades:
                user_ids.add(str(t.user_a))
                user_ids.add(str(t.user_b))
            users = (
                session.query(User)
                .filter(User.user_id.in_(list(user_ids)))
                .all()
            ) if user_ids else []
            usernames = {str(u.user_id): (u.username or f"user_{u.user_id}") for u in users}

            out = []
            for t in trades:
                d = t.to_dict()
                user_a_id = str(t.user_a)
                user_b_id = str(t.user_b)
                d["user_a_username"] = usernames.get(user_a_id, f"user_{user_a_id}")
                d["user_b_username"] = usernames.get(user_b_id, f"user_{user_b_id}")

                partner_id = user_b_id if t.user_a == tg_id else user_a_id
                d["partner_user_id"] = partner_id
                d["partner_username"] = usernames.get(partner_id, f"user_{partner_id}")
                try:
                    pid_int = int(partner_id)
                    if pid_int >= self._TG_OFFSET:
                        d["partner_telegram_id"] = pid_int - self._TG_OFFSET
                except Exception:
                    pass

                out.append(d)
            return out
        except Exception as e:
            logger.error(f"[TMA] get_user_trades error: {e}")
            return []
        finally:
            session.close()

    # --- Dust & Crafting ---

    def get_dust_balance(self, user_id: str) -> int:
        """Return user's current dust balance."""
        session = self.get_session()
        try:
            b = session.query(UserBalances).filter_by(user_id=user_id).first()
            return b.dust or 0 if b else 0
        finally:
            session.close()

    def dust_cards(self, user_id: str, card_ids: List[str]) -> Tuple[bool, str]:
        """Convert cards to dust. Removes one copy of each card and awards dust."""
        session = self.get_session()
        try:
            total_dust = 0
            for card_id in card_ids:
                uc = session.query(UserCard).filter_by(user_id=user_id, card_id=card_id).first()
                if not uc:
                    session.rollback()
                    return False, f"You don't own card {card_id}"
                card = session.query(Card).filter_by(card_id=card_id).first()
                rarity = (card.rarity or "common").lower() if card else "common"
                total_dust += self._DUST_VALUE.get(rarity, 10)
                uc.quantity -= 1
                if uc.quantity <= 0:
                    session.delete(uc)

            b = session.query(UserBalances).filter_by(user_id=user_id).first()
            if not b:
                b = UserBalances(user_id=user_id)
                session.add(b)
                session.flush()
            b.dust = (b.dust or 0) + total_dust
            session.commit()
            return True, f"Converted {len(card_ids)} card(s) into {total_dust} dust"
        except Exception as e:
            session.rollback()
            logger.error(f"[TMA] dust_cards error: {e}")
            return False, str(e)
        finally:
            session.close()

    def craft_card(self, user_id: str, card_id: str) -> Tuple[bool, str]:
        """Craft a card using dust. Deducts craft cost and adds card to collection."""
        session = self.get_session()
        try:
            card = session.query(Card).filter_by(card_id=card_id).first()
            if not card:
                return False, "Card not found"
            rarity = (card.rarity or "common").lower()
            cost = self._CRAFT_COST.get(rarity, 40)

            b = session.query(UserBalances).filter_by(user_id=user_id).first()
            if not b or (b.dust or 0) < cost:
                have = b.dust or 0 if b else 0
                return False, f"Need {cost} dust but you only have {have}"

            b.dust = (b.dust or 0) - cost
            existing = session.query(UserCard).filter_by(user_id=user_id, card_id=card_id).first()
            if existing:
                existing.quantity += 1
            else:
                session.add(UserCard(
                    user_id=user_id, card_id=card_id, quantity=1,
                    acquired_from="craft", acquired_at=datetime.utcnow(),
                ))
            session.commit()
            return True, f"Crafted {card.name} for {cost} dust"
        except Exception as e:
            session.rollback()
            logger.error(f"[TMA] craft_card error: {e}")
            return False, str(e)
        finally:
            session.close()

    def close(self):
        """Closes the database connection."""
        if self._engine:
            self._engine.dispose()
            self._engine = None
            self._Session = None
            Database._instance = None # Reset the singleton instance

def get_db() -> Database:
    """Returns a singleton instance of the Database class."""
    from config import settings
    return Database(settings.DATABASE_URL)

# Example Usage (for testing or direct script execution)
if __name__ == "__main__":
    # Example for SQLite
    db_sqlite = Database("sqlite:///./test.db")
    session_sqlite = db_sqlite.get_session()
    # Test operations...
    session_sqlite.close()
    db_sqlite.close()

    # Example for PostgreSQL (replace with your actual connection string)
    # db_pg = Database("postgresql://user:password@host:port/dbname")
    # session_pg = db_pg.get_session()
    # Test operations...
    # session_pg.close()
    # db_pg.close()


# Alias for backward compatibility
DatabaseManager = Database


class _PgConnectionWrapper:
    """Wraps a raw psycopg2 connection so it can be used as a context manager
    (commits on success, rolls back on error, closes on exit)."""

    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return self._conn.cursor()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self._conn.rollback()
        else:
            self._conn.commit()
        self._conn.close()
        return False

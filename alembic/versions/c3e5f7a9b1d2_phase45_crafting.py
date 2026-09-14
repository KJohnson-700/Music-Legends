"""Phase 4.5 — craft_events table; consumed_at on stars_orders

Revision ID: c3e5f7a9b1d2
Revises: a9c2d4e6f8b1
Create Date: 2026-09-14

Guarded: DatabaseManager also creates/extends these on first boot.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = 'c3e5f7a9b1d2'
down_revision: Union[str, Sequence[str], None] = 'a9c2d4e6f8b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return inspect(op.get_bind()).has_table(name)


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if not _has_table("craft_events"):
        op.create_table(
            "craft_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.String(), nullable=False, index=True),
            sa.Column("input_card_ids", sa.Text(), nullable=False),
            sa.Column("input_rarity", sa.String(), nullable=False),
            sa.Column("target_rarity", sa.String(), nullable=False, index=True),
            sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("output_card_id", sa.String(), nullable=True),
            sa.Column("output_family", sa.String(), nullable=True),
            sa.Column("inherited_family", sa.String(), nullable=True),
            sa.Column("boost_order_id", sa.String(), nullable=True),
            sa.Column("success_pct", sa.Integer(), nullable=True),
            sa.Column("roll_value", sa.Float(), nullable=True),
            sa.Column("seed", sa.BigInteger(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
    if _has_table("stars_orders") and not _has_column("stars_orders", "consumed_at"):
        op.add_column("stars_orders", sa.Column("consumed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    if _has_table("stars_orders") and _has_column("stars_orders", "consumed_at"):
        op.drop_column("stars_orders", "consumed_at")
    if _has_table("craft_events"):
        op.drop_table("craft_events")

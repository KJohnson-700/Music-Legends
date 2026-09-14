"""Phase 5 — Telegram Stars orders + rail columns on revenue_events

Revision ID: a9c2d4e6f8b1
Revises: f4a7c1b2e9d3
Create Date: 2026-09-13

DatabaseManager.init_database() also creates the table / adds the columns
automatically (create_all + ADD COLUMN loop), so this file is guarded to be safe
to run either before or after that.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = 'a9c2d4e6f8b1'
down_revision: Union[str, Sequence[str], None] = 'f4a7c1b2e9d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return inspect(op.get_bind()).has_table(name)


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if not _has_table("stars_orders"):
        op.create_table(
            "stars_orders",
            sa.Column("order_id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), nullable=False, index=True),
            sa.Column("telegram_id", sa.BigInteger(), nullable=True),
            sa.Column("product_type", sa.String(), nullable=False),
            sa.Column("product_ref", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=True),
            sa.Column("stars_amount", sa.Integer(), nullable=False),
            sa.Column("usd_cents_ref", sa.Integer(), nullable=True),
            sa.Column("host_token", sa.String(), nullable=True),
            sa.Column("host_share_bps", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="pending"),
            sa.Column("invoice_link", sa.Text(), nullable=True),
            sa.Column("telegram_payment_charge_id", sa.String(), nullable=True, unique=True),
            sa.Column("provider_payment_charge_id", sa.String(), nullable=True),
            sa.Column("cards_json", sa.Text(), nullable=True),
            sa.Column("result_json", sa.Text(), nullable=True),
            sa.Column("error", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("paid_at", sa.DateTime(), nullable=True),
            sa.Column("fulfilled_at", sa.DateTime(), nullable=True),
        )
    for col in (
        sa.Column("rail", sa.String(), nullable=True, server_default="stripe"),
        sa.Column("gross_stars", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("net_cents", sa.Integer(), nullable=True, server_default="0"),
    ):
        if not _has_column("revenue_events", col.name):
            op.add_column("revenue_events", col)


def downgrade() -> None:
    for col in ("net_cents", "gross_stars", "rail"):
        if _has_column("revenue_events", col):
            op.drop_column("revenue_events", col)
    if _has_table("stars_orders"):
        op.drop_table("stars_orders")

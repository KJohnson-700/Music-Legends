"""Phase 4 — genre family + momentum columns on cards; scout state on pending TMA battles

Revision ID: f4a7c1b2e9d3
Revises: d74802d31b7e
Create Date: 2026-09-13

Note: DatabaseManager.init_database() also adds missing columns automatically
(ADD COLUMN loop over Base.metadata), so this migration is idempotent-safe:
each statement is guarded by a column-existence check.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = 'f4a7c1b2e9d3'
down_revision: Union[str, Sequence[str], None] = 'd74802d31b7e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {c["name"] for c in inspect(bind).get_columns(table)}


def _add(table: str, column: sa.Column) -> None:
    if not _has_column(table, column.name):
        op.add_column(table, column)


def upgrade() -> None:
    _add("cards", sa.Column("genre_family", sa.String(), nullable=True, server_default="NEUTRAL"))
    _add("cards", sa.Column("genre_source", sa.String(), nullable=True))
    _add("cards", sa.Column("view_count", sa.BigInteger(), nullable=True))
    _add("cards", sa.Column("view_delta", sa.BigInteger(), nullable=True, server_default="0"))
    _add("cards", sa.Column("momentum_hot", sa.Boolean(), nullable=True, server_default=sa.false()))
    _add("cards", sa.Column("views_checked_at", sa.DateTime(), nullable=True))
    _add("pending_tma_battles", sa.Column("opponent_scout_json", sa.Text(), nullable=True))
    op.execute("UPDATE cards SET genre_family = 'NEUTRAL' WHERE genre_family IS NULL")
    op.execute("UPDATE cards SET view_delta = 0 WHERE view_delta IS NULL")


def downgrade() -> None:
    for col in ("views_checked_at", "momentum_hot", "view_delta", "view_count", "genre_source", "genre_family"):
        if _has_column("cards", col):
            op.drop_column("cards", col)
    if _has_column("pending_tma_battles", "opponent_scout_json"):
        op.drop_column("pending_tma_battles", "opponent_scout_json")

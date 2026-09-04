"""Add users.token_generation for refresh-token reuse revocation.

Every access and refresh token carries the generation it was minted under.
Bumping the counter refuses all of them at once, which is what happens when a
refresh token is presented twice — the sign that it leaked, and that the copy
the thief minted has to die alongside the one they stole.

Revision ID: 3b2953903c0a
Revises: 789a21f30bb9
Create Date: 2026-09-04 18:22:23.731396
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3b2953903c0a"
down_revision: str | None = "789a21f30bb9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default backfills rows that already exist: a NOT NULL column added
    # without one fails outright on a table with any data in it. The model keeps
    # a Python-side default of 0, so new rows agree either way.
    op.add_column(
        "users",
        sa.Column("token_generation", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "token_generation")

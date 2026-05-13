"""add api_tokens table for long-lived personal access tokens

Schema:
   id            UUID PK
   user_id       UUID FK → users, NOT NULL
   name          str(120)
   prefix        str(16)  UNIQUE
   token_hash    str(120)  — bcrypt(full_token)
   last_used_at  timestamptz?
   expires_at    timestamptz?
   revoked_at    timestamptz?  — soft revoke; auth filters on this
   created_at    timestamptz  server_default now()

Index: (user_id, revoked_at) — list-my-tokens query is the hot path; the
partial filter on revoked NULL/non-NULL benefits from this.

Revision ID: f5c6d7e8f9a0
Revises: f4b5c6d7e8f9
Create Date: 2026-05-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f5c6d7e8f9a0"
down_revision: Union[str, Sequence[str], None] = "f4b5c6d7e8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(120), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_api_tokens_user_id"),
        sa.UniqueConstraint("prefix", name="uq_api_tokens_prefix"),
    )
    op.create_index("ix_api_tokens_user_id_active", "api_tokens", ["user_id", "revoked_at"])


def downgrade() -> None:
    op.drop_index("ix_api_tokens_user_id_active", table_name="api_tokens")
    op.drop_table("api_tokens")

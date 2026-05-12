"""add UserRole enum and user.role column

N1.A — Introduce a three-tier organization role (editor / producer / admin)
on top of the existing binary ``is_superadmin`` flag. Both fields stay in
the table; ``role`` is the new source of truth, ``is_superadmin`` is kept
in lockstep for backwards compatibility with the pre-N1.A code paths
(admin router, setup wizard, signup flow). The role endpoint updates both.

Migration logic:
  * Create the ``userrole`` enum type if absent.
  * Add ``users.role`` defaulting to ``editor``.
  * Backfill: every existing ``is_superadmin = TRUE`` user becomes ``admin``.
    Everyone else stays at the column default ``editor``. The user can then
    promote individuals (Tania, Lera) to producer via the admin UI.

Postgres 12+ supports ``CREATE TYPE`` and ``ALTER TABLE ... ADD COLUMN`` in
a single transaction, so this runs as a normal Alembic upgrade.

Revision ID: e2f3a4b5c6d7
Revises: d1f2c3a4b5e6
Create Date: 2026-05-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e2f3a4b5c6d7'
down_revision: Union[str, Sequence[str], None] = 'd1f2c3a4b5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create the enum type. IF NOT EXISTS guards re-runs on a host that
    # already has the type from a manual psql session.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'userrole') THEN
                CREATE TYPE userrole AS ENUM ('editor', 'producer', 'admin');
            END IF;
        END$$;
    """)

    # Add the column with a sane default so existing rows have a value.
    op.add_column(
        'users',
        sa.Column(
            'role',
            sa.Enum('editor', 'producer', 'admin', name='userrole', create_type=False),
            nullable=False,
            server_default='editor',
        ),
    )

    # Backfill existing admins.
    op.execute("UPDATE users SET role = 'admin' WHERE is_superadmin = TRUE")


def downgrade() -> None:
    op.drop_column('users', 'role')
    # Leave the enum type in place; harmless if it lingers and re-running
    # upgrade is idempotent. Dropping a Postgres enum type that any column
    # references is fiddly and offers no real benefit.

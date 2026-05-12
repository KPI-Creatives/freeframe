"""add Asset.phase, reviewer_id, priority, custom_fields and phase tracking

N1.B — extends Asset with the producer-managed workflow state and the
Send-to-client / Mark-delivered tracking columns. Two new enums:

  * ``assetphase``    — internal | client | delivered
  * ``assetpriority`` — P0 | P1 | P2

Plus these columns on ``assets``:

  * ``reviewer_id``                — FK to users
  * ``priority``                   — assetpriority NULL
  * ``phase``                      — assetphase NOT NULL DEFAULT 'internal'
  * ``phase_client_at``            — timestamptz NULL
  * ``phase_delivered_at``         — timestamptz NULL
  * ``client_baseline_version_id`` — FK to asset_versions
  * ``delivered_version_id``       — FK to asset_versions
  * ``block_reason``               — text
  * ``custom_fields``              — jsonb DEFAULT '{}'

Existing rows default to ``phase='internal'``. That's the right semantic —
any pre-N1.B asset has not been formally Send-to-client'ed; its versions and
comments are unfiltered (which is also the existing behaviour, so no UI
regression).

Idempotent enum creation guards re-runs.

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-05-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3a4b5c6d7e8'
down_revision: Union[str, Sequence[str], None] = 'e2f3a4b5c6d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── New enum types ───────────────────────────────────────────────────
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'assetphase') THEN
                CREATE TYPE assetphase AS ENUM ('internal', 'client', 'delivered');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'assetpriority') THEN
                CREATE TYPE assetpriority AS ENUM ('P0', 'P1', 'P2');
            END IF;
        END$$;
    """)

    # ── New Asset columns ────────────────────────────────────────────────
    op.add_column('assets', sa.Column(
        'reviewer_id', sa.UUID(), nullable=True,
    ))
    op.create_foreign_key(
        'fk_assets_reviewer_id_users', 'assets', 'users',
        ['reviewer_id'], ['id'],
    )
    op.create_index('ix_assets_reviewer_id', 'assets', ['reviewer_id'])

    op.add_column('assets', sa.Column(
        'priority',
        sa.Enum('P0', 'P1', 'P2', name='assetpriority', create_type=False),
        nullable=True,
    ))
    op.create_index('ix_assets_priority', 'assets', ['priority'])

    op.add_column('assets', sa.Column(
        'phase',
        sa.Enum('internal', 'client', 'delivered', name='assetphase', create_type=False),
        nullable=False,
        server_default='internal',
    ))
    op.create_index('ix_assets_phase', 'assets', ['phase'])

    op.add_column('assets', sa.Column('phase_client_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('assets', sa.Column('phase_delivered_at', sa.DateTime(timezone=True), nullable=True))

    op.add_column('assets', sa.Column('client_baseline_version_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        'fk_assets_client_baseline_version', 'assets', 'asset_versions',
        ['client_baseline_version_id'], ['id'],
    )

    op.add_column('assets', sa.Column('delivered_version_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        'fk_assets_delivered_version', 'assets', 'asset_versions',
        ['delivered_version_id'], ['id'],
    )

    op.add_column('assets', sa.Column('block_reason', sa.String(500), nullable=True))
    op.add_column('assets', sa.Column(
        'custom_fields', sa.dialects.postgresql.JSONB(), nullable=True,
        server_default=sa.text("'{}'::jsonb"),
    ))


def downgrade() -> None:
    op.drop_column('assets', 'custom_fields')
    op.drop_column('assets', 'block_reason')
    op.drop_constraint('fk_assets_delivered_version', 'assets', type_='foreignkey')
    op.drop_column('assets', 'delivered_version_id')
    op.drop_constraint('fk_assets_client_baseline_version', 'assets', type_='foreignkey')
    op.drop_column('assets', 'client_baseline_version_id')
    op.drop_column('assets', 'phase_delivered_at')
    op.drop_column('assets', 'phase_client_at')
    op.drop_index('ix_assets_phase', table_name='assets')
    op.drop_column('assets', 'phase')
    op.drop_index('ix_assets_priority', table_name='assets')
    op.drop_column('assets', 'priority')
    op.drop_index('ix_assets_reviewer_id', table_name='assets')
    op.drop_constraint('fk_assets_reviewer_id_users', 'assets', type_='foreignkey')
    op.drop_column('assets', 'reviewer_id')
    # Leave enum types — same rationale as the UserRole migration.

"""add time tracking columns

Adds the data layer for the "log editor's time per version" feature:

  * ``timetrackingdefault`` enum — ``on | off | inherit``
  * ``folders.time_tracking_default`` — folder-level policy (default ``inherit``)
  * ``assets.track_time`` — boolean, resolved from folder chain at asset
    creation; subsequent overrides happen via PATCH /assets/:id
  * ``assets.total_minutes_spent`` — denormalised int cache, updated in the
    same transaction as ``asset_versions.minutes_spent`` so grid views read
    one column without aggregations
  * ``asset_versions.minutes_spent`` — int NULL; ``NULL`` means the editor
    skipped or hasn't been asked yet. Constrained to multiples of 5 so the
    UI snap doesn't end up writing weird timestamps from a custom field.

Defaults for existing rows: ``track_time = false`` and
``total_minutes_spent = 0``. That's the right semantic — pre-feature assets
weren't tracked, so the new column simply confirms what was always true.
The folder default is ``inherit`` for every existing folder; we don't try to
pre-guess which existing folders should be ``on`` (e.g. by name match
``%review%``) because there's no live data in the FreeFrame pilot yet —
we're shipping into an empty workspace.

Idempotent enum creation guards re-runs. Constraint enforces the
``% 5 == 0`` rule at the database layer so it survives anyone bypassing
the API.

Revision ID: f4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-05-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f4b5c6d7e8f9"
down_revision: Union[str, Sequence[str], None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── New enum type ───────────────────────────────────────────────────
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_type WHERE typname = 'timetrackingdefault'
            ) THEN
                CREATE TYPE timetrackingdefault AS ENUM ('on', 'off', 'inherit');
            END IF;
        END$$;
        """
    )

    # ── Folder column ───────────────────────────────────────────────────
    op.add_column(
        "folders",
        sa.Column(
            "time_tracking_default",
            sa.Enum(
                "on",
                "off",
                "inherit",
                name="timetrackingdefault",
                create_type=False,
            ),
            nullable=False,
            server_default="inherit",
        ),
    )

    # ── Asset columns ───────────────────────────────────────────────────
    op.add_column(
        "assets",
        sa.Column(
            "track_time",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "assets",
        sa.Column(
            "total_minutes_spent",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    # ── AssetVersion column + check constraint ──────────────────────────
    op.add_column(
        "asset_versions",
        sa.Column("minutes_spent", sa.Integer(), nullable=True),
    )
    # Constraint: NULL is fine (skipped); non-NULL must be >= 0 AND multiple of 5.
    # The 5-min step covers the smallest preset (5m) and a custom input that
    # snaps. Multiples of 5 means 15m, 30m, 45m all pass; arbitrary 7m fails.
    op.create_check_constraint(
        "ck_asset_versions_minutes_spent_5min_step",
        "asset_versions",
        "minutes_spent IS NULL OR (minutes_spent >= 0 AND minutes_spent % 5 = 0)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_asset_versions_minutes_spent_5min_step",
        "asset_versions",
        type_="check",
    )
    op.drop_column("asset_versions", "minutes_spent")
    op.drop_column("assets", "total_minutes_spent")
    op.drop_column("assets", "track_time")
    op.drop_column("folders", "time_tracking_default")
    # Leave the enum type — same rationale as N1.A/N1.B migrations: a future
    # forward migration may want to reuse it.

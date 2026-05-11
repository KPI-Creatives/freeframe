"""add 'document' to assettype and filetype enums

Adds the ``document`` value to the Postgres ``assettype`` and ``filetype``
enums so we can store Markdown (and, later, HTML/other text formats) as
first-class asset rows alongside video/audio/image.

Documents bypass the Celery transcoding pipeline entirely — the upload
router marks them as ``ready`` immediately and the original bytes stay in
S3 verbatim. No new columns are required.

Postgres 15 supports ``ALTER TYPE ... ADD VALUE`` inside a transaction
block, so this can run as a normal Alembic upgrade.

Revision ID: d1f2c3a4b5e6
Revises: 8ca3dffea55f
Create Date: 2026-05-11
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd1f2c3a4b5e6'
down_revision: Union[str, Sequence[str], None] = '8ca3dffea55f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IF NOT EXISTS guards against being re-applied (e.g. on a host that
    # was already manually patched). Postgres 12+ supports this clause.
    op.execute("ALTER TYPE assettype ADD VALUE IF NOT EXISTS 'document'")
    op.execute("ALTER TYPE filetype ADD VALUE IF NOT EXISTS 'document'")


def downgrade() -> None:
    # Postgres does not support removing an enum value. The standard
    # workaround (create a new type, rebuild columns, drop the old type)
    # is risky on a populated table and offers no real benefit — the
    # 'document' value is harmless if left in place.
    pass

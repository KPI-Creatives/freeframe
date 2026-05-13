"""ApiToken — long-lived personal access tokens for scripted Frame access.

Standard PAT design (think GitHub fine-grained PATs or Stripe API keys):

  * The full token is shown ONCE at creation time and is never recoverable.
  * Only a bcrypt hash of the secret half is stored at rest.
  * Each token belongs to a user; requests authenticate as that user with
    the user's full role and project memberships. No scopes in v1 — the
    token can do anything the owner could do via the UI. Scopes are
    follow-up work.
  * Tokens can be revoked (soft) at any time via DELETE /me/api-tokens/:id;
    `revoked_at` is the sentinel column. Revoked tokens stop authenticating
    immediately because the auth path filters on it.

Public format:  ``ft_<prefix-8>_<secret-32+>``
   * ``ft``  — namespace, lets a future grep distinguish from random JWTs
   * ``<prefix>`` — short URL-safe identifier shown in the UI list view
   * ``<secret>`` — URL-safe random, never stored in plain
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import String, DateTime, ForeignKey, func, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

try:
    from ..database import Base
except ImportError:
    from database import Base


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    # Human-readable label set at creation ("kpi-sync", "migrator", "cli").
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Short URL-safe identifier of the token (first 8 chars after the ``ft_``
    # namespace). Unique across all tokens. Used to look up the row before
    # bcrypt-verifying the secret half — keeps the auth path O(1) without
    # comparing every hash in the table.
    prefix: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    # bcrypt(full_token). The full token (including prefix) is hashed, not
    # just the secret, so a leaked prefix alone cannot help attackers.
    token_hash: Mapped[str] = mapped_column(String(120), nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Optional explicit expiry. Null = never expires. Most tokens used in v1
    # are open-ended (kpi-sync, migrator); short-lived tokens for one-off
    # scripts can set this.
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_api_tokens_user_id_active", "user_id", "revoked_at"),
    )

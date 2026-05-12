import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional
from sqlalchemy import String, Enum, DateTime, JSON, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
try:
    from ..database import Base
except ImportError:
    from database import Base

class UserStatus(str, PyEnum):
    active = "active"
    deactivated = "deactivated"
    pending_invite = "pending_invite"
    pending_verification = "pending_verification"


class UserRole(str, PyEnum):
    """Organization-level role for an internal KPI user account.

    Three tiers, monotonic in privileges:

      * ``editor``   — uploads versions to assets where they are assignee,
                       comments, logs time, internal-approves their own work.
                       Default for any new invite.
      * ``producer`` — everything editor plus: creates assets in projects
                       they are a member of, manages assignee/reviewer/phase/
                       priority, ``Send to client``, ``Mark delivered``,
                       manages project membership.
      * ``admin``    — everything producer plus a global bypass: sees every
                       project without explicit membership, manages org-level
                       users (invite / change role / deactivate), branding,
                       billing.

    External clients are NOT a UserRole — they live in the ``guest_users``
    table and act via share-links (where the ``ShareLink.permission`` field
    gates view / comment / approve).
    """
    editor = "editor"
    producer = "producer"
    admin = "admin"


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[UserStatus] = mapped_column(Enum(UserStatus), default=UserStatus.active)
    is_superadmin: Mapped[bool] = mapped_column(default=False)
    # role mirrors is_superadmin for admins (kept in lockstep by the
    # ``users.update_user_role`` endpoint) and lets us express editor /
    # producer distinctions that ``is_superadmin`` cannot. New code should
    # read ``role``; ``is_superadmin`` stays for backwards compat with the
    # pre-N1.A code paths (admin router, setup wizard, signup).
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False, default=UserRole.editor)
    email_verified: Mapped[bool] = mapped_column(default=False)
    invite_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    invite_token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    preferences: Mapped[dict] = mapped_column(JSON, nullable=False, server_default='{}')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

class GuestUser(Base):
    __tablename__ = "guest_users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

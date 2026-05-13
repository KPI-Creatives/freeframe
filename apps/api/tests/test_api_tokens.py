"""Unit tests for API token primitives.

HTTP-level tests for the /me/api-tokens endpoints need a real DB session
to exercise the FK + UNIQUE prefix constraint; they live as integration
tests against staging. This file covers the pure logic surface:

  * ``mint_token`` shape and uniqueness
  * ``verify_token`` happy / unhappy paths via MagicMock'd Session
  * Producer/admin gate on the endpoint (role check)
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest


# ── mint_token ───────────────────────────────────────────────────────────────


def test_mint_token_shape():
    from apps.api.services.token_service import mint_token

    public, prefix, hashed = mint_token()
    assert public.startswith("ft_")
    assert public.startswith(f"ft_{prefix}_")
    assert len(prefix) == 8
    assert len(hashed) > 30  # bcrypt hashes are ~60 chars
    # Secret is everything after the second underscore.
    secret = public.split("_", 2)[2]
    assert len(secret) >= 32


def test_mint_token_unique_per_call():
    from apps.api.services.token_service import mint_token

    seen = set()
    for _ in range(50):
        public, prefix, _ = mint_token()
        assert prefix not in seen, "prefix must be unique across runs"
        seen.add(prefix)
        assert public not in seen
        seen.add(public)


def test_mint_token_hash_verifies_against_full_public_token():
    """The bcrypt hash is of the FULL public token, not just the secret —
    so a leaked prefix alone is useless without the secret half."""
    import bcrypt

    from apps.api.services.token_service import mint_token

    public, prefix, hashed = mint_token()
    assert bcrypt.checkpw(public[:72].encode(), hashed.encode())
    # Just the prefix half should NOT verify
    prefix_only = f"ft_{prefix}_"
    assert not bcrypt.checkpw(prefix_only.encode(), hashed.encode())


# ── verify_token helpers ─────────────────────────────────────────────────────


def _mock_row(public_token: str, hashed: str, **overrides):
    """Build a mock ApiToken row matching a real one."""
    row = MagicMock()
    row.prefix = public_token.split("_", 2)[1]
    row.token_hash = hashed
    row.revoked_at = overrides.get("revoked_at")
    row.expires_at = overrides.get("expires_at")
    row.user_id = overrides.get("user_id", "user-uuid")
    row.last_used_at = None
    return row


def _db_returning(row):
    db = MagicMock()
    db.query.return_value = db
    db.filter.return_value = db
    db.first.return_value = row
    return db


# ── verify_token happy path ──────────────────────────────────────────────────


def test_verify_token_returns_row_tuple_for_valid_token():
    from apps.api.services.token_service import mint_token, verify_token

    public, _, hashed = mint_token()
    row = _mock_row(public, hashed)
    db = _db_returning(row)

    out = verify_token(db, public)
    assert out is not None
    returned_row, bumped = out
    assert returned_row is row
    # First-ever use: bumped is True and last_used_at advanced.
    assert bumped is True
    assert row.last_used_at is not None


# ── verify_token unhappy paths ───────────────────────────────────────────────


def test_verify_token_rejects_malformed_no_namespace():
    from apps.api.services.token_service import verify_token

    db = MagicMock()
    assert verify_token(db, "") is None
    assert verify_token(db, "eyJhbGciOiJIUzI1NiJ9.x.y") is None
    assert verify_token(db, "random") is None


def test_verify_token_rejects_namespace_only_no_secret():
    from apps.api.services.token_service import verify_token

    db = MagicMock()
    assert verify_token(db, "ft_") is None
    # Has namespace + prefix but missing underscore + secret
    assert verify_token(db, "ft_abc12345") is None


def test_verify_token_rejects_unknown_prefix():
    from apps.api.services.token_service import verify_token

    db = _db_returning(None)
    assert verify_token(db, "ft_abc12345_thisisnotarealsecret123456") is None


def test_verify_token_rejects_wrong_secret():
    from apps.api.services.token_service import mint_token, verify_token

    public, _, hashed = mint_token()
    row = _mock_row(public, hashed)
    db = _db_returning(row)

    # Mangle the secret half — prefix matches, secret doesn't.
    parts = public.split("_", 2)
    bad = f"{parts[0]}_{parts[1]}_NotTheRealSecretButLooksValid"
    assert verify_token(db, bad) is None


def test_verify_token_rejects_revoked():
    """The DB query filters on ``revoked_at IS NULL`` — so a revoked row
    will return None from .first(). We simulate that here."""
    from apps.api.services.token_service import mint_token, verify_token

    public, _, hashed = mint_token()
    db = _db_returning(None)  # filter on revoked_at = NULL excludes the row
    assert verify_token(db, public) is None


def test_verify_token_rejects_expired():
    from apps.api.services.token_service import mint_token, verify_token

    public, _, hashed = mint_token()
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    row = _mock_row(public, hashed, expires_at=past)
    db = _db_returning(row)

    assert verify_token(db, public) is None


def test_verify_token_accepts_not_yet_expired():
    from apps.api.services.token_service import mint_token, verify_token

    public, _, hashed = mint_token()
    future = datetime.now(timezone.utc) + timedelta(days=30)
    row = _mock_row(public, hashed, expires_at=future)
    db = _db_returning(row)

    out = verify_token(db, public)
    assert out is not None
    returned_row, _bumped = out
    assert returned_row is row


def test_verify_token_naive_expires_at_treated_as_utc():
    """Defensive: if expires_at slips out of DB as naive datetime, the
    comparator should still work — we replace tzinfo with UTC."""
    from apps.api.services.token_service import mint_token, verify_token

    public, _, hashed = mint_token()
    past_naive = datetime.utcnow() - timedelta(minutes=5)  # naive
    row = _mock_row(public, hashed, expires_at=past_naive)
    db = _db_returning(row)

    assert verify_token(db, public) is None


# ── Role gate on router (pure logic) ─────────────────────────────────────────


def test_producer_can_create():
    from apps.api.models.user import UserRole
    from apps.api.services.permissions_role import role_at_least

    user = MagicMock()
    user.role = UserRole.producer
    assert role_at_least(user, UserRole.producer)


def test_admin_can_create():
    from apps.api.models.user import UserRole
    from apps.api.services.permissions_role import role_at_least

    user = MagicMock()
    user.role = UserRole.admin
    assert role_at_least(user, UserRole.producer)


def test_editor_cannot_create():
    from apps.api.models.user import UserRole
    from apps.api.services.permissions_role import role_at_least

    user = MagicMock()
    user.role = UserRole.editor
    assert not role_at_least(user, UserRole.producer)


def test_verify_token_throttles_last_used_bumps_to_once_per_minute():
    """Two verifies within 60 seconds: the first bumps, the second does not."""
    from apps.api.services.token_service import mint_token, verify_token

    public, _, hashed = mint_token()
    row = _mock_row(public, hashed)
    # Pretend the token was used 5 seconds ago — under the throttle window.
    row.last_used_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    db = _db_returning(row)

    out = verify_token(db, public)
    assert out is not None
    _row, bumped = out
    assert bumped is False, "throttled: <60s since last use → no bump"


def test_verify_token_bumps_again_after_minute_window():
    from apps.api.services.token_service import mint_token, verify_token

    public, _, hashed = mint_token()
    row = _mock_row(public, hashed)
    row.last_used_at = datetime.now(timezone.utc) - timedelta(seconds=120)
    db = _db_returning(row)

    out = verify_token(db, public)
    assert out is not None
    _row, bumped = out
    assert bumped is True, "≥60s since last use → bump"

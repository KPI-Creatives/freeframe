"""Personal API token primitives.

Lifecycle:
  * ``mint_token()`` is called once at create time. Returns the public
    string the user copies and the bcrypt hash we store.
  * ``verify_token()`` is called on every auth attempt. Parses
    ``ft_<prefix>_<secret>``, looks up the row by prefix, bcrypt-verifies
    the secret half against ``token_hash``, and returns the row if valid
    and not revoked/expired.

Format reminder:
    Full token = ``ft_<prefix>_<secret>``
      * prefix is 8 url-safe chars, also stored in the row (visible to user)
      * secret is 32+ url-safe chars, never stored in plain
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Optional, Tuple

import bcrypt
from sqlalchemy.orm import Session

from ..models.api_token import ApiToken


_PREFIX_LEN = 8
_SECRET_LEN = 32  # token_urlsafe(32) → ~43 chars (base64-url-encoded 32 bytes)
_NAMESPACE = "ft_"


def mint_token() -> Tuple[str, str, str]:
    """Generate a new token. Returns (public_token, prefix, bcrypt_hash).

    The public token is what we hand to the caller exactly once. The
    bcrypt hash is what we store on the row. The prefix is plain because
    we need it as a lookup key during ``verify_token`` — bcrypt isn't a
    full-table-scan-friendly index.
    """
    prefix = secrets.token_urlsafe(_PREFIX_LEN)[:_PREFIX_LEN]
    secret = secrets.token_urlsafe(_SECRET_LEN)
    public = f"{_NAMESPACE}{prefix}_{secret}"
    # bcrypt hashes the FULL public token, not just the secret. A leaked
    # prefix alone is useless — the attacker still needs the secret half.
    hashed = bcrypt.hashpw(public[:72].encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    return public, prefix, hashed


def verify_token(db: Session, token: str) -> Optional[ApiToken]:
    """Return the matching live ApiToken row or None.

    Cheap path: parse → lookup by prefix (indexed unique) → bcrypt compare.
    Side effect on success: bumps ``last_used_at``. We don't touch the DB
    on miss/expired/revoked so a token-stuffing attack stays cheap to handle.
    """
    if not token or not token.startswith(_NAMESPACE):
        return None
    rest = token[len(_NAMESPACE):]
    if "_" not in rest:
        return None
    prefix, _, _ = rest.partition("_")
    if not prefix or len(prefix) > 16:
        return None

    row = (
        db.query(ApiToken)
        .filter(ApiToken.prefix == prefix, ApiToken.revoked_at.is_(None))
        .first()
    )
    if row is None:
        return None

    # Expiry check (cheap, done before bcrypt).
    if row.expires_at is not None:
        now = datetime.now(timezone.utc)
        exp = row.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if now >= exp:
            return None

    # bcrypt verify — constant-time inside the lib.
    try:
        ok = bcrypt.checkpw(token[:72].encode("utf-8"), row.token_hash.encode("utf-8"))
    except ValueError:
        ok = False
    if not ok:
        return None

    # Update last_used_at. We commit later in the same request lifecycle
    # via the DB session that's normally committed on response.
    row.last_used_at = datetime.now(timezone.utc)
    return row

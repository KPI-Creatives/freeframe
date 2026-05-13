"""Personal API token management.

Surface mounted under the existing `/me` router-prefix convention. The
three endpoints map 1:1 to the standard PAT lifecycle:

  * ``POST   /me/api-tokens``       — mint a new token (returned ONCE)
  * ``GET    /me/api-tokens``       — list this user's tokens (no secrets)
  * ``DELETE /me/api-tokens/{id}``  — soft-revoke a token

Permissions: only producers and admins can mint tokens. Editors can already
do everything they need through the UI; long-lived tokens are an
automation primitive and we'd rather scope its existence to the people
who will actually script against the API.
"""
from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..middleware.auth import get_current_user
from ..models.api_token import ApiToken
from ..models.user import User, UserRole
from ..schemas.api_token import (
    ApiTokenCreate,
    ApiTokenCreateResponse,
    ApiTokenResponse,
)
from ..services.permissions_role import role_at_least
from ..services.token_service import mint_token


router = APIRouter(prefix="/me/api-tokens", tags=["api-tokens"])


def _require_producer_or_admin(user: User) -> None:
    """Editors cannot mint API tokens — see module docstring."""
    if not role_at_least(user, UserRole.producer):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Creating API tokens requires producer or admin role.",
        )


@router.post(
    "",
    response_model=ApiTokenCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_api_token(
    body: ApiTokenCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_producer_or_admin(current_user)

    public, prefix, token_hash = mint_token()
    row = ApiToken(
        user_id=current_user.id,
        name=body.name,
        prefix=prefix,
        token_hash=token_hash,
        expires_at=body.expires_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    # ApiTokenCreateResponse is the ONLY surface that includes the raw
    # secret. All future reads return the listing schema, which strips it.
    return ApiTokenCreateResponse(
        id=row.id,
        name=row.name,
        prefix=row.prefix,
        token=public,
        expires_at=row.expires_at,
        created_at=row.created_at,
    )


@router.get("", response_model=list[ApiTokenResponse])
def list_api_tokens(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List the current user's tokens — both active and revoked, so the
    user can see the history. The UI hides revoked ones behind a toggle."""
    rows = (
        db.query(ApiToken)
        .filter(ApiToken.user_id == current_user.id)
        .order_by(ApiToken.created_at.desc())
        .all()
    )
    return [ApiTokenResponse.model_validate(r) for r in rows]


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_token(
    token_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Soft-revoke. ``revoked_at`` is set; ``verify_token`` filters on this
    column so the token stops authenticating immediately on next request."""
    row = (
        db.query(ApiToken)
        .filter(ApiToken.id == token_id, ApiToken.user_id == current_user.id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Token not found")
    if row.revoked_at is not None:
        # Idempotent — revoke again is a no-op. Returning 204 keeps the
        # caller from having to special-case "already revoked".
        return
    row.revoked_at = datetime.now(timezone.utc)
    db.commit()

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import uuid
from typing import Optional
from sqlalchemy.orm import Session
from ..database import get_db
from ..services.auth_service import decode_token, get_user_by_id
from ..services.token_service import verify_token as verify_api_token
from ..models.user import User, UserStatus

bearer_scheme = HTTPBearer()
optional_bearer_scheme = HTTPBearer(auto_error=False)


def _resolve_credentials(token: str, db: Session) -> Optional[User]:
    """Resolve a bearer credential to a User.

    Accepts BOTH:
      * session JWTs (``eyJ...``) issued by the login flow — same code path
        as before, decode + lookup-by-sub.
      * personal API tokens (``ft_<prefix>_<secret>``) issued via
        ``POST /me/api-tokens`` — lookup-by-prefix + bcrypt-verify-secret.

    The two formats are distinguished by the ``ft_`` namespace prefix so
    we don't waste a JWT decode attempt on every API call.
    """
    if not token:
        return None
    if token.startswith("ft_"):
        row = verify_api_token(db, token)
        if row is None:
            return None
        return get_user_by_id(db, row.user_id)
    # Fall through to JWT path
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        return None
    return get_user_by_id(db, uuid.UUID(payload["sub"]))


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    user = _resolve_credentials(credentials.credentials, db)
    if user is None or user.status == UserStatus.deactivated:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return user


def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(optional_bearer_scheme),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Returns the authenticated user if a valid token is provided, None otherwise."""
    if not credentials:
        return None
    try:
        user = _resolve_credentials(credentials.credentials, db)
        if user is None or user.status == UserStatus.deactivated:
            return None
        return user
    except Exception:
        return None

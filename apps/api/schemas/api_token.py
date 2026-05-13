import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ApiTokenCreate(BaseModel):
    """POST /me/api-tokens body."""

    name: str = Field(..., min_length=1, max_length=120)
    # Optional explicit expiry. ``None`` = never expires. The frontend
    # surfaces a small "Expires in" picker (7d / 30d / 90d / never).
    expires_at: Optional[datetime] = None


class ApiTokenCreateResponse(BaseModel):
    """Returned ONCE on creation. The ``token`` field is the only chance the
    caller has to capture the full secret. After this it's bcrypt-hashed and
    unrecoverable.
    """

    id: uuid.UUID
    name: str
    prefix: str
    token: str               # ft_<prefix>_<secret> — full, plain-text, one-shot
    expires_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class ApiTokenResponse(BaseModel):
    """Listing / GET shape. Does NOT include the secret — only the prefix that
    the user wrote down at creation time and uses to disambiguate."""

    id: uuid.UUID
    name: str
    prefix: str
    last_used_at: Optional[datetime]
    expires_at: Optional[datetime]
    revoked_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}

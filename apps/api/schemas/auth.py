from pydantic import BaseModel, EmailStr
import uuid
from ..models.user import UserStatus

class RegisterRequest(BaseModel):
    email: EmailStr
    name: str
    password: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    needs_password: bool = False  # True if user needs to set password

class RefreshRequest(BaseModel):
    refresh_token: str

class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    avatar_url: str | None
    status: UserStatus
    email_verified: bool = False
    is_superadmin: bool = False
    role: str = "editor"        # editor | producer | admin (mirrors users.UserRole)
    invite_token: str | None = None
    preferences: dict = {}

    model_config = {"from_attributes": True}

class InviteRequest(BaseModel):
    email: EmailStr
    name: str
    role: str = "editor"          # editor | producer | admin (default editor)

# Magic code flow
class SendMagicCodeRequest(BaseModel):
    email: EmailStr

class SendMagicCodeResponse(BaseModel):
    message: str
    email: str

class VerifyMagicCodeRequest(BaseModel):
    email: EmailStr
    code: str

class SetPasswordRequest(BaseModel):
    password: str

# Invite flow
class AcceptInviteRequest(BaseModel):
    token: str
    password: str

class InviteInfoResponse(BaseModel):
    email: str
    name: str
    org_name: str | None = None

class UpdateProfileRequest(BaseModel):
    name: str | None = None
    avatar_url: str | None = None

class UpdateUserRoleRequest(BaseModel):
    # Either field is accepted; ``role`` is the new canonical form, ``is_admin``
    # remains for old clients (true ⇒ admin, false ⇒ editor — a coarse mapping
    # that does NOT touch a user who is already a producer).
    role: str | None = None         # "editor" | "producer" | "admin"
    is_admin: bool | None = None

class DeactivateUserRequest(BaseModel):
    user_id: uuid.UUID


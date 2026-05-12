"""Organization-role permission helpers.

These complement ``permissions.py``'s per-project role checks. UserRole
permissions (editor < producer < admin) are global to the workspace and
gate org-level actions like creating assets, sending to client, managing
other users.

Usage:
    from fastapi import Depends
    from ..services.permissions_role import require_producer

    @router.post("/assets/{id}/send-to-client")
    def send_to_client(..., _: User = Depends(require_producer)):
        ...
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status

from ..middleware.auth import get_current_user
from ..models.user import User, UserRole


# Monotonic role order: a check for ``producer`` passes for ``producer`` AND
# ``admin``, but not for ``editor``.
_ROLE_RANK = {
    UserRole.editor: 1,
    UserRole.producer: 2,
    UserRole.admin: 3,
}


def role_at_least(user: User, minimum: UserRole) -> bool:
    """True if ``user.role`` >= ``minimum`` in the editor < producer < admin order."""
    return _ROLE_RANK.get(user.role, 0) >= _ROLE_RANK[minimum]


def require_role(minimum: UserRole):
    """Build a FastAPI dependency that 403s unless the user's role >= minimum."""
    def _check(current_user: User = Depends(get_current_user)) -> User:
        if not role_at_least(current_user, minimum):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires {minimum.value} role or higher",
            )
        return current_user
    return _check


# Convenience pre-built dependencies — import these directly.
require_editor = require_role(UserRole.editor)
require_producer = require_role(UserRole.producer)
require_admin = require_role(UserRole.admin)

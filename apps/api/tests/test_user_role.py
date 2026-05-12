"""Tests for the N1.A UserRole layer.

Covers:
  * Enum/role rank helper
  * require_role dependency rejects insufficient roles
  * /admin/users/{id}/role accepts the new ``role`` field
  * /admin/users/{id}/role keeps backwards compat with ``is_admin``
  * Demoting an admin promotes is_superadmin in lockstep
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest

from apps.api.models.user import UserRole
from apps.api.services.permissions_role import role_at_least


def _user(role: UserRole) -> MagicMock:
    u = MagicMock()
    u.role = role
    return u


def _fake_target(role: UserRole, is_superadmin: bool = False) -> MagicMock:
    """A mock User that satisfies UserResponse validation when serialized."""
    from apps.api.models.user import UserStatus
    t = MagicMock()
    t.id = uuid.uuid4()
    t.email = "target@example.com"
    t.name = "Target User"
    t.avatar_url = None
    t.status = UserStatus.active
    t.deleted_at = None
    t.role = role
    t.is_superadmin = is_superadmin
    t.email_verified = False
    t.invite_token = None
    t.preferences = {}
    return t


def test_role_at_least_editor():
    assert role_at_least(_user(UserRole.editor), UserRole.editor)
    assert role_at_least(_user(UserRole.producer), UserRole.editor)
    assert role_at_least(_user(UserRole.admin), UserRole.editor)


def test_role_at_least_producer():
    assert not role_at_least(_user(UserRole.editor), UserRole.producer)
    assert role_at_least(_user(UserRole.producer), UserRole.producer)
    assert role_at_least(_user(UserRole.admin), UserRole.producer)


def test_role_at_least_admin():
    assert not role_at_least(_user(UserRole.editor), UserRole.admin)
    assert not role_at_least(_user(UserRole.producer), UserRole.admin)
    assert role_at_least(_user(UserRole.admin), UserRole.admin)


def test_user_role_enum_values():
    assert UserRole.editor.value == "editor"
    assert UserRole.producer.value == "producer"
    assert UserRole.admin.value == "admin"


# ── admin endpoint tests ─────────────────────────────────────────────────────

def test_update_role_accepts_role_field(client, mock_db, test_user, auth_headers):
    """PATCH /admin/users/:id/role with {role: 'producer'} promotes to producer."""
    target = _fake_target(UserRole.editor)
    mock_db.first.return_value = target
    test_user.is_superadmin = True
    test_user.role = UserRole.admin

    resp = client.patch(
        f"/admin/users/{target.id}/role",
        json={"role": "producer"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert target.role == UserRole.producer
    assert target.is_superadmin is False  # producer != admin


def test_update_role_accepts_legacy_is_admin_flag(client, mock_db, test_user, auth_headers):
    """Old clients sending {is_admin: true} still work and produce role=admin."""
    target = _fake_target(UserRole.editor)
    mock_db.first.return_value = target
    test_user.is_superadmin = True
    test_user.role = UserRole.admin

    resp = client.patch(
        f"/admin/users/{target.id}/role",
        json={"is_admin": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert target.role == UserRole.admin
    assert target.is_superadmin is True


def test_update_role_legacy_remove_admin_does_not_touch_producer(client, mock_db, test_user, auth_headers):
    """Legacy `{is_admin: false}` on an existing producer must NOT demote to editor."""
    target = _fake_target(UserRole.producer)
    mock_db.first.return_value = target
    test_user.is_superadmin = True
    test_user.role = UserRole.admin

    resp = client.patch(
        f"/admin/users/{target.id}/role",
        json={"is_admin": False},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert target.role == UserRole.producer  # unchanged
    assert target.is_superadmin is False


def test_update_role_rejects_unknown_role(client, mock_db, test_user, auth_headers):
    target = _fake_target(UserRole.editor)
    mock_db.first.return_value = target
    test_user.is_superadmin = True
    test_user.role = UserRole.admin

    resp = client.patch(
        f"/admin/users/{target.id}/role",
        json={"role": "wizard"},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "wizard" in resp.text.lower()


# ── Smart per-project default tests ──────────────────────────────────────────

def test_default_project_role_for_admin():
    from apps.api.routers.projects import _default_project_role_for
    from apps.api.models.project import ProjectRole
    u = MagicMock()
    u.role = UserRole.admin
    assert _default_project_role_for(u) == ProjectRole.owner


def test_default_project_role_for_producer():
    from apps.api.routers.projects import _default_project_role_for
    from apps.api.models.project import ProjectRole
    u = MagicMock()
    u.role = UserRole.producer
    assert _default_project_role_for(u) == ProjectRole.owner


def test_default_project_role_for_editor():
    from apps.api.routers.projects import _default_project_role_for
    from apps.api.models.project import ProjectRole
    u = MagicMock()
    u.role = UserRole.editor
    assert _default_project_role_for(u) == ProjectRole.editor


def test_default_project_role_falls_back_to_viewer_for_unknown_role():
    from apps.api.routers.projects import _default_project_role_for
    from apps.api.models.project import ProjectRole
    u = MagicMock()
    u.role = None  # malformed user
    assert _default_project_role_for(u) == ProjectRole.viewer

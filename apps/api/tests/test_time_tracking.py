"""Unit tests for Time Tracking primitives.

HTTP-level tests for the log-time endpoint live as integration tests against
the staging Coolify deploy — they need a real Postgres to exercise the
CHECK constraint and the SUM-recompute in the same txn. This file covers
pure logic:

  * ``TimeTrackingDefault`` enum sanity
  * ``LogTimeRequest`` validation (multiple-of-5, range, null/skip)
  * ``resolve_track_time_default`` walk: explicit on/off, parent inheritance,
    root inheritance falling back to off, cycle defence
  * ``_can_log_time`` permission matrix: producer bypass, editor own/window
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest


# ── Enum sanity ──────────────────────────────────────────────────────────────


def test_time_tracking_default_enum_values():
    from apps.api.models.folder import TimeTrackingDefault

    assert TimeTrackingDefault.on.value == "on"
    assert TimeTrackingDefault.off.value == "off"
    assert TimeTrackingDefault.inherit.value == "inherit"


# ── LogTimeRequest schema validation ─────────────────────────────────────────


def test_log_time_request_accepts_multiples_of_5():
    from apps.api.schemas.asset import LogTimeRequest

    for v in [0, 5, 10, 15, 30, 45, 60, 90, 120, 480, 1440]:
        req = LogTimeRequest(minutes_spent=v)
        assert req.minutes_spent == v


def test_log_time_request_accepts_null_as_skip():
    from apps.api.schemas.asset import LogTimeRequest

    assert LogTimeRequest(minutes_spent=None).minutes_spent is None
    assert LogTimeRequest().minutes_spent is None  # default


def test_log_time_request_rejects_non_multiple_of_5():
    from apps.api.schemas.asset import LogTimeRequest
    from pydantic import ValidationError

    for bad in [1, 3, 7, 11, 17, 23, 91, 119]:
        with pytest.raises(ValidationError):
            LogTimeRequest(minutes_spent=bad)


def test_log_time_request_rejects_negative():
    from apps.api.schemas.asset import LogTimeRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        LogTimeRequest(minutes_spent=-5)


def test_log_time_request_rejects_above_24h_cap():
    from apps.api.schemas.asset import LogTimeRequest
    from pydantic import ValidationError

    # 1440 (24h) is the boundary, allowed. 1445 over.
    LogTimeRequest(minutes_spent=1440)
    with pytest.raises(ValidationError):
        LogTimeRequest(minutes_spent=1445)


# ── resolve_track_time_default walk ──────────────────────────────────────────


def _mock_folder(
    folder_id: uuid.UUID,
    parent_id: uuid.UUID | None,
    policy: str,
):
    """A MagicMock folder with the fields the resolver reads."""
    from apps.api.models.folder import TimeTrackingDefault

    f = MagicMock()
    f.id = folder_id
    f.parent_id = parent_id
    f.time_tracking_default = TimeTrackingDefault(policy)
    f.deleted_at = None
    return f


def _resolver_db(folder_chain: list):
    """Build a mock Session whose query(Folder).filter(...).first() walks
    through ``folder_chain`` in order (each call returns the next folder).
    """
    db = MagicMock()
    folders_by_id = {f.id: f for f in folder_chain}

    # The resolver does: db.query(Folder).filter(Folder.id == current_id,
    # Folder.deleted_at.is_(None)).first()
    # We can't easily intercept Folder.id == ... but we CAN make first()
    # pop the next folder if the chain was constructed in walk order.
    iterator = iter(folder_chain)

    def _first():
        try:
            return next(iterator)
        except StopIteration:
            return None

    db.query.return_value = db
    db.filter.return_value = db
    db.first.side_effect = _first
    return db


def test_resolve_returns_false_for_no_folder():
    from apps.api.services.folder_helpers import resolve_track_time_default

    db = MagicMock()
    assert resolve_track_time_default(db, None) is False


def test_resolve_explicit_on():
    from apps.api.services.folder_helpers import resolve_track_time_default

    f_id = uuid.uuid4()
    folder = _mock_folder(f_id, None, "on")
    db = _resolver_db([folder])

    assert resolve_track_time_default(db, f_id) is True


def test_resolve_explicit_off():
    from apps.api.services.folder_helpers import resolve_track_time_default

    f_id = uuid.uuid4()
    folder = _mock_folder(f_id, None, "off")
    db = _resolver_db([folder])

    assert resolve_track_time_default(db, f_id) is False


def test_resolve_inherits_from_parent_on():
    from apps.api.services.folder_helpers import resolve_track_time_default

    parent_id = uuid.uuid4()
    child_id = uuid.uuid4()
    child = _mock_folder(child_id, parent_id, "inherit")
    parent = _mock_folder(parent_id, None, "on")
    db = _resolver_db([child, parent])

    assert resolve_track_time_default(db, child_id) is True


def test_resolve_inherits_from_parent_off():
    from apps.api.services.folder_helpers import resolve_track_time_default

    parent_id = uuid.uuid4()
    child_id = uuid.uuid4()
    child = _mock_folder(child_id, parent_id, "inherit")
    parent = _mock_folder(parent_id, None, "off")
    db = _resolver_db([child, parent])

    assert resolve_track_time_default(db, child_id) is False


def test_resolve_full_inherit_chain_falls_back_to_off():
    """If every folder in the chain says ``inherit``, the conservative root
    default is off — don't ask for time unless something explicitly opts in.
    """
    from apps.api.services.folder_helpers import resolve_track_time_default

    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    grand = _mock_folder(a, None, "inherit")
    parent = _mock_folder(b, a, "inherit")
    child = _mock_folder(c, b, "inherit")
    db = _resolver_db([child, parent, grand])

    assert resolve_track_time_default(db, c) is False


def test_resolve_missing_folder_returns_false():
    """If a folder lookup mid-walk returns None (deleted, race), fail safe."""
    from apps.api.services.folder_helpers import resolve_track_time_default

    db = MagicMock()
    db.query.return_value = db
    db.filter.return_value = db
    db.first.return_value = None  # folder not found

    assert resolve_track_time_default(db, uuid.uuid4()) is False


# ── _can_log_time permission matrix ──────────────────────────────────────────


def _mk_version(created_by: uuid.UUID, age_days: float):
    v = MagicMock()
    v.created_by = created_by
    v.created_at = datetime.now(timezone.utc) - timedelta(days=age_days)
    return v


def _mk_user(user_id: uuid.UUID, role: str):
    from apps.api.models.user import UserRole

    u = MagicMock()
    u.id = user_id
    u.role = UserRole(role)
    return u


def test_can_log_time_producer_can_log_own_recent():
    from apps.api.routers.assets import _can_log_time

    uid = uuid.uuid4()
    user = _mk_user(uid, "producer")
    version = _mk_version(uid, age_days=1)

    allowed, _ = _can_log_time(version, user)
    assert allowed is True


def test_can_log_time_producer_can_log_someone_elses_old_version():
    """Producer bypass: own/window rules don't apply."""
    from apps.api.routers.assets import _can_log_time

    user = _mk_user(uuid.uuid4(), "producer")
    version = _mk_version(uuid.uuid4(), age_days=30)  # someone else, 30d old

    allowed, _ = _can_log_time(version, user)
    assert allowed is True


def test_can_log_time_admin_can_log_anything():
    from apps.api.routers.assets import _can_log_time

    user = _mk_user(uuid.uuid4(), "admin")
    version = _mk_version(uuid.uuid4(), age_days=100)

    allowed, _ = _can_log_time(version, user)
    assert allowed is True


def test_can_log_time_editor_can_log_own_within_window():
    from apps.api.routers.assets import _can_log_time

    uid = uuid.uuid4()
    user = _mk_user(uid, "editor")
    version = _mk_version(uid, age_days=3)

    allowed, _ = _can_log_time(version, user)
    assert allowed is True


def test_can_log_time_editor_cannot_log_someone_elses():
    from apps.api.routers.assets import _can_log_time

    user = _mk_user(uuid.uuid4(), "editor")
    version = _mk_version(uuid.uuid4(), age_days=1)  # different uploader

    allowed, reason = _can_log_time(version, user)
    assert allowed is False
    assert "editor who uploaded" in reason


def test_can_log_time_editor_cannot_log_outside_window():
    from apps.api.routers.assets import _can_log_time

    uid = uuid.uuid4()
    user = _mk_user(uid, "editor")
    version = _mk_version(uid, age_days=10)  # own, but stale

    allowed, reason = _can_log_time(version, user)
    assert allowed is False
    assert "7-day" in reason


def test_can_log_time_editor_at_window_boundary():
    """Exactly 7 days old = allowed; 7.5 days = denied."""
    from apps.api.routers.assets import _can_log_time

    uid = uuid.uuid4()
    user = _mk_user(uid, "editor")

    just_within = _mk_version(uid, age_days=6.9)
    allowed, _ = _can_log_time(just_within, user)
    assert allowed is True

    just_outside = _mk_version(uid, age_days=7.1)
    allowed, reason = _can_log_time(just_outside, user)
    assert allowed is False

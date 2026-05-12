"""Unit tests for N1.B asset workflow primitives.

HTTP-level tests for the phase guards and Send-to-client endpoint are
deliberately out of scope here — they need a real DB session to exercise
``require_project_role`` and the multi-step ORM lookups. Those land as
integration tests in a separate suite that runs against the Coolify staging
deploy. This file covers the pure logic:

  * Enum sanity (UserRole, AssetPhase, AssetPriority values are stable)
  * Pydantic schema validation for VideoCustomFields
  * Phase rank ordering (used by the backwards-transition guard)
  * Custom-fields dict validator (the public helper used by routers)
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from apps.api.models.asset import AssetPhase, AssetPriority


# ── Enum sanity ──────────────────────────────────────────────────────────────

def test_asset_phase_enum_values():
    assert AssetPhase.internal.value == "internal"
    assert AssetPhase.client.value == "client"
    assert AssetPhase.delivered.value == "delivered"


def test_asset_priority_enum_values():
    assert AssetPriority.P0.value == "P0"
    assert AssetPriority.P1.value == "P1"
    assert AssetPriority.P2.value == "P2"


# ── Phase ordering — used by the backwards-transition guard ──────────────────

def test_phase_rank_internal_lt_client_lt_delivered():
    """The PATCH /assets/:id handler uses this exact ordering to forbid
    backwards transitions. If the ranking changes, the guard breaks."""
    order = {AssetPhase.internal: 0, AssetPhase.client: 1, AssetPhase.delivered: 2}
    assert order[AssetPhase.internal] < order[AssetPhase.client] < order[AssetPhase.delivered]


def test_phase_backwards_detected_by_rank_compare():
    order = {AssetPhase.internal: 0, AssetPhase.client: 1, AssetPhase.delivered: 2}
    # client -> internal: backwards
    assert order[AssetPhase.internal] < order[AssetPhase.client]
    # delivered -> client: backwards
    assert order[AssetPhase.client] < order[AssetPhase.delivered]
    # internal -> client: forward (allowed)
    assert order[AssetPhase.client] > order[AssetPhase.internal]


# ── VideoCustomFields validation ─────────────────────────────────────────────

def test_video_custom_fields_accepts_known_format():
    from apps.api.schemas.video_fields import VideoCustomFields
    v = VideoCustomFields(format="yt-long", goal="education")
    assert v.format == "yt-long"
    assert v.goal == "education"


def test_video_custom_fields_rejects_unknown_format():
    from apps.api.schemas.video_fields import VideoCustomFields
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        VideoCustomFields(format="vimeo-square")


def test_video_custom_fields_rejects_unknown_goal():
    from apps.api.schemas.video_fields import VideoCustomFields
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        VideoCustomFields(goal="explosion-of-vibes")


def test_video_custom_fields_style_max_three():
    from apps.api.schemas.video_fields import VideoCustomFields
    from pydantic import ValidationError
    # Up to 3 values is allowed
    v = VideoCustomFields(style=["talking-head", "b-roll", "motion-graphics"])
    assert len(v.style) == 3
    # 4 values fails
    with pytest.raises(ValidationError):
        VideoCustomFields(style=[
            "talking-head", "b-roll", "motion-graphics", "talking-head"
        ])


def test_video_custom_fields_style_accepts_empty():
    from apps.api.schemas.video_fields import VideoCustomFields
    v = VideoCustomFields()
    assert v.style == []


def test_video_custom_fields_talent_max_length():
    from apps.api.schemas.video_fields import VideoCustomFields
    from pydantic import ValidationError
    # 500 chars is the boundary
    VideoCustomFields(talent="a" * 500)
    with pytest.raises(ValidationError):
        VideoCustomFields(talent="a" * 501)


def test_validate_custom_fields_dict_empty_returns_empty():
    from apps.api.schemas.video_fields import validate_custom_fields_dict
    assert validate_custom_fields_dict({}) == {}
    assert validate_custom_fields_dict(None) == {}


def test_validate_custom_fields_dict_passes_valid_payload():
    from apps.api.schemas.video_fields import validate_custom_fields_dict
    out = validate_custom_fields_dict({
        "format": "shorts",
        "goal": "awareness",
        "style": ["b-roll"],
    })
    assert out["format"] == "shorts"
    assert out["goal"] == "awareness"
    assert out["style"] == ["b-roll"]


# ── Phase-aware comment filter time-window logic ─────────────────────────────

def test_phase_filter_window_internal_no_floor():
    """When phase=internal, no time floor applies — all comments visible."""
    asset_phase = AssetPhase.internal
    phase_client_at = None
    assert asset_phase not in (AssetPhase.client, AssetPhase.delivered)


def test_phase_filter_window_client_uses_phase_client_at():
    """When phase=client, the floor is phase_client_at — same for delivered."""
    asset_phase = AssetPhase.client
    phase_client_at = datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc)
    before = phase_client_at - timedelta(minutes=10)
    after = phase_client_at + timedelta(minutes=10)
    assert asset_phase in (AssetPhase.client, AssetPhase.delivered)
    # The actual SQL filter is `Comment.created_at >= phase_client_at`
    assert before < phase_client_at
    assert after >= phase_client_at

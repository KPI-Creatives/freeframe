"""Video-specific custom fields stored in Asset.custom_fields (JSONB).

These are KPI-Creatives-specific workflow metadata: format / goal / source /
style / talent. They live in JSONB rather than separate columns because
they are video-only, won't drive cross-cutting queries (priority, phase,
assignee, reviewer are columns for that), and we want to add new ones
without a migration.

Pydantic enforces typed values at the API boundary. Anything reaching the
database has gone through validation.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class VideoFormat(str, Enum):
    yt_long = "yt-long"
    shorts = "shorts"
    reels = "reels"
    tiktok = "tiktok"


class VideoGoal(str, Enum):
    awareness = "awareness"
    lead_gen = "lead-gen"
    education = "education"
    proof = "proof"


class VideoSource(str, Enum):
    original_shoot = "original-shoot"
    client_supplied = "client-supplied"
    stock_mix = "stock-mix"


class VideoStyle(str, Enum):
    talking_head = "talking-head"
    b_roll = "b-roll"
    motion_graphics = "motion-graphics"


class VideoCustomFields(BaseModel):
    """All video-specific custom fields, all optional.

    Producers fill these to set editorial intent (Goal, Source), aspect /
    duration constraints (Format), and editorial flavour (Style, Talent).
    Empty values are valid — a brand-new asset has no fields set until the
    producer configures them.
    """

    format: VideoFormat | None = None
    goal: VideoGoal | None = None
    source: VideoSource | None = None
    style: list[VideoStyle] = Field(default_factory=list, max_length=3)
    talent: str | None = Field(default=None, max_length=500)

    model_config = {"use_enum_values": True}


def validate_custom_fields_dict(data: dict | None) -> dict:
    """Validate an inbound JSON object against the schema and return a plain
    dict suitable for writing to Asset.custom_fields. Empty/None passthrough
    yields ``{}`` so the DB column stays well-formed.
    """
    if not data:
        return {}
    # ``model_validate`` raises ValidationError on bad input — FastAPI will
    # convert that to a 422 response automatically when used in an endpoint
    # signature; the wrapper here is for explicit invocation.
    return VideoCustomFields.model_validate(data).model_dump(exclude_none=False)

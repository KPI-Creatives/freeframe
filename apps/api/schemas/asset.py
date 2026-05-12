from pydantic import BaseModel, Field, field_validator
import uuid
from datetime import datetime
from typing import Optional
from ..models.asset import AssetType, AssetStatus, ProcessingStatus, FileType, AssetPhase, AssetPriority
from ..models.activity import NotificationType

class MediaFileResponse(BaseModel):
    id: uuid.UUID
    version_id: uuid.UUID
    file_type: FileType
    original_filename: str
    mime_type: str
    file_size_bytes: int
    s3_key_raw: str
    s3_key_processed: Optional[str]
    s3_key_thumbnail: Optional[str]
    width: Optional[int]
    height: Optional[int]
    duration_seconds: Optional[float]
    fps: Optional[float]
    sequence_order: Optional[int]
    model_config = {"from_attributes": True}

class AssetVersionResponse(BaseModel):
    id: uuid.UUID
    asset_id: uuid.UUID
    version_number: int
    processing_status: ProcessingStatus
    # Editor-reported time spent on this version. ``None`` means the modal
    # was skipped or the asset isn't tracked. UI shows it as ``v2 · 30m``
    # in the version switcher when present.
    minutes_spent: Optional[int] = None
    created_by: uuid.UUID
    created_at: datetime
    files: list[MediaFileResponse] = []
    model_config = {"from_attributes": True}

class AssetResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: Optional[str]
    asset_type: AssetType
    status: AssetStatus
    rating: Optional[int]
    assignee_id: Optional[uuid.UUID]
    reviewer_id: Optional[uuid.UUID] = None
    folder_id: Optional[uuid.UUID] = None
    priority: Optional[AssetPriority] = None
    phase: AssetPhase = AssetPhase.internal
    phase_client_at: Optional[datetime] = None
    phase_delivered_at: Optional[datetime] = None
    client_baseline_version_id: Optional[uuid.UUID] = None
    delivered_version_id: Optional[uuid.UUID] = None
    block_reason: Optional[str] = None
    custom_fields: Optional[dict] = None
    # Time tracking — see models/asset.py for semantics.
    track_time: bool = False
    total_minutes_spent: int = 0
    due_date: Optional[datetime]
    keywords: Optional[list]
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    latest_version: Optional[AssetVersionResponse] = None
    thumbnail_url: Optional[str] = None
    model_config = {"from_attributes": True}

class AssetUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[AssetStatus] = None
    rating: Optional[int] = None
    assignee_id: Optional[uuid.UUID] = None
    reviewer_id: Optional[uuid.UUID] = None
    priority: Optional[AssetPriority] = None
    phase: Optional[AssetPhase] = None
    block_reason: Optional[str] = None
    # JSONB blob holding video-specific custom fields (format/goal/source/
    # style/talent). The shape is validated against
    # apps/api/schemas/video_fields.py:VideoCustomFields when the asset is
    # asset_type == 'video'. Other asset types may carry a free-form dict;
    # validation lives at the router level rather than the schema level so
    # we can branch on asset_type.
    custom_fields: Optional[dict] = None
    # Editor toggle in the Fields tab. Override the folder-resolved default.
    # No "inherit" — once resolved on creation, it's a per-asset boolean
    # forever (folder moves don't re-resolve).
    track_time: Optional[bool] = None
    due_date: Optional[datetime] = None
    keywords: Optional[list] = None

class StreamUrlResponse(BaseModel):
    url: str
    asset_type: AssetType
    expires_in: int = 3600

class NotificationResponse(BaseModel):
    id: uuid.UUID
    type: NotificationType
    asset_id: uuid.UUID
    comment_id: Optional[uuid.UUID] = None
    read: bool
    created_at: datetime
    # Enriched fields
    asset_name: Optional[str] = None
    actor_name: Optional[str] = None
    comment_preview: Optional[str] = None
    project_id: Optional[uuid.UUID] = None



# ── N1.B: Send-to-client / Mark-delivered request schemas ────────────────────

class SendToClientRequest(BaseModel):
    """Single atomic action — flip phase, snapshot baseline, mint share-link,
    send email. The producer fills these fields in one modal."""
    recipient_email: str                       # client email; share link emailed here
    recipient_name: Optional[str] = None       # used in the email greeting (optional)
    permission: str = "comment"               # "view" | "comment" | "approve"
    expires_in_days: Optional[int] = None      # null = never expires
    password: Optional[str] = None             # optional password gate
    message: Optional[str] = None              # custom note in the email body


class SendToClientResponse(BaseModel):
    asset_id: uuid.UUID
    phase: str                                  # always "client" after this call
    phase_client_at: datetime
    client_baseline_version_id: Optional[uuid.UUID] = None
    share_link_id: uuid.UUID
    share_url: str                              # full https URL to give the client


class MarkDeliveredRequest(BaseModel):
    """Payload is currently empty — the action is self-describing. Kept as a
    BaseModel so we can add fields (notify_channels, custom_note) later
    without breaking the endpoint signature."""
    pass


class MarkDeliveredResponse(BaseModel):
    asset_id: uuid.UUID
    phase: str                                  # always "delivered"
    phase_delivered_at: datetime
    delivered_version_id: Optional[uuid.UUID] = None
    share_links_downgraded: int                 # how many existing share-links got dropped to view-only


# ── Time-tracking request/response ────────────────────────────────────────────

class LogTimeRequest(BaseModel):
    """Body of POST /assets/:asset_id/versions/:version_id/log-time.

    ``minutes_spent``:
      * positive int, multiple of 5 — the editor's reported duration in min
      * ``0`` — explicit zero (cleared / mistake)
      * ``None`` — Skip pressed; treat as "unset"

    Hard-cap at 24h * 60 = 1440 to catch accidental "1080" or "1200" typo
    in the custom field. If someone actually spent more than a day on a
    single version, that's two entries split across versions, not one.
    """

    minutes_spent: Optional[int] = Field(default=None, ge=0, le=1440)

    @field_validator("minutes_spent")
    @classmethod
    def _multiple_of_five(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return v
        if v % 5 != 0:
            raise ValueError("minutes_spent must be a multiple of 5")
        return v


class LogTimeResponse(BaseModel):
    asset_id: uuid.UUID
    version_id: uuid.UUID
    version_minutes_spent: Optional[int]
    asset_total_minutes_spent: int

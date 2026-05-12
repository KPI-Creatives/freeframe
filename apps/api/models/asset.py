import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional
from sqlalchemy import String, Enum, DateTime, ForeignKey, Integer, Float, func, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
try:
    from ..database import Base
except ImportError:
    from database import Base

class AssetType(str, PyEnum):
    image = "image"
    image_carousel = "image_carousel"
    audio = "audio"
    video = "video"
    # Documents (Markdown/HTML/etc.). Stored verbatim in S3; no transcoding.
    document = "document"

class AssetStatus(str, PyEnum):
    draft = "draft"
    in_review = "in_review"
    approved = "approved"
    rejected = "rejected"
    archived = "archived"

class ProcessingStatus(str, PyEnum):
    uploading = "uploading"
    processing = "processing"
    ready = "ready"
    failed = "failed"


class AssetPhase(str, PyEnum):
    """Position of an asset in the review lifecycle.

      * ``internal``  — KPI internal QA cycle. Editor uploads, producer/reviewer
                        comments. Client cannot see anything yet.
      * ``client``    — Asset has been sent to the external client. Share-link
                        viewer filters versions and comments to only those at or
                        after ``phase_client_at`` (the moment the producer
                        clicked Send to client). ``client_baseline_version_id``
                        points to the version that was current at that moment.
      * ``delivered`` — Final, client-approved. ``delivered_version_id`` points
                        to the final version. New share-links default to view-only.

    Phase advances are ONE-WAY (internal → client → delivered). Backwards
    moves are forbidden by the API. Once a client has seen a version, the
    filter window is irreversible.
    """
    internal = "internal"
    client = "client"
    delivered = "delivered"


class AssetPriority(str, PyEnum):
    """Producer-set urgency tier.

      * ``P0`` — drop everything (client deadline, paid campaign, partner commit)
      * ``P1`` — committed for the current week (default for client work)
      * ``P2`` — committed for the quarter; backlog
    """
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    asset_type: Mapped[AssetType] = mapped_column(Enum(AssetType), nullable=False)
    status: Mapped[AssetStatus] = mapped_column(Enum(AssetStatus), default=AssetStatus.draft)
    rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    assignee_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    # "Who internally signs off on the cut" — producer who runs review. Distinct
    # from assignee (who's actively working). Set by producer when assigning the task.
    reviewer_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    folder_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("folders.id"), nullable=True, index=True)
    due_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    priority: Mapped[Optional[AssetPriority]] = mapped_column(Enum(AssetPriority), nullable=True, index=True)
    phase: Mapped[AssetPhase] = mapped_column(Enum(AssetPhase), nullable=False, default=AssetPhase.internal, index=True)
    # Phase transition tracking — filled when phase advances. Used by the share
    # viewer to filter versions+comments for guest views (only items >= these
    # timestamps + the baseline version are visible). See models/share.py.
    phase_client_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    phase_delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    client_baseline_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("asset_versions.id"), nullable=True)
    delivered_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("asset_versions.id"), nullable=True)
    block_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Video-specific custom fields (format/goal/source/style/talent) — validated
    # against schemas/video_fields.py. JSON for flexibility; columns above for
    # cross-cutting filtering (priority, phase, assignee, reviewer).
    custom_fields: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True, default=dict)
    keywords: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_assets_project_folder_deleted", "project_id", "folder_id", "deleted_at"),
    )

class AssetVersion(Base):
    __tablename__ = "asset_versions"
    __table_args__ = (UniqueConstraint("asset_id", "version_number", name="uq_asset_versions_asset_version"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assets.id"), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    processing_status: Mapped[ProcessingStatus] = mapped_column(Enum(ProcessingStatus), default=ProcessingStatus.uploading)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

class FileType(str, PyEnum):
    image = "image"
    audio = "audio"
    video = "video"
    document = "document"

class MediaFile(Base):
    __tablename__ = "media_files"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("asset_versions.id"), nullable=False, index=True)
    file_type: Mapped[FileType] = mapped_column(Enum(FileType), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    s3_key_raw: Mapped[str] = mapped_column(String(1000), nullable=False)
    s3_key_processed: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    s3_key_thumbnail: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sequence_order: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class CarouselItem(Base):
    __tablename__ = "carousel_items"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("asset_versions.id"), nullable=False)
    media_file_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("media_files.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)

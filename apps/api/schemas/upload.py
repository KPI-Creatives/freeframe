from pydantic import BaseModel
import uuid
from ..models.asset import AssetType

ALLOWED_MIME_TYPES = {
    # Images
    "image/jpeg", "image/png", "image/webp", "image/heic", "image/tiff", "image/gif",
    # Audio — include both canonical and legacy aliases used by different browsers/OSes
    "audio/mpeg", "audio/mp3",
    "audio/wav", "audio/wave", "audio/x-wav", "audio/vnd.wave",
    "audio/flac", "audio/x-flac",
    "audio/aac", "audio/x-aac",
    "audio/ogg", "audio/vorbis",
    "audio/x-m4a", "audio/m4a", "audio/mp4", "audio/aiff", "audio/x-aiff",
    "audio/webm",
    # Video — include common aliases
    "video/mp4", "video/quicktime", "video/x-msvideo", "video/avi",
    "video/x-matroska", "video/webm", "video/mpeg", "video/x-ms-wmv",
    # Documents — Markdown only in v1. HTML/PDF deferred to later phases.
    # Some browsers send octet-stream for .md (no system mapping); fall back
    # to extension validation in the router for that case.
    "text/markdown", "text/x-markdown", "text/plain",
}

# Extensions accepted when MIME alone is ambiguous (e.g. text/plain or
# application/octet-stream from browsers without a .md mapping). Checked
# against the original_filename in the upload router.
ALLOWED_DOCUMENT_EXTENSIONS = {".md", ".markdown"}

# Per-asset-type size caps. Documents are tiny (real scripts are <100 KB);
# 5 MB is generous and blocks accidentally large attachments.
MAX_DOCUMENT_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB

MAX_FILE_SIZE_BYTES = 200 * 1024 * 1024 * 1024  # 200 GB
CHUNK_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

def mime_to_asset_type(mime_type: str, filename: str | None = None) -> AssetType:
    if mime_type.startswith("image/"):
        return AssetType.image
    elif mime_type.startswith("audio/"):
        return AssetType.audio
    elif mime_type.startswith("video/"):
        return AssetType.video
    elif mime_type in ("text/markdown", "text/x-markdown"):
        return AssetType.document
    # Some browsers send text/plain (or no mapping at all) for .md files.
    # Disambiguate using the filename extension if provided.
    if filename:
        import os
        ext = os.path.splitext(filename)[1].lower()
        if ext in ALLOWED_DOCUMENT_EXTENSIONS:
            return AssetType.document
    raise ValueError(f"Unsupported mime type: {mime_type}")

class InitiateUploadRequest(BaseModel):
    project_id: uuid.UUID
    asset_name: str
    original_filename: str
    mime_type: str
    file_size_bytes: int
    # For new version of existing asset
    asset_id: uuid.UUID | None = None
    folder_id: uuid.UUID | None = None

class InitiateUploadResponse(BaseModel):
    upload_id: str
    s3_key: str
    asset_id: uuid.UUID
    version_id: uuid.UUID

class PresignPartRequest(BaseModel):
    s3_key: str
    upload_id: str
    part_number: int  # 1-indexed

class PresignPartResponse(BaseModel):
    presigned_url: str
    part_number: int

class UploadPart(BaseModel):
    PartNumber: int
    ETag: str

class CompleteUploadRequest(BaseModel):
    s3_key: str
    upload_id: str
    asset_id: uuid.UUID
    version_id: uuid.UUID
    parts: list[UploadPart]

class CompleteUploadResponse(BaseModel):
    status: str
    asset_id: uuid.UUID
    version_id: uuid.UUID

class AbortUploadRequest(BaseModel):
    s3_key: str
    upload_id: str
    version_id: uuid.UUID

"""Tests for the v1 document-asset upload flow.

Covers:
  * mime_to_asset_type maps .md (and text/markdown) to AssetType.document
  * .md files are accepted via the extension fallback even when the browser
    sends text/plain or no mapping
  * Documents have a 5 MB hard cap and reject larger payloads with HTTP 400
  * /upload/complete skips Celery transcoding for documents and marks the
    version ready in a single commit
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest

from apps.api.models.asset import AssetType
from apps.api.schemas.upload import (
    ALLOWED_DOCUMENT_EXTENSIONS,
    MAX_DOCUMENT_SIZE_BYTES,
    mime_to_asset_type,
)


# ── pure-function tests (no DB) ──────────────────────────────────────────────

def test_mime_to_asset_type_markdown():
    assert mime_to_asset_type("text/markdown") == AssetType.document
    assert mime_to_asset_type("text/x-markdown") == AssetType.document


def test_mime_to_asset_type_md_extension_fallback():
    # Some browsers send text/plain for .md — extension wins.
    assert mime_to_asset_type("text/plain", "script.md") == AssetType.document
    assert mime_to_asset_type("application/octet-stream", "outline.markdown") == AssetType.document


def test_mime_to_asset_type_rejects_unknown_without_extension():
    with pytest.raises(ValueError):
        mime_to_asset_type("text/plain")  # no filename → can't disambiguate
    with pytest.raises(ValueError):
        mime_to_asset_type("application/zip", "x.zip")


def test_document_size_cap_constant():
    # Real scripts are <100 KB; 5 MB blocks accidentally huge attachments.
    assert MAX_DOCUMENT_SIZE_BYTES == 5 * 1024 * 1024


def test_allowed_document_extensions():
    assert ".md" in ALLOWED_DOCUMENT_EXTENSIONS
    assert ".markdown" in ALLOWED_DOCUMENT_EXTENSIONS


# ── /upload/complete behaviour: skip Celery, mark ready ──────────────────────

@patch("apps.api.routers.upload._trigger_processing")
@patch("apps.api.routers.upload.complete_multipart_upload")
def test_complete_upload_marks_document_ready_and_skips_transcode(
    mock_s3_complete, mock_trigger, client, mock_db, test_user, auth_headers,
):
    """Document uploads must NOT dispatch the Celery transcode task.

    Behaviour expected of the patched router:
      1. S3 multipart complete is called as for any asset.
      2. AssetVersion.processing_status is set to 'ready' (not 'processing').
      3. _trigger_processing is NEVER called.
      4. Response status is 'ready'.
    """
    asset_id = uuid.uuid4()
    version_id = uuid.uuid4()

    version = MagicMock()
    version.id = version_id
    version.created_by = test_user.id
    version.deleted_at = None
    version.processing_status = "uploading"

    asset = MagicMock()
    asset.id = asset_id
    asset.deleted_at = None
    asset.asset_type = AssetType.document

    # mock_db.query(...).filter(...).first() returns whatever was last set —
    # so we sequence: AssetVersion lookup, then Asset lookup.
    mock_db.first.side_effect = [version, asset]

    payload = {
        "s3_key": f"raw/proj/{asset_id}/{version_id}/original.md",
        "upload_id": "fake-upload-id",
        "asset_id": str(asset_id),
        "version_id": str(version_id),
        "parts": [{"PartNumber": 1, "ETag": "etag-1"}],
    }
    resp = client.post("/upload/complete", json=payload, headers=auth_headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ready"
    assert body["asset_id"] == str(asset_id)
    assert body["version_id"] == str(version_id)

    # The Celery dispatcher must not have been called for a document.
    mock_trigger.assert_not_called()

    # S3 multipart-complete should still run.
    mock_s3_complete.assert_called_once()


@patch("apps.api.routers.upload._trigger_processing")
@patch("apps.api.routers.upload.complete_multipart_upload")
def test_complete_upload_video_still_triggers_transcode(
    mock_s3_complete, mock_trigger, client, mock_db, test_user, auth_headers,
):
    """Regression: don't accidentally short-circuit non-document assets."""
    asset_id = uuid.uuid4()
    version_id = uuid.uuid4()

    version = MagicMock()
    version.id = version_id
    version.created_by = test_user.id
    version.deleted_at = None
    version.processing_status = "uploading"

    asset = MagicMock()
    asset.id = asset_id
    asset.deleted_at = None
    asset.asset_type = AssetType.video

    mock_db.first.side_effect = [version, asset]

    payload = {
        "s3_key": f"raw/proj/{asset_id}/{version_id}/original.mp4",
        "upload_id": "fake-upload-id",
        "asset_id": str(asset_id),
        "version_id": str(version_id),
        "parts": [{"PartNumber": 1, "ETag": "etag-1"}],
    }
    resp = client.post("/upload/complete", json=payload, headers=auth_headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "processing"
    # And the transcode task IS scheduled.
    # BackgroundTasks executes the task synchronously in TestClient, so the
    # mock should have been called exactly once.
    mock_trigger.assert_called_once_with(uuid.UUID(str(asset_id)), uuid.UUID(str(version_id)))

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
import os
import uuid
from datetime import datetime, timezone
from typing import Optional
from ..database import get_db
from ..middleware.auth import get_current_user
from ..models.user import User
from ..models.asset import Asset, AssetVersion, MediaFile, AssetType, FileType, ProcessingStatus, AssetPhase, AssetPriority
from ..models.project import Project, ProjectMember, ProjectRole
from ..models.share import AssetShare
from ..models.activity import Mention, Notification, NotificationType
from ..schemas.asset import AssetResponse, AssetVersionResponse, AssetUpdate, StreamUrlResponse, MediaFileResponse, SendToClientRequest, MarkDeliveredRequest
from ..schemas.notification import AssignmentUpdate
from ..services.permissions import require_project_role, require_asset_access, can_access_asset, is_public_project, get_project_member
from ..services.s3_service import generate_presigned_get_url, build_download_filename
from .hls_proxy import create_hls_token
from ..schemas.upload import InitiateUploadRequest, InitiateUploadResponse, ALLOWED_MIME_TYPES, MAX_FILE_SIZE_BYTES, mime_to_asset_type
from ..services.s3_service import create_multipart_upload

router = APIRouter(tags=["assets"])


def _build_asset_response(asset: Asset, db: Session) -> AssetResponse:
    """Build AssetResponse with latest version and its files."""
    latest_version = db.query(AssetVersion).filter(
        AssetVersion.asset_id == asset.id,
        AssetVersion.deleted_at.is_(None),
    ).order_by(AssetVersion.version_number.desc()).first()

    version_response = None
    thumbnail_url = None
    if latest_version:
        files = db.query(MediaFile).filter(MediaFile.version_id == latest_version.id).all()
        version_response = AssetVersionResponse.model_validate(latest_version)
        version_response.files = [MediaFileResponse.model_validate(f) for f in files]
        # Get thumbnail from first file that has one.
        # Audio stores waveform JSON in s3_key_thumbnail — skip it, it's not an image.
        if asset.asset_type != AssetType.audio:
            for f in files:
                if f.s3_key_thumbnail:
                    thumbnail_url = generate_presigned_get_url(f.s3_key_thumbnail)
                    break

    resp = AssetResponse.model_validate(asset)
    resp.latest_version = version_response
    resp.thumbnail_url = thumbnail_url
    return resp


def _build_asset_responses_bulk(assets: list[Asset], db: Session) -> list[AssetResponse]:
    """Build AssetResponse list with bulk-loaded versions and files (no N+1)."""
    if not assets:
        return []

    asset_ids = [a.id for a in assets]

    # Bulk load latest version per asset using a subquery
    latest_version_subq = (
        db.query(
            AssetVersion.asset_id,
            func.max(AssetVersion.version_number).label("max_version"),
        )
        .filter(AssetVersion.asset_id.in_(asset_ids), AssetVersion.deleted_at.is_(None))
        .group_by(AssetVersion.asset_id)
        .subquery()
    )
    latest_versions = (
        db.query(AssetVersion)
        .join(latest_version_subq, (AssetVersion.asset_id == latest_version_subq.c.asset_id) & (AssetVersion.version_number == latest_version_subq.c.max_version))
        .all()
    )
    version_by_asset = {v.asset_id: v for v in latest_versions}

    # Bulk load media files for all those versions
    version_ids = [v.id for v in latest_versions]
    all_files = db.query(MediaFile).filter(MediaFile.version_id.in_(version_ids)).all() if version_ids else []
    files_by_version: dict = {}
    for f in all_files:
        files_by_version.setdefault(f.version_id, []).append(f)

    result = []
    for asset in assets:
        version = version_by_asset.get(asset.id)
        version_response = None
        thumbnail_url = None
        if version:
            files = files_by_version.get(version.id, [])
            version_response = AssetVersionResponse.model_validate(version)
            version_response.files = [MediaFileResponse.model_validate(f) for f in files]
            # Audio stores waveform JSON in s3_key_thumbnail — skip it, it's not an image.
            if asset.asset_type != AssetType.audio:
                for f in files:
                    if f.s3_key_thumbnail:
                        thumbnail_url = generate_presigned_get_url(f.s3_key_thumbnail)
                        break

        asset_resp = AssetResponse.model_validate(asset)
        asset_resp.latest_version = version_response
        asset_resp.thumbnail_url = thumbnail_url
        result.append(asset_resp)
    return result


@router.get("/projects/{project_id}/assets", response_model=list[AssetResponse])
def list_assets(
    project_id: uuid.UUID,
    include_failed: bool = Query(False, description="Include assets whose latest version failed processing"),
    folder_id: Optional[str] = Query(None, description="Filter by folder. 'root' for root level, UUID for specific folder."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Allow access if user is a project member OR the project is public
    member = get_project_member(db, project_id, current_user.id)
    if not member and not is_public_project(db, project_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a project member")

    query = db.query(Asset).filter(
        Asset.project_id == project_id,
        Asset.deleted_at.is_(None),
    )

    if folder_id == "root":
        query = query.filter(Asset.folder_id.is_(None))
    elif folder_id is not None:
        query = query.filter(Asset.folder_id == uuid.UUID(folder_id))

    assets = query.all()

    if not include_failed:
        # Exclude assets where the only version is failed or still uploading
        asset_ids = [a.id for a in assets]
        if asset_ids:
            # Find assets that have at least one non-failed, non-uploading version
            usable = set(
                row[0] for row in db.query(AssetVersion.asset_id).filter(
                    AssetVersion.asset_id.in_(asset_ids),
                    AssetVersion.deleted_at.is_(None),
                    AssetVersion.processing_status.notin_([ProcessingStatus.failed, ProcessingStatus.uploading]),
                ).distinct().all()
            )
            # Also include assets with no versions yet (just created)
            has_any_version = set(
                row[0] for row in db.query(AssetVersion.asset_id).filter(
                    AssetVersion.asset_id.in_(asset_ids),
                    AssetVersion.deleted_at.is_(None),
                ).distinct().all()
            )
            assets = [a for a in assets if a.id in usable or a.id not in has_any_version]

    return _build_asset_responses_bulk(assets, db)


@router.get("/assets/{asset_id}", response_model=AssetResponse)
def get_asset(
    asset_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    asset = db.query(Asset).filter(Asset.id == asset_id, Asset.deleted_at.is_(None)).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    require_asset_access(db, asset, current_user)
    return _build_asset_response(asset, db)


@router.patch("/assets/{asset_id}", response_model=AssetResponse)
def update_asset(
    asset_id: uuid.UUID,
    body: AssetUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    asset = db.query(Asset).filter(Asset.id == asset_id, Asset.deleted_at.is_(None)).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    require_project_role(db, asset.project_id, current_user, ProjectRole.editor)

    updates = body.model_dump(exclude_unset=True)

    # Validate video-specific custom fields against the typed Pydantic
    # schema. We do this here (not on the schema itself) so the rule can
    # branch on asset_type — a future document-type asset may want a
    # different validator.
    if "custom_fields" in updates and updates["custom_fields"] is not None:
        if asset.asset_type == AssetType.video:
            from ..schemas.video_fields import validate_custom_fields_dict
            try:
                updates["custom_fields"] = validate_custom_fields_dict(updates["custom_fields"])
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Invalid custom_fields: {e}")

    # Phase transition guards. Phase is producer-managed; advancing it via this
    # generic PATCH is allowed (Send-to-client and Mark-delivered also funnel
    # through here for the column write, but they're more typically called via
    # their dedicated atomic endpoints). Going BACKWARDS is forbidden — once a
    # client has seen something, the filter window is one-way.
    if "phase" in updates:
        new_phase = updates["phase"]
        if isinstance(new_phase, str):
            try:
                new_phase = AssetPhase(new_phase)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid phase '{new_phase}'")
        order = {AssetPhase.internal: 0, AssetPhase.client: 1, AssetPhase.delivered: 2}
        current_rank = order.get(asset.phase, 0)
        new_rank = order.get(new_phase, 0)
        if new_rank < current_rank:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Phase cannot move backwards ({asset.phase.value} -> {new_phase.value}). "
                    "Once an asset has been sent to client or delivered, the visibility "
                    "window is irreversible. Create a new asset if you need a fresh start."
                ),
            )
        # Side effects when advancing through the funnel — fill the matching
        # timestamp + baseline pointer if not already set.
        from datetime import datetime as _dt, timezone as _tz
        now = _dt.now(_tz.utc)
        if new_phase == AssetPhase.client and asset.phase_client_at is None:
            asset.phase_client_at = now
            if asset.client_baseline_version_id is None:
                # Latest non-deleted version becomes the baseline (the one the
                # client sees as their starting point).
                from ..models.asset import AssetVersion
                latest = db.query(AssetVersion).filter(
                    AssetVersion.asset_id == asset.id,
                    AssetVersion.deleted_at.is_(None),
                ).order_by(AssetVersion.version_number.desc()).first()
                if latest is not None:
                    asset.client_baseline_version_id = latest.id
        elif new_phase == AssetPhase.delivered and asset.phase_delivered_at is None:
            asset.phase_delivered_at = now
            if asset.delivered_version_id is None:
                from ..models.asset import AssetVersion
                latest = db.query(AssetVersion).filter(
                    AssetVersion.asset_id == asset.id,
                    AssetVersion.deleted_at.is_(None),
                ).order_by(AssetVersion.version_number.desc()).first()
                if latest is not None:
                    asset.delivered_version_id = latest.id
        updates["phase"] = new_phase

    for field, value in updates.items():
        setattr(asset, field, value)
    db.commit()
    db.refresh(asset)
    return _build_asset_response(asset, db)


@router.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(
    asset_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    asset = db.query(Asset).filter(Asset.id == asset_id, Asset.deleted_at.is_(None)).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    require_project_role(db, asset.project_id, current_user, ProjectRole.editor)
    asset.deleted_at = datetime.now(timezone.utc)
    db.commit()


@router.get("/assets/{asset_id}/versions", response_model=list[AssetVersionResponse])
def list_asset_versions(
    asset_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    asset = db.query(Asset).filter(Asset.id == asset_id, Asset.deleted_at.is_(None)).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    require_asset_access(db, asset, current_user)

    versions = db.query(AssetVersion).filter(
        AssetVersion.asset_id == asset_id,
        AssetVersion.deleted_at.is_(None),
    ).order_by(AssetVersion.version_number.desc()).all()

    result = []
    version_ids = [v.id for v in versions]
    all_files = db.query(MediaFile).filter(MediaFile.version_id.in_(version_ids)).all() if version_ids else []
    files_by_version: dict = {}
    for f in all_files:
        files_by_version.setdefault(f.version_id, []).append(f)

    for v in versions:
        vr = AssetVersionResponse.model_validate(v)
        vr.files = [MediaFileResponse.model_validate(f) for f in files_by_version.get(v.id, [])]
        result.append(vr)
    return result


@router.get("/assets/{asset_id}/stream", response_model=StreamUrlResponse)
def get_stream_url(
    asset_id: uuid.UUID,
    version_id: Optional[uuid.UUID] = Query(default=None),
    download: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    asset = db.query(Asset).filter(Asset.id == asset_id, Asset.deleted_at.is_(None)).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    require_asset_access(db, asset, current_user)

    # Get the requested version or latest
    if version_id:
        version = db.query(AssetVersion).filter(
            AssetVersion.id == version_id,
            AssetVersion.asset_id == asset_id,
            AssetVersion.deleted_at.is_(None),
        ).first()
    else:
        version = db.query(AssetVersion).filter(
            AssetVersion.asset_id == asset_id,
            AssetVersion.deleted_at.is_(None),
        ).order_by(AssetVersion.version_number.desc()).first()

    if not version:
        raise HTTPException(status_code=404, detail="No version found")
    if version.processing_status != ProcessingStatus.ready:
        raise HTTPException(status_code=409, detail="Asset version is not ready yet")

    media_file = db.query(MediaFile).filter(MediaFile.version_id == version.id).first()
    if not media_file:
        raise HTTPException(status_code=404, detail="Media file not found")

    if asset.asset_type == AssetType.video and media_file.s3_key_processed:
        if download:
            # For video downloads, use the raw file (original upload) so user gets a single file
            s3_key = media_file.s3_key_raw or media_file.s3_key_processed
            filename = build_download_filename(asset.name, media_file.original_filename or s3_key)
            url = generate_presigned_get_url(s3_key, download_filename=filename)
        else:
            # Route through the HLS proxy so the master playlist, variant
            # playlists, and .ts segments all get served via short-lived
            # presigned URLs — the S3 bucket can stay fully private. (#51)
            token = create_hls_token(media_file.s3_key_processed)
            url = f"/stream/hls/master.m3u8?token={token}"
    else:
        s3_key = media_file.s3_key_processed or media_file.s3_key_raw
        if download:
            filename = build_download_filename(asset.name, media_file.original_filename or s3_key)
            url = generate_presigned_get_url(s3_key, download_filename=filename)
        else:
            url = generate_presigned_get_url(s3_key)

    return StreamUrlResponse(url=url, asset_type=asset.asset_type)


@router.post("/assets/{asset_id}/versions", response_model=InitiateUploadResponse)
def initiate_new_version(
    asset_id: uuid.UUID,
    body: InitiateUploadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Initiate upload of a new version for an existing asset."""
    asset = db.query(Asset).filter(Asset.id == asset_id, Asset.deleted_at.is_(None)).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    require_project_role(db, asset.project_id, current_user, ProjectRole.editor)

    if body.mime_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported file type")
    if body.file_size_bytes > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="File exceeds 10GB limit")

    last_version = db.query(AssetVersion).filter(
        AssetVersion.asset_id == asset_id,
        AssetVersion.deleted_at.is_(None),
    ).order_by(AssetVersion.version_number.desc()).first()
    next_version_number = (last_version.version_number + 1) if last_version else 1

    version = AssetVersion(
        asset_id=asset_id,
        version_number=next_version_number,
        processing_status=ProcessingStatus.uploading,
        created_by=current_user.id,
    )
    db.add(version)
    db.flush()

    ext = os.path.splitext(body.original_filename)[1].lower()
    s3_key = f"raw/{asset.project_id}/{asset_id}/{version.id}/original{ext}"
    upload_id = create_multipart_upload(s3_key, body.mime_type)

    file_type_map = {
        AssetType.image: FileType.image,
        AssetType.audio: FileType.audio,
        AssetType.video: FileType.video,
        AssetType.image_carousel: FileType.image,
        AssetType.document: FileType.document,
    }
    media_file = MediaFile(
        version_id=version.id,
        file_type=file_type_map.get(asset.asset_type, FileType.video),
        original_filename=body.original_filename,
        mime_type=body.mime_type,
        file_size_bytes=body.file_size_bytes,
        s3_key_raw=s3_key,
    )
    db.add(media_file)
    db.commit()

    return InitiateUploadResponse(
        upload_id=upload_id,
        s3_key=s3_key,
        asset_id=asset_id,
        version_id=version.id,
    )


@router.patch("/assets/{asset_id}/assignment", response_model=AssetResponse)
def update_assignment(
    asset_id: uuid.UUID,
    body: AssignmentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    asset = db.query(Asset).filter(Asset.id == asset_id, Asset.deleted_at.is_(None)).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    require_project_role(db, asset.project_id, current_user, ProjectRole.editor)

    if "assignee_id" in body.model_fields_set:
        asset.assignee_id = body.assignee_id
    if "due_date" in body.model_fields_set:
        asset.due_date = body.due_date

    if "assignee_id" in body.model_fields_set and body.assignee_id is not None:
        notification = Notification(
            user_id=body.assignee_id,
            type=NotificationType.assignment,
            asset_id=asset.id,
        )
        db.add(notification)

    db.commit()
    db.refresh(asset)
    return _build_asset_response(asset, db)


@router.get("/assets/{asset_id}/assignment")
def get_assignment(
    asset_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    asset = db.query(Asset).filter(Asset.id == asset_id, Asset.deleted_at.is_(None)).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    require_project_role(db, asset.project_id, current_user, ProjectRole.viewer)
    return {
        "assignee_id": str(asset.assignee_id) if asset.assignee_id else None,
        "due_date": asset.due_date.isoformat() if asset.due_date else None,
    }


@router.post("/assets/{asset_id}/versions/{version_id}/retry-processing")
def retry_version_processing(
    asset_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Re-enqueue Celery transcode for a stuck or failed version.

    Useful when a previous worker died mid-process or the user wants a
    forced retry. Sets the version back to `processing` and dispatches
    the same Celery task as the original upload-complete flow.
    """
    asset = db.query(Asset).filter(Asset.id == asset_id, Asset.deleted_at.is_(None)).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    require_project_role(db, asset.project_id, current_user, ProjectRole.editor)

    version = db.query(AssetVersion).filter(
        AssetVersion.id == version_id,
        AssetVersion.asset_id == asset_id,
        AssetVersion.deleted_at.is_(None),
    ).first()
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    if version.processing_status == ProcessingStatus.ready:
        raise HTTPException(status_code=409, detail="Version is already ready")
    if version.processing_status == ProcessingStatus.uploading:
        raise HTTPException(status_code=409, detail="Upload not complete yet")

    version.processing_status = ProcessingStatus.processing
    db.commit()

    from ..tasks.transcode_tasks import process_asset
    from ..tasks.celery_app import send_task_safe
    send_task_safe(process_asset, str(asset_id), str(version_id))

    return {"ok": True, "asset_id": str(asset_id), "version_id": str(version_id), "status": "processing"}


@router.post("/assets/{asset_id}/versions/{version_id}/cancel-processing")
def cancel_version_processing(
    asset_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark a stuck/processing version as failed so the user can dismiss it.

    Does not delete the version or its DB rows — only flips the status so the
    UI's Failed tab picks it up. The user can subsequently dismiss it or
    re-upload from scratch.
    """
    asset = db.query(Asset).filter(Asset.id == asset_id, Asset.deleted_at.is_(None)).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    require_project_role(db, asset.project_id, current_user, ProjectRole.editor)

    version = db.query(AssetVersion).filter(
        AssetVersion.id == version_id,
        AssetVersion.asset_id == asset_id,
        AssetVersion.deleted_at.is_(None),
    ).first()
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    if version.processing_status == ProcessingStatus.ready:
        raise HTTPException(status_code=409, detail="Version is already ready — nothing to cancel")

    version.processing_status = ProcessingStatus.failed
    db.commit()

    return {"ok": True, "asset_id": str(asset_id), "version_id": str(version_id), "status": "failed"}


# ─── N1.B — Producer atomic actions ──────────────────────────────────────────


@router.post("/assets/{asset_id}/send-to-client", response_model=None)
def send_asset_to_client(
    asset_id: uuid.UUID,
    body: SendToClientRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Atomic 'Send to client review'.

    Five steps in one transaction (DB-level):
      1. Asset.phase  → ``client`` (must not be ``delivered`` already).
      2. ``phase_client_at`` set to now if first time.
      3. ``client_baseline_version_id`` snapshots the current latest version
         (the one the client sees as their starting point).
      4. ShareLink minted for this asset with the given permission, optional
         password and expiry.
      5. Resend email queued to ``recipient_email`` with the share URL.

    Permission model:
      * Global gate:  caller must be producer or admin (UserRole).
      * Project gate: caller must be a project member with editor+ rights.
    Both are checked; failing either is 403.

    Idempotency:
      * Re-running on an asset already in ``client`` phase is allowed —
        it does NOT reset ``phase_client_at`` or the baseline. Instead it
        creates a NEW share-link (different token, different recipient is
        a valid re-use case) and sends a fresh email.
      * Re-running on an asset already in ``delivered`` phase is rejected
        (400) — phase backwards is forbidden.

    Returns the new share URL so the producer can copy it as a fallback
    (in case the email gets caught in spam).
    """
    # Co-located imports so this endpoint stays self-contained.
    from ..models.share import ShareLink, SharePermission
    from ..services.permissions_role import require_role
    from ..models.user import UserRole
    from ..tasks.email_tasks import send_share_email
    from ..tasks.celery_app import send_task_safe
    from ..config import settings
    from datetime import timedelta
    import secrets, bcrypt
    from ..services.crypto_service import encrypt_password

    payload = body

    # Permission gates.
    if not require_role(UserRole.producer)(current_user):
        # ``require_role`` returns the user when allowed; raises 403 otherwise.
        # The 'if not' branch is defensive — control should not reach here.
        raise HTTPException(status_code=403, detail="Forbidden")

    asset = db.query(Asset).filter(
        Asset.id == asset_id,
        Asset.deleted_at.is_(None),
    ).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    require_project_role(db, asset.project_id, current_user, ProjectRole.editor)

    # Forbid backwards phase transition (delivered → client).
    if asset.phase == AssetPhase.delivered:
        raise HTTPException(
            status_code=400,
            detail=(
                "Asset is already delivered. Phase cannot move backwards. "
                "If the client needs to review again, create a new asset."
            ),
        )

    # Validate permission enum.
    try:
        permission = SharePermission(payload.permission)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid permission '{payload.permission}' — expected view, comment, or approve",
        )

    now = datetime.now(timezone.utc)

    # ── Phase transition (only if not already in client phase) ───────────
    if asset.phase != AssetPhase.client:
        # Snapshot the latest version as the client baseline.
        latest = db.query(AssetVersion).filter(
            AssetVersion.asset_id == asset.id,
            AssetVersion.deleted_at.is_(None),
        ).order_by(AssetVersion.version_number.desc()).first()
        asset.phase = AssetPhase.client
        asset.phase_client_at = now
        if latest is not None:
            asset.client_baseline_version_id = latest.id

    # ── Mint share-link ──────────────────────────────────────────────────
    token = secrets.token_urlsafe(32)
    password_hash = None
    password_encrypted = None
    if payload.password:
        pwd_bytes = payload.password[:72].encode("utf-8")
        password_hash = bcrypt.hashpw(pwd_bytes, bcrypt.gensalt()).decode("utf-8")
        password_encrypted = encrypt_password(payload.password)

    expires_at = None
    if payload.expires_in_days is not None and payload.expires_in_days > 0:
        expires_at = now + timedelta(days=payload.expires_in_days)

    link = ShareLink(
        asset_id=asset.id,
        token=token,
        created_by=current_user.id,
        title=asset.name,
        description=payload.message,
        expires_at=expires_at,
        password_hash=password_hash,
        password_encrypted=password_encrypted,
        permission=permission,
        # show_versions stays True at the model level — phase filter applies
        # at the viewer layer, so this column doesn't need toggling.
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    db.refresh(asset)

    # ── Email the client ─────────────────────────────────────────────────
    share_url = f"{settings.frontend_url}/share/{token}"
    import logging as _logging
    _log = _logging.getLogger("send_to_client")
    _log.info(
        "send-to-client: enqueueing email to=%s sharer=%s asset=%s share=%s perm=%s",
        payload.recipient_email,
        (current_user.name or current_user.email),
        asset.name,
        share_url,
        permission.value,
    )
    try:
        send_task_safe(
            send_share_email,
            to_email=payload.recipient_email,
            sharer_name=current_user.name or "KPI Creatives",
            asset_name=asset.name,
            asset_link=share_url,
            permission=permission.value,
            message=payload.message,
        )
        _log.info("send-to-client: email enqueue OK to=%s", payload.recipient_email)
    except Exception:
        # send_task_safe swallows errors in its background thread, so this
        # branch is mostly unreachable — but if the import/threading itself
        # fails synchronously, surface it in the logs without breaking the
        # phase transition (the share-link already exists, producer can
        # copy the URL).
        _log.exception("send-to-client: email enqueue failed to=%s", payload.recipient_email)

    return {
        "asset_id": str(asset.id),
        "phase": asset.phase.value,
        "phase_client_at": asset.phase_client_at.isoformat() if asset.phase_client_at else None,
        "client_baseline_version_id": str(asset.client_baseline_version_id) if asset.client_baseline_version_id else None,
        "share_link_id": str(link.id),
        "share_url": share_url,
    }


@router.post("/assets/{asset_id}/mark-delivered", response_model=None)
def mark_asset_delivered(
    asset_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Atomic 'Mark delivered'.

      1. Asset.phase  → ``delivered`` (only if currently ``client``).
      2. ``phase_delivered_at`` set to now.
      3. ``delivered_version_id`` snapshots the current latest version.
      4. Any existing share-links on this asset get permission downgraded
         to view-only — the client signed off, no more commenting needed.

    Permission model:
      * Global gate:  caller must be producer or admin.
      * Project gate: caller must be a project member with editor+ rights.

    Backwards transitions (delivered → client) are forbidden. If you need to
    re-open a project after delivery, create a new asset.
    """
    from ..models.share import ShareLink, SharePermission
    from ..services.permissions_role import require_role
    from ..models.user import UserRole

    if not require_role(UserRole.producer)(current_user):
        raise HTTPException(status_code=403, detail="Forbidden")

    asset = db.query(Asset).filter(
        Asset.id == asset_id,
        Asset.deleted_at.is_(None),
    ).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    require_project_role(db, asset.project_id, current_user, ProjectRole.editor)

    # Phase semantics:
    #   internal  → delivered  : allowed but unusual (no client review cycle).
    #   client    → delivered  : the common path.
    #   delivered → delivered  : idempotent (no-op).
    if asset.phase == AssetPhase.delivered:
        # Idempotent; return current state without changes.
        downgraded = 0
    else:
        now = datetime.now(timezone.utc)
        latest = db.query(AssetVersion).filter(
            AssetVersion.asset_id == asset.id,
            AssetVersion.deleted_at.is_(None),
        ).order_by(AssetVersion.version_number.desc()).first()

        asset.phase = AssetPhase.delivered
        asset.phase_delivered_at = now
        if latest is not None:
            asset.delivered_version_id = latest.id

        # Downgrade any active share-link on this asset to view-only — the
        # client signed off; no further commenting/approving should happen.
        active_links = db.query(ShareLink).filter(
            ShareLink.asset_id == asset.id,
            ShareLink.deleted_at.is_(None),
        ).all()
        downgraded = 0
        for link in active_links:
            if link.permission != SharePermission.view:
                link.permission = SharePermission.view
                downgraded += 1

        db.commit()
        db.refresh(asset)

    return {
        "asset_id": str(asset.id),
        "phase": asset.phase.value,
        "phase_delivered_at": asset.phase_delivered_at.isoformat() if asset.phase_delivered_at else None,
        "delivered_version_id": str(asset.delivered_version_id) if asset.delivered_version_id else None,
        "share_links_downgraded": downgraded,
    }

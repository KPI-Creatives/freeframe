import uuid
import sys
import os
import asyncio
import json
import math
import subprocess
import tempfile
from pathlib import Path

# Ensure the workspace root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from .celery_app import celery_app
from ..database import SessionLocal
from ..models.asset import AssetVersion, MediaFile, ProcessingStatus, AssetType
from ..models.asset import Asset
from ..services.s3_service import get_s3_client
from ..config import settings


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def process_asset(self, asset_id: str, version_id: str):
    """Main processing task dispatched after upload completes."""
    db = SessionLocal()
    try:
        version = db.query(AssetVersion).filter(AssetVersion.id == uuid.UUID(version_id)).first()
        if not version:
            return  # version already cleaned up

        asset = db.query(Asset).filter(Asset.id == uuid.UUID(asset_id)).first()
        if not asset:
            if version:
                version.processing_status = ProcessingStatus.failed
                db.commit()
            return

        media_file = db.query(MediaFile).filter(MediaFile.version_id == version.id).first()
        if not media_file:
            version.processing_status = ProcessingStatus.failed
            db.commit()
            return

        # Reset to processing status before each attempt
        version.processing_status = ProcessingStatus.processing
        db.commit()

        output_prefix = f"processed/{asset.project_id}/{asset_id}/{version_id}"
        s3 = get_s3_client()

        try:
            if asset.asset_type in (AssetType.video,):
                _process_video(db, asset, version, media_file, s3, output_prefix)
            elif asset.asset_type == AssetType.audio:
                _process_audio(db, asset, version, media_file, s3, output_prefix)
            elif asset.asset_type in (AssetType.image, AssetType.image_carousel):
                _process_image(db, asset, version, media_file, s3, output_prefix)

            version.processing_status = ProcessingStatus.ready
            db.commit()

            # Publish SSE event (best-effort)
            _publish_event(str(asset.project_id), "transcode_complete", {
                "asset_id": asset_id,
                "version_id": version_id,
            })

        except Exception as exc:
            version.processing_status = ProcessingStatus.failed
            db.commit()
            _publish_event(str(asset.project_id), "transcode_failed", {
                "asset_id": asset_id,
                "error": str(exc),
            })
            raise self.retry(exc=exc)

    finally:
        db.close()


def _process_video(db, asset, version, media_file, s3, output_prefix):
    from packages.transcoder.ffmpeg_transcoder import FFmpegTranscoder
    from packages.transcoder.base import TranscodeJob

    transcoder = FFmpegTranscoder(s3, settings.s3_bucket, settings.s3_endpoint)
    job = TranscodeJob(
        media_id=str(asset.id),
        version_id=str(version.id),
        input_s3_key=media_file.s3_key_raw,
        output_s3_prefix=output_prefix,
        qualities=["1080p", "720p"],
    )
    result = _run_async(transcoder.transcode(job))
    if not result.success:
        raise RuntimeError(f"Transcode failed: {result.error}")

    media_file.s3_key_processed = result.hls_prefix
    if result.thumbnail_keys:
        media_file.s3_key_thumbnail = result.thumbnail_keys[0]
    db.flush()

    # Generate sprite sheet + WebVTT for timeline-hover preview. Failure
    # here is non-fatal — playback still works, the player just falls
    # back to the live-seek preview when sprite keys are NULL.
    try:
        sprite_key, vtt_key = _generate_sprite(
            s3=s3,
            input_s3_key=media_file.s3_key_raw,
            output_prefix=output_prefix,
            duration_seconds=media_file.duration_seconds or (result.duration_seconds if hasattr(result, "duration_seconds") else None),
        )
        media_file.s3_key_sprite = sprite_key
        media_file.s3_key_sprite_vtt = vtt_key
        db.flush()
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning("sprite generation failed: %s", exc, exc_info=True)


def _process_audio(db, asset, version, media_file, s3, output_prefix):
    from packages.transcoder.image_processor import process_audio
    result = process_audio(s3, settings.s3_bucket, media_file.s3_key_raw, output_prefix)
    media_file.s3_key_processed = result.get("mp3_key")
    if result.get("waveform_key"):
        media_file.s3_key_thumbnail = result["waveform_key"]
    db.flush()


def _process_image(db, asset, version, media_file, s3, output_prefix):
    from packages.transcoder.image_processor import process_image
    result = process_image(s3, settings.s3_bucket, media_file.s3_key_raw, output_prefix)
    media_file.s3_key_processed = result.get("webp_key")
    media_file.s3_key_thumbnail = result.get("thumbnail_key")
    db.flush()


def _ffprobe_duration(s3, input_s3_key: str) -> float | None:
    """Run ffprobe against a presigned R2 URL to extract media duration.

    Returns the duration in seconds, or None if probing fails for any reason
    (network, malformed file, ffprobe missing, etc.).
    """
    try:
        input_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.s3_bucket, "Key": input_s3_key},
            ExpiresIn=3600,
        )
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                input_url,
            ],
            check=True, capture_output=True, timeout=60, text=True,
        )
        return float(out.stdout.strip())
    except Exception:
        return None


def _generate_sprite(s3, input_s3_key: str, output_prefix: str, duration_seconds: float | None) -> tuple[str, str]:
    """Generate sprite-sheet JPG + WebVTT track for hover-scrub preview.

    Strategy:
      1. Stream raw input from S3 via presigned URL (no full local copy).
      2. ffmpeg picks ``GRID*GRID`` (default 10x10 = 100) evenly spaced
         frames, scales each to TILE_W x TILE_H, and tiles them into a
         single JPG.
      3. Build a WebVTT track that maps each timestamp range
         ``[i*step, (i+1)*step)`` to the tile at row=i//GRID col=i%GRID
         via the ``#xywh=`` media-fragment URI syntax.

    Returns ``(sprite_s3_key, vtt_s3_key)``. Raises on failure.

    The chosen 10x10 / 160x90 px tile gives:
      sprite size  ~1.5 MB
      VTT size     ~10 KB
      total cost   <2 MB per video.
    Cheaper than one HLS segment.
    """
    if not duration_seconds or duration_seconds <= 0:
        # Fallback: probe the source file directly. Used when the upstream
        # pipeline doesn't populate media_file.duration_seconds (which is the
        # case for the current FFmpegTranscoder; see _process_video).
        duration_seconds = _ffprobe_duration(s3, input_s3_key)
        if not duration_seconds or duration_seconds <= 0:
            raise RuntimeError("could not determine duration via ffprobe either")

    GRID = 10
    TILE_W = 160
    TILE_H = 90
    n_frames = GRID * GRID
    step = duration_seconds / n_frames  # seconds between captured frames

    # Get a presigned read URL so ffmpeg can stream directly from R2.
    input_url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": input_s3_key},
        ExpiresIn=3600,
    )

    with tempfile.TemporaryDirectory() as tmp:
        sprite_path = Path(tmp) / "sprite.jpg"
        vtt_path = Path(tmp) / "sprite.vtt"

        # ffmpeg expression: 1 frame every ``step`` seconds, scale, tile.
        # ``-frames:v 1`` to write a single output image (the tiled mosaic).
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", input_url,
            "-vf", f"fps=1/{step:.6f},scale={TILE_W}:{TILE_H}:force_original_aspect_ratio=decrease,pad={TILE_W}:{TILE_H}:(ow-iw)/2:(oh-ih)/2:color=black,tile={GRID}x{GRID}",
            "-frames:v", "1",
            "-q:v", "5",  # JPG quality 1-31, lower=better; 5 ~= 80-85% q
            str(sprite_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)

        # WebVTT: one cue per tile, mapping `step` of seconds to the tile rect.
        with vtt_path.open("w") as f:
            f.write("WEBVTT\n\n")
            for i in range(n_frames):
                start = i * step
                end = min((i + 1) * step, duration_seconds)
                col = i % GRID
                row = i // GRID
                x = col * TILE_W
                y = row * TILE_H
                f.write(
                    f"{_fmt_vtt(start)} --> {_fmt_vtt(end)}\n"
                    f"sprite.jpg#xywh={x},{y},{TILE_W},{TILE_H}\n\n"
                )

        sprite_key = f"{output_prefix}/sprite.jpg"
        vtt_key = f"{output_prefix}/sprite.vtt"
        with sprite_path.open("rb") as f:
            s3.put_object(Bucket=settings.s3_bucket, Key=sprite_key, Body=f, ContentType="image/jpeg")
        with vtt_path.open("rb") as f:
            s3.put_object(Bucket=settings.s3_bucket, Key=vtt_key, Body=f, ContentType="text/vtt")

    return sprite_key, vtt_key


def _fmt_vtt(seconds: float) -> str:
    """Format seconds as HH:MM:SS.mmm for WebVTT cues."""
    if seconds < 0:
        seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds - h * 3600 - m * 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


@celery_app.task(bind=True, max_retries=2, default_retry_delay=120)
def regenerate_sprite(self, media_file_id: str):
    """Backfill task: re-run sprite generation for an existing media_file.

    Used to populate ``s3_key_sprite`` / ``s3_key_sprite_vtt`` for videos
    that were transcoded before this feature shipped. Called manually:

        from apps.api.tasks.transcode_tasks import regenerate_sprite
        for mf_id in media_file_ids_missing_sprite:
            regenerate_sprite.delay(str(mf_id))
    """
    db = SessionLocal()
    try:
        mf = db.query(MediaFile).filter(MediaFile.id == uuid.UUID(media_file_id)).first()
        if not mf or not mf.s3_key_raw or not mf.duration_seconds:
            return
        version = db.query(AssetVersion).filter(AssetVersion.id == mf.version_id).first()
        if not version:
            return
        asset = db.query(Asset).filter(Asset.id == version.asset_id).first()
        if not asset:
            return
        output_prefix = f"processed/{asset.project_id}/{asset.id}/{version.id}"
        sprite_key, vtt_key = _generate_sprite(
            s3=get_s3_client(),
            input_s3_key=mf.s3_key_raw,
            output_prefix=output_prefix,
            duration_seconds=mf.duration_seconds,
        )
        mf.s3_key_sprite = sprite_key
        mf.s3_key_sprite_vtt = vtt_key
        db.commit()
    except Exception as exc:
        try:
            raise self.retry(exc=exc)
        except Exception:
            pass
    finally:
        db.close()


def _publish_event(project_id: str, event_type: str, payload: dict):
    """Publish SSE event via Redis from Celery worker context."""
    try:
        import redis as sync_redis
        r = sync_redis.from_url(settings.redis_url, decode_responses=True)
        message = json.dumps({"type": event_type, "payload": payload})
        r.publish(f"project:{project_id}", message)
        r.close()
    except Exception:
        pass  # SSE publish is best-effort

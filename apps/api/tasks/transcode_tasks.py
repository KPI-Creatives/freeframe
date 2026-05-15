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
            hls_prefix=result.hls_prefix,
            output_prefix=output_prefix,
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


def _list_hls_playlist(s3, hls_prefix: str) -> tuple[str, str]:
    """Find a playlist.m3u8 under the processed HLS prefix.

    Prefers 720p over 1080p (faster decode), falls back to whatever exists.
    Returns (playlist_key, segment_prefix) where segment_prefix is the
    parent S3 prefix of segment files referenced in the playlist.
    """
    # FFmpegTranscoder writes one subdir per -map index (via -var_stream_map
    # v:0,a:0 v:1,a:1 ...), not per quality name. With the qualities list
    # ["1080p", "720p"] (or ["1080p", "720p", "360p"] on legacy files),
    # index 1 is always 720p — that's the sweet spot for sprite decode
    # (small segments, fast). Fall back through 0 (1080p) and 2 (360p)
    # in case the file was transcoded with a different ladder.
    candidates = [
        f"{hls_prefix.rstrip('/')}/1/playlist.m3u8",
        f"{hls_prefix.rstrip('/')}/0/playlist.m3u8",
        f"{hls_prefix.rstrip('/')}/2/playlist.m3u8",
    ]
    for key in candidates:
        try:
            s3.head_object(Bucket=settings.s3_bucket, Key=key)
            return key, key.rsplit("/", 1)[0]
        except Exception:
            continue
    raise RuntimeError(f"no HLS playlist found under {hls_prefix}")


def _generate_sprite(s3, hls_prefix: str, output_prefix: str) -> tuple[str, str]:
    """Generate sprite-sheet JPG + WebVTT track for hover-scrub preview.

    Strategy (HLS-based):
      1. Locate a processed HLS playlist (720p preferred — fast decode).
      2. Parse EXTINF entries to derive segment list + total duration.
      3. Download all segments locally (small, ~250 KB each, edge-cached).
      4. Run ffmpeg on the local playlist to extract evenly-spaced frames
         and tile them into a single JPG.
      5. Build a WebVTT track mapping each timestamp range to its tile via
         the `#xywh=` media-fragment URI syntax.

    Why HLS instead of raw input: raw MP4 can be 1-10 GB and lives behind
    CloudFront; the old code timed out at 600s on long files because the
    whole stream had to be re-read end-to-end through the CDN. HLS segments
    are pre-encoded for fast random-access decode (2-second GOP, 720p), so
    the same operation runs in 5-30s.

    Returns ``(sprite_s3_key, vtt_s3_key)``. Raises on failure.
    """
    GRID = 10
    TILE_W = 160
    TILE_H = 90
    n_frames = GRID * GRID

    playlist_key, segment_s3_prefix = _list_hls_playlist(s3, hls_prefix)
    playlist_text = s3.get_object(Bucket=settings.s3_bucket, Key=playlist_key)["Body"].read().decode()

    durations = []
    segments = []
    for line in playlist_text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF:"):
            # format: #EXTINF:<duration>,<title>
            durations.append(float(line.split(":", 1)[1].split(",", 1)[0]))
        elif line and not line.startswith("#"):
            segments.append(line)

    if not segments or not durations:
        raise RuntimeError(f"playlist {playlist_key} has no segments")

    total_duration = sum(durations)
    if total_duration <= 0:
        raise RuntimeError(f"playlist {playlist_key} reports zero duration")

    step = total_duration / n_frames

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # 1. Download every segment locally (preserves relative names so the
        #    playlist resolves correctly when ffmpeg reads it).
        for seg_name in segments:
            seg_key = f"{segment_s3_prefix}/{seg_name}"
            local = tmp_path / seg_name
            local.parent.mkdir(parents=True, exist_ok=True)
            with local.open("wb") as f:
                f.write(s3.get_object(Bucket=settings.s3_bucket, Key=seg_key)["Body"].read())

        # 2. Write a local copy of the playlist next to the segments.
        local_playlist = tmp_path / "playlist.m3u8"
        local_playlist.write_text(playlist_text)

        sprite_path = tmp_path / "sprite.jpg"
        vtt_path = tmp_path / "sprite.vtt"

        # 3. Tile.
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-allowed_extensions", "ALL",
            "-i", str(local_playlist),
            "-vf", f"fps=1/{step:.6f},scale={TILE_W}:{TILE_H}:force_original_aspect_ratio=decrease,pad={TILE_W}:{TILE_H}:(ow-iw)/2:(oh-ih)/2:color=black,tile={GRID}x{GRID}",
            "-frames:v", "1",
            "-q:v", "5",
            str(sprite_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)

        # 4. Build VTT.
        with vtt_path.open("w") as f:
            f.write("WEBVTT\n\n")
            for i in range(n_frames):
                start = i * step
                end = min((i + 1) * step, total_duration)
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
        if not mf or not mf.s3_key_processed:
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
            hls_prefix=mf.s3_key_processed,
            output_prefix=output_prefix,
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

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional
import boto3
from botocore.config import Config
from .base import BaseTranscoder, TranscodeJob, TranscodeResult, VideoMetadata


# (scale_filter, target_height, crf)
# CRF stays the same — preset controls speed, CRF controls quality.
QUALITY_MAP = {
    "1080p": ("1920:1080", 1080, 20),
    "720p":  ("1280:720",  720,  22),
    "360p":  ("640:360",   360,  26),
}

# libx264 preset for the re-encode fallback. veryfast trades ~10-15% larger
# output for ~3-5x faster encode vs the previous "fast" preset. For HLS review
# uploads where R2 storage is cheap and feedback latency is the priority, this
# is the right trade-off.
X264_PRESET = "veryfast"

# Stream-copy fast path is taken when the source is already H.264 video plus
# AAC audio (or no audio). Both codecs are the HLS spec defaults, so we can
# remux directly into MPEG-TS segments without re-encoding a single frame —
# wall-clock time goes from "minutes" to "seconds-per-GB".
STREAM_COPY_VIDEO_CODECS = {"h264"}
STREAM_COPY_AUDIO_CODECS = {"aac", ""}  # "" = no audio track present


class FFmpegTranscoder(BaseTranscoder):
    def __init__(self, s3_client, bucket: str, s3_endpoint: str = None):
        self.s3 = s3_client
        self.bucket = bucket
        self.s3_endpoint = s3_endpoint

    def _get_presigned_url(self, s3_key: str, expires_in: int = 7200) -> str:
        """Generate a presigned URL for streaming input to FFmpeg."""
        return self.s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": s3_key},
            ExpiresIn=expires_in,
        )

    async def get_video_metadata(self, s3_key: str) -> VideoMetadata:
        """Get video metadata using streaming (no full download)."""
        input_url = self._get_presigned_url(s3_key)
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", "-select_streams", "v:0", input_url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        fps_parts = stream.get("r_frame_rate", "30/1").split("/")
        fps = float(fps_parts[0]) / float(fps_parts[1])
        return VideoMetadata(
            duration_seconds=float(stream.get("duration", 0)),
            width=int(stream.get("width", 0)),
            height=int(stream.get("height", 0)),
            fps=fps,
        )

    async def generate_thumbnails(self, s3_key: str, count: int) -> list[str]:
        """Generate thumbnails at 1 per 10 seconds using streaming input."""
        input_url = self._get_presigned_url(s3_key)
        thumb_dir = tempfile.mkdtemp()
        try:
            cmd = [
                "ffmpeg", "-i", input_url,
                "-vf", "fps=0.1",
                "-q:v", "2",
                f"{thumb_dir}/thumb_%04d.jpg",
            ]
            subprocess.run(cmd, capture_output=True, check=True, timeout=600)
            return [str(p) for p in sorted(Path(thumb_dir).glob("thumb_*.jpg"))]
        finally:
            shutil.rmtree(thumb_dir, ignore_errors=True)

    async def generate_waveform(self, s3_key: str) -> dict:
        """Generate waveform data for audio visualization using streaming."""
        input_url = self._get_presigned_url(s3_key)
        # Simplified waveform: just return peak data (full waveform extraction is complex)
        return {"samples": [], "peak": 1.0, "source": s3_key}

    def _probe(self, input_url: str) -> dict:
        """Run ffprobe and return parsed stream info. Never raises — on failure
        returns an empty dict so the caller falls back to the safe re-encode
        path."""
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "quiet", "-print_format", "json",
                    "-show_streams", "-show_format", input_url,
                ],
                capture_output=True, text=True, timeout=120,
            )
            return json.loads(result.stdout or "{}")
        except Exception:
            return {}

    async def transcode(self, job: TranscodeJob) -> TranscodeResult:
        """Transcode video to HLS via streaming input from S3.

        Decision tree, in order of preference:

        1. **Stream-copy fast path** — when source is H.264 + AAC (or video-only
           H.264). Output is a single-variant HLS at source bitrate, produced
           by remuxing into MPEG-TS segments. No re-encoding. 5-15× faster.

        2. **Re-encode with skip-upscale** — when source codec/container would
           force a re-encode anyway (HEVC, ProRes, AV1, AAC-LATM, etc.) or when
           the source contains tracks we can't directly remux. We then build
           the requested HLS quality ladder, but only include variants whose
           target height is ≤ the source height — generating a 1080p variant
           from a 720p source just upscales (worse than the original, wastes
           CPU). The encoder uses preset=veryfast for ~3-5× speedup vs medium.

        Only output files are written to disk; the input is streamed via a
        presigned URL.
        """
        work_dir = Path(tempfile.mkdtemp(prefix=f"transcode_{job.version_id}_"))

        # 2 hour expiry for large files
        input_url = self._get_presigned_url(job.input_s3_key, expires_in=7200)

        try:
            # 1. Probe source for codec + dimensions
            probe = self._probe(input_url)
            video_stream = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), {})
            audio_stream = next((s for s in probe.get("streams", []) if s.get("codec_type") == "audio"), {})

            source_codec_video = (video_stream.get("codec_name") or "").lower()
            source_codec_audio = (audio_stream.get("codec_name") or "").lower()
            source_height = int(video_stream.get("height") or 0)
            source_width = int(video_stream.get("width") or 0)
            source_bitrate = int(probe.get("format", {}).get("bit_rate") or 0)

            can_stream_copy = (
                source_codec_video in STREAM_COPY_VIDEO_CODECS
                and source_codec_audio in STREAM_COPY_AUDIO_CODECS
                and source_height > 0
                and source_width > 0
            )

            hls_dir = work_dir / "hls"
            hls_dir.mkdir()

            if can_stream_copy:
                self._run_stream_copy(input_url, hls_dir, source_width, source_height, source_bitrate)
            else:
                self._run_reencode(input_url, hls_dir, job.qualities, source_height)

            # 2. Upload HLS files to S3
            uploaded_keys = []
            for f in hls_dir.rglob("*"):
                if f.is_file():
                    relative = f.relative_to(hls_dir)
                    s3_key = f"{job.output_s3_prefix}/{relative}"
                    content_type, cache_control = self._get_content_type(f.name)
                    self.s3.upload_file(
                        str(f), self.bucket, s3_key,
                        ExtraArgs={"ContentType": content_type, "CacheControl": cache_control},
                    )
                    uploaded_keys.append(s3_key)

            # 3. Generate and upload thumbnail (using streaming URL)
            thumb_path = work_dir / "thumb_0001.jpg"
            thumb_cmd = [
                "ffmpeg", "-y", "-i", input_url,
                "-vf", "fps=0.1", "-q:v", "2", "-frames:v", "1",
                str(work_dir / "thumb_%04d.jpg"),
            ]
            subprocess.run(thumb_cmd, check=True, capture_output=True)
            thumbnail_key = f"{job.output_s3_prefix}/thumbnail.jpg"
            if thumb_path.exists():
                self.s3.upload_file(
                    str(thumb_path), self.bucket, thumbnail_key,
                    ExtraArgs={"ContentType": "image/jpeg", "CacheControl": "max-age=86400"},
                )

            return TranscodeResult(
                success=True,
                hls_prefix=job.output_s3_prefix,
                thumbnail_keys=[thumbnail_key],
            )

        except Exception as e:
            return TranscodeResult(success=False, error=str(e))
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _run_stream_copy(
        self,
        input_url: str,
        hls_dir: Path,
        width: int,
        height: int,
        bitrate: int,
    ) -> None:
        """Fast path: remux source streams into a single-variant HLS without re-encoding.

        Output layout matches the multi-variant path so the rest of the pipeline
        (uploads, manifest references) is unchanged. The single variant lives in
        a folder named ``0`` for parity with the re-encode path's ``%v`` outputs.
        """
        variant_dir = hls_dir / "0"
        variant_dir.mkdir()

        cmd = [
            "ffmpeg", "-y", "-i", input_url,
            "-c", "copy",
            # 2-second segments to match the re-encode path
            "-f", "hls",
            "-hls_time", "2",
            "-hls_playlist_type", "vod",
            "-hls_flags", "independent_segments",
            "-hls_segment_type", "mpegts",
            "-hls_segment_filename", str(variant_dir / "seg_%03d.ts"),
            str(variant_dir / "playlist.m3u8"),
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=14400)

        # Hand-write master.m3u8 that points at the single variant. We cannot let
        # ffmpeg generate it with -master_pl_name because that flag needs
        # -var_stream_map which only applies to encoded outputs (not -c copy).
        # Conservative bandwidth: use probed bitrate if available, otherwise a
        # reasonable default for 1080p H.264.
        bandwidth = bitrate if bitrate > 0 else 5_000_000
        (hls_dir / "master.m3u8").write_text(
            "#EXTM3U\n"
            "#EXT-X-VERSION:6\n"
            f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={width}x{height}\n"
            "0/playlist.m3u8\n"
        )

    def _run_reencode(
        self,
        input_url: str,
        hls_dir: Path,
        requested_qualities: list[str],
        source_height: int,
    ) -> None:
        """Slow path: re-encode to a quality ladder, skipping any variants that
        would upscale the source.

        If we can't determine the source height (probe failed → source_height=0),
        we encode the full requested ladder to stay safe.
        """
        if source_height > 0:
            ladder = [q for q in requested_qualities if q in QUALITY_MAP and QUALITY_MAP[q][1] <= source_height]
            if not ladder:
                # Source is smaller than even our smallest target — fall back
                # to the smallest variant so the asset is still streamable.
                ladder = ["360p"]
        else:
            ladder = [q for q in requested_qualities if q in QUALITY_MAP]

        split_outputs = "".join(f"[v{i}]" for i in range(len(ladder)))
        filter_complex = f"[v:0]split={len(ladder)}{split_outputs};"
        filter_complex += ";".join(
            f"[v{i}]scale={QUALITY_MAP[q][0]}:force_original_aspect_ratio=decrease,pad=ceil(iw/2)*2:ceil(ih/2)*2[{q}]"
            for i, q in enumerate(ladder)
        )

        ffmpeg_cmd = [
            "ffmpeg", "-y", "-i", input_url,
            "-filter_complex", filter_complex,
        ]

        for i, quality in enumerate(ladder):
            scale, _target_h, crf = QUALITY_MAP[quality]
            ffmpeg_cmd += [
                "-map", f"[{quality}]", "-map", "a:0?",
                f"-c:v:{i}", "libx264", "-crf", str(crf), "-preset", X264_PRESET,
                "-force_key_frames", "expr:gte(t,n_forced*2)",
            ]

        ffmpeg_cmd += [
            "-f", "hls",
            "-hls_time", "2",
            "-hls_playlist_type", "vod",
            "-hls_flags", "independent_segments",
            "-hls_segment_type", "mpegts",
            "-master_pl_name", "master.m3u8",
            "-var_stream_map", " ".join(f"v:{i},a:{i}" for i in range(len(ladder))),
            "-hls_segment_filename", str(hls_dir / "%v" / "seg_%03d.ts"),
            str(hls_dir / "%v" / "playlist.m3u8"),
        ]

        for q in ladder:
            (hls_dir / q).mkdir(exist_ok=True)

        # 4 hour timeout for very large files
        subprocess.run(ffmpeg_cmd, check=True, capture_output=True, timeout=14400)

    @staticmethod
    def _get_content_type(filename: str) -> tuple[str, str]:
        ext = Path(filename).suffix.lower()
        MAP = {
            ".m3u8": ("application/vnd.apple.mpegurl", "no-cache"),
            ".ts": ("video/mp2t", "max-age=31536000"),
            ".jpg": ("image/jpeg", "max-age=86400"),
        }
        return MAP.get(ext, ("application/octet-stream", "no-cache"))

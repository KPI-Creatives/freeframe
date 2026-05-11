"""Worker-startup cleanup.

Re-enqueues any AssetVersion stuck in ``processing`` state when the transcoding
worker starts up.

Why this is safe and free of false positives:

* Celery does **not** persist in-flight tasks across worker restarts. If a
  worker is killed (OOM, container redeploy, host reboot), every task that was
  executing at that moment is lost — no automatic retry, no recovery.
* Therefore, at the moment the worker comes back up, any ``AssetVersion`` row
  still in ``processing`` is guaranteed to be orphaned: nothing in the system
  is working on it.
* A long-running transcode of a healthy worker is unaffected — this hook only
  fires at worker startup, not periodically.
* An 8-hour upload sits in ``uploading`` state, not ``processing``, so it is
  not even scanned.

Hook is registered via Celery's ``worker_ready`` signal so it runs exactly once
per worker process, after the worker has fully initialised and can accept new
tasks. We guard with ``sys.argv`` so only the transcoding worker (the one that
actually consumes ``process_asset`` tasks) performs the rescue — the email
worker and beat scheduler do not touch processing rows.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from celery.signals import worker_ready

from .celery_app import send_task_safe
from ..database import SessionLocal
from ..models.asset import AssetVersion, ProcessingStatus

logger = logging.getLogger(__name__)


def _is_transcoding_worker() -> bool:
    """Return True if this Celery worker process consumes the transcoding queue.

    We inspect ``sys.argv`` because Celery does not expose the queue list cleanly
    via the public API at signal-handler time. The transcoding worker is started
    with ``celery ... worker -Q transcoding`` (see docker-compose.coolify.yml).
    """
    argv = " ".join(sys.argv)
    return "-Q transcoding" in argv or "--queues=transcoding" in argv or "--queue=transcoding" in argv


def _requeue_orphaned_processings() -> int:
    """Re-enqueue every version still in ``processing`` state.

    Returns the number of tasks re-enqueued (for logging / tests).
    """
    # Local import keeps celery_app's worker-import sequence simple and avoids
    # the transcode module pulling its heavy deps unless we actually need them.
    from .transcode_tasks import process_asset

    requeued = 0
    db = SessionLocal()
    try:
        orphaned = (
            db.query(AssetVersion)
            .filter(
                AssetVersion.processing_status == ProcessingStatus.processing,
                AssetVersion.deleted_at.is_(None),
            )
            .order_by(AssetVersion.created_at)
            .all()
        )
        for version in orphaned:
            stuck_for = datetime.now(timezone.utc) - version.created_at
            logger.warning(
                "Worker restart: re-enqueuing orphaned processing asset=%s version=%s in_state_for=%s",
                version.asset_id,
                version.id,
                stuck_for,
            )
            send_task_safe(process_asset, str(version.asset_id), str(version.id))
            requeued += 1
    finally:
        db.close()

    return requeued


@worker_ready.connect
def on_worker_ready(sender, **kwargs):
    """Catch processing rows orphaned by the previous worker process."""
    if not _is_transcoding_worker():
        return
    try:
        n = _requeue_orphaned_processings()
        if n:
            logger.info("Re-enqueued %d orphaned processing version(s) after worker restart", n)
    except Exception:
        # Never let cleanup take down worker startup — log and move on.
        logger.exception("Failed to requeue orphaned processings on worker startup")

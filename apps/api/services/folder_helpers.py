"""Helpers for folder-policy resolution.

Today this is just ``resolve_track_time_default`` — used at asset creation to
turn a folder's ``time_tracking_default`` enum (`on`/`off`/`inherit`) plus
its parent chain into a single boolean that lives on the asset for the rest
of its life.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy.orm import Session

from ..models.folder import Folder, TimeTrackingDefault


# Max depth of the parent walk. We already cap folder depth at 10 in
# ``routers/folders.MAX_FOLDER_DEPTH`` — this matches it. Defensive against
# a future bug that creates a parent_id cycle.
_MAX_RESOLVE_DEPTH = 12


def resolve_track_time_default(
    db: Session, folder_id: Optional[uuid.UUID]
) -> bool:
    """Walk up from ``folder_id`` until we hit a folder with an explicit
    ``on``/``off`` policy. Return that as a bool. If the entire chain is
    ``inherit`` (or ``folder_id`` is ``None``), return ``False`` — root
    default is conservative: don't ask unless something explicitly opts in.

    The result is consumed at asset creation time in
    ``routers/upload.initiate_upload`` and stored on ``Asset.track_time``.
    Moving the asset between folders later does NOT re-resolve; the editor
    has to toggle ``track_time`` directly via the Fields tab.
    """

    if folder_id is None:
        return False

    current_id: Optional[uuid.UUID] = folder_id
    seen: set[uuid.UUID] = set()
    depth = 0

    while current_id is not None and depth < _MAX_RESOLVE_DEPTH:
        if current_id in seen:
            # Cycle — bail. Don't try to be clever about which value won;
            # tree integrity is enforced at insert time.
            return False
        seen.add(current_id)

        folder = (
            db.query(Folder)
            .filter(Folder.id == current_id, Folder.deleted_at.is_(None))
            .first()
        )
        if folder is None:
            return False

        policy = folder.time_tracking_default
        if policy == TimeTrackingDefault.on:
            return True
        if policy == TimeTrackingDefault.off:
            return False
        # ``inherit`` — climb to parent
        current_id = folder.parent_id
        depth += 1

    return False

import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

from ..models.folder import TimeTrackingDefault


class FolderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    parent_id: Optional[uuid.UUID] = None
    # Time-tracking policy for assets created inside this folder. Default is
    # ``inherit`` — at asset creation, the system walks up the parent chain
    # until a non-``inherit`` value is found; if the whole chain is
    # ``inherit``, the resolved default is ``off``.
    time_tracking_default: TimeTrackingDefault = TimeTrackingDefault.inherit


class FolderUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    parent_id: Optional[uuid.UUID] = None  # use model_fields_set to distinguish unset vs null
    time_tracking_default: Optional[TimeTrackingDefault] = None


class FolderResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    parent_id: Optional[uuid.UUID]
    name: str
    time_tracking_default: TimeTrackingDefault = TimeTrackingDefault.inherit
    # Resolved effective default, after walking up the parent chain. Useful
    # for the folder-settings dialog (so the UI can show "Inherit (currently
    # off)"). Computed at response build time, not stored.
    time_tracking_resolved: bool = False
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    item_count: int = 0

    model_config = {"from_attributes": True}


class FolderTreeNode(BaseModel):
    id: uuid.UUID
    name: str
    parent_id: Optional[uuid.UUID]
    time_tracking_default: TimeTrackingDefault = TimeTrackingDefault.inherit
    item_count: int = 0
    children: list["FolderTreeNode"] = []


class AssetMoveRequest(BaseModel):
    folder_id: Optional[uuid.UUID] = None  # null = move to root


class BulkMoveRequest(BaseModel):
    asset_ids: list[uuid.UUID] = []
    folder_ids: list[uuid.UUID] = []
    target_folder_id: Optional[uuid.UUID] = None  # null = root

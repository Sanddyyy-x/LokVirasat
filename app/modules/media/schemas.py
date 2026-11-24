from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field


class PresignUploadRequest(BaseModel):
    media_kind: Literal["image", "video", "audio", "document"]
    mime_type: str = Field(min_length=3, max_length=100)
    file_size_bytes: int = Field(gt=0)
    filename: str = Field(min_length=1, max_length=255)
    access_level: Literal["public", "private_signed"] = "public"
    alt_text: str | None = Field(default=None, max_length=300)
    width_px: int | None = Field(default=None, gt=0)
    height_px: int | None = Field(default=None, gt=0)
    duration_seconds: int | None = Field(default=None, gt=0, le=180)


class PresignUploadResponse(BaseModel):
    media_id: UUID
    storage_key: str
    upload_url: str
    upload_method: str = "PUT"
    upload_status: str
    access_level: str
    expires_at: datetime


class ConfirmUploadRequest(BaseModel):
    width_px: int | None = Field(default=None, gt=0)
    height_px: int | None = Field(default=None, gt=0)
    duration_seconds: int | None = Field(default=None, gt=0, le=180)


class MediaAssetOut(BaseModel):
    media_id: UUID
    uploader_id: UUID
    content_id: UUID | None
    storage_key: str
    media_kind: str
    mime_type: str
    file_size_bytes: int
    width_px: int | None
    height_px: int | None
    duration_seconds: int | None
    display_order: int
    alt_text: str | None
    access_level: str
    upload_status: str
    url: str
    created_at: datetime


class CleanupResultOut(BaseModel):
    purged_count: int
    deleted_media_ids: list[UUID]
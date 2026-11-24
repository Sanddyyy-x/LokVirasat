import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user, require_roles
from app.modules.media.schemas import (
    CleanupResultOut,
    ConfirmUploadRequest,
    MediaAssetOut,
    PresignUploadRequest,
    PresignUploadResponse,
)

router = APIRouter(prefix="/media", tags=["media"])

MAX_BYTES_BY_KIND: dict[str, int] = {
    "image": 10 * 1024 * 1024,      # 10 MB
    "audio": 20 * 1024 * 1024,      # 20 MB
    "document": 20 * 1024 * 1024,   # 20 MB
    "video": 100 * 1024 * 1024,     # 100 MB
}

ALLOWED_MIMES_BY_KIND: dict[str, set[str]] = {
    "image": {"image/jpeg", "image/png", "image/webp", "image/avif"},
    "audio": {"audio/mpeg", "audio/mp3", "audio/wav", "audio/ogg", "audio/mp4", "audio/aac"},
    "document": {"application/pdf"},
    "video": {"video/mp4", "video/webm", "video/quicktime"},
}

CREATOR_OR_STAFF_ROLES = {
    "cultural_creator",
    "researcher_historian",
    "organization",
    "moderator",
    "administrator",
}


def _safe_extension(filename: str, mime_type: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext and re.match(r"^\.[a-z0-9]{1,8}$", ext):
        return ext
    fallback = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/avif": ".avif",
        "video/mp4": ".mp4",
        "video/webm": ".webm",
        "video/quicktime": ".mov",
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/wav": ".wav",
        "audio/ogg": ".ogg",
        "application/pdf": ".pdf",
    }
    return fallback.get(mime_type, "")


def _bucket_for_access(access_level: str) -> str:
    return (
        settings.supabase_private_bucket
        if access_level == "private_signed"
        else settings.supabase_public_bucket
    )


async def _create_presigned_upload_url(storage_key: str, access_level: str) -> str:
    if settings.supabase_url and settings.supabase_service_role_key:
        bucket = _bucket_for_access(access_level)
        endpoint = f"{settings.supabase_url.rstrip('/')}/storage/v1/object/upload/sign/{bucket}/{storage_key}"
        headers = {
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
            "apikey": settings.supabase_service_role_key,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(endpoint, headers=headers)
            if res.status_code >= 400:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Failed to generate storage upload URL",
                )
            signed_path = res.json().get("url", "")
            return f"{settings.supabase_url.rstrip('/')}/storage/v1{signed_path}"

    # Local dev fallback when Supabase Storage credentials are not configured
    return f"http://localhost:8000/media/mock-upload/{storage_key}"


async def _resolve_asset_url(storage_key: str, access_level: str) -> str:
    if settings.supabase_url and settings.supabase_service_role_key:
        base = settings.supabase_url.rstrip("/")
        bucket = _bucket_for_access(access_level)
        if access_level == "public":
            return f"{base}/storage/v1/object/public/{bucket}/{storage_key}"

        endpoint = f"{base}/storage/v1/object/sign/{bucket}/{storage_key}"
        headers = {
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
            "apikey": settings.supabase_service_role_key,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(endpoint, headers=headers, json={"expiresIn": 300})
            if res.status_code >= 400:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Failed to sign private media URL",
                )
            signed_url = res.json().get("signedURL", "")
            return f"{base}/storage/v1{signed_url}"

    if access_level == "public":
        return f"https://cdn.lokvirasat.local/{storage_key}"
    return f"https://cdn.lokvirasat.local/signed/{storage_key}?expires_in=300"


async def _delete_from_object_storage(items: list[tuple[str, str]]) -> None:
    if not items or not (settings.supabase_url and settings.supabase_service_role_key):
        return

    by_bucket: dict[str, list[str]] = {}
    for storage_key, access_level in items:
        bucket = _bucket_for_access(access_level)
        by_bucket.setdefault(bucket, []).append(storage_key)

    base = settings.supabase_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
        "apikey": settings.supabase_service_role_key,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        for bucket, prefixes in by_bucket.items():
            await client.request(
                "DELETE",
                f"{base}/storage/v1/object/{bucket}",
                headers=headers,
                json={"prefixes": prefixes},
            )


# ---------------------------------------------------------------------------
# STEP 01 · PRESIGN (Reserve Asset Row with 30m TTL)
# ---------------------------------------------------------------------------
@router.post("/presign", response_model=PresignUploadResponse, status_code=status.HTTP_201_CREATED)
async def presign_upload(
    payload: PresignUploadRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    mime = payload.mime_type.strip().lower()
    if mime not in ALLOWED_MIMES_BY_KIND[payload.media_kind]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported MIME type '{mime}' for {payload.media_kind}",
        )

    max_bytes = MAX_BYTES_BY_KIND[payload.media_kind]
    if payload.file_size_bytes > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{payload.media_kind} exceeds maximum allowed size of {max_bytes} bytes",
        )

    user_roles = set(current_user.get("roles") or [])
    if not user_roles.intersection(CREATOR_OR_STAFF_ROLES) and payload.media_kind in {"video", "audio"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only creators or staff can upload video or audio assets",
        )

    ext = _safe_extension(payload.filename, mime)
    now = datetime.now(timezone.utc)
    storage_key = f"{payload.media_kind}/{current_user['user_id']}/{now:%Y/%m}/{uuid4()}{ext}"
    expires_at = now + timedelta(minutes=30)

    media_id = await db.scalar(
        text(
            """
            INSERT INTO media_assets (
                uploader_id, storage_key, media_kind, mime_type, file_size_bytes,
                width_px, height_px, duration_seconds, alt_text, access_level,
                upload_status, expires_at
            )
            VALUES (
                :uploader_id, :storage_key, CAST(:media_kind AS media_kind_enum),
                :mime_type, :file_size_bytes, :width_px, :height_px, :duration_seconds,
                :alt_text, :access_level, 'pending_upload', :expires_at
            )
            RETURNING media_id
            """
        ),
        {
            "uploader_id": current_user["user_id"],
            "storage_key": storage_key,
            "media_kind": payload.media_kind,
            "mime_type": mime,
            "file_size_bytes": payload.file_size_bytes,
            "width_px": payload.width_px,
            "height_px": payload.height_px,
            "duration_seconds": payload.duration_seconds,
            "alt_text": payload.alt_text.strip() if payload.alt_text else None,
            "access_level": payload.access_level,
            "expires_at": expires_at,
        },
    )
    await db.commit()

    upload_url = await _create_presigned_upload_url(storage_key, payload.access_level)
    return PresignUploadResponse(
        media_id=media_id,
        storage_key=storage_key,
        upload_url=upload_url,
        upload_status="pending_upload",
        access_level=payload.access_level,
        expires_at=expires_at,
    )


# ---------------------------------------------------------------------------
# STEP 02 · VALIDATE & ACTIVATE
# ---------------------------------------------------------------------------
@router.post("/{media_id}/confirm", response_model=MediaAssetOut)
async def confirm_upload(
    media_id: UUID,
    payload: ConfirmUploadRequest | None = None,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    asset = (
        await db.execute(
            text(
                """
                SELECT media_id, uploader_id, content_id, storage_key,
                       media_kind::text AS media_kind, mime_type, file_size_bytes,
                       width_px, height_px, duration_seconds, display_order,
                       alt_text, access_level, upload_status, expires_at, created_at
                FROM media_assets
                WHERE media_id = :media_id
                """
            ),
            {"media_id": media_id},
        )
    ).mappings().first()

    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media asset not found")
    if asset["uploader_id"] != current_user["user_id"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to confirm this asset")
    if asset["upload_status"] not in {"pending_upload", "active"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot confirm media in '{asset['upload_status']}' state",
        )

    now = datetime.now(timezone.utc)
    if asset["upload_status"] == "pending_upload" and asset["expires_at"] and asset["expires_at"] < now:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Presigned upload window has expired")

    final_duration = (payload.duration_seconds if payload and payload.duration_seconds is not None else asset["duration_seconds"])
    if asset["media_kind"] == "video" and final_duration is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Video assets require duration_seconds (1 to 180) before activation",
        )

    updated = (
        await db.execute(
            text(
                """
                UPDATE media_assets
                   SET upload_status = 'active',
                       expires_at = NULL,
                       width_px = COALESCE(:width_px, width_px),
                       height_px = COALESCE(:height_px, height_px),
                       duration_seconds = COALESCE(:duration_seconds, duration_seconds)
                 WHERE media_id = :media_id
                RETURNING media_id, uploader_id, content_id, storage_key,
                          media_kind::text AS media_kind, mime_type, file_size_bytes,
                          width_px, height_px, duration_seconds, display_order,
                          alt_text, access_level, upload_status, created_at
                """
            ),
            {
                "media_id": media_id,
                "width_px": payload.width_px if payload else None,
                "height_px": payload.height_px if payload else None,
                "duration_seconds": payload.duration_seconds if payload else None,
            },
        )
    ).mappings().first()
    await db.commit()

    url = await _resolve_asset_url(updated["storage_key"], updated["access_level"])
    return MediaAssetOut(**dict(updated), url=url)


# ---------------------------------------------------------------------------
# STEP 03 · ACCESS (Public CDN vs. 5-Minute Signed URL)
# ---------------------------------------------------------------------------
@router.get("/{media_id}/url", response_model=MediaAssetOut)
async def get_media_access_url(
    media_id: UUID,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    asset = (
        await db.execute(
            text(
                """
                SELECT media_id, uploader_id, content_id, storage_key,
                       media_kind::text AS media_kind, mime_type, file_size_bytes,
                       width_px, height_px, duration_seconds, display_order,
                       alt_text, access_level, upload_status, created_at
                FROM media_assets
                WHERE media_id = :media_id AND upload_status = 'active'
                """
            ),
            {"media_id": media_id},
        )
    ).mappings().first()

    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active media asset not found")

    if asset["access_level"] == "private_signed":
        user_roles = set(current_user.get("roles") or [])
        is_staff = bool(user_roles.intersection({"moderator", "administrator"}))
        if asset["uploader_id"] != current_user["user_id"] and not is_staff:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to private media")

    url = await _resolve_asset_url(asset["storage_key"], asset["access_level"])
    return MediaAssetOut(**dict(asset), url=url)


# ---------------------------------------------------------------------------
# STEP 04 · SOFT-ORPHAN & SCHEDULED PURGE CLEANUP
# ---------------------------------------------------------------------------
@router.delete("/{media_id}", status_code=status.HTTP_204_NO_CONTENT)
async def mark_media_orphaned(
    media_id: UUID,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    asset = (
        await db.execute(
            text("SELECT uploader_id FROM media_assets WHERE media_id = :id"),
            {"id": media_id},
        )
    ).mappings().first()

    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media asset not found")

    user_roles = set(current_user.get("roles") or [])
    is_staff = bool(user_roles.intersection({"moderator", "administrator"}))
    if asset["uploader_id"] != current_user["user_id"] and not is_staff:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to delete this asset")

    await db.execute(
        text(
            """
            UPDATE media_assets
               SET upload_status = 'orphaned', content_id = NULL
             WHERE media_id = :id AND upload_status <> 'deleted'
            """
        ),
        {"id": media_id},
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def run_media_cleanup(db: AsyncSession) -> CleanupResultOut:
    stale_rows = (
        await db.execute(
            text(
                """
                SELECT media_id, storage_key, access_level
                FROM media_assets
                WHERE upload_status = 'orphaned'
                   OR (upload_status = 'pending_upload' AND expires_at IS NOT NULL AND expires_at < NOW())
                LIMIT 200
                """
            )
        )
    ).mappings().all()

    if not stale_rows:
        return CleanupResultOut(purged_count=0, deleted_media_ids=[])

    await _delete_from_object_storage([(r["storage_key"], r["access_level"]) for r in stale_rows])

    ids = [r["media_id"] for r in stale_rows]
    await db.execute(
        text(
            """
            UPDATE media_assets
               SET upload_status = 'deleted', expires_at = NULL
             WHERE media_id = ANY(:ids)
            """
        ),
        {"ids": ids},
    )
    await db.commit()
    return CleanupResultOut(purged_count=len(ids), deleted_media_ids=ids)


@router.post("/cleanup", response_model=CleanupResultOut)
async def trigger_media_cleanup(
    _: dict[str, Any] = Depends(require_roles("administrator")),
    db: AsyncSession = Depends(get_db),
):
    return await run_media_cleanup(db)
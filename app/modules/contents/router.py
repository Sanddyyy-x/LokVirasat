import json
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, require_roles
from app.modules.contents.schemas import (
    AuthorSummaryOut,
    ContentFeedResponse,
    ContentOut,
    CreateContentRequest,
    UpdateContentRequest,
)

router = APIRouter(prefix="/contents", tags=["contents"])

CREATOR_ROLES = ("cultural_creator", "researcher_historian", "organization", "administrator")


def _normalize_tags(raw_tags: list[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for tag in raw_tags:
        norm = tag.strip().lstrip("#").lower()
        if norm and norm not in seen:
            seen.add(norm)
            cleaned.append(norm)
    return cleaned


async def _fetch_content_detail(db: AsyncSession, content_id: UUID) -> ContentOut | None:
    row = (
        await db.execute(
            text(
                """
                SELECT
                    c.content_id,
                    c.content_type::text AS content_type,
                    c.title,
                    c.caption_or_summary,
                    c.language_code,
                    c.region_id,
                    r.name AS region_name,
                    c.category_id,
                    hc.name AS category_name,
                    c.community_id,
                    c.historical_era,
                    c.tags,
                    c.visibility::text AS visibility,
                    c.moderation_status::text AS moderation_status,
                    c.verification_status::text AS verification_status,
                    c.like_count,
                    c.comment_count,
                    c.save_count,
                    c.view_count,
                    c.published_at,
                    c.created_at,
                    c.updated_at,
                    u.user_id AS author_user_id,
                    u.username AS author_username,
                    u.full_name AS author_full_name,
                    u.is_verified_badge AS author_is_verified_badge
                FROM contents c
                JOIN users u ON u.user_id = c.author_id
                LEFT JOIN regions r ON r.region_id = c.region_id
                LEFT JOIN heritage_categories hc ON hc.category_id = c.category_id
                WHERE c.content_id = :content_id
                """
            ),
            {"content_id": content_id},
        )
    ).mappings().first()

    if not row:
        return None

    data = dict(row)
    reel_details = None
    article_details = None

    if data["content_type"] == "reel":
        rd = (
            await db.execute(
                text(
                    """
                    SELECT video_media_id, thumbnail_media_id, duration_seconds,
                           aspect_ratio, audio_track_name, transcript_text
                    FROM reel_details
                    WHERE content_id = :content_id
                    """
                ),
                {"content_id": content_id},
            )
        ).mappings().first()
        reel_details = dict(rd) if rd else None

    elif data["content_type"] == "article":
        ad = (
            await db.execute(
                text(
                    """
                    SELECT body_markdown, cover_media_id, reading_time_mins,
                           references_json, primary_source_notes
                    FROM article_details
                    WHERE content_id = :content_id
                    """
                ),
                {"content_id": content_id},
            )
        ).mappings().first()
        article_details = dict(ad) if ad else None

    media_rows = (
        await db.execute(
            text(
                """
                SELECT media_id, storage_key, media_kind::text AS media_kind,
                       mime_type, width_px, height_px, duration_seconds,
                       display_order, alt_text, access_level
                FROM media_assets
                WHERE content_id = :content_id AND upload_status = 'active'
                ORDER BY display_order ASC, created_at ASC
                """
            ),
            {"content_id": content_id},
        )
    ).mappings().all()

    return ContentOut(
        content_id=data["content_id"],
        content_type=data["content_type"],
        title=data["title"],
        caption_or_summary=data["caption_or_summary"],
        language_code=data["language_code"],
        region_id=data["region_id"],
        region_name=data["region_name"],
        category_id=data["category_id"],
        category_name=data["category_name"],
        community_id=data["community_id"],
        historical_era=data["historical_era"],
        tags=list(data["tags"] or []),
        visibility=data["visibility"],
        moderation_status=data["moderation_status"],
        verification_status=data["verification_status"],
        like_count=data["like_count"],
        comment_count=data["comment_count"],
        save_count=data["save_count"],
        view_count=data["view_count"],
        published_at=data["published_at"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        author=AuthorSummaryOut(
            user_id=data["author_user_id"],
            username=data["author_username"],
            full_name=data["author_full_name"],
            is_verified_badge=data["author_is_verified_badge"],
        ),
        reel_details=reel_details,
        article_details=article_details,
        media_assets=[dict(m) for m in media_rows],
    )


@router.post("", response_model=ContentOut, status_code=status.HTTP_201_CREATED)
async def create_content(
    payload: CreateContentRequest,
    current_user: dict[str, Any] = Depends(require_roles(*CREATOR_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    tags = _normalize_tags(payload.tags)

    try:
        content_id = await db.scalar(
            text(
                """
                INSERT INTO contents (
                    author_id, community_id, content_type, title, caption_or_summary,
                    language_code, region_id, category_id, historical_era, tags,
                    visibility, published_at
                )
                VALUES (
                    :author_id, :community_id, CAST(:content_type AS content_type_enum),
                    :title, :caption_or_summary, :language_code, :region_id, :category_id,
                    :historical_era, :tags, CAST(:visibility AS visibility_enum),
                    CASE WHEN :visibility = 'public' THEN NOW() ELSE NULL END
                )
                RETURNING content_id
                """
            ),
            {
                "author_id": current_user["user_id"],
                "community_id": payload.community_id,
                "content_type": payload.content_type,
                "title": payload.title.strip() if payload.title else None,
                "caption_or_summary": payload.caption_or_summary,
                "language_code": payload.language_code.strip().lower(),
                "region_id": payload.region_id,
                "category_id": payload.category_id,
                "historical_era": payload.historical_era.strip() if payload.historical_era else None,
                "tags": tags,
                "visibility": payload.visibility,
            },
        )

        if payload.content_type == "reel" and payload.reel_details:
            rd = payload.reel_details
            await db.execute(
                text(
                    """
                    INSERT INTO reel_details (
                        content_id, video_media_id, thumbnail_media_id,
                        duration_seconds, aspect_ratio, audio_track_name, transcript_text
                    )
                    VALUES (
                        :content_id, :video_media_id, :thumbnail_media_id,
                        :duration_seconds, :aspect_ratio, :audio_track_name, :transcript_text
                    )
                    """
                ),
                {
                    "content_id": content_id,
                    "video_media_id": rd.video_media_id,
                    "thumbnail_media_id": rd.thumbnail_media_id,
                    "duration_seconds": rd.duration_seconds,
                    "aspect_ratio": rd.aspect_ratio,
                    "audio_track_name": rd.audio_track_name,
                    "transcript_text": rd.transcript_text,
                },
            )

        elif payload.content_type == "article" and payload.article_details:
            ad = payload.article_details
            await db.execute(
                text(
                    """
                    INSERT INTO article_details (
                        content_id, body_markdown, cover_media_id,
                        reading_time_mins, references_json, primary_source_notes
                    )
                    VALUES (
                        :content_id, :body_markdown, :cover_media_id,
                        :reading_time_mins, CAST(:references_json AS JSONB), :primary_source_notes
                    )
                    """
                ),
                {
                    "content_id": content_id,
                    "body_markdown": ad.body_markdown,
                    "cover_media_id": ad.cover_media_id,
                    "reading_time_mins": ad.reading_time_mins,
                    "references_json": json.dumps(ad.references_json),
                    "primary_source_notes": ad.primary_source_notes,
                },
            )

        if payload.media_ids:
            for order_idx, media_id in enumerate(payload.media_ids):
                await db.execute(
                    text(
                        """
                        UPDATE media_assets
                           SET content_id = :content_id, display_order = :display_order
                         WHERE media_id = :media_id
                           AND uploader_id = :uploader_id
                           AND upload_status = 'active'
                        """
                    ),
                    {
                        "content_id": content_id,
                        "display_order": order_idx,
                        "media_id": media_id,
                        "uploader_id": current_user["user_id"],
                    },
                )

        await db.commit()
    except (IntegrityError, DBAPIError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid content or media reference: {exc.orig}",
        )

    created = await _fetch_content_detail(db, content_id)
    return created


@router.get("", response_model=ContentFeedResponse)
async def list_contents(
    content_type: Literal["post", "reel", "article"] | None = Query(default=None),
    region_id: int | None = Query(default=None),
    category_id: int | None = Query(default=None),
    tag: str | None = Query(default=None),
    language_code: str | None = Query(default=None),
    author_username: str | None = Query(default=None),
    q: str | None = Query(default=None, min_length=1, max_length=120),
    sort: Literal["recent", "popular"] = Query(default="recent"),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    conditions = [
        "c.visibility = 'public'",
        "c.moderation_status = 'published'",
    ]
    params: dict[str, Any] = {"fetch_limit": limit + 1, "offset": offset}

    if content_type:
        conditions.append("c.content_type = CAST(:content_type AS content_type_enum)")
        params["content_type"] = content_type
    if region_id is not None:
        conditions.append("c.region_id = :region_id")
        params["region_id"] = region_id
    if category_id is not None:
        conditions.append("c.category_id = :category_id")
        params["category_id"] = category_id
    if tag:
        conditions.append(":tag = ANY(c.tags)")
        params["tag"] = tag.strip().lstrip("#").lower()
    if language_code:
        conditions.append("c.language_code = :language_code")
        params["language_code"] = language_code.strip().lower()
    if author_username:
        conditions.append("LOWER(u.username) = :author_username")
        params["author_username"] = author_username.strip().lower()
    if q:
        conditions.append("c.search_vector @@ plainto_tsquery('english', :q)")
        params["q"] = q.strip()

    order_by = (
        "(c.like_count * 2 + c.save_count * 3 + c.comment_count) DESC, c.published_at DESC NULLS LAST"
        if sort == "popular"
        else "c.published_at DESC NULLS LAST, c.created_at DESC"
    )

    rows = (
        await db.execute(
            text(
                f"""
                SELECT
                    c.content_id,
                    c.content_type::text AS content_type,
                    c.title,
                    c.caption_or_summary,
                    c.language_code,
                    c.region_id,
                    r.name AS region_name,
                    c.category_id,
                    hc.name AS category_name,
                    c.community_id,
                    c.historical_era,
                    c.tags,
                    c.visibility::text AS visibility,
                    c.moderation_status::text AS moderation_status,
                    c.verification_status::text AS verification_status,
                    c.like_count,
                    c.comment_count,
                    c.save_count,
                    c.view_count,
                    c.published_at,
                    c.created_at,
                    c.updated_at,
                    u.user_id AS author_user_id,
                    u.username AS author_username,
                    u.full_name AS author_full_name,
                    u.is_verified_badge AS author_is_verified_badge
                FROM contents c
                JOIN users u ON u.user_id = c.author_id
                LEFT JOIN regions r ON r.region_id = c.region_id
                LEFT JOIN heritage_categories hc ON hc.category_id = c.category_id
                WHERE {' AND '.join(conditions)}
                ORDER BY {order_by}
                LIMIT :fetch_limit OFFSET :offset
                """
            ),
            params,
        )
    ).mappings().all()

    has_more = len(rows) > limit
    page_rows = rows[:limit]

    items = [
        ContentOut(
            content_id=row["content_id"],
            content_type=row["content_type"],
            title=row["title"],
            caption_or_summary=row["caption_or_summary"],
            language_code=row["language_code"],
            region_id=row["region_id"],
            region_name=row["region_name"],
            category_id=row["category_id"],
            category_name=row["category_name"],
            community_id=row["community_id"],
            historical_era=row["historical_era"],
            tags=list(row["tags"] or []),
            visibility=row["visibility"],
            moderation_status=row["moderation_status"],
            verification_status=row["verification_status"],
            like_count=row["like_count"],
            comment_count=row["comment_count"],
            save_count=row["save_count"],
            view_count=row["view_count"],
            published_at=row["published_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            author=AuthorSummaryOut(
                user_id=row["author_user_id"],
                username=row["author_username"],
                full_name=row["author_full_name"],
                is_verified_badge=row["author_is_verified_badge"],
            ),
        )
        for row in page_rows
    ]

    return ContentFeedResponse(items=items, limit=limit, offset=offset, has_more=has_more)


@router.get("/{content_id}", response_model=ContentOut)
async def get_content(content_id: UUID, db: AsyncSession = Depends(get_db)):
    item = await _fetch_content_detail(db, content_id)
    if not item or item.moderation_status == "removed":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found")
    return item


@router.patch("/{content_id}", response_model=ContentOut)
async def update_content(
    content_id: UUID,
    payload: UpdateContentRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    existing = (
        await db.execute(
            text("SELECT author_id, content_type::text AS content_type, published_at FROM contents WHERE content_id = :id"),
            {"id": content_id},
        )
    ).mappings().first()

    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found")
    if existing["author_id"] != current_user["user_id"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the author can edit this content")

    updates = payload.model_dump(exclude_unset=True)
    article_fields = {}
    for key in ("body_markdown", "references_json", "primary_source_notes"):
        if key in updates:
            article_fields[key] = updates.pop(key)

    if updates:
        set_clauses = []
        params: dict[str, Any] = {"content_id": content_id}
        for field, value in updates.items():
            if field == "tags" and value is not None:
                set_clauses.append("tags = :tags")
                params["tags"] = _normalize_tags(value)
            elif field == "visibility" and value is not None:
                set_clauses.append("visibility = CAST(:visibility AS visibility_enum)")
                params["visibility"] = value
                if value == "public" and existing["published_at"] is None:
                    set_clauses.append("published_at = NOW()")
            else:
                set_clauses.append(f"{field} = :{field}")
                params[field] = value.strip() if isinstance(value, str) else value

        await db.execute(
            text(f"UPDATE contents SET {', '.join(set_clauses)} WHERE content_id = :content_id"),
            params,
        )

    if article_fields and existing["content_type"] == "article":
        a_clauses = []
        a_params: dict[str, Any] = {"content_id": content_id}
        for field, value in article_fields.items():
            if field == "references_json" and value is not None:
                a_clauses.append("references_json = CAST(:references_json AS JSONB)")
                a_params["references_json"] = json.dumps(value)
            else:
                a_clauses.append(f"{field} = :{field}")
                a_params[field] = value
        await db.execute(
            text(f"UPDATE article_details SET {', '.join(a_clauses)} WHERE content_id = :content_id"),
            a_params,
        )

    await db.commit()
    return await _fetch_content_detail(db, content_id)


@router.delete("/{content_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_content(
    content_id: UUID,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    existing = (
        await db.execute(
            text("SELECT author_id FROM contents WHERE content_id = :id"),
            {"id": content_id},
        )
    ).mappings().first()

    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found")

    roles = set(current_user.get("roles") or [])
    is_staff = bool(roles.intersection({"moderator", "administrator"}))
    if existing["author_id"] != current_user["user_id"] and not is_staff:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to delete this content")

    # Mark linked media assets as orphaned for scheduled object-storage cleanup
    await db.execute(
        text(
            """
            UPDATE media_assets
               SET upload_status = 'orphaned', content_id = NULL
             WHERE content_id = :content_id AND upload_status = 'active'
            """
        ),
        {"content_id": content_id},
    )
    await db.execute(text("DELETE FROM contents WHERE content_id = :content_id"), {"content_id": content_id})
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
import json
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, get_optional_current_user
from app.modules.interactions.schemas import (
    AffinitySummaryOut,
    BaselineRecommendationItem,
    BatchInteractionRequest,
    BatchInteractionResponse,
    InteractionEventIn,
    InteractionEventOut,
    SearchLogRequest,
    SearchLogResponse,
)

router = APIRouter(tags=["interactions"])


async def _fetch_content_taxonomy_map(
    db: AsyncSession, content_ids: set[UUID]
) -> dict[UUID, dict[str, Any]]:
    if not content_ids:
        return {}

    rows = (
        await db.execute(
            text(
                """
                SELECT
                    c.content_id,
                    c.author_id,
                    c.content_type::text AS content_type,
                    c.region_id,
                    r.name AS region_name,
                    c.category_id,
                    hc.slug AS category_slug,
                    c.tags
                FROM contents c
                LEFT JOIN regions r ON r.region_id = c.region_id
                LEFT JOIN heritage_categories hc ON hc.category_id = c.category_id
                WHERE c.content_id = ANY(:ids)
                """
            ),
            {"ids": list(content_ids)},
        )
    ).mappings().all()

    return {row["content_id"]: dict(row) for row in rows}


async def _insert_single_interaction(
    db: AsyncSession,
    event: InteractionEventIn,
    user_id: UUID | None,
    taxonomy_map: dict[UUID, dict[str, Any]],
) -> dict[str, Any]:
    enriched_context: dict[str, Any] = dict(event.context_json)

    if event.content_id is not None:
        meta = taxonomy_map.get(event.content_id)
        if not meta:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Content {event.content_id} not found",
            )
        enriched_context.setdefault("content_type", meta["content_type"])
        enriched_context.setdefault("author_id", str(meta["author_id"]))
        if meta["region_id"] is not None:
            enriched_context.setdefault("region_id", meta["region_id"])
            enriched_context.setdefault("region_name", meta["region_name"])
        if meta["category_id"] is not None:
            enriched_context.setdefault("category_id", meta["category_id"])
            enriched_context.setdefault("category_slug", meta["category_slug"])
        if meta["tags"]:
            enriched_context.setdefault("tags", list(meta["tags"]))

    if event.target_user_id is not None:
        enriched_context.setdefault("target_user_id", str(event.target_user_id))

    occurred_at = event.occurred_at or datetime.now(timezone.utc)

    row = (
        await db.execute(
            text(
                """
                INSERT INTO user_interactions (
                    user_id, session_id, content_id, event_type, feed_source,
                    dwell_ms, watch_ms, completion_pct, position_index,
                    context_json, occurred_at
                )
                VALUES (
                    :user_id, :session_id, :content_id, :event_type, :feed_source,
                    :dwell_ms, :watch_ms, :completion_pct, :position_index,
                    CAST(:context_json AS JSONB), :occurred_at
                )
                RETURNING
                    interaction_id, user_id, session_id, content_id,
                    event_type, feed_source, dwell_ms, watch_ms,
                    CAST(completion_pct AS FLOAT) AS completion_pct,
                    position_index, context_json, occurred_at
                """
            ),
            {
                "user_id": user_id,
                "session_id": event.session_id.strip(),
                "content_id": event.content_id,
                "event_type": event.event_type,
                "feed_source": event.feed_source,
                "dwell_ms": event.dwell_ms,
                "watch_ms": event.watch_ms,
                "completion_pct": event.completion_pct,
                "position_index": event.position_index,
                "context_json": json.dumps(enriched_context),
                "occurred_at": occurred_at,
            },
        )
    ).mappings().first()

    if event.event_type == "view" and event.content_id is not None:
        await db.execute(
            text("UPDATE contents SET view_count = view_count + 1 WHERE content_id = :id"),
            {"id": event.content_id},
        )

    return dict(row)


# ---------------------------------------------------------------------------
# 1. SINGLE & BATCH INTERACTION INGESTION
# ---------------------------------------------------------------------------
@router.post("/interactions", response_model=InteractionEventOut, status_code=status.HTTP_201_CREATED)
async def log_interaction(
    payload: InteractionEventIn,
    current_user: dict[str, Any] | None = Depends(get_optional_current_user),
    db: AsyncSession = Depends(get_db),
):
    user_id = current_user["user_id"] if current_user else None
    content_ids = {payload.content_id} if payload.content_id else set()
    taxonomy_map = await _fetch_content_taxonomy_map(db, content_ids)

    record = await _insert_single_interaction(db, payload, user_id, taxonomy_map)
    await db.commit()
    return InteractionEventOut(**record)


@router.post("/interactions/batch", response_model=BatchInteractionResponse, status_code=status.HTTP_201_CREATED)
async def log_interactions_batch(
    payload: BatchInteractionRequest,
    current_user: dict[str, Any] | None = Depends(get_optional_current_user),
    db: AsyncSession = Depends(get_db),
):
    user_id = current_user["user_id"] if current_user else None
    content_ids = {ev.content_id for ev in payload.events if ev.content_id is not None}
    taxonomy_map = await _fetch_content_taxonomy_map(db, content_ids)

    inserted_ids: list[UUID] = []
    for event in payload.events:
        record = await _insert_single_interaction(db, event, user_id, taxonomy_map)
        inserted_ids.append(record["interaction_id"])

    await db.commit()
    return BatchInteractionResponse(recorded_count=len(inserted_ids), interaction_ids=inserted_ids)


# ---------------------------------------------------------------------------
# 2. SEARCH QUERY LOGGING (Writes to search_queries + user_interactions)
# ---------------------------------------------------------------------------
@router.post("/interactions/searches", response_model=SearchLogResponse, status_code=status.HTTP_201_CREATED)
async def log_search_query(
    payload: SearchLogRequest,
    current_user: dict[str, Any] | None = Depends(get_optional_current_user),
    db: AsyncSession = Depends(get_db),
):
    user_id = current_user["user_id"] if current_user else None
    clean_query = payload.query_text.strip()

    search_row = (
        await db.execute(
            text(
                """
                INSERT INTO search_queries (
                    user_id, session_id, query_text, region_id,
                    category_id, content_type, result_count
                )
                VALUES (
                    :user_id, :session_id, :query_text, :region_id,
                    :category_id, CAST(:content_type AS content_type_enum), :result_count
                )
                RETURNING
                    search_id, user_id, session_id, query_text,
                    region_id, category_id, content_type::text AS content_type,
                    result_count, searched_at
                """
            ),
            {
                "user_id": user_id,
                "session_id": payload.session_id.strip(),
                "query_text": clean_query,
                "region_id": payload.region_id,
                "category_id": payload.category_id,
                "content_type": payload.content_type,
                "result_count": payload.result_count,
            },
        )
    ).mappings().first()

    search_context = {
        "search_id": str(search_row["search_id"]),
        "query_text": clean_query,
        "region_id": payload.region_id,
        "category_id": payload.category_id,
        "content_type": payload.content_type,
        "result_count": payload.result_count,
    }

    interaction_id = await db.scalar(
        text(
            """
            INSERT INTO user_interactions (
                user_id, session_id, content_id, event_type, feed_source, context_json
            )
            VALUES (
                :user_id, :session_id, NULL, 'search', 'search', CAST(:context_json AS JSONB)
            )
            RETURNING interaction_id
            """
        ),
        {
            "user_id": user_id,
            "session_id": payload.session_id.strip(),
            "context_json": json.dumps(search_context),
        },
    )

    await db.commit()
    return SearchLogResponse(**dict(search_row), interaction_id=interaction_id)


# ---------------------------------------------------------------------------
# 3. RECOMMENDATION-READY AFFINITY & POPULARITY BASELINE ENDPOINTS
# ---------------------------------------------------------------------------
@router.get("/interactions/affinity/me", response_model=AffinitySummaryOut)
async def get_my_taxonomy_affinity(
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    uid = current_user["user_id"]

    region_rows = (
        await db.execute(
            text(
                """
                SELECT
                    (ui.context_json->>'region_id')::INT AS region_id,
                    MAX(ui.context_json->>'region_name') AS region_name,
                    COUNT(*) AS interaction_count
                FROM user_interactions ui
                WHERE ui.user_id = :uid
                  AND ui.context_json ? 'region_id'
                  AND ui.event_type NOT IN ('skip', 'unlike', 'unbookmark', 'unfollow')
                GROUP BY 1
                ORDER BY interaction_count DESC
                LIMIT 10
                """
            ),
            {"uid": uid},
        )
    ).mappings().all()

    category_rows = (
        await db.execute(
            text(
                """
                SELECT
                    (ui.context_json->>'category_id')::INT AS category_id,
                    MAX(ui.context_json->>'category_slug') AS category_slug,
                    COUNT(*) AS interaction_count
                FROM user_interactions ui
                WHERE ui.user_id = :uid
                  AND ui.context_json ? 'category_id'
                  AND ui.event_type NOT IN ('skip', 'unlike', 'unbookmark', 'unfollow')
                GROUP BY 1
                ORDER BY interaction_count DESC
                LIMIT 10
                """
            ),
            {"uid": uid},
        )
    ).mappings().all()

    tag_rows = (
        await db.execute(
            text(
                """
                SELECT
                    tag_elem AS tag,
                    COUNT(*) AS interaction_count
                FROM user_interactions ui,
                     jsonb_array_elements_text(COALESCE(ui.context_json->'tags', '[]'::jsonb)) AS tag_elem
                WHERE ui.user_id = :uid
                  AND ui.event_type NOT IN ('skip', 'unlike', 'unbookmark', 'unfollow')
                GROUP BY tag_elem
                ORDER BY interaction_count DESC
                LIMIT 15
                """
            ),
            {"uid": uid},
        )
    ).mappings().all()

    return AffinitySummaryOut(
        user_id=uid,
        top_regions=[dict(r) for r in region_rows],
        top_categories=[dict(c) for c in category_rows],
        top_tags=[dict(t) for t in tag_rows],
    )


@router.get("/recommendations/baseline", response_model=list[BaselineRecommendationItem])
async def get_popularity_baseline_feed(
    content_type: Literal["post", "reel", "article"] | None = Query(default=None),
    region_id: int | None = Query(default=None),
    category_id: int | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    conditions = [
        "c.visibility = 'public'",
        "c.moderation_status = 'published'",
    ]
    params: dict[str, Any] = {"limit": limit}

    if content_type:
        conditions.append("c.content_type = CAST(:content_type AS content_type_enum)")
        params["content_type"] = content_type
    if region_id is not None:
        conditions.append("c.region_id = :region_id")
        params["region_id"] = region_id
    if category_id is not None:
        conditions.append("c.category_id = :category_id")
        params["category_id"] = category_id

    rows = (
        await db.execute(
            text(
                f"""
                SELECT
                    c.content_id,
                    c.content_type::text AS content_type,
                    c.title,
                    c.caption_or_summary,
                    c.region_id,
                    r.name AS region_name,
                    c.category_id,
                    hc.name AS category_name,
                    c.tags,
                    c.like_count,
                    c.comment_count,
                    c.save_count,
                    c.view_count,
                    ROUND(
                        (
                            (c.like_count * 3.0 + c.save_count * 5.0 + c.comment_count * 4.0 + c.view_count * 0.2)
                            / POWER(GREATEST(EXTRACT(EPOCH FROM (NOW() - COALESCE(c.published_at, c.created_at))) / 3600.0, 0) + 2.0, 1.2)
                        )::NUMERIC,
                        4
                    )::FLOAT AS popularity_score,
                    c.published_at
                FROM contents c
                LEFT JOIN regions r ON r.region_id = c.region_id
                LEFT JOIN heritage_categories hc ON hc.category_id = c.category_id
                WHERE {' AND '.join(conditions)}
                ORDER BY popularity_score DESC, c.published_at DESC NULLS LAST
                LIMIT :limit
                """
            ),
            params,
        )
    ).mappings().all()

    return [
        BaselineRecommendationItem(
            **{**dict(row), "tags": list(row["tags"] or [])}
        )
        for row in rows
    ]
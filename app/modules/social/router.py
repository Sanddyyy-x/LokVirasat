from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.modules.social.schemas import (
    CommentAuthorOut,
    CommentOut,
    CreateCommentRequest,
    FollowResponse,
    FollowUserSummary,
    LikeResponse,
    SaveContentRequest,
    SavedItemOut,
    SaveResponse,
    UpdateCommentRequest,
)

router = APIRouter(tags=["social"])


# ---------------------------------------------------------------------------
# 1. CONTENT LIKES (Idempotent)
# ---------------------------------------------------------------------------
@router.post("/contents/{content_id}/like", response_model=LikeResponse)
async def like_content(
    content_id: UUID,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    exists = await db.scalar(
        text("SELECT 1 FROM contents WHERE content_id = :id AND moderation_status <> 'removed'"),
        {"id": content_id},
    )
    if not exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found")

    inserted = await db.scalar(
        text(
            """
            INSERT INTO content_likes (user_id, content_id)
            VALUES (:user_id, :content_id)
            ON CONFLICT (user_id, content_id) DO NOTHING
            RETURNING 1
            """
        ),
        {"user_id": current_user["user_id"], "content_id": content_id},
    )

    if inserted:
        await db.execute(
            text("UPDATE contents SET like_count = like_count + 1 WHERE content_id = :id"),
            {"id": content_id},
        )

    await db.commit()
    like_count = await db.scalar(
        text("SELECT like_count FROM contents WHERE content_id = :id"),
        {"id": content_id},
    )
    return LikeResponse(content_id=content_id, liked=True, like_count=like_count or 0)


@router.delete("/contents/{content_id}/like", response_model=LikeResponse)
async def unlike_content(
    content_id: UUID,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    exists = await db.scalar(
        text("SELECT 1 FROM contents WHERE content_id = :id"),
        {"id": content_id},
    )
    if not exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found")

    deleted = await db.scalar(
        text(
            """
            DELETE FROM content_likes
            WHERE user_id = :user_id AND content_id = :content_id
            RETURNING 1
            """
        ),
        {"user_id": current_user["user_id"], "content_id": content_id},
    )

    if deleted:
        await db.execute(
            text("UPDATE contents SET like_count = GREATEST(like_count - 1, 0) WHERE content_id = :id"),
            {"id": content_id},
        )

    await db.commit()
    like_count = await db.scalar(
        text("SELECT like_count FROM contents WHERE content_id = :id"),
        {"id": content_id},
    )
    return LikeResponse(content_id=content_id, liked=False, like_count=like_count or 0)


# ---------------------------------------------------------------------------
# 2. THREADED COMMENTS
# ---------------------------------------------------------------------------
@router.post("/contents/{content_id}/comments", response_model=CommentOut, status_code=status.HTTP_201_CREATED)
async def create_comment(
    content_id: UUID,
    payload: CreateCommentRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    exists = await db.scalar(
        text("SELECT 1 FROM contents WHERE content_id = :id AND moderation_status <> 'removed'"),
        {"id": content_id},
    )
    if not exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found")

    if payload.parent_comment_id is not None:
        parent_content_id = await db.scalar(
            text(
                """
                SELECT content_id FROM comments
                WHERE comment_id = :parent_id AND moderation_status <> 'removed'
                """
            ),
            {"parent_id": payload.parent_comment_id},
        )
        if parent_content_id != content_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="parent_comment_id must belong to the same content item",
            )

    row = (
        await db.execute(
            text(
                """
                INSERT INTO comments (content_id, user_id, parent_comment_id, body)
                VALUES (:content_id, :user_id, :parent_comment_id, :body)
                RETURNING comment_id, content_id, parent_comment_id, body, is_edited,
                          moderation_status::text AS moderation_status, created_at, updated_at
                """
            ),
            {
                "content_id": content_id,
                "user_id": current_user["user_id"],
                "parent_comment_id": payload.parent_comment_id,
                "body": payload.body.strip(),
            },
        )
    ).mappings().first()

    await db.execute(
        text("UPDATE contents SET comment_count = comment_count + 1 WHERE content_id = :id"),
        {"id": content_id},
    )
    await db.commit()

    return CommentOut(
        **dict(row),
        author=CommentAuthorOut(
            user_id=current_user["user_id"],
            username=current_user["username"],
            full_name=current_user["full_name"],
            is_verified_badge=current_user["is_verified_badge"],
        ),
    )


@router.get("/contents/{content_id}/comments", response_model=list[CommentOut])
async def list_comments(
    content_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            text(
                """
                SELECT
                    cm.comment_id,
                    cm.content_id,
                    cm.parent_comment_id,
                    cm.body,
                    cm.is_edited,
                    cm.moderation_status::text AS moderation_status,
                    cm.created_at,
                    cm.updated_at,
                    u.user_id AS author_user_id,
                    u.username AS author_username,
                    u.full_name AS author_full_name,
                    u.is_verified_badge AS author_is_verified_badge
                FROM comments cm
                JOIN users u ON u.user_id = cm.user_id
                WHERE cm.content_id = :content_id
                  AND cm.moderation_status = 'published'
                ORDER BY cm.created_at ASC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"content_id": content_id, "limit": limit, "offset": offset},
        )
    ).mappings().all()

    return [
        CommentOut(
            comment_id=r["comment_id"],
            content_id=r["content_id"],
            parent_comment_id=r["parent_comment_id"],
            body=r["body"],
            is_edited=r["is_edited"],
            moderation_status=r["moderation_status"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            author=CommentAuthorOut(
                user_id=r["author_user_id"],
                username=r["author_username"],
                full_name=r["author_full_name"],
                is_verified_badge=r["author_is_verified_badge"],
            ),
        )
        for r in rows
    ]


@router.patch("/comments/{comment_id}", response_model=CommentOut)
async def update_comment(
    comment_id: UUID,
    payload: UpdateCommentRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    existing = (
        await db.execute(
            text("SELECT user_id FROM comments WHERE comment_id = :id"),
            {"id": comment_id},
        )
    ).mappings().first()

    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")
    if existing["user_id"] != current_user["user_id"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the author can edit this comment")

    row = (
        await db.execute(
            text(
                """
                UPDATE comments
                   SET body = :body, is_edited = TRUE
                 WHERE comment_id = :id
                RETURNING comment_id, content_id, parent_comment_id, body, is_edited,
                          moderation_status::text AS moderation_status, created_at, updated_at
                """
            ),
            {"id": comment_id, "body": payload.body.strip()},
        )
    ).mappings().first()
    await db.commit()

    return CommentOut(
        **dict(row),
        author=CommentAuthorOut(
            user_id=current_user["user_id"],
            username=current_user["username"],
            full_name=current_user["full_name"],
            is_verified_badge=current_user["is_verified_badge"],
        ),
    )


@router.delete("/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_comment(
    comment_id: UUID,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    existing = (
        await db.execute(
            text("SELECT user_id, content_id FROM comments WHERE comment_id = :id"),
            {"id": comment_id},
        )
    ).mappings().first()

    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")

    roles = set(current_user.get("roles") or [])
    is_staff = bool(roles.intersection({"moderator", "administrator"}))
    if existing["user_id"] != current_user["user_id"] and not is_staff:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to delete this comment")

    await db.execute(text("DELETE FROM comments WHERE comment_id = :id"), {"id": comment_id})
    await db.execute(
        text(
            """
            UPDATE contents
               SET comment_count = (
                   SELECT COUNT(*) FROM comments
                   WHERE content_id = :content_id AND moderation_status = 'published'
               )
             WHERE content_id = :content_id
            """
        ),
        {"content_id": existing["content_id"]},
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# 3. SOCIAL GRAPH (Follows)
# ---------------------------------------------------------------------------
@router.post("/users/{user_id}/follow", response_model=FollowResponse)
async def follow_user(
    user_id: UUID,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user_id == current_user["user_id"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot follow yourself")

    target_exists = await db.scalar(
        text("SELECT 1 FROM users WHERE user_id = :id AND account_status = 'active'"),
        {"id": user_id},
    )
    if not target_exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    await db.execute(
        text(
            """
            INSERT INTO follows (follower_id, following_id)
            VALUES (:follower_id, :following_id)
            ON CONFLICT DO NOTHING
            """
        ),
        {"follower_id": current_user["user_id"], "following_id": user_id},
    )
    await db.commit()

    follower_count = await db.scalar(
        text("SELECT COUNT(*) FROM follows WHERE following_id = :id"),
        {"id": user_id},
    )
    return FollowResponse(target_user_id=user_id, following=True, follower_count=follower_count or 0)


@router.delete("/users/{user_id}/follow", response_model=FollowResponse)
async def unfollow_user(
    user_id: UUID,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        text("DELETE FROM follows WHERE follower_id = :follower_id AND following_id = :following_id"),
        {"follower_id": current_user["user_id"], "following_id": user_id},
    )
    await db.commit()

    follower_count = await db.scalar(
        text("SELECT COUNT(*) FROM follows WHERE following_id = :id"),
        {"id": user_id},
    )
    return FollowResponse(target_user_id=user_id, following=False, follower_count=follower_count or 0)


@router.get("/users/{username}/followers", response_model=list[FollowUserSummary])
async def list_followers(username: str, db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            text(
                """
                SELECT u.user_id, u.username, u.full_name, u.is_verified_badge, f.created_at AS followed_at
                FROM follows f
                JOIN users target ON target.user_id = f.following_id
                JOIN users u ON u.user_id = f.follower_id
                WHERE LOWER(target.username) = :username
                ORDER BY f.created_at DESC
                """
            ),
            {"username": username.strip().lower()},
        )
    ).mappings().all()
    return [FollowUserSummary(**dict(r)) for r in rows]


@router.get("/users/{username}/following", response_model=list[FollowUserSummary])
async def list_following(username: str, db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            text(
                """
                SELECT u.user_id, u.username, u.full_name, u.is_verified_badge, f.created_at AS followed_at
                FROM follows f
                JOIN users actor ON actor.user_id = f.follower_id
                JOIN users u ON u.user_id = f.following_id
                WHERE LOWER(actor.username) = :username
                ORDER BY f.created_at DESC
                """
            ),
            {"username": username.strip().lower()},
        )
    ).mappings().all()
    return [FollowUserSummary(**dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# 4. BOOKMARKS / SAVED COLLECTIONS
# ---------------------------------------------------------------------------
@router.post("/contents/{content_id}/save", response_model=SaveResponse)
async def save_content(
    content_id: UUID,
    payload: SaveContentRequest | None = None,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    exists = await db.scalar(
        text("SELECT 1 FROM contents WHERE content_id = :id AND moderation_status <> 'removed'"),
        {"id": content_id},
    )
    if not exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found")

    collection_name = (payload.collection_name if payload else "Saved").strip()
    already_saved = await db.scalar(
        text("SELECT 1 FROM saved_items WHERE user_id = :u AND content_id = :c"),
        {"u": current_user["user_id"], "c": content_id},
    )

    if already_saved:
        await db.execute(
            text(
                """
                UPDATE saved_items
                   SET collection_name = :collection_name
                 WHERE user_id = :u AND content_id = :c
                """
            ),
            {"u": current_user["user_id"], "c": content_id, "collection_name": collection_name},
        )
    else:
        await db.execute(
            text(
                """
                INSERT INTO saved_items (user_id, content_id, collection_name)
                VALUES (:u, :c, :collection_name)
                """
            ),
            {"u": current_user["user_id"], "c": content_id, "collection_name": collection_name},
        )
        await db.execute(
            text("UPDATE contents SET save_count = save_count + 1 WHERE content_id = :id"),
            {"id": content_id},
        )

    await db.commit()
    save_count = await db.scalar(
        text("SELECT save_count FROM contents WHERE content_id = :id"),
        {"id": content_id},
    )
    return SaveResponse(
        content_id=content_id,
        saved=True,
        collection_name=collection_name,
        save_count=save_count or 0,
    )


@router.delete("/contents/{content_id}/save", response_model=SaveResponse)
async def unsave_content(
    content_id: UUID,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    exists = await db.scalar(
        text("SELECT 1 FROM contents WHERE content_id = :id"),
        {"id": content_id},
    )
    if not exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found")

    deleted = await db.scalar(
        text(
            """
            DELETE FROM saved_items
            WHERE user_id = :u AND content_id = :c
            RETURNING 1
            """
        ),
        {"u": current_user["user_id"], "c": content_id},
    )

    if deleted:
        await db.execute(
            text("UPDATE contents SET save_count = GREATEST(save_count - 1, 0) WHERE content_id = :id"),
            {"id": content_id},
        )

    await db.commit()
    save_count = await db.scalar(
        text("SELECT save_count FROM contents WHERE content_id = :id"),
        {"id": content_id},
    )
    return SaveResponse(
        content_id=content_id,
        saved=False,
        collection_name=None,
        save_count=save_count or 0,
    )


@router.get("/users/me/saved", response_model=list[SavedItemOut])
async def list_my_saved_items(
    collection_name: str | None = Query(default=None),
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    params: dict[str, Any] = {"user_id": current_user["user_id"]}
    collection_filter = ""
    if collection_name:
        collection_filter = "AND s.collection_name = :collection_name"
        params["collection_name"] = collection_name.strip()

    rows = (
        await db.execute(
            text(
                f"""
                SELECT
                    c.content_id,
                    c.content_type::text AS content_type,
                    c.title,
                    c.caption_or_summary,
                    s.collection_name,
                    s.created_at AS saved_at,
                    u.username AS author_username,
                    u.full_name AS author_full_name
                FROM saved_items s
                JOIN contents c ON c.content_id = s.content_id
                JOIN users u ON u.user_id = c.author_id
                WHERE s.user_id = :user_id
                  AND c.moderation_status <> 'removed'
                  {collection_filter}
                ORDER BY s.created_at DESC
                """
            ),
            params,
        )
    ).mappings().all()

    return [SavedItemOut(**dict(r)) for r in rows]
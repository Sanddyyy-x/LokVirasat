from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import fetch_user_with_roles, get_current_user
from app.modules.users.schemas import (
    MyProfileResponse,
    PublicProfileResponse,
    UpdateProfileRequest,
)

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=MyProfileResponse)
async def get_my_profile(current_user: dict[str, Any] = Depends(get_current_user)):
    return MyProfileResponse(**current_user)


@router.patch("/me", response_model=MyProfileResponse)
async def update_my_profile(
    payload: UpdateProfileRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return MyProfileResponse(**current_user)

    set_clauses = []
    params: dict[str, Any] = {"user_id": current_user["user_id"]}
    for field, value in updates.items():
        set_clauses.append(f"{field} = :{field}")
        params[field] = value.strip() if isinstance(value, str) else value

    query = f"UPDATE users SET {', '.join(set_clauses)} WHERE user_id = :user_id"
    await db.execute(text(query), params)
    await db.commit()

    refreshed = await fetch_user_with_roles(db, str(current_user["user_id"]))
    return MyProfileResponse(**refreshed)


@router.get("/{username}", response_model=PublicProfileResponse)
async def get_public_profile(username: str, db: AsyncSession = Depends(get_db)):
    row = (
        await db.execute(
            text(
                """
                SELECT
                    u.user_id,
                    u.username,
                    u.full_name,
                    u.bio,
                    u.avatar_storage_key,
                    u.home_state,
                    u.preferred_language,
                    u.is_verified_badge,
                    u.created_at,
                    COALESCE(
                        ARRAY_AGG(r.role_key ORDER BY r.role_id) FILTER (WHERE r.role_key IS NOT NULL),
                        '{}'
                    ) AS roles,
                    (SELECT COUNT(*) FROM follows f WHERE f.following_id = u.user_id) AS follower_count,
                    (SELECT COUNT(*) FROM follows f WHERE f.follower_id = u.user_id) AS following_count,
                    (
                        SELECT COUNT(*) FROM contents c
                        WHERE c.author_id = u.user_id
                          AND c.visibility = 'public'
                          AND c.moderation_status = 'published'
                    ) AS published_content_count
                FROM users u
                LEFT JOIN user_roles ur ON ur.user_id = u.user_id
                LEFT JOIN roles r ON r.role_id = ur.role_id
                WHERE LOWER(u.username) = :username
                  AND u.account_status = 'active'
                GROUP BY u.user_id
                """
            ),
            {"username": username.strip().lower()},
        )
    ).mappings().first()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return PublicProfileResponse(**dict(row))
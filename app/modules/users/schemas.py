from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, EmailStr, Field


class MyProfileResponse(BaseModel):
    user_id: UUID
    username: str
    email: EmailStr
    full_name: str
    bio: str | None
    avatar_storage_key: str | None
    home_state: str | None
    preferred_language: str
    account_status: str
    is_verified_badge: bool
    roles: list[str]
    created_at: datetime


class UpdateProfileRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    bio: str | None = Field(default=None, max_length=1000)
    home_state: str | None = Field(default=None, max_length=80)
    preferred_language: str | None = Field(default=None, max_length=10)


class PublicProfileResponse(BaseModel):
    user_id: UUID
    username: str
    full_name: str
    bio: str | None
    avatar_storage_key: str | None
    home_state: str | None
    preferred_language: str
    is_verified_badge: bool
    roles: list[str]
    follower_count: int
    following_count: int
    published_content_count: int
    created_at: datetime
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field


class LikeResponse(BaseModel):
    content_id: UUID
    liked: bool
    like_count: int


class CreateCommentRequest(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    parent_comment_id: UUID | None = None


class UpdateCommentRequest(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


class CommentAuthorOut(BaseModel):
    user_id: UUID
    username: str
    full_name: str
    is_verified_badge: bool


class CommentOut(BaseModel):
    comment_id: UUID
    content_id: UUID
    parent_comment_id: UUID | None
    body: str
    is_edited: bool
    moderation_status: str
    created_at: datetime
    updated_at: datetime
    author: CommentAuthorOut


class FollowResponse(BaseModel):
    target_user_id: UUID
    following: bool
    follower_count: int


class FollowUserSummary(BaseModel):
    user_id: UUID
    username: str
    full_name: str
    is_verified_badge: bool
    followed_at: datetime


class SaveContentRequest(BaseModel):
    collection_name: str = Field(default="Saved", min_length=1, max_length=80)


class SaveResponse(BaseModel):
    content_id: UUID
    saved: bool
    collection_name: str | None
    save_count: int


class SavedItemOut(BaseModel):
    content_id: UUID
    content_type: str
    title: str | None
    caption_or_summary: str | None
    collection_name: str
    saved_at: datetime
    author_username: str
    author_full_name: str
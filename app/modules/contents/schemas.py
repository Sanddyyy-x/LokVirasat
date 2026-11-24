from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, Field, model_validator


class ReelDetailsIn(BaseModel):
    video_media_id: UUID
    thumbnail_media_id: UUID
    duration_seconds: int = Field(gt=0, le=180)
    aspect_ratio: str = Field(default="9:16", max_length=12)
    audio_track_name: str | None = Field(default=None, max_length=160)
    transcript_text: str | None = None


class ArticleDetailsIn(BaseModel):
    body_markdown: str = Field(min_length=1)
    cover_media_id: UUID | None = None
    reading_time_mins: int = Field(default=1, gt=0)
    references_json: list[dict[str, Any]] = Field(default_factory=list)
    primary_source_notes: str | None = None


class CreateContentRequest(BaseModel):
    content_type: Literal["post", "reel", "article"]
    title: str | None = Field(default=None, max_length=240)
    caption_or_summary: str | None = None
    language_code: str = Field(default="en", max_length=10)
    region_id: int | None = None
    category_id: int | None = None
    community_id: UUID | None = None
    historical_era: str | None = Field(default=None, max_length=100)
    tags: list[str] = Field(default_factory=list)
    visibility: Literal["public", "community_only", "draft", "archived"] = "public"
    media_ids: list[UUID] = Field(default_factory=list)
    reel_details: ReelDetailsIn | None = None
    article_details: ArticleDetailsIn | None = None

    @model_validator(mode="after")
    def validate_subtype_requirements(self):
        if self.content_type == "article":
            if not self.title or not self.title.strip():
                raise ValueError("Articles require a non-empty title")
            if self.article_details is None:
                raise ValueError("Articles require article_details")
        elif self.content_type == "reel":
            if self.reel_details is None:
                raise ValueError("Reels require reel_details")
        return self


class UpdateContentRequest(BaseModel):
    title: str | None = Field(default=None, max_length=240)
    caption_or_summary: str | None = None
    language_code: str | None = Field(default=None, max_length=10)
    region_id: int | None = None
    category_id: int | None = None
    historical_era: str | None = Field(default=None, max_length=100)
    tags: list[str] | None = None
    visibility: Literal["public", "community_only", "draft", "archived"] | None = None
    body_markdown: str | None = Field(default=None, min_length=1)
    references_json: list[dict[str, Any]] | None = None
    primary_source_notes: str | None = None


class AuthorSummaryOut(BaseModel):
    user_id: UUID
    username: str
    full_name: str
    is_verified_badge: bool


class ContentOut(BaseModel):
    content_id: UUID
    content_type: str
    title: str | None
    caption_or_summary: str | None
    language_code: str
    region_id: int | None
    region_name: str | None
    category_id: int | None
    category_name: str | None
    community_id: UUID | None
    historical_era: str | None
    tags: list[str]
    visibility: str
    moderation_status: str
    verification_status: str
    like_count: int
    comment_count: int
    save_count: int
    view_count: int
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime
    author: AuthorSummaryOut
    reel_details: dict[str, Any] | None = None
    article_details: dict[str, Any] | None = None
    media_assets: list[dict[str, Any]] = Field(default_factory=list)


class ContentFeedResponse(BaseModel):
    items: list[ContentOut]
    limit: int
    offset: int
    has_more: bool
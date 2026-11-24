from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, Field, model_validator

EventType = Literal[
    "impression",
    "view",
    "video_progress",
    "skip",
    "like",
    "unlike",
    "bookmark",
    "unbookmark",
    "follow",
    "unfollow",
    "search",
]

FeedSource = Literal[
    "home",
    "reels",
    "explore",
    "search",
    "community",
    "creator_profile",
]

CONTENT_SCOPED_EVENTS = {
    "impression",
    "view",
    "video_progress",
    "skip",
    "like",
    "unlike",
    "bookmark",
    "unbookmark",
}


class InteractionEventIn(BaseModel):
    session_id: str = Field(min_length=4, max_length=80)
    event_type: EventType
    feed_source: FeedSource
    content_id: UUID | None = None
    target_user_id: UUID | None = None
    dwell_ms: int | None = Field(default=None, ge=0)
    watch_ms: int | None = Field(default=None, ge=0)
    completion_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    position_index: int | None = Field(default=None, ge=0, le=32767)
    context_json: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime | None = None

    @model_validator(mode="after")
    def validate_event_fields(self):
        if self.event_type in CONTENT_SCOPED_EVENTS and self.content_id is None:
            raise ValueError(f"content_id is required for '{self.event_type}' events")
        if self.event_type in {"follow", "unfollow"} and self.target_user_id is None:
            raise ValueError(f"target_user_id is required for '{self.event_type}' events")
        return self


class BatchInteractionRequest(BaseModel):
    events: list[InteractionEventIn] = Field(min_length=1, max_length=100)


class InteractionEventOut(BaseModel):
    interaction_id: UUID
    user_id: UUID | None
    session_id: str
    content_id: UUID | None
    event_type: str
    feed_source: str
    dwell_ms: int | None
    watch_ms: int | None
    completion_pct: float | None
    position_index: int | None
    context_json: dict[str, Any]
    occurred_at: datetime


class BatchInteractionResponse(BaseModel):
    recorded_count: int
    interaction_ids: list[UUID]


class SearchLogRequest(BaseModel):
    session_id: str = Field(min_length=4, max_length=80)
    query_text: str = Field(min_length=1, max_length=300)
    region_id: int | None = None
    category_id: int | None = None
    content_type: Literal["post", "reel", "article"] | None = None
    result_count: int = Field(default=0, ge=0)


class SearchLogResponse(BaseModel):
    search_id: UUID
    interaction_id: UUID
    user_id: UUID | None
    session_id: str
    query_text: str
    region_id: int | None
    category_id: int | None
    content_type: str | None
    result_count: int
    searched_at: datetime


class AffinitySummaryOut(BaseModel):
    user_id: UUID
    top_regions: list[dict[str, Any]]
    top_categories: list[dict[str, Any]]
    top_tags: list[dict[str, Any]]


class BaselineRecommendationItem(BaseModel):
    content_id: UUID
    content_type: str
    title: str | None
    caption_or_summary: str | None
    region_id: int | None
    region_name: str | None
    category_id: int | None
    category_name: str | None
    tags: list[str]
    like_count: int
    comment_count: int
    save_count: int
    view_count: int
    popularity_score: float
    published_at: datetime | None
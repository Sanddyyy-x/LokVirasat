-- =============================================================================
-- Migration 002: Recommendation-Ready Interaction & Search Event Tracking
-- =============================================================================

CREATE TABLE IF NOT EXISTS user_interactions (
    interaction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID        REFERENCES users(user_id) ON DELETE SET NULL,
    session_id     VARCHAR(80) NOT NULL,
    content_id     UUID        REFERENCES contents(content_id) ON DELETE CASCADE,
    event_type     VARCHAR(24) NOT NULL
                   CHECK (event_type IN (
                       'impression', 'view', 'video_progress', 'skip',
                       'like', 'unlike', 'bookmark', 'unbookmark',
                       'follow', 'unfollow', 'search'
                   )),
    feed_source    VARCHAR(32) NOT NULL
                   CHECK (feed_source IN (
                       'home', 'reels', 'explore', 'search', 'community', 'creator_profile'
                   )),
    dwell_ms       INT          CHECK (dwell_ms IS NULL OR dwell_ms >= 0),
    watch_ms       INT          CHECK (watch_ms IS NULL OR watch_ms >= 0),
    completion_pct NUMERIC(5,2) CHECK (completion_pct IS NULL OR (completion_pct >= 0 AND completion_pct <= 100)),
    position_index SMALLINT     CHECK (position_index IS NULL OR position_index >= 0),
    context_json   JSONB        NOT NULL DEFAULT '{}'::JSONB,
    occurred_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS search_queries (
    search_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID         REFERENCES users(user_id) ON DELETE SET NULL,
    session_id   VARCHAR(80)  NOT NULL,
    query_text   TEXT         NOT NULL CHECK (char_length(trim(query_text)) > 0),
    region_id    INT          REFERENCES regions(region_id) ON DELETE SET NULL,
    category_id  INT          REFERENCES heritage_categories(category_id) ON DELETE SET NULL,
    content_type content_type_enum,
    result_count INT          NOT NULL DEFAULT 0 CHECK (result_count >= 0),
    searched_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_interactions_user_time
    ON user_interactions (user_id, occurred_at DESC)
    WHERE user_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_interactions_content_event
    ON user_interactions (content_id, event_type, occurred_at DESC)
    WHERE content_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_interactions_feed_time
    ON user_interactions (feed_source, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_search_queries_time
    ON search_queries (searched_at DESC);
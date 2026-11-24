-- =============================================================================
-- LokVirasat — Cultural Heritage Social Media Platform (Revised MVP Schema)
-- Project Context: Smart India Hackathon 2026 (PS 26214) & Final-Year Project
-- Target DBMS: PostgreSQL 15+
-- =============================================================================
-- Scope Summary:
--   - 18 Essential MVP Tables across 6 Functional Domains
--   - 6 Core Enumerated Types (remaining status fields use CHECK constraints
--     for friction-free migrations during iterative development)
--   - 10 High-Impact Indexes aligned directly to feed, search, and queue queries
--   - Subtype Consistency Triggers enforcing post/reel/article/media integrity
--
-- Deferred to Phase 2 (documented in architecture study):
--   stories, story_views, cultural_events, event_rsvps, notifications,
--   organization_profiles, tags, content_tags
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- 1. CORE ENUMERATED TYPES (6 Total)
CREATE TYPE content_type_enum AS ENUM ('post', 'reel', 'article');
CREATE TYPE media_kind_enum AS ENUM ('image', 'video', 'audio', 'document');
CREATE TYPE visibility_enum AS ENUM ('public', 'community_only', 'draft', 'archived');
CREATE TYPE moderation_status_enum AS ENUM ('published', 'under_review', 'flagged', 'removed');
CREATE TYPE verification_status_enum AS ENUM ('unverified', 'pending_review', 'verified', 'needs_citation', 'disputed');
CREATE TYPE report_status_enum AS ENUM ('open', 'in_review', 'resolved_removed', 'resolved_dismissed');

-- 2. DOMAIN 1: IDENTITY, UNIFIED ACCOUNTS & RBAC (4 Tables)
CREATE TABLE users (
    user_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username           VARCHAR(50)  NOT NULL UNIQUE,
    email              VARCHAR(255) NOT NULL UNIQUE,
    password_hash      TEXT         NOT NULL,
    full_name          VARCHAR(120) NOT NULL,
    bio                TEXT,
    avatar_storage_key TEXT,
    home_state         VARCHAR(80),
    preferred_language VARCHAR(10)  NOT NULL DEFAULT 'en',
    account_status     VARCHAR(20)  NOT NULL DEFAULT 'active'
                       CHECK (account_status IN ('active', 'suspended', 'deactivated')),
    is_verified_badge  BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE roles (
    role_id     SMALLSERIAL PRIMARY KEY,
    role_key    VARCHAR(40) NOT NULL UNIQUE,
    description TEXT        NOT NULL
);

INSERT INTO roles (role_key, description) VALUES
    ('registered_user',      'Like, comment, follow, save, share, and report cultural content'),
    ('cultural_creator',     'Publish cultural posts, reels, photographs, and articles'),
    ('researcher_historian', 'Document and peer-verify historical and cultural accuracy'),
    ('organization',         'Official cultural bodies showcasing traditions and curated archives'),
    ('moderator',            'Review moderation reports and enforce community guidelines'),
    ('administrator',        'Manage platform governance, roles, taxonomy, and researcher approvals');

CREATE TABLE user_roles (
    user_id     UUID        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    role_id     SMALLINT    NOT NULL REFERENCES roles(role_id) ON DELETE RESTRICT,
    assigned_by UUID        REFERENCES users(user_id) ON DELETE SET NULL,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, role_id)
);

CREATE TABLE researcher_applications (
    application_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id              UUID         NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    institution          VARCHAR(160) NOT NULL,
    specialization       VARCHAR(160) NOT NULL,
    academic_profile_url TEXT,
    credential_doc_key   TEXT         NOT NULL,
    status               VARCHAR(20)  NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending', 'approved', 'rejected')),
    reviewed_by          UUID         REFERENCES users(user_id) ON DELETE SET NULL,
    review_notes         TEXT,
    submitted_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    reviewed_at          TIMESTAMPTZ
);

CREATE UNIQUE INDEX idx_researcher_one_pending
    ON researcher_applications (user_id)
    WHERE status = 'pending';

-- 3. DOMAIN 2: CULTURAL HERITAGE TAXONOMY (2 Tables)
CREATE TABLE regions (
    region_id        SERIAL PRIMARY KEY,
    name             VARCHAR(120) NOT NULL,
    region_type      VARCHAR(24)  NOT NULL
                     CHECK (region_type IN ('country', 'cultural_zone', 'state', 'union_territory', 'district', 'city')),
    parent_region_id INT          REFERENCES regions(region_id) ON DELETE SET NULL,
    iso_code         VARCHAR(16),
    UNIQUE (name, region_type, parent_region_id)
);

CREATE TABLE heritage_categories (
    category_id        SERIAL PRIMARY KEY,
    slug               VARCHAR(80)  NOT NULL UNIQUE,
    name               VARCHAR(120) NOT NULL,
    parent_category_id INT          REFERENCES heritage_categories(category_id) ON DELETE SET NULL,
    description        TEXT
);

-- 4. DOMAIN 3: CULTURAL COMMUNITIES (2 Tables)
CREATE TABLE communities (
    community_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug         VARCHAR(90)  NOT NULL UNIQUE,
    name         VARCHAR(140) NOT NULL,
    description  TEXT,
    banner_key   TEXT,
    region_id    INT          REFERENCES regions(region_id) ON DELETE SET NULL,
    category_id  INT          REFERENCES heritage_categories(category_id) ON DELETE SET NULL,
    created_by   UUID         NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    privacy_type VARCHAR(16)  NOT NULL DEFAULT 'public'
                 CHECK (privacy_type IN ('public', 'private')),
    member_count INT          NOT NULL DEFAULT 1 CHECK (member_count >= 0),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE community_members (
    community_id UUID        NOT NULL REFERENCES communities(community_id) ON DELETE CASCADE,
    user_id      UUID        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    member_role  VARCHAR(20) NOT NULL DEFAULT 'member'
                 CHECK (member_role IN ('member', 'community_mod', 'admin')),
    status       VARCHAR(16) NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active', 'pending', 'banned')),
    joined_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (community_id, user_id)
);

-- 5. DOMAIN 4: UNIFIED CONTENT & MEDIA LIFECYCLE ENGINE (4 Tables)
CREATE TABLE contents (
    content_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    author_id           UUID                     NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    community_id        UUID                     REFERENCES communities(community_id) ON DELETE SET NULL,
    content_type        content_type_enum        NOT NULL,
    title               VARCHAR(240),
    caption_or_summary  TEXT,
    language_code       VARCHAR(10)              NOT NULL DEFAULT 'en',
    region_id           INT                      REFERENCES regions(region_id) ON DELETE SET NULL,
    category_id         INT                      REFERENCES heritage_categories(category_id) ON DELETE SET NULL,
    historical_era      VARCHAR(100),
    tags                TEXT[]                   NOT NULL DEFAULT '{}',
    visibility          visibility_enum          NOT NULL DEFAULT 'public',
    moderation_status   moderation_status_enum   NOT NULL DEFAULT 'published',
    verification_status verification_status_enum NOT NULL DEFAULT 'unverified',
    like_count          INT                      NOT NULL DEFAULT 0 CHECK (like_count >= 0),
    comment_count       INT                      NOT NULL DEFAULT 0 CHECK (comment_count >= 0),
    save_count          INT                      NOT NULL DEFAULT 0 CHECK (save_count >= 0),
    view_count          BIGINT                   NOT NULL DEFAULT 0 CHECK (view_count >= 0),
    search_vector       TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('english', COALESCE(title, '')), 'A') ||
        setweight(to_tsvector('english', COALESCE(caption_or_summary, '')), 'B') ||
        setweight(to_tsvector('english', COALESCE(historical_era, '')), 'C')
    ) STORED,
    published_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ              NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ              NOT NULL DEFAULT NOW(),
    CHECK (content_type <> 'article' OR (title IS NOT NULL AND char_length(trim(title)) > 0))
);

CREATE TABLE media_assets (
    media_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    uploader_id      UUID            NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    content_id       UUID            REFERENCES contents(content_id) ON DELETE SET NULL,
    storage_key      TEXT            NOT NULL UNIQUE,
    media_kind       media_kind_enum NOT NULL,
    mime_type        VARCHAR(100)    NOT NULL,
    file_size_bytes  BIGINT          NOT NULL CHECK (file_size_bytes > 0 AND file_size_bytes <= 104857600),
    width_px         INT             CHECK (width_px IS NULL OR width_px > 0),
    height_px        INT             CHECK (height_px IS NULL OR height_px > 0),
    duration_seconds SMALLINT        CHECK (duration_seconds IS NULL OR (duration_seconds > 0 AND duration_seconds <= 180)),
    display_order    SMALLINT        NOT NULL DEFAULT 0 CHECK (display_order >= 0),
    alt_text         VARCHAR(300),
    access_level     VARCHAR(16)     NOT NULL DEFAULT 'public'
                     CHECK (access_level IN ('public', 'private_signed')),
    upload_status    VARCHAR(20)     NOT NULL DEFAULT 'pending_upload'
                     CHECK (upload_status IN ('pending_upload', 'active', 'orphaned', 'deleted')),
    expires_at       TIMESTAMPTZ,
    created_at       TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CHECK (
        (media_kind = 'image'    AND file_size_bytes <= 10485760) OR
        (media_kind = 'audio'    AND file_size_bytes <= 20971520) OR
        (media_kind = 'document' AND file_size_bytes <= 20971520) OR
        (media_kind = 'video'    AND file_size_bytes <= 104857600)
    )
);

CREATE TABLE reel_details (
    content_id         UUID PRIMARY KEY REFERENCES contents(content_id) ON DELETE CASCADE,
    video_media_id     UUID        NOT NULL UNIQUE REFERENCES media_assets(media_id) ON DELETE RESTRICT,
    thumbnail_media_id UUID        NOT NULL REFERENCES media_assets(media_id) ON DELETE RESTRICT,
    duration_seconds   SMALLINT    NOT NULL CHECK (duration_seconds > 0 AND duration_seconds <= 180),
    aspect_ratio       VARCHAR(12) NOT NULL DEFAULT '9:16',
    audio_track_name   VARCHAR(160),
    transcript_text    TEXT
);

CREATE TABLE article_details (
    content_id           UUID PRIMARY KEY REFERENCES contents(content_id) ON DELETE CASCADE,
    body_markdown        TEXT     NOT NULL CHECK (char_length(trim(body_markdown)) > 0),
    cover_media_id       UUID     REFERENCES media_assets(media_id) ON DELETE SET NULL,
    reading_time_mins    SMALLINT NOT NULL DEFAULT 1 CHECK (reading_time_mins > 0),
    references_json      JSONB    NOT NULL DEFAULT '[]'::JSONB,
    primary_source_notes TEXT
);

-- 6. DOMAIN 5: SOCIAL GRAPH & ENGAGEMENT (4 Tables)
CREATE TABLE follows (
    follower_id  UUID        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    following_id UUID        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (follower_id, following_id),
    CHECK (follower_id <> following_id)
);

CREATE TABLE content_likes (
    user_id    UUID        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    content_id UUID        NOT NULL REFERENCES contents(content_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, content_id)
);

CREATE TABLE comments (
    comment_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content_id        UUID                   NOT NULL REFERENCES contents(content_id) ON DELETE CASCADE,
    user_id           UUID                   NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    parent_comment_id UUID                   REFERENCES comments(comment_id) ON DELETE CASCADE,
    body              TEXT                   NOT NULL CHECK (char_length(trim(body)) > 0),
    is_edited         BOOLEAN                NOT NULL DEFAULT FALSE,
    moderation_status moderation_status_enum NOT NULL DEFAULT 'published',
    created_at        TIMESTAMPTZ            NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ            NOT NULL DEFAULT NOW()
);

CREATE TABLE saved_items (
    user_id         UUID        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    content_id      UUID        NOT NULL REFERENCES contents(content_id) ON DELETE CASCADE,
    collection_name VARCHAR(80) NOT NULL DEFAULT 'Saved',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, content_id)
);

-- 7. DOMAIN 6: HERITAGE VERIFICATION & MODERATION (2 Tables)
CREATE TABLE heritage_reviews (
    review_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content_id   UUID        NOT NULL REFERENCES contents(content_id) ON DELETE CASCADE,
    reviewer_id  UUID        NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    verdict      VARCHAR(20) NOT NULL CHECK (verdict IN ('verified', 'needs_citation', 'disputed')),
    review_notes TEXT        NOT NULL CHECK (char_length(trim(review_notes)) > 0),
    sources_json JSONB       NOT NULL DEFAULT '[]'::JSONB,
    reviewed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (content_id, reviewer_id)
);

CREATE TABLE moderation_reports (
    report_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reporter_id           UUID               NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    target_type           VARCHAR(20)        NOT NULL CHECK (target_type IN ('content', 'comment', 'user', 'community')),
    target_id             UUID               NOT NULL,
    reason_category       VARCHAR(30)        NOT NULL CHECK (reason_category IN ('misinformation', 'hate_speech', 'off_topic', 'copyright', 'spam', 'other')),
    details               TEXT,
    status                report_status_enum NOT NULL DEFAULT 'open',
    assigned_moderator_id UUID               REFERENCES users(user_id) ON DELETE SET NULL,
    resolution_notes      TEXT,
    created_at            TIMESTAMPTZ        NOT NULL DEFAULT NOW(),
    resolved_at           TIMESTAMPTZ
);

-- 8. TARGETED MVP INDEXES (10 Total, including idx_researcher_one_pending above)
CREATE INDEX idx_contents_public_feed
    ON contents (content_type, published_at DESC)
    WHERE visibility = 'public' AND moderation_status = 'published';

CREATE INDEX idx_contents_region_category
    ON contents (region_id, category_id, published_at DESC)
    WHERE visibility = 'public' AND moderation_status = 'published';

CREATE INDEX idx_contents_author_community
    ON contents (author_id, community_id, created_at DESC);

CREATE INDEX idx_contents_search_fts
    ON contents USING GIN (search_vector);

CREATE INDEX idx_contents_tags_gin
    ON contents USING GIN (tags);

CREATE INDEX idx_media_content_order
    ON media_assets (content_id, display_order)
    WHERE content_id IS NOT NULL;

CREATE INDEX idx_media_cleanup_queue
    ON media_assets (upload_status, expires_at)
    WHERE upload_status IN ('pending_upload', 'orphaned');

CREATE INDEX idx_comments_content_time
    ON comments (content_id, created_at DESC);

CREATE INDEX idx_reports_open_queue
    ON moderation_reports (status, created_at ASC)
    WHERE status IN ('open', 'in_review');

-- 9. INTEGRITY & AUTOMATION TRIGGERS
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_communities_updated_at
    BEFORE UPDATE ON communities
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_contents_updated_at
    BEFORE UPDATE ON contents
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_comments_updated_at
    BEFORE UPDATE ON comments
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE OR REPLACE FUNCTION enforce_reel_consistency()
RETURNS TRIGGER AS $$
DECLARE
    v_content_type content_type_enum;
    v_video_kind   media_kind_enum;
    v_thumb_kind   media_kind_enum;
BEGIN
    SELECT content_type INTO v_content_type FROM contents WHERE content_id = NEW.content_id;
    IF v_content_type IS DISTINCT FROM 'reel' THEN
        RAISE EXCEPTION 'reel_details.content_id (%) must reference a content row with content_type = reel', NEW.content_id;
    END IF;

    SELECT media_kind INTO v_video_kind FROM media_assets WHERE media_id = NEW.video_media_id;
    IF v_video_kind IS DISTINCT FROM 'video' THEN
        RAISE EXCEPTION 'reel_details.video_media_id (%) must reference a video asset', NEW.video_media_id;
    END IF;

    SELECT media_kind INTO v_thumb_kind FROM media_assets WHERE media_id = NEW.thumbnail_media_id;
    IF v_thumb_kind IS DISTINCT FROM 'image' THEN
        RAISE EXCEPTION 'reel_details.thumbnail_media_id (%) must reference an image asset', NEW.thumbnail_media_id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_enforce_reel_consistency
    BEFORE INSERT OR UPDATE ON reel_details
    FOR EACH ROW EXECUTE FUNCTION enforce_reel_consistency();

CREATE OR REPLACE FUNCTION enforce_article_consistency()
RETURNS TRIGGER AS $$
DECLARE
    v_content_type content_type_enum;
    v_cover_kind   media_kind_enum;
BEGIN
    SELECT content_type INTO v_content_type FROM contents WHERE content_id = NEW.content_id;
    IF v_content_type IS DISTINCT FROM 'article' THEN
        RAISE EXCEPTION 'article_details.content_id (%) must reference a content row with content_type = article', NEW.content_id;
    END IF;

    IF NEW.cover_media_id IS NOT NULL THEN
        SELECT media_kind INTO v_cover_kind FROM media_assets WHERE media_id = NEW.cover_media_id;
        IF v_cover_kind IS DISTINCT FROM 'image' THEN
            RAISE EXCEPTION 'article_details.cover_media_id (%) must reference an image asset', NEW.cover_media_id;
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_enforce_article_consistency
    BEFORE INSERT OR UPDATE ON article_details
    FOR EACH ROW EXECUTE FUNCTION enforce_article_consistency();

CREATE OR REPLACE FUNCTION sync_content_verification_status()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE contents
       SET verification_status = NEW.verdict::verification_status_enum
     WHERE content_id = NEW.content_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_sync_heritage_review
    AFTER INSERT OR UPDATE ON heritage_reviews
    FOR EACH ROW EXECUTE FUNCTION sync_content_verification_status();
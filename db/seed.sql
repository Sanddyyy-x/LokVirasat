-- =============================================================================
-- LokVirasat — Repeatable Development Seed Data
-- =============================================================================

-- 1. Regions (Country -> State -> District)
INSERT INTO regions (name, region_type, parent_region_id, iso_code)
SELECT 'India', 'country', NULL, 'IN'
WHERE NOT EXISTS (
    SELECT 1 FROM regions WHERE name = 'India' AND region_type = 'country'
);

INSERT INTO regions (name, region_type, parent_region_id, iso_code)
SELECT s.name, 'state', r.region_id, s.iso
FROM (VALUES
    ('Rajasthan',   'IN-RJ'),
    ('Maharashtra', 'IN-MH'),
    ('Tamil Nadu',  'IN-TN'),
    ('West Bengal', 'IN-WB')
) AS s(name, iso)
CROSS JOIN (
    SELECT region_id FROM regions WHERE name = 'India' AND region_type = 'country' LIMIT 1
) r
WHERE NOT EXISTS (
    SELECT 1 FROM regions x
    WHERE x.name = s.name AND x.region_type = 'state' AND x.parent_region_id = r.region_id
);

INSERT INTO regions (name, region_type, parent_region_id)
SELECT 'Bikaner', 'district', r.region_id
FROM regions r
WHERE r.name = 'Rajasthan' AND r.region_type = 'state'
  AND NOT EXISTS (
      SELECT 1 FROM regions x
      WHERE x.name = 'Bikaner' AND x.parent_region_id = r.region_id
  );

-- 2. Heritage Categories
INSERT INTO heritage_categories (slug, name, description) VALUES
    ('folk-music',      'Folk Music',      'Regional oral musical traditions and instruments'),
    ('classical-dance', 'Classical Dance', 'Codified Indian classical dance traditions'),
    ('architecture',    'Architecture',    'Forts, stepwells, havelis, and temple heritage'),
    ('textiles',        'Textiles',        'Handloom weaves, block prints, and embroidery')
ON CONFLICT (slug) DO NOTHING;

-- 3. Test Users (placeholder hashes; Milestone 2 will replace with Argon2id via /auth/register)
INSERT INTO users (username, email, password_hash, full_name, home_state, preferred_language, is_verified_badge) VALUES
    ('demo_creator',   'creator@lokvirasat.dev',   'PLACEHOLDER_HASH_REPLACE_IN_M2', 'Aarav Rathore',    'Rajasthan',   'hi', FALSE),
    ('demo_historian', 'historian@lokvirasat.dev', 'PLACEHOLDER_HASH_REPLACE_IN_M2', 'Dr. Meera Iyer',   'Tamil Nadu',  'en', TRUE),
    ('demo_admin',     'admin@lokvirasat.dev',     'PLACEHOLDER_HASH_REPLACE_IN_M2', 'LokVirasat Admin', 'Maharashtra', 'en', TRUE)
ON CONFLICT (username) DO NOTHING;

-- Assign stackable roles
INSERT INTO user_roles (user_id, role_id)
SELECT u.user_id, r.role_id
FROM users u
JOIN roles r ON (
    (u.username = 'demo_creator'   AND r.role_key IN ('registered_user', 'cultural_creator')) OR
    (u.username = 'demo_historian' AND r.role_key IN ('registered_user', 'cultural_creator', 'researcher_historian')) OR
    (u.username = 'demo_admin'     AND r.role_key IN ('registered_user', 'moderator', 'administrator'))
)
ON CONFLICT DO NOTHING;

-- 4. Sample Cultural Article
INSERT INTO contents (
    author_id, content_type, title, caption_or_summary, language_code,
    region_id, category_id, historical_era, tags, visibility, moderation_status, published_at
)
SELECT
    (SELECT user_id FROM users WHERE username = 'demo_creator'),
    'article',
    'Rampuria Havelis of Bikaner',
    'Red Dulmera sandstone facades blending Rajput, Mughal, and Victorian motifs.',
    'en',
    (SELECT region_id FROM regions WHERE name = 'Bikaner' LIMIT 1),
    (SELECT category_id FROM heritage_categories WHERE slug = 'architecture'),
    '19th Century',
    ARRAY['bikaner', 'haveli', 'rajasthan', 'architecture'],
    'public',
    'published',
    NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM contents WHERE title = 'Rampuria Havelis of Bikaner'
);

INSERT INTO article_details (content_id, body_markdown, reading_time_mins, references_json)
SELECT
    c.content_id,
    '# Rampuria Havelis\nBuilt in Dulmera red sandstone, the Rampuria Havelis showcase intricate jharokhas and merchant-era craftsmanship in Bikaner.',
    3,
    '[{"title": "Rajasthan Heritage Survey", "year": 2021}]'::JSONB
FROM contents c
WHERE c.title = 'Rampuria Havelis of Bikaner'
ON CONFLICT (content_id) DO NOTHING;
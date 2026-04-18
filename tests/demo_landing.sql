-- =====================================================================
-- payp — Landing page demo database ("Aurora Analytics")
--
-- A fictional B2B SaaS analytics company. Every table is engineered so
-- that payp's killer features look gorgeous on a screenshot:
--
--   • /plot line    → revenue_daily         (365 days hockey stick)
--   • /plot bar     → organizations         (mrr by region/industry)
--   • /plot heatmap → events                (signups by hour × weekday)
--   • /plot boxplot → subscriptions         (mrr distribution by plan)
--   • /plot pie     → organizations.plan    (plan mix)
--   • security check → users, api_keys      (plaintext secrets, PII)
--   • migration      → events               (unpartitioned, no index)
--   • schema explore → rich COMMENTs, FKs
-- =====================================================================

-- ---------- Organizations -------------------------------------------
CREATE TABLE organizations (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(100) NOT NULL,
    slug            VARCHAR(50) UNIQUE NOT NULL,
    industry        VARCHAR(40) NOT NULL,
    region          VARCHAR(20) NOT NULL,
    country         CHAR(2)     NOT NULL,
    plan            VARCHAR(20) NOT NULL,
    seats           INT         NOT NULL DEFAULT 1,
    mrr_usd         NUMERIC(10,2) NOT NULL DEFAULT 0,
    health_score    INT         NOT NULL DEFAULT 50,
    created_at      TIMESTAMPTZ NOT NULL,
    churned_at      TIMESTAMPTZ
);
COMMENT ON TABLE  organizations IS 'Aurora customer accounts (workspaces)';
COMMENT ON COLUMN organizations.plan         IS 'free | starter | growth | enterprise';
COMMENT ON COLUMN organizations.region       IS 'NA | EU | APAC | LATAM';
COMMENT ON COLUMN organizations.health_score IS '0-100, computed weekly from engagement signals';

CREATE INDEX idx_orgs_plan   ON organizations(plan);
CREATE INDEX idx_orgs_region ON organizations(region);

-- ---------- Users ---------------------------------------------------
CREATE TABLE users (
    id              SERIAL PRIMARY KEY,
    org_id          INT         NOT NULL REFERENCES organizations(id),
    email           VARCHAR(255) UNIQUE NOT NULL,
    full_name       VARCHAR(100) NOT NULL,
    role            VARCHAR(20)  NOT NULL,
    -- ⚠ intentionally insecure: SHA-1 hex, no salt — security check bait
    password_hash   VARCHAR(40)  NOT NULL,
    mfa_enabled     BOOLEAN      NOT NULL DEFAULT false,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL
);
COMMENT ON TABLE  users IS 'Aurora end users. PII — treat email + name as sensitive.';
COMMENT ON COLUMN users.role          IS 'owner | admin | member | viewer';
COMMENT ON COLUMN users.password_hash IS 'DEPRECATED: legacy SHA-1. Migrate to argon2id.';

-- ---------- Subscriptions -------------------------------------------
CREATE TABLE subscriptions (
    id              SERIAL PRIMARY KEY,
    org_id          INT          NOT NULL REFERENCES organizations(id),
    plan            VARCHAR(20)  NOT NULL,
    status          VARCHAR(20)  NOT NULL,
    mrr_usd         NUMERIC(10,2) NOT NULL,
    seats           INT          NOT NULL,
    started_on      DATE         NOT NULL,
    renews_on       DATE,
    canceled_on     DATE
);
COMMENT ON COLUMN subscriptions.status IS 'trialing | active | past_due | canceled';

-- ---------- Invoices ------------------------------------------------
CREATE TABLE invoices (
    id              SERIAL PRIMARY KEY,
    org_id          INT          NOT NULL REFERENCES organizations(id),
    number          VARCHAR(20)  UNIQUE NOT NULL,
    amount_usd      NUMERIC(10,2) NOT NULL,
    status          VARCHAR(20)  NOT NULL,
    issued_on       DATE         NOT NULL,
    due_on          DATE         NOT NULL,
    paid_on         DATE
);
COMMENT ON COLUMN invoices.status IS 'draft | open | paid | overdue | void';

-- ---------- Revenue (pre-aggregated, for line chart) -----------------
CREATE TABLE revenue_daily (
    day             DATE PRIMARY KEY,
    mrr_usd         NUMERIC(12,2) NOT NULL,
    new_mrr         NUMERIC(10,2) NOT NULL,
    expansion_mrr   NUMERIC(10,2) NOT NULL,
    churn_mrr       NUMERIC(10,2) NOT NULL,
    active_orgs     INT           NOT NULL,
    signups         INT           NOT NULL
);
COMMENT ON TABLE revenue_daily IS 'Daily revenue rollup — primary chart surface';

-- ---------- Events (heatmap source) ---------------------------------
-- NOTE: intentionally no FK, no index on occurred_at — migration bait
CREATE TABLE events (
    id              BIGSERIAL PRIMARY KEY,
    org_id          INT          NOT NULL,
    user_id         INT,
    event_name      VARCHAR(40)  NOT NULL,
    occurred_at     TIMESTAMPTZ  NOT NULL,
    properties      JSONB
);
COMMENT ON TABLE events IS 'Raw product telemetry. TODO: partition by month, index occurred_at';

-- ---------- API keys (security check bait) --------------------------
CREATE TABLE api_keys (
    id              SERIAL PRIMARY KEY,
    org_id          INT          NOT NULL REFERENCES organizations(id),
    name            VARCHAR(80)  NOT NULL,
    -- ⚠ intentionally insecure: stored in plaintext
    key_plaintext   VARCHAR(64)  NOT NULL,
    scopes          TEXT[]       NOT NULL,
    last_used_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL
);
COMMENT ON COLUMN api_keys.key_plaintext IS 'FIXME: hash with sha256 + prefix, never store plaintext';

-- ---------- Feature flags -------------------------------------------
CREATE TABLE feature_flags (
    key             VARCHAR(50) PRIMARY KEY,
    enabled         BOOLEAN     NOT NULL,
    rollout_pct     INT         NOT NULL DEFAULT 0,
    description     TEXT
);

-- =====================================================================
--                          SEED DATA
-- =====================================================================

-- 60 organizations across 4 regions, 4 plans, 8 industries
INSERT INTO organizations (name, slug, industry, region, country, plan, seats, mrr_usd, health_score, created_at, churned_at) VALUES
('Lumen Robotics',      'lumen-robotics',      'Robotics',     'NA',   'US', 'enterprise', 240, 9800.00,  92, '2025-04-11 09:12:00+00', NULL),
('Northwind Freight',   'northwind-freight',   'Logistics',    'EU',   'DE', 'enterprise', 180, 7400.00,  88, '2025-04-18 10:30:00+00', NULL),
('Kaizen Foods',        'kaizen-foods',        'Retail',       'APAC', 'JP', 'growth',      64, 2990.00,  81, '2025-05-02 02:45:00+00', NULL),
('Brightpath Health',   'brightpath-health',   'Healthcare',   'NA',   'US', 'enterprise', 310, 12400.00, 95, '2025-05-09 14:00:00+00', NULL),
('Mistral Bank',        'mistral-bank',        'FinTech',      'EU',   'FR', 'enterprise', 420, 14800.00, 96, '2025-05-15 08:20:00+00', NULL),
('Halcyon AI',          'halcyon-ai',          'AI/ML',        'NA',   'CA', 'growth',      48, 2490.00,  84, '2025-05-22 16:10:00+00', NULL),
('Obsidian Labs',       'obsidian-labs',       'Security',     'EU',   'NL', 'growth',      36, 1990.00,  79, '2025-06-01 09:00:00+00', NULL),
('Polaris Media',       'polaris-media',       'Media',        'NA',   'US', 'growth',      52, 2790.00,  76, '2025-06-08 11:30:00+00', NULL),
('Savanna Mobility',    'savanna-mobility',    'Transport',    'APAC', 'SG', 'growth',      42, 2290.00,  82, '2025-06-15 03:15:00+00', NULL),
('Tundra Energy',       'tundra-energy',       'Energy',       'EU',   'NO', 'enterprise', 150, 6800.00,  89, '2025-06-22 07:45:00+00', NULL),
('Azure Peaks Travel',  'azure-peaks-travel',  'Travel',       'LATAM','BR', 'starter',     12, 490.00,   68, '2025-07-03 13:00:00+00', NULL),
('Meridian Legal',      'meridian-legal',      'Legal',        'NA',   'US', 'starter',     18, 690.00,   71, '2025-07-10 10:20:00+00', NULL),
('Verdant Farms',       'verdant-farms',       'Agriculture',  'EU',   'ES', 'starter',      8, 390.00,   64, '2025-07-17 08:30:00+00', NULL),
('Cobalt Manufacturing','cobalt-manufacturing','Manufacturing','NA',   'US', 'growth',      78, 3490.00,  86, '2025-07-24 15:00:00+00', NULL),
('Helix Biotech',       'helix-biotech',       'Healthcare',   'EU',   'CH', 'enterprise', 200, 8900.00,  93, '2025-07-31 09:45:00+00', NULL),
('Orion Gaming',        'orion-gaming',        'Gaming',       'APAC', 'KR', 'growth',      56, 2890.00,  77, '2025-08-07 12:00:00+00', NULL),
('Zenith Capital',      'zenith-capital',      'FinTech',      'NA',   'US', 'enterprise', 290, 11200.00, 94, '2025-08-14 10:15:00+00', NULL),
('Solstice Fashion',    'solstice-fashion',    'Retail',       'EU',   'IT', 'growth',      34, 1790.00,  72, '2025-08-21 14:30:00+00', NULL),
('Arcadia Studios',     'arcadia-studios',     'Media',        'NA',   'US', 'growth',      44, 2190.00,  75, '2025-08-28 11:00:00+00', NULL),
('Beluga Shipping',     'beluga-shipping',     'Logistics',    'EU',   'DK', 'growth',      60, 2690.00,  83, '2025-09-04 08:45:00+00', NULL),
('Coral Dynamics',      'coral-dynamics',      'Robotics',     'APAC', 'AU', 'growth',      40, 2090.00,  80, '2025-09-11 05:20:00+00', NULL),
('Driftwood Hotels',    'driftwood-hotels',    'Travel',       'NA',   'MX', 'starter',     22, 890.00,   69, '2025-09-18 16:00:00+00', NULL),
('Ember Grid',          'ember-grid',          'Energy',       'NA',   'CA', 'growth',      50, 2490.00,  85, '2025-09-25 09:30:00+00', NULL),
('Fjord Software',      'fjord-software',      'SaaS',         'EU',   'SE', 'enterprise', 170, 7200.00,  91, '2025-10-02 10:00:00+00', NULL),
('Glacier Logistics',   'glacier-logistics',   'Logistics',    'NA',   'US', 'growth',      66, 3090.00,  78, '2025-10-09 13:30:00+00', NULL),
('Harbor Point Retail', 'harbor-point-retail', 'Retail',       'EU',   'UK', 'growth',      38, 1890.00,  74, '2025-10-16 09:00:00+00', NULL),
('Ionic Chemistry',     'ionic-chemistry',     'Manufacturing','EU',   'BE', 'growth',      45, 2190.00,  82, '2025-10-23 14:15:00+00', NULL),
('Juno Aerospace',      'juno-aerospace',      'Aerospace',    'NA',   'US', 'enterprise', 260, 10400.00, 90, '2025-10-30 08:00:00+00', NULL),
('Kestrel Insurance',   'kestrel-insurance',   'FinTech',      'EU',   'IE', 'enterprise', 140, 5800.00,  87, '2025-11-06 11:00:00+00', NULL),
('Lotus Wellness',      'lotus-wellness',      'Healthcare',   'APAC', 'IN', 'starter',     16, 590.00,   66, '2025-11-13 04:30:00+00', NULL),
('Monsoon Learning',    'monsoon-learning',    'EdTech',       'APAC', 'IN', 'growth',      52, 2490.00,  79, '2025-11-20 05:00:00+00', NULL),
('Nimbus Data',         'nimbus-data',         'SaaS',         'NA',   'US', 'enterprise', 220, 9100.00,  93, '2025-11-27 10:45:00+00', NULL),
('Onyx Security',       'onyx-security',       'Security',     'EU',   'DE', 'growth',      48, 2290.00,  81, '2025-12-04 09:15:00+00', NULL),
('Prairie Labs',        'prairie-labs',        'Biotech',      'NA',   'CA', 'growth',      35, 1690.00,  73, '2025-12-11 13:00:00+00', NULL),
('Quanta Photonics',    'quanta-photonics',    'Hardware',     'APAC', 'TW', 'growth',      42, 2090.00,  77, '2025-12-18 06:30:00+00', NULL),
('Ravenwood Press',     'ravenwood-press',     'Media',        'EU',   'UK', 'starter',     14, 490.00,   65, '2025-12-25 12:00:00+00', NULL),
('Sable Holdings',      'sable-holdings',      'FinTech',      'NA',   'US', 'enterprise', 310, 12800.00, 96, '2026-01-08 09:30:00+00', NULL),
('Terracotta Ceramics', 'terracotta-ceramics', 'Manufacturing','EU',   'PT', 'starter',     10, 390.00,   62, '2026-01-15 10:00:00+00', NULL),
('Ultraviolet Optics',  'ultraviolet-optics',  'Hardware',     'APAC', 'JP', 'growth',      38, 1890.00,  78, '2026-01-22 03:20:00+00', NULL),
('Vanguard Motors',     'vanguard-motors',     'Automotive',   'EU',   'DE', 'enterprise', 280, 10900.00, 92, '2026-01-29 08:45:00+00', NULL),
('Willow Fintech',      'willow-fintech',      'FinTech',      'LATAM','AR', 'growth',      44, 2190.00,  80, '2026-02-05 14:00:00+00', NULL),
('Xanadu Resorts',      'xanadu-resorts',      'Travel',       'APAC', 'TH', 'growth',      32, 1590.00,  70, '2026-02-12 11:30:00+00', NULL),
('Yellowstone Tools',   'yellowstone-tools',   'Manufacturing','NA',   'US', 'growth',      56, 2690.00,  84, '2026-02-19 09:00:00+00', NULL),
('Zephyr Networks',     'zephyr-networks',     'Telecom',      'EU',   'FR', 'enterprise', 190, 8200.00,  88, '2026-02-26 10:15:00+00', NULL),
('Amber Genomics',      'amber-genomics',      'Biotech',      'NA',   'US', 'growth',      40, 2090.00,  82, '2026-03-05 13:45:00+00', NULL),
('Basalt Mining',       'basalt-mining',       'Energy',       'LATAM','CL', 'growth',      46, 2290.00,  75, '2026-03-12 08:30:00+00', NULL),
('Cinder Robotics',     'cinder-robotics',     'Robotics',     'EU',   'CH', 'growth',      38, 1890.00,  79, '2026-03-19 11:00:00+00', NULL),
('Driftglass Tours',    'driftglass-tours',    'Travel',       'NA',   'US', 'starter',      6, 290.00,   60, '2026-03-26 15:30:00+00', '2026-04-05 10:00:00+00'),
('Echo Valley Wines',   'echo-valley-wines',   'Agriculture',  'EU',   'FR', 'starter',     12, 490.00,   63, '2026-03-28 09:00:00+00', NULL),
('Ferrous Steel',       'ferrous-steel',       'Manufacturing','APAC', 'CN', 'growth',      52, 2490.00,  76, '2026-03-30 04:15:00+00', NULL),
('Granite Consulting',  'granite-consulting',  'Consulting',   'NA',   'US', 'starter',     20, 790.00,   72, '2026-04-01 10:00:00+00', NULL),
('Horizon Broadcast',   'horizon-broadcast',   'Media',        'EU',   'UK', 'growth',      34, 1690.00,  74, '2026-04-02 11:30:00+00', NULL),
('Indigo Apparel',      'indigo-apparel',      'Retail',       'APAC', 'VN', 'starter',     14, 590.00,   67, '2026-04-03 06:00:00+00', NULL),
('Jasper Games',        'jasper-games',        'Gaming',       'NA',   'US', 'free',         3, 0.00,     55, '2026-04-04 14:20:00+00', NULL),
('Klondike Crypto',     'klondike-crypto',     'FinTech',      'NA',   'CA', 'growth',      28, 1390.00,  70, '2026-04-05 09:45:00+00', NULL),
('Larkspur Florists',   'larkspur-florists',   'Retail',       'EU',   'NL', 'free',         2, 0.00,     52, '2026-04-06 13:00:00+00', NULL),
('Magnolia Realty',     'magnolia-realty',     'Real Estate',  'NA',   'US', 'starter',     18, 690.00,   68, '2026-04-07 10:30:00+00', NULL),
('Nightshade Audio',    'nightshade-audio',    'Hardware',     'EU',   'DE', 'free',         4, 0.00,     58, '2026-04-08 11:00:00+00', NULL),
('Opal Diagnostics',    'opal-diagnostics',    'Healthcare',   'APAC', 'AU', 'growth',      36, 1790.00,  77, '2026-04-09 05:30:00+00', NULL),
('Paper Kite Design',   'paper-kite-design',   'Design',       'EU',   'UK', 'free',         2, 0.00,     50, '2026-04-10 12:15:00+00', NULL);

-- ---------- Users: 3-5 per org, deterministic -----------------------
INSERT INTO users (org_id, email, full_name, role, password_hash, mfa_enabled, last_login_at, created_at)
SELECT
    o.id,
    lower(split_part(o.slug,'-',1)) || '.' || n.role_name || '@' || o.slug || '.io',
    initcap(n.first_name) || ' ' || initcap(n.last_name),
    n.role_name,
    -- SHA-1 of 'password' + id  →  insecure on purpose
    encode(sha1((o.id::text || n.role_name)::bytea), 'hex'),
    (o.plan IN ('enterprise','growth') AND n.role_name IN ('owner','admin')),
    o.created_at + (random() * interval '30 days'),
    o.created_at + (random() * interval '2 days')
FROM organizations o
CROSS JOIN (VALUES
    ('owner',  'alex',    'ng'),
    ('admin',  'priya',   'shah'),
    ('member', 'liam',    'okafor'),
    ('member', 'yuki',    'tanaka'),
    ('viewer', 'sofia',   'rossi')
) AS n(role_name, first_name, last_name);

-- ---------- Subscriptions (1 per org) -------------------------------
INSERT INTO subscriptions (org_id, plan, status, mrr_usd, seats, started_on, renews_on, canceled_on)
SELECT
    id,
    plan,
    CASE
        WHEN churned_at IS NOT NULL THEN 'canceled'
        WHEN plan = 'free' THEN 'trialing'
        WHEN id % 13 = 0 THEN 'past_due'
        ELSE 'active'
    END,
    mrr_usd,
    seats,
    created_at::date,
    (created_at + interval '1 year')::date,
    churned_at::date
FROM organizations;

-- ---------- Invoices: last 6 months per paying org ------------------
INSERT INTO invoices (org_id, number, amount_usd, status, issued_on, due_on, paid_on)
SELECT
    o.id,
    'INV-' || lpad(o.id::text,4,'0') || '-' || to_char(m, 'YYYYMM'),
    o.mrr_usd,
    CASE
        WHEN m >= date_trunc('month', DATE '2026-04-01') THEN 'open'
        WHEN o.id % 17 = 0 AND m = date_trunc('month', DATE '2026-03-01') THEN 'overdue'
        ELSE 'paid'
    END,
    m::date,
    (m + interval '14 days')::date,
    CASE
        WHEN m >= date_trunc('month', DATE '2026-04-01') THEN NULL
        WHEN o.id % 17 = 0 AND m = date_trunc('month', DATE '2026-03-01') THEN NULL
        ELSE (m + interval '6 days')::date
    END
FROM organizations o
CROSS JOIN generate_series(
    GREATEST(date_trunc('month', o.created_at), DATE '2025-11-01'),
    DATE '2026-04-01',
    interval '1 month'
) m
WHERE o.plan <> 'free' AND (o.churned_at IS NULL OR m::date < o.churned_at::date);

-- ---------- Revenue daily: 365 days, hockey-stick curve -------------
INSERT INTO revenue_daily (day, mrr_usd, new_mrr, expansion_mrr, churn_mrr, active_orgs, signups)
SELECT
    d::date,
    ROUND((
        18000                                            -- base
        + POWER(day_idx, 1.55) * 6.2                     -- hockey stick growth
        + SIN(day_idx / 9.0) * 1800                      -- weekly wobble
        + (random() * 1400 - 700)                        -- noise
    )::numeric, 2) AS mrr_usd,
    ROUND((650 + POWER(day_idx, 1.15) * 0.45 + random()*260)::numeric, 2) AS new_mrr,
    ROUND((120 + POWER(day_idx, 1.05) * 0.18 + random()*90)::numeric, 2) AS expansion_mrr,
    ROUND((180 + random()*220 + (CASE WHEN day_idx % 28 = 0 THEN 600 ELSE 0 END))::numeric, 2) AS churn_mrr,
    (8 + (day_idx * 0.17)::int + (random()*3)::int) AS active_orgs,
    (4 + (day_idx * 0.09)::int + (random()*4)::int
       + CASE WHEN extract(dow FROM d) IN (0,6) THEN -2 ELSE 0 END) AS signups
FROM (
    SELECT d, row_number() OVER (ORDER BY d) - 1 AS day_idx
    FROM generate_series(DATE '2025-04-12', DATE '2026-04-11', interval '1 day') d
) s;

-- ---------- Events: ~6k rows, strong hour×weekday pattern -----------
-- Business hours heavy (Mon-Fri 9-18 UTC), sparse nights/weekends.
INSERT INTO events (org_id, user_id, event_name, occurred_at, properties)
SELECT
    1 + (random() * 59)::int,
    1 + (random() * 299)::int,
    (ARRAY['login','dashboard_view','query_run','chart_export','share_link',
           'alert_triggered','api_call','invite_sent'])[1 + (random()*7)::int],
    ts,
    jsonb_build_object('source', (ARRAY['web','api','mobile'])[1+(random()*2)::int])
FROM (
    SELECT
        (DATE '2026-03-15' + (floor(random()*28) || ' days')::interval)
        + make_interval(
            hours => CASE
                WHEN random() < 0.78 THEN 9 + (random()*9)::int   -- 9-17 bias
                ELSE (random()*23)::int
            END,
            mins  => (random()*59)::int
        ) AS ts,
        gs
    FROM generate_series(1, 6000) gs
) s
WHERE extract(dow FROM ts) NOT IN (0,6) OR random() < 0.18;

-- ---------- API keys (security check bait) --------------------------
INSERT INTO api_keys (org_id, name, key_plaintext, scopes, last_used_at, created_at) VALUES
(1,  'Production backend', 'FAKE_STRIPE_KEY_EXAMPLE_9f3c2a8b_NOT_REAL', ARRAY['read','write','admin'],   '2026-04-11 08:30:00+00', '2025-04-12 10:00:00+00'),
(1,  'CI pipeline',        'FAKE_STRIPE_KEY_EXAMPLE_7e1a9b2c_NOT_REAL', ARRAY['read','write'],           '2026-04-10 22:15:00+00', '2025-06-01 09:00:00+00'),
(4,  'Data warehouse ETL', 'FAKE_STRIPE_KEY_EXAMPLE_2c8e4a1b_NOT_REAL', ARRAY['read'],                   '2026-04-11 02:00:00+00', '2025-05-20 14:00:00+00'),
(5,  'Grafana',            'FAKE_STRIPE_KEY_EXAMPLE_4a7b1c8e_NOT_REAL', ARRAY['read'],                   '2026-04-11 09:00:00+00', '2025-05-30 11:30:00+00'),
(5,  'Legacy ingest',      'FAKE_STRIPE_KEY_EXAMPLE_1a2b3c4d_NOT_REAL', ARRAY['read','write','admin'],   '2025-11-03 14:00:00+00', '2025-05-20 10:00:00+00'),
(17, 'Looker',             'FAKE_STRIPE_KEY_EXAMPLE_8b4e1c7a_NOT_REAL', ARRAY['read'],                   '2026-04-11 07:45:00+00', '2025-08-20 10:00:00+00'),
(28, 'Internal dashboard', 'FAKE_STRIPE_KEY_EXAMPLE_3f7a2e8c_NOT_REAL', ARRAY['read','write'],           '2026-04-10 18:20:00+00', '2025-11-01 12:00:00+00'),
(32, 'Billing exporter',   'FAKE_STRIPE_KEY_EXAMPLE_6b9c3f1e_NOT_REAL', ARRAY['read','admin'],           '2026-04-11 06:00:00+00', '2025-12-04 09:30:00+00'),
(37, 'Mobile app',         'FAKE_STRIPE_KEY_EXAMPLE_5e8a2b9c_NOT_REAL', ARRAY['read'],                   '2026-04-11 10:10:00+00', '2026-01-10 11:00:00+00'),
(40, 'Customer portal',    'FAKE_STRIPE_KEY_EXAMPLE_9d1e4a8b_NOT_REAL', ARRAY['read','write'],           '2026-04-10 23:30:00+00', '2026-02-01 08:45:00+00');

-- ---------- Feature flags -------------------------------------------
INSERT INTO feature_flags (key, enabled, rollout_pct, description) VALUES
('ai_insights',           true,  100, 'Natural-language query over dashboards'),
('partitioned_events',    false,   0, 'Roll out monthly partitioning for events table'),
('argon2_passwords',      false,  10, 'Rehash legacy SHA-1 passwords on next login'),
('scim_provisioning',     true,   45, 'Enterprise SCIM user sync'),
('chart_export_pdf',      true,  100, 'Export any chart as PDF'),
('realtime_alerts',       true,   80, 'WebSocket-based alert delivery'),
('dark_mode',             true,  100, 'Dark UI theme'),
('mrr_forecasting',       false,  25, 'Beta: 90-day MRR forecasting model'),
('api_key_hashing',       false,   0, 'Store api keys as sha256 hashes only');

-- =====================================================================
-- VIEWS — make common queries trivial so payp responses stay clean
-- =====================================================================

CREATE VIEW v_mrr_by_region AS
SELECT region, SUM(mrr_usd) AS mrr_usd, COUNT(*) AS orgs
FROM organizations WHERE churned_at IS NULL GROUP BY region ORDER BY mrr_usd DESC;

CREATE VIEW v_mrr_by_industry AS
SELECT industry, SUM(mrr_usd) AS mrr_usd, COUNT(*) AS orgs
FROM organizations WHERE churned_at IS NULL GROUP BY industry ORDER BY mrr_usd DESC;

CREATE VIEW v_plan_mix AS
SELECT plan, COUNT(*) AS orgs, SUM(mrr_usd) AS mrr_usd
FROM organizations WHERE churned_at IS NULL GROUP BY plan ORDER BY mrr_usd DESC;

CREATE VIEW v_top_customers AS
SELECT id, name, industry, region, plan, mrr_usd, health_score
FROM organizations WHERE churned_at IS NULL ORDER BY mrr_usd DESC LIMIT 10;

ANALYZE;

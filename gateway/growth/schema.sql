CREATE TABLE IF NOT EXISTS organizations (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY,
  organization_id UUID NOT NULL REFERENCES organizations(id),
  email TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'owner',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (organization_id, email)
);

CREATE TABLE IF NOT EXISTS growth_channels (
  id UUID PRIMARY KEY,
  organization_id UUID NOT NULL REFERENCES organizations(id),
  platform TEXT NOT NULL,
  name TEXT NOT NULL,
  niche TEXT NOT NULL DEFAULT '',
  language TEXT NOT NULL DEFAULT 'pt-BR',
  market TEXT NOT NULL DEFAULT '',
  settings JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS topics (
  id UUID PRIMARY KEY,
  channel_id UUID NOT NULL REFERENCES growth_channels(id),
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'discovered',
  signals JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS opportunities (
  id UUID PRIMARY KEY,
  channel_id UUID NOT NULL REFERENCES growth_channels(id),
  topic_id UUID REFERENCES topics(id),
  score NUMERIC(6,2) NOT NULL,
  score_version TEXT NOT NULL,
  dimensions JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'created',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS research (
  id UUID PRIMARY KEY,
  channel_id UUID NOT NULL REFERENCES growth_channels(id),
  opportunity_id UUID REFERENCES opportunities(id),
  version INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'draft',
  summary TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (opportunity_id, version)
);

CREATE TABLE IF NOT EXISTS sources (
  id UUID PRIMARY KEY,
  channel_id UUID NOT NULL REFERENCES growth_channels(id),
  url TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  source_type TEXT NOT NULL,
  quality TEXT NOT NULL,
  last_verified_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS claims (
  id UUID PRIMARY KEY,
  channel_id UUID NOT NULL REFERENCES growth_channels(id),
  research_id UUID NOT NULL REFERENCES research(id),
  statement TEXT NOT NULL,
  confidence NUMERIC(5,4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  status TEXT NOT NULL DEFAULT 'unverified',
  evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS content_genomes (
  id UUID PRIMARY KEY,
  channel_id UUID NOT NULL REFERENCES growth_channels(id),
  opportunity_id UUID REFERENCES opportunities(id),
  research_id UUID REFERENCES research(id),
  version INTEGER NOT NULL DEFAULT 1,
  content JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (channel_id, opportunity_id, version)
);

CREATE TABLE IF NOT EXISTS scripts (
  id UUID PRIMARY KEY,
  channel_id UUID NOT NULL REFERENCES growth_channels(id),
  genome_id UUID NOT NULL REFERENCES content_genomes(id),
  format TEXT NOT NULL CHECK (format IN ('long', 'short')),
  version INTEGER NOT NULL DEFAULT 1,
  body TEXT NOT NULL,
  structure JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scenes (
  id UUID PRIMARY KEY,
  channel_id UUID NOT NULL REFERENCES growth_channels(id),
  script_id UUID NOT NULL REFERENCES scripts(id),
  sequence INTEGER NOT NULL,
  specification JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (script_id, sequence)
);

CREATE TABLE IF NOT EXISTS assets (
  id UUID PRIMARY KEY,
  channel_id UUID NOT NULL REFERENCES growth_channels(id),
  provider TEXT NOT NULL,
  provider_asset_id TEXT NOT NULL,
  source_url TEXT NOT NULL,
  sha256 TEXT,
  perceptual_hash TEXT,
  semantic_embedding_ref TEXT,
  reuse_score NUMERIC(5,4),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (provider, provider_asset_id)
);

CREATE TABLE IF NOT EXISTS licenses (
  id UUID PRIMARY KEY,
  channel_id UUID NOT NULL REFERENCES growth_channels(id),
  asset_id UUID NOT NULL REFERENCES assets(id),
  provider TEXT NOT NULL,
  license_type TEXT NOT NULL,
  commercial_use BOOLEAN NOT NULL,
  status TEXT NOT NULL,
  evidence JSONB NOT NULL,
  verified_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS videos (
  id UUID PRIMARY KEY,
  channel_id UUID NOT NULL REFERENCES growth_channels(id),
  genome_id UUID REFERENCES content_genomes(id),
  workflow_id UUID,
  version INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL,
  manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
  object_key TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS shorts (
  id UUID PRIMARY KEY,
  channel_id UUID NOT NULL REFERENCES growth_channels(id),
  video_id UUID REFERENCES videos(id),
  genome_id UUID REFERENCES content_genomes(id),
  version INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL,
  manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
  object_key TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS thumbnails (
  id UUID PRIMARY KEY,
  channel_id UUID NOT NULL REFERENCES growth_channels(id),
  video_id UUID REFERENCES videos(id),
  version INTEGER NOT NULL DEFAULT 1,
  concept JSONB NOT NULL,
  object_key TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS experiments (
  id UUID PRIMARY KEY,
  channel_id UUID NOT NULL REFERENCES growth_channels(id),
  video_id UUID REFERENCES videos(id),
  status TEXT NOT NULL,
  variants JSONB NOT NULL,
  result JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS growth_publications (
  id UUID PRIMARY KEY,
  channel_id UUID NOT NULL REFERENCES growth_channels(id),
  video_id UUID REFERENCES videos(id),
  short_id UUID REFERENCES shorts(id),
  platform TEXT NOT NULL,
  status TEXT NOT NULL,
  external_id TEXT,
  published_at TIMESTAMPTZ,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS metrics (
  id UUID PRIMARY KEY,
  channel_id UUID NOT NULL REFERENCES growth_channels(id),
  publication_id UUID REFERENCES growth_publications(id),
  metric_scope TEXT NOT NULL CHECK (metric_scope IN ('qualified_organic', 'paid', 'unknown')),
  captured_at TIMESTAMPTZ NOT NULL,
  values JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS learnings (
  id UUID PRIMARY KEY,
  channel_id UUID NOT NULL REFERENCES growth_channels(id),
  status TEXT NOT NULL DEFAULT 'candidate',
  confidence NUMERIC(5,4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  sample_size INTEGER NOT NULL DEFAULT 0,
  rule JSONB NOT NULL,
  evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
  version INTEGER NOT NULL DEFAULT 1,
  promoted_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS costs (
  id UUID PRIMARY KEY,
  channel_id UUID NOT NULL REFERENCES growth_channels(id),
  workflow_id UUID,
  category TEXT NOT NULL,
  provider TEXT NOT NULL,
  amount NUMERIC(14,6) NOT NULL CHECK (amount >= 0),
  currency TEXT NOT NULL DEFAULT 'USD',
  usage JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS revenues (
  id UUID PRIMARY KEY,
  channel_id UUID NOT NULL REFERENCES growth_channels(id),
  source TEXT NOT NULL,
  amount NUMERIC(14,6) NOT NULL,
  currency TEXT NOT NULL DEFAULT 'USD',
  attribution JSONB NOT NULL DEFAULT '{}'::jsonb,
  occurred_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workflows (
  id UUID PRIMARY KEY,
  channel_id UUID NOT NULL REFERENCES growth_channels(id),
  temporal_workflow_id TEXT UNIQUE,
  workflow_type TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  state TEXT NOT NULL,
  current_step TEXT NOT NULL DEFAULT 'planned',
  input JSONB NOT NULL,
  output JSONB,
  error JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS domain_events (
  event_id UUID PRIMARY KEY,
  event_type TEXT NOT NULL,
  schema_version INTEGER NOT NULL,
  channel_id UUID NOT NULL REFERENCES growth_channels(id),
  workflow_id UUID NOT NULL,
  timestamp TIMESTAMPTZ NOT NULL,
  payload JSONB NOT NULL,
  correlation_id TEXT NOT NULL,
  causation_id TEXT,
  idempotency_key TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS outbox (
  id BIGSERIAL PRIMARY KEY,
  event_id UUID NOT NULL UNIQUE REFERENCES domain_events(event_id),
  topic TEXT NOT NULL,
  payload JSONB NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  published_at TIMESTAMPTZ,
  last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_workflows_channel_created ON workflows(channel_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_workflow_timestamp ON domain_events(workflow_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox(available_at) WHERE published_at IS NULL;

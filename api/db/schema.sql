-- Commonplace schema — from DATA_MODEL.md in the design handoff.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- A single journal page (or logical entry spanning multiple photographed pages)
CREATE TABLE entries (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  written_date  DATE NOT NULL,           -- date the user wrote it (may differ from captured_at)
  captured_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  status        TEXT NOT NULL DEFAULT 'processing', -- processing | ready | error
  raw_text      TEXT,                    -- current transcript (post-corrections), plain text
  transcript_json JSONB,                 -- [{text, confidence: high|med|low, alternates: [...], bbox}] per word, for the correction UI
  mood          TEXT,                    -- inferred: content | calm | anxious | tired | ... (extend as patterns emerge)
  mood_score    NUMERIC,                 -- optional continuous score if you want finer-grained mood trends than a fixed enum
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Original photographed page(s), 1:N with entries (an entry can span multiple photos)
CREATE TABLE entry_images (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  entry_id    UUID NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
  storage_key TEXT NOT NULL,             -- path in object storage
  page_order  INT NOT NULL DEFAULT 0,
  width       INT, height INT
);

-- A recurring person detected across entries
CREATE TABLE people (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name          TEXT NOT NULL UNIQUE,     -- canonical display name; resolve aliases at extraction time
  aliases       TEXT[] DEFAULT '{}',      -- e.g. nicknames the model has seen refer to this person
  first_seen    DATE,
  last_seen     DATE,
  notes         TEXT
);

-- A recurring theme/topic detected across entries
CREATE TABLE themes (
  id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name   TEXT NOT NULL UNIQUE,
  first_seen DATE,
  last_seen  DATE
);

-- Many-to-many: which entries mention which person, with the model's confidence + supporting excerpt
CREATE TABLE mentions (
  id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  entry_id  UUID NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
  person_id UUID NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  excerpt   TEXT,             -- the sentence/span that triggered the mention
  UNIQUE (entry_id, person_id)
);

-- Many-to-many: which entries touch which theme
CREATE TABLE theme_mentions (
  id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  entry_id  UUID NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
  theme_id  UUID NOT NULL REFERENCES themes(id) ON DELETE CASCADE,
  excerpt   TEXT,
  UNIQUE (entry_id, theme_id)
);

-- Every manual correction a user makes to a transcribed word — this IS your fine-tuning dataset later
CREATE TABLE corrections (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  entry_id     UUID NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
  word_index   INT NOT NULL,
  model_text   TEXT NOT NULL,     -- what the model originally transcribed
  corrected_text TEXT NOT NULL,   -- what the user changed it to
  model_confidence TEXT,          -- high | med | low at time of correction
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Precomputed/derived pattern callouts shown on the Insights screen (recompute periodically, don't derive on every page load)
CREATE TABLE insights (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  kind        TEXT NOT NULL,      -- co_occurrence | mood_correlation | streak | custom
  summary     TEXT NOT NULL,      -- the plain-language sentence shown in the UI
  data_json   JSONB,              -- supporting numbers, for potential drill-down later
  generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_entries_written_date ON entries (written_date DESC);
CREATE INDEX idx_entries_fts ON entries USING GIN (to_tsvector('english', coalesce(raw_text, '')));
CREATE INDEX idx_mentions_person ON mentions (person_id);
CREATE INDEX idx_theme_mentions_theme ON theme_mentions (theme_id);

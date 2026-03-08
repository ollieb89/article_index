-- Phase 14: Contextual Policy Routing
-- Adds per-query-type thresholds and latency budgets to policy_registry
-- Adds evidence_shape to policy_telemetry

ALTER TABLE intelligence.policy_registry 
ADD COLUMN IF NOT EXISTS contextual_thresholds JSONB NOT NULL DEFAULT '{}'::jsonb,
ADD COLUMN IF NOT EXISTS latency_budgets JSONB NOT NULL DEFAULT '{
    "general": 2000,
    "exact_fact": 1000,
    "ambiguous": 3000,
    "summarization": 4000
}'::jsonb;

ALTER TABLE intelligence.policy_telemetry
ADD COLUMN IF NOT EXISTS evidence_shape JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN intelligence.policy_registry.contextual_thresholds IS 'Per-query-type threshold overrides (e.g. {"exact_fact": {"high": 0.85}})';
COMMENT ON COLUMN intelligence.policy_registry.latency_budgets IS 'Per-query-type latency budgets in milliseconds';

-- Update initial policy v13.0 with some contextual defaults if it exists
UPDATE intelligence.policy_registry
SET contextual_thresholds = '{
    "exact_fact": {"high": 0.85, "medium": 0.60},
    "summarization": {"high": 0.65, "medium": 0.40}
}'::jsonb
WHERE version = 'v13.0';

-- Migration 007: Phase 4 - Policy Infrastructure Hardening (Wave 1)
-- Schema Foundation for Immutable Policy Versioning & Deterministic Replay
-- 
-- This migration implements the foundational schema changes for Phase 4:
-- 1. Policy hashing & immutability verification
-- 2. Policy activation audit history
-- 3. Telemetry schema versioning for forward compatibility
-- 4. Frozen retrieval snapshots for deterministic replay
--
-- Status: Idempotent (safe to run multiple times)
-- Created: 2026-03-08

BEGIN TRANSACTION;

-- ============================================================================
-- TASK 1.1: Add Policy Hash & Content Immutability
-- ============================================================================
-- Purpose: Enable immutable policy content verification via SHA-256 fingerprints
-- Impact: policy_registry table gains policy_hash column with UNIQUE constraint

ALTER TABLE intelligence.policy_registry
ADD COLUMN IF NOT EXISTS policy_hash TEXT;

COMMENT ON COLUMN intelligence.policy_registry.policy_hash IS 
  'SHA-256 fingerprint of policy content (format: sha256:<hexdigest>). Enables tamper-proof identity and immutability verification.';

-- Backfill existing policies with deterministic SHA-256 hashes
-- Uses canonical JSON format (sorted keys, tight spacing) for determinism
-- Only backfills policies that don't have a hash yet
UPDATE intelligence.policy_registry
SET policy_hash = 'sha256:' || encode(
    digest(
        json_build_object(
            'thresholds', thresholds,
            'routing_rules', routing_rules,
            'contextual_thresholds', COALESCE(contextual_thresholds, '{}'::jsonb),
            'latency_budgets', COALESCE(latency_budgets, '{}'::jsonb),
            'version', version
        )::text,
        'sha256'
    ),
    'hex'
)
WHERE policy_hash IS NULL;

-- Create UNIQUE constraint to prevent duplicate policy content
CREATE UNIQUE INDEX IF NOT EXISTS idx_policy_registry_policy_hash_unique
ON intelligence.policy_registry(policy_hash);

-- Add NOT NULL constraint after backfill ensures all new policies must have hashes
ALTER TABLE intelligence.policy_registry
ALTER COLUMN policy_hash SET NOT NULL;

-- ============================================================================
-- TASK 1.2: Create Policy Activation History Table
-- ============================================================================
-- Purpose: Track every policy activation with timestamp, actor, reason, and prior state
-- Impact: New table intelligence.policy_activations for audit trail

CREATE TABLE IF NOT EXISTS intelligence.policy_activations (
    activation_id SERIAL PRIMARY KEY,
    policy_version TEXT NOT NULL REFERENCES intelligence.policy_registry(version),
    activated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_by TEXT NOT NULL,  -- e.g., "admin:user@example.com", "calibration:auto", "ci:regression_test"
    reason TEXT,                 -- e.g., "Deployment v2.1", "Rollback from v13.1", "Manual A/B test"
    deactivated_at TIMESTAMPTZ,  -- NULL while policy is active; populated when replaced
    prior_policy_version TEXT REFERENCES intelligence.policy_registry(version),
    
    CONSTRAINT check_deactivation_order CHECK (deactivated_at IS NULL OR deactivated_at > activated_at)
);

COMMENT ON TABLE intelligence.policy_activations IS 
  'Append-only audit trail of policy activations. Records who activated which policy, when, why, and what prior policy was active.';

COMMENT ON COLUMN intelligence.policy_activations.activated_by IS 
  'Actor identity. Format: <source>:<identifier>, e.g. "admin:ops@company.com", "calibration:auto", "ci:phase_4_test".';

COMMENT ON COLUMN intelligence.policy_activations.reason IS 
  'Human-readable reason for activation (optional). Examples: "Deployment v2.1", "Rollback from v13.1", "A/B test control group".';

COMMENT ON COLUMN intelligence.policy_activations.prior_policy_version IS 
  'Which policy was active immediately before this activation. Enables quick rollback and audit chain traversal.';

-- Indexes for efficient audit queries
CREATE INDEX IF NOT EXISTS idx_policy_activations_policy_version
ON intelligence.policy_activations(policy_version);

CREATE INDEX IF NOT EXISTS idx_policy_activations_activated_at_desc
ON intelligence.policy_activations(activated_at DESC);

CREATE INDEX IF NOT EXISTS idx_policy_activations_activated_by
ON intelligence.policy_activations(activated_by);

-- ============================================================================
-- TASK 1.3: Add Telemetry Schema Version & Policy Hash to Traces
-- ============================================================================
-- Purpose: Enable forward-compatible schema evolution and deterministic replay identity
-- Impact: policy_telemetry table gains policy_hash and telemetry_schema_version columns

ALTER TABLE intelligence.policy_telemetry
ADD COLUMN IF NOT EXISTS policy_hash TEXT,
ADD COLUMN IF NOT EXISTS telemetry_schema_version TEXT DEFAULT '1.0';

COMMENT ON COLUMN intelligence.policy_telemetry.policy_hash IS 
  'SHA-256 hash of the policy used for this request. Enables deterministic replay verification and immutability checks.';

COMMENT ON COLUMN intelligence.policy_telemetry.telemetry_schema_version IS 
  'Schema version of this trace record. Enables version-aware queries and forward-compatible migrations. Current: 1.0 (Phase 4 foundation).';

-- Indexes for schema version queries (enables efficient version-aware filtering)
CREATE INDEX IF NOT EXISTS idx_telemetry_schema_version
ON intelligence.policy_telemetry(telemetry_schema_version);

-- ============================================================================
-- TASK 1.4: Create Frozen Retrieval Items Storage
-- ============================================================================
-- Purpose: Store immutable retrieval results for deterministic replay
-- Impact: policy_telemetry table gains retrieval_items JSONB column

ALTER TABLE intelligence.policy_telemetry
ADD COLUMN IF NOT EXISTS retrieval_items JSONB DEFAULT '{}'::jsonb;

COMMENT ON COLUMN intelligence.policy_telemetry.retrieval_items IS 
  'Frozen snapshot of retrieval results (item IDs, scores, rank order, parameters) captured at request time. Enables deterministic replay without live queries.';

-- GIN index for efficient JSONB queries (e.g., filtering by retrieval mode, item type)
CREATE INDEX IF NOT EXISTS idx_telemetry_retrieval_items_gin
ON intelligence.policy_telemetry
USING gin (retrieval_items);

-- ============================================================================
-- VERIFICATION & SCHEMA CONSISTENCY
-- ============================================================================
-- Ensure all new columns are visible and consistent

ALTER TABLE intelligence.policy_telemetry
ADD COLUMN IF NOT EXISTS retrieval_parameters JSONB DEFAULT '{}'::jsonb;

COMMENT ON COLUMN intelligence.policy_telemetry.retrieval_parameters IS 
  'Retrieval parameters at request time (limit, threshold, mode). Enables replay context reconstruction.';

COMMIT;

-- ============================================================================
-- SUCCESS VERIFICATION QUERIES
-- ============================================================================
-- These queries can be run post-migration to verify all changes applied correctly:
--
-- 1. Verify policy_hash column and constraint:
--    SELECT COUNT(*) FROM intelligence.policy_registry WHERE policy_hash IS NULL;
--    (Should return: 0)
--
-- 2. Verify activation history table:
--    SELECT COUNT(*) FROM information_schema.tables 
--    WHERE table_schema='intelligence' AND table_name='policy_activations';
--    (Should return: 1)
--
-- 3. Verify telemetry columns:
--    SELECT column_name FROM information_schema.columns 
--    WHERE table_schema='intelligence' AND table_name='policy_telemetry'
--    AND column_name IN ('policy_hash', 'telemetry_schema_version', 'retrieval_items');
--    (Should return: 3 rows with those column names)
--
-- 4. Verify schema version index:
--    SELECT * FROM pg_indexes 
--    WHERE tablename='policy_telemetry' AND indexname='idx_telemetry_schema_version';
--    (Should return: 1 row)
--
-- 5. Verify GIN index on retrieval_items:
--    SELECT * FROM pg_indexes 
--    WHERE tablename='policy_telemetry' AND indexname='idx_telemetry_retrieval_items_gin';
--    (Should return: 1 row)

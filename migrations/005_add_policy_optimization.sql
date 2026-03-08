-- Phase 13: Closed-Loop Policy Optimization Infrastructure

-- Policy Registry for versioned thresholds and rules
CREATE TABLE IF NOT EXISTS intelligence.policy_registry (
    version TEXT PRIMARY KEY,
    is_active BOOLEAN DEFAULT FALSE,
    thresholds JSONB NOT NULL DEFAULT '{
        "high": 0.75,
        "medium": 0.50,
        "low": 0.25,
        "insufficient": 0.0
    }'::jsonb,
    routing_rules JSONB NOT NULL DEFAULT '{
        "default": "standard",
        "query_types": {
            "ambiguous": {"medium": "query_expand + rerank"},
            "multi-hop": {"medium": "expanded_retrieval"},
            "exact_fact": {"medium": "rerank_only"}
        }
    }'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for active policy lookup
CREATE INDEX IF NOT EXISTS idx_policy_registry_active ON intelligence.policy_registry(is_active) WHERE is_active = TRUE;

-- Insert initial policy v13.0
INSERT INTO intelligence.policy_registry (version, is_active)
VALUES ('v13.0', TRUE)
ON CONFLICT (version) DO NOTHING;

-- Policy Telemetry for request-level tracing
CREATE TABLE IF NOT EXISTS intelligence.policy_telemetry (
    query_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_text TEXT NOT NULL,
    query_type TEXT DEFAULT 'general',
    confidence_score FLOAT NOT NULL,
    confidence_band TEXT NOT NULL,
    action_taken TEXT NOT NULL,
    execution_path TEXT NOT NULL,
    policy_version TEXT REFERENCES intelligence.policy_registry(version),
    
    -- Retrieval metadata
    retrieval_mode TEXT DEFAULT 'hybrid',
    chunks_retrieved INTEGER DEFAULT 0,
    
    -- Outcome metrics
    latency_ms INTEGER,
    groundedness_score FLOAT,
    unsupported_claim_count INTEGER,
    citation_accuracy FLOAT,
    quality_score FLOAT, -- From AnswerQuality enum (0-5)
    
    -- Raw response data (optional, for replay)
    metadata JSONB DEFAULT '{}'::jsonb,
    
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for analysis
CREATE INDEX IF NOT EXISTS idx_telemetry_policy_version ON intelligence.policy_telemetry(policy_version);
CREATE INDEX IF NOT EXISTS idx_telemetry_confidence_band ON intelligence.policy_telemetry(confidence_band);
CREATE INDEX IF NOT EXISTS idx_telemetry_created_at ON intelligence.policy_telemetry(created_at);
CREATE INDEX IF NOT EXISTS idx_telemetry_query_type ON intelligence.policy_telemetry(query_type);

-- Trigger for policy updated_at
CREATE TRIGGER update_policy_registry_updated_at
    BEFORE UPDATE ON intelligence.policy_registry
    FOR EACH ROW
    EXECUTE FUNCTION intelligence.update_updated_at_column();

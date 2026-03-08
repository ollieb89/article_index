import pytest
from shared.policy import RAGPolicy
from shared.evidence_scorer import EvidenceScorer, ConfidenceBand

def test_rag_policy_contextual_thresholds():
    """Test that policy returns different thresholds based on query_type."""
    policy = RAGPolicy(
        version="test-14",
        thresholds={"high": 0.75, "medium": 0.50},
        contextual_thresholds={
            "exact_fact": {"high": 0.90, "medium": 0.70},
            "summarization": {"high": 0.60, "medium": 0.30}
        }
    )
    
    # Global fallback
    assert policy.get_threshold("high", "general") == 0.75
    
    # Exact fact overrides (stricter)
    assert policy.get_threshold("high", "exact_fact") == 0.90
    assert policy.get_threshold("medium", "exact_fact") == 0.70
    
    # Summarization overrides (looser)
    assert policy.get_threshold("high", "summarization") == 0.60
    assert policy.get_threshold("medium", "summarization") == 0.30

def test_rag_policy_latency_budgets():
    """Test that policy returns correct latency budgets."""
    policy = RAGPolicy(
        version="test-14",
        latency_budgets={
            "general": 2000,
            "exact_fact": 500
        }
    )
    
    assert policy.get_latency_budget("general") == 2000
    assert policy.get_latency_budget("exact_fact") == 500
    assert policy.get_latency_budget("unknown") == 2000 # Fallback to general

def test_evidence_scorer_contextual_bands():
    """Test that EvidenceScorer assigns correct bands using contextual thresholds."""
    policy = RAGPolicy(
        version="test-14",
        thresholds={"high": 0.75, "medium": 0.50, "low": 0.25},
        contextual_thresholds={
            "exact_fact": {"high": 0.85, "medium": 0.65}
        }
    )
    
    scorer = EvidenceScorer()
    # Rich chunks that produce a composite score between 0.75 (general high) and 0.85 (exact_fact high)
    # - high top score → strong score_strength
    # - gradual decay → good score_decay
    # - all from both lexical+vector but with full overlap → slightly penalised method_agreement
    # - 3 unique docs → good source_diversity
    # Together these produce composite ~0.83 which is HIGH for general (>= 0.75)
    # but MEDIUM for exact_fact (>= 0.85)
    chunks = [
        {"hybrid_score": 0.90, "document_id": 1, "from_lexical": True, "from_vector": True},
        {"hybrid_score": 0.85, "document_id": 2, "from_lexical": True, "from_vector": True},
        {"hybrid_score": 0.82, "document_id": 3, "from_lexical": True, "from_vector": True},
        {"hybrid_score": 0.78, "document_id": 1, "from_lexical": True, "from_vector": True},
        {"hybrid_score": 0.75, "document_id": 2, "from_lexical": True, "from_vector": True},
    ]

    # In 'general' context, composite score ~0.83 → HIGH (>= 0.75)
    conf_general = scorer.score_evidence(chunks, "query", query_type="general", policy=policy)
    assert conf_general.band == "high"

    # In 'exact_fact' context, same composite score ~0.83 → MEDIUM (0.65 <= 0.83 < 0.85)
    conf_exact = scorer.score_evidence(chunks, "query", query_type="exact_fact", policy=policy)
    assert conf_exact.band == "medium"

def test_evidence_shape_metadata():
    """Test that ConfidenceScore includes evidence shape metadata."""
    scorer = EvidenceScorer()
    chunks = [
        {"hybrid_score": 0.9, "document_id": 1, "from_lexical": True, "from_vector": True},
        {"hybrid_score": 0.8, "document_id": 2, "from_lexical": True}
    ]
    
    conf = scorer.score_evidence(chunks, "query")
    shape = conf.component_scores.get("evidence_shape")
    
    assert shape is not None
    assert shape["source_count"] == 2
    assert "source_diversity" in shape
    assert "score_decay" in shape
    assert "agreement" in shape

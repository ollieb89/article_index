import sys
import os
from typing import Dict, List, Any

# Add workspace root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from shared.policy import RAGPolicy
from shared.evidence_scorer import EvidenceScorer

def verify_policy_logic():
    print("Verifying RAGPolicy contextual thresholds...")
    policy = RAGPolicy(
        version="test-14",
        thresholds={"high": 0.75, "medium": 0.50},
        contextual_thresholds={
            "exact_fact": {"high": 0.90, "medium": 0.70},
            "summarization": {"high": 0.60, "medium": 0.30}
        }
    )
    
    assert policy.get_threshold("high", "general") == 0.75
    assert policy.get_threshold("high", "exact_fact") == 0.90
    assert policy.get_threshold("medium", "summarization") == 0.30
    print("✅ RAGPolicy logic verified.")

def verify_evidence_scorer():
    print("Verifying EvidenceScorer contextual bands...")
    policy = RAGPolicy(
        version="test-14",
        thresholds={"high": 0.75, "medium": 0.50, "low": 0.25},
        contextual_thresholds={
            "exact_fact": {"high": 0.85, "medium": 0.65}
        }
    )
    
    scorer = EvidenceScorer()
    # High-quality chunks: multiple sources, high scores, high agreement
    chunks = [
        {"hybrid_score": 0.9, "document_id": 1, "from_lexical": True, "from_vector": True},
        {"hybrid_score": 0.85, "document_id": 2, "from_lexical": True, "from_vector": True},
        {"hybrid_score": 0.82, "document_id": 3, "from_lexical": True, "from_vector": True},
        {"hybrid_score": 0.8, "document_id": 4, "from_lexical": True, "from_vector": True},
        {"hybrid_score": 0.78, "document_id": 5, "from_lexical": True, "from_vector": True}
    ]
    
    conf_general = scorer.score_evidence(chunks, "query", query_type="general", policy=policy)
    print(f"  General context score: {conf_general.score:.3f}, Band: {conf_general.band}")
    assert conf_general.band == "high"
    
    # Policy override for 'exact_fact' high is 0.85
    # If chunks.score is e.g. 0.82, it should be medium in exact_fact but high in general
    conf_exact = scorer.score_evidence(chunks, "query", query_type="exact_fact", policy=policy)
    print(f"  Exact context score: {conf_exact.score:.3f}, Band: {conf_exact.band}")
    assert conf_exact.band == "medium"
    
    print("✅ EvidenceScorer logic verified.")

def verify_evidence_shape():
    print("Verifying Evidence Shape metadata...")
    scorer = EvidenceScorer()
    chunks = [
        {"hybrid_score": 0.9, "document_id": 1, "from_lexical": True, "from_vector": True},
        {"hybrid_score": 0.8, "document_id": 2, "from_lexical": True}
    ]
    
    conf = scorer.score_evidence(chunks, "query")
    shape = conf.component_scores.get("evidence_shape")
    print(f"  Shape info: {shape}")
    assert shape["source_count"] == 2
    assert "source_diversity" in shape
    print("✅ Evidence Shape verified.")

if __name__ == "__main__":
    try:
        verify_policy_logic()
        verify_evidence_scorer()
        verify_evidence_shape()
        print("\n--- ALL LOGIC VERIFIED ---")
    except Exception as e:
        print(f"\n❌ VERIFICATION FAILED: {e}")
        sys.exit(1)

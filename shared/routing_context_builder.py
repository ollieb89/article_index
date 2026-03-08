"""RoutingContext builder for Phase 5 integration.

This module provides helper functions to build a RoutingContext from
existing pipeline components (classifier, state labeler, evidence scorer).

Usage Example:
    ```python
    from shared.routing_context_builder import build_routing_context
    
    context = build_routing_context(
        query="What is machine learning?",
        query_type=QueryType.EXACT_FACT,
        chunks=retrieved_chunks,
        confidence_band="high",
        retrieval_state=RetrievalState.SOLID,
        evidence_shape=evidence_shape,
        effort_budget="medium"
    )
    ```
"""

import logging
from typing import Any, Dict, List, Optional
from shared.routing_engine import RoutingContext

logger = logging.getLogger(__name__)


def build_routing_context(
    query: str,
    query_type: Any,
    chunks: List[Dict[str, Any]],
    confidence_band: str,
    retrieval_state: Any,
    evidence_shape: Optional[Any] = None,
    effort_budget: str = "medium"
) -> RoutingContext:
    """Build a RoutingContext from pipeline components.
    
    This is the glue between retrieval/evidence components and the rule engine.
    
    Args:
        query: The user's query string
        query_type: QueryType enum or string
        chunks: Retrieved chunks (used for evidence shape extraction)
        confidence_band: Confidence band string (high/medium/low/insufficient)
        retrieval_state: RetrievalState enum or string
        evidence_shape: EvidenceShape object or None
        effort_budget: Effort budget level (low/medium/high)
        
    Returns:
        RoutingContext populated with all fields
    """
    # Convert enums to strings
    query_type_str = _to_string(query_type)
    retrieval_state_str = _to_string(retrieval_state)
    
    # Build evidence shape bands dict
    evidence_shape_bands = _extract_evidence_bands(evidence_shape)
    
    context = RoutingContext(
        query_type=query_type_str,
        retrieval_state=retrieval_state_str,
        confidence_band=confidence_band,
        evidence_shape=evidence_shape_bands,
        effort_budget=effort_budget
    )
    
    logger.debug(
        f"Built RoutingContext: type={query_type_str}, "
        f"state={retrieval_state_str}, band={confidence_band}, "
        f"budget={effort_budget}"
    )
    
    return context


def _to_string(value: Any) -> str:
    """Convert enum or string to string."""
    if value is None:
        return "other"
    if hasattr(value, 'value'):
        return value.value
    return str(value)


def _extract_evidence_bands(evidence_shape: Optional[Any]) -> Dict[str, str]:
    """Extract categorical bands from EvidenceShape object.
    
    Args:
        evidence_shape: EvidenceShape object or dict
        
    Returns:
        Dict with coverage_band, agreement_band, spread_band
    """
    if evidence_shape is None:
        return {}
    
    # If it's already a dict, return it
    if isinstance(evidence_shape, dict):
        # Filter to only include band fields
        bands = {}
        for key in ['coverage_band', 'agreement_band', 'spread_band']:
            if key in evidence_shape:
                bands[key] = evidence_shape[key]
        return bands
    
    # Extract from object
    bands = {}
    
    # Try to get raw scores and compute bands
    coverage_score = getattr(evidence_shape, 'top1_score', None)
    agreement_score = getattr(evidence_shape, 'chunk_agreement', None)
    
    # Compute bands from scores
    if coverage_score is not None:
        bands['coverage_band'] = _score_to_band(coverage_score, 
                                               high=0.80, medium=0.50)
    
    if agreement_score is not None:
        bands['agreement_band'] = _score_to_band(agreement_score,
                                                high=0.75, medium=0.45)
    
    # Check for spread (would need score distribution)
    # For now, omit spread_band if not directly available
    
    return bands


def _score_to_band(score: float, high: float, medium: float) -> str:
    """Convert a score to a categorical band.
    
    Args:
        score: Numeric score [0, 1]
        high: Threshold for high band
        medium: Threshold for medium band
        
    Returns:
        Band string: high, medium, or low
    """
    if score >= high:
        return "high"
    elif score >= medium:
        return "medium"
    else:
        return "low"


def update_trace_from_decision(
    trace: Any,
    decision: Any,
    context: RoutingContext
) -> None:
    """Update a PolicyTrace with Phase 5 fields from a RoutingDecision.
    
    Args:
        trace: PolicyTrace object to update
        decision: RoutingDecision with Phase 5 fields
        context: RoutingContext used for routing
    """
    # Update trace with Phase 5 fields
    if hasattr(trace, 'query_type'):
        trace.query_type = context.query_type
    
    if hasattr(trace, 'retrieval_state'):
        trace.retrieval_state = context.retrieval_state
    
    if hasattr(trace, 'evidence_shape_bands'):
        trace.evidence_shape_bands = context.evidence_shape
    
    if hasattr(trace, 'effort_budget'):
        trace.effort_budget = context.effort_budget
    
    # Update with decision fields
    if hasattr(decision, 'matched_rule_id') and hasattr(trace, 'matched_rule_id'):
        trace.matched_rule_id = decision.matched_rule_id
    
    if hasattr(decision, 'matched_rule_priority') and hasattr(trace, 'matched_rule_priority'):
        trace.matched_rule_priority = decision.matched_rule_priority
    
    if hasattr(decision, 'matched_rule_specificity') and hasattr(trace, 'matched_rule_specificity'):
        trace.matched_rule_specificity = decision.matched_rule_specificity
    
    if hasattr(decision, 'fallback_used') and hasattr(trace, 'fallback_used'):
        trace.fallback_used = decision.fallback_used
    
    if hasattr(decision, 'fallback_reason') and hasattr(trace, 'fallback_reason'):
        trace.fallback_reason = decision.fallback_reason
    
    if hasattr(decision, 'budget_override_applied') and hasattr(trace, 'budget_override_applied'):
        trace.budget_override_applied = decision.budget_override_applied
    
    if hasattr(decision, 'requested_execution_path') and hasattr(trace, 'requested_execution_path'):
        trace.requested_execution_path = decision.requested_execution_path
    
    # Update telemetry schema version
    if hasattr(trace, 'telemetry_schema_version'):
        trace.telemetry_schema_version = "1.1"

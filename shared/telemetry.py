"""Policy telemetry for closed-loop RAG optimization.

This module provides the PolicyTrace structure for logging request-level
outcomes, including confidence scores, actions taken, and answer quality.
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class PolicyTrace:
    """Detailed telemetry for a single RAG request."""
    query_text: str
    query_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    query_type: str = "general"
    confidence_score: float = 0.0
    confidence_band: str = "unknown"
    action_taken: str = "none"
    execution_path: str = "none"
    retrieval_state: str = "unknown"
    policy_version: str = "unknown"
    
    retrieval_mode: str = "hybrid"
    chunks_retrieved: int = 0
    
    latency_ms: Optional[int] = None
    groundedness_score: Optional[float] = None
    unsupported_claim_count: Optional[int] = None
    citation_accuracy: Optional[float] = None
    quality_score: Optional[float] = None
    
    # Phase 2: Confidence-driven control
    retrieval_depth: int = 0  # Number of candidates retrieved before ranking
    reranker_invoked: bool = False  # Whether reranker was called
    reranker_reason: Optional[str] = None  # Why: "score_gap", "weak_evidence", "conflict", "cautious_path_mandatory", etc.
    
    # Token accounting
    tokens_generated: int = 0  # Tokens in final answer
    tokens_total: int = 0  # All tokens (retrieval + generation context + answer)
    
    # Abstention tracking
    abstention_triggered: bool = False  # True if query returned abstention response
    
    # Stage flags for routing decisions
    stage_flags: Dict[str, bool] = field(default_factory=dict)  # e.g., {"reranker_invoked": True, "retrieval_expanded": False}
    
    evidence_shape: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # Phase 4: Policy infrastructure hardening fields
    policy_hash: Optional[str] = None  # SHA-256 hash of policy content
    telemetry_schema_version: str = "1.1"  # Schema version for forward compatibility (1.1 = Phase 5)
    retrieval_items: List[Dict[str, Any]] = field(default_factory=list)  # Frozen retrieval snapshot
    retrieval_parameters: Dict[str, Any] = field(default_factory=dict)  # Retrieval params at request time
    
    # Phase 5: Contextual Policy Routing fields
    evidence_shape_bands: Dict[str, str] = field(default_factory=dict)  # coverage_band, agreement_band, spread_band
    effort_budget: str = "medium"  # low, medium, high
    matched_rule_id: Optional[str] = None  # Which rule was applied
    matched_rule_priority: Optional[int] = None  # Priority of winning rule
    matched_rule_specificity: Optional[int] = None  # Specificity of winning rule
    fallback_used: bool = False  # Whether fallback was triggered
    fallback_reason: Optional[str] = None  # Why fallback was used
    budget_override_applied: bool = False  # Whether budget constraint downgraded path
    requested_execution_path: Optional[str] = None  # Original path before budget override

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        result = {
            "query_id": self.query_id,
            "query_text": self.query_text,
            "query_type": self.query_type,
            "confidence_score": self.confidence_score,
            "confidence_band": self.confidence_band,
            "action_taken": self.action_taken,
            "execution_path": self.execution_path,
            "retrieval_state": self.retrieval_state,
            "policy_version": self.policy_version,
            "retrieval_mode": self.retrieval_mode,
            "chunks_retrieved": self.chunks_retrieved,
            "latency_ms": self.latency_ms,
            "groundedness_score": self.groundedness_score,
            "unsupported_claim_count": self.unsupported_claim_count,
            "citation_accuracy": self.citation_accuracy,
            "quality_score": self.quality_score,
            # Phase 2 fields
            "retrieval_depth": self.retrieval_depth,
            "reranker_invoked": self.reranker_invoked,
            "reranker_reason": self.reranker_reason,
            "tokens_generated": self.tokens_generated,
            "tokens_total": self.tokens_total,
            "abstention_triggered": self.abstention_triggered,
            "evidence_shape": self.evidence_shape,
            "created_at": self.created_at,
            # Phase 4 fields
            "policy_hash": self.policy_hash,
            "telemetry_schema_version": self.telemetry_schema_version,
            "retrieval_items": self.retrieval_items,
            "retrieval_parameters": self.retrieval_parameters,
            # Phase 5 fields
            "evidence_shape_bands": self.evidence_shape_bands,
            "effort_budget": self.effort_budget,
            "matched_rule_id": self.matched_rule_id,
            "matched_rule_priority": self.matched_rule_priority,
            "matched_rule_specificity": self.matched_rule_specificity,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
            "budget_override_applied": self.budget_override_applied,
            "requested_execution_path": self.requested_execution_path
        }
        
        # Merge stage_flags into metadata
        metadata = dict(self.metadata)
        if self.stage_flags:
            metadata["stage_flags"] = self.stage_flags
        result["metadata"] = metadata
        
        return result


def backfill_trace_fields(trace: Dict, source_version: str = "0.9") -> Dict:
    """Backfill missing fields in telemetry traces for forward compatibility.
    
    This function ensures pre-Phase4 traces can be processed with Phase4+ code
    by deriving missing fields from available data.
    
    Args:
        trace: Telemetry trace dict (potentially from older schema version)
        source_version: Schema version of the source trace
        
    Returns:
        Updated trace dict with all Phase 4 fields populated
    """
    result = dict(trace)
    
    # Set schema version if missing
    if not result.get('telemetry_schema_version'):
        result['telemetry_schema_version'] = '1.0'
    
    # Derive retrieval_state from confidence_band if missing
    if not result.get('retrieval_state') and result.get('confidence_band'):
        band = result['confidence_band']
        state_map = {
            'high': 'SOLID',
            'medium': 'FRAGILE',
            'low': 'SPARSE',
            'insufficient': 'ABSENT',
            'unknown': 'ABSENT'
        }
        result['retrieval_state'] = state_map.get(band, 'ABSENT')
    
    # Derive stage_flags from execution_path if missing
    if not result.get('stage_flags') and result.get('execution_path'):
        path = result['execution_path']
        result['stage_flags'] = {
            'reranker_invoked': path in ['cautious', 'expanded_retrieval'],
            'retrieval_expanded': path == 'expanded_retrieval'
        }
    
    # Ensure retrieval_items exists
    if not result.get('retrieval_items'):
        result['retrieval_items'] = []
    
    # Ensure retrieval_parameters exists
    if not result.get('retrieval_parameters'):
        result['retrieval_parameters'] = {
            'limit': result.get('chunks_retrieved', 5),
            'threshold': 0.7  # Default threshold
        }
    
    return result


def validate_telemetry_health(trace: Dict) -> Tuple[bool, List[str]]:
    """Validate telemetry trace completeness and data quality.
    
    Args:
        trace: Telemetry trace dict to validate
        
    Returns:
        Tuple of (is_valid: bool, error_messages: List[str])
    """
    errors = []
    
    # Check required fields
    required_fields = [
        'query_id', 'query_text', 'query_type', 'confidence_score',
        'confidence_band', 'action_taken', 'routing_action', 
        'policy_version', 'retrieval_state'
    ]
    
    for field in required_fields:
        if field not in trace or trace[field] is None:
            errors.append(f"Missing required field: {field}")
    
    # Validate confidence_band values
    valid_bands = ['high', 'medium', 'low', 'insufficient', 'unknown']
    if trace.get('confidence_band') and trace['confidence_band'] not in valid_bands:
        errors.append(f"Invalid confidence_band: {trace['confidence_band']}")
    
    # Validate routing_action present
    if not trace.get('routing_action') and not trace.get('action_taken'):
        errors.append("Missing routing_action or action_taken")
    
    # Validate policy_version present
    if not trace.get('policy_version'):
        errors.append("Missing policy_version")
    
    return len(errors) == 0, errors

"""Policy telemetry for closed-loop RAG optimization.

This module provides the PolicyTrace structure for logging request-level
outcomes, including confidence scores, actions taken, and answer quality.
"""

import logging
from typing import List, Dict, Any, Optional
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
    
    evidence_shape: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
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
            "evidence_shape": self.evidence_shape,
            "metadata": self.metadata,
            "created_at": self.created_at
        }

import logging
from enum import Enum
from .evidence_shape import EvidenceShape

logger = logging.getLogger(__name__)

class RetrievalState(str, Enum):
    STRONG = "strong"
    RECOVERABLE = "recoverable"
    FRAGILE = "fragile"
    INSUFFICIENT = "insufficient"
    CONFLICTED = "conflicted"

class RetrievalStateLabeler:
    """Maps EvidenceShape to operational retrieval states."""

    def label(self, shape: EvidenceShape) -> RetrievalState:
        """Categorize the evidence shape into a RetrievalState.
        
        Args:
            shape: The EvidenceShape extracted from retrieval results.
            
        Returns:
            The corresponding RetrievalState.
        """
        if shape.contradiction_flag:
            return RetrievalState.CONFLICTED
            
        if shape.source_count == 0 or shape.top1_score < 0.3:
            return RetrievalState.INSUFFICIENT
            
        # Strong evidence: high score and good overlap/consensus
        if shape.top1_score >= 0.75 and (shape.chunk_agreement >= 0.5 or shape.source_count >= 2):
            return RetrievalState.STRONG
            
        # Recoverable: moderate score or good diversity despite lower overlap
        if shape.top1_score >= 0.5 or (shape.topk_mean_score >= 0.4 and shape.source_count >= 3):
            return RetrievalState.RECOVERABLE
            
        # Fragile: weak scores or single source with low score
        return RetrievalState.FRAGILE

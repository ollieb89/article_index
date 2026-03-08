import logging
import numpy as np
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class EvidenceShape:
    """Structural characteristics of retrieved evidence."""
    def __init__(
        self,
        top1_score: float,
        topk_mean_score: float,
        score_gap: float,
        source_diversity: float,
        source_count: int,
        chunk_agreement: float,
        contradiction_flag: bool = False
    ):
        self.top1_score = top1_score
        self.topk_mean_score = topk_mean_score
        self.score_gap = score_gap
        self.source_diversity = source_diversity
        self.source_count = source_count
        self.chunk_agreement = chunk_agreement
        self.contradiction_flag = contradiction_flag

    def to_dict(self) -> Dict[str, Any]:
        return {
            "top1_score": round(self.top1_score, 3),
            "topk_mean_score": round(self.topk_mean_score, 3),
            "score_gap": round(self.score_gap, 3),
            "source_diversity": round(self.source_diversity, 3),
            "source_count": self.source_count,
            "chunk_agreement": round(self.chunk_agreement, 3),
            "contradiction_flag": self.contradiction_flag
        }

class EvidenceShapeExtractor:
    """Extracts structural characteristics from retrieval results."""

    def extract(self, chunks: List[Dict[str, Any]], query: str) -> EvidenceShape:
        """Analyze chunks to produce an EvidenceShape.
        
        Args:
            chunks: List of retrieved chunks with scores.
            query: The user query.
            
        Returns:
            An EvidenceShape object.
        """
        if not chunks:
            return EvidenceShape(0, 0, 0, 0, 0, 0)

        scores = [c.get('hybrid_score', c.get('rrf_score', c.get('semantic_score', 0))) for c in chunks]
        top1_score = scores[0]
        topk_mean = sum(scores) / len(scores)
        score_gap = (scores[0] - scores[1]) if len(scores) > 1 else 1.0
        
        doc_ids = [c.get('document_id') for c in chunks if c.get('document_id')]
        unique_docs = set(doc_ids)
        source_count = len(unique_docs)
        source_diversity = source_count / len(chunks) if chunks else 0
        
        # Simple agreement: lexical vs vector overlap in top results
        lexical_count = sum(1 for c in chunks if c.get('from_lexical'))
        vector_count = sum(1 for c in chunks if c.get('from_vector'))
        both_count = sum(1 for c in chunks if c.get('from_lexical') and c.get('from_vector'))
        agreement = both_count / len(chunks) if chunks else 0
        
        # Simple contradiction detection: if scores are high but sources disagree
        # (Placeholder for more advanced logic)
        contradiction_flag = False
        if source_count > 1 and agreement < 0.2 and top1_score > 0.8:
            # High score but very different results from different sources might mean conflict
            # In a real system, we'd use an LLM or NLI here.
            contradiction_flag = False # Keep safe for now unless specifically triggered
            
        return EvidenceShape(
            top1_score=top1_score,
            topk_mean_score=topk_mean,
            score_gap=score_gap,
            source_diversity=source_diversity,
            source_count=source_count,
            chunk_agreement=agreement,
            contradiction_flag=contradiction_flag
        )

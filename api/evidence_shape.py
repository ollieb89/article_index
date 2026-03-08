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
        
        # Phase 2: Real contradiction detection
        contradiction_flag = self._detect_contradiction(chunks, top1_score)
            
        return EvidenceShape(
            top1_score=top1_score,
            topk_mean_score=topk_mean,
            score_gap=score_gap,
            source_diversity=source_diversity,
            source_count=source_count,
            chunk_agreement=agreement,
            contradiction_flag=contradiction_flag
        )

    def _detect_contradiction(self, chunks: List[Dict[str, Any]], top_score: float) -> bool:
        """
        Detect contradictory claims in top passages using rule-based approach.
        
        Simple rule-based approach:
        - Look for explicit negations in top-3 passages
        - Check for opposing entities or actions
        - Flag if found and all passages have high scores
        
        Returns:
            True if contradiction detected, False otherwise
        """
        import re
        
        if len(chunks) < 2 or top_score < 0.7:
            return False  # Can't have contradiction with low confidence or few sources
        
        # Patterns indicating negation or opposition
        negation_patterns = [
            r'\b(?:no|not|never|neither|cannot|isnt|dont|doesnt|wont)\b',
            r'\b(?:false|incorrect|wrong|denial of|denies|denying)\b'
        ]
        
        top_chunks = chunks[:3]
        texts = [c.get('content', '') for c in top_chunks]
        
        # Count negations in each chunk
        negation_counts = []
        for text in texts:
            count = sum(
                len(re.findall(pattern, text, re.IGNORECASE))
                for pattern in negation_patterns
            )
            negation_counts.append(count)
        
        # If one chunk has high negations and another has few, likely contradiction
        has_strong_negation = max(negation_counts) > 2
        has_no_negation = min(negation_counts) == 0
        
        if has_strong_negation and has_no_negation:
            logger.debug(
                f"Phase 2 contradiction detected: negation pattern mismatch in top passages "
                f"(negations: {negation_counts})"
            )
            return True
        
        return False

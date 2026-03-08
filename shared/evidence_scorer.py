"""Evidence scoring for retrieval confidence estimation.

This module calculates confidence scores for retrieved evidence,
helping determine if the system has enough quality information
to answer confidently.

Confidence inputs:
- Top-k score strength and decay
- Lexical/vector agreement
- Rerank confidence (Phase 7)
- Query transform diversity (Phase 8)
- Source concentration vs diversity

Usage:
    from shared.evidence_scorer import EvidenceScorer
    
    scorer = EvidenceScorer()
    confidence = scorer.score_evidence(
        chunks=retrieved_chunks,
        query="What is machine learning?",
        rerank_decision=decision,  # from Phase 7
        transform_metadata=meta    # from Phase 8
    )
    
    print(f"Confidence: {confidence.score} ({confidence.band})")
"""

import logging
import math
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class ConfidenceBand(Enum):
    """Confidence bands for evidence quality."""
    HIGH = "high"       # > 0.75
    MEDIUM = "medium"   # 0.50 - 0.75
    LOW = "low"         # 0.25 - 0.50
    INSUFFICIENT = "insufficient"  # < 0.25


@dataclass
class ConfidenceScore:
    """Evidence confidence score with breakdown.
    
    Attributes:
        score: Overall confidence (0-1)
        band: Human-readable confidence band
        evidence_strength: Qualitative assessment
        coverage_estimate: Estimated coverage of query (0-1)
        component_scores: Breakdown by input factor
        recommendations: Suggested actions
    """
    score: float
    band: str
    evidence_strength: str
    coverage_estimate: float
    component_scores: Dict[str, float] = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            'score': round(self.score, 3),
            'band': self.band,
            'evidence_strength': self.evidence_strength,
            'coverage_estimate': round(self.coverage_estimate, 3),
            'component_scores': {k: round(v, 3) for k, v in self.component_scores.items()},
            'recommendations': self.recommendations
        }


class EvidenceScorer:
    """Score retrieved evidence for confidence estimation.
    
    Combines multiple signals to estimate whether the retrieved
    evidence is sufficient to answer the query well.
    
    Signals:
    - Score strength: How high are the retrieval scores?
    - Score decay: How quickly do scores drop after rank 1?
    - Method agreement: Do lexical and vector agree?
    - Source diversity: Are results from different documents?
    - Prior confidence: Rerank/transform confidence from earlier stages
    
    Attributes:
        high_confidence_threshold: Threshold for high confidence (default 0.75)
        min_confidence_threshold: Minimum confidence for answering (default 0.25)
    """
    
    def __init__(
        self,
        high_confidence_threshold: float = 0.75,
        min_confidence_threshold: float = 0.25
    ):
        """Initialize the evidence scorer.
        
        Args:
            high_confidence_threshold: Score above which confidence is high
            min_confidence_threshold: Score below which evidence is insufficient
        """
        self.high_confidence_threshold = high_confidence_threshold
        self.min_confidence_threshold = min_confidence_threshold
        
        logger.info(
            f"EvidenceScorer initialized: high_threshold={high_confidence_threshold}, "
            f"min_threshold={min_confidence_threshold}"
        )
    
    def score_evidence(
        self,
        chunks: List[Dict[str, Any]],
        query: str,
        query_type: str = "general",
        rerank_decision: Optional[Any] = None,
        transform_metadata: Optional[Dict[str, Any]] = None,
        policy: Optional[Any] = None
    ) -> ConfidenceScore:
        """Calculate confidence score for retrieved evidence.
        
        Args:
            chunks: Retrieved chunks with scores
            query: Original query
            query_type: Type of query for contextual thresholds
            rerank_decision: Optional rerank decision from Phase 7
            transform_metadata: Optional transform metadata from Phase 8
            policy: Optional RAGPolicy to use for thresholds
            
        Returns:
            ConfidenceScore with overall score and breakdown
        """
        if not chunks:
            return ConfidenceScore(
                score=0.0,
                band=ConfidenceBand.INSUFFICIENT.value,
                evidence_strength="none",
                coverage_estimate=0.0,
                recommendations=["No evidence retrieved"]
            )

        # Determine thresholds from policy or self
        if policy:
            high_threshold = policy.get_threshold("high", query_type)
            medium_threshold = policy.get_threshold("medium", query_type)
            min_threshold = policy.get_threshold("low", query_type)
        else:
            high_threshold = self.high_confidence_threshold
            medium_threshold = 0.50
            min_threshold = self.min_confidence_threshold
        
        # Calculate component scores
        components = {}
        
        # 1. Score strength (how high are the top scores?)
        components['score_strength'] = self._score_strength(chunks)
        
        # 2. Score decay (how quickly do scores drop?)
        components['score_decay'] = self._score_decay(chunks)
        
        # 3. Method agreement (lexical vs vector agreement)
        components['method_agreement'] = self._method_agreement(chunks)
        
        # 4. Source diversity (how many different documents?)
        components['source_diversity'] = self._source_diversity(chunks)
        
        # 5. Prior confidence from reranking
        if rerank_decision and hasattr(rerank_decision, 'confidence'):
            components['rerank_confidence'] = rerank_decision.confidence
        else:
            components['rerank_confidence'] = 0.5  # Neutral
        
        # 6. Query transform overlap/diversity
        if transform_metadata:
            # Higher overlap means transforms found similar results (good consensus)
            # But too high means transforms didn't add diversity
            overlap = transform_metadata.get('result_overlap', 0.5)
            # Ideal overlap: 30-60% (some consensus, some diversity)
            if 0.30 <= overlap <= 0.60:
                components['transform_quality'] = 0.8
            elif overlap < 0.30:
                components['transform_quality'] = 0.5  # Too little overlap
            else:
                components['transform_quality'] = 0.6  # Too much overlap
        else:
            components['transform_quality'] = 0.5  # Neutral if no transform
        
        # Calculate weighted overall score
        weights = {
            'score_strength': 0.25,
            'score_decay': 0.15,
            'method_agreement': 0.15,
            'source_diversity': 0.20,
            'rerank_confidence': 0.15,
            'transform_quality': 0.10
        }
        
        overall_score = sum(
            components.get(k, 0.5) * w for k, w in weights.items()
        )
        
        # Clamp to 0-1
        overall_score = max(0.0, min(1.0, overall_score))
        
        # Determine band
        if overall_score >= high_threshold:
            band = ConfidenceBand.HIGH.value
            strength = "strong"
        elif overall_score >= medium_threshold:
            band = ConfidenceBand.MEDIUM.value
            strength = "moderate"
        elif overall_score >= min_threshold:
            band = ConfidenceBand.LOW.value
            strength = "weak"
        else:
            band = ConfidenceBand.INSUFFICIENT.value
            strength = "insufficient"
        
        # Estimate coverage
        coverage = self._estimate_coverage(chunks, query)
        
        # Generate recommendations
        recommendations = self._generate_recommendations(
            overall_score, components, chunks
        )
        
        # Add shape metadata to component scores
        components['evidence_shape'] = {
            "source_diversity": components.get('source_diversity', 0.5),
            "score_decay": components.get('score_decay', 0.5),
            "agreement": components.get('method_agreement', 0.5),
            "source_count": len(set(c.get('document_id') for c in chunks if c.get('document_id')))
        }
        
        return ConfidenceScore(
            score=overall_score,
            band=band,
            evidence_strength=strength,
            coverage_estimate=coverage,
            component_scores=components,
            recommendations=recommendations
        )
    
    def _score_strength(self, chunks: List[Dict[str, Any]]) -> float:
        """Score based on how high the top retrieval scores are."""
        if not chunks:
            return 0.0
        
        # Get top score
        top_score = chunks[0].get('hybrid_score', 0)
        if not top_score:
            top_score = chunks[0].get('rrf_score', 0)
        if not top_score:
            top_score = chunks[0].get('semantic_score', 0.5)
        
        # Normalize: assume 0.8+ is excellent, 0.5 is okay, <0.3 is poor
        if top_score >= 0.80:
            return 1.0
        elif top_score >= 0.60:
            return 0.8
        elif top_score >= 0.50:
            return 0.6
        elif top_score >= 0.40:
            return 0.4
        elif top_score >= 0.30:
            return 0.2
        else:
            return 0.1
    
    def _score_decay(self, chunks: List[Dict[str, Any]]) -> float:
        """Score based on how quickly scores decay after rank 1."""
        if len(chunks) < 3:
            return 0.5  # Not enough data
        
        # Get scores for top 3
        scores = []
        for chunk in chunks[:3]:
            score = chunk.get('hybrid_score', 0)
            if not score:
                score = chunk.get('rrf_score', 0)
            if not score:
                score = chunk.get('semantic_score', 0.5)
            scores.append(score)
        
        if len(scores) < 2 or scores[0] == 0:
            return 0.5
        
        # Calculate decay: score[2] / score[0]
        # Ideal: gradual decay (high ratio)
        # Bad: sharp drop (low ratio)
        decay_ratio = scores[2] / scores[0]
        
        # Score decay metric: want > 0.6 ratio for high confidence
        if decay_ratio >= 0.70:
            return 1.0
        elif decay_ratio >= 0.60:
            return 0.8
        elif decay_ratio >= 0.50:
            return 0.6
        elif decay_ratio >= 0.40:
            return 0.4
        else:
            return 0.2
    
    def _method_agreement(self, chunks: List[Dict[str, Any]]) -> float:
        """Score based on agreement between lexical and vector retrieval."""
        # Count chunks from each source
        lexical_count = sum(1 for c in chunks if c.get('from_lexical'))
        vector_count = sum(1 for c in chunks if c.get('from_vector'))
        both_count = sum(1 for c in chunks if c.get('from_lexical') and c.get('from_vector'))
        
        total = len(chunks)
        if total == 0:
            return 0.5
        
        # Ideal: good mix with some overlap
        # Both sources present, with overlap
        if lexical_count > 0 and vector_count > 0:
            overlap_ratio = both_count / total
            # Ideal overlap: 30-60%
            if 0.30 <= overlap_ratio <= 0.60:
                return 0.9
            elif overlap_ratio > 0.60:
                return 0.7  # Too much overlap
            else:
                return 0.6  # Too little overlap
        elif lexical_count > 0 or vector_count > 0:
            return 0.4  # Only one source
        else:
            return 0.5  # Unknown sources
    
    def _source_diversity(self, chunks: List[Dict[str, Any]]) -> float:
        """Score based on diversity of source documents."""
        doc_ids = set(c.get('document_id') for c in chunks if c.get('document_id'))
        
        if not doc_ids:
            return 0.5
        
        num_docs = len(doc_ids)
        num_chunks = len(chunks)
        
        # Ideal: 2-4 documents covering the query
        if num_docs == 1:
            # Single source - risky if it's wrong
            return 0.5
        elif 2 <= num_docs <= 4:
            # Good diversity
            return 1.0
        elif 5 <= num_docs <= 6:
            # High diversity, might be scattered
            return 0.8
        else:
            # Too scattered
            return 0.6
    
    def _estimate_coverage(self, chunks: List[Dict[str, Any]], query: str) -> float:
        """Estimate how well the chunks cover the query."""
        if not chunks:
            return 0.0
        
        # Simple heuristic: more chunks = better coverage
        # But diminishing returns after 5-6 chunks
        num_chunks = len(chunks)
        
        if num_chunks >= 6:
            base_coverage = 0.9
        elif num_chunks >= 4:
            base_coverage = 0.8
        elif num_chunks >= 2:
            base_coverage = 0.6
        else:
            base_coverage = 0.4
        
        # Adjust by average score
        avg_score = sum(
            c.get('hybrid_score', c.get('rrf_score', 0.5)) for c in chunks
        ) / len(chunks)
        
        # Scale coverage by score quality
        coverage = base_coverage * (0.5 + avg_score / 2)
        
        return round(min(1.0, coverage), 3)
    
    def _generate_recommendations(
        self,
        overall_score: float,
        components: Dict[str, float],
        chunks: List[Dict[str, Any]]
    ) -> List[str]:
        """Generate actionable recommendations based on scores."""
        recommendations = []
        
        if overall_score >= self.high_confidence_threshold:
            recommendations.append("Evidence is strong - proceed with confidence")
            return recommendations
        
        if overall_score < self.min_confidence_threshold:
            recommendations.append("Evidence is insufficient - consider asking for clarification")
        
        # Component-specific recommendations
        if components.get('score_strength', 0.5) < 0.5:
            recommendations.append("Retrieval scores are weak - try rephrasing the query")
        
        if components.get('score_decay', 0.5) < 0.4:
            recommendations.append("Score drops sharply after top result - limited supporting evidence")
        
        if components.get('method_agreement', 0.5) < 0.5:
            recommendations.append("Lexical and semantic retrieval disagree - query may be ambiguous")
        
        if components.get('source_diversity', 0.5) < 0.6:
            recommendations.append("Limited source diversity - answers rely on few documents")
        
        if len(chunks) < 3:
            recommendations.append("Few chunks retrieved - coverage may be incomplete")
        
        return recommendations
    
    def get_config(self) -> Dict[str, Any]:
        """Get current configuration."""
        return {
            'high_confidence_threshold': self.high_confidence_threshold,
            'min_confidence_threshold': self.min_confidence_threshold,
            'bands': {
                'high': f'> {self.high_confidence_threshold}',
                'medium': f'0.50 - {self.high_confidence_threshold}',
                'low': f'{self.min_confidence_threshold} - 0.50',
                'insufficient': f'< {self.min_confidence_threshold}'
            }
        }

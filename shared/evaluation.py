"""Evaluation and calibration metrics for RAG system.

This module provides tools for evaluating RAG answer quality and
calibrating confidence scores against actual performance.

Key Metrics:
- Confidence Calibration: Do confidence bands predict actual quality?
- Groundedness: Is the answer supported by retrieved evidence?
- Citation Precision/Recall: Quality of citation tracking
- False Confidence Rate: Overconfidence on poor answers

Usage:
    from shared.evaluation import CalibrationAuditor, Evaluator
    
    # Run calibration audit
    auditor = CalibrationAuditor()
    results = await auditor.run_audit(
        test_queries=queries,
        rag_endpoint=rag_func
    )
    
    # Check calibration
    print(f"High confidence accuracy: {results.high_conf_accuracy}")
    print(f"False confidence rate: {results.false_confidence_rate}")
"""

import logging
import math
from typing import List, Dict, Any, Optional, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import json
from datetime import datetime

from shared.evaluation.calibration import (
    CalibrationReport as NewCalibrationReport,
    run_confidence_calibration_audit,
    get_confidence_band
)

logger = logging.getLogger(__name__)


class AnswerQuality(Enum):
    """Qualitative assessment of answer quality."""
    EXCELLENT = 5  # Fully answered, well-supported
    GOOD = 4       # Answered with minor gaps
    FAIR = 3       # Partial answer, some support
    POOR = 2       # Major gaps or weak support
    BAD = 1        # Wrong or unsupported
    UNANSWERABLE = 0  # Could not answer


@dataclass
class EvaluationResult:
    """Result of evaluating a single RAG response.
    
    Attributes:
        query: The original question
        answer: Generated answer
        confidence_score: System confidence (0-1)
        confidence_band: high/medium/low/insufficient
        retrieved_chunks: Chunks used for context
        citations: Citation report
        quality_score: Ground truth quality (0-5)
        groundedness: How well answer is supported (0-1)
        citation_precision: Relevant citations / total citations
        citation_recall: Cited relevant chunks / total relevant
        unsupported_claims: List of claims without evidence
        evaluation_time: When evaluation was run
    """
    query: str
    answer: str
    confidence_score: float
    confidence_band: str
    retrieved_chunks: List[Dict[str, Any]]
    citations: Dict[str, Any]
    quality_score: float
    groundedness: float
    citation_precision: float
    citation_recall: float
    unsupported_claims: List[str]
    evaluation_time: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            'query': self.query,
            'answer': self.answer[:500] + '...' if len(self.answer) > 500 else self.answer,
            'confidence_score': round(self.confidence_score, 3),
            'confidence_band': self.confidence_band,
            'chunks_retrieved': len(self.retrieved_chunks),
            'citations': self.citations,
            'quality_score': self.quality_score,
            'groundedness': round(self.groundedness, 3),
            'citation_precision': round(self.citation_precision, 3),
            'citation_recall': round(self.citation_recall, 3),
            'unsupported_claims': self.unsupported_claims[:5],
            'evaluation_time': self.evaluation_time
        }


@dataclass
class CalibrationReport:
    """Report on confidence calibration across multiple evaluations.
    
    Shows whether confidence bands actually predict answer quality.
    """
    total_evaluations: int
    
    # Per-band accuracy
    high_conf_count: int
    high_conf_accuracy: float  # % of high confidence that are actually good
    high_conf_groundedness: float
    
    medium_conf_count: int
    medium_conf_accuracy: float
    medium_conf_groundedness: float
    
    low_conf_count: int
    low_conf_accuracy: float
    low_conf_groundedness: float
    
    insufficient_conf_count: int
    insufficient_conf_accuracy: float
    
    # Key metrics
    false_confidence_rate: float  # High confidence but poor quality
    underconfidence_rate: float   # Low confidence but good quality
    calibration_error: float      # ECE (Expected Calibration Error)
    
    # Citation quality
    avg_citation_precision: float
    avg_citation_recall: float
    avg_supported_claim_ratio: float
    
    # Correlations
    confidence_quality_correlation: float
    confidence_groundedness_correlation: float
    
    # Raw data for further analysis
    band_distribution: Dict[str, int]
    recommendations: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            'total_evaluations': self.total_evaluations,
            'band_distribution': self.band_distribution,
            'high_confidence': {
                'count': self.high_conf_count,
                'accuracy': round(self.high_conf_accuracy, 3),
                'groundedness': round(self.high_conf_groundedness, 3)
            },
            'medium_confidence': {
                'count': self.medium_conf_count,
                'accuracy': round(self.medium_conf_accuracy, 3),
                'groundedness': round(self.medium_conf_groundedness, 3)
            },
            'low_confidence': {
                'count': self.low_conf_count,
                'accuracy': round(self.low_conf_accuracy, 3),
                'groundedness': round(self.low_conf_groundedness, 3)
            },
            'insufficient_confidence': {
                'count': self.insufficient_conf_count,
                'accuracy': round(self.insufficient_conf_accuracy, 3)
            },
            'key_metrics': {
                'false_confidence_rate': round(self.false_confidence_rate, 3),
                'underconfidence_rate': round(self.underconfidence_rate, 3),
                'calibration_error': round(self.calibration_error, 3)
            },
            'citation_quality': {
                'precision': round(self.avg_citation_precision, 3),
                'recall': round(self.avg_citation_recall, 3),
                'supported_claim_ratio': round(self.avg_supported_claim_ratio, 3)
            },
            'correlations': {
                'confidence_quality': round(self.confidence_quality_correlation, 3),
                'confidence_groundedness': round(self.confidence_groundedness_correlation, 3)
            },
            'recommendations': self.recommendations
        }


class GroundednessChecker:
    """Check if an answer is grounded in retrieved evidence.
    
    Uses multiple strategies:
    1. N-gram overlap between answer and chunks
    2. Key phrase presence in chunks
    3. Claim verification against evidence
    """
    
    def __init__(
        self,
        min_ngram_size: int = 3,
        max_ngram_size: int = 5,
        overlap_threshold: float = 0.3
    ):
        self.min_ngram_size = min_ngram_size
        self.max_ngram_size = max_ngram_size
        self.overlap_threshold = overlap_threshold
    
    def check_groundedness(
        self,
        answer: str,
        chunks: List[Dict[str, Any]]
    ) -> Tuple[float, List[str]]:
        """Calculate groundedness score and find unsupported claims.
        
        Returns:
            Tuple of (groundedness_score, unsupported_claims)
        """
        if not answer or not chunks:
            return 0.0, ["No answer or no evidence provided"]
        
        # Segment answer into claims/sentences
        claims = self._segment_into_claims(answer)
        
        if not claims:
            return 1.0, []  # Very short answer, assume grounded
        
        supported_claims = []
        unsupported_claims = []
        
        for claim in claims:
            if self._is_claim_supported(claim, chunks):
                supported_claims.append(claim)
            else:
                # Check if it's a meta-statement (e.g., "I don't know")
                if self._is_meta_statement(claim):
                    supported_claims.append(claim)
                else:
                    unsupported_claims.append(claim)
        
        groundedness = len(supported_claims) / len(claims) if claims else 0.0
        
        return groundedness, unsupported_claims
    
    def _segment_into_claims(self, answer: str) -> List[str]:
        """Split answer into individual claims/sentences."""
        import re
        
        # Split on sentence boundaries
        sentences = re.split(r'(?<=[.!?])\s+', answer)
        
        claims = []
        for sent in sentences:
            sent = sent.strip()
            # Skip very short fragments and citations
            if len(sent) > 15 and not sent.startswith('[') and not sent.startswith('Source'):
                claims.append(sent)
        
        return claims
    
    def _is_claim_supported(self, claim: str, chunks: List[Dict[str, Any]]) -> bool:
        """Check if a claim is supported by any chunk."""
        claim_lower = claim.lower()
        claim_words = set(self._extract_key_words(claim))
        
        for chunk in chunks:
            chunk_text = chunk.get('content', '').lower()
            
            # Strategy 1: Check for significant n-gram overlap
            if self._ngram_overlap(claim_lower, chunk_text) >= self.overlap_threshold:
                return True
            
            # Strategy 2: Check if key words are present
            chunk_words = set(self._extract_key_words(chunk_text))
            if claim_words and chunk_words:
                overlap = len(claim_words & chunk_words) / len(claim_words)
                if overlap >= 0.5:  # 50% of key words found
                    return True
        
        return False
    
    def _is_meta_statement(self, text: str) -> bool:
        """Check if text is a meta-statement (not a factual claim)."""
        meta_phrases = [
            "i don't have", "i don't know", "i couldn't find",
            "not enough information", "insufficient evidence",
            "unable to answer", "cannot answer", "no information",
            "context doesn't contain", "not mentioned in"
        ]
        
        text_lower = text.lower()
        return any(phrase in text_lower for phrase in meta_phrases)
    
    def _extract_key_words(self, text: str) -> List[str]:
        """Extract key words from text (excluding stopwords)."""
        import re
        
        stopwords = {
            'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
            'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
            'would', 'could', 'should', 'may', 'might', 'must', 'shall',
            'can', 'need', 'dare', 'ought', 'used', 'to', 'of', 'in',
            'for', 'on', 'with', 'at', 'by', 'from', 'as', 'into',
            'through', 'during', 'before', 'after', 'above', 'below',
            'between', 'under', 'and', 'but', 'or', 'yet', 'so',
            'if', 'because', 'although', 'though', 'while', 'where',
            'when', 'that', 'which', 'who', 'whom', 'whose', 'what',
            'this', 'these', 'those', 'i', 'you', 'he', 'she', 'it',
            'we', 'they', 'them', 'their', 'there', 'than', 'then'
        }
        
        words = re.findall(r'\b[a-zA-Z]+\b', text.lower())
        return [w for w in words if len(w) > 2 and w not in stopwords]
    
    def _ngram_overlap(self, text1: str, text2: str) -> float:
        """Calculate n-gram overlap between two texts."""
        def get_ngrams(text, n):
            words = text.split()
            return set(' '.join(words[i:i+n]) for i in range(len(words)-n+1))
        
        total_overlap = 0
        total_ngrams = 0
        
        for n in range(self.min_ngram_size, self.max_ngram_size + 1):
            ngrams1 = get_ngrams(text1, n)
            ngrams2 = get_ngrams(text2, n)
            
            if ngrams1:
                overlap = len(ngrams1 & ngrams2) / len(ngrams1)
                total_overlap += overlap
                total_ngrams += 1
        
        return total_overlap / total_ngrams if total_ngrams > 0 else 0.0


class CitationEvaluator:
    """Evaluate citation precision and recall.
    
    Precision: Of the citations used, how many are actually relevant?
    Recall: Of the relevant chunks, how many were cited?
    """
    
    def __init__(self, relevance_threshold: float = 0.3):
        self.relevance_threshold = relevance_threshold
    
    def evaluate_citations(
        self,
        answer: str,
        chunks: List[Dict[str, Any]],
        citation_report: Dict[str, Any]
    ) -> Tuple[float, float]:
        """Calculate citation precision and recall.
        
        Returns:
            Tuple of (precision, recall)
        """
        if not chunks:
            return 0.0, 0.0
        
        # Get cited chunk IDs
        citations = citation_report.get('citations', [])
        cited_chunk_ids = set(c['chunk_id'] for c in citations)
        
        # Determine which chunks are actually relevant to the answer
        relevant_chunk_ids = self._identify_relevant_chunks(answer, chunks)
        
        # Calculate precision: cited & relevant / cited
        if cited_chunk_ids:
            cited_and_relevant = cited_chunk_ids & relevant_chunk_ids
            precision = len(cited_and_relevant) / len(cited_chunk_ids)
        else:
            precision = 0.0
        
        # Calculate recall: cited & relevant / relevant
        if relevant_chunk_ids:
            recall = len(cited_and_relevant) / len(relevant_chunk_ids)
        else:
            recall = 0.0
        
        return precision, recall
    
    def _identify_relevant_chunks(
        self,
        answer: str,
        chunks: List[Dict[str, Any]]
    ) -> Set[int]:
        """Identify which chunks are actually relevant to the answer."""
        relevant = set()
        answer_words = set(answer.lower().split())
        
        for chunk in chunks:
            chunk_id = chunk.get('id')
            chunk_text = chunk.get('content', '').lower()
            chunk_words = set(chunk_text.split())
            
            # Calculate word overlap
            if answer_words and chunk_words:
                overlap = len(answer_words & chunk_words) / len(answer_words)
                if overlap >= self.relevance_threshold:
                    relevant.add(chunk_id)
        
        return relevant


class CalibrationAuditor:
    """Audit confidence calibration across multiple RAG queries.
    
    This is the main entry point for Phase 10 evaluation.
    
    Usage:
        auditor = CalibrationAuditor()
        report = await auditor.run_audit(
            test_queries=[...],
            rag_endpoint=your_rag_function
        )
        
        # Check if high confidence means high quality
        print(report.high_conf_accuracy)
        print(report.false_confidence_rate)
    """
    
    def __init__(
        self,
        quality_threshold_good: float = 3.5,  # Quality >= 3.5 is "good"
        groundedness_threshold: float = 0.7     # >= 70% grounded is good
    ):
        self.quality_threshold = quality_threshold_good
        self.groundedness_threshold = groundedness_threshold
        self.groundedness_checker = GroundednessChecker()
        self.citation_evaluator = CitationEvaluator()
    
    async def run_audit(
        self,
        test_queries: List[Dict[str, Any]],
        rag_endpoint: Callable,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> CalibrationReport:
        """Run full calibration audit on test queries.
        
        Args:
            test_queries: List of test cases with 'query' and optional 'expected_quality'
            rag_endpoint: Async function that takes a query and returns RAG response
            progress_callback: Optional callback(current, total) for progress updates
            
        Returns:
            CalibrationReport with full analysis
        """
        results = []
        total = len(test_queries)
        
        for i, test_case in enumerate(test_queries):
            try:
                result = await self._evaluate_single(
                    test_case=test_case,
                    rag_endpoint=rag_endpoint
                )
                results.append(result)
                
                if progress_callback:
                    progress_callback(i + 1, total)
                    
            except Exception as e:
                logger.error(f"Evaluation failed for query '{test_case.get('query', 'unknown')}': {e}")
                continue
        
        return self._generate_report(results)
    
    async def _evaluate_single(
        self,
        test_case: Dict[str, Any],
        rag_endpoint: Callable
    ) -> EvaluationResult:
        """Evaluate a single RAG query."""
        query = test_case['query']
        
        # Get RAG response
        rag_response = await rag_endpoint(query)
        
        answer = rag_response.get('answer', '')
        confidence = rag_response.get('confidence', {})
        confidence_score = confidence.get('score', 0.5)
        confidence_band = confidence.get('band', 'unknown')
        chunks = rag_response.get('chunks', [])
        citations = rag_response.get('citations', {})
        
        # Calculate groundedness
        groundedness, unsupported = self.groundedness_checker.check_groundedness(
            answer, chunks
        )
        
        # Calculate citation metrics
        precision, recall = self.citation_evaluator.evaluate_citations(
            answer, chunks, citations
        )
        
        # Determine quality score
        # If ground truth provided, use it; otherwise estimate from metrics
        if 'expected_quality' in test_case:
            quality = test_case['expected_quality']
        else:
            quality = self._estimate_quality(answer, groundedness, citations)
        
        return EvaluationResult(
            query=query,
            answer=answer,
            confidence_score=confidence_score,
            confidence_band=confidence_band,
            retrieved_chunks=chunks,
            citations=citations,
            quality_score=quality,
            groundedness=groundedness,
            citation_precision=precision,
            citation_recall=recall,
            unsupported_claims=unsupported
        )
    
    def _estimate_quality(
        self,
        answer: str,
        groundedness: float,
        citations: Dict[str, Any]
    ) -> float:
        """Estimate answer quality from metrics (when ground truth not provided)."""
        # Start with groundedness as base
        score = groundedness * 4  # Scale to 0-4
        
        # Bonus for good citations
        supported_ratio = citations.get('supported_claim_ratio', 0)
        score += supported_ratio
        
        # Penalty for very short or hedging answers
        if len(answer) < 50:
            score -= 1
        
        meta_phrases = ["don't know", "don't have", "not enough", "cannot answer"]
        if any(p in answer.lower() for p in meta_phrases):
            # Honest about limitations - not bad, but not excellent
            score = min(score, 2.5)
        
        return max(0, min(5, score))
    
    def _generate_report(self, results: List[EvaluationResult]) -> CalibrationReport:
        """Generate calibration report from evaluation results."""
        if not results:
            return self._empty_report()
        
        # Group by confidence band
        high_conf = [r for r in results if r.confidence_band == 'high']
        medium_conf = [r for r in results if r.confidence_band == 'medium']
        low_conf = [r for r in results if r.confidence_band == 'low']
        insufficient_conf = [r for r in results if r.confidence_band == 'insufficient']
        
        # Calculate per-band accuracy (quality >= threshold)
        high_accuracy = self._calc_accuracy(high_conf)
        medium_accuracy = self._calc_accuracy(medium_conf)
        low_accuracy = self._calc_accuracy(low_conf)
        insufficient_accuracy = self._calc_accuracy(insufficient_conf)
        
        # Calculate per-band groundedness
        high_groundedness = self._calc_avg_groundedness(high_conf)
        medium_groundedness = self._calc_avg_groundedness(medium_conf)
        low_groundedness = self._calc_avg_groundedness(low_conf)
        
        # False confidence: high confidence but poor quality
        false_confident = [r for r in high_conf if r.quality_score < self.quality_threshold]
        false_confidence_rate = len(false_confident) / len(high_conf) if high_conf else 0
        
        # Underconfidence: low confidence but good quality
        underconfident = [r for r in low_conf if r.quality_score >= self.quality_threshold]
        underconfident += [r for r in insufficient_conf if r.quality_score >= self.quality_threshold]
        underconfidence_rate = len(underconfident) / (len(low_conf) + len(insufficient_conf)) if (low_conf or insufficient_conf) else 0
        
        # Calibration error (ECE - Expected Calibration Error)
        calibration_error = self._calculate_ece(results)
        
        # Correlations
        conf_quality_corr = self._correlation(
            [r.confidence_score for r in results],
            [r.quality_score for r in results]
        )
        conf_ground_corr = self._correlation(
            [r.confidence_score for r in results],
            [r.groundedness for r in results]
        )
        
        # Citation metrics
        avg_precision = sum(r.citation_precision for r in results) / len(results)
        avg_recall = sum(r.citation_recall for r in results) / len(results)
        avg_supported = sum(
            r.citations.get('supported_claim_ratio', 0) for r in results
        ) / len(results)
        
        # Generate recommendations
        recommendations = self._generate_recommendations(
            high_conf, false_confidence_rate, calibration_error,
            conf_quality_corr, avg_supported
        )
        
        return CalibrationReport(
            total_evaluations=len(results),
            high_conf_count=len(high_conf),
            high_conf_accuracy=high_accuracy,
            high_conf_groundedness=high_groundedness,
            medium_conf_count=len(medium_conf),
            medium_conf_accuracy=medium_accuracy,
            medium_conf_groundedness=medium_groundedness,
            low_conf_count=len(low_conf),
            low_conf_accuracy=low_accuracy,
            low_conf_groundedness=low_groundedness,
            insufficient_conf_count=len(insufficient_conf),
            insufficient_conf_accuracy=insufficient_accuracy,
            false_confidence_rate=false_confidence_rate,
            underconfidence_rate=underconfidence_rate,
            calibration_error=calibration_error,
            avg_citation_precision=avg_precision,
            avg_citation_recall=avg_recall,
            avg_supported_claim_ratio=avg_supported,
            confidence_quality_correlation=conf_quality_corr,
            confidence_groundedness_correlation=conf_ground_corr,
            band_distribution={
                'high': len(high_conf),
                'medium': len(medium_conf),
                'low': len(low_conf),
                'insufficient': len(insufficient_conf)
            },
            recommendations=recommendations
        )
    
    def _calc_accuracy(self, results: List[EvaluationResult]) -> float:
        """Calculate accuracy (quality >= threshold) for a result set."""
        if not results:
            return 0.0
        good = sum(1 for r in results if r.quality_score >= self.quality_threshold)
        return good / len(results)
    
    def _calc_avg_groundedness(self, results: List[EvaluationResult]) -> float:
        """Calculate average groundedness for a result set."""
        if not results:
            return 0.0
        return sum(r.groundedness for r in results) / len(results)
    
    def _calculate_ece(self, results: List[EvaluationResult]) -> float:
        """Calculate Expected Calibration Error.
        
        ECE measures the difference between confidence and actual accuracy.
        Perfect calibration: ECE = 0
        """
        if not results:
            return 0.0
        
        # Bin by confidence score (4 bins)
        bins = [(0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.0)]
        bin_errors = []
        
        for low, high in bins:
            bin_results = [r for r in results if low <= r.confidence_score < high]
            if not bin_results:
                continue
            
            avg_confidence = sum(r.confidence_score for r in bin_results) / len(bin_results)
            accuracy = self._calc_accuracy(bin_results)
            bin_errors.append(abs(avg_confidence - accuracy) * len(bin_results))
        
        return sum(bin_errors) / len(results) if results else 0.0
    
    def _correlation(self, x: List[float], y: List[float]) -> float:
        """Calculate Pearson correlation coefficient."""
        if len(x) < 2 or len(x) != len(y):
            return 0.0
        
        n = len(x)
        sum_x = sum(x)
        sum_y = sum(y)
        sum_xy = sum(a * b for a, b in zip(x, y))
        sum_x2 = sum(a * a for a in x)
        sum_y2 = sum(b * b for b in y)
        
        numerator = n * sum_xy - sum_x * sum_y
        denominator = math.sqrt((n * sum_x2 - sum_x * sum_x) * (n * sum_y2 - sum_y * sum_y))
        
        return numerator / denominator if denominator != 0 else 0.0
    
    def _generate_recommendations(
        self,
        high_conf_results: List[EvaluationResult],
        false_conf_rate: float,
        calibration_error: float,
        conf_quality_corr: float,
        avg_supported: float
    ) -> List[str]:
        """Generate tuning recommendations based on audit results."""
        recommendations = []
        
        # False confidence issues
        if false_conf_rate > 0.2:
            recommendations.append(
                f"HIGH PRIORITY: False confidence rate is {false_conf_rate:.1%}. "
                "Consider raising confidence thresholds or improving groundedness checks."
            )
        
        # Calibration issues
        if calibration_error > 0.15:
            recommendations.append(
                f"Calibration error is {calibration_error:.2f} (target < 0.15). "
                "Confidence scores may need recalibration."
            )
        
        # Correlation issues
        if conf_quality_corr < 0.3:
            recommendations.append(
                f"Low confidence-quality correlation ({conf_quality_corr:.2f}). "
                "Confidence scoring may not reflect actual answer quality."
            )
        
        # Citation issues
        if avg_supported < 0.7:
            recommendations.append(
                f"Supported claim ratio is {avg_supported:.1%}. "
                "Consider stricter context filtering or citation validation."
            )
        
        # High confidence accuracy check
        high_conf_accuracy = self._calc_accuracy(high_conf_results)
        if high_conf_accuracy < 0.8 and high_conf_results:
            recommendations.append(
                f"High-confidence accuracy is only {high_conf_accuracy:.1%}. "
                "Review evidence scoring weights for 'score_strength' and 'source_diversity'."
            )
        
        if not recommendations:
            recommendations.append(
                "Calibration looks good. Monitor these metrics in production."
            )
        
        return recommendations
    
    def _empty_report(self) -> CalibrationReport:
        """Generate empty report when no evaluations were successful."""
        return CalibrationReport(
            total_evaluations=0,
            high_conf_count=0, high_conf_accuracy=0, high_conf_groundedness=0,
            medium_conf_count=0, medium_conf_accuracy=0, medium_conf_groundedness=0,
            low_conf_count=0, low_conf_accuracy=0, low_conf_groundedness=0,
            insufficient_conf_count=0, insufficient_conf_accuracy=0,
            false_confidence_rate=0, underconfidence_rate=0, calibration_error=0,
            avg_citation_precision=0, avg_citation_recall=0, avg_supported_claim_ratio=0,
            confidence_quality_correlation=0, confidence_groundedness_correlation=0,
            band_distribution={}, recommendations=["No evaluations completed successfully"]
        )


class Evaluator:
    """High-level evaluator for running different types of evaluations.
    
    Provides convenient methods for common evaluation scenarios.
    """
    
    def __init__(self):
        self.auditor = CalibrationAuditor()
    
    async def evaluate_single_query(
        self,
        query: str,
        rag_endpoint: Callable
    ) -> EvaluationResult:
        """Evaluate a single query."""
        return await self.auditor._evaluate_single(
            {'query': query},
            rag_endpoint
        )
    
    async def run_calibration_audit(
        self,
        test_queries: List[Dict[str, Any]],
        rag_endpoint: Callable,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> CalibrationReport:
        """Run full calibration audit."""
        return await self.auditor.run_audit(
            test_queries, rag_endpoint, progress_callback
        )
    
    def create_test_suite(self) -> List[Dict[str, Any]]:
        """Create a default test suite for evaluation.
        
        Returns a set of queries covering different difficulty levels
        and edge cases for calibration testing.
        """
        return [
            # Factual retrieval (should be high confidence)
            {
                'query': 'What is machine learning?',
                'category': 'factual',
                'difficulty': 'easy'
            },
            {
                'query': 'Explain the difference between supervised and unsupervised learning',
                'category': 'factual',
                'difficulty': 'medium'
            },
            # Ambiguous queries (may trigger lower confidence)
            {
                'query': 'How does it work?',
                'category': 'ambiguous',
                'difficulty': 'hard'
            },
            {
                'query': 'Tell me about the best approach',
                'category': 'ambiguous',
                'difficulty': 'hard'
            },
            # Sparse evidence queries
            {
                'query': 'What are the specific implementation details of the XYZ algorithm version 2.3?',
                'category': 'sparse',
                'difficulty': 'hard'
            },
            # Contradictory potential
            {
                'query': 'What are the advantages and disadvantages?',
                'category': 'multi-facet',
                'difficulty': 'medium'
            },
            # Long troubleshooting
            {
                'query': 'Why does the system fail when I configure feature X with setting Y and then run process Z?',
                'category': 'troubleshooting',
                'difficulty': 'hard'
            },
            # Exact error message
            {
                'query': 'Error: connection timeout on port 8080',
                'category': 'error',
                'difficulty': 'medium'
            },
            # Out of scope
            {
                'query': 'What is the capital of France?',
                'category': 'out_of_scope',
                'difficulty': 'easy'
            },
            # Vague
            {
                'query': 'Recent developments',
                'category': 'vague',
                'difficulty': 'hard'
            }
        ]

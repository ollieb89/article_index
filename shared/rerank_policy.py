"""Selective reranking policy: decide when reranking is likely to help.

This module implements decision triggers for conditional reranking.
Instead of always or never reranking, we analyze retrieval signals
to identify "hard" queries where reranking is most likely to improve results.

Usage:
    from shared.rerank_policy import RerankPolicy, RerankDecision
    
    policy = RerankPolicy(
        mode='selective',
        score_gap_threshold=0.03,
        disagreement_threshold=0.40
    )
    
    decision = policy.should_rerank(
        query="machine learning",
        candidates=hybrid_results
    )
    
    if decision.should_rerank:
        results = await reranker.rerank(candidates)
    
    # Debug output
    print(decision.explanation)
"""

import logging
import re
from typing import List, Dict, Any, Optional, Set
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class RerankMode(Enum):
    """Reranking operation modes."""
    OFF = "off"           # Never rerank
    ALWAYS = "always"     # Always rerank
    SELECTIVE = "selective"  # Rerank based on triggers


@dataclass
class RerankDecision:
    """Decision result from the rerank policy.
    
    Attributes:
        should_rerank: Whether reranking should be applied
        mode: The rerank mode that produced this decision
        triggers: List of trigger names that fired (if selective mode)
        trigger_details: Detailed signal values for each trigger
        explanation: Human-readable explanation of the decision
        confidence: Confidence score (0-1) that reranking will help
    """
    should_rerank: bool
    mode: str
    triggers: List[str] = field(default_factory=list)
    trigger_details: Dict[str, Any] = field(default_factory=dict)
    explanation: str = ""
    confidence: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert decision to dict for JSON serialization."""
        return {
            'should_rerank': self.should_rerank,
            'mode': self.mode,
            'triggers': self.triggers,
            'trigger_details': self.trigger_details,
            'explanation': self.explanation,
            'confidence': round(self.confidence, 3)
        }


class RerankPolicy:
    """Policy engine for selective reranking decisions.
    
    Analyzes retrieval signals to determine when reranking is likely
    to improve result quality. Uses multiple triggers that can each
    indicate a "hard" query where reranking may help.
    
    Triggers:
    - score_gap: Small gap between top-ranked and Nth-ranked items
    - ranking_disagreement: Lexical and semantic rankings disagree strongly
    - query_complexity: Long, multi-part, or comparison queries
    - low_evidence: Top chunks have weak similarity scores
    
    Attributes:
        mode: RerankMode (off, always, selective)
        score_gap_threshold: Minimum gap to avoid reranking (default 0.03)
        disagreement_threshold: Min disagreement to trigger (default 0.40)
        min_top_score: Minimum top score to avoid reranking (default 0.55)
        complex_query_words: Word count threshold for complexity (default 12)
    """
    
    # Patterns for detecting complex query types
    COMPLEX_PATTERNS = {
        'comparison': r'\b(compare|versus|vs|difference between|similarities?|better than|worse than)\b',
        'explanation': r'\b(explain|how does|why does|what causes|reason for)\b',
        'multi_part': r'\b(and|also|additionally|furthermore|moreover)\b.*\?',
        'conditional': r'\b(if|when|while|during|unless)\b',
    }
    
    def __init__(
        self,
        mode: str = 'off',
        score_gap_threshold: float = 0.03,
        disagreement_threshold: float = 0.40,
        min_top_score: float = 0.55,
        complex_query_words: int = 12
    ):
        """Initialize the rerank policy.
        
        Args:
            mode: 'off', 'always', or 'selective'
            score_gap_threshold: Score gap below which reranking triggers
            disagreement_threshold: Disagreement above which reranking triggers
            min_top_score: Top score below which reranking triggers
            complex_query_words: Word count above which query is "complex"
        """
        try:
            self.mode = RerankMode(mode.lower())
        except ValueError:
            logger.warning(f"Invalid rerank mode '{mode}', defaulting to 'off'")
            self.mode = RerankMode.OFF
        
        self.score_gap_threshold = score_gap_threshold
        self.disagreement_threshold = disagreement_threshold
        self.min_top_score = min_top_score
        self.complex_query_words = complex_query_words
        
        # Statistics tracking
        self._stats = {
            'queries_total': 0,
            'queries_reranked': 0,
            'triggers_fired': {
                'small_score_gap': 0,
                'high_rank_disagreement': 0,
                'complex_query': 0,
                'low_evidence': 0,
            },
            'trigger_combinations': {},  # Key: "trigger1+trigger2", Value: count
            'total_triggers_fired': 0,  # Sum of all trigger firings
        }
        
        logger.info(
            f"RerankPolicy initialized: mode={self.mode.value}, "
            f"score_gap={score_gap_threshold}, disagreement={disagreement_threshold}"
        )
    
    def _update_stats(self, decision: RerankDecision) -> None:
        """Update statistics based on decision.
        
        Args:
            decision: The rerank decision that was made
        """
        self._stats['queries_total'] += 1
        
        if decision.should_rerank:
            self._stats['queries_reranked'] += 1
            self._stats['total_triggers_fired'] += len(decision.triggers)
            
            # Update per-trigger counts
            for trigger in decision.triggers:
                if trigger in self._stats['triggers_fired']:
                    self._stats['triggers_fired'][trigger] += 1
            
            # Track trigger combinations
            if len(decision.triggers) > 1:
                combo_key = '+'.join(sorted(decision.triggers))
                self._stats['trigger_combinations'][combo_key] = \
                    self._stats['trigger_combinations'].get(combo_key, 0) + 1
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current statistics.
        
        Returns:
            Dict with statistics including rates and per-trigger breakdown
        """
        total = self._stats['queries_total']
        reranked = self._stats['queries_reranked']
        
        # Calculate rates
        rerank_rate = reranked / total if total > 0 else 0.0
        avg_triggers = (self._stats['total_triggers_fired'] / reranked) if reranked > 0 else 0.0
        
        # Per-trigger stats with rates
        trigger_stats = {}
        for trigger, count in self._stats['triggers_fired'].items():
            trigger_stats[trigger] = {
                'count': count,
                'rate': round(count / total, 4) if total > 0 else 0.0
            }
        
        return {
            'queries_total': total,
            'queries_reranked': reranked,
            'rerank_rate': round(rerank_rate, 4),
            'avg_triggers_per_reranked_query': round(avg_triggers, 2),
            'triggers': trigger_stats,
            'trigger_combinations': self._stats['trigger_combinations']
        }
    
    def reset_stats(self) -> None:
        """Reset all statistics to zero."""
        self._stats = {
            'queries_total': 0,
            'queries_reranked': 0,
            'triggers_fired': {
                'small_score_gap': 0,
                'high_rank_disagreement': 0,
                'complex_query': 0,
                'low_evidence': 0,
            },
            'trigger_combinations': {},
            'total_triggers_fired': 0,
        }
        logger.info("RerankPolicy statistics reset")
    
    def should_rerank(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        lexical_candidates: Optional[List[Dict[str, Any]]] = None,
        vector_candidates: Optional[List[Dict[str, Any]]] = None
    ) -> RerankDecision:
        """Evaluate whether reranking should be applied.
        
        Args:
            query: The user query string
            candidates: Hybrid retrieval results (merged and ranked)
            lexical_candidates: Optional raw lexical results for disagreement calc
            vector_candidates: Optional raw vector results for disagreement calc
            
        Returns:
            RerankDecision with should_rerank and explanation
        """
        # Mode-based early decisions
        if self.mode == RerankMode.OFF:
            return RerankDecision(
                should_rerank=False,
                mode=self.mode.value,
                explanation="Reranking disabled (mode=off)"
            )
        
        if self.mode == RerankMode.ALWAYS:
            # Still track stats for always mode
            decision = RerankDecision(
                should_rerank=True,
                mode=self.mode.value,
                triggers=['always_mode'],
                explanation="Reranking always enabled (mode=always)"
            )
            self._update_stats(decision)
            return decision
        
        # Selective mode: evaluate triggers
        decision = self._evaluate_selective(query, candidates, lexical_candidates, vector_candidates)
        self._update_stats(decision)
        return decision
    
    def _evaluate_selective(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        lexical_candidates: Optional[List[Dict[str, Any]]] = None,
        vector_candidates: Optional[List[Dict[str, Any]]] = None
    ) -> RerankDecision:
        """Evaluate selective triggers.
        
        Args:
            query: User query
            candidates: Hybrid results
            lexical_candidates: Raw lexical results
            vector_candidates: Raw vector results
            
        Returns:
            RerankDecision based on trigger evaluation
        """
        triggers = []
        details = {}
        confidence_scores = []
        
        # Trigger 1: Score gap
        score_gap_triggered, score_gap_details = self._check_score_gap(candidates)
        details['score_gap'] = score_gap_details
        if score_gap_triggered:
            triggers.append('small_score_gap')
            confidence_scores.append(score_gap_details.get('confidence', 0.5))
        
        # Trigger 2: Ranking disagreement
        disagreement_triggered, disagreement_details = self._check_ranking_disagreement(
            candidates, lexical_candidates, vector_candidates
        )
        details['ranking_disagreement'] = disagreement_details
        if disagreement_triggered:
            triggers.append('high_rank_disagreement')
            confidence_scores.append(disagreement_details.get('confidence', 0.5))
        
        # Trigger 3: Query complexity
        complexity_triggered, complexity_details = self._check_query_complexity(query)
        details['query_complexity'] = complexity_details
        if complexity_triggered:
            triggers.append('complex_query')
            confidence_scores.append(complexity_details.get('confidence', 0.5))
        
        # Trigger 4: Low evidence
        evidence_triggered, evidence_details = self._check_low_evidence(candidates)
        details['low_evidence'] = evidence_details
        if evidence_triggered:
            triggers.append('low_evidence')
            confidence_scores.append(evidence_details.get('confidence', 0.5))
        
        # Calculate overall confidence
        confidence = max(confidence_scores) if confidence_scores else 0.0
        
        # Build explanation
        if triggers:
            trigger_str = ', '.join(triggers)
            explanation = f"Reranking triggered by: {trigger_str}"
        else:
            explanation = "No reranking triggers fired; using baseline hybrid results"
        
        return RerankDecision(
            should_rerank=len(triggers) > 0,
            mode=self.mode.value,
            triggers=triggers,
            trigger_details=details,
            explanation=explanation,
            confidence=confidence
        )
    
    def _check_score_gap(
        self,
        candidates: List[Dict[str, Any]]
    ) -> tuple[bool, Dict[str, Any]]:
        """Check if score gap between top and Nth result is small.
        
        A small gap suggests uncertainty in ranking - reranking may help
        distinguish between closely-scored candidates.
        
        Args:
            candidates: Ranked list of candidates
            
        Returns:
            (triggered, details_dict)
        """
        if len(candidates) < 5:
            return False, {'reason': 'insufficient_candidates', 'count': len(candidates)}
        
        # Get hybrid scores
        scores = [c.get('hybrid_score', 0) for c in candidates[:10]]
        if not scores or max(scores) == 0:
            return False, {'reason': 'no_scores_available'}
        
        top_score = scores[0]
        fifth_score = scores[4] if len(scores) >= 5 else scores[-1]
        gap = top_score - fifth_score
        
        # Normalize gap relative to top score
        normalized_gap = gap / top_score if top_score > 0 else 0
        
        triggered = normalized_gap < self.score_gap_threshold
        
        # Confidence based on how small the gap is
        if triggered:
            confidence = min(1.0, (self.score_gap_threshold - normalized_gap) / self.score_gap_threshold + 0.3)
        else:
            confidence = 0.0
        
        return triggered, {
            'top_score': round(top_score, 4),
            'fifth_score': round(fifth_score, 4),
            'gap': round(gap, 4),
            'normalized_gap': round(normalized_gap, 4),
            'threshold': self.score_gap_threshold,
            'triggered': triggered,
            'confidence': round(confidence, 3)
        }
    
    def _check_ranking_disagreement(
        self,
        candidates: List[Dict[str, Any]],
        lexical_candidates: Optional[List[Dict[str, Any]]],
        vector_candidates: Optional[List[Dict[str, Any]]]
    ) -> tuple[bool, Dict[str, Any]]:
        """Check if lexical and vector rankings disagree significantly.
        
        Strong disagreement between retrieval methods suggests the query
        may benefit from a second-stage reranker to resolve conflicts.
        
        Args:
            candidates: Hybrid results
            lexical_candidates: Raw lexical results (optional)
            vector_candidates: Raw vector results (optional)
            
        Returns:
            (triggered, details_dict)
        """
        # Check if we have source information in candidates
        lexical_ids = set()
        vector_ids = set()
        
        for c in candidates:
            if c.get('from_lexical'):
                lexical_ids.add(c['id'])
            if c.get('from_vector'):
                vector_ids.add(c['id'])
        
        # If we have raw candidate lists, use those for deeper analysis
        if lexical_candidates and vector_candidates:
            lexical_rank = {c['id']: i for i, c in enumerate(lexical_candidates)}
            vector_rank = {c['id']: i for i, c in enumerate(vector_candidates)}
            
            # Calculate rank disagreement for overlapping items
            overlaps = set(lexical_rank.keys()) & set(vector_rank.keys())
            if len(overlaps) >= 3:
                disagreements = []
                for chunk_id in list(overlaps)[:10]:  # Sample first 10
                    lex_pos = lexical_rank[chunk_id]
                    vec_pos = vector_rank[chunk_id]
                    # Normalize to 0-1 range
                    lex_norm = lex_pos / len(lexical_candidates)
                    vec_norm = vec_pos / len(vector_candidates)
                    disagreements.append(abs(lex_norm - vec_norm))
                
                avg_disagreement = sum(disagreements) / len(disagreements)
                triggered = avg_disagreement > self.disagreement_threshold
                
                # Confidence scales with disagreement magnitude
                confidence = min(1.0, avg_disagreement / self.disagreement_threshold) if triggered else 0.0
                
                return triggered, {
                    'overlap_count': len(overlaps),
                    'sampled_count': len(disagreements),
                    'avg_disagreement': round(avg_disagreement, 4),
                    'threshold': self.disagreement_threshold,
                    'triggered': triggered,
                    'confidence': round(confidence, 3)
                }
        
        # Fallback: use provenance info from merged candidates
        total = len(candidates)
        only_lexical = sum(1 for c in candidates if c.get('from_lexical') and not c.get('from_vector'))
        only_vector = sum(1 for c in candidates if c.get('from_vector') and not c.get('from_lexical'))
        both = sum(1 for c in candidates if c.get('from_lexical') and c.get('from_vector'))
        
        # High divergence = many results exclusive to one method
        if total > 0:
            divergence = (only_lexical + only_vector) / total
            triggered = divergence > self.disagreement_threshold
            
            confidence = min(1.0, divergence / self.disagreement_threshold) if triggered else 0.0
            
            return triggered, {
                'only_lexical': only_lexical,
                'only_vector': only_vector,
                'both': both,
                'divergence_ratio': round(divergence, 4),
                'threshold': self.disagreement_threshold,
                'triggered': triggered,
                'confidence': round(confidence, 3),
                'method': 'provenance_fallback'
            }
        
        return False, {'reason': 'insufficient_data'}
    
    def _check_query_complexity(self, query: str) -> tuple[bool, Dict[str, Any]]:
        """Check if query is complex (long, multi-part, comparison, etc.).
        
        Complex queries often benefit from reranking because they have
        multiple intents or require synthesis across concepts.
        
        Args:
            query: User query string
            
        Returns:
            (triggered, details_dict)
        """
        words = query.split()
        word_count = len(words)
        
        # Check for complex query patterns
        patterns_found = {}
        for name, pattern in self.COMPLEX_PATTERNS.items():
            matches = re.findall(pattern, query, re.IGNORECASE)
            if matches:
                patterns_found[name] = len(matches)
        
        # Determine if complex
        is_long = word_count >= self.complex_query_words
        has_patterns = len(patterns_found) > 0
        
        triggered = is_long or has_patterns
        
        # Confidence based on complexity indicators
        confidence = 0.0
        if triggered:
            confidence = 0.4
            if is_long:
                confidence += min(0.3, (word_count - self.complex_query_words) / 50)
            if has_patterns:
                confidence += min(0.3, len(patterns_found) * 0.15)
            confidence = min(1.0, confidence)
        
        return triggered, {
            'word_count': word_count,
            'threshold': self.complex_query_words,
            'is_long': is_long,
            'patterns_found': patterns_found,
            'pattern_count': len(patterns_found),
            'triggered': triggered,
            'confidence': round(confidence, 3)
        }
    
    def _check_low_evidence(
        self,
        candidates: List[Dict[str, Any]]
    ) -> tuple[bool, Dict[str, Any]]:
        """Check if top candidates have weak similarity scores.
        
        Low scores suggest the retrieval is uncertain about relevance,
        making reranking more likely to help.
        
        Args:
            candidates: Ranked candidates
            
        Returns:
            (triggered, details_dict)
        """
        if not candidates:
            return False, {'reason': 'no_candidates'}
        
        top_candidate = candidates[0]
        top_score = top_candidate.get('hybrid_score', 0)
        
        # Also check semantic score if available
        semantic_score = top_candidate.get('semantic_score', 0)
        lexical_score = top_candidate.get('lexical_score', 0)
        
        # Use the best available score
        best_score = max(top_score, semantic_score, lexical_score)
        
        triggered = best_score < self.min_top_score
        
        # Confidence based on how low the score is
        confidence = 0.0
        if triggered:
            confidence = min(1.0, (self.min_top_score - best_score) / self.min_top_score + 0.3)
        
        return triggered, {
            'top_hybrid_score': round(top_score, 4),
            'top_semantic_score': round(semantic_score, 4) if semantic_score else None,
            'top_lexical_score': round(lexical_score, 4) if lexical_score else None,
            'best_score': round(best_score, 4),
            'threshold': self.min_top_score,
            'triggered': triggered,
            'confidence': round(confidence, 3)
        }
    
    def get_config(self) -> Dict[str, Any]:
        """Get current policy configuration.
        
        Returns:
            Dict with all policy settings
        """
        return {
            'mode': self.mode.value,
            'score_gap_threshold': self.score_gap_threshold,
            'disagreement_threshold': self.disagreement_threshold,
            'min_top_score': self.min_top_score,
            'complex_query_words': self.complex_query_words,
            'triggers': {
                'score_gap': f'Gap < {self.score_gap_threshold}',
                'ranking_disagreement': f'Disagreement > {self.disagreement_threshold}',
                'query_complexity': f'Words >= {self.complex_query_words} or patterns',
                'low_evidence': f'Top score < {self.min_top_score}'
            }
        }

"""Query transformation for improved retrieval recall.

This module transforms user queries into multiple retrieval queries
to improve recall, especially for ambiguous or complex questions.

Transformation strategies:
- Multi-query: Generate 2-3 alternate phrasings of the question
- Step-back: Create a broader conceptual version of the question

Usage:
    from shared.query_transformer import QueryTransformer, TransformDecision
    
    transformer = QueryTransformer(
        mode='selective',
        max_expanded_queries=3
    )
    
    decision = transformer.transform("Why does pgvector timeout on large imports?")
    
    # decision.transformed_queries contains original + generated queries
    for query in decision.transformed_queries:
        results = await retriever.retrieve(query)
        # Merge results...
    
    # decision.transform_types shows which transformations applied
"""

import logging
import re
from typing import List, Dict, Any, Optional, Set
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class TransformMode(Enum):
    """Query transformation operation modes."""
    OFF = "off"           # No transformation
    ALWAYS = "always"     # Always transform
    SELECTIVE = "selective"  # Transform based on triggers


@dataclass
class TransformDecision:
    """Decision result from query transformation.
    
    Attributes:
        should_transform: Whether transformation was applied
        mode: The transform mode that produced this decision
        original_query: The user's original query
        transformed_queries: List of queries to use for retrieval
        transform_types: List of transformation types applied
        trigger_reasons: Why transformation was triggered (if selective)
        confidence: Confidence that transformation will help
    """
    should_transform: bool
    mode: str
    original_query: str
    transformed_queries: List[str] = field(default_factory=list)
    transform_types: List[str] = field(default_factory=list)
    trigger_reasons: List[str] = field(default_factory=list)
    confidence: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert decision to dict for JSON serialization."""
        return {
            'should_transform': self.should_transform,
            'mode': self.mode,
            'original_query': self.original_query,
            'transformed_queries': self.transformed_queries,
            'transform_types': self.transform_types,
            'trigger_reasons': self.trigger_reasons,
            'confidence': round(self.confidence, 3),
            'query_count': len(self.transformed_queries)
        }


class QueryTransformer:
    """Transform queries to improve retrieval recall.
    
    Implements multiple transformation strategies:
    - Multi-query expansion: Generate alternate phrasings
    - Step-back reformulation: Create broader conceptual versions
    
    Uses selective mode to apply transformations only when
    they are likely to help (ambiguous, complex, or low-evidence queries).
    
    Attributes:
        mode: TransformMode (off, always, selective)
        max_expanded_queries: Maximum number of queries to generate (default 3)
        enable_multi_query: Enable multi-query expansion
        enable_step_back: Enable step-back reformulation
    """
    
    # Patterns indicating complex/ambiguous queries
    AMBIGUITY_PATTERNS = {
        'vague_terms': r'\b(something|anything|stuff|things?)\b',
        'question_words': r'\b(why|how|what causes|reason for)\b',
        'comparison': r'\b(compare|versus|vs|difference|similarities?|better|worse)\b',
        'multi_aspect': r'\b(and|also|plus|additionally|furthermore)\b.*\b(how|what|why)\b',
    }
    
    # Domain-specific expansion templates
    EXPANSION_TEMPLATES = {
        'technical_issue': [
            "{topic} problem",
            "{topic} error",
            "{topic} troubleshooting",
        ],
        'concept_explanation': [
            "what is {topic}",
            "{topic} explained",
            "{topic} overview",
        ],
        'how_to': [
            "how to {action}",
            "{action} guide",
            "{action} tutorial",
        ],
    }
    
    def __init__(
        self,
        mode: str = 'off',
        max_expanded_queries: int = 3,
        enable_multi_query: bool = True,
        enable_step_back: bool = True,
        min_query_words: int = 4,
        ambiguity_threshold: int = 1
    ):
        """Initialize the query transformer.
        
        Args:
            mode: 'off', 'always', or 'selective'
            max_expanded_queries: Maximum queries to generate (2-4 recommended)
            enable_multi_query: Enable multi-query expansion
            enable_step_back: Enable step-back reformulation
            min_query_words: Minimum words before considering transformation
            ambiguity_threshold: Number of ambiguity indicators to trigger
        """
        try:
            self.mode = TransformMode(mode.lower())
        except ValueError:
            logger.warning(f"Invalid transform mode '{mode}', defaulting to 'off'")
            self.mode = TransformMode.OFF
        
        self.max_expanded_queries = max(2, min(max_expanded_queries, 5))
        self.enable_multi_query = enable_multi_query
        self.enable_step_back = enable_step_back
        self.min_query_words = min_query_words
        self.ambiguity_threshold = ambiguity_threshold
        
        # Statistics tracking
        self._stats = {
            'queries_total': 0,
            'queries_transformed': 0,
            'transform_types': {
                'multi_query': 0,
                'step_back': 0,
            },
            'generated_queries_count': 0,
        }
        
        logger.info(
            f"QueryTransformer initialized: mode={self.mode.value}, "
            f"max_queries={self.max_expanded_queries}"
        )
    
    def _update_stats(self, decision: TransformDecision) -> None:
        """Update statistics based on decision."""
        self._stats['queries_total'] += 1
        
        if decision.should_transform:
            self._stats['queries_transformed'] += 1
            self._stats['generated_queries_count'] += len(decision.transformed_queries) - 1
            
            for ttype in decision.transform_types:
                if ttype in self._stats['transform_types']:
                    self._stats['transform_types'][ttype] += 1
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current statistics."""
        total = self._stats['queries_total']
        transformed = self._stats['queries_transformed']
        
        return {
            'queries_total': total,
            'queries_transformed': transformed,
            'transform_rate': round(transformed / total, 4) if total > 0 else 0.0,
            'avg_generated_per_transform': (
                (self._stats['generated_queries_count'] / transformed) 
                if transformed > 0 else 0.0
            ),
            'transform_types': {
                k: {'count': v, 'rate': round(v / total, 4) if total > 0 else 0.0}
                for k, v in self._stats['transform_types'].items()
            }
        }
    
    def reset_stats(self) -> None:
        """Reset all statistics."""
        self._stats = {
            'queries_total': 0,
            'queries_transformed': 0,
            'transform_types': {
                'multi_query': 0,
                'step_back': 0,
            },
            'generated_queries_count': 0,
        }
        logger.info("QueryTransformer statistics reset")
    
    def transform(
        self,
        query: str,
        candidates: Optional[List[Dict[str, Any]]] = None
    ) -> TransformDecision:
        """Transform query for improved retrieval.
        
        Args:
            query: Original user query
            candidates: Optional retrieval results to check for low evidence
            
        Returns:
            TransformDecision with transformed queries
        """
        # Mode-based early decisions
        if self.mode == TransformMode.OFF:
            return TransformDecision(
                should_transform=False,
                mode=self.mode.value,
                original_query=query,
                transformed_queries=[query]
            )
        
        if self.mode == TransformMode.ALWAYS:
            transformed = self._generate_transforms(query)
            decision = TransformDecision(
                should_transform=True,
                mode=self.mode.value,
                original_query=query,
                transformed_queries=transformed,
                transform_types=['multi_query', 'step_back'] if self.enable_multi_query and self.enable_step_back else ['multi_query'],
                trigger_reasons=['always_mode'],
                confidence=0.5
            )
            self._update_stats(decision)
            return decision
        
        # Selective mode
        return self._transform_selective(query, candidates)
    
    def _transform_selective(
        self,
        query: str,
        candidates: Optional[List[Dict[str, Any]]]
    ) -> TransformDecision:
        """Apply selective transformation based on query characteristics."""
        reasons = []
        
        # Check 1: Query is too short
        word_count = len(query.split())
        if word_count < self.min_query_words:
            decision = TransformDecision(
                should_transform=False,
                mode=self.mode.value,
                original_query=query,
                transformed_queries=[query],
                trigger_reasons=['query_too_short']
            )
            self._update_stats(decision)
            return decision
        
        # Check 2: Ambiguous query patterns
        ambiguity_score = 0
        for pattern_name, pattern in self.AMBIGUITY_PATTERNS.items():
            if re.search(pattern, query, re.IGNORECASE):
                ambiguity_score += 1
        
        if ambiguity_score >= self.ambiguity_threshold:
            reasons.append('ambiguous_query')
        
        # Check 3: Low evidence from retrieval (if candidates provided)
        if candidates is not None and len(candidates) > 0:
            top_score = candidates[0].get('hybrid_score', 0) or candidates[0].get('semantic_score', 0)
            if top_score < 0.55:  # Weak top result
                reasons.append('low_evidence')
        
        # Check 4: Complex multi-part query
        if word_count >= 12 or ' and ' in query.lower().split('?')[0]:
            reasons.append('complex_query')
        
        # Decide whether to transform
        if not reasons:
            decision = TransformDecision(
                should_transform=False,
                mode=self.mode.value,
                original_query=query,
                transformed_queries=[query],
                trigger_reasons=['query_clear']
            )
            self._update_stats(decision)
            return decision
        
        # Generate transformations
        transformed = self._generate_transforms(query)
        
        # Limit to max_expanded_queries
        if len(transformed) > self.max_expanded_queries:
            transformed = transformed[:self.max_expanded_queries]
        
        # Determine transform types used
        transform_types = []
        if self.enable_multi_query:
            transform_types.append('multi_query')
        if self.enable_step_back:
            transform_types.append('step_back')
        
        decision = TransformDecision(
            should_transform=True,
            mode=self.mode.value,
            original_query=query,
            transformed_queries=transformed,
            transform_types=transform_types,
            trigger_reasons=reasons,
            confidence=min(0.9, 0.5 + len(reasons) * 0.15)
        )
        self._update_stats(decision)
        return decision
    
    def _normalize_query(self, query: str) -> str:
        """Normalize query for deduplication.
        
        Converts to lowercase, removes punctuation, sorts tokens.
        """
        # Lowercase and remove punctuation
        normalized = re.sub(r'[^\w\s]', '', query.lower())
        # Split into tokens, sort, rejoin
        tokens = sorted(normalized.split())
        return ' '.join(tokens)
    
    def _generate_transforms(self, query: str) -> List[str]:
        """Generate transformed versions of the query.
        
        Returns list including original + generated queries.
        Applies query-level deduplication to avoid near-duplicate retrievals.
        """
        queries = [query]  # Always include original
        seen_normalized = {self._normalize_query(query)}
        
        # Extract key concepts
        concepts = self._extract_concepts(query)
        
        # Multi-query expansion
        if self.enable_multi_query:
            expanded = self._generate_multi_queries(query, concepts)
            for q in expanded:
                norm = self._normalize_query(q)
                if norm not in seen_normalized:
                    seen_normalized.add(norm)
                    queries.append(q)
                else:
                    logger.debug(f"Dropping near-duplicate generated query: {q}")
        
        # Step-back reformulation
        if self.enable_step_back and len(queries) < self.max_expanded_queries:
            step_back = self._generate_step_back(query, concepts)
            if step_back:
                norm = self._normalize_query(step_back)
                if norm not in seen_normalized:
                    seen_normalized.add(norm)
                    queries.append(step_back)
                else:
                    logger.debug(f"Dropping duplicate step-back query: {step_back}")
        
        return queries[:self.max_expanded_queries]
    
    def _extract_concepts(self, query: str) -> List[str]:
        """Extract key concepts from query.
        
        Simple extraction of noun phrases and technical terms.
        """
        # Remove common stop words
        stop_words = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 
                      'be', 'been', 'being', 'have', 'has', 'had',
                      'do', 'does', 'did', 'will', 'would', 'could',
                      'should', 'may', 'might', 'must', 'shall',
                      'can', 'need', 'dare', 'ought', 'used'}
        
        words = query.lower().split()
        concepts = []
        
        for word in words:
            # Clean punctuation
            clean = re.sub(r'[^\w\s]', '', word)
            if clean and clean not in stop_words and len(clean) > 2:
                concepts.append(clean)
        
        # Look for technical terms (camelCase, dotted, acronyms)
        technical = re.findall(r'\b[A-Z][a-z]+[A-Z]\w*\b|\b\w+\.\w+\b|\b[A-Z]{2,}\b', query)
        concepts.extend([t.lower() for t in technical])
        
        return list(dict.fromkeys(concepts))  # Preserve order, remove duplicates
    
    def _generate_multi_queries(self, query: str, concepts: List[str]) -> List[str]:
        """Generate alternate phrasings of the query."""
        generated = []
        
        # Strategy 1: Keyword-focused version
        if concepts:
            keyword_query = ' '.join(concepts[:5])
            if keyword_query != query.lower():
                generated.append(keyword_query)
        
        # Strategy 2: Question to statement
        if query.lower().startswith(('what is', 'what are', 'explain')):
            # Convert to statement form
            statement = re.sub(r'^(what is|what are|explain)\s+', '', query, flags=re.IGNORECASE)
            if statement:
                generated.append(f"{statement} overview")
                generated.append(f"{statement} guide")
        
        # Strategy 3: Issue/problem angle for troubleshooting queries
        if any(word in query.lower() for word in ['error', 'fail', 'timeout', 'slow', 'bug', 'issue']):
            for concept in concepts[:2]:
                generated.append(f"{concept} troubleshooting")
                generated.append(f"{concept} fix")
        
        # Strategy 4: How-to angle for action queries
        if any(word in query.lower() for word in ['how to', 'how do', 'configure', 'setup', 'install']):
            for concept in concepts[:2]:
                generated.append(f"{concept} tutorial")
                generated.append(f"{concept} example")
        
        return list(dict.fromkeys(generated))  # Remove duplicates
    
    def _generate_step_back(self, query: str, concepts: List[str]) -> Optional[str]:
        """Generate a broader, more conceptual version of the query."""
        
        # Remove specific constraints to get broader concept
        # Example: "Why does pgvector timeout on large imports?" -> "pgvector performance issues"
        
        if len(concepts) >= 2:
            # Take first two concepts and make broader
            broader = f"{concepts[0]} {concepts[1]}"
            
            # Add appropriate suffix based on query type
            if any(w in query.lower() for w in ['error', 'fail', 'timeout', 'bug']):
                return f"{broader} issues"
            elif any(w in query.lower() for w in ['how to', 'configure', 'setup']):
                return f"{broader} configuration"
            elif any(w in query.lower() for w in ['compare', 'vs', 'difference']):
                return f"{broader} comparison"
            else:
                return f"{broader} overview"
        
        return None
    
    def merge_results(
        self,
        results_per_query: List[List[Dict[str, Any]]],
        original_query: str,
        max_results: int = 10,
        rrf_k: int = 60
    ) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Merge and deduplicate results from multiple query retrievals.
        
        Uses Reciprocal Rank Fusion (RRF) for score aggregation:
        score = Σ 1/(k + rank) for each query that retrieved the chunk
        
        Args:
            results_per_query: List of result lists, one per transformed query
            original_query: The original user query (for logging)
            max_results: Maximum results to return
            rrf_k: RRF constant (default 60)
            
        Returns:
            Tuple of (merged_results, merge_metadata)
        """
        metadata = {
            'queries_used': len(results_per_query),
            'total_candidates': sum(len(r) for r in results_per_query),
            'rrf_k': rrf_k
        }
        
        if not results_per_query:
            metadata['unique_chunks'] = 0
            metadata['result_overlap'] = 0.0
            return [], metadata
        
        if len(results_per_query) == 1:
            metadata['unique_chunks'] = len(results_per_query[0])
            metadata['result_overlap'] = 1.0
            return results_per_query[0][:max_results], metadata
        
        # Track chunks and their RRF scores
        chunk_scores = {}  # chunk_id -> {'chunk': chunk_data, 'rrf_score': score, 'ranks': []}
        
        # Calculate RRF scores
        for query_idx, query_results in enumerate(results_per_query):
            for rank, chunk in enumerate(query_results):
                chunk_id = chunk.get('id')
                if not chunk_id:
                    continue
                
                # RRF score contribution: 1/(k + rank)
                rrf_contribution = 1.0 / (rrf_k + rank)
                
                if chunk_id not in chunk_scores:
                    chunk_scores[chunk_id] = {
                        'chunk': chunk.copy(),
                        'rrf_score': 0.0,
                        'ranks': [],
                        'query_indices': []
                    }
                
                chunk_scores[chunk_id]['rrf_score'] += rrf_contribution
                chunk_scores[chunk_id]['ranks'].append(rank)
                chunk_scores[chunk_id]['query_indices'].append(query_idx)
        
        # Add RRF metadata to chunks and prepare for sorting
        merged = []
        for chunk_id, data in chunk_scores.items():
            chunk = data['chunk']
            chunk['rrf_score'] = round(data['rrf_score'], 6)
            chunk['rrf_ranks'] = data['ranks']
            chunk['from_query_count'] = len(data['query_indices'])
            chunk['rrf_k'] = rrf_k
            
            # Also set hybrid_score for compatibility with downstream ranking
            chunk['hybrid_score'] = data['rrf_score']
            chunk['transform_boost'] = 1.0 + (chunk['from_query_count'] - 1) * 0.1
            
            merged.append(chunk)
        
        # Sort by RRF score descending
        merged.sort(key=lambda x: x.get('rrf_score', 0), reverse=True)
        
        # Calculate cross-query overlap metric
        if len(results_per_query) > 1:
            # Count chunks found by multiple queries
            multi_query_chunks = sum(1 for m in merged if m['from_query_count'] > 1)
            metadata['result_overlap'] = round(multi_query_chunks / len(merged), 4) if merged else 0.0
        else:
            metadata['result_overlap'] = 1.0
        
        metadata['unique_chunks'] = len(merged)
        metadata['multi_query_chunks'] = sum(1 for m in merged if m['from_query_count'] > 1)
        
        logger.info(
            f"RRF merged {metadata['total_candidates']} results from "
            f"{metadata['queries_used']} queries into {metadata['unique_chunks']} unique chunks "
            f"(overlap: {metadata['result_overlap']:.1%}) "
            f"for '{original_query[:50]}...'"
        )
        
        # Log generated query analytics
        self._log_merge_analytics(original_query, results_per_query, metadata)
        
        return merged[:max_results], metadata
    
    def _log_merge_analytics(
        self,
        original_query: str,
        results_per_query: List[List[Dict[str, Any]]],
        metadata: Dict[str, Any]
    ) -> None:
        """Log detailed analytics about the merge for debugging and optimization.
        
        Args:
            original_query: The original user query
            results_per_query: Results from each transformed query
            metadata: Merge metadata
        """
        if not results_per_query or len(results_per_query) < 2:
            return
        
        # Log per-query result counts
        query_counts = [len(r) for r in results_per_query]
        
        # Calculate pairwise overlaps
        pairwise_overlaps = []
        for i in range(len(results_per_query)):
            for j in range(i + 1, len(results_per_query)):
                ids_i = set(c.get('id') for c in results_per_query[i] if c.get('id'))
                ids_j = set(c.get('id') for c in results_per_query[j] if c.get('id'))
                if ids_i and ids_j:
                    overlap = len(ids_i & ids_j) / len(ids_i | ids_j)
                    pairwise_overlaps.append(round(overlap, 3))
        
        avg_pairwise_overlap = sum(pairwise_overlaps) / len(pairwise_overlaps) if pairwise_overlaps else 0
        
        logger.debug(
            f"Query transform analytics for '{original_query[:50]}...': "
            f"query_results={query_counts}, "
            f"unique={metadata['unique_chunks']}, "
            f"multi_query={metadata.get('multi_query_chunks', 0)}, "
            f"avg_pairwise_overlap={avg_pairwise_overlap:.2%}"
        )
    
    def get_config(self) -> Dict[str, Any]:
        """Get current configuration."""
        return {
            'mode': self.mode.value,
            'max_expanded_queries': self.max_expanded_queries,
            'enable_multi_query': self.enable_multi_query,
            'enable_step_back': self.enable_step_back,
            'min_query_words': self.min_query_words,
            'ambiguity_threshold': self.ambiguity_threshold,
            'transform_types': {
                'multi_query': 'Generate 2-3 alternate phrasings',
                'step_back': 'Create broader conceptual version'
            }
        }

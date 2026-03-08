"""Lightweight reranker for improving hybrid search results.

This module implements a reranking layer that operates on the top-N candidates
from hybrid retrieval to improve result quality.

Reranking approach:
1. Retrieve top N candidates using hybrid search (lexical + vector)
2. Score each candidate with a cross-encoder or similarity model
3. Reorder by reranker score
4. Return top k results

Supports three modes:
- off: Pass through to hybrid retriever (no reranking)
- always: Always rerank all queries
- selective: Use RerankPolicy to decide which queries need reranking

Usage:
    from shared.reranker import Reranker
    from shared.rerank_policy import RerankPolicy
    
    policy = RerankPolicy(mode='selective')
    
    reranker = Reranker(
        hybrid_retriever=hybrid_retriever,
        policy=policy,
        top_n=30,
        final_k=10
    )
    
    # Returns tuple of (results, decision)
    results, decision = await reranker.rerank_with_decision(
        query="machine learning",
        query_embedding=embedding
    )
    
    print(decision.explanation)  # Why reranking was/wasn't applied
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
import asyncio

from shared.ollama_client import OllamaClient
from shared.hybrid_retriever import HybridRetriever
from shared.database import DocumentRepository
from shared.rerank_policy import RerankPolicy, RerankDecision, RerankMode

logger = logging.getLogger(__name__)


class Reranker:
    """Lightweight reranker for hybrid search results.
    
    Implements a two-stage retrieval pipeline:
    1. Retrieve top-N candidates using hybrid search
    2. (Optional) Apply selective policy to decide if reranking is needed
    3. Rerank candidates using a cross-encoder or similarity model
    4. Return top-k results
    
    The reranker is designed to be:
    - Optional: Can be enabled/disabled via config
    - Selective: Can use policy to rerank only "hard" queries
    - Lightweight: Operates on small candidate sets (top 20-50)
    - Configurable: Supports different reranking strategies
    
    Attributes:
        enabled: Whether reranking is enabled (deprecated, use policy.mode)
        policy: RerankPolicy for selective reranking decisions
        top_n: Number of candidates to retrieve for reranking
        final_k: Number of results to return after reranking
        model: Reranking model/strategy to use
        use_cross_encoder: Whether to use cross-encoder (vs embedding similarity)
    """
    
    def __init__(
        self,
        hybrid_retriever: HybridRetriever,
        enabled: bool = False,
        policy: Optional[RerankPolicy] = None,
        top_n: int = 30,
        final_k: int = 10,
        model: str = "cross-encoder",
        use_cross_encoder: bool = True
    ):
        """Initialize the reranker.
        
        Args:
            hybrid_retriever: HybridRetriever instance for initial retrieval
            enabled: Whether reranking is enabled (legacy, use policy)
            policy: RerankPolicy for selective reranking (if None, uses legacy enabled flag)
            top_n: Number of candidates to retrieve for reranking (default 30)
            final_k: Number of results to return (default 10)
            model: Reranking model name (for logging/compatibility)
            use_cross_encoder: Use cross-encoder (True) or embedding similarity (False)
        """
        self.hybrid_retriever = hybrid_retriever
        self.top_n = top_n
        self.final_k = final_k
        self.model = model
        self.use_cross_encoder = use_cross_encoder
        self.ollama = OllamaClient()
        
        # Set up policy: use provided policy or create from legacy enabled flag
        if policy is not None:
            self.policy = policy
        else:
            # Legacy: convert enabled flag to policy
            mode = 'always' if enabled else 'off'
            self.policy = RerankPolicy(mode=mode)
        
        # Backward compatibility: expose enabled property
        self.enabled = self.policy.mode != RerankMode.OFF
        
        logger.info(
            f"Reranker initialized: mode={self.policy.mode.value}, top_n={top_n}, "
            f"final_k={final_k}, model={model}"
        )
    
    async def rerank(
        self,
        query: str,
        query_embedding: Optional[List[float]] = None,
        candidates: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """Rerank candidates and return top-k results.
        
        Legacy method - does not return decision info. Use rerank_with_decision
        for new code that needs visibility into reranking decisions.
        
        If candidates are not provided, retrieves them using hybrid search.
        
        Args:
            query: User query string
            query_embedding: Pre-computed query embedding (optional)
            candidates: Pre-retrieved candidates (optional)
            
        Returns:
            List of reranked chunk dicts with rerank_score
        """
        results, _ = await self.rerank_with_decision(
            query=query,
            query_embedding=query_embedding,
            candidates=candidates
        )
        return results
    
    async def rerank_with_decision(
        self,
        query: str,
        query_embedding: Optional[List[float]] = None,
        candidates: Optional[List[Dict[str, Any]]] = None,
        lexical_candidates: Optional[List[Dict[str, Any]]] = None,
        vector_candidates: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[List[Dict[str, Any]], RerankDecision]:
        """Rerank candidates and return results with decision metadata.
        
        This is the primary method for selective reranking. It:
        1. Retrieves candidates if not provided
        2. Uses policy to decide if reranking should apply
        3. Optionally reranks based on decision
        4. Returns results with decision explanation
        
        Args:
            query: User query string
            query_embedding: Pre-computed query embedding (optional)
            candidates: Pre-retrieved candidates (optional)
            lexical_candidates: Raw lexical results for disagreement calc (optional)
            vector_candidates: Raw vector results for disagreement calc (optional)
            
        Returns:
            Tuple of (results, decision)
        """
        # Stage 1: Retrieve top-N candidates if not provided
        if candidates is None:
            candidates = await self.hybrid_retriever.retrieve(
                query=query,
                query_embedding=query_embedding,
                k=self.top_n
            )
        
        if not candidates:
            decision = RerankDecision(
                should_rerank=False,
                mode=self.policy.mode.value,
                explanation="No candidates retrieved"
            )
            return [], decision
        
        if len(candidates) <= self.final_k:
            # Not enough candidates to benefit from reranking
            decision = RerankDecision(
                should_rerank=False,
                mode=self.policy.mode.value,
                explanation="Insufficient candidates for reranking"
            )
            return candidates[:self.final_k], decision
        
        # Stage 2: Use policy to decide if reranking should apply
        decision = self.policy.should_rerank(
            query=query,
            candidates=candidates,
            lexical_candidates=lexical_candidates,
            vector_candidates=vector_candidates
        )
        
        if not decision.should_rerank:
            # Return baseline results with decision metadata
            logger.debug(f"Skipping rerank: {decision.explanation}")
            # Add decision info to first candidate for visibility
            if candidates:
                candidates[0]['rerank_decision'] = decision.to_dict()
            return candidates[:self.final_k], decision
        
        # Stage 3: Apply reranking
        logger.info(
            f"Reranking triggered for '{query[:50]}...': {decision.triggers}"
        )
        
        scored_candidates = await self._score_candidates(query, candidates)
        
        # Stage 4: Sort by rerank score and return top-k
        scored_candidates.sort(key=lambda x: x.get('rerank_score', 0), reverse=True)
        results = scored_candidates[:self.final_k]
        
        # Add decision info to results
        if results:
            results[0]['rerank_decision'] = decision.to_dict()
        
        return results, decision
    
    async def _score_candidates(
        self,
        query: str,
        candidates: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Score candidates using the selected reranking method.
        
        Args:
            query: User query string
            candidates: List of candidate chunks
            
        Returns:
            Candidates with rerank_score added
        """
        if self.use_cross_encoder:
            return await self._score_with_cross_encoder(query, candidates)
        else:
            return await self._score_with_embedding(query, candidates)
    
    async def _score_with_cross_encoder(
        self,
        query: str,
        candidates: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Score candidates using a cross-encoder approach via Ollama.
        
        Uses a lightweight prompt to score query-chunk relevance.
        Falls back to embedding similarity if cross-encoder fails.
        
        Args:
            query: User query string
            candidates: List of candidate chunks
            
        Returns:
            Candidates with rerank_score added
        """
        scored = []
        
        # Process in small batches to avoid overwhelming the API
        batch_size = 5
        for i in range(0, len(candidates), batch_size):
            batch = candidates[i:i + batch_size]
            
            # Score batch concurrently
            tasks = [
                self._score_single_cross_encoder(query, candidate)
                for candidate in batch
            ]
            scores = await asyncio.gather(*tasks, return_exceptions=True)
            
            for candidate, score in zip(batch, scores):
                if isinstance(score, Exception):
                    logger.warning(f"Cross-encoder scoring failed: {score}")
                    # Fall back to hybrid score
                    score = candidate.get('hybrid_score', 0.5)
                
                candidate['rerank_score'] = round(score, 4)
                candidate['rerank_method'] = 'cross_encoder'
                scored.append(candidate)
        
        return scored
    
    async def _score_single_cross_encoder(
        self,
        query: str,
        candidate: Dict[str, Any]
    ) -> float:
        """Score a single candidate using cross-encoder.
        
        Args:
            query: User query string
            candidate: Candidate chunk dict
            
        Returns:
            Relevance score between 0 and 1
        """
        content = candidate.get('content', '')[:500]  # Truncate long chunks
        
        prompt = f"""Rate the relevance of the following text to the query.

Query: {query}

Text: {content}

Rate relevance from 0 (completely irrelevant) to 1 (highly relevant).
Respond with only a number between 0 and 1."""
        
        try:
            response = await self.ollama.generate_response(
                prompt=prompt,
                context="",
                model=None  # Use default model
            )
            
            # Extract number from response
            import re
            numbers = re.findall(r'0?\.\d+', response.strip())
            if numbers:
                score = float(numbers[0])
                return max(0.0, min(1.0, score))  # Clamp to [0, 1]
            else:
                # Fallback: check for keywords
                if 'relevant' in response.lower() and 'not' not in response.lower():
                    return 0.7
                return 0.5
                
        except Exception as e:
            logger.warning(f"Cross-encoder scoring failed for candidate: {e}")
            raise
    
    async def _score_with_embedding(
        self,
        query: str,
        candidates: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Score candidates using embedding similarity.
        
        This is a lightweight fallback that uses the existing embedding
        but scores more precisely than the initial retrieval.
        
        Args:
            query: User query string
            candidates: List of candidate chunks
            
        Returns:
            Candidates with rerank_score added
        """
        # Get query embedding
        query_embedding = await self.ollama.generate_embedding(query)
        
        scored = []
        for candidate in candidates:
            # Use semantic_score if available, otherwise calculate
            semantic_score = candidate.get('semantic_score')
            
            if semantic_score is None:
                # Get chunk embedding and calculate similarity
                chunk_embedding = await self.ollama.generate_embedding(
                    candidate.get('content', '')[:500]
                )
                semantic_score = self._cosine_similarity(
                    query_embedding, chunk_embedding
                )
            
            # Combine with lexical score if available
            lexical_score = candidate.get('lexical_score', 0)
            
            # Weighted combination (can be tuned)
            rerank_score = 0.7 * semantic_score + 0.3 * lexical_score
            
            candidate['rerank_score'] = round(rerank_score, 4)
            candidate['rerank_method'] = 'embedding'
            scored.append(candidate)
        
        return scored
    
    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """Calculate cosine similarity between two vectors.
        
        Args:
            a: First vector
            b: Second vector
            
        Returns:
            Cosine similarity between -1 and 1
        """
        import math
        
        dot_product = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        
        if norm_a == 0 or norm_b == 0:
            return 0.0
        
        return dot_product / (norm_a * norm_b)
    
    async def compare_with_baseline(
        self,
        query: str,
        query_embedding: Optional[List[float]] = None,
        k: int = 10
    ) -> Dict[str, Any]:
        """Compare reranked results with baseline hybrid search.
        
        Useful for benchmarking and evaluation.
        
        Args:
            query: User query string
            query_embedding: Pre-computed query embedding (optional)
            k: Number of results to return
            
        Returns:
            Dict with baseline results, reranked results, and comparison metrics
        """
        import time
        
        # Get baseline results
        start = time.perf_counter()
        baseline_results = await self.hybrid_retriever.retrieve(
            query=query,
            query_embedding=query_embedding,
            k=k
        )
        baseline_time_ms = (time.perf_counter() - start) * 1000
        
        # Get reranked results
        original_top_n = self.final_k
        self.final_k = k  # Temporarily set to k for comparison
        
        start = time.perf_counter()
        reranked_results, decision = await self.rerank_with_decision(
            query=query,
            query_embedding=query_embedding
        )
        rerank_time_ms = (time.perf_counter() - start) * 1000
        
        self.final_k = original_top_n  # Restore
        
        # Calculate overlap
        baseline_ids = set(r['id'] for r in baseline_results)
        reranked_ids = set(r['id'] for r in reranked_results)
        overlap = baseline_ids & reranked_ids
        
        # Calculate position changes for overlapping items
        position_changes = []
        for chunk_id in overlap:
            baseline_pos = next(
                (i for i, r in enumerate(baseline_results) if r['id'] == chunk_id),
                None
            )
            rerank_pos = next(
                (i for i, r in enumerate(reranked_results) if r['id'] == chunk_id),
                None
            )
            if baseline_pos is not None and rerank_pos is not None:
                position_changes.append({
                    'chunk_id': chunk_id,
                    'baseline_position': baseline_pos,
                    'reranked_position': rerank_pos,
                    'position_delta': baseline_pos - rerank_pos  # Positive = moved up
                })
        
        avg_position_change = sum(
            abs(p['position_delta']) for p in position_changes
        ) / len(position_changes) if position_changes else 0
        
        return {
            'query': query,
            'k': k,
            'baseline': {
                'results': [r['id'] for r in baseline_results],
                'latency_ms': round(baseline_time_ms, 3)
            },
            'reranked': {
                'results': [r['id'] for r in reranked_results],
                'latency_ms': round(rerank_time_ms, 3),
                'decision': decision.to_dict() if decision else None
            },
            'comparison': {
                'overlap_count': len(overlap),
                'overlap_pct': len(overlap) / max(len(baseline_ids), len(reranked_ids)) if max(len(baseline_ids), len(reranked_ids)) > 0 else 0,
                'baseline_only': list(baseline_ids - reranked_ids),
                'reranked_only': list(reranked_ids - baseline_ids),
                'latency_delta_ms': round(rerank_time_ms - baseline_time_ms, 3),
                'latency_delta_pct': round(
                    (rerank_time_ms - baseline_time_ms) / baseline_time_ms * 100, 1
                ) if baseline_time_ms > 0 else 0,
                'position_changes': position_changes,
                'avg_position_change': round(avg_position_change, 2)
            }
        }
    
    async def compare_selective_modes(
        self,
        query: str,
        query_embedding: Optional[List[float]] = None,
        k: int = 10
    ) -> Dict[str, Any]:
        """Compare all three reranking modes: baseline, always, selective.
        
        This is useful for benchmarking selective reranking to verify:
        - Selective mode applies reranking less often than always mode
        - Selective mode has lower latency than always mode
        - Selective mode maintains quality on triggered queries
        
        Args:
            query: User query string
            query_embedding: Pre-computed query embedding (optional)
            k: Number of results to return
            
        Returns:
            Dict with results from all three modes and comparison metrics
        """
        import time
        
        # Store original policy
        original_policy = self.policy
        
        results = {'query': query, 'k': k}
        
        # 1. Baseline (no reranking)
        start = time.perf_counter()
        baseline_results = await self.hybrid_retriever.retrieve(
            query=query,
            query_embedding=query_embedding,
            k=k
        )
        baseline_time_ms = (time.perf_counter() - start) * 1000
        
        results['baseline'] = {
            'results': [r['id'] for r in baseline_results],
            'latency_ms': round(baseline_time_ms, 3),
            'count': len(baseline_results)
        }
        
        # 2. Always rerank
        self.policy = RerankPolicy(mode='always')
        original_top_n = self.final_k
        self.final_k = k
        
        start = time.perf_counter()
        always_results, always_decision = await self.rerank_with_decision(
            query=query,
            query_embedding=query_embedding
        )
        always_time_ms = (time.perf_counter() - start) * 1000
        
        results['always'] = {
            'results': [r['id'] for r in always_results],
            'latency_ms': round(always_time_ms, 3),
            'count': len(always_results),
            'rerank_applied': always_decision.should_rerank if always_decision else False
        }
        
        # 3. Selective rerank
        self.policy = RerankPolicy(
            mode='selective',
            score_gap_threshold=original_policy.score_gap_threshold,
            disagreement_threshold=original_policy.disagreement_threshold,
            min_top_score=original_policy.min_top_score,
            complex_query_words=original_policy.complex_query_words
        )
        
        start = time.perf_counter()
        selective_results, selective_decision = await self.rerank_with_decision(
            query=query,
            query_embedding=query_embedding
        )
        selective_time_ms = (time.perf_counter() - start) * 1000
        
        results['selective'] = {
            'results': [r['id'] for r in selective_results],
            'latency_ms': round(selective_time_ms, 3),
            'count': len(selective_results),
            'rerank_applied': selective_decision.should_rerank if selective_decision else False,
            'decision': selective_decision.to_dict() if selective_decision else None
        }
        
        # Restore original settings
        self.final_k = original_top_n
        self.policy = original_policy
        
        # Calculate overlaps
        baseline_ids = set(results['baseline']['results'])
        always_ids = set(results['always']['results'])
        selective_ids = set(results['selective']['results'])
        
        results['comparison'] = {
            'baseline_vs_always': {
                'overlap': len(baseline_ids & always_ids),
                'baseline_only': len(baseline_ids - always_ids),
                'always_only': len(always_ids - baseline_ids)
            },
            'baseline_vs_selective': {
                'overlap': len(baseline_ids & selective_ids),
                'baseline_only': len(baseline_ids - selective_ids),
                'selective_only': len(selective_ids - baseline_ids)
            },
            'latency': {
                'baseline_ms': round(baseline_time_ms, 3),
                'always_ms': round(always_time_ms, 3),
                'selective_ms': round(selective_time_ms, 3),
                'always_vs_baseline_pct': round(
                    (always_time_ms - baseline_time_ms) / baseline_time_ms * 100, 1
                ) if baseline_time_ms > 0 else 0,
                'selective_vs_baseline_pct': round(
                    (selective_time_ms - baseline_time_ms) / baseline_time_ms * 100, 1
                ) if baseline_time_ms > 0 else 0,
                'selective_vs_always_pct': round(
                    (selective_time_ms - always_time_ms) / always_time_ms * 100, 1
                ) if always_time_ms > 0 else 0
            },
            'selective_savings': {
                'rerank_skipped': not results['selective']['rerank_applied'],
                'latency_saved_ms': round(always_time_ms - selective_time_ms, 3)
            }
        }
        
        return results

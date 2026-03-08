"""Hybrid retriever: combines lexical and semantic search for better RAG retrieval.

This module implements two-stage retrieval:
1. Fetch lexical candidates using PostgreSQL full-text search
2. Fetch vector candidates using pgvector similarity
3. Merge and rerank in Python for maximum control

Based on the hybrid search implementation plan (Phase 2: Retrieval Design).
"""

import asyncio
import logging
import re
from typing import List, Dict, Any, Optional

from shared.database import DocumentRepository

logger = logging.getLogger(__name__)


class HybridRetriever:
    """Two-stage hybrid retriever: lexical + vector → merge → rerank.
    
    Implements the retrieval design from Phase 2 of the hybrid search plan:
    - Lexical search uses PostgreSQL tsvector with weighted title/content
    - Vector search uses pgvector cosine similarity
    - Results are merged and reranked using weighted score blending or RRF
    
    Attributes:
        document_repo: Repository for database operations
        lexical_weight: Weight for lexical scores in final blend (default 0.35)
        semantic_weight: Weight for semantic scores in final blend (default 0.65)
        lexical_limit: Number of lexical candidates to fetch (default 30)
        vector_limit: Number of vector candidates to fetch (default 40)
        use_rrf: Use Reciprocal Rank Fusion instead of score blending
        rrf_k: RRF constant (default 60)
        auto_tune_weights: Automatically adjust weights based on query type
    """
    
    # Query pattern detection for auto-tuning weights
    PATTERNS = {
        'version_numbers': r'\b\w+\d+\.\d+\w*\b',     # llama3.2, v1.0, python3.11
        'dotted_tokens': r'\b[\w-]+\.[\w.-]+\b',      # ACME-42, error.code, pgvector.hnsw
        'acronyms': r'\b[A-Z]{2,}\b',                # API, SQL, RAG, FTS
        'quoted_phrases': r'"[^"]+"',                 # "exact phrase"
        'error_codes': r'\b[A-Z][a-z]+Error\b',      # TimeoutError, ConnectionError
    }
    
    def __init__(
        self,
        document_repo: DocumentRepository,
        lexical_weight: float = 0.35,
        semantic_weight: float = 0.65,
        lexical_limit: int = 30,
        vector_limit: int = 40,
        use_rrf: bool = False,
        rrf_k: int = 60,
        auto_tune_weights: bool = True
    ):
        """Initialize the hybrid retriever.
        
        Args:
            document_repo: DocumentRepository instance for database access
            lexical_weight: Weight for lexical scores (0.0-1.0)
            semantic_weight: Weight for semantic scores (0.0-1.0)
            lexical_limit: Number of lexical candidates to retrieve
            vector_limit: Number of vector candidates to retrieve
            use_rrf: Use Reciprocal Rank Fusion instead of score blending
            rrf_k: RRF constant (default 60)
            auto_tune_weights: Auto-adjust weights for exact-term queries
        """
        self.document_repo = document_repo
        self.lexical_weight = lexical_weight
        self.semantic_weight = semantic_weight
        self.lexical_limit = lexical_limit
        self.vector_limit = vector_limit
        self.use_rrf = use_rrf
        self.rrf_k = rrf_k
        self.auto_tune_weights = auto_tune_weights
        
        # Validate weights sum to 1.0
        if abs(lexical_weight + semantic_weight - 1.0) > 0.001:
            raise ValueError(f"Weights must sum to 1.0, got {lexical_weight} + {semantic_weight}")
    
    def detect_query_type(self, query: str) -> Dict[str, float]:
        """Detect query type and return appropriate weights.
        
        Exact-term-heavy queries (versions, acronyms, quoted phrases)
        benefit from higher lexical weight.
        
        Args:
            query: User query string
            
        Returns:
            Dict with 'lexical' and 'semantic' weights
        """
        if not self.auto_tune_weights:
            return {
                'lexical': self.lexical_weight,
                'semantic': self.semantic_weight
            }
        
        exact_term_indicators = sum(
            1 for pattern in self.PATTERNS.values()
            if re.search(pattern, query)
        )
        
        # Boost lexical for exact-term-heavy queries
        if exact_term_indicators >= 2 or '"' in query:
            logger.debug(f"Query '{query[:50]}...' detected as exact-term-heavy, "
                        f"boosting lexical weight")
            return {'lexical': 0.50, 'semantic': 0.50}
        
        return {
            'lexical': self.lexical_weight,
            'semantic': self.semantic_weight
        }
    
    async def fetch_lexical(
        self, 
        query: str, 
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Fetch candidates using PostgreSQL full-text search.
        
        Uses the weighted search_tsv column with title (A) and content (D) weights.
        
        Args:
            query: Search query text
            limit: Override default lexical_limit
            
        Returns:
            List of chunk dicts with lexical_score
        """
        limit = limit or self.lexical_limit
        
        try:
            results = await self.document_repo.find_similar_chunks_lexical(query, limit)
            logger.debug(f"Lexical search for '{query[:50]}...' returned {len(results)} results")
            return results
        except Exception as e:
            logger.error(f"Lexical search failed: {e}")
            return []
    
    async def fetch_vector(
        self, 
        embedding: List[float], 
        limit: Optional[int] = None,
        similarity_threshold: float = 0.0
    ) -> List[Dict[str, Any]]:
        """Fetch candidates using vector similarity.
        
        Uses pgvector cosine distance for semantic similarity search.
        
        Args:
            embedding: Query embedding vector
            limit: Override default vector_limit
            similarity_threshold: Minimum similarity score
            
        Returns:
            List of chunk dicts with semantic_score
        """
        limit = limit or self.vector_limit
        
        try:
            results = await self.document_repo.find_similar_chunks_semantic(
                embedding, limit, similarity_threshold
            )
            logger.debug(f"Vector search returned {len(results)} results")
            return results
        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            return []
    
    def normalize_scores(
        self, 
        items: List[Dict[str, Any]], 
        score_key: str
    ) -> List[Dict[str, Any]]:
        """Normalize scores to 0-1 range using min-max normalization.
        
        ts_rank and cosine similarity are on different scales and must be
        normalized before blending.
        
        Args:
            items: List of result items with scores
            score_key: Key for the score field (e.g., 'lexical_score')
            
        Returns:
            Items with added {score_key}_norm field
        """
        if not items:
            return items
        
        scores = [item[score_key] for item in items]
        min_score, max_score = min(scores), max(scores)
        range_score = max_score - min_score if max_score > min_score else 1
        
        for item in items:
            item[f'{score_key}_norm'] = (item[score_key] - min_score) / range_score
        
        return items
    
    def merge_and_rerank(
        self,
        lexical: List[Dict[str, Any]],
        vector: List[Dict[str, Any]],
        weights: Optional[Dict[str, float]] = None
    ) -> List[Dict[str, Any]]:
        """Merge lexical and vector results and compute hybrid scores.
        
        Implements either weighted score blending or Reciprocal Rank Fusion (RRF)
        based on the use_rrf setting.
        
        Args:
            lexical: Results from lexical search with lexical_score
            vector: Results from vector search with semantic_score
            weights: Optional override for lexical/semantic weights
            
        Returns:
            Merged and reranked list with hybrid_score
        """
        weights = weights or {
            'lexical': self.lexical_weight,
            'semantic': self.semantic_weight
        }
        
        # Create lookup by id
        lexical_by_id = {item['id']: item for item in lexical}
        vector_by_id = {item['id']: item for item in vector}
        
        all_ids = set(lexical_by_id.keys()) | set(vector_by_id.keys())
        
        # Normalize scores within each result set
        lexical = self.normalize_scores(lexical, 'lexical_score')
        vector = self.normalize_scores(vector, 'semantic_score')
        
        merged = []
        for chunk_id in all_ids:
            # Build base item with metadata from available source
            lex_item = lexical_by_id.get(chunk_id, {})
            vec_item = vector_by_id.get(chunk_id, {})
            
            item = {
                'id': chunk_id,
                'document_id': lex_item.get('document_id') or vec_item.get('document_id'),
                'chunk_index': lex_item.get('chunk_index') or vec_item.get('chunk_index'),
                'title': lex_item.get('title') or vec_item.get('title') or 'Untitled',
                'content': lex_item.get('content') or vec_item.get('content') or '',
            }
            
            if self.use_rrf:
                # Reciprocal Rank Fusion: 1/(k + rank)
                lexical_rank = next(
                    (i for i, x in enumerate(lexical) if x['id'] == chunk_id),
                    len(lexical)
                )
                vector_rank = next(
                    (i for i, x in enumerate(vector) if x['id'] == chunk_id),
                    len(vector)
                )
                item['hybrid_score'] = (
                    1 / (self.rrf_k + lexical_rank) +
                    1 / (self.rrf_k + vector_rank)
                )
                item['lexical_rank'] = lexical_rank
                item['vector_rank'] = vector_rank
            else:
                # Weighted score blending
                lexical_score = lexical_by_id.get(chunk_id, {}).get('lexical_score_norm', 0)
                semantic_score = vector_by_id.get(chunk_id, {}).get('semantic_score_norm', 0)
                item['hybrid_score'] = (
                    weights['lexical'] * lexical_score +
                    weights['semantic'] * semantic_score
                )
                item['lexical_score'] = lex_item.get('lexical_score')
                item['semantic_score'] = vec_item.get('semantic_score')
            
            # Track provenance
            item['from_lexical'] = chunk_id in lexical_by_id
            item['from_vector'] = chunk_id in vector_by_id
            
            merged.append(item)
        
        # Sort by hybrid score descending
        merged.sort(key=lambda x: x['hybrid_score'], reverse=True)
        return merged
    
    async def retrieve(
        self,
        query: str,
        query_embedding: Optional[List[float]] = None,
        k: int = 8,
        fetch_lexical: bool = True,
        fetch_vector: bool = True
    ) -> List[Dict[str, Any]]:
        """Main entry point: fetch, merge, and return top-k chunks.
        
        This method implements the complete hybrid retrieval pipeline:
        1. Auto-detect query type for weight tuning
        2. Fetch lexical and/or vector candidates concurrently
        3. Merge and rerank results
        4. Return top-k chunks
        
        Gracefully handles failures:
        - If lexical fails: returns vector-only results
        - If vector fails: returns lexical-only results
        - If both fail: returns empty list
        
        Args:
            query: User query string
            query_embedding: Optional pre-computed embedding (fetched if None)
            k: Number of top results to return
            fetch_lexical: Whether to perform lexical search
            fetch_vector: Whether to perform vector search (requires embedding)
            
        Returns:
            List of top-k chunk dicts with hybrid_score and provenance info
        """
        # Auto-tune weights based on query type
        weights = self.detect_query_type(query)
        logger.debug(f"Retrieving with weights: {weights}")
        
        # Prepare fetch tasks
        tasks = []
        task_types = []
        
        if fetch_lexical:
            tasks.append(self.fetch_lexical(query))
            task_types.append('lexical')
        
        if fetch_vector and query_embedding is not None:
            tasks.append(self.fetch_vector(query_embedding))
            task_types.append('vector')
        
        if not tasks:
            logger.warning("No retrieval tasks configured")
            return []
        
        # Execute fetches concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        lexical_hits = []
        vector_hits = []
        
        for task_type, result in zip(task_types, results):
            if isinstance(result, Exception):
                logger.error(f"{task_type} search failed: {result}")
            elif task_type == 'lexical':
                lexical_hits = result
            else:
                vector_hits = result
        
        # Log retrieval stats
        overlap = len(set(x['id'] for x in lexical_hits) & 
                     set(x['id'] for x in vector_hits))
        logger.info(
            f"Retrieved: {len(lexical_hits)} lexical, {len(vector_hits)} vector, "
            f"{overlap} overlap"
        )
        
        # Handle fallbacks
        if not lexical_hits and not vector_hits:
            logger.warning("Both searches returned no results")
            return []
        
        if not lexical_hits:
            logger.info("No lexical hits, returning vector-only results")
            return self.normalize_scores(vector_hits, 'semantic_score')[:k]
        
        if not vector_hits:
            logger.info("No vector hits, returning lexical-only results")
            return self.normalize_scores(lexical_hits, 'lexical_score')[:k]
        
        # Merge and rerank
        merged = self.merge_and_rerank(lexical_hits, vector_hits, weights)
        
        return merged[:k]
    
    async def retrieve_with_transform(
        self,
        query: str,
        query_transformer,
        query_embedding: Optional[List[float]] = None,
        k: int = 8,
        latency_budget_ms: Optional[float] = None
    ) -> tuple[List[Dict[str, Any]], Any, Dict[str, Any]]:
        """Retrieve with query transformation for improved recall.
        
        This method transforms the query into multiple variants,
        retrieves for each, and merges results with deduplication.
        
        Includes latency budget guard: if retrieval time exceeds budget,
        remaining expansions are skipped.
        
        Args:
            query: User query string
            query_transformer: QueryTransformer instance
            query_embedding: Optional pre-computed embedding
            k: Number of top results to return
            latency_budget_ms: Optional latency budget in milliseconds
            
        Returns:
            Tuple of (results, transform_decision, merge_metadata)
        """
        import time
        from shared.query_transformer import TransformDecision
        
        start_time = time.perf_counter()
        
        # First, do a quick retrieval to check for low evidence
        # This helps the transformer decide if transformation is needed
        quick_results = await self.retrieve(query, query_embedding, k=min(k, 5))
        
        # Transform the query
        decision = query_transformer.transform(query, candidates=quick_results)
        
        if not decision.should_transform or len(decision.transformed_queries) == 1:
            # No transformation needed or applied, return original results
            if not quick_results:
                quick_results = await self.retrieve(query, query_embedding, k=k)
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            metadata = {
                'transform_applied': False,
                'queries_used': 1,
                'latency_ms': round(elapsed_ms, 2)
            }
            return quick_results, decision, metadata
        
        # Retrieve for each transformed query
        logger.info(
            f"Query transformation: '{query[:50]}...' -> "
            f"{len(decision.transformed_queries)} queries: {decision.transform_types}"
        )
        
        all_results = []
        queries_used = 0
        budget_exceeded = False
        
        for idx, tq in enumerate(decision.transformed_queries):
            # Check latency budget before each retrieval
            if latency_budget_ms:
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                remaining_budget = latency_budget_ms - elapsed_ms
                
                if remaining_budget < 20:  # Need at least 20ms for useful retrieval
                    logger.warning(
                        f"Latency budget exceeded after {queries_used} queries "
                        f"({elapsed_ms:.1f}ms > {latency_budget_ms}ms), "
                        f"skipping remaining expansions for '{query[:50]}...'"
                    )
                    budget_exceeded = True
                    break
            
            # Get embedding for transformed query if needed
            if query_embedding is not None and tq.lower() == query.lower():
                tq_embedding = query_embedding
            else:
                # Generate new embedding for transformed query
                # Note: This requires ollama client - handled by caller
                tq_embedding = None
            
            results = await self.retrieve(tq, tq_embedding, k=k * 2)  # Get more for merging
            all_results.append(results)
            queries_used += 1
            
            # Early exit if we have enough results and budget is tight
            if latency_budget_ms and idx > 0:
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                remaining_budget = latency_budget_ms - elapsed_ms
                
                # If we have results from 2+ queries and budget is tight, stop
                if queries_used >= 2 and remaining_budget < 50:
                    logger.info(
                        f"Early exit: {queries_used} queries sufficient, "
                        f"{remaining_budget:.1f}ms budget remaining"
                    )
                    break
        
        # Merge and deduplicate results using RRF
        merged, merge_metadata = query_transformer.merge_results(all_results, query, max_results=k)
        
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        merge_metadata['transform_applied'] = True
        merge_metadata['queries_planned'] = len(decision.transformed_queries)
        merge_metadata['queries_used'] = queries_used
        merge_metadata['budget_exceeded'] = budget_exceeded
        merge_metadata['latency_ms'] = round(elapsed_ms, 2)
        
        if latency_budget_ms:
            merge_metadata['latency_budget_ms'] = latency_budget_ms
            merge_metadata['budget_utilization'] = round(elapsed_ms / latency_budget_ms, 2)
        
        return merged, decision, merge_metadata
    
    async def retrieve_with_ranking_mode(
        self,
        query: str,
        query_embedding: Optional[List[float]] = None,
        k: int = 8,
        ranking_mode: str = 'weighted'
    ) -> List[Dict[str, Any]]:
        """Retrieve using a specific ranking mode.
        
        This method allows explicit selection of ranking mode for benchmarking
        and comparison purposes.
        
        Args:
            query: User query string
            query_embedding: Optional pre-computed embedding
            k: Number of top results to return
            ranking_mode: 'weighted' or 'rrf'
            
        Returns:
            List of top-k chunk dicts
        """
        # Save current mode
        original_use_rrf = self.use_rrf
        
        try:
            # Set requested mode
            if ranking_mode == 'rrf':
                self.use_rrf = True
            elif ranking_mode == 'weighted':
                self.use_rrf = False
            else:
                raise ValueError(f"Unknown ranking_mode: {ranking_mode}. Use 'weighted' or 'rrf'")
            
            # Run retrieval
            return await self.retrieve(query, query_embedding, k)
        finally:
            # Restore original mode
            self.use_rrf = original_use_rrf
    
    async def compare_ranking_modes(
        self,
        query: str,
        query_embedding: Optional[List[float]] = None,
        k: int = 8
    ) -> Dict[str, Any]:
        """Compare weighted and RRF ranking for the same query.
        
        This is useful for benchmarking and evaluation to determine which
        ranking method works better for a given corpus.
        
        Args:
            query: User query string
            query_embedding: Optional pre-computed embedding
            k: Number of top results to return
            
        Returns:
            Dict with results from both modes and comparison metrics
        """
        import time
        
        # Fetch candidates once
        weights = self.detect_query_type(query)
        
        lexical_hits = await self.fetch_lexical(query)
        vector_hits = await self.fetch_vector(query_embedding) if query_embedding else []
        
        results = {
            'query': query,
            'k': k,
            'lexical_candidates': len(lexical_hits),
            'vector_candidates': len(vector_hits),
            'overlap_candidates': len(set(x['id'] for x in lexical_hits) & 
                                       set(x['id'] for x in vector_hits))
        }
        
        # Test weighted ranking
        start = time.perf_counter()
        self.use_rrf = False
        if lexical_hits and vector_hits:
            weighted_results = self.merge_and_rerank(lexical_hits, vector_hits, weights)[:k]
        elif vector_hits:
            weighted_results = self.normalize_scores(vector_hits, 'semantic_score')[:k]
        elif lexical_hits:
            weighted_results = self.normalize_scores(lexical_hits, 'lexical_score')[:k]
        else:
            weighted_results = []
        weighted_time_ms = (time.perf_counter() - start) * 1000
        
        # Test RRF ranking
        start = time.perf_counter()
        self.use_rrf = True
        if lexical_hits and vector_hits:
            rrf_results = self.merge_and_rerank(lexical_hits, vector_hits, weights)[:k]
        elif vector_hits:
            rrf_results = self.normalize_scores(vector_hits, 'semantic_score')[:k]
        elif lexical_hits:
            rrf_results = self.normalize_scores(lexical_hits, 'lexical_score')[:k]
        else:
            rrf_results = []
        rrf_time_ms = (time.perf_counter() - start) * 1000
        
        # Calculate overlap between modes
        weighted_ids = set(r['id'] for r in weighted_results)
        rrf_ids = set(r['id'] for r in rrf_results)
        overlap = weighted_ids & rrf_ids
        
        results['weighted'] = {
            'chunk_ids': [r['id'] for r in weighted_results],
            'scores': [round(r.get('hybrid_score', 0), 4) for r in weighted_results],
            'latency_ms': round(weighted_time_ms, 3),
            'from_lexical': sum(1 for r in weighted_results if r.get('from_lexical')),
            'from_vector': sum(1 for r in weighted_results if r.get('from_vector'))
        }
        
        results['rrf'] = {
            'chunk_ids': [r['id'] for r in rrf_results],
            'scores': [round(r.get('hybrid_score', 0), 4) for r in rrf_results],
            'latency_ms': round(rrf_time_ms, 3),
            'from_lexical': sum(1 for r in rrf_results if r.get('from_lexical')),
            'from_vector': sum(1 for r in rrf_results if r.get('from_vector'))
        }
        
        results['comparison'] = {
            'overlap_count': len(overlap),
            'overlap_ids': list(overlap),
            'weighted_only': list(weighted_ids - rrf_ids),
            'rrf_only': list(rrf_ids - weighted_ids),
            'latency_delta_ms': round(rrf_time_ms - weighted_time_ms, 3),
            'latency_delta_pct': round((rrf_time_ms - weighted_time_ms) / weighted_time_ms * 100, 1) 
                                if weighted_time_ms > 0 else 0
        }
        
        return results

#!/usr/bin/env python3
"""HNSW Vector Index Benchmark Tool

This script benchmarks HNSW vector search performance against exact (brute-force)
vector search and evaluates different ef_search settings.

Usage:
    # Run with default benchmark queries
    python scripts/benchmark_hnsw.py
    
    # Run with custom queries file
    python scripts/benchmark_hnsw.py --queries-file queries.json
    
    # Run specific ef_search values
    python scripts/benchmark_hnsw.py --ef-search 20 40 60 80 100
    
    # Output to specific directory
    python scripts/benchmark_hnsw.py --output-dir ./benchmark_results
    
    # Skip hybrid benchmark (faster)
    python scripts/benchmark_hnsw.py --skip-hybrid
    
    # Run RAG quality evaluation
    python scripts/benchmark_hnsw.py --eval-rag --rag-sample-size 20
    
    # Set minimum recall threshold for recommendation
    python scripts/benchmark_hnsw.py --min-recall 0.93

Output:
    - JSON: Detailed results with all metrics
    - CSV: Summary table for analysis
    - Console: Human-readable recommendation
"""

import argparse
import json
import os
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


# Default benchmark queries covering different query types
DEFAULT_BENCHMARK_QUERIES = [
    # Semantic/conceptual queries
    "What is machine learning and how does it work?",
    "Explain vector search and embeddings",
    "How does neural network training work?",
    
    # Exact term queries
    "Python 3.11 asyncio best practices",
    "PostgreSQL pgvector extension setup",
    "API authentication JWT tokens",
    
    # Hybrid queries
    "docker compose health check configuration",
    "fastapi dependency injection patterns",
    "celery redis queue configuration",
    
    # Short queries
    "semantic search",
    "RAG pipeline",
    "chunking strategies",
]


@dataclass
class BenchmarkEnvironment:
    """Environment details for reproducible benchmarks."""
    database_chunks: int
    database_documents: int
    embedding_dimension: int
    postgresql_version: str
    pgvector_version: str
    hnsw_m: Optional[int]
    hnsw_ef_construction: Optional[int]
    cpu_info: str
    platform: str
    cache_warm: bool
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'database_chunks': self.database_chunks,
            'database_documents': self.database_documents,
            'embedding_dimension': self.embedding_dimension,
            'postgresql_version': self.postgresql_version,
            'pgvector_version': self.pgvector_version,
            'hnsw_m': self.hnsw_m,
            'hnsw_ef_construction': self.hnsw_ef_construction,
            'cpu_info': self.cpu_info,
            'platform': self.platform,
            'cache_warm': self.cache_warm
        }


@dataclass
class BenchmarkQuery:
    """A single benchmark query."""
    text: str
    embedding: Optional[List[float]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'text': self.text,
            'embedding': self.embedding is not None
        }


@dataclass
class RetrievalResult:
    """Results from a single retrieval operation."""
    query: str
    method: str  # 'exact', 'hnsw', 'hybrid'
    ef_search: Optional[int]  # None for exact/hybrid
    top_k: int
    chunk_ids: List[int]
    scores: List[float]
    latency_ms: float
    explain_plan: Optional[str] = None  # For verification
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'query': self.query,
            'method': self.method,
            'ef_search': self.ef_search,
            'top_k': self.top_k,
            'chunk_ids': self.chunk_ids,
            'scores': self.scores,
            'latency_ms': round(self.latency_ms, 2),
            'explain_plan': self.explain_plan
        }


@dataclass
class OverlapMetrics:
    """Overlap metrics comparing two result sets."""
    query: str
    baseline_method: str
    compare_method: str
    baseline_ef: Optional[int]
    compare_ef: Optional[int]
    top_k: int
    baseline_results: int
    compare_results: int
    overlap_count: int
    overlap_ids: List[int]
    missing_ids: List[int]  # IDs in baseline but not in compare
    recall: float  # % of baseline results recovered
    latency_baseline_ms: float
    latency_compare_ms: float
    latency_delta_pct: float
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'query': self.query,
            'baseline_method': self.baseline_method,
            'compare_method': self.compare_method,
            'baseline_ef': self.baseline_ef,
            'compare_ef': self.compare_ef,
            'top_k': self.top_k,
            'baseline_results': self.baseline_results,
            'compare_results': self.compare_results,
            'overlap_count': self.overlap_count,
            'recall': round(self.recall, 4),
            'latency_baseline_ms': round(self.latency_baseline_ms, 2),
            'latency_compare_ms': round(self.latency_compare_ms, 2),
            'latency_delta_pct': round(self.latency_delta_pct, 2)
        }


@dataclass
class RecallOutlier:
    """Query with unusually low recall - for debugging."""
    query: str
    ef_search: int
    top_k: int
    recall: float
    exact_ids: List[int]
    hnsw_ids: List[int]
    missing_ids: List[int]
    exact_scores: List[float]
    hnsw_scores: List[float]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'query': self.query,
            'ef_search': self.ef_search,
            'top_k': self.top_k,
            'recall': round(self.recall, 4),
            'exact_ids': self.exact_ids,
            'hnsw_ids': self.hnsw_ids,
            'missing_ids': self.missing_ids
        }


@dataclass
class EfSearchBenchmark:
    """Benchmark results for a specific ef_search value."""
    ef_search: int
    latencies_ms: List[float] = field(default_factory=list)
    overlap_metrics: List[OverlapMetrics] = field(default_factory=list)
    outliers: List[RecallOutlier] = field(default_factory=list)
    
    @property
    def p50_latency_ms(self) -> float:
        from statistics import median
        return median(self.latencies_ms) if self.latencies_ms else 0
    
    @property
    def p95_latency_ms(self) -> float:
        if not self.latencies_ms:
            return 0
        from statistics import median
        sorted_latencies = sorted(self.latencies_ms)
        idx = int(len(sorted_latencies) * 0.95)
        return sorted_latencies[min(idx, len(sorted_latencies) - 1)]
    
    @property
    def max_latency_ms(self) -> float:
        return max(self.latencies_ms) if self.latencies_ms else 0
    
    @property
    def mean_latency_ms(self) -> float:
        from statistics import mean
        return mean(self.latencies_ms) if self.latencies_ms else 0
    
    @property
    def mean_recall_top5(self) -> float:
        from statistics import mean
        recalls = [m.recall for m in self.overlap_metrics if m.top_k == 5]
        return mean(recalls) if recalls else 0
    
    @property
    def mean_recall_top10(self) -> float:
        from statistics import mean
        recalls = [m.recall for m in self.overlap_metrics if m.top_k == 10]
        return mean(recalls) if recalls else 0
    
    @property
    def min_recall_top10(self) -> float:
        recalls = [m.recall for m in self.overlap_metrics if m.top_k == 10]
        return min(recalls) if recalls else 0
    
    def to_summary_dict(self) -> Dict[str, Any]:
        return {
            'ef_search': self.ef_search,
            'p50_latency_ms': round(self.p50_latency_ms, 2),
            'p95_latency_ms': round(self.p95_latency_ms, 2),
            'max_latency_ms': round(self.max_latency_ms, 2),
            'mean_latency_ms': round(self.mean_latency_ms, 2),
            'mean_recall_top5': round(self.mean_recall_top5, 4),
            'mean_recall_top10': round(self.mean_recall_top10, 4),
            'min_recall_top10': round(self.min_recall_top10, 4),
            'query_count': len(self.latencies_ms),
            'outlier_count': len(self.outliers)
        }


@dataclass
class RAGQualityResult:
    """RAG output quality metrics."""
    query: str
    ef_search: int
    answer_generated: bool
    citations_present: bool
    chunks_used: int
    token_count: int
    generation_time_ms: float
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'query': self.query,
            'ef_search': self.ef_search,
            'answer_generated': self.answer_generated,
            'citations_present': self.citations_present,
            'chunks_used': self.chunks_used,
            'token_count': self.token_count,
            'generation_time_ms': round(self.generation_time_ms, 2)
        }


@dataclass
class RankingComparisonResult:
    """Comparison between weighted and RRF ranking for a single query."""
    query: str
    k: int
    weighted_results: List[int]
    rrf_results: List[int]
    overlap_count: int
    overlap_ids: List[int]
    weighted_only: List[int]
    rrf_only: List[int]
    weighted_latency_ms: float
    rrf_latency_ms: float
    latency_delta_ms: float
    latency_delta_pct: float
    lexical_candidates: int
    vector_candidates: int
    
    @property
    def overlap_pct(self) -> float:
        """Percentage of results that overlap between modes."""
        if not self.weighted_results and not self.rrf_results:
            return 1.0
        union = len(set(self.weighted_results) | set(self.rrf_results))
        return self.overlap_count / union if union > 0 else 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'query': self.query,
            'k': self.k,
            'overlap_count': self.overlap_count,
            'overlap_pct': round(self.overlap_pct, 4),
            'weighted_results': self.weighted_results,
            'rrf_results': self.rrf_results,
            'weighted_only': self.weighted_only,
            'rrf_only': self.rrf_only,
            'weighted_latency_ms': round(self.weighted_latency_ms, 3),
            'rrf_latency_ms': round(self.rrf_latency_ms, 3),
            'latency_delta_ms': round(self.latency_delta_ms, 3),
            'latency_delta_pct': round(self.latency_delta_pct, 1)
        }


@dataclass
class RankingModeBenchmark:
    """Benchmark results comparing weighted vs RRF ranking."""
    comparisons: List[RankingComparisonResult] = field(default_factory=list)
    
    @property
    def mean_overlap_pct(self) -> float:
        from statistics import mean
        return mean([c.overlap_pct for c in self.comparisons]) if self.comparisons else 0
    
    @property
    def mean_latency_delta_pct(self) -> float:
        from statistics import mean
        return mean([c.latency_delta_pct for c in self.comparisons]) if self.comparisons else 0
    
    @property
    def weighted_p95_latency_ms(self) -> float:
        if not self.comparisons:
            return 0
        latencies = sorted([c.weighted_latency_ms for c in self.comparisons])
        idx = int(len(latencies) * 0.95)
        return latencies[min(idx, len(latencies) - 1)]
    
    @property
    def rrf_p95_latency_ms(self) -> float:
        if not self.comparisons:
            return 0
        latencies = sorted([c.rrf_latency_ms for c in self.comparisons])
        idx = int(len(latencies) * 0.95)
        return latencies[min(idx, len(latencies) - 1)]
    
    def to_summary_dict(self) -> Dict[str, Any]:
        return {
            'mean_overlap_pct': round(self.mean_overlap_pct, 4),
            'mean_latency_delta_pct': round(self.mean_latency_delta_pct, 2),
            'weighted_p95_latency_ms': round(self.weighted_p95_latency_ms, 3),
            'rrf_p95_latency_ms': round(self.rrf_p95_latency_ms, 3),
            'query_count': len(self.comparisons)
        }


@dataclass
class RerankingComparisonResult:
    """Comparison between baseline hybrid and reranker for a single query."""
    query: str
    k: int
    baseline_results: List[int]
    reranked_results: List[int]
    overlap_count: int
    overlap_ids: List[int]
    baseline_only: List[int]
    reranked_only: List[int]
    baseline_latency_ms: float
    reranked_latency_ms: float
    latency_delta_ms: float
    latency_delta_pct: float
    position_changes: List[Dict[str, Any]]
    avg_position_change: float
    
    @property
    def overlap_pct(self) -> float:
        """Percentage of results that overlap between modes."""
        if not self.baseline_results and not self.reranked_results:
            return 1.0
        union = len(set(self.baseline_results) | set(self.reranked_results))
        return self.overlap_count / union if union > 0 else 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'query': self.query,
            'k': self.k,
            'overlap_count': self.overlap_count,
            'overlap_pct': round(self.overlap_pct, 4),
            'baseline_results': self.baseline_results,
            'reranked_results': self.reranked_results,
            'baseline_only': self.baseline_only,
            'reranked_only': self.reranked_only,
            'baseline_latency_ms': round(self.baseline_latency_ms, 3),
            'reranked_latency_ms': round(self.reranked_latency_ms, 3),
            'latency_delta_ms': round(self.latency_delta_ms, 3),
            'latency_delta_pct': round(self.latency_delta_pct, 1),
            'avg_position_change': round(self.avg_position_change, 2)
        }


@dataclass
class RerankingBenchmark:
    """Benchmark results comparing baseline vs reranked retrieval."""
    comparisons: List[RerankingComparisonResult] = field(default_factory=list)
    
    @property
    def mean_overlap_pct(self) -> float:
        from statistics import mean
        return mean([c.overlap_pct for c in self.comparisons]) if self.comparisons else 0
    
    @property
    def mean_latency_delta_pct(self) -> float:
        from statistics import mean
        return mean([c.latency_delta_pct for c in self.comparisons]) if self.comparisons else 0
    
    @property
    def baseline_p95_latency_ms(self) -> float:
        if not self.comparisons:
            return 0
        latencies = sorted([c.baseline_latency_ms for c in self.comparisons])
        idx = int(len(latencies) * 0.95)
        return latencies[min(idx, len(latencies) - 1)]
    
    @property
    def reranked_p95_latency_ms(self) -> float:
        if not self.comparisons:
            return 0
        latencies = sorted([c.reranked_latency_ms for c in self.comparisons])
        idx = int(len(latencies) * 0.95)
        return latencies[min(idx, len(latencies) - 1)]
    
    @property
    def mean_position_change(self) -> float:
        from statistics import mean
        return mean([c.avg_position_change for c in self.comparisons]) if self.comparisons else 0
    
    def to_summary_dict(self) -> Dict[str, Any]:
        return {
            'mean_overlap_pct': round(self.mean_overlap_pct, 4),
            'mean_latency_delta_pct': round(self.mean_latency_delta_pct, 2),
            'baseline_p95_latency_ms': round(self.baseline_p95_latency_ms, 3),
            'reranked_p95_latency_ms': round(self.reranked_p95_latency_ms, 3),
            'mean_position_change': round(self.mean_position_change, 2),
            'query_count': len(self.comparisons)
        }


@dataclass
class BenchmarkReport:
    """Complete benchmark report."""
    timestamp: str
    environment: BenchmarkEnvironment
    queries: List[str]
    ef_search_values: List[int]
    vector_summary: Dict[str, Any] = field(default_factory=dict)
    hybrid_summary: Dict[str, Any] = field(default_factory=dict)
    ranking_comparison: Optional[RankingModeBenchmark] = None
    exact_benchmark: Optional[EfSearchBenchmark] = None
    hnsw_benchmarks: List[EfSearchBenchmark] = field(default_factory=list)
    hybrid_results: List[Dict[str, Any]] = field(default_factory=list)
    rag_quality: List[RAGQualityResult] = field(default_factory=list)
    recommendation: str = ""
    ranking_recommendation: str = ""
    reranking_comparison: Optional[RerankingBenchmark] = None
    reranking_recommendation: str = ""
    selective_reranking_comparison: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'timestamp': self.timestamp,
            'environment': self.environment.to_dict(),
            'queries': self.queries,
            'ef_search_values': self.ef_search_values,
            'vector_summary': self.vector_summary,
            'hybrid_summary': self.hybrid_summary,
            'ranking_comparison': self.ranking_comparison.to_summary_dict() if self.ranking_comparison else None,
            'reranking_comparison': self.reranking_comparison.to_summary_dict() if self.reranking_comparison else None,
            'exact_benchmark': self.exact_benchmark.to_summary_dict() if self.exact_benchmark else None,
            'hnsw_benchmarks': [b.to_summary_dict() for b in self.hnsw_benchmarks],
            'hybrid_results': self.hybrid_results,
            'rag_quality': [r.to_dict() for r in self.rag_quality],
            'recommendation': self.recommendation,
            'ranking_recommendation': self.ranking_recommendation,
            'reranking_recommendation': self.reranking_recommendation,
            'selective_reranking_comparison': self.selective_reranking_comparison
        }


class HNSWBenchmark:
    """Main benchmark runner."""
    
    def __init__(self, output_dir: str = "./benchmark_results"):
        from shared.ollama_client import OllamaClient
        
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.ollama = OllamaClient()
        
    async def get_environment(self, cache_warm: bool = False) -> BenchmarkEnvironment:
        """Capture benchmark environment for reproducibility."""
        from shared.database import db_manager, document_repo
        
        async with db_manager.get_async_connection_context() as conn:
            # Database stats
            stats = await document_repo.get_stats()
            
            # PostgreSQL version
            pg_version = await conn.fetchval("SELECT version()")
            
            # pgvector version
            try:
                pgvector_version = await conn.fetchval("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            except:
                pgvector_version = "unknown"
            
            # HNSW index parameters
            hnsw_m = None
            hnsw_ef_construction = None
            try:
                index_def = await conn.fetchval("""
                    SELECT indexdef FROM pg_indexes 
                    WHERE schemaname = 'intelligence' 
                      AND indexname = 'idx_chunks_embedding_hnsw'
                """)
                if index_def:
                    import re
                    m_match = re.search(r'm\s*=\s*(\d+)', index_def)
                    ef_match = re.search(r'ef_construction\s*=\s*(\d+)', index_def)
                    hnsw_m = int(m_match.group(1)) if m_match else None
                    hnsw_ef_construction = int(ef_match.group(1)) if ef_match else None
            except:
                pass
            
            # Embedding dimension
            try:
                dim_row = await conn.fetchrow("""
                    SELECT atttypmod as dim 
                    FROM pg_attribute 
                    WHERE attrelid = 'intelligence.chunks'::regclass 
                      AND attname = 'embedding'
                """)
                embedding_dim = dim_row['dim'] if dim_row else 768
            except:
                embedding_dim = 768
        
        return BenchmarkEnvironment(
            database_chunks=stats.get('total_chunks', 0),
            database_documents=stats.get('total_documents', 0),
            embedding_dimension=embedding_dim,
            postgresql_version=pg_version.split()[1] if pg_version else "unknown",
            pgvector_version=pgvector_version or "unknown",
            hnsw_m=hnsw_m,
            hnsw_ef_construction=hnsw_ef_construction,
            cpu_info=platform.processor() or "unknown",
            platform=f"{platform.system()} {platform.release()}",
            cache_warm=cache_warm
        )
    
    async def generate_embeddings(self, queries: List[str]) -> List["BenchmarkQuery"]:
        """Generate embeddings for all benchmark queries."""
        print("Generating embeddings for benchmark queries...")
        benchmark_queries = []
        
        for i, query in enumerate(queries):
            try:
                embedding = await self.ollama.generate_embedding(query)
                benchmark_queries.append(BenchmarkQuery(
                    text=query,
                    embedding=embedding
                ))
                print(f"  [{i+1}/{len(queries)}] {query[:50]}...")
            except Exception as e:
                print(f"  ERROR: Failed to generate embedding for '{query}': {e}")
        
        print(f"Generated {len(benchmark_queries)} embeddings")
        return benchmark_queries
    
    async def run_exact_search(
        self,
        query: "BenchmarkQuery",
        top_k: int = 10
    ) -> "RetrievalResult":
        """Run exact (brute-force) vector search by disabling index usage."""
        import time
        from shared.database import db_manager
        
        if not query.embedding:
            raise ValueError("Query must have embedding")
        
        start_time = time.perf_counter()
        
        async with db_manager.get_async_connection_context() as conn:
            # Disable ALL index usage to force exact sequential scan
            await conn.execute("SET enable_indexscan = off")
            await conn.execute("SET enable_bitmapscan = off")
            await conn.execute("SET enable_indexonlyscan = off")
            
            embedding_str = f"[{','.join(str(x) for x in query.embedding)}]"
            
            # Verify exact mode with EXPLAIN
            explain = await conn.fetchval("""
                EXPLAIN (FORMAT TEXT)
                SELECT id, 1 - (embedding <=> $1::vector) as score
                FROM intelligence.chunks
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> $1::vector
                LIMIT $2
            """, embedding_str, top_k)
            
            # Ensure no index scan in plan
            explain_upper = explain.upper()
            if 'INDEX' in explain_upper and 'IDX_' in explain_upper:
                print(f"  WARNING: EXPLAIN shows index usage: {explain[:200]}")
            
            rows = await conn.fetch(
                """
                SELECT id, 1 - (embedding <=> $1::vector) as score
                FROM intelligence.chunks
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> $1::vector
                LIMIT $2
                """,
                embedding_str, top_k
            )
            
            # Re-enable index scans
            await conn.execute("SET enable_indexscan = on")
            await conn.execute("SET enable_bitmapscan = on")
            await conn.execute("SET enable_indexonlyscan = on")
        
        latency_ms = (time.perf_counter() - start_time) * 1000
        
        return RetrievalResult(
            query=query.text,
            method='exact',
            ef_search=None,
            top_k=top_k,
            chunk_ids=[r['id'] for r in rows],
            scores=[round(r['score'], 4) for r in rows],
            latency_ms=latency_ms,
            explain_plan=explain
        )
    
    async def run_hnsw_search(
        self,
        query: "BenchmarkQuery",
        ef_search: int,
        top_k: int = 10
    ) -> "RetrievalResult":
        """Run HNSW vector search with specific ef_search."""
        import time
        from shared.database import db_manager
        
        if not query.embedding:
            raise ValueError("Query must have embedding")
        
        start_time = time.perf_counter()
        
        async with db_manager.get_async_connection_context() as conn:
            # Set ef_search for this session
            await conn.execute(f"SET hnsw.ef_search = {ef_search}")
            
            embedding_str = f"[{','.join(str(x) for x in query.embedding)}]"
            
            # Get EXPLAIN to verify HNSW usage
            explain = await conn.fetchval("""
                EXPLAIN (FORMAT TEXT)
                SELECT id, 1 - (embedding <=> $1::vector) as score
                FROM intelligence.chunks
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> $1::vector
                LIMIT $2
            """, embedding_str, top_k)
            
            rows = await conn.fetch(
                """
                SELECT id, 1 - (embedding <=> $1::vector) as score
                FROM intelligence.chunks
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> $1::vector
                LIMIT $2
                """,
                embedding_str, top_k
            )
        
        latency_ms = (time.perf_counter() - start_time) * 1000
        
        return RetrievalResult(
            query=query.text,
            method='hnsw',
            ef_search=ef_search,
            top_k=top_k,
            chunk_ids=[r['id'] for r in rows],
            scores=[round(r['score'], 4) for r in rows],
            latency_ms=latency_ms,
            explain_plan=explain
        )
    
    async def run_hybrid_search(
        self,
        query: "BenchmarkQuery",
        top_k: int = 10
    ) -> Dict[str, Any]:
        """Run hybrid search (lexical + HNSW)."""
        import time
        from shared.hybrid_retriever import HybridRetriever
        from shared.database import document_repo
        
        start_time = time.perf_counter()
        
        # Create hybrid retriever
        retriever = HybridRetriever(
            document_repo=document_repo,
            lexical_weight=0.35,
            semantic_weight=0.65,
            use_rrf=False
        )
        
        # Get embedding
        embedding = None
        if query.embedding:
            embedding = query.embedding
        
        # Retrieve
        chunks = await retriever.retrieve(
            query=query.text,
            query_embedding=embedding,
            k=top_k
        )
        
        latency_ms = (time.perf_counter() - start_time) * 1000
        
        return {
            'query': query.text,
            'method': 'hybrid',
            'top_k': top_k,
            'chunk_ids': [c['id'] for c in chunks],
            'scores': [round(c.get('hybrid_score', 0), 4) for c in chunks],
            'latency_ms': round(latency_ms, 2),
            'from_lexical': sum(1 for c in chunks if c.get('from_lexical', False)),
            'from_vector': sum(1 for c in chunks if c.get('from_vector', False))
        }
    
    def calculate_overlap(
        self,
        baseline: "RetrievalResult",
        compare: "RetrievalResult"
    ) -> "OverlapMetrics":
        """Calculate overlap metrics between two result sets."""
        baseline_set = set(baseline.chunk_ids)
        compare_set = set(compare.chunk_ids)
        
        overlap = baseline_set & compare_set
        missing = baseline_set - compare_set
        
        recall = len(overlap) / len(baseline_set) if baseline_set else 0
        
        latency_delta_pct = (
            (compare.latency_ms - baseline.latency_ms) / baseline.latency_ms * 100
            if baseline.latency_ms > 0 else 0
        )
        
        return OverlapMetrics(
            query=baseline.query,
            baseline_method=baseline.method,
            compare_method=compare.method,
            baseline_ef=baseline.ef_search,
            compare_ef=compare.ef_search,
            top_k=baseline.top_k,
            baseline_results=len(baseline.chunk_ids),
            compare_results=len(compare.chunk_ids),
            overlap_count=len(overlap),
            overlap_ids=list(overlap),
            missing_ids=list(missing),
            recall=recall,
            latency_baseline_ms=baseline.latency_ms,
            latency_compare_ms=compare.latency_ms,
            latency_delta_pct=latency_delta_pct
        )
    
    def create_outlier(
        self,
        query: "BenchmarkQuery",
        ef_search: int,
        top_k: int,
        overlap: "OverlapMetrics",
        exact_result: "RetrievalResult",
        hnsw_result: "RetrievalResult"
    ) -> Optional["RecallOutlier"]:
        """Create outlier record if recall is unusually low."""
        # Threshold for outlier: recall below 80% for top-10
        if top_k == 10 and overlap.recall < 0.80:
            return RecallOutlier(
                query=query.text,
                ef_search=ef_search,
                top_k=top_k,
                recall=overlap.recall,
                exact_ids=exact_result.chunk_ids,
                hnsw_ids=hnsw_result.chunk_ids,
                missing_ids=overlap.missing_ids,
                exact_scores=exact_result.scores,
                hnsw_scores=hnsw_result.scores
            )
        return None
    
    async def benchmark_ef_search(
        self,
        queries: List["BenchmarkQuery"],
        ef_search: int,
        exact_results: List["RetrievalResult"],
        outlier_threshold: float = 0.80
    ) -> "EfSearchBenchmark":
        """Benchmark a specific ef_search value."""
        from statistics import mean
        
        print(f"\nBenchmarking ef_search={ef_search}...")
        
        benchmark = EfSearchBenchmark(ef_search=ef_search)
        
        for query in queries:
            for top_k in [5, 10]:
                # Get exact result for this query/top_k
                exact_result = next(
                    (r for r in exact_results if r.query == query.text and r.top_k == top_k),
                    None
                )
                
                if not exact_result:
                    continue
                
                # Run HNSW search
                hnsw_result = await self.run_hnsw_search(query, ef_search, top_k)
                benchmark.latencies_ms.append(hnsw_result.latency_ms)
                
                # Calculate overlap
                overlap = self.calculate_overlap(exact_result, hnsw_result)
                benchmark.overlap_metrics.append(overlap)
                
                # Check for outlier
                outlier = self.create_outlier(
                    query, ef_search, top_k, overlap, exact_result, hnsw_result
                )
                if outlier:
                    benchmark.outliers.append(outlier)
        
        print(f"  p50 latency: {benchmark.p50_latency_ms:.2f}ms")
        print(f"  p95 latency: {benchmark.p95_latency_ms:.2f}ms")
        print(f"  mean recall@5: {benchmark.mean_recall_top5:.2%}")
        print(f"  mean recall@10: {benchmark.mean_recall_top10:.2%}")
        print(f"  min recall@10: {benchmark.min_recall_top10:.2%}")
        if benchmark.outliers:
            print(f"  outliers: {len(benchmark.outliers)} queries with recall < 80%")
        
        return benchmark
    
    async def benchmark_hybrid(
        self, queries: List["BenchmarkQuery"]
    ) -> List[Dict[str, Any]]:
        """Benchmark hybrid search."""
        from statistics import mean
        
        print("\nBenchmarking hybrid search...")
        
        results = []
        for query in queries:
            for top_k in [5, 10]:
                result = await self.run_hybrid_search(query, top_k)
                results.append(result)
        
        avg_latency = mean(r['latency_ms'] for r in results)
        print(f"  Average latency: {avg_latency:.2f}ms")
        
        return results
    
    async def compare_ranking_modes(
        self, queries: List["BenchmarkQuery"]
    ) -> RankingModeBenchmark:
        """Compare weighted vs RRF ranking for hybrid search."""
        from shared.hybrid_retriever import HybridRetriever
        from shared.database import document_repo
        
        print("\n" + "="*60)
        print("COMPARING RANKING MODES: weighted vs RRF")
        print("="*60)
        
        comparison = RankingModeBenchmark()
        
        # Create retriever for comparison
        retriever = HybridRetriever(
            document_repo=document_repo,
            lexical_weight=0.35,
            semantic_weight=0.65,
            lexical_limit=30,
            vector_limit=40,
            use_rrf=False,  # Will toggle during comparison
            auto_tune_weights=False  # Keep weights constant for fair comparison
        )
        
        for query in queries:
            for k in [5, 10]:
                try:
                    # Run comparison
                    result = await retriever.compare_ranking_modes(
                        query=query.text,
                        query_embedding=query.embedding,
                        k=k
                    )
                    
                    comparison.comparisons.append(RankingComparisonResult(
                        query=query.text,
                        k=k,
                        weighted_results=result['weighted']['chunk_ids'],
                        rrf_results=result['rrf']['chunk_ids'],
                        overlap_count=result['comparison']['overlap_count'],
                        overlap_ids=result['comparison']['overlap_ids'],
                        weighted_only=result['comparison']['weighted_only'],
                        rrf_only=result['comparison']['rrf_only'],
                        weighted_latency_ms=result['weighted']['latency_ms'],
                        rrf_latency_ms=result['rrf']['latency_ms'],
                        latency_delta_ms=result['comparison']['latency_delta_ms'],
                        latency_delta_pct=result['comparison']['latency_delta_pct'],
                        lexical_candidates=result['lexical_candidates'],
                        vector_candidates=result['vector_candidates']
                    ))
                    
                except Exception as e:
                    print(f"  ERROR comparing ranking for '{query.text[:40]}...': {e}")
        
        # Print summary
        print(f"\n  Comparisons: {len(comparison.comparisons)}")
        print(f"  Mean overlap: {comparison.mean_overlap_pct:.1%}")
        print(f"  Weighted p95 latency: {comparison.weighted_p95_latency_ms:.2f}ms")
        print(f"  RRF p95 latency: {comparison.rrf_p95_latency_ms:.2f}ms")
        print(f"  Latency delta: {comparison.mean_latency_delta_pct:+.1f}%")
        
        return comparison
    
    def generate_ranking_recommendation(self, comparison: RankingModeBenchmark) -> str:
        """Generate recommendation for ranking mode."""
        if not comparison or not comparison.comparisons:
            return "No ranking comparison data available."
        
        # Simple policy: 
        # - If RRF latency is >10% worse, prefer weighted
        # - If overlap is >90% and RRF latency is similar, either is fine
        # - If RRF latency is better or similar, prefer RRF (simpler, no weight tuning)
        
        mean_overlap = comparison.mean_overlap_pct
        latency_delta_pct = comparison.mean_latency_delta_pct
        rrf_p95 = comparison.rrf_p95_latency_ms
        weighted_p95 = comparison.weighted_p95_latency_ms
        
        if latency_delta_pct > 10:
            # RRF is significantly slower
            recommendation = "weighted"
            reason = f"RRF latency is {latency_delta_pct:.1f}% higher than weighted"
        elif mean_overlap > 0.90 and abs(latency_delta_pct) < 5:
            # Results are very similar
            recommendation = "weighted"  # Keep current default (simpler)
            reason = f"Results are {mean_overlap:.1%} similar with minimal latency difference"
        else:
            # RRF is comparable or better
            recommendation = "rrf"
            reason = f"RRF offers comparable performance (latency delta: {latency_delta_pct:+.1f}%)"
        
        return f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        RANKING MODE RECOMMENDATION                            ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  Recommended ranking mode: {recommendation:10s}                                  ║
║                                                                              ║
║  Reasoning:                                                                  ║
║    {reason:70s}  ║
║                                                                              ║
║  Performance comparison:                                                     ║
║    • Result overlap:        {mean_overlap:5.1%}                                 ║
║    • Weighted p95 latency:  {weighted_p95:6.2f}ms                              ║
║    • RRF p95 latency:       {rrf_p95:6.2f}ms                              ║
║    • Latency delta:         {latency_delta_pct:+6.1f}%                               ║
║                                                                              ║
║  To apply this recommendation:                                               ║
║    export HYBRID_RANKING_MODE={recommendation}                                   ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
    
    async def compare_reranking(
        self, queries: List["BenchmarkQuery"]
    ) -> RerankingBenchmark:
        """Compare baseline hybrid vs reranker-enhanced retrieval."""
        from shared.reranker import Reranker
        from shared.hybrid_retriever import HybridRetriever
        from shared.database import document_repo
        
        print("\n" + "="*60)
        print("COMPARING RERANKING: baseline hybrid vs reranker")
        print("="*60)
        
        benchmark = RerankingBenchmark()
        
        # Create hybrid retriever
        hybrid_retriever = HybridRetriever(
            document_repo=document_repo,
            lexical_weight=0.35,
            semantic_weight=0.65,
            lexical_limit=30,
            vector_limit=40,
            use_rrf=False,
            auto_tune_weights=False
        )
        
        # Create reranker
        reranker = Reranker(
            hybrid_retriever=hybrid_retriever,
            enabled=True,
            top_n=30,
            final_k=10,
            model='cross_encoder',
            use_cross_encoder=True
        )
        
        for query in queries:
            for k in [5, 10]:
                try:
                    # Run comparison
                    result = await reranker.compare_with_baseline(
                        query=query.text,
                        query_embedding=query.embedding,
                        k=k
                    )
                    
                    benchmark.comparisons.append(RerankingComparisonResult(
                        query=query.text,
                        k=k,
                        baseline_results=result['baseline']['results'],
                        reranked_results=result['reranked']['results'],
                        overlap_count=result['comparison']['overlap_count'],
                        overlap_ids=result['comparison']['overlap_ids'],
                        baseline_only=result['comparison']['baseline_only'],
                        reranked_only=result['comparison']['reranked_only'],
                        baseline_latency_ms=result['baseline']['latency_ms'],
                        reranked_latency_ms=result['reranked']['latency_ms'],
                        latency_delta_ms=result['comparison']['latency_delta_ms'],
                        latency_delta_pct=result['comparison']['latency_delta_pct'],
                        position_changes=result['comparison']['position_changes'],
                        avg_position_change=result['comparison']['avg_position_change']
                    ))
                    
                except Exception as e:
                    print(f"  ERROR comparing reranking for '{query.text[:40]}...': {e}")
        
        # Print summary
        print(f"\n  Comparisons: {len(benchmark.comparisons)}")
        print(f"  Mean overlap: {benchmark.mean_overlap_pct:.1%}")
        print(f"  Baseline p95 latency: {benchmark.baseline_p95_latency_ms:.2f}ms")
        print(f"  Reranked p95 latency: {benchmark.reranked_p95_latency_ms:.2f}ms")
        print(f"  Latency delta: {benchmark.mean_latency_delta_pct:+.1f}%")
        print(f"  Mean position change: {benchmark.mean_position_change:.1f}")
        
        return benchmark
    
    def generate_reranking_recommendation(self, benchmark: RerankingBenchmark) -> str:
        """Generate recommendation for reranking."""
        if not benchmark or not benchmark.comparisons:
            return "No reranking comparison data available."
        
        mean_overlap = benchmark.mean_overlap_pct
        latency_delta_pct = benchmark.mean_latency_delta_pct
        mean_position_change = benchmark.mean_position_change
        reranked_p95 = benchmark.reranked_p95_latency_ms
        baseline_p95 = benchmark.baseline_p95_latency_ms
        
        # Decision policy:
        # - If latency increase > 50%, recommend disabled
        # - If overlap > 95% and position changes are small, keep disabled (simpler)
        # - If meaningful position changes (> 2 avg) and acceptable latency, enable
        
        if latency_delta_pct > 50:
            recommendation = "disabled"
            reason = f"Reranking adds {latency_delta_pct:.0f}% latency overhead"
        elif mean_overlap > 0.95 and mean_position_change < 1.5:
            recommendation = "disabled"
            reason = f"Results are {mean_overlap:.1%} similar with minimal reordering"
        elif mean_position_change >= 2.0 and latency_delta_pct < 50:
            recommendation = "enabled"
            reason = f"Meaningful reordering (avg {mean_position_change:.1f} positions) with acceptable latency cost"
        else:
            recommendation = "disabled"
            reason = f"Benefits unclear (overlap: {mean_overlap:.1%}, position change: {mean_position_change:.1f})"
        
        return f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        RERANKING RECOMMENDATION                               ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  Recommended reranking: {recommendation:15s}                             ║
║                                                                              ║
║  Reasoning:                                                                  ║
║    {reason:70s}  ║
║                                                                              ║
║  Performance comparison:                                                     ║
║    • Result overlap:           {mean_overlap:5.1%}                            ║
║    • Baseline p95 latency:     {baseline_p95:6.2f}ms                          ║
║    • Reranked p95 latency:     {reranked_p95:6.2f}ms                          ║
║    • Latency delta:            {latency_delta_pct:+6.1f}%                            ║
║    • Mean position change:     {mean_position_change:5.1f}                           ║
║                                                                              ║
║  To apply this recommendation:                                               ║
║    export RERANK_MODE={'always' if recommendation == 'enabled' else 'off'}                                     ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
    
    async def compare_selective_reranking(
        self, queries: List["BenchmarkQuery"]
    ) -> Dict[str, Any]:
        """Compare baseline, always-on, and selective reranking modes.
        
        This benchmark helps validate that selective reranking:
        1. Applies reranking less often than always mode
        2. Has lower latency than always mode
        3. Maintains quality on triggered queries
        """
        from shared.reranker import Reranker
        from shared.rerank_policy import RerankPolicy
        from shared.hybrid_retriever import HybridRetriever
        from shared.database import document_repo
        
        print("\n" + "="*70)
        print("COMPARING SELECTIVE RERANKING: baseline vs always vs selective")
        print("="*70)
        
        # Create hybrid retriever
        hybrid_retriever = HybridRetriever(
            document_repo=document_repo,
            lexical_weight=0.35,
            semantic_weight=0.65,
            lexical_limit=30,
            vector_limit=40,
            use_rrf=False,
            auto_tune_weights=False
        )
        
        # Create reranker with selective policy
        policy = RerankPolicy(
            mode='selective',
            score_gap_threshold=0.03,
            disagreement_threshold=0.40,
            min_top_score=0.55,
            complex_query_words=12
        )
        
        reranker = Reranker(
            hybrid_retriever=hybrid_retriever,
            policy=policy,
            top_n=30,
            final_k=10,
            model='cross_encoder',
            use_cross_encoder=True
        )
        
        results = []
        
        for query in queries:
            for k in [5, 10]:
                try:
                    comparison = await reranker.compare_selective_modes(
                        query=query.text,
                        query_embedding=query.embedding,
                        k=k
                    )
                    results.append(comparison)
                except Exception as e:
                    print(f"  ERROR comparing selective reranking for '{query.text[:40]}...': {e}")
        
        # Calculate aggregate statistics
        baseline_times = []
        always_times = []
        selective_times = []
        rerank_applied_count = 0
        total_queries = len(results)
        
        for r in results:
            baseline_times.append(r['comparison']['latency']['baseline_ms'])
            always_times.append(r['comparison']['latency']['always_ms'])
            selective_times.append(r['comparison']['latency']['selective_ms'])
            if r['comparison']['selective_savings']['rerank_skipped']:
                rerank_applied_count += 1
        
        import statistics
        
        selective_results = {
            'total_comparisons': total_queries,
            'rerank_triggered_count': total_queries - rerank_applied_count,
            'rerank_skipped_count': rerank_applied_count,
            'rerank_trigger_rate': (total_queries - rerank_applied_count) / total_queries if total_queries > 0 else 0,
            'latency': {
                'baseline_p95_ms': round(statistics.quantiles(baseline_times, n=20)[18] if len(baseline_times) >= 20 else max(baseline_times), 2) if baseline_times else 0,
                'always_p95_ms': round(statistics.quantiles(always_times, n=20)[18] if len(always_times) >= 20 else max(always_times), 2) if always_times else 0,
                'selective_p95_ms': round(statistics.quantiles(selective_times, n=20)[18] if len(selective_times) >= 20 else max(selective_times), 2) if selective_times else 0,
                'always_vs_baseline_pct': round(
                    (statistics.mean(always_times) - statistics.mean(baseline_times)) / statistics.mean(baseline_times) * 100, 1
                ) if baseline_times and statistics.mean(baseline_times) > 0 else 0,
                'selective_vs_baseline_pct': round(
                    (statistics.mean(selective_times) - statistics.mean(baseline_times)) / statistics.mean(baseline_times) * 100, 1
                ) if baseline_times and statistics.mean(baseline_times) > 0 else 0,
                'selective_vs_always_pct': round(
                    (statistics.mean(selective_times) - statistics.mean(always_times)) / statistics.mean(always_times) * 100, 1
                ) if always_times and statistics.mean(always_times) > 0 else 0,
            }
        }
        
        # Print summary
        print(f"\n  Comparisons: {selective_results['total_comparisons']}")
        print(f"  Rerank triggered: {selective_results['rerank_triggered_count']} ({selective_results['rerank_trigger_rate']:.1%})")
        print(f"  Rerank skipped: {selective_results['rerank_skipped_count']}")
        print(f"\n  Latency Comparison:")
        print(f"    Baseline p95:  {selective_results['latency']['baseline_p95_ms']:.2f}ms")
        print(f"    Always p95:    {selective_results['latency']['always_p95_ms']:.2f}ms ({selective_results['latency']['always_vs_baseline_pct']:+.1f}%)")
        print(f"    Selective p95: {selective_results['latency']['selective_p95_ms']:.2f}ms ({selective_results['latency']['selective_vs_baseline_pct']:+.1f}%)")
        print(f"\n  Selective vs Always: {selective_results['latency']['selective_vs_always_pct']:+.1f}% latency")
        
        return selective_results
    
    async def evaluate_rag_quality(
        self,
        queries: List["BenchmarkQuery"],
        ef_search_values: List[int],
        sample_size: int = 10
    ) -> List["RAGQualityResult"]:
        """Evaluate RAG output quality at different ef_search values."""
        import time
        from shared.database import db_manager, document_repo
        
        print(f"\nEvaluating RAG quality (sample size: {sample_size})...")
        
        results = []
        sample_queries = queries[:sample_size]
        
        for ef_search in ef_search_values:
            print(f"  ef_search={ef_search}...")
            
            for query in sample_queries:
                try:
                    start_time = time.perf_counter()
                    
                    # Set ef_search
                    async with db_manager.get_async_connection_context() as conn:
                        await conn.execute(f"SET hnsw.ef_search = {ef_search}")
                    
                    # Generate embedding
                    embedding = await self.ollama.generate_embedding(query.text)
                    
                    # Get context
                    context_result = await document_repo.get_rag_context(
                        embedding=embedding,
                        limit=5,
                        similarity_threshold=0.0
                    )
                    
                    # Generate answer
                    if context_result and context_result.get('context'):
                        answer = await self.ollama.generate_response(
                            prompt=query.text,
                            context=context_result['context']
                        )
                        answer_generated = bool(answer and len(answer) > 10)
                        citations_present = '[' in answer if answer else False
                        chunks_used = len(context_result.get('document_ids', []))
                        token_count = len(context_result['context'].split())
                    else:
                        answer_generated = False
                        citations_present = False
                        chunks_used = 0
                        token_count = 0
                    
                    generation_time_ms = (time.perf_counter() - start_time) * 1000
                    
                    results.append(RAGQualityResult(
                        query=query.text,
                        ef_search=ef_search,
                        answer_generated=answer_generated,
                        citations_present=citations_present,
                        chunks_used=chunks_used,
                        token_count=token_count,
                        generation_time_ms=generation_time_ms
                    ))
                    
                except Exception as e:
                    print(f"    ERROR for '{query.text[:40]}...': {e}")
                    results.append(RAGQualityResult(
                        query=query.text,
                        ef_search=ef_search,
                        answer_generated=False,
                        citations_present=False,
                        chunks_used=0,
                        token_count=0,
                        generation_time_ms=0
                    ))
        
        return results
    
    def generate_recommendation(
        self, 
        report: "BenchmarkReport",
        min_recall_threshold: float = 0.93
    ) -> str:
        """Generate a recommendation based on benchmark results.
        
        Uses a two-stage approach:
        1. Filter to settings meeting minimum recall threshold
        2. Choose lowest latency among qualifying settings
        """
        if not report.hnsw_benchmarks:
            return "No HNSW benchmark data available for recommendation."
        
        # Stage 1: Filter by minimum recall threshold
        qualifying = [
            b for b in report.hnsw_benchmarks 
            if b.mean_recall_top10 >= min_recall_threshold
        ]
        
        if not qualifying:
            # No setting meets threshold - report the best available
            best_by_recall = max(report.hnsw_benchmarks, key=lambda x: x.mean_recall_top10)
            return f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                           BENCHMARK RECOMMENDATION                            ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  ⚠️  NO SETTING MEETS MINIMUM RECALL THRESHOLD ({min_recall_threshold:.0%})    ║
║                                                                              ║
║  Best available: ef_search={best_by_recall.ef_search}                          ║
║    • Mean recall@10: {best_by_recall.mean_recall_top10:.1%} (below threshold) ║
║    • p95 latency: {best_by_recall.p95_latency_ms:.1f}ms                       ║
║                                                                              ║
║  Consider:                                                                   ║
║    • Increasing HNSW 'm' parameter (requires index rebuild)                  ║
║    • Using higher ef_search values (increases latency)                       ║
║    • Checking for data quality issues (outliers listed in report)            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
        
        # Stage 2: Among qualifying, choose best latency
        best = min(qualifying, key=lambda x: x.p95_latency_ms)
        
        # Build detailed comparison
        lines = []
        for b in sorted(report.hnsw_benchmarks, key=lambda x: x.ef_search):
            marker = " ← RECOMMENDED" if b.ef_search == best.ef_search else ""
            meets = "✓" if b.mean_recall_top10 >= min_recall_threshold else "✗"
            lines.append(
                f"║    {meets} ef_search={b.ef_search:3d}: "
                f"recall={b.mean_recall_top10:5.1%}, "
                f"p95={b.p95_latency_ms:6.1f}ms{marker:20s}║"
            )
        
        recommendation = f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                           BENCHMARK RECOMMENDATION                            ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  Recommended default: ef_search={best.ef_search}                               ║
║                                                                              ║
║  Selection criteria:                                                         ║
║    • Minimum recall threshold: {min_recall_threshold:.0%}                                      ║
║    • Optimization: Lowest p95 latency among qualifying settings              ║
║                                                                              ║
║  Performance at recommendation:                                              ║
║    • Mean recall@10: {best.mean_recall_top10:.1%}                            ║
║    • Min recall@10:  {best.min_recall_top10:.1%}                             ║
║    • p95 latency:    {best.p95_latency_ms:.1f}ms                             ║
║                                                                              ║
║  All results (✓ = meets recall threshold):                                   ║
"""
        for line in lines:
            recommendation += line + "\n"
        
        if best.outliers:
            recommendation += f"║                                                                              ║\n"
            recommendation += f"║  ⚠️  {len(best.outliers)} outlier queries with recall < 80% (see JSON for details) ║\n"
        
        recommendation += """║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
        
        return recommendation
    
    def export_json(self, report: "BenchmarkReport", filename: str):
        """Export benchmark report to JSON."""
        import json
        filepath = self.output_dir / filename
        with open(filepath, 'w') as f:
            json.dump(report.to_dict(), f, indent=2)
        print(f"\nExported JSON: {filepath}")
    
    def export_csv(self, report: "BenchmarkReport", filename: str):
        """Export HNSW benchmark summary to CSV."""
        import csv
        filepath = self.output_dir / filename
        
        with open(filepath, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'search_type', 'ef_search', 'p50_latency_ms', 'p95_latency_ms', 
                'max_latency_ms', 'mean_latency_ms', 'mean_recall_top5', 
                'mean_recall_top10', 'min_recall_top10', 'query_count', 'outlier_count'
            ])
            
            # Exact baseline
            if report.exact_benchmark:
                writer.writerow([
                    'exact', 'N/A',
                    round(report.exact_benchmark.p50_latency_ms, 2),
                    round(report.exact_benchmark.p95_latency_ms, 2),
                    round(report.exact_benchmark.max_latency_ms, 2),
                    round(report.exact_benchmark.mean_latency_ms, 2),
                    '1.0000', '1.0000', '1.0000',
                    len(report.exact_benchmark.latencies_ms), 0
                ])
            
            # HNSW results
            for b in report.hnsw_benchmarks:
                summary = b.to_summary_dict()
                writer.writerow([
                    'hnsw', summary['ef_search'],
                    summary['p50_latency_ms'],
                    summary['p95_latency_ms'],
                    summary['max_latency_ms'],
                    summary['mean_latency_ms'],
                    summary['mean_recall_top5'],
                    summary['mean_recall_top10'],
                    summary['min_recall_top10'],
                    summary['query_count'],
                    summary['outlier_count']
                ])
            
            # Hybrid summary if available
            if report.hybrid_results:
                import statistics
                hybrid_latencies = [r['latency_ms'] for r in report.hybrid_results]
                writer.writerow([
                    'hybrid', 'N/A',
                    round(statistics.median(hybrid_latencies), 2),
                    round(sorted(hybrid_latencies)[int(len(hybrid_latencies)*0.95)], 2),
                    round(max(hybrid_latencies), 2),
                    round(statistics.mean(hybrid_latencies), 2),
                    'N/A', 'N/A', 'N/A',
                    len(hybrid_latencies), 0
                ])
        
        print(f"Exported CSV: {filepath}")
    
    async def run(
        self,
        queries: List[str],
        ef_search_values: List[int],
        skip_hybrid: bool = False,
        eval_rag: bool = False,
        rag_sample_size: int = 10,
        min_recall_threshold: float = 0.93,
        cache_warm: bool = False,
        compare_ranking: bool = False,
        compare_reranking: bool = False,
        compare_selective_reranking: bool = False
    ) -> "BenchmarkReport":
        """Run complete benchmark suite."""
        import asyncio
        import time
        from datetime import datetime
        from statistics import mean
        from shared.database import db_manager, document_repo
        from shared.hybrid_retriever import HybridRetriever
        
        timestamp = datetime.utcnow().isoformat()
        print(f"\n{'='*60}")
        print(f"HNSW Vector Index Benchmark")
        print(f"Started: {timestamp}")
        print(f"{'='*60}")
        
        # Get environment details
        print("\nCapturing environment details...")
        environment = await self.get_environment(cache_warm=cache_warm)
        print(f"  Database: {environment.database_chunks:,} chunks, {environment.database_documents:,} documents")
        print(f"  PostgreSQL: {environment.postgresql_version}")
        print(f"  pgvector: {environment.pgvector_version}")
        print(f"  HNSW: m={environment.hnsw_m}, ef_construction={environment.hnsw_ef_construction}")
        
        if environment.database_chunks == 0:
            print("ERROR: No chunks in database. Please ingest documents first.")
            sys.exit(1)
        
        # Generate embeddings
        benchmark_queries = await self.generate_embeddings(queries)
        
        if not benchmark_queries:
            print("ERROR: No valid queries to benchmark.")
            sys.exit(1)
        
        # Initialize report
        report = BenchmarkReport(
            timestamp=timestamp,
            environment=environment,
            queries=queries,
            ef_search_values=ef_search_values
        )
        
        # Step 1: Run exact search (baseline)
        print("\n" + "="*60)
        print("STEP 1: Exact (brute-force) vector search")
        print("="*60)
        
        exact_results = []
        exact_latencies = []
        
        for query in benchmark_queries:
            for top_k in [5, 10]:
                try:
                    result = await self.run_exact_search(query, top_k)
                    exact_results.append(result)
                    exact_latencies.append(result.latency_ms)
                    print(f"  {query.text[:40]:40s} k={top_k}: {result.latency_ms:6.2f}ms")
                except Exception as e:
                    print(f"  ERROR: {e}")
        
        if exact_latencies:
            report.exact_benchmark = EfSearchBenchmark(
                ef_search=-1,  # Marker for exact
                latencies_ms=exact_latencies,
                overlap_metrics=[]
            )
            print(f"\nExact search: p50={report.exact_benchmark.p50_latency_ms:.2f}ms, "
                  f"p95={report.exact_benchmark.p95_latency_ms:.2f}ms")
        
        # Step 2: Benchmark each ef_search value
        print("\n" + "="*60)
        print("STEP 2: HNSW ef_search sweep")
        print("="*60)
        
        for ef_search in ef_search_values:
            benchmark = await self.benchmark_ef_search(
                benchmark_queries, ef_search, exact_results
            )
            report.hnsw_benchmarks.append(benchmark)
        
        # Build vector search summary
        if report.hnsw_benchmarks:
            best_recall = max(report.hnsw_benchmarks, key=lambda x: x.mean_recall_top10)
            best_latency = min(report.hnsw_benchmarks, key=lambda x: x.p95_latency_ms)
            report.vector_summary = {
                'best_recall_ef_search': best_recall.ef_search,
                'best_recall_value': round(best_recall.mean_recall_top10, 4),
                'best_latency_ef_search': best_latency.ef_search,
                'best_latency_p95_ms': round(best_latency.p95_latency_ms, 2)
            }
        
        # Step 3: Hybrid search benchmark
        if not skip_hybrid:
            print("\n" + "="*60)
            print("STEP 3: Hybrid search (lexical + HNSW)")
            print("="*60)
            report.hybrid_results = await self.benchmark_hybrid(benchmark_queries)
            
            # Build hybrid summary
            if report.hybrid_results:
                import statistics
                latencies = [r['latency_ms'] for r in report.hybrid_results]
                report.hybrid_summary = {
                    'mean_latency_ms': round(statistics.mean(latencies), 2),
                    'p95_latency_ms': round(sorted(latencies)[int(len(latencies)*0.95)], 2),
                    'query_count': len(report.hybrid_results)
                }
        
        # Step 4: Ranking mode comparison
        if compare_ranking:
            print("\n" + "="*60)
            print("STEP 4: Ranking mode comparison (weighted vs RRF)")
            print("="*60)
            report.ranking_comparison = await self.compare_ranking_modes(benchmark_queries)
        
        # Step 5: RAG quality evaluation
        # Step 5: Reranking comparison
        if compare_reranking:
            print("\n" + "="*60)
            print("STEP 5: Reranking comparison (baseline vs reranker)")
            print("="*60)
            report.reranking_comparison = await self.compare_reranking(benchmark_queries)
        
        # Step 5b: Selective reranking comparison
        if compare_selective_reranking:
            print("\n" + "="*60)
            print("STEP 5b: Selective reranking comparison (baseline vs always vs selective)")
            print("="*60)
            report.selective_reranking_comparison = await self.compare_selective_reranking(benchmark_queries)
        
        # Step 6: RAG quality evaluation
        if eval_rag:
            print("\n" + "="*60)
            print("STEP 6: RAG quality evaluation")
            print("="*60)
            report.rag_quality = await self.evaluate_rag_quality(
                benchmark_queries, ef_search_values, rag_sample_size
            )
        
        # Step 7: Generate recommendations
        print("\n" + "="*60)
        print("STEP 7: Generate recommendations")
        print("="*60)
        report.recommendation = self.generate_recommendation(report, min_recall_threshold)
        
        if compare_ranking and report.ranking_comparison:
            report.ranking_recommendation = self.generate_ranking_recommendation(report.ranking_comparison)
        
        if compare_reranking and report.reranking_comparison:
            report.reranking_recommendation = self.generate_reranking_recommendation(report.reranking_comparison)
        
        # Step 8: Export results
        print("\n" + "="*60)
        print("STEP 8: Export results")
        print("="*60)
        
        json_filename = f"hnsw_benchmark_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        csv_filename = f"hnsw_benchmark_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        self.export_json(report, json_filename)
        self.export_csv(report, csv_filename)
        
        # Display recommendations
        print("\n" + "="*60)
        print("HNSW RECOMMENDATION")
        print("="*60)
        print(report.recommendation)
        
        if report.ranking_recommendation:
            print("\n" + "="*60)
            print("RANKING MODE RECOMMENDATION")
            print("="*60)
            print(report.ranking_recommendation)
        
        if report.reranking_recommendation:
            print("\n" + "="*60)
            print("RERANKING RECOMMENDATION")
            print("="*60)
            print(report.reranking_recommendation)
        
        return report


def load_queries_from_file(filepath: str) -> List[str]:
    """Load benchmark queries from JSON file."""
    with open(filepath, 'r') as f:
        data = json.load(f)
    
    if isinstance(data, list):
        return data
    elif isinstance(data, dict) and 'queries' in data:
        return data['queries']
    else:
        raise ValueError("Query file must be a list of strings or object with 'queries' key")


def main():
    import asyncio
    
    parser = argparse.ArgumentParser(
        description="Benchmark HNSW vector search performance",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with default queries
  python scripts/benchmark_hnsw.py
  
  # Custom queries file
  python scripts/benchmark_hnsw.py --queries-file queries.json
  
  # Specific ef_search values
  python scripts/benchmark_hnsw.py --ef-search 20 40 60 80 100 150
  
  # Set minimum recall threshold (default: 0.93)
  python scripts/benchmark_hnsw.py --min-recall 0.90
  
  # Output to specific directory
  python scripts/benchmark_hnsw.py --output-dir ./results
  
  # Skip hybrid search (faster)
  python scripts/benchmark_hnsw.py --skip-hybrid
  
  # Include RAG quality evaluation
  python scripts/benchmark_hnsw.py --eval-rag --rag-sample-size 15
  
  # Mark benchmark as cache-warm
  python scripts/benchmark_hnsw.py --cache-warm
  
  # Compare weighted vs RRF ranking modes
  python scripts/benchmark_hnsw.py --compare-ranking
  
  # Compare baseline hybrid vs with reranker
  python scripts/benchmark_hnsw.py --compare-reranking
  
  # Compare selective reranking (baseline vs always vs selective)
  python scripts/benchmark_hnsw.py --compare-selective-reranking
        """
    )
    
    parser.add_argument(
        '--queries-file',
        type=str,
        help='JSON file containing benchmark queries (default: use built-in queries)'
    )
    
    parser.add_argument(
        '--ef-search',
        type=int,
        nargs='+',
        default=[20, 40, 80, 100],
        help='ef_search values to benchmark (default: 20 40 80 100)'
    )
    
    parser.add_argument(
        '--min-recall',
        type=float,
        default=0.93,
        help='Minimum recall threshold for recommendation (default: 0.93)'
    )
    
    parser.add_argument(
        '--output-dir',
        type=str,
        default='./benchmark_results',
        help='Directory for output files (default: ./benchmark_results)'
    )
    
    parser.add_argument(
        '--skip-hybrid',
        action='store_true',
        help='Skip hybrid search benchmark'
    )
    
    parser.add_argument(
        '--eval-rag',
        action='store_true',
        help='Evaluate RAG output quality'
    )
    
    parser.add_argument(
        '--rag-sample-size',
        type=int,
        default=10,
        help='Number of queries for RAG evaluation (default: 10)'
    )
    
    parser.add_argument(
        '--cache-warm',
        action='store_true',
        help='Mark benchmark as running with warm cache'
    )
    
    parser.add_argument(
        '--compare-ranking',
        action='store_true',
        help='Compare weighted vs RRF ranking modes for hybrid search'
    )
    
    parser.add_argument(
        '--compare-reranking',
        action='store_true',
        help='Compare baseline hybrid vs reranker-enhanced retrieval'
    )
    
    parser.add_argument(
        '--compare-selective-reranking',
        action='store_true',
        help='Compare baseline vs always-on vs selective reranking modes'
    )
    
    args = parser.parse_args()
    
    # Load queries
    if args.queries_file:
        queries = load_queries_from_file(args.queries_file)
        print(f"Loaded {len(queries)} queries from {args.queries_file}")
    else:
        queries = DEFAULT_BENCHMARK_QUERIES
        print(f"Using {len(queries)} default benchmark queries")
    
    # Run benchmark
    benchmark = HNSWBenchmark(output_dir=args.output_dir)
    
    try:
        asyncio.run(benchmark.run(
            queries=queries,
            ef_search_values=args.ef_search,
            skip_hybrid=args.skip_hybrid,
            eval_rag=args.eval_rag,
            rag_sample_size=args.rag_sample_size,
            min_recall_threshold=args.min_recall,
            cache_warm=args.cache_warm,
            compare_ranking=args.compare_ranking,
            compare_reranking=args.compare_reranking,
            compare_selective_reranking=args.compare_selective_reranking
        ))
    except KeyboardInterrupt:
        print("\n\nBenchmark interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nBenchmark failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()

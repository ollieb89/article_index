import json
import os
import re
import asyncpg
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import List, Dict, Any, Optional, Tuple
import asyncio
from contextlib import asynccontextmanager


def _normalize_database_url(url: str) -> str:
    """Convert SQLAlchemy-style URLs to postgresql:// for asyncpg/psycopg2."""
    if not url:
        return url
    # postgresql+psycopg, postgresql+asyncpg -> postgresql
    return re.sub(r"^postgresql\+[a-z0-9]+", "postgresql", url, flags=re.IGNORECASE)


class DatabaseManager:
    """Database connection manager for PostgreSQL with async and sync support."""

    def __init__(self):
        raw_url = os.getenv("DATABASE_URL")
        if not raw_url:
            raise ValueError("DATABASE_URL environment variable is required")
        self.database_url = _normalize_database_url(raw_url)

    # Async connection methods
    async def get_async_connection(self) -> asyncpg.Connection:
        """Get async database connection."""
        return await asyncpg.connect(self.database_url)

    @asynccontextmanager
    async def get_async_connection_context(self):
        """Context manager for async database connection."""
        conn = await self.get_async_connection()
        try:
            yield conn
        finally:
            await conn.close()

    # Sync connection methods
    def get_sync_connection(self):
        """Get sync database connection."""
        return psycopg2.connect(self.database_url, cursor_factory=RealDictCursor)

    def get_sync_connection_context(self):
        """Context manager for sync database connection."""
        conn = self.get_sync_connection()
        try:
            yield conn
        finally:
            conn.close()

    async def set_search_params(self, ef_search: int = 100) -> None:
        """Set HNSW search parameters for the session.
        
        Higher ef_search = better recall, slower queries.
        Default is usually 40, range is 1-1000.
        
        Args:
            ef_search: HNSW exploration factor (1-1000)
        
        Example:
            await db_manager.set_search_params(ef_search=100)  # Better recall
            await db_manager.set_search_params(ef_search=40)   # Faster queries
        """
        async with self.get_async_connection_context() as conn:
            await conn.execute(f"SET hnsw.ef_search = {ef_search}")


class DocumentRepository:
    """Repository for document and chunk operations."""

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    # Document operations
    async def create_document(
        self,
        title: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        embedding: Optional[List[float]] = None,
        content_hash: Optional[str] = None,
    ) -> int:
        """Create a new document."""
        async with self.db.get_async_connection_context() as conn:
            query = """
                INSERT INTO intelligence.documents
                (title, content, metadata, embedding, content_hash)
                VALUES ($1, $2, $3::jsonb, $4, $5)
                RETURNING id
            """

            embedding_str = None
            if embedding:
                embedding_str = f"[{','.join(str(x) for x in embedding)}]"

            result = await conn.fetchrow(
                query,
                title,
                content,
                json.dumps(metadata or {}),
                embedding_str,
                content_hash,
            )
            return result["id"]

    async def get_document_by_content_hash(
        self, content_hash: str
    ) -> Optional[Dict[str, Any]]:
        """Get document by content hash (for duplicate detection)."""
        async with self.db.get_async_connection_context() as conn:
            query = """
                SELECT id, title, content, metadata, created_at, updated_at
                FROM intelligence.documents
                WHERE content_hash = $1
            """
            row = await conn.fetchrow(query, content_hash)
            return dict(row) if row else None

    async def get_document(self, document_id: int) -> Optional[Dict[str, Any]]:
        """Get document by ID."""
        async with self.db.get_async_connection_context() as conn:
            query = """
                SELECT id, title, content, metadata, created_at, updated_at
                FROM intelligence.documents
                WHERE id = $1
            """
            result = await conn.fetchrow(query, document_id)
            return dict(result) if result else None

    async def update_document_embedding(
        self,
        document_id: int,
        embedding: List[float]
    ) -> bool:
        """Update document embedding."""
        async with self.db.get_async_connection_context() as conn:
            query = """
                UPDATE intelligence.documents
                SET embedding = $1, updated_at = NOW()
                WHERE id = $2
            """
            embedding_str = f"[{','.join(str(x) for x in embedding)}]"
            result = await conn.execute(query, embedding_str, document_id)
            return result == "UPDATE 1"

    async def list_documents(
        self,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """List documents with pagination."""
        async with self.db.get_async_connection_context() as conn:
            query = """
                SELECT id, title, created_at, updated_at,
                       CASE WHEN embedding IS NOT NULL THEN true ELSE false END as has_embedding
                FROM intelligence.documents
                ORDER BY created_at DESC
                LIMIT $1 OFFSET $2
            """
            results = await conn.fetch(query, limit, offset)
            return [dict(row) for row in results]

    # Chunk operations
    async def create_chunks(
        self,
        document_id: int,
        chunks: List[Dict[str, Any]],
        title: Optional[str] = None
    ) -> List[int]:
        """Create multiple chunks for a document.
        
        Args:
            document_id: Parent document ID
            chunks: List of chunk data dicts with content, embedding, chunk_index
            title: Document title (for backfill, chunks can also include title)
        """
        async with self.db.get_async_connection_context() as conn:
            chunk_ids = []

            for chunk_data in chunks:
                # Use chunk's title if provided, else fall back to parameter
                chunk_title = chunk_data.get('title', title)
                
                query = """
                    INSERT INTO intelligence.chunks
                    (document_id, content, embedding, chunk_index, title)
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING id
                """

                embedding_str = None
                if chunk_data.get('embedding'):
                    embedding_str = f"[{','.join(str(x) for x in chunk_data['embedding'])}]"

                result = await conn.fetchrow(
                    query,
                    document_id,
                    chunk_data['content'],
                    embedding_str,
                    chunk_data['chunk_index'],
                    chunk_title
                )
                chunk_ids.append(result['id'])

            return chunk_ids

    async def get_document_chunks(self, document_id: int) -> List[Dict[str, Any]]:
        """Get all chunks for a document."""
        async with self.db.get_async_connection_context() as conn:
            query = """
                SELECT id, content, chunk_index, created_at,
                       CASE WHEN embedding IS NOT NULL THEN true ELSE false END as has_embedding
                FROM intelligence.chunks
                WHERE document_id = $1
                ORDER BY chunk_index
            """
            results = await conn.fetch(query, document_id)
            return [dict(row) for row in results]

    async def update_chunk_embedding(
        self,
        chunk_id: int,
        embedding: List[float]
    ) -> bool:
        """Update chunk embedding."""
        async with self.db.get_async_connection_context() as conn:
            query = """
                UPDATE intelligence.chunks
                SET embedding = $1
                WHERE id = $2
            """
            embedding_str = f"[{','.join(str(x) for x in embedding)}]"
            result = await conn.execute(query, embedding_str, chunk_id)
            return result == "UPDATE 1"

    # Search operations
    async def find_similar_chunks(
        self,
        embedding: List[float],
        limit: int = 5,
        similarity_threshold: float = 0.7
    ) -> List[Dict[str, Any]]:
        """Find similar chunks using vector similarity."""
        async with self.db.get_async_connection_context() as conn:
            query = """
                SELECT * FROM intelligence.find_similar_chunks(
                    $1::vector, $2, $3
                )
            """
            embedding_str = f"[{','.join(str(x) for x in embedding)}]"
            results = await conn.fetch(query, embedding_str, limit, similarity_threshold)
            return [dict(row) for row in results]

    async def find_similar_documents(
        self,
        embedding: List[float],
        limit: int = 5,
        similarity_threshold: float = 0.7
    ) -> List[Dict[str, Any]]:
        """Find similar documents using vector similarity."""
        async with self.db.get_async_connection_context() as conn:
            query = """
                SELECT * FROM intelligence.find_similar_documents(
                    $1::vector, $2, $3
                )
            """
            embedding_str = f"[{','.join(str(x) for x in embedding)}]"
            results = await conn.fetch(query, embedding_str, limit, similarity_threshold)
            return [dict(row) for row in results]

    async def get_rag_context(
        self,
        embedding: List[float],
        limit: int = 5,
        similarity_threshold: float = 0.7
    ) -> Optional[Dict[str, Any]]:
        """Get aggregated context for RAG queries."""
        async with self.db.get_async_connection_context() as conn:
            query = """
                SELECT * FROM intelligence.get_rag_context(
                    $1::vector, $2, $3
                )
            """
            embedding_str = f"[{','.join(str(x) for x in embedding)}]"
            results = await conn.fetch(query, embedding_str, limit, similarity_threshold)
            return dict(results[0]) if results else None

    # Hybrid search operations (Phase 2: Retrieval Design)
    async def find_similar_chunks_lexical(
        self,
        query: str,
        limit: int = 30
    ) -> List[Dict[str, Any]]:
        """Find similar chunks using PostgreSQL full-text search.
        
        Args:
            query: Search query text
            limit: Maximum number of results (default 30)
            
        Returns:
            List of chunks with lexical_score
        """
        async with self.db.get_async_connection_context() as conn:
            rows = await conn.fetch(
                "SELECT * FROM intelligence.find_similar_chunks_lexical($1, $2)",
                query, limit
            )
            return [dict(row) for row in rows]

    async def find_similar_chunks_semantic(
        self,
        embedding: List[float],
        limit: int = 40,
        similarity_threshold: float = 0.0
    ) -> List[Dict[str, Any]]:
        """Find similar chunks using vector similarity (for hybrid search).
        
        Args:
            embedding: Query embedding vector
            limit: Maximum number of results (default 40)
            similarity_threshold: Minimum similarity score (default 0.0 for hybrid)
            
        Returns:
            List of chunks with semantic_score
        """
        async with self.db.get_async_connection_context() as conn:
            embedding_str = f"[{','.join(str(x) for x in embedding)}]"
            rows = await conn.fetch(
                "SELECT * FROM intelligence.find_similar_chunks_semantic($1::vector, $2, $3)",
                embedding_str, limit, similarity_threshold
            )
            return [dict(row) for row in rows]

    # Utility operations
    async def get_stats(self) -> Dict[str, Any]:
        """Get database statistics."""
        async with self.db.get_async_connection_context() as conn:
            queries = {
                'total_documents': "SELECT COUNT(*) as count FROM intelligence.documents",
                'total_chunks': "SELECT COUNT(*) as count FROM intelligence.chunks",
                'documents_with_embeddings': """
                    SELECT COUNT(*) as count FROM intelligence.documents
                    WHERE embedding IS NOT NULL
                """,
                'chunks_with_embeddings': """
                    SELECT COUNT(*) as count FROM intelligence.chunks
                    WHERE embedding IS NOT NULL
                """
            }

            stats = {}
            for key, query in queries.items():
                result = await conn.fetchrow(query)
                stats[key] = result['count']

            return stats

    async def get_index_stats(self) -> Dict[str, Any]:
        """Get vector index statistics including HNSW info.
        
        Returns:
            Dict with index names, sizes, and HNSW parameters
        """
        async with self.db.get_async_connection_context() as conn:
            # Get embedding-related indexes
            index_rows = await conn.fetch("""
                SELECT 
                    indexname,
                    pg_size_pretty(pg_relation_size(indexrelid)) as size,
                    pg_relation_size(indexrelid) as size_bytes,
                    indexdef
                FROM pg_stat_user_indexes 
                WHERE schemaname = 'intelligence' 
                  AND indexname LIKE '%embedding%'
                ORDER BY indexname
            """)
            
            indexes = []
            hnsw_detected = False
            
            for row in index_rows:
                index_info = {
                    'name': row['indexname'],
                    'size': row['size'],
                    'size_bytes': row['size_bytes'],
                    'definition': row['indexdef']
                }
                
                # Detect HNSW parameters from index definition
                if 'hnsw' in row['indexdef'].lower():
                    hnsw_detected = True
                    import re
                    m_match = re.search(r'm\s*=\s*(\d+)', row['indexdef'])
                    ef_match = re.search(r'ef_construction\s*=\s*(\d+)', row['indexdef'])
                    index_info['type'] = 'hnsw'
                    index_info['m'] = int(m_match.group(1)) if m_match else None
                    index_info['ef_construction'] = int(ef_match.group(1)) if ef_match else None
                elif 'ivfflat' in row['indexdef'].lower():
                    index_info['type'] = 'ivfflat'
                else:
                    index_info['type'] = 'unknown'
                
                indexes.append(index_info)
            
            # Get current ef_search setting
            ef_search_row = await conn.fetchrow("SHOW hnsw.ef_search")
            current_ef_search = ef_search_row['hnsw.ef_search'] if ef_search_row else '40'
            
            return {
                'indexes': indexes,
                'hnsw_enabled': hnsw_detected,
                'ef_search': int(current_ef_search) if current_ef_search.isdigit() else 40,
                'estimated_memory_mb': sum(idx.get('size_bytes', 0) for idx in indexes) / (1024 * 1024)
            }


# Convenience function for direct database access
async def get_db_connection():
    """Get async database connection."""
    return await db_manager.get_async_connection()


class PolicyRepository:
    """Repository for policy and telemetry operations."""

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    async def get_active_policy(self) -> Optional[Dict[str, Any]]:
        """Get the currently active policy."""
        async with self.db.get_async_connection_context() as conn:
            query = """
                SELECT version, thresholds, routing_rules, contextual_thresholds, latency_budgets
                FROM intelligence.policy_registry
                WHERE is_active = TRUE
                LIMIT 1
            """
            row = await conn.fetchrow(query)
            return dict(row) if row else None

    async def list_policies(self, limit: int = 10) -> List[Dict[str, Any]]:
        """List policies in the registry."""
        async with self.db.get_async_connection_context() as conn:
            query = """
                SELECT version, is_active, thresholds, routing_rules, contextual_thresholds, latency_budgets, created_at
                FROM intelligence.policy_registry
                ORDER BY created_at DESC
                LIMIT $1
            """
            rows = await conn.fetch(query, limit)
            return [dict(row) for row in rows]

    async def log_telemetry(self, trace_data: Dict[str, Any]) -> str:
        """Log a policy telemetry trace.
        
        Args:
            trace_data: Dict containing PolicyTrace fields
            
        Returns:
            The query_id of the logged trace.
        """
        async with self.db.get_async_connection_context() as conn:
            query = """
                INSERT INTO intelligence.policy_telemetry (
                    query_id, query_text, query_type, confidence_score,
                    confidence_band, action_taken, execution_path,
                    retrieval_state, policy_version, retrieval_mode, 
                    chunks_retrieved, latency_ms, evidence_shape, metadata
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb, $14::jsonb
                )
                RETURNING query_id
            """
            result = await conn.fetchrow(
                query,
                trace_data.get('query_id'),
                trace_data.get('query_text'),
                trace_data.get('query_type'),
                trace_data.get('confidence_score'),
                trace_data.get('confidence_band'),
                trace_data.get('action_taken'),
                trace_data.get('execution_path'),
                trace_data.get('retrieval_state'),
                trace_data.get('policy_version'),
                trace_data.get('retrieval_mode'),
                trace_data.get('chunks_retrieved'),
                trace_data.get('latency_ms'),
                json.dumps(trace_data.get('evidence_shape', {})),
                json.dumps(trace_data.get('metadata', {}))
            )
            return result['query_id'] if result else None

    async def get_telemetry_by_id(self, query_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a specific telemetry record by ID."""
        try:
            async with self.db.get_async_connection_context() as conn:
                query = "SELECT * FROM intelligence.policy_telemetry WHERE query_id = $1"
                row = await conn.fetchrow(query, query_id)
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get telemetry {query_id}: {e}")
            return None

    async def get_route_distribution(self, days: int = 7) -> List[Dict[str, Any]]:
        """Get distribution of policy actions over recent period."""
        try:
            async with self.db.get_async_connection_context() as conn:
                # Use string formatting safely because 'days' is expected as an int from internal/admin use
                query = f"""
                    SELECT action_taken, execution_path, COUNT(*) as count
                    FROM intelligence.policy_telemetry
                    WHERE created_at > NOW() - INTERVAL '{days} days'
                    GROUP BY action_taken, execution_path
                    ORDER BY count DESC
                """
                rows = await conn.fetch(query)
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Failed to get route distribution: {e}")
            return []
            return str(result['query_id'])

    async def update_telemetry_outcome(
        self,
        query_id: str,
        groundedness: Optional[float] = None,
        quality: Optional[float] = None,
        unsupported_count: Optional[int] = None,
        citation_accuracy: Optional[float] = None
    ) -> bool:
        """Update telemetry record with outcome metrics."""
        async with self.db.get_async_connection_context() as conn:
            query = """
                UPDATE intelligence.policy_telemetry
                SET groundedness_score = COALESCE($2, groundedness_score),
                    quality_score = COALESCE($3, quality_score),
                    unsupported_claim_count = COALESCE($4, unsupported_claim_count),
                    citation_accuracy = COALESCE($5, citation_accuracy)
                WHERE query_id = $1
            """
            result = await conn.execute(
                query, query_id, groundedness, quality, unsupported_count, citation_accuracy
            )
            return result == "UPDATE 1"

    async def get_recent_telemetry(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Fetch recent telemetry for analysis."""
        async with self.db.get_async_connection_context() as conn:
            query = """
                SELECT * FROM intelligence.policy_telemetry
                ORDER BY created_at DESC
                LIMIT $1
            """
            rows = await conn.fetch(query, limit)
            return [dict(row) for row in rows]


# Global instances
db_manager = DatabaseManager()
document_repo = DocumentRepository(db_manager)
policy_repo = PolicyRepository(db_manager)

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
        embedding: Optional[List[float]] = None
    ) -> int:
        """Create a new document."""
        async with self.db.get_async_connection_context() as conn:
            query = """
                INSERT INTO intelligence.documents (title, content, metadata, embedding)
                VALUES ($1, $2, $3::jsonb, $4)
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
                embedding_str
            )
            return result["id"]

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
        chunks: List[Dict[str, Any]]
    ) -> List[int]:
        """Create multiple chunks for a document."""
        async with self.db.get_async_connection_context() as conn:
            chunk_ids = []

            for chunk_data in chunks:
                query = """
                    INSERT INTO intelligence.chunks
                    (document_id, content, embedding, chunk_index)
                    VALUES ($1, $2, $3, $4)
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
                    chunk_data['chunk_index']
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


# Global instances
db_manager = DatabaseManager()
document_repo = DocumentRepository(db_manager)

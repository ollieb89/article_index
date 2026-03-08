"""Unit tests for HNSW plan assumptions without requiring database.

These tests verify that the SQL and configuration files are correctly
set up for HNSW vector indexing as per the implementation plan.
"""

import pytest
from pathlib import Path


class TestHNSWSchemaConfiguration:
    """Verify schema.sql defines HNSW index."""

    def test_schema_sql_contains_hnsw_index(self):
        """Verify schema.sql defines HNSW index for chunks."""
        schema_path = Path(__file__).parent.parent / "schema.sql"
        content = schema_path.read_text()
        
        assert "idx_chunks_embedding_hnsw" in content, \
            "schema.sql should define idx_chunks_embedding_hnsw"
        assert "USING hnsw" in content, \
            "schema.sql should use HNSW index type"
        assert "vector_cosine_ops" in content, \
            "schema.sql should use vector_cosine_ops"

    def test_schema_sql_hnsw_parameters(self):
        """Verify HNSW index has correct parameters."""
        schema_path = Path(__file__).parent.parent / "schema.sql"
        content = schema_path.read_text()
        
        # Extract HNSW section
        hnsw_section = content.split("idx_chunks_embedding_hnsw")[1].split(";")[0]
        
        assert "m = 16" in hnsw_section or "m=16" in content, \
            "HNSW should have m=16 parameter"
        assert "ef_construction = 64" in hnsw_section or "ef_construction=64" in content, \
            "HNSW should have ef_construction=64 parameter"


class TestHNSWIndexesConfiguration:
    """Verify indexes.sql has HNSW as default."""

    def test_indexes_sql_prefers_hnsw(self):
        """Verify indexes.sql has HNSW as primary (uncommented) and IVFFlat commented."""
        indexes_path = Path(__file__).parent.parent / "indexes.sql"
        content = indexes_path.read_text()
        
        # HNSW should be uncommented (active)
        hnsw_section = content.split("idx_chunks_embedding_hnsw")[0]
        # Find the CREATE INDEX line for HNSW
        lines = content.split("\n")
        hnsw_create_line = None
        for i, line in enumerate(lines):
            if "idx_chunks_embedding_hnsw" in line and "CREATE INDEX" in line:
                hnsw_create_line = line.strip()
                break
        
        assert hnsw_create_line is not None, "Should have HNSW index creation"
        assert not hnsw_create_line.startswith("--"), \
            "HNSW index should not be commented out"

    def test_indexes_sql_ivfflat_is_commented(self):
        """Verify IVFFlat index is commented as alternative."""
        indexes_path = Path(__file__).parent.parent / "indexes.sql"
        content = indexes_path.read_text()
        
        # IVFFlat should be commented out
        lines = content.split("\n")
        ivfflat_found = False
        for i, line in enumerate(lines):
            if "ivfflat" in line.lower() and "CREATE INDEX" in line:
                ivfflat_found = True
                assert line.strip().startswith("--"), \
                    "IVFFlat index should be commented out as alternative"
        
        assert ivfflat_found, "Should have IVFFlat index commented out"

    def test_indexes_sql_has_document_hnsw(self):
        """Verify indexes.sql has HNSW index for documents."""
        indexes_path = Path(__file__).parent.parent / "indexes.sql"
        content = indexes_path.read_text()
        
        assert "idx_documents_embedding_hnsw" in content, \
            "indexes.sql should define HNSW index for documents"


class TestHNSWMigration:
    """Verify migration 004 has conditional HNSW creation."""

    def test_migration_004_has_header_comment(self):
        """Verify migration 004 has HNSW conditional creation header."""
        migration_path = Path(__file__).parent.parent / "migrations" / "004_add_hybrid_search.sql"
        content = migration_path.read_text()
        
        assert "MIGRATION STATUS: HNSW index creation is CONDITIONAL" in content, \
            "Migration 004 should document conditional HNSW creation"
        assert "schema.sql already creates this index" in content, \
            "Migration should reference schema.sql HNSW creation"

    def test_migration_004_has_conditional_creation(self):
        """Verify migration uses conditional HNSW index creation."""
        migration_path = Path(__file__).parent.parent / "migrations" / "004_add_hybrid_search.sql"
        content = migration_path.read_text()
        
        # Should have the DO block for conditional creation
        assert "IF NOT EXISTS" in content, \
            "Migration should use IF NOT EXISTS for safe index creation"
        assert "pg_indexes" in content, \
            "Migration should check pg_indexes for existing index"


class TestHNSWSearchFunctions:
    """Verify SQL search functions use correct operators."""

    def test_schema_sql_has_similarity_functions(self):
        """Verify schema.sql has semantic search functions using <=> operator."""
        schema_path = Path(__file__).parent.parent / "schema.sql"
        content = schema_path.read_text()
        
        # Check for vector distance operator
        assert "embedding <=>" in content, \
            "Schema should use <=> operator for vector distance"
        
        # Check for semantic search function
        assert "find_similar_chunks_semantic" in content, \
            "Schema should define find_similar_chunks_semantic function"


class TestHNSWDocumentation:
    """Verify HNSW documentation exists in plan."""

    def test_hnsw_plan_documentation_exists(self):
        """Verify HNSW implementation plan exists."""
        plan_path = Path(__file__).parent.parent / "docs" / "plans" / "HNSW_vector_index.md"
        assert plan_path.exists(), \
            "HNSW implementation plan should exist"


@pytest.mark.integration
class TestHNSWIntegration:
    """Integration tests requiring running database (marked separately)."""

    async def test_hnsw_index_exists(self):
        """Verify HNSW index exists in database."""
        # This test requires a running database
        # Import here to avoid import errors when DB is not available
        from shared.database import db_manager
        
        async with db_manager.get_async_connection_context() as conn:
            row = await conn.fetchrow("""
                SELECT indexname, indexdef 
                FROM pg_indexes 
                WHERE schemaname = 'intelligence' 
                  AND indexname = 'idx_chunks_embedding_hnsw'
            """)
            
            assert row is not None, "HNSW index should exist in database"
            assert "hnsw" in row["indexdef"].lower(), \
                "Index should be HNSW type"

    async def test_hnsw_query_plan_uses_index(self):
        """Verify query planner uses HNSW index."""
        from shared.database import db_manager
        
        async with db_manager.get_async_connection_context() as conn:
            # Get a sample embedding if available
            row = await conn.fetchrow(
                "SELECT embedding FROM intelligence.chunks WHERE embedding IS NOT NULL LIMIT 1"
            )
            
            if not row:
                pytest.skip("No embeddings in database to test query plan")
            
            embedding_str = str(row["embedding"])
            
            # Check query plan
            plan = await conn.fetchval(
                """EXPLAIN (FORMAT TEXT)
                   SELECT id FROM intelligence.chunks
                   ORDER BY embedding <=> $1::vector
                   LIMIT 5""",
                embedding_str
            )
            
            plan_lower = plan.lower()
            assert "idx_chunks_embedding_hnsw" in plan_lower or "hnsw" in plan_lower, \
                f"Query plan should use HNSW index, got: {plan}"

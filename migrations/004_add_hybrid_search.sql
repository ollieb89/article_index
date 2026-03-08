-- Migration: Add hybrid search support (Phase 1: Schema Changes)
-- Author: Agent
-- Date: 2026-03-07
-- Description: Adds weighted full-text search to chunks table for hybrid retrieval

-- Step 1: Add title column to chunks (nullable for migration safety)
-- This denormalizes the document title into chunks for weighted search
ALTER TABLE intelligence.chunks
ADD COLUMN IF NOT EXISTS title TEXT;

COMMENT ON COLUMN intelligence.chunks.title IS 
  'Denormalized document title for weighted full-text search';

-- Step 2: Backfill titles from documents table
-- Run this in batches for large tables (10000 at a time)
UPDATE intelligence.chunks c
SET title = d.title
FROM intelligence.documents d
WHERE c.document_id = d.id
  AND c.title IS NULL;

-- Step 3: Add weighted tsvector generated column
-- Weight A (1.0) for title, D (0.1) for content
-- Stored generated column computes once on write/update
ALTER TABLE intelligence.chunks
ADD COLUMN IF NOT EXISTS search_tsv tsvector
GENERATED ALWAYS AS (
  setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
  setweight(to_tsvector('english', coalesce(content, '')), 'D')
) STORED;

COMMENT ON COLUMN intelligence.chunks.search_tsv IS 
  'Weighted full-text search vector: title(A=1.0) + content(D=0.1)';

-- Step 4: Create GIN index for fast full-text search
-- Use CONCURRENTLY for zero-downtime on production (run outside transactions)
-- For initial migration, regular CREATE INDEX is acceptable
CREATE INDEX IF NOT EXISTS idx_chunks_search_tsv
ON intelligence.chunks
USING gin (search_tsv);

-- Step 5: Optional HNSW index for better vector performance
-- Only create if not already exists (IVFFlat may already be present)
-- HNSW provides better recall than IVFFlat
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes 
        WHERE indexname = 'idx_chunks_embedding_hnsw'
    ) THEN
        CREATE INDEX idx_chunks_embedding_hnsw
        ON intelligence.chunks
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64);
    END IF;
END $$;

-- Step 6: Create SQL functions for hybrid retrieval

-- Lexical search function using full-text search
CREATE OR REPLACE FUNCTION intelligence.find_similar_chunks_lexical(
    p_query TEXT,
    p_limit INTEGER DEFAULT 30
)
RETURNS TABLE (
    id INTEGER,
    document_id INTEGER,
    chunk_index INTEGER,
    title TEXT,
    content TEXT,
    lexical_score FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        c.id,
        c.document_id,
        c.chunk_index,
        c.title,
        c.content,
        ts_rank(c.search_tsv, plainto_tsquery('english', p_query))::FLOAT AS lexical_score
    FROM intelligence.chunks c
    WHERE c.search_tsv @@ plainto_tsquery('english', p_query)
    ORDER BY lexical_score DESC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION intelligence.find_similar_chunks_lexical IS 
  'Find similar chunks using PostgreSQL full-text search with weighted title/content';

-- Semantic search function using vector similarity
-- Different from existing find_similar_chunks: returns chunk_index, no threshold by default
CREATE OR REPLACE FUNCTION intelligence.find_similar_chunks_semantic(
    p_embedding VECTOR(768),
    p_limit INTEGER DEFAULT 40,
    p_similarity_threshold FLOAT DEFAULT 0.0
)
RETURNS TABLE (
    id INTEGER,
    document_id INTEGER,
    chunk_index INTEGER,
    title TEXT,
    content TEXT,
    semantic_score FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        c.id,
        c.document_id,
        c.chunk_index,
        c.title,
        c.content,
        (1 - (c.embedding <=> p_embedding))::FLOAT AS semantic_score
    FROM intelligence.chunks c
    WHERE c.embedding IS NOT NULL
      AND (1 - (c.embedding <=> p_embedding)) > p_similarity_threshold
    ORDER BY c.embedding <=> p_embedding
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION intelligence.find_similar_chunks_semantic IS 
  'Find similar chunks using vector cosine similarity (for hybrid search)';

-- Step 7: Verify the migration
-- These queries should return results after migration
-- SELECT COUNT(*) FROM intelligence.chunks WHERE title IS NOT NULL;
-- SELECT COUNT(*) FROM intelligence.chunks WHERE search_tsv IS NOT NULL;
-- EXPLAIN SELECT * FROM intelligence.chunks WHERE search_tsv @@ 'test';

-- Create pgvector extension (required for VECTOR type)
CREATE EXTENSION IF NOT EXISTS vector;

-- Create intelligence schema for vector search and RAG
CREATE SCHEMA IF NOT EXISTS intelligence;

-- Documents table for storing full articles
CREATE TABLE intelligence.documents (
    id SERIAL PRIMARY KEY,
    title TEXT,
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    embedding VECTOR(768),
    content_hash TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_content_hash_unique
ON intelligence.documents(content_hash) WHERE content_hash IS NOT NULL;

-- Chunks table for storing smaller pieces of documents
-- Includes denormalized title and full-text search vector for hybrid retrieval
CREATE TABLE intelligence.chunks (
    id SERIAL PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES intelligence.documents(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    embedding VECTOR(768),
    chunk_index INTEGER NOT NULL,
    title TEXT,  -- Denormalized from documents for weighted search
    search_tsv tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(content, '')), 'D')
    ) STORED,  -- Weighted FTS: title(A=1.0) + content(D=0.1)
    created_at TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON COLUMN intelligence.chunks.title IS 'Denormalized document title for weighted full-text search';
COMMENT ON COLUMN intelligence.chunks.search_tsv IS 'Weighted full-text search vector: title(A=1.0) + content(D=0.1)';

-- Indexes for better performance
CREATE INDEX idx_chunks_document_id ON intelligence.chunks(document_id);
CREATE INDEX idx_documents_created_at ON intelligence.documents(created_at);
CREATE INDEX idx_chunks_created_at ON intelligence.chunks(created_at);

-- Full-text search index for hybrid search
CREATE INDEX idx_chunks_search_tsv ON intelligence.chunks USING gin (search_tsv);

-- HNSW index for vector similarity (better recall than IVFFlat)
CREATE INDEX idx_chunks_embedding_hnsw 
ON intelligence.chunks 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- Similarity search function for chunks
CREATE FUNCTION intelligence.find_similar_chunks(
    p_embedding VECTOR(768),
    p_limit INTEGER DEFAULT 5,
    p_similarity_threshold FLOAT DEFAULT 0.7
)
RETURNS TABLE (
    id INTEGER,
    document_id INTEGER,
    content TEXT,
    similarity FLOAT,
    title TEXT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        c.id,
        c.document_id,
        c.content,
        1 - (c.embedding <=> p_embedding) AS similarity,
        d.title
    FROM intelligence.chunks c
    JOIN intelligence.documents d ON c.document_id = d.id
    WHERE c.embedding IS NOT NULL
      AND 1 - (c.embedding <=> p_embedding) > p_similarity_threshold
    ORDER BY c.embedding <=> p_embedding
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql;

-- Similarity search function for full documents
CREATE FUNCTION intelligence.find_similar_documents(
    p_embedding VECTOR(768),
    p_limit INTEGER DEFAULT 5,
    p_similarity_threshold FLOAT DEFAULT 0.7
)
RETURNS TABLE (
    id INTEGER,
    title TEXT,
    content TEXT,
    similarity FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        d.id,
        d.title,
        d.content,
        1 - (d.embedding <=> p_embedding) AS similarity
    FROM intelligence.documents d
    WHERE d.embedding IS NOT NULL
      AND 1 - (d.embedding <=> p_embedding) > p_similarity_threshold
    ORDER BY d.embedding <=> p_embedding
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql;

-- Function to get context for RAG (aggregates multiple chunks)
CREATE FUNCTION intelligence.get_rag_context(
    p_embedding VECTOR(768),
    p_limit INTEGER DEFAULT 5,
    p_similarity_threshold FLOAT DEFAULT 0.7
)
RETURNS TABLE (
    context TEXT,
    document_ids INTEGER[],
    similarities FLOAT[]
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        string_agg(c.content, E'\n\n' ORDER BY c.similarity DESC) as context,
        array_agg(DISTINCT c.document_id ORDER BY c.document_id) as document_ids,
        array_agg(c.similarity ORDER BY c.similarity DESC) as similarities
    FROM (
        SELECT
            c.id,
            c.document_id,
            c.content,
            1 - (c.embedding <=> p_embedding) AS similarity
        FROM intelligence.chunks c
        WHERE c.embedding IS NOT NULL
          AND 1 - (c.embedding <=> p_embedding) > p_similarity_threshold
        ORDER BY c.embedding <=> p_embedding
        LIMIT p_limit
    ) c;
END;
$$ LANGUAGE plpgsql;

-- Hybrid search functions: lexical and semantic retrieval

-- Lexical search using PostgreSQL full-text search
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

-- Semantic search using vector similarity (for hybrid search)
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

-- Add trigger for updated_at timestamp
CREATE OR REPLACE FUNCTION intelligence.update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_documents_updated_at
    BEFORE UPDATE ON intelligence.documents
    FOR EACH ROW
    EXECUTE FUNCTION intelligence.update_updated_at_column();

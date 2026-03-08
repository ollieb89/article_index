-- Vector indexes for performance optimization

-- HNSW index for chunks (RECOMMENDED: better recall, faster queries)
-- Already created in schema.sql for new deployments
-- For existing deployments, run this manually or via migration 004
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_chunks_embedding_hnsw 
ON intelligence.chunks 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- IVFFlat index for chunks (Alternative: lower memory, faster build)
-- Only use if memory constrained: requires training, less accurate
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_chunks_embedding_ivfflat 
-- ON intelligence.chunks 
-- USING ivfflat (embedding vector_cosine_ops)
-- WITH (lists = 100);

-- HNSW index for documents (for document-level similarity)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_documents_embedding_hnsw 
ON intelligence.documents 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- GIN index for metadata JSONB
CREATE INDEX IF NOT EXISTS idx_documents_metadata_gin 
ON intelligence.documents 
USING GIN (metadata);

-- GIN index for document title (for text search)
CREATE INDEX IF NOT EXISTS idx_documents_title_gin 
ON intelligence.documents 
USING GIN (to_tsvector('english', title));

-- GIN index for chunk content (for text search)
CREATE INDEX IF NOT EXISTS idx_chunks_content_gin 
ON intelligence.chunks 
USING GIN (to_tsvector('english', content));

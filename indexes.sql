-- Vector indexes for performance optimization
-- Add these after you have some data in the tables

-- IVFFlat index for chunks (good balance of performance and memory)
CREATE INDEX CONCURRENTLY idx_chunks_embedding_ivfflat 
ON intelligence.chunks 
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- HNSW index for chunks (better recall, more memory)
-- Alternative to IVFFlat, uncomment if you need better recall
-- CREATE INDEX CONCURRENTLY idx_chunks_embedding_hnsw 
-- ON intelligence.chunks 
-- USING hnsw (embedding vector_cosine_ops)
-- WITH (m = 16, ef_construction = 64);

-- IVFFlat index for documents
CREATE INDEX CONCURRENTLY idx_documents_embedding_ivfflat 
ON intelligence.documents 
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- HNSW index for documents (alternative)
-- CREATE INDEX CONCURRENTLY idx_documents_embedding_hnsw 
-- ON intelligence.documents 
-- USING hnsw (embedding vector_cosine_ops)
-- WITH (m = 16, ef_construction = 64);

-- GIN index for metadata JSONB
CREATE INDEX idx_documents_metadata_gin 
ON intelligence.documents 
USING GIN (metadata);

-- GIN index for document title (for text search)
CREATE INDEX idx_documents_title_gin 
ON intelligence.documents 
USING GIN (to_tsvector('english', title));

-- GIN index for chunk content (for text search)
CREATE INDEX idx_chunks_content_gin 
ON intelligence.chunks 
USING GIN (to_tsvector('english', content));

-- Migration: add content_hash for duplicate detection
-- Run on existing DBs: psql -f migrations/001_add_content_hash.sql $DATABASE_URL

ALTER TABLE intelligence.documents
ADD COLUMN IF NOT EXISTS content_hash TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_content_hash_unique
ON intelligence.documents(content_hash) WHERE content_hash IS NOT NULL;

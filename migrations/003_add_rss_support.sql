-- Migration 003: Add RSS feed support and source_url column

-- Add source_url as first-class column to documents table
ALTER TABLE intelligence.documents 
ADD COLUMN source_url TEXT,
ADD COLUMN content_hash VARCHAR(64) DEFAULT NULL;

-- Create index on source_url for better deduplication and filtering
CREATE INDEX idx_documents_source_url ON intelligence.documents(source_url);

-- Create unique index on content hash for exact deduplication
CREATE UNIQUE INDEX idx_documents_content_hash ON intelligence.documents(content_hash) 
WHERE content_hash IS NOT NULL;

-- RSS feeds table
CREATE TABLE intelligence.feeds (
    id SERIAL PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    title TEXT,
    description TEXT,
    last_fetched_at TIMESTAMPTZ,
    last_entry_at TIMESTAMPTZ,
    fetch_interval_minutes INTEGER DEFAULT 60,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    -- Feed metadata
    feed_type VARCHAR(50) DEFAULT 'rss', -- rss, atom, etc.
    author TEXT,
    language VARCHAR(10),
    
    -- Statistics
    total_entries_fetched INTEGER DEFAULT 0,
    total_entries_processed INTEGER DEFAULT 0,
    last_error TEXT,
    consecutive_errors INTEGER DEFAULT 0
);

-- Feed entries tracking table (to avoid reprocessing)
CREATE TABLE intelligence.feed_entries (
    id SERIAL PRIMARY KEY,
    feed_id INTEGER NOT NULL REFERENCES intelligence.feeds(id) ON DELETE CASCADE,
    entry_url TEXT NOT NULL,
    entry_title TEXT,
    entry_hash VARCHAR(64) NOT NULL, -- hash of title + content + URL
    published_at TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    document_id INTEGER REFERENCES intelligence.documents(id) ON DELETE SET NULL,
    status VARCHAR(20) DEFAULT 'pending', -- pending, processed, skipped, error
    
    UNIQUE(feed_id, entry_hash),
    UNIQUE(feed_id, entry_url)
);

-- Indexes for feed entries
CREATE INDEX idx_feed_entries_feed_id ON intelligence.feed_entries(feed_id);
CREATE INDEX idx_feed_entries_status ON intelligence.feed_entries(status);
CREATE INDEX idx_feed_entries_processed_at ON intelligence.feed_entries(processed_at);
CREATE INDEX idx_feed_entries_published_at ON intelligence.feed_entries(published_at);

-- Update updated_at trigger for feeds
CREATE OR REPLACE FUNCTION update_feeds_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language plpgsql;

CREATE TRIGGER trigger_update_feeds_updated_at
    BEFORE UPDATE ON intelligence.feeds
    FOR EACH ROW
    EXECUTE FUNCTION update_feeds_updated_at();

-- Function to update content hash trigger
CREATE OR REPLACE FUNCTION update_document_content_hash()
RETURNS TRIGGER AS $$
BEGIN
    -- Generate hash from title, content, and source_url
    IF NEW.title IS NOT NULL AND NEW.content IS NOT NULL THEN
        NEW.content_hash = encode(
            sha256(NEW.title || NEW.content || COALESCE(NEW.source_url, '')),
            'hex'
        );
    END IF;
    RETURN NEW;
END;
$$ language plpgsql;

-- Create trigger to auto-update content hash
CREATE TRIGGER trigger_update_document_content_hash
    BEFORE INSERT OR UPDATE ON intelligence.documents
    FOR EACH ROW
    EXECUTE FUNCTION update_document_content_hash();

-- Function to safely add feed (returns existing if exists)
CREATE OR REPLACE FUNCTION upsert_feed(
    p_url TEXT,
    p_title TEXT DEFAULT NULL,
    p_description TEXT DEFAULT NULL,
    p_feed_type VARCHAR(50) DEFAULT 'rss',
    p_author TEXT DEFAULT NULL,
    p_language VARCHAR(10) DEFAULT NULL
)
RETURNS INTEGER AS $$
DECLARE
    feed_id INTEGER;
BEGIN
    -- Try to insert new feed
    INSERT INTO intelligence.feeds (
        url, title, description, feed_type, author, language
    ) VALUES (
        p_url, p_title, p_description, p_feed_type, p_author, p_language
    ) 
    ON CONFLICT (url) DO NOTHING
    RETURNING id INTO feed_id;
    
    -- If insert failed (conflict), get existing ID
    IF feed_id IS NULL THEN
        SELECT id INTO feed_id FROM intelligence.feeds WHERE url = p_url;
    END IF;
    
    RETURN feed_id;
END;
$$ LANGUAGE plpgsql;

-- Function to check if entry was already processed
CREATE OR REPLACE FUNCTION is_entry_processed(
    p_feed_id INTEGER,
    p_entry_hash VARCHAR(64),
    p_entry_url TEXT
)
RETURNS BOOLEAN AS $$
DECLARE
    exists_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO exists_count
    FROM intelligence.feed_entries 
    WHERE feed_id = p_feed_id 
    AND (entry_hash = p_entry_hash OR entry_url = p_entry_url);
    
    RETURN exists_count > 0;
END;
$$ LANGUAGE plpgsql;

-- Function to record feed entry
CREATE OR REPLACE FUNCTION record_feed_entry(
    p_feed_id INTEGER,
    p_entry_url TEXT,
    p_entry_title TEXT,
    p_entry_hash VARCHAR(64),
    p_published_at TIMESTAMPTZ DEFAULT NULL,
    p_status VARCHAR(20) DEFAULT 'pending'
)
RETURNS INTEGER AS $$
DECLARE
    entry_id INTEGER;
BEGIN
    INSERT INTO intelligence.feed_entries (
        feed_id, entry_url, entry_title, entry_hash, 
        published_at, status
    ) VALUES (
        p_feed_id, p_entry_url, p_entry_title, p_entry_hash,
        p_published_at, p_status
    )
    ON CONFLICT (feed_id, entry_hash) DO NOTHING
    RETURNING id INTO entry_id;
    
    -- If insert failed (conflict), get existing ID
    IF entry_id IS NULL THEN
        SELECT id INTO entry_id 
        FROM intelligence.feed_entries 
        WHERE feed_id = p_feed_id AND entry_hash = p_entry_hash;
    END IF;
    
    RETURN entry_id;
END;
$$ LANGUAGE plpgsql;

-- View for feed statistics
CREATE VIEW intelligence.feed_stats AS
SELECT 
    f.id,
    f.url,
    f.title,
    f.is_active,
    f.last_fetched_at,
    f.last_entry_at,
    f.total_entries_fetched,
    f.total_entries_processed,
    COUNT(fe.id) as pending_entries,
    COUNT(CASE WHEN fe.status = 'processed' THEN 1 END) as processed_entries,
    COUNT(CASE WHEN fe.status = 'error' THEN 1 END) as error_entries,
    ROUND(
        COUNT(CASE WHEN fe.status = 'processed' THEN 1 END) * 100.0 / 
        NULLIF(COUNT(fe.id), 0), 2
    ) as success_rate
FROM intelligence.feeds f
LEFT JOIN intelligence.feed_entries fe ON f.id = fe.feed_id
GROUP BY f.id, f.url, f.title, f.is_active, f.last_fetched_at, 
         f.last_entry_at, f.total_entries_fetched, f.total_entries_processed;

-- Grant permissions (adjust as needed)
-- GRANT SELECT, INSERT, UPDATE, DELETE ON intelligence.feeds TO article_index;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON intelligence.feed_entries TO article_index;
-- GRANT SELECT ON intelligence.feed_stats TO article_index;

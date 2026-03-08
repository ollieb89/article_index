"""
RSS feed parsing and entry extraction utilities.
Handles feed fetching, entry parsing, and deduplication.
"""

import logging
import hashlib
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import urljoin, urlparse
import httpx
import feedparser
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class RSSFeedEntry:
    """Represents a single RSS feed entry."""
    
    def __init__(
        self,
        title: str,
        url: str,
        content: str,
        published: Optional[datetime] = None,
        author: Optional[str] = None,
        summary: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ):
        self.title = title
        self.url = url
        self.content = content
        self.published = published or datetime.now(timezone.utc)
        self.author = author
        self.summary = summary
        self.tags = tags or []
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for processing."""
        return {
            "title": self.title,
            "url": self.url,
            "content": self.content,
            "published": self.published.isoformat(),
            "author": self.author,
            "summary": self.summary,
            "tags": self.tags,
        }
    
    def get_content_hash(self) -> str:
        """Generate content hash for deduplication."""
        content_for_hash = f"{self.title}{self.content}{self.url}"
        return hashlib.sha256(content_for_hash.encode()).hexdigest()


class RSSFeedParser:
    """RSS feed parser with content extraction and deduplication."""
    
    def __init__(self, timeout: int = 30, user_agent: str = None):
        self.timeout = timeout
        self.user_agent = user_agent or "ArticleIndex-RSSBot/1.0"
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers={"User-Agent": self.user_agent},
            follow_redirects=True,
        )
    
    async def fetch_feed(self, feed_url: str) -> Dict[str, Any]:
        """Fetch RSS feed from URL."""
        try:
            response = await self.client.get(feed_url)
            response.raise_for_status()
            
            # Parse RSS feed
            feed = feedparser.parse(response.content)
            
            if feed.bozo and feed.bozo_exception:
                logger.warning(f"Feed parsing warning for {feed_url}: {feed.bozo_exception}")
            
            return feed
            
        except httpx.HTTPError as e:
            logger.error(f"HTTP error fetching feed {feed_url}: {e}")
            raise ValueError(f"Failed to fetch feed: {e}")
        except Exception as e:
            logger.error(f"Error parsing feed {feed_url}: {e}")
            raise ValueError(f"Failed to parse feed: {e}")
    
    def extract_content_from_html(self, html_content: str, base_url: str = "") -> str:
        """Extract clean text content from HTML."""
        try:
            soup = BeautifulSoup(html_content, "html.parser")
            
            # Remove script and style elements
            for script in soup(["script", "style"]):
                script.decompose()
            
            # Get text content
            text = soup.get_text()
            
            # Clean up whitespace
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = " ".join(chunk for chunk in chunks if chunk)
            
            return text
            
        except Exception as e:
            logger.warning(f"Error extracting content from HTML: {e}")
            return html_content
    
    def normalize_url(self, url: str, base_url: str) -> str:
        """Normalize and resolve URL."""
        if not url:
            return ""
        
        # Resolve relative URLs
        if base_url:
            url = urljoin(base_url, url)
        
        # Parse and rebuild to ensure consistency
        parsed = urlparse(url)
        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if parsed.query:
            normalized += f"?{parsed.query}"
        if parsed.fragment:
            normalized += f"#{parsed.fragment}"
        
        return normalized
    
    def parse_feed_entry(self, entry, feed_url: str) -> RSSFeedEntry:
        """Parse a single feed entry into RSSFeedEntry."""
        # Get basic info
        title = getattr(entry, "title", "") or "No title"
        link = getattr(entry, "link", "")
        
        # Normalize URL
        normalized_url = self.normalize_url(link, feed_url)
        
        # Extract content (try different fields)
        content = ""
        if hasattr(entry, "content") and entry.content:
            content = entry.content[0].value if entry.content else ""
        elif hasattr(entry, "description"):
            content = entry.description
        elif hasattr(entry, "summary"):
            content = entry.summary
        
        # If content is HTML, extract text
        if content and "<" in content:
            content = self.extract_content_from_html(content, feed_url)
        
        # Fallback to summary if no content
        if not content and hasattr(entry, "summary"):
            content = self.extract_content_from_html(entry.summary, feed_url)
        
        # Get publication date
        published = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            try:
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            except (ValueError, TypeError):
                pass
        elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
            try:
                published = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
            except (ValueError, TypeError):
                pass
        
        # Get author
        author = None
        if hasattr(entry, "author"):
            author = entry.author
        elif hasattr(entry, "authors") and entry.authors:
            author = entry.authors[0].get("name", "")
        
        # Get tags
        tags = []
        if hasattr(entry, "tags") and entry.tags:
            tags = [tag.term for tag in entry.tags if hasattr(tag, "term")]
        
        # Get summary (different from content)
        summary = getattr(entry, "summary", "")
        if summary and summary == content:
            summary = ""
        elif summary and "<" in summary:
            summary = self.extract_content_from_html(summary, feed_url)
        
        return RSSFeedEntry(
            title=title,
            url=normalized_url,
            content=content,
            published=published,
            author=author,
            summary=summary,
            tags=tags,
        )
    
    async def parse_feed(self, feed_url: str, max_entries: int = 50) -> List[RSSFeedEntry]:
        """Parse RSS feed and return list of entries."""
        feed_data = await self.fetch_feed(feed_url)
        
        entries = []
        feed_info = feed_data.get("feed", {})
        
        # Get feed entries
        feed_entries = feed_data.get("entries", [])
        
        for i, entry in enumerate(feed_entries[:max_entries]):
            try:
                parsed_entry = self.parse_feed_entry(entry, feed_url)
                
                # Skip entries without URLs or content
                if not parsed_entry.url or not parsed_entry.content:
                    logger.warning(f"Skipping entry {i+1} with missing URL or content")
                    continue
                
                # Skip very short content (likely not real articles)
                if len(parsed_entry.content.strip()) < 100:
                    logger.warning(f"Skipping entry {i+1} with very short content")
                    continue
                
                entries.append(parsed_entry)
                
            except Exception as e:
                logger.warning(f"Error parsing entry {i+1}: {e}")
                continue
        
        logger.info(f"Parsed {len(entries)} valid entries from feed {feed_url}")
        return entries
    
    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()


async def fetch_feed_entries(feed_url: str, max_entries: int = 50) -> List[Dict[str, Any]]:
    """Convenience function to fetch and parse RSS feed entries."""
    parser = RSSFeedParser()
    try:
        entries = await parser.parse_feed(feed_url, max_entries)
        return [entry.to_dict() for entry in entries]
    finally:
        await parser.close()

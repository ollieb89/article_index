#!/usr/bin/env python3
"""
Test script for RSS feed ingestion functionality.
Tests the complete RSS ingestion pipeline.
"""

import asyncio
import sys
import os
import time
import requests
from typing import Dict, Any

# Add the shared directory to the path
sys.path.append('/app/shared')

from rss_parser import RSSFeedParser
from database import get_db_connection

API_BASE = os.getenv('API_BASE', 'http://localhost:8001')
API_KEY = os.getenv('API_KEY', 'df27bf50-c887-4ce5-84bb-0fff80f5dd84')


def print_section(title: str):
    """Print a section header."""
    print(f"\n{'='*60}")
    print(f" {title}")
    print('='*60)


def print_subsection(title: str):
    """Print a subsection header."""
    print(f"\n--- {title} ---")


async def test_rss_parser():
    """Test the RSS parser directly."""
    print_section("Testing RSS Parser")
    
    parser = RSSFeedParser()
    
    # Test feeds
    test_feeds = [
        "https://feeds.bbci.co.uk/news/rss.xml",
        "https://rss.cnn.com/rss/edition.rss",
    ]
    
    for feed_url in test_feeds:
        print_subsection(f"Testing: {feed_url}")
        try:
            entries = await parser.parse_feed(feed_url, 3)
            print(f"✓ Found {len(entries)} entries")
            
            for i, entry in enumerate(entries[:2]):  # Show first 2
                print(f"  {i+1}. {entry.title[:80]}...")
                print(f"     URL: {entry.url}")
                print(f"     Content length: {len(entry.content)} chars")
            
        except Exception as e:
            print(f"✗ Error: {e}")
    
    await parser.close()


def test_api_endpoints():
    """Test the RSS API endpoints."""
    print_section("Testing RSS API Endpoints")
    
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": API_KEY
    }
    
    # Test feed creation
    print_subsection("Creating RSS Feed")
    feed_data = {
        "url": "https://feeds.bbci.co.uk/news/rss.xml",
        "max_entries": 3,
        "auto_process": True
    }
    
    try:
        response = requests.post(f"{API_BASE}/feeds/async", json=feed_data, headers=headers)
        if response.status_code == 200:
            task_data = response.json()
            task_id = task_data["task_id"]
            print(f"✓ Feed processing started: {task_id}")
            
            # Poll task status
            print_subsection("Monitoring Task Status")
            for i in range(30):  # Wait up to 30 seconds
                response = requests.get(f"{API_BASE}/tasks/{task_id}")
                if response.status_code == 200:
                    task_status = response.json()
                    print(f"  Status: {task_status['status']}")
                    
                    if task_status['status'] in ['SUCCESS', 'FAILURE']:
                        if task_status['status'] == 'SUCCESS':
                            result = task_status['result']
                            print(f"✓ Processing completed!")
                            print(f"  Entries found: {result.get('entries_found', 0)}")
                            print(f"  Entries processed: {result.get('entries_processed', 0)}")
                            print(f"  Entries skipped: {result.get('entries_skipped', 0)}")
                            print(f"  Feed ID: {result.get('feed_id')}")
                        else:
                            print(f"✗ Processing failed: {task_status.get('error', 'Unknown error')}")
                        break
                else:
                    print(f"✗ Failed to get task status: {response.status_code}")
                
                time.sleep(1)
            else:
                print("⚠ Task processing timed out")
        else:
            print(f"✗ Failed to create feed: {response.status_code}")
            print(f"  Response: {response.text}")
            
    except Exception as e:
        print(f"✗ API test failed: {e}")
    
    # Test feed listing
    print_subsection("Listing Feeds")
    try:
        response = requests.get(f"{API_BASE}/feeds/")
        if response.status_code == 200:
            feeds_data = response.json()
            print(f"✓ Found {len(feeds_data.get('feeds', []))} feeds")
            
            for feed in feeds_data.get('feeds', [])[:3]:  # Show first 3
                print(f"  - {feed['title']}")
                print(f"    URL: {feed['url']}")
                print(f"    Active: {feed['is_active']}")
                print(f"    Last fetched: {feed.get('last_fetched_at', 'Never')}")
        else:
            print(f"✗ Failed to list feeds: {response.status_code}")
            
    except Exception as e:
        print(f"✗ Feed listing test failed: {e}")


async def test_database_state():
    """Test the database state after RSS processing."""
    print_section("Checking Database State")
    
    conn = await get_db_connection()
    
    try:
        # Check feeds table
        feeds = await conn.fetch("SELECT * FROM intelligence.feeds ORDER BY created_at DESC LIMIT 5")
        print(f"✓ Found {len(feeds)} feeds in database")
        
        for feed in feeds:
            print(f"  - {feed['url']} (ID: {feed['id']})")
            print(f"    Last fetched: {feed['last_fetched_at']}")
            print(f"    Total entries fetched: {feed['total_entries_fetched']}")
        
        # Check feed entries table
        entries = await conn.fetch("SELECT * FROM intelligence.feed_entries ORDER BY first_seen_at DESC LIMIT 5")
        print(f"\n✓ Found {len(entries)} feed entries in database")
        
        for entry in entries:
            print(f"  - {entry['entry_title'][:60]}... (Status: {entry['status']})")
            print(f"    Feed ID: {entry['feed_id']}")
            print(f"    Document ID: {entry['document_id']}")
        
        # Check documents with source URLs
        docs_with_urls = await conn.fetch(
            "SELECT id, title, source_url FROM intelligence.documents WHERE source_url IS NOT NULL LIMIT 5"
        )
        print(f"\n✓ Found {len(docs_with_urls)} documents with source URLs")
        
        for doc in docs_with_urls:
            print(f"  - {doc['title'][:60]}... (ID: {doc['id']})")
            print(f"    Source: {doc['source_url']}")
            
    except Exception as e:
        print(f"✗ Database check failed: {e}")
    finally:
        await conn.close()


async def main():
    """Run all RSS ingestion tests."""
    print("🧪 RSS Ingestion Test Suite")
    print(f"API Base: {API_BASE}")
    
    # Test components
    await test_rss_parser()
    test_api_endpoints()
    await test_database_state()
    
    print_section("Test Summary")
    print("RSS ingestion testing completed!")
    print("\nNext steps:")
    print("1. Check individual feed statistics via GET /feeds/{id}/stats")
    print("2. Verify articles were created via GET /articles/")
    print("3. Test search with RSS content via POST /search")
    print("4. Test RAG with RSS content via POST /rag")


if __name__ == "__main__":
    asyncio.run(main())

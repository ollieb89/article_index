#!/usr/bin/env python3
"""CI script for policy replay regression testing.

This script runs batch replay on recent traces and fails if any
divergences are detected. Designed for CI integration.

Usage:
    python scripts/test_replay_ci.py [--limit N] [--api-url URL]
    
Exit codes:
    0: All replays passed
    1: One or more replays failed
    2: Script error
"""

import argparse
import sys
import requests
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_API_URL = "http://localhost:8001"


def run_replay_batch(api_url: str, limit: int, api_key: str = None) -> dict:
    """Run batch replay via API.
    
    Args:
        api_url: Base URL of the API
        limit: Maximum number of traces to replay
        api_key: Optional API key for authentication
        
    Returns:
        Dict with replay results
    """
    url = f"{api_url}/admin/replay/batch"
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
    
    params = {"limit": limit}
    
    logger.info(f"Running batch replay (limit={limit})...")
    response = requests.post(url, params=params, headers=headers, timeout=60)
    
    if response.status_code == 400:
        # CI fail-on-error: failures detected
        result = response.json()
        logger.error(f"Batch replay found {result.get('failed', 0)} failures")
        return result
    elif response.status_code != 200:
        logger.error(f"API error: {response.status_code} - {response.text}")
        raise RuntimeError(f"API request failed: {response.status_code}")
    
    return response.json()


def main():
    parser = argparse.ArgumentParser(
        description="CI replay regression test for policy determinism"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of traces to replay (default: 50)"
    )
    parser.add_argument(
        "--api-url",
        type=str,
        default=DEFAULT_API_URL,
        help=f"API base URL (default: {DEFAULT_API_URL})"
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="API key for authentication (or set API_KEY env var)"
    )
    parser.add_argument(
        "--fail-on-partial",
        action="store_true",
        help="Also fail on partial replays (missing policies)"
    )
    
    args = parser.parse_args()
    
    # Get API key from environment if not provided
    api_key = args.api_key or os.environ.get("API_KEY")
    
    try:
        result = run_replay_batch(args.api_url, args.limit, api_key)
        
        # Log results
        total = result.get('total_replayed', 0)
        passed = result.get('passed', 0)
        failed = result.get('failed', 0)
        partial = result.get('partial', 0)
        
        logger.info(f"Replay complete: {total} traces")
        logger.info(f"  Passed: {passed}")
        logger.info(f"  Failed: {failed}")
        logger.info(f"  Partial: {partial}")
        
        # Log failures
        failures = result.get('failures', [])
        for failure in failures[:10]:  # Log first 10
            logger.error(
                f"  - Trace {failure.get('trace_id')}: "
                f"{failure.get('status')} - {failure.get('reason')}"
            )
        if len(failures) > 10:
            logger.error(f"  ... and {len(failures) - 10} more")
        
        # Determine exit code
        if failed > 0:
            logger.error(f"FAILED: {failed} replays diverged")
            return 1
        
        if args.fail_on_partial and partial > 0:
            logger.error(f"FAILED: {partial} partial replays")
            return 1
        
        logger.info("SUCCESS: All replays passed")
        return 0
        
    except Exception as e:
        logger.error(f"Script error: {e}")
        return 2


if __name__ == "__main__":
    import os  # Import here to avoid issues with argparse
    sys.exit(main())

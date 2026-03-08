#!/usr/bin/env python3
"""Generate confidence calibration report for RAG system.

This script runs a calibration audit and generates a human-readable report.
It can output to console or save to a file.

Usage:
    # Run with default test suite
    python scripts/calibration_report.py
    
    # Run with custom queries
    python scripts/calibration_report.py --queries "What is ML?" "How does it work?"
    
    # Save to file
    python scripts/calibration_report.py --output report.json
    
    # Limit queries for faster execution
    python scripts/calibration_report.py --max-queries 10

Environment:
    Requires API_BASE and API_KEY environment variables.
    Default: API_BASE=http://localhost:8001
"""

import os
import sys
import json
import argparse
import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import httpx


def print_header(text: str, char: str = "=") -> None:
    """Print a formatted header."""
    print()
    print(char * 60)
    print(f"  {text}")
    print(char * 60)


def print_metric(name: str, value: Any, suffix: str = "") -> None:
    """Print a metric with consistent formatting."""
    if isinstance(value, float):
        value_str = f"{value:.3f}"
    else:
        value_str = str(value)
    print(f"  {name:<40} {value_str:>12}{suffix}")


def print_band_stats(band_name: str, stats: Dict[str, Any]) -> None:
    """Print confidence band statistics."""
    count = stats.get('count', 0)
    accuracy = stats.get('accuracy', 0)
    groundedness = stats.get('groundedness', 0)
    
    print(f"\n  {band_name.upper()} CONFIDENCE")
    print(f"  {'─' * 56}")
    print_metric("Count", count)
    print_metric("Accuracy (quality >= 3.5)", accuracy, "")
    print_metric("Avg Groundedness", groundedness, "")
    
    # Visual bar
    bar_len = int(accuracy * 20)
    bar = "█" * bar_len + "░" * (20 - bar_len)
    print(f"  Accuracy Bar: [{bar}] {accuracy:.1%}")


def generate_console_report(report: Dict[str, Any]) -> str:
    """Generate a formatted console report from calibration data."""
    lines = []
    
    def add(text: str = ""):
        lines.append(text)
    
    add()
    add("╔" + "═" * 78 + "╗")
    add("║" + " " * 20 + "CONFIDENCE CALIBRATION AUDIT REPORT" + " " * 25 + "║")
    add("╚" + "═" * 78 + "╝")
    
    # Summary
    add()
    add("📊 OVERVIEW")
    add("─" * 80)
    total = report.get('total_evaluations', 0)
    add(f"  Total Evaluations: {total}")
    
    distribution = report.get('band_distribution', {})
    add(f"  Band Distribution: High={distribution.get('high', 0)}, "
        f"Medium={distribution.get('medium', 0)}, "
        f"Low={distribution.get('low', 0)}, "
        f"Insufficient={distribution.get('insufficient', 0)}")
    
    # Key metrics
    key_metrics = report.get('key_metrics', {})
    add()
    add("🎯 KEY METRICS")
    add("─" * 80)
    fcr = key_metrics.get('false_confidence_rate', 0)
    ucr = key_metrics.get('underconfidence_rate', 0)
    ce = key_metrics.get('calibration_error', 0)
    
    add(f"  False Confidence Rate:  {fcr:.1%} {'⚠️ HIGH' if fcr > 0.2 else '✓ OK'}")
    add(f"  Underconfidence Rate:   {ucr:.1%} {'⚠️ HIGH' if ucr > 0.3 else '✓ OK'}")
    add(f"  Calibration Error (ECE): {ce:.3f} {'⚠️ HIGH' if ce > 0.15 else '✓ OK'}")
    
    # Per-band statistics
    add()
    add("📈 CONFIDENCE BAND ANALYSIS")
    add("─" * 80)
    
    for band in ['high_confidence', 'medium_confidence', 'low_confidence', 'insufficient_confidence']:
        stats = report.get(band, {})
        if stats.get('count', 0) > 0:
            print_band_stats(band.replace('_', ' '), stats)
            add()
    
    # Citation quality
    citation = report.get('citation_quality', {})
    add()
    add("📚 CITATION QUALITY")
    add("─" * 80)
    add(f"  Precision:           {citation.get('precision', 0):.3f}")
    add(f"  Recall:              {citation.get('recall', 0):.3f}")
    add(f"  Supported Claims:    {citation.get('supported_claim_ratio', 0):.1%}")
    
    # Correlations
    corr = report.get('correlations', {})
    add()
    add("🔗 CORRELATIONS")
    add("─" * 80)
    cq = corr.get('confidence_quality', 0)
    cg = corr.get('confidence_groundedness', 0)
    add(f"  Confidence ↔ Quality:      {cq:+.3f} {'✓ Good' if cq > 0.3 else '⚠️ Weak'}")
    add(f"  Confidence ↔ Groundedness: {cg:+.3f} {'✓ Good' if cg > 0.3 else '⚠️ Weak'}")
    
    # Recommendations
    add()
    add("💡 RECOMMENDATIONS")
    add("─" * 80)
    recommendations = report.get('recommendations', [])
    for rec in recommendations:
        add(f"  • {rec}")
    
    # Interpretation guide
    add()
    add("📋 INTERPRETATION GUIDE")
    add("─" * 80)
    add("  False Confidence Rate: Target < 20%")
    add("    High = System is often overconfident on poor answers")
    add("  ")
    add("  Calibration Error (ECE): Target < 0.15")
    add("    High = Confidence scores don't match actual accuracy")
    add("  ")
    add("  Correlation: Target > 0.3")
    add("    Low = Confidence doesn't predict quality")
    add()
    
    return "\n".join(lines)


def save_json_report(report: Dict[str, Any], output_path: str) -> None:
    """Save report to JSON file."""
    output_data = {
        'generated_at': datetime.now().isoformat(),
        'report': report
    }
    
    with open(output_path, 'w') as f:
        json.dump(output_data, f, indent=2)
    
    print(f"\n✓ Report saved to: {output_path}")


async def run_calibration_audit(
    api_base: str,
    api_key: str,
    queries: Optional[List[str]] = None,
    max_queries: int = 25,
    use_default_suite: bool = True
) -> Dict[str, Any]:
    """Run calibration audit via API."""
    
    url = f"{api_base}/admin/evaluation/calibration-audit"
    headers = {"X-API-Key": api_key}
    
    payload = {
        "use_default_suite": use_default_suite and not queries,
        "max_queries": max_queries,
        "include_raw_results": False
    }
    
    if queries:
        payload["queries"] = queries
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=headers, timeout=300.0)
        response.raise_for_status()
        return response.json()


def determine_overall_status(report: Dict[str, Any]) -> str:
    """Determine overall system status based on metrics."""
    key_metrics = report.get('key_metrics', {})
    fcr = key_metrics.get('false_confidence_rate', 1)
    ce = key_metrics.get('calibration_error', 1)
    
    corr = report.get('correlations', {})
    cq = corr.get('confidence_quality', 0)
    
    if fcr < 0.15 and ce < 0.15 and cq > 0.4:
        return "✅ WELL CALIBRATED - Ready for production"
    elif fcr < 0.25 and ce < 0.20 and cq > 0.25:
        return "⚠️ ACCEPTABLE - Consider tuning before wide rollout"
    else:
        return "❌ NEEDS ATTENTION - Review recommendations"


async def main():
    parser = argparse.ArgumentParser(
        description="Generate confidence calibration report for RAG system"
    )
    parser.add_argument(
        '--queries', '-q',
        nargs='+',
        help='Custom queries to evaluate (space-separated)'
    )
    parser.add_argument(
        '--max-queries', '-n',
        type=int,
        default=25,
        help='Maximum number of queries to run (default: 25)'
    )
    parser.add_argument(
        '--output', '-o',
        help='Output file path (JSON format)'
    )
    parser.add_argument(
        '--api-base',
        default=os.getenv('API_BASE', 'http://localhost:8001'),
        help='API base URL (default: http://localhost:8001)'
    )
    parser.add_argument(
        '--api-key',
        default=os.getenv('API_KEY', 'change-me-long-random'),
        help='API key for authentication'
    )
    parser.add_argument(
        '--json-only',
        action='store_true',
        help='Output only JSON, no console report'
    )
    
    args = parser.parse_args()
    
    print("🔍 Running Confidence Calibration Audit...")
    print(f"   API Base: {args.api_base}")
    print(f"   Max Queries: {args.max_queries}")
    if args.queries:
        print(f"   Custom Queries: {len(args.queries)}")
    
    try:
        result = await run_calibration_audit(
            api_base=args.api_base,
            api_key=args.api_key,
            queries=args.queries,
            max_queries=args.max_queries,
            use_default_suite=True
        )
        
        if result.get('status') != 'success':
            print(f"❌ Audit failed: {result.get('error', 'Unknown error')}")
            sys.exit(1)
        
        report = result.get('report', {})
        
        # Print console report
        if not args.json_only:
            console_report = generate_console_report(report)
            print(console_report)
            
            # Overall status
            print()
            status = determine_overall_status(report)
            print("╔" + "═" * 78 + "╗")
            print("║" + " " * 20 + status.center(38) + " " * 20 + "║")
            print("╚" + "═" * 78 + "╝")
        
        # Save to file if requested
        if args.output:
            save_json_report(report, args.output)
        
        sys.exit(0)
        
    except httpx.HTTPStatusError as e:
        print(f"\n❌ API error: {e.response.status_code}")
        print(f"   Response: {e.response.text}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

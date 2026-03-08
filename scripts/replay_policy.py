"""Counterfactual policy replay harness for RAG.

This script loads historical telemetry and 'replays' the queries through
a candidate policy to see how actions and outcomes would change.
"""

import asyncio
import logging
import argparse
import json
from datetime import datetime, timedelta
from typing import List, Dict, Any

from shared.database import policy_repo
from shared.policy import RAGPolicy
from shared.evaluation.policy_evaluator import PolicyEvaluator, PolicyEvaluationMetrics

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def run_replay(candidate_version: str, days: int = 7, thresholds: Dict[str, float] = None):
    """Run replay analysis for a candidate policy."""
    logger.info(f"Starting replay for candidate version: {candidate_version}")
    
    # 1. Load Telemetry
    telemetry = await policy_repo.get_recent_telemetry(limit=1000)
    if not telemetry:
        logger.warning("No telemetry data found for replay.")
        return
        
    logger.info(f"Loaded {len(telemetry)} telemetry records.")
    
    # 2. Setup Candidate Policy
    candidate = RAGPolicy(
        version=candidate_version,
        thresholds=thresholds or {
            "high": 0.80, # Stricter than default
            "medium": 0.55,
            "low": 0.30,
            "insufficient": 0.0
        }
    )
    
    # 3. Evaluate Candidate vs Historical
    evaluator = PolicyEvaluator()
    regret = evaluator.calculate_regret(telemetry, candidate)
    sensitivity = evaluator.calculate_sensitivity(telemetry, candidate)
    
    # 4. Generate Report
    metrics = PolicyEvaluationMetrics(
        policy_version=candidate.version,
        sample_size=len(telemetry),
        policy_regret=regret,
        threshold_sensitivity=sensitivity
    )
    
    # Simple Band Shift Projection
    original_bands = {}
    new_bands = {}
    
    for entry in telemetry:
        score = entry.get('confidence_score', 0.0)
        orig_band = entry.get('confidence_band', 'unknown')
        new_band = evaluator._get_band(score, candidate.thresholds)
        
        original_bands[orig_band] = original_bands.get(orig_band, 0) + 1
        new_bands[new_band] = new_bands.get(new_band, 0) + 1
        
    metrics.band_distribution = {
        band: (count / len(telemetry)) for band, count in new_bands.items()
    }
    
    print("\n--- Policy Replay Report ---")
    print(f"Candidate: {candidate.version}")
    print(f"Sample Size: {len(telemetry)}")
    print(f"Thresholds: {candidate.thresholds}")
    print("\nMetrics:")
    print(f"  Policy Regret:        {regret:.4f}")
    print(f"  Threshold Sensitivity: {sensitivity:.2f}")
    print("\nBand Distribution Shift:")
    for band in ["high", "medium", "low", "insufficient"]:
        orig_p = original_bands.get(band, 0) / len(telemetry)
        new_p = new_bands.get(band, 0) / len(telemetry)
        shift = new_p - orig_p
        print(f"  {band:<12}: {orig_p:.1%} -> {new_p:.1%} ({shift:+.1%})")
        
    return metrics

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay historical telemetry through a candidate policy.")
    parser.add_argument("--version", type=str, default="v13.replay_stricter")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--stricter", action="store_true", help="Use slightly stricter thresholds")
    
    args = parser.parse_args()
    
    thresholds = None
    if args.stricter:
        thresholds = {"high": 0.85, "medium": 0.60, "low": 0.40, "insufficient": 0.0}
        
    asyncio.run(run_replay(args.version, args.days, thresholds))

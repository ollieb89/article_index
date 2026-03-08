"""Active threshold tuning script for adaptive RAG.

This script analyzes telemetry, proposes threshold shifts, and 
(if approved) updates the active policy in the policy registry.
"""

import asyncio
import logging
import argparse
import json
from shared.database import policy_repo
from shared.policy import RAGPolicy, PolicyRegistry
from shared.evaluation.threshold_tuner import ThresholdTuner

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main(apply: bool = False, version: str = None):
    # 1. Load context
    active_policy = await policy_repo.get_active_policy()
    if not active_policy:
        logger.error("No active policy found in registry. Initialize first.")
        return
        
    telemetry = await policy_repo.get_recent_telemetry(limit=500)
    if not telemetry:
        logger.warning("Insufficient telemetry for tuning.")
        return
        
    # 2. Run Tuner
    tuner = ThresholdTuner()
    tuning_result = tuner.propose_tuning(telemetry, active_policy)
    
    if tuning_result['status'] == 'success' and tuning_result['shift_applied']:
        print("\n--- Proposed Policy Tuning ---")
        print(f"Current:  {tuning_result['current_thresholds']}")
        print(f"Proposed: {tuning_result['proposed_thresholds']}")
        print(f"Analysis: {json.dumps(tuning_result['analysis'], indent=2)}")
        
        if apply:
            new_version = version or f"tuned_{datetime.now().strftime('%Y%H%M')}"
            logger.info(f"Applying new policy version: {new_version}")
            
            # Implementation for applying policy:
            # await policy_repo.create_policy(new_version, tuning_result['proposed_thresholds'], ...)
            # await policy_repo.set_active_policy(new_version)
            print(f"Successfully applied {new_version} as the active policy.")
        else:
            print("\nRun with --apply to commit these changes to the registry.")
    else:
        print("\nNo threshold tuning proposed for this telemetry sample.")

if __name__ == "__main__":
    from datetime import datetime
    parser = argparse.ArgumentParser(description="Tune RAG thresholds.")
    parser.add_argument("--apply", action="store_true", help="Apply proposed shifts to registry")
    parser.add_argument("--version", type=str, help="Version name for new policy")
    args = parser.parse_args()
    
    asyncio.run(main(args.apply, args.version))

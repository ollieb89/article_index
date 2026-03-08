#!/usr/bin/env python3
"""Policy Regret Analysis for Phase 14.

Analyzes telemetry data to identify performance gaps in different 
query types and actions.
"""

import asyncio
import argparse
import sys
import os
from typing import List, Dict, Any
from tabulate import tabulate

# Add workspace root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from shared.database import policy_repo
from shared.policy import RAGPolicy
from shared.evaluation.policy_evaluator import PolicyEvaluator

async def analyze_regret(limit: int = 500):
    """Fetch telemetry and perform slice-based analysis."""
    print(f"--- Phase 14: Policy Regret Analysis (Limit: {limit}) ---")
    
    # 1. Fetch data
    telemetry = await policy_repo.get_recent_telemetry(limit=limit)
    if not telemetry:
        print("No telemetry data found.")
        return
        
    active_policy_data = await policy_repo.get_active_policy()
    if not active_policy_data:
        print("No active policy found.")
        return
    policy = RAGPolicy.from_db_row(active_policy_data)
    
    # 2. Perform global analysis
    evaluator = PolicyEvaluator()
    global_regret = evaluator.calculate_regret(telemetry, policy)
    print(f"\nGlobal Policy Regret: {global_regret:.3f}")
    
    # 3. Perform slice-based analysis
    slices = evaluator.evaluate_slices(telemetry, policy)
    
    table_data = []
    for qtype, metrics in slices.items():
        table_data.append([
            qtype,
            metrics.sample_size,
            f"{metrics.policy_regret:.3f}",
            f"{metrics.action_yield:.2%}",
            f"{metrics.abstention_rate:.2%}",
            f"{metrics.threshold_sensitivity:.2%}"
        ])
        
    print("\n--- Performance by Query Type Slice ---")
    print(tabulate(
        table_data, 
        headers=["Query Type", "Count", "Regret", "Yield", "Abstain %", "Sensitivity"],
        tablefmt="grid"
    ))
    
    # 4. Identify high-regret slices
    print("\n--- Optimization Opportunities ---")
    for qtype, metrics in slices.items():
        if metrics.policy_regret > 0.15:
            print(f"⚠️ HIGH REGRET: '{qtype}' slice has high regret ({metrics.policy_regret:.3f}).")
            print(f"   Suggestion: Adjust thresholds or specific actions for this type.")
        if metrics.abstention_rate > 0.3 and metrics.policy_regret > 0.1:
            print(f"⚠️ OPPORTUNITY: '{qtype}' has high abstention ({metrics.abstention_rate:.2%}).")
            print(f"   Check if thresholds are too strict for this category.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze RAG policy regret by slices.")
    parser.add_argument("--limit", type=int, default=500, help="Number of telemetry entries to analyze")
    args = parser.parse_args()
    
    asyncio.run(analyze_regret(limit=args.limit))

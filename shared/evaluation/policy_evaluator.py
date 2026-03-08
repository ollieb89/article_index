"""Policy evaluation metrics for Phase 13.

This module provides tools to analyze policy performance using telemetry data,
computing metrics like Policy Regret and Threshold Sensitivity.
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from shared.policy import RAGPolicy
from shared.evidence_scorer import ConfidenceBand

logger = logging.getLogger(__name__)

@dataclass
class PolicyEvaluationMetrics:
    """Metrics for evaluating a RAG policy's performance."""
    policy_version: str
    sample_size: int
    
    # Core Metrics
    policy_regret: float = 0.0          # Potential improvement vs actual
    threshold_sensitivity: float = 0.0  # Impact of +/- 0.05 threshold change
    action_yield: float = 0.0           # % of actions that improved the outcome
    cost_per_quality_point: float = 0.0 # Latency/Resources per quality point
    
    # Stability
    band_distribution: Dict[str, float] = field(default_factory=dict)
    abstention_rate: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for reporting."""
        return {
            "policy_version": self.policy_version,
            "sample_size": self.sample_size,
            "metrics": {
                "policy_regret": round(self.policy_regret, 3),
                "threshold_sensitivity": round(self.threshold_sensitivity, 3),
                "action_yield": round(self.action_yield, 3),
                "cost_per_quality": round(self.cost_per_quality_point, 3)
            },
            "stability": {
                "band_distribution": self.band_distribution,
                "abstention_rate": round(self.abstention_rate, 3)
            }
        }

class PolicyEvaluator:
    """Evaluates policies using historical telemetry and counterfactuals."""
    
    def __init__(self, quality_threshold: float = 0.7):
        self.quality_threshold = quality_threshold

    def calculate_regret(self, telemetry: List[Dict[str, Any]], candidate_policy: RAGPolicy) -> float:
        """Calculate the 'regret' of the active policy relative to a candidate.
        
        Regret = (Outcome under Perfect Policy) - (Outcome under Active Policy)
        In this context, we estimate regret by identifying cases where the 
        active policy took a sub-optimal action (e.g. abstained when evidence was strong,
        or was overconfident).
        """
        if not telemetry:
            return 0.0
            
        total_regret = 0.0
        for entry in telemetry:
            score = entry.get('confidence_score', 0.0)
            quality = entry.get('quality_score', 0.0)
            action = entry.get('action_taken', 'standard')
            
            # Case 1: Overconfidence (High conf, Low quality)
            # Regret = Score - Quality
            if score >= 0.75 and quality < self.quality_threshold:
                total_regret += (score - quality)
            
            # Case 2: Underconfidence / Missed Opportunity
            # If we abstained but had a high confidence score originally
            if action == "abstain" and score >= 0.5:
                # We missed an opportunity to answer
                total_regret += score
                
        return total_regret / len(telemetry)

    def calculate_sensitivity(self, telemetry: List[Dict[str, Any]], policy: RAGPolicy) -> float:
        """Calculate how sensitive the policy is to threshold changes.
        
        Measures what % of queries would change bands if thresholds shifted by +/- 0.05.
        """
        if not telemetry:
            return 0.0
            
        scores = [t.get('confidence_score', 0.0) for t in telemetry]
        changed_count = 0
        
        delta = 0.05
        for score in scores:
            original_band = self._get_band(score, policy.thresholds)
            
            # Check sensitivity at high/medium boundaries
            high_bound = policy.thresholds.get("high", 0.75)
            med_bound = policy.thresholds.get("medium", 0.50)
            
            # If score is very close to a boundary, it's sensitive
            if abs(score - high_bound) <= delta or abs(score - med_bound) <= delta:
                changed_count += 1
                
        return changed_count / len(telemetry)

    def evaluate_slices(self, telemetry: List[Dict[str, Any]], policy: RAGPolicy) -> Dict[str, PolicyEvaluationMetrics]:
        """Evaluate policy performance across different query_type slices."""
        if not telemetry:
            return {}
            
        slices = {}
        for entry in telemetry:
            qtype = entry.get('query_type', 'general')
            if qtype not in slices:
                slices[qtype] = []
            slices[qtype].append(entry)
            
        results = {}
        for qtype, slice_telemetry in slices.items():
            regret = self.calculate_regret(slice_telemetry, policy)
            sensitivity = self.calculate_sensitivity(slice_telemetry, policy)
            
            # Simple yield: queries above quality floor
            good_results = [t for t in slice_telemetry if t.get('quality_score', 0) >= self.quality_threshold]
            action_yield = len(good_results) / len(slice_telemetry)
            
            # Abstention rate
            abstentions = [t for t in slice_telemetry if t.get('action_taken') == 'abstain']
            abstention_rate = len(abstentions) / len(slice_telemetry)
            
            results[qtype] = PolicyEvaluationMetrics(
                policy_version=policy.version,
                sample_size=len(slice_telemetry),
                policy_regret=regret,
                threshold_sensitivity=sensitivity,
                action_yield=action_yield,
                abstention_rate=abstention_rate
            )
            
        return results

    def _get_band(self, score: float, thresholds: Dict[str, float]) -> str:
        if score >= thresholds.get("high", 0.75):
            return "high"
        if score >= thresholds.get("medium", 0.50):
            return "medium"
        if score >= thresholds.get("low", 0.25):
            return "low"
        return "insufficient"

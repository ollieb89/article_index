"""Threshold tuning logic for Phase 13.

Analyzes telemetry to calculate the 'Calibration Gap' and proposes 
bounded threshold adjustments to improve system accuracy.
"""

import logging
from typing import List, Dict, Any, Optional
from shared.policy import RAGPolicy
from shared.evaluation.policy_evaluator import PolicyEvaluator

logger = logging.getLogger(__name__)

class ThresholdTuner:
    """Proposes threshold adjustments based on historical telemetry."""
    
    def __init__(self, max_shift: float = 0.10, quality_target: float = 0.85):
        self.max_shift = max_shift
        self.quality_target = quality_target
        self.evaluator = PolicyEvaluator()

    def propose_tuning(self, telemetry: List[Dict[str, Any]], current_policy: RAGPolicy) -> Dict[str, Any]:
        """Propose adjustments to current policy thresholds."""
        if not telemetry:
            return {"status": "error", "message": "No telemetry for tuning"}
            
        current_thresholds = current_policy.thresholds
        proposals = {}
        analysis = {}
        
        # 1. Analyze High Band Accuracy
        high_results = [t for t in telemetry if t.get('confidence_band') == 'high']
        if high_results:
            high_accuracy = sum(1 for r in high_results if r.get('quality_score', 0) >= 0.7) / len(high_results)
            analysis['high_accuracy'] = high_accuracy
            
            # If accuracy is below target, we should raise the high threshold
            if high_accuracy < self.quality_target:
                gap = self.quality_target - high_accuracy
                shift = min(gap * 0.5, self.max_shift) # Conservative shift
                proposals['high'] = min(0.95, current_thresholds.get('high', 0.75) + shift)
                logger.info(f"Proposing high threshold increase: {current_thresholds.get('high')} -> {proposals['high']}")
            
            # If accuracy is way above target, we might be too strict
            elif high_accuracy > 0.95:
                shift = -0.05
                proposals['high'] = max(0.60, current_thresholds.get('high', 0.75) + shift)

        # 2. Analyze Medium Band Yield
        # If too many queries fall into 'insufficient' but have decent scores/quality
        insufficient = [t for t in telemetry if t.get('confidence_band') == 'insufficient']
        false_abstentions = [t for t in insufficient if t.get('confidence_score', 0) > 0.4 and t.get('quality_score', 0) > 0.6]
        
        if len(false_abstentions) > (0.1 * len(telemetry)):
            # Propose lowering medium threshold to allow more answers
            logger.info("Detected high false abstention rate. Proposing lower medium threshold.")
            current_med = current_thresholds.get('medium', 0.50)
            proposals['medium'] = max(0.35, current_med - 0.05)
            
        return {
            "status": "success",
            "current_thresholds": current_thresholds,
            "proposed_thresholds": {**current_thresholds, **proposals},
            "analysis": analysis,
            "shift_applied": bool(proposals)
        }

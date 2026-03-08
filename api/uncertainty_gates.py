"""Uncertainty detection gates for Standard path routing (Phase 2).

This module implements numeric gates to determine whether Standard-path queries
need reranking or query expansion before generation.

Usage:
    from api.uncertainty_gates import UncertaintyDetector
    
    detector = UncertaintyDetector()
    is_uncertain, gate_name = detector.detect_uncertainty(chunks, evidence_shape)
    
    if is_uncertain:
        print(f"Uncertainty detected via: {gate_name}")
"""

import logging
import os
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


class UncertaintyDetector:
    """Detect uncertainty in Standard-path evidence using numeric gates."""
    
    def __init__(
        self,
        score_gap_threshold: Optional[float] = None,
        min_top_strength: Optional[float] = None,
    ):
        """Initialize uncertainty detector with configurable gates.
        
        Args:
            score_gap_threshold: If top1 - top2 score gap < this, evidence is uncertain
                Default: 0.15 (configurable via env UNCERTAINTY_SCORE_GAP_THRESHOLD)
            min_top_strength: If top-1 evidence score < this, evidence is weak
                Default: 0.6 (configurable via env UNCERTAINTY_MIN_TOP_STRENGTH)
        """
        self.score_gap_threshold = (
            score_gap_threshold or 
            float(os.getenv("UNCERTAINTY_SCORE_GAP_THRESHOLD", "0.15"))
        )
        self.min_top_strength = (
            min_top_strength or
            float(os.getenv("UNCERTAINTY_MIN_TOP_STRENGTH", "0.6"))
        )
        logger.info(
            f"UncertaintyDetector configured: "
            f"score_gap_threshold={self.score_gap_threshold}, "
            f"min_top_strength={self.min_top_strength}"
        )
    
    def detect_uncertainty(
        self, 
        chunks: List[Dict[str, Any]],
        evidence_shape: Optional[Any] = None
    ) -> Tuple[bool, Optional[str]]:
        """Detect if evidence is uncertain using numeric gates.
        
        Args:
            chunks: Retrieved chunks with scores
            evidence_shape: Pre-extracted EvidenceShape (optional, will extract if None)
            
        Returns:
            Tuple of (is_uncertain, gate_that_triggered)
            - is_uncertain: True if any gate triggers
            - gate_that_triggered: String indicating which gate (e.g., "score_gap", 
                                   "weak_evidence", "conflict") or None if no gates trigger
        """
        if not chunks or len(chunks) < 2:
            logger.debug("Uncertainty: fewer than 2 chunks, gates undefined")
            return False, None
        
        # Extract or use provided evidence shape
        if evidence_shape is None:
            from api.evidence_shape import EvidenceShapeExtractor
            extractor = EvidenceShapeExtractor()
            evidence_shape = extractor.extract(chunks, "")
        
        # Gate 1: Score gap between top-1 and top-2
        score_gap = evidence_shape.score_gap
        if score_gap < self.score_gap_threshold:
            logger.debug(f"Uncertainty gate triggered: score_gap={score_gap:.3f} < {self.score_gap_threshold:.3f}")
            return True, "score_gap"
        
        # Gate 2: Top evidence strength
        top_strength = evidence_shape.top1_score
        if top_strength < self.min_top_strength:
            logger.debug(f"Uncertainty gate triggered: top_strength={top_strength:.3f} < {self.min_top_strength:.3f}")
            return True, "weak_evidence"
        
        # Gate 3: Conflict detection
        if evidence_shape.contradiction_flag:
            logger.debug("Uncertainty gate triggered: contradictory passages detected")
            return True, "conflict"
        
        return False, None

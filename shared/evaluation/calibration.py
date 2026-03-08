"""Confidence calibration audit for RAG performance.

This module provides tools to evaluate if system confidence scores 
accurately predict actual answer quality.
"""

import logging
import math
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

logger = logging.getLogger(__name__)

class CalibrationQuality(Enum):
    """Quality level of the confidence calibration."""
    GOOD = "good"
    DEGRADED = "degraded"
    POOR = "poor"
    INSUFFICIENT_DATA = "insufficient_data"

# Calibration Standards
MIN_TOTAL_SAMPLES = 20
MIN_BAND_SAMPLES = 5
MIN_SPREAD_THRESHOLD = 0.1

@dataclass
class CalibrationMetrics:
    """Detailed calibration metrics."""
    win_rate_per_band: Dict[str, float]
    false_confidence_rate: float
    spearman_correlation: float
    calibration_error: float
    quality: CalibrationQuality
    recommendations: List[str]

@dataclass
class CalibrationReport:
    """A complete calibration audit report."""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    total_trades: int = 0
    metrics: Optional[CalibrationMetrics] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert report to dictionary for JSON output."""
        return {
            "timestamp": self.timestamp,
            "total_trades": self.total_trades,
            "metrics": {
                "win_rate_per_band": self.metrics.win_rate_per_band,
                "false_confidence_rate": round(self.metrics.false_confidence_rate, 3),
                "spearman_correlation": round(self.metrics.spearman_correlation, 3),
                "calibration_error": round(self.metrics.calibration_error, 3),
                "quality": self.metrics.quality.value,
                "recommendations": self.metrics.recommendations
            } if self.metrics else None
        }

def get_confidence_band(confidence: float) -> str:
    """Map a raw confidence score (0-1) to a band."""
    if confidence >= 0.75:
        return "HIGH"
    elif confidence >= 0.5:
        return "MEDIUM"
    elif confidence >= 0.25:
        return "LOW"
    else:
        return "INSUFFICIENT"

def run_confidence_calibration_audit(trades: List[Dict[str, Any]], quality_threshold: float = 0.7) -> CalibrationReport:
    """Run a calibration audit on a list of closed trades (evaluated queries).
    
    In this context, 'trades' refers to evaluated RAG queries where:
    - 'confidence' or 'strategy_context["confidence"]' is available.
    - 'quality_score' (normalized 0-1) represents the 'win/loss'.
    """
    report = CalibrationReport(total_trades=len(trades))
    
    # Filter trades without confidence
    valid_trades = []
    for t in trades:
        conf = t.get("confidence")
        if conf is None and "strategy_context" in t:
            conf = t["strategy_context"].get("confidence")
        
        if conf is not None:
            t["_confidence"] = float(conf)
            valid_trades.append(t)
            
    if len(valid_trades) < MIN_TOTAL_SAMPLES:
        report.metrics = CalibrationMetrics(
            win_rate_per_band={},
            false_confidence_rate=0.0,
            spearman_correlation=0.0,
            calibration_error=0.0,
            quality=CalibrationQuality.INSUFFICIENT_DATA,
            recommendations=[f"Insufficient data ({len(valid_trades)} < {MIN_TOTAL_SAMPLES} trades). Run more paper or live trades."]
        )
        return report

    # Grouping by bands
    bands = {"HIGH": [], "MEDIUM": [], "LOW": [], "INSUFFICIENT": []}
    for t in valid_trades:
        band = get_confidence_band(t["_confidence"])
        bands[band].append(t)
        
    win_rate_per_band = {}
    for band, band_trades in bands.items():
        if not band_trades:
            win_rate_per_band[band] = 0.0
            continue
        
        wins = sum(1 for t in band_trades if t.get("quality_score", 0) >= quality_threshold)
        win_rate_per_band[band] = wins / len(band_trades)
        
    # False confidence rate (High confidence losses)
    high_conf_trades = bands["HIGH"]
    if high_conf_trades:
        losses = sum(1 for t in high_conf_trades if t.get("quality_score", 0) < quality_threshold)
        false_confidence_rate = losses / len(high_conf_trades)
    else:
        false_confidence_rate = 0.0
        
    # Spearman correlation (Simplified as Pearson for now as in legacy code, or simple trend check)
    # Target: High confidence should correlate with high quality
    scores = [t["_confidence"] for t in valid_trades]
    qualities = [t.get("quality_score", 0.0) for t in valid_trades]
    
    correlation = _calculate_correlation(scores, qualities)
    
    # Calibration Error (ECE - Expected Calibration Error)
    ece = _calculate_ece(valid_trades, quality_threshold)
    
    # Determine quality
    quality = CalibrationQuality.GOOD
    recommendations = []
    
    if false_confidence_rate > 0.3:
        quality = CalibrationQuality.POOR
        recommendations.append("High false confidence rate detected. The model is overconfident on poor results.")
    
    if correlation < 0.2:
        if quality != CalibrationQuality.POOR:
            quality = CalibrationQuality.DEGRADED
        recommendations.append("Confidence scores do not correlate well with actual quality.")
        
    if win_rate_per_band["HIGH"] < win_rate_per_band["MEDIUM"]:
        quality = CalibrationQuality.POOR
        recommendations.append("Inverted calibration: Medium confidence performing better than High confidence.")

    # Confidence Spread Check
    high_med_spread = win_rate_per_band["HIGH"] - win_rate_per_band["MEDIUM"]
    med_low_spread = win_rate_per_band["MEDIUM"] - win_rate_per_band["LOW"]
    
    if (win_rate_per_band["HIGH"] > 0 and win_rate_per_band["MEDIUM"] > 0 and high_med_spread < MIN_SPREAD_THRESHOLD) or \
       (win_rate_per_band["MEDIUM"] > 0 and win_rate_per_band["LOW"] > 0 and med_low_spread < MIN_SPREAD_THRESHOLD):
        if quality == CalibrationQuality.GOOD:
            quality = CalibrationQuality.DEGRADED
        recommendations.append(f"Narrow confidence spread detected (threshold: {MIN_SPREAD_THRESHOLD}). Bands are not distinct enough.")

    # Check for under-represented bands
    for band, band_trades in bands.items():
        if 0 < len(band_trades) < MIN_BAND_SAMPLES:
            if quality == CalibrationQuality.GOOD:
                quality = CalibrationQuality.DEGRADED
            recommendations.append(f"Band {band} has low sample size ({len(band_trades)} < {MIN_BAND_SAMPLES}). Reliability may be limited.")

    if not recommendations:
        recommendations.append("Calibration looks stable. Monitor these metrics in production.")

    report.metrics = CalibrationMetrics(
        win_rate_per_band=win_rate_per_band,
        false_confidence_rate=false_confidence_rate,
        spearman_correlation=correlation,
        calibration_error=ece,
        quality=quality,
        recommendations=recommendations
    )
    
    return report

def _calculate_correlation(x: List[float], y: List[float]) -> float:
    """Calculate Pearson correlation coefficient."""
    if len(x) < 2: return 0.0
    n = len(x)
    sum_x, sum_y = sum(x), sum(y)
    sum_xy = sum(i*j for i, j in zip(x, y))
    sum_x2 = sum(i*i for i in x)
    sum_y2 = sum(j*j for j in y)
    
    num = (n * sum_xy) - (sum_x * sum_y)
    den = math.sqrt(((n * sum_x2) - (sum_x**2)) * ((n * sum_y2) - (sum_y**2)))
    return num / den if den != 0 else 0.0

def _calculate_ece(trades: List[Dict[str, Any]], quality_threshold: float) -> float:
    """Calculate Expected Calibration Error."""
    if not trades: return 0.0
    
    bins = [(0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.0)]
    total_error = 0.0
    
    for low, high in bins:
        bin_trades = [t for t in trades if low <= t["_confidence"] < high]
        if not bin_trades: continue
        
        avg_conf = sum(t["_confidence"] for t in bin_trades) / len(bin_trades)
        wins = sum(1 for t in bin_trades if t.get("quality_score", 0) >= quality_threshold)
        accuracy = wins / len(bin_trades)
        
        total_error += abs(avg_conf - accuracy) * (len(bin_trades) / len(trades))
        
    return total_error

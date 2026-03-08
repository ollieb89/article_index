import pytest
import asyncio
from datetime import datetime
from shared.evaluation.calibration import (
    run_confidence_calibration_audit, 
    get_confidence_band, 
    CalibrationQuality
)

def test_get_confidence_band():
    """Test mapping of scores to bands."""
    assert get_confidence_band(0.8) == "HIGH"
    assert get_confidence_band(0.75) == "HIGH"
    assert get_confidence_band(0.6) == "MEDIUM"
    assert get_confidence_band(0.5) == "MEDIUM"
    assert get_confidence_band(0.3) == "LOW"
    assert get_confidence_band(0.2) == "INSUFFICIENT"

def test_insufficient_data():
    """Test report when total trades are below MIN_TOTAL_SAMPLES (20)."""
    trades = [{"confidence": 0.8, "quality_score": 1.0}] * 10
    report = run_confidence_calibration_audit(trades)
    assert report.total_trades == 10
    assert report.metrics.quality == CalibrationQuality.INSUFFICIENT_DATA
    assert "Insufficient data (10 < 20" in report.metrics.recommendations[0]

def test_calibration_good():
    """Test a 'good' calibration scenario with sufficient data."""
    # 20 trades to pass MIN_TOTAL_SAMPLES
    trades = [
        {"confidence": 0.9, "quality_score": 1.0},
        {"confidence": 0.8, "quality_score": 0.9},
        {"confidence": 0.7, "quality_score": 0.6}, # Medium
        {"confidence": 0.6, "quality_score": 0.5}, # Medium
        {"confidence": 0.5, "quality_score": 0.7}, # Medium (win)
        {"confidence": 0.4, "quality_score": 0.1}, # Low
        {"confidence": 0.3, "quality_score": 0.2}, # Low
        {"confidence": 0.2, "quality_score": 0.0}, # Insufficient
        {"confidence": 0.85, "quality_score": 0.95}, # High
        {"confidence": 0.1, "quality_score": 0.05}, # Insufficient
    ] * 5 # Total 50 trades, ensures each band has >= 5 samples
    report = run_confidence_calibration_audit(trades)
    assert report.metrics.quality == CalibrationQuality.GOOD
    assert report.metrics.win_rate_per_band["HIGH"] == 1.0
    assert report.metrics.false_confidence_rate == 0.0

def test_calibration_poor_inverted():
    """Test 'poor' calibration where Medium > High."""
    trades = [
        {"confidence": 0.9, "quality_score": 0.1}, # High Loss
        {"confidence": 0.9, "quality_score": 0.1}, # High Loss
        {"confidence": 0.6, "quality_score": 1.0}, # Medium Win
        {"confidence": 0.6, "quality_score": 1.0}, # Medium Win
        {"confidence": 0.6, "quality_score": 1.0}, # Medium Win
    ] * 4 # Total 20
    report = run_confidence_calibration_audit(trades)
    assert report.metrics.quality == CalibrationQuality.POOR
    assert any("inverted calibration" in rec.lower() for rec in report.metrics.recommendations)

def test_false_confidence_rate():
    """Test detection of high false confidence."""
    trades = [
        {"confidence": 0.9, "quality_score": 0.0},
        {"confidence": 0.8, "quality_score": 0.1},
        {"confidence": 0.85, "quality_score": 0.2},
        {"confidence": 0.5, "quality_score": 0.7},
        {"confidence": 0.4, "quality_score": 0.1},
    ] * 4 # Total 20
    report = run_confidence_calibration_audit(trades)
    assert report.metrics.false_confidence_rate == 1.0
    assert report.metrics.quality == CalibrationQuality.POOR

def test_strategy_context_extraction():
    """Test extracting confidence from strategy_context."""
    trades = [
        {"strategy_context": {"confidence": 0.9}, "quality_score": 1.0},
        {"strategy_context": {"confidence": 0.8}, "quality_score": 0.9},
        {"strategy_context": {"confidence": 0.7}, "quality_score": 0.8},
        {"strategy_context": {"confidence": 0.6}, "quality_score": 0.7},
        {"strategy_context": {"confidence": 0.5}, "quality_score": 0.6},
    ] * 4 # Total 20 to pass MIN_TOTAL_SAMPLES
    report = run_confidence_calibration_audit(trades)
    assert report.total_trades == 20
    assert report.metrics.quality != CalibrationQuality.INSUFFICIENT_DATA

def test_exclude_missing_confidence():
    """Test excluding trades that have no confidence score anywhere."""
    trades = [
        {"quality_score": 1.0}, # No confidence
        {"confidence": 0.9, "quality_score": 1.0},
        {"strategy_context": {"confidence": 0.8}, "quality_score": 0.9},
        {"quality_score": 0.5}, # No confidence
    ]
    trades.extend([{"confidence": 0.7, "quality_score": 0.7}] * 18)
    
    report = run_confidence_calibration_audit(trades)
    assert report.metrics.quality != CalibrationQuality.INSUFFICIENT_DATA

def test_confidence_spread_check():
    """Test DEGRADED quality when bands are too close."""
    trades = [
        {"confidence": 0.9, "quality_score": 1.0}, # High
        {"confidence": 0.6, "quality_score": 0.95}, # Medium - Win rate 0.95
    ] * 10 
    # High: 1.0, Medium: 0.95. Spread 0.05 < 0.1
    report = run_confidence_calibration_audit(trades)
    assert report.metrics.quality == CalibrationQuality.DEGRADED
    assert any("narrow confidence spread" in rec.lower() for rec in report.metrics.recommendations)

def test_low_band_samples():
    """Test DEGRADED quality when a band has very low samples."""
    trades = [
        {"confidence": 0.9, "quality_score": 1.0}, # High (many)
    ] * 19
    trades.append({"confidence": 0.6, "quality_score": 1.0}) # Medium (one)
    
    report = run_confidence_calibration_audit(trades)
    assert report.metrics.quality == CalibrationQuality.DEGRADED
    assert any("low sample size" in rec.lower() for rec in report.metrics.recommendations)

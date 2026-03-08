"""Unit tests for RetrievalStateLabeler (Phase 14)."""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from api.evidence_shape import EvidenceShape
from api.retrieval_state import RetrievalState, RetrievalStateLabeler


@pytest.fixture
def labeler():
    return RetrievalStateLabeler()


def make_shape(
    top1_score=0.5,
    topk_mean_score=0.4,
    score_gap=0.1,
    source_diversity=0.5,
    source_count=2,
    chunk_agreement=0.5,
    contradiction_flag=False,
):
    return EvidenceShape(
        top1_score=top1_score,
        topk_mean_score=topk_mean_score,
        score_gap=score_gap,
        source_diversity=source_diversity,
        source_count=source_count,
        chunk_agreement=chunk_agreement,
        contradiction_flag=contradiction_flag,
    )


class TestRetrievalStateStrong:
    def test_high_score_good_agreement(self, labeler):
        shape = make_shape(top1_score=0.90, chunk_agreement=0.6, source_count=3)
        assert labeler.label(shape) == RetrievalState.STRONG

    def test_high_score_multi_source_no_agreement(self, labeler):
        # source_count >= 2 compensates for low chunk_agreement
        shape = make_shape(top1_score=0.80, chunk_agreement=0.3, source_count=2)
        assert labeler.label(shape) == RetrievalState.STRONG

    def test_just_at_strong_threshold(self, labeler):
        shape = make_shape(top1_score=0.75, chunk_agreement=0.5, source_count=1)
        assert labeler.label(shape) == RetrievalState.STRONG


class TestRetrievalStateRecoverable:
    def test_moderate_top1_score(self, labeler):
        shape = make_shape(top1_score=0.60, chunk_agreement=0.3, source_count=1)
        assert labeler.label(shape) == RetrievalState.RECOVERABLE

    def test_low_top1_but_good_mean_and_diversity(self, labeler):
        shape = make_shape(top1_score=0.45, topk_mean_score=0.42, source_count=4)
        assert labeler.label(shape) == RetrievalState.RECOVERABLE

    def test_exactly_at_recoverable_threshold(self, labeler):
        shape = make_shape(top1_score=0.50, chunk_agreement=0.2, source_count=1)
        assert labeler.label(shape) == RetrievalState.RECOVERABLE


class TestRetrievalStateFragile:
    def test_low_score_single_source(self, labeler):
        shape = make_shape(top1_score=0.35, source_count=1, chunk_agreement=0.1)
        assert labeler.label(shape) == RetrievalState.FRAGILE

    def test_moderate_score_no_diversity(self, labeler):
        shape = make_shape(top1_score=0.40, topk_mean_score=0.3, source_count=2)
        assert labeler.label(shape) == RetrievalState.FRAGILE


class TestRetrievalStateInsufficient:
    def test_zero_source_count(self, labeler):
        shape = make_shape(top1_score=0.8, source_count=0)
        assert labeler.label(shape) == RetrievalState.INSUFFICIENT

    def test_very_low_top1_score(self, labeler):
        shape = make_shape(top1_score=0.2, source_count=3)
        assert labeler.label(shape) == RetrievalState.INSUFFICIENT


class TestRetrievalStateConflicted:
    def test_contradiction_flag_overrides_all(self, labeler):
        # Even high scores, the contradiction flag takes priority
        shape = make_shape(top1_score=0.95, source_count=5, contradiction_flag=True)
        assert labeler.label(shape) == RetrievalState.CONFLICTED

    def test_no_contradiction_flag(self, labeler):
        shape = make_shape(top1_score=0.5, source_count=2, contradiction_flag=False)
        assert labeler.label(shape) != RetrievalState.CONFLICTED

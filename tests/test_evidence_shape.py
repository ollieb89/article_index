"""Unit tests for EvidenceShapeExtractor (Phase 14)."""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from api.evidence_shape import EvidenceShape, EvidenceShapeExtractor


@pytest.fixture
def extractor():
    return EvidenceShapeExtractor()


def make_chunk(doc_id, score, from_lexical=False, from_vector=False, score_key='hybrid_score'):
    return {
        score_key: score,
        'document_id': doc_id,
        'from_lexical': from_lexical,
        'from_vector': from_vector,
    }


class TestEvidenceShapeEmpty:
    def test_empty_chunks_returns_zeros(self, extractor):
        shape = extractor.extract([], "test query")
        assert shape.top1_score == 0
        assert shape.topk_mean_score == 0
        assert shape.score_gap == 0
        assert shape.source_diversity == 0
        assert shape.source_count == 0
        assert shape.chunk_agreement == 0
        assert shape.contradiction_flag is False


class TestEvidenceShapeScores:
    def test_top1_score_extracted(self, extractor):
        chunks = [make_chunk(1, 0.9), make_chunk(2, 0.7), make_chunk(3, 0.5)]
        shape = extractor.extract(chunks, "query")
        assert shape.top1_score == pytest.approx(0.9, abs=0.01)

    def test_topk_mean_is_average(self, extractor):
        chunks = [make_chunk(1, 0.9), make_chunk(2, 0.7), make_chunk(3, 0.5)]
        shape = extractor.extract(chunks, "query")
        expected_mean = (0.9 + 0.7 + 0.5) / 3
        assert shape.topk_mean_score == pytest.approx(expected_mean, abs=0.01)

    def test_score_gap_between_top_two(self, extractor):
        chunks = [make_chunk(1, 0.9), make_chunk(2, 0.6)]
        shape = extractor.extract(chunks, "query")
        assert shape.score_gap == pytest.approx(0.9 - 0.6, abs=0.01)

    def test_score_gap_single_chunk_is_one(self, extractor):
        chunks = [make_chunk(1, 0.8)]
        shape = extractor.extract(chunks, "query")
        assert shape.score_gap == pytest.approx(1.0, abs=0.01)

    def test_uses_rrf_score_fallback(self, extractor):
        chunks = [{'rrf_score': 0.75, 'document_id': 1}]
        shape = extractor.extract(chunks, "query")
        assert shape.top1_score == pytest.approx(0.75, abs=0.01)


class TestEvidenceShapeSourceDiversity:
    def test_single_source(self, extractor):
        chunks = [make_chunk(1, 0.9), make_chunk(1, 0.8), make_chunk(1, 0.7)]
        shape = extractor.extract(chunks, "query")
        assert shape.source_count == 1
        assert shape.source_diversity == pytest.approx(1 / 3, abs=0.01)

    def test_multiple_sources(self, extractor):
        chunks = [make_chunk(1, 0.9), make_chunk(2, 0.8), make_chunk(3, 0.7)]
        shape = extractor.extract(chunks, "query")
        assert shape.source_count == 3
        assert shape.source_diversity == pytest.approx(1.0, abs=0.01)

    def test_mixed_sources(self, extractor):
        chunks = [make_chunk(1, 0.9), make_chunk(1, 0.8), make_chunk(2, 0.7)]
        shape = extractor.extract(chunks, "query")
        assert shape.source_count == 2
        assert shape.source_diversity == pytest.approx(2 / 3, abs=0.01)


class TestEvidenceShapeChunkAgreement:
    def test_all_from_both_sources(self, extractor):
        chunks = [
            make_chunk(1, 0.9, from_lexical=True, from_vector=True),
            make_chunk(2, 0.8, from_lexical=True, from_vector=True),
        ]
        shape = extractor.extract(chunks, "query")
        assert shape.chunk_agreement == pytest.approx(1.0, abs=0.01)

    def test_no_overlap(self, extractor):
        chunks = [
            make_chunk(1, 0.9, from_lexical=True, from_vector=False),
            make_chunk(2, 0.8, from_lexical=False, from_vector=True),
        ]
        shape = extractor.extract(chunks, "query")
        assert shape.chunk_agreement == pytest.approx(0.0, abs=0.01)


class TestEvidenceShapeToDict:
    def test_to_dict_structure(self, extractor):
        chunks = [make_chunk(1, 0.9), make_chunk(2, 0.7)]
        shape = extractor.extract(chunks, "query")
        d = shape.to_dict()
        assert 'top1_score' in d
        assert 'topk_mean_score' in d
        assert 'score_gap' in d
        assert 'source_diversity' in d
        assert 'source_count' in d
        assert 'chunk_agreement' in d
        assert 'contradiction_flag' in d

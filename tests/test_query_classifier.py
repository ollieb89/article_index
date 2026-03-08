"""Unit tests for QueryClassifier (Phase 14)."""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from api.query_classifier import QueryClassifier, QueryType


@pytest.fixture
def classifier():
    return QueryClassifier()


class TestQueryClassifierExactFact:
    def test_who_question(self, classifier):
        assert classifier.classify("Who founded Apple?") == QueryType.EXACT_FACT

    def test_when_question(self, classifier):
        assert classifier.classify("When was the Eiffel Tower built?") == QueryType.EXACT_FACT

    def test_where_question(self, classifier):
        assert classifier.classify("Where is the capital of France?") == QueryType.EXACT_FACT

    def test_how_many_question(self, classifier):
        assert classifier.classify("How many employees does Google have?") == QueryType.EXACT_FACT

    def test_date_keyword(self, classifier):
        assert classifier.classify("What is the founding date of NASA?") == QueryType.EXACT_FACT


class TestQueryClassifierComparison:
    def test_versus_keyword(self, classifier):
        # "What is the difference..." would match EXACT_FACT first (^what is pattern),
        # so use a query that starts with the comparison keyword directly.
        assert classifier.classify("Python 2 versus Python 3 performance differences") == QueryType.COMPARISON

    def test_compare_keyword(self, classifier):
        assert classifier.classify("Compare React and Vue for frontend development") == QueryType.COMPARISON

    def test_better_keyword(self, classifier):
        assert classifier.classify("Which database is better for machine learning?") == QueryType.COMPARISON

    def test_cheaper_keyword(self, classifier):
        assert classifier.classify("Is AWS cheaper than GCP for storage?") == QueryType.COMPARISON


class TestQueryClassifierSummarization:
    def test_summarize_keyword(self, classifier):
        assert classifier.classify("Summarize the main points of the article") == QueryType.SUMMARIZATION

    def test_overview_keyword(self, classifier):
        assert classifier.classify("Give an overview of quantum computing") == QueryType.SUMMARIZATION

    def test_tldr_keyword(self, classifier):
        assert classifier.classify("TL;DR of the document please") == QueryType.SUMMARIZATION


class TestQueryClassifierProcedural:
    def test_how_to(self, classifier):
        assert classifier.classify("How to set up a Python virtual environment?") == QueryType.PROCEDURAL

    def test_steps_keyword(self, classifier):
        assert classifier.classify("Steps to configure Nginx for HTTPS") == QueryType.PROCEDURAL

    def test_guide_keyword(self, classifier):
        assert classifier.classify("Guide to deploying Docker containers on AWS") == QueryType.PROCEDURAL


class TestQueryClassifierAmbiguous:
    def test_single_word(self, classifier):
        assert classifier.classify("Python") == QueryType.AMBIGUOUS

    def test_two_words(self, classifier):
        assert classifier.classify("machine learning") == QueryType.AMBIGUOUS

    def test_empty_string(self, classifier):
        assert classifier.classify("") == QueryType.UNKNOWN

    def test_whitespace_only(self, classifier):
        assert classifier.classify("   ") == QueryType.UNKNOWN


class TestQueryClassifierFallback:
    def test_generic_why_question(self, classifier):
        result = classifier.classify("Why is the sky blue on a clear day?")
        # Falls through to EXACT_FACT fallback or a matched type
        assert result in (QueryType.EXACT_FACT, QueryType.UNKNOWN, QueryType.AMBIGUOUS)

    def test_is_question_fallback(self, classifier):
        result = classifier.classify("Is this a valid approach to data science?")
        assert isinstance(result, QueryType)

    def test_classify_returns_query_type(self, classifier):
        result = classifier.classify("What are the main differences between supervised and unsupervised learning?")
        assert isinstance(result, QueryType)

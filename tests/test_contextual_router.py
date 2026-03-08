"""Unit tests for ContextualRouter (Phase 14)."""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from api.query_classifier import QueryType
from api.retrieval_state import RetrievalState
from api.routing import ContextualRouter, RoutingContext, RouteDecision
from shared.policy import RAGPolicy


@pytest.fixture
def router():
    return ContextualRouter()


@pytest.fixture
def default_policy():
    return RAGPolicy(
        version="test-14",
        thresholds={"high": 0.75, "medium": 0.50, "low": 0.25},
        routing_rules={"default": "standard", "query_types": {}},
        contextual_thresholds={
            "exact_fact": {"high": 0.85, "medium": 0.60},
            "summarization": {"high": 0.65, "medium": 0.40},
        },
        latency_budgets={"general": 2000, "exact_fact": 1000}
    )


def make_context(
    query_type=QueryType.UNKNOWN,
    confidence_band="high",
    retrieval_state=RetrievalState.STRONG,
    latency_budget=2000,
    policy=None,
):
    if policy is None:
        policy = RAGPolicy(version="test")
    return RoutingContext(
        query_type=query_type,
        confidence_band=confidence_band,
        retrieval_state=retrieval_state,
        latency_budget=latency_budget,
        policy=policy,
    )


class TestContextualRouterConflicted:
    def test_conflicted_high_band_is_conservative(self, router, default_policy):
        ctx = make_context(
            confidence_band="high",
            retrieval_state=RetrievalState.CONFLICTED,
            policy=default_policy,
        )
        decision = router.route(ctx)
        assert decision.action == "conservative_prompt"
        assert "conflicted" in decision.execution_path

    def test_conflicted_medium_band_abstains(self, router, default_policy):
        ctx = make_context(
            confidence_band="medium",
            retrieval_state=RetrievalState.CONFLICTED,
            policy=default_policy,
        )
        decision = router.route(ctx)
        assert decision.action == "abstain"

    def test_conflicted_low_band_abstains(self, router, default_policy):
        ctx = make_context(
            confidence_band="low",
            retrieval_state=RetrievalState.CONFLICTED,
            policy=default_policy,
        )
        decision = router.route(ctx)
        assert decision.action == "abstain"


class TestContextualRouterExactFact:
    def test_fragile_exact_fact_triggers_expansion(self, router, default_policy):
        ctx = make_context(
            query_type=QueryType.EXACT_FACT,
            confidence_band="medium",
            retrieval_state=RetrievalState.FRAGILE,
            policy=default_policy,
        )
        decision = router.route(ctx)
        assert decision.action == "expanded_retrieval"

    def test_low_confidence_exact_fact_abstains(self, router, default_policy):
        ctx = make_context(
            query_type=QueryType.EXACT_FACT,
            confidence_band="low",
            retrieval_state=RetrievalState.STRONG,
            policy=default_policy,
        )
        decision = router.route(ctx)
        assert decision.action == "abstain"

    def test_strong_high_exact_fact_proceeds_standard(self, router, default_policy):
        ctx = make_context(
            query_type=QueryType.EXACT_FACT,
            confidence_band="high",
            retrieval_state=RetrievalState.STRONG,
            policy=default_policy,
        )
        decision = router.route(ctx)
        assert decision.action == "standard"


class TestContextualRouterSummarization:
    def test_recoverable_low_band_allowed_for_summarization(self, router, default_policy):
        ctx = make_context(
            query_type=QueryType.SUMMARIZATION,
            confidence_band="low",
            retrieval_state=RetrievalState.RECOVERABLE,
            policy=default_policy,
        )
        decision = router.route(ctx)
        assert decision.action == "standard"


class TestContextualRouterDefault:
    def test_high_band_default_standard(self, router, default_policy):
        ctx = make_context(
            query_type=QueryType.UNKNOWN,
            confidence_band="high",
            retrieval_state=RetrievalState.STRONG,
            policy=default_policy,
        )
        decision = router.route(ctx)
        assert decision.action == "standard"

    def test_medium_band_default_expanded(self, router, default_policy):
        ctx = make_context(
            query_type=QueryType.UNKNOWN,
            confidence_band="medium",
            retrieval_state=RetrievalState.RECOVERABLE,
            policy=default_policy,
        )
        decision = router.route(ctx)
        assert decision.action == "expanded_retrieval"

    def test_low_band_default_conservative(self, router, default_policy):
        ctx = make_context(
            query_type=QueryType.UNKNOWN,
            confidence_band="low",
            retrieval_state=RetrievalState.FRAGILE,
            policy=default_policy,
        )
        decision = router.route(ctx)
        assert decision.action == "conservative_prompt"


class TestRouteDecisionStructure:
    def test_route_decision_has_required_fields(self, router, default_policy):
        ctx = make_context(policy=default_policy)
        decision = router.route(ctx)
        assert isinstance(decision, RouteDecision)
        assert hasattr(decision, 'action')
        assert hasattr(decision, 'execution_path')
        assert hasattr(decision, 'reason')
        assert decision.action
        assert decision.execution_path

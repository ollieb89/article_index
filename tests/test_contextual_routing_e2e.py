"""E2E tests for Phase 5 contextual routing.

These tests verify that the contextual routing system correctly maps
query types, retrieval states, and evidence shapes to execution paths.
"""

import pytest
from shared.routing_engine import RoutingContext, RoutingRule, RuleEngine
from shared.contextual_router_v2 import ContextualRouterV2
from shared.budget_constraint import BudgetConstraint
from shared.default_policies import get_phase5_default_policy


class MockPolicy:
    """Mock policy for testing."""
    
    def __init__(self, policy_dict):
        self.policy_version = policy_dict.get('policy_version', 'test')
        self.contextual_routing_rules = policy_dict.get('contextual_routing_rules', [])
        self.routing_defaults = policy_dict.get('routing_defaults', {})


class TestContextualRoutingE2E:
    """E2E tests for key routing scenarios."""
    
    @pytest.fixture
    def router(self):
        """Create router with default Phase 5 policy."""
        policy_dict = get_phase5_default_policy()
        return ContextualRouterV2(MockPolicy(policy_dict))
    
    def test_exact_fact_solid_high_to_fast(self, router):
        """E2E: exact_fact + SOLID + high confidence → fast path."""
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="SOLID",
            confidence_band="high",
            evidence_shape={"coverage_band": "high"},
            effort_budget="medium"
        )
        
        decision = router.route(context)
        
        assert decision.execution_path == "fast"
        assert decision.matched_rule_id == "exact_fact_solid_high"
        assert decision.fallback_used is False
    
    def test_comparison_solid_to_standard(self, router):
        """E2E: comparison + SOLID → standard path."""
        context = RoutingContext(
            query_type="comparison",
            retrieval_state="SOLID",
            confidence_band="high",
            effort_budget="medium"
        )
        
        decision = router.route(context)
        
        assert decision.execution_path == "standard"
        assert decision.matched_rule_id == "comparison_solid"
    
    def test_fragile_retrieval_to_cautious(self, router):
        """E2E: FRAGILE retrieval → cautious path (regardless of confidence)."""
        # Even with high confidence, fragile should trigger cautious
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="FRAGILE",
            confidence_band="high",
            effort_budget="medium"
        )
        
        decision = router.route(context)
        
        assert decision.execution_path == "cautious"
        assert decision.matched_rule_id == "fragile_guardrail"
        assert decision.action.get("expand_retrieval") is True
        assert decision.action.get("invoke_reranker") is True
    
    def test_conflicted_retrieval_to_cautious(self, router):
        """E2E: CONFLICTED retrieval → cautious path."""
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="CONFLICTED",
            confidence_band="medium",
            effort_budget="medium"
        )
        
        decision = router.route(context)
        
        assert decision.execution_path == "cautious"
        assert decision.matched_rule_id == "conflicted_guardrail"
    
    def test_empty_retrieval_to_abstain(self, router):
        """E2E: EMPTY retrieval → abstain."""
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="EMPTY",
            confidence_band="high",  # Even with high confidence
            effort_budget="medium"
        )
        
        decision = router.route(context)
        
        assert decision.execution_path == "abstain"
        assert decision.matched_rule_id == "empty_abstain"
        assert decision.action.get("generation_skipped") is True
    
    def test_low_confidence_to_cautious(self, router):
        """E2E: low confidence → cautious path."""
        # Use query_type that doesn't have a more specific rule
        # low_confidence_cautious has specificity 1 (just confidence_band)
        # We need a query type where no more specific rule exists
        context = RoutingContext(
            query_type="other",
            retrieval_state="SOLID",
            confidence_band="low",
            effort_budget="medium"
        )
        
        decision = router.route(context)
        
        assert decision.execution_path == "cautious"
        assert decision.matched_rule_id == "low_confidence_cautious"
    
    def test_no_rule_match_fallback(self, router):
        """E2E: No rule match → fallback to confidence band default."""
        # Use a query type that may not have specific rules
        context = RoutingContext(
            query_type="other",
            retrieval_state="SOLID",
            confidence_band="medium",
            effort_budget="medium"
        )
        
        decision = router.route(context)
        
        # Should fall back to confidence band default
        assert decision.execution_path == "standard"
        assert decision.fallback_used is True


class TestBudgetConstraintE2E:
    """E2E tests for budget constraint layer."""
    
    @pytest.fixture
    def constraint(self):
        return BudgetConstraint()
    
    def test_cautious_downgraded_to_standard_low_budget(self, constraint):
        """E2E: cautious + low budget → downgraded to standard."""
        from shared.routing_engine import RoutingDecision
        
        decision = RoutingDecision(execution_path="cautious")
        result = constraint.apply(decision, "low")
        
        assert result.execution_path == "standard"
        assert result.budget_override_applied is True
        assert result.requested_execution_path == "cautious"
    
    def test_standard_unchanged_low_budget(self, constraint):
        """E2E: standard + low budget → unchanged."""
        from shared.routing_engine import RoutingDecision
        
        decision = RoutingDecision(execution_path="standard")
        result = constraint.apply(decision, "low")
        
        assert result.execution_path == "standard"
        assert result.budget_override_applied is False
    
    def test_cautious_unchanged_medium_budget(self, constraint):
        """E2E: cautious + medium budget → unchanged."""
        from shared.routing_engine import RoutingDecision
        
        decision = RoutingDecision(execution_path="cautious")
        result = constraint.apply(decision, "medium")
        
        assert result.execution_path == "cautious"
        assert result.budget_override_applied is False
    
    def test_abstain_protected_any_budget(self, constraint):
        """E2E: abstain + any budget → unchanged."""
        from shared.routing_engine import RoutingDecision
        
        for budget in ["low", "medium", "high"]:
            decision = RoutingDecision(execution_path="abstain")
            result = constraint.apply(decision, budget)
            
            assert result.execution_path == "abstain", f"Failed for budget: {budget}"
            assert result.budget_override_applied is False


class TestRulePrecedenceE2E:
    """E2E tests for rule precedence algorithm."""
    
    def test_specificity_wins_over_priority(self):
        """E2E: More specific rule beats less specific even with lower priority."""
        rules = [
            RoutingRule(
                id="broad_high_priority",
                priority=200,
                conditions={"query_type": "exact_fact"},
                action={"execution_path": "standard"}
            ),
            RoutingRule(
                id="specific_low_priority",
                priority=50,
                conditions={"query_type": "exact_fact", "confidence_band": "high"},
                action={"execution_path": "fast"}
            )
        ]
        engine = RuleEngine(rules)
        
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="SOLID",
            confidence_band="high"
        )
        
        decision = engine.route(context)
        
        # Specific rule wins despite lower priority
        assert decision.matched_rule_id == "specific_low_priority"
        assert decision.matched_rule_specificity == 2
    
    def test_priority_wins_same_specificity(self):
        """E2E: Higher priority wins when specificity is equal."""
        rules = [
            RoutingRule(
                id="low_priority",
                priority=50,
                conditions={"query_type": "exact_fact"},
                action={"execution_path": "standard"}
            ),
            RoutingRule(
                id="high_priority",
                priority=100,
                conditions={"query_type": "exact_fact"},
                action={"execution_path": "fast"}
            )
        ]
        engine = RuleEngine(rules)
        
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="SOLID",
            confidence_band="medium"
        )
        
        decision = engine.route(context)
        
        assert decision.matched_rule_id == "high_priority"
    
    def test_id_tiebreak_same_specificity_and_priority(self):
        """E2E: ID breaks ties when specificity and priority are equal."""
        rules = [
            RoutingRule(
                id="b_rule",
                priority=50,
                conditions={"query_type": "exact_fact"},
                action={"execution_path": "standard"}
            ),
            RoutingRule(
                id="a_rule",
                priority=50,
                conditions={"query_type": "exact_fact"},
                action={"execution_path": "fast"}
            )
        ]
        engine = RuleEngine(rules)
        
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="SOLID",
            confidence_band="medium"
        )
        
        decision = engine.route(context)
        
        # a_rule wins (alphabetically first)
        assert decision.matched_rule_id == "a_rule"


class TestPhase5Requirements:
    """Tests verifying Phase 5 requirements CTX-01 through CTX-04."""
    
    @pytest.fixture
    def router(self):
        policy_dict = get_phase5_default_policy()
        return ContextualRouterV2(MockPolicy(policy_dict))
    
    def test_ctx01_query_type_routing_dimension(self, router):
        """CTX-01: Query type is a first-class routing dimension."""
        # Different query types should produce different paths
        
        # exact_fact + SOLID + high → fast
        context1 = RoutingContext(
            query_type="exact_fact",
            retrieval_state="SOLID",
            confidence_band="high"
        )
        decision1 = router.route(context1)
        
        # comparison + SOLID → standard
        context2 = RoutingContext(
            query_type="comparison",
            retrieval_state="SOLID",
            confidence_band="high"
        )
        decision2 = router.route(context2)
        
        # Different query types produce different results
        assert decision1.execution_path == "fast"
        assert decision2.execution_path == "standard"
    
    def test_ctx02_evidence_shape_routing(self, router):
        """CTX-02: Evidence shape drives retrieval decisions."""
        # High coverage + high agreement should allow fast path
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="SOLID",
            confidence_band="high",
            evidence_shape={"coverage_band": "high", "agreement_band": "high"}
        )
        
        decision = router.route(context)
        
        # Evidence shape should be considered
        assert decision.execution_path == "fast"
    
    def test_ctx03_retrieval_state_routing(self, router):
        """CTX-03: Retrieval state maps to distinct execution paths."""
        
        # SOLID → fast (for exact_fact + high)
        context_solid = RoutingContext(
            query_type="exact_fact",
            retrieval_state="SOLID",
            confidence_band="high"
        )
        decision_solid = router.route(context_solid)
        assert decision_solid.execution_path == "fast"
        
        # FRAGILE → cautious
        context_fragile = RoutingContext(
            query_type="exact_fact",
            retrieval_state="FRAGILE",
            confidence_band="high"
        )
        decision_fragile = router.route(context_fragile)
        assert decision_fragile.execution_path == "cautious"
        
        # EMPTY → abstain
        context_empty = RoutingContext(
            query_type="exact_fact",
            retrieval_state="EMPTY",
            confidence_band="high"
        )
        decision_empty = router.route(context_empty)
        assert decision_empty.execution_path == "abstain"
    
    def test_ctx04_effort_budget_enforcement(self):
        """CTX-04: Effort budgets enforced as post-routing constraint."""
        from shared.routing_engine import RoutingDecision
        
        constraint = BudgetConstraint()
        
        # Low budget prevents cautious path
        decision = RoutingDecision(execution_path="cautious")
        result = constraint.apply(decision, "low")
        
        assert result.execution_path == "standard"  # Downgraded
        assert result.budget_override_applied is True


class TestIntegrationFlow:
    """Integration tests for complete routing flow."""
    
    def test_full_routing_pipeline(self):
        """Test complete routing pipeline: Router → Budget → Decision."""
        # Create router with default policy
        policy_dict = get_phase5_default_policy()
        router = ContextualRouterV2(MockPolicy(policy_dict))
        
        # Create budget constraint
        constraint = BudgetConstraint()
        
        # Test case: fragile retrieval with high budget
        # Fragile → cautious, and high budget allows cautious (no downgrade)
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="FRAGILE",
            confidence_band="high",
            effort_budget="high"
        )
        
        # Step 1: Route through rule engine
        decision = router.route(context)
        assert decision.execution_path == "cautious"  # Fragile rule
        assert decision.matched_rule_id == "fragile_guardrail"
        
        # Step 2: Apply budget constraint (high budget = no limit)
        final_decision = constraint.apply(decision, context.effort_budget)
        
        # No budget override
        assert final_decision.execution_path == "cautious"
        assert final_decision.budget_override_applied is False
        
        # Test case: cautious path with low budget (should downgrade)
        context2 = RoutingContext(
            query_type="exact_fact",
            retrieval_state="FRAGILE",
            confidence_band="high",
            effort_budget="low"
        )
        
        decision2 = router.route(context2)
        assert decision2.execution_path == "cautious"
        
        # Low budget downgrades cautious to standard
        final_decision2 = constraint.apply(decision2, "low")
        assert final_decision2.execution_path == "standard"
        assert final_decision2.budget_override_applied is True
        assert final_decision2.requested_execution_path == "cautious"

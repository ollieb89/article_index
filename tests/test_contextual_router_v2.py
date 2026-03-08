"""Unit tests for ContextualRouterV2.

Tests cover:
- Router initialization from policy
- Rule parsing from policy dict
- Routing through rule engine
- Policy reload
- Integration with default Phase 5 policy
"""

import pytest
from unittest.mock import MagicMock, patch

from shared.routing_engine import RoutingContext, RoutingRule
from shared.contextual_router_v2 import ContextualRouterV2
from shared.default_policies import get_phase5_default_policy, get_minimal_policy


class MockPolicy:
    """Mock policy for testing."""
    
    def __init__(self, policy_dict):
        self.policy_version = policy_dict.get('policy_version', 'test')
        self.contextual_routing_rules = policy_dict.get('contextual_routing_rules', [])
        self.routing_defaults = policy_dict.get('routing_defaults', {})
    
    def to_dict(self):
        return {
            'policy_version': self.policy_version,
            'contextual_routing_rules': self.contextual_routing_rules,
            'routing_defaults': self.routing_defaults
        }


class TestContextualRouterV2Init:
    """Tests for router initialization."""
    
    def test_init_with_minimal_policy(self):
        """Test initialization with minimal policy."""
        policy_dict = get_minimal_policy()
        policy = MockPolicy(policy_dict)
        
        router = ContextualRouterV2(policy)
        
        assert router.policy == policy
        assert len(router.engine.rules) == 2  # Two rules in minimal policy
    
    def test_init_with_default_policy(self):
        """Test initialization with default Phase 5 policy."""
        policy_dict = get_phase5_default_policy()
        policy = MockPolicy(policy_dict)
        
        router = ContextualRouterV2(policy)
        
        assert len(router.engine.rules) > 10  # Many rules in default policy
    
    def test_init_with_empty_policy(self):
        """Test initialization with empty policy."""
        policy = MockPolicy({
            'policy_version': 'empty',
            'contextual_routing_rules': [],
            'routing_defaults': {
                'by_confidence_band': {'high': 'fast'}
            }
        })
        
        router = ContextualRouterV2(policy)
        
        assert len(router.engine.rules) == 0
        assert router.defaults['by_confidence_band']['high'] == 'fast'


class TestContextualRouterV2Routing:
    """Tests for routing behavior."""
    
    @pytest.fixture
    def router(self):
        """Create router with default policy."""
        policy_dict = get_phase5_default_policy()
        policy = MockPolicy(policy_dict)
        return ContextualRouterV2(policy)
    
    def test_route_exact_fact_solid_high(self, router):
        """Test routing exact fact with solid retrieval and high confidence."""
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="SOLID",
            confidence_band="high"
        )
        
        decision = router.route(context)
        
        assert decision.execution_path == "fast"
        assert decision.matched_rule_id == "exact_fact_solid_high"
        assert decision.fallback_used is False
    
    def test_route_fragile_retrieval(self, router):
        """Test routing fragile retrieval (guardrail)."""
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="FRAGILE",
            confidence_band="high"  # Even with high confidence
        )
        
        decision = router.route(context)
        
        # Fragile guardrail should apply
        assert decision.execution_path == "cautious"
        assert decision.matched_rule_id == "fragile_guardrail"
    
    def test_route_empty_retrieval(self, router):
        """Test routing empty retrieval (abstain)."""
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="EMPTY",
            confidence_band="high"
        )
        
        decision = router.route(context)
        
        # Empty abstain rule should apply
        assert decision.execution_path == "abstain"
        assert decision.matched_rule_id == "empty_abstain"
    
    def test_route_fallback(self, router):
        """Test fallback when no contextual rule matches."""
        context = RoutingContext(
            query_type="other",  # May not have specific rule
            retrieval_state="SOLID",
            confidence_band="medium"
        )
        
        decision = router.route(context)
        
        # Should fall back to confidence band default
        assert decision.execution_path == "standard"
        assert decision.fallback_used is True


class TestContextualRouterV2RuleManagement:
    """Tests for rule management methods."""
    
    @pytest.fixture
    def router(self):
        """Create router with minimal policy."""
        policy_dict = get_minimal_policy()
        policy = MockPolicy(policy_dict)
        return ContextualRouterV2(policy)
    
    def test_get_rule_existing(self, router):
        """Test getting an existing rule."""
        rule = router.get_rule("fragile_guardrail")
        
        assert rule is not None
        assert rule.id == "fragile_guardrail"
    
    def test_get_rule_missing(self, router):
        """Test getting a non-existent rule."""
        rule = router.get_rule("nonexistent")
        
        assert rule is None
    
    def test_list_rules(self, router):
        """Test listing rules."""
        rules = router.list_rules()
        
        assert len(rules) == 2
        assert all(isinstance(r, RoutingRule) for r in rules)
    
    def test_list_rules_include_disabled(self, router):
        """Test listing rules including disabled."""
        # Minimal policy has no disabled rules, but test the parameter
        rules = router.list_rules(include_disabled=True)
        
        assert len(rules) == 2


class TestContextualRouterV2PolicyReload:
    """Tests for policy reload."""
    
    def test_reload_policy(self):
        """Test reloading with a new policy."""
        # Start with minimal
        router = ContextualRouterV2(MockPolicy(get_minimal_policy()))
        assert len(router.engine.rules) == 2
        
        # Reload with empty
        router.reload_policy(MockPolicy({
            'policy_version': 'empty',
            'contextual_routing_rules': [],
            'routing_defaults': {'by_confidence_band': {}}
        }))
        
        assert len(router.engine.rules) == 0


class TestPolicyParsing:
    """Tests for rule parsing."""
    
    def test_parse_valid_rules(self):
        """Test parsing valid rule dicts."""
        policy_dict = {
            'policy_version': 'test',
            'contextual_routing_rules': [
                {
                    'id': 'test_rule',
                    'conditions': {'query_type': 'exact_fact'},
                    'action': {'execution_path': 'fast'},
                    'priority': 100,
                    'reason': 'Test'
                }
            ],
            'routing_defaults': {'by_confidence_band': {}}
        }
        
        router = ContextualRouterV2(MockPolicy(policy_dict))
        
        assert len(router.engine.rules) == 1
        rule = router.engine.rules[0]
        assert rule.id == 'test_rule'
        assert rule.priority == 100
        assert rule.reason == 'Test'
    
    def test_parse_rules_with_defaults(self):
        """Test parsing rules with default values."""
        policy_dict = {
            'policy_version': 'test',
            'contextual_routing_rules': [
                {
                    'id': 'minimal_rule',
                    'conditions': {'query_type': 'exact_fact'},
                    'action': {'execution_path': 'fast'}
                    # No priority, no enabled, no reason
                }
            ],
            'routing_defaults': {'by_confidence_band': {}}
        }
        
        router = ContextualRouterV2(MockPolicy(policy_dict))
        
        rule = router.engine.rules[0]
        assert rule.priority == 0  # Default
        assert rule.enabled is True  # Default
        assert rule.reason is None  # Default
    
    def test_parse_disabled_rules_skipped(self):
        """Test that disabled rules are not loaded into engine."""
        policy_dict = {
            'policy_version': 'test',
            'contextual_routing_rules': [
                {
                    'id': 'enabled_rule',
                    'enabled': True,
                    'conditions': {'query_type': 'exact_fact'},
                    'action': {'execution_path': 'fast'}
                },
                {
                    'id': 'disabled_rule',
                    'enabled': False,
                    'conditions': {'query_type': 'comparison'},
                    'action': {'execution_path': 'standard'}
                }
            ],
            'routing_defaults': {'by_confidence_band': {}}
        }
        
        router = ContextualRouterV2(MockPolicy(policy_dict))
        
        # Only enabled rule should be in engine
        assert len(router.engine.rules) == 1
        assert router.engine.rules[0].id == 'enabled_rule'
        
        # But disabled should still be in all_rules
        assert len(router.engine._all_rules) == 2
    
    def test_parse_invalid_rules_logged(self):
        """Test that invalid rules are logged and skipped."""
        policy_dict = {
            'policy_version': 'test',
            'contextual_routing_rules': [
                {
                    'id': 'valid_rule',
                    'conditions': {'query_type': 'exact_fact'},
                    'action': {'execution_path': 'fast'}
                },
                {
                    # Missing id - will be skipped
                    'conditions': {'query_type': 'comparison'},
                    'action': {'execution_path': 'standard'}
                }
            ],
            'routing_defaults': {'by_confidence_band': {}}
        }
        
        router = ContextualRouterV2(MockPolicy(policy_dict))
        
        # Only valid rule should be loaded
        assert len(router.engine.rules) == 1


class TestDefaultPolicies:
    """Tests for default policy functions."""
    
    def test_get_phase5_default_policy_structure(self):
        """Test default policy has correct structure."""
        policy = get_phase5_default_policy()
        
        assert 'policy_version' in policy
        assert 'routing_defaults' in policy
        assert 'contextual_routing_rules' in policy
        assert policy['policy_version'] == 'v5.0-contextual'
    
    def test_get_minimal_policy_structure(self):
        """Test minimal policy has correct structure."""
        policy = get_minimal_policy()
        
        assert 'policy_version' in policy
        assert policy['policy_version'] == 'v5.0-minimal'
        assert len(policy['contextual_routing_rules']) == 2
    
    def test_default_policy_routing_defaults(self):
        """Test default policy has correct confidence band defaults."""
        policy = get_phase5_default_policy()
        defaults = policy['routing_defaults']['by_confidence_band']
        
        assert defaults['high'] == 'fast'
        assert defaults['medium'] == 'standard'
        assert defaults['low'] == 'cautious'
        assert defaults['insufficient'] == 'abstain'

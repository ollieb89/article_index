"""Unit tests for BudgetConstraint layer.

Tests cover:
- Downgrade when path exceeds budget
- No change when path within budget
- Abstain protection (never override)
- All budget/path combinations
- Custom budget levels
"""

import pytest
from shared.routing_engine import RoutingDecision
from shared.budget_constraint import BudgetConstraint


class TestBudgetConstraintBasic:
    """Basic tests for BudgetConstraint."""
    
    def test_initialization_defaults(self):
        """Test initialization with default budget levels."""
        constraint = BudgetConstraint()
        assert constraint.budget_levels["low"] == "standard"
        assert constraint.budget_levels["medium"] == "cautious"
        assert constraint.budget_levels["high"] is None
    
    def test_initialization_custom(self):
        """Test initialization with custom budget levels."""
        custom = {"low": "fast", "medium": "standard", "high": "cautious"}
        constraint = BudgetConstraint(custom)
        assert constraint.budget_levels == custom


class TestBudgetConstraintDowngrade:
    """Tests for budget downgrade behavior."""
    
    @pytest.fixture
    def constraint(self):
        return BudgetConstraint()
    
    def test_downgrade_cautious_to_standard_low_budget(self, constraint):
        """Test cautious → standard when budget is low."""
        decision = RoutingDecision(execution_path="cautious")
        result = constraint.apply(decision, "low")
        
        assert result.execution_path == "standard"
        assert result.budget_override_applied is True
        assert result.requested_execution_path == "cautious"
    
    def test_downgrade_standard_to_standard_low_budget(self, constraint):
        """Test standard unchanged when budget is low."""
        decision = RoutingDecision(execution_path="standard")
        result = constraint.apply(decision, "low")
        
        assert result.execution_path == "standard"
        assert result.budget_override_applied is False
    
    def test_downgrade_fast_to_standard_low_budget(self, constraint):
        """Test fast unchanged when budget is low."""
        decision = RoutingDecision(execution_path="fast")
        result = constraint.apply(decision, "low")
        
        assert result.execution_path == "fast"
        assert result.budget_override_applied is False
    
    def test_downgrade_cautious_to_cautious_medium_budget(self, constraint):
        """Test cautious unchanged when budget is medium."""
        decision = RoutingDecision(execution_path="cautious")
        result = constraint.apply(decision, "medium")
        
        assert result.execution_path == "cautious"
        assert result.budget_override_applied is False
    
    def test_downgrade_fast_to_fast_medium_budget(self, constraint):
        """Test fast unchanged when budget is medium."""
        decision = RoutingDecision(execution_path="fast")
        result = constraint.apply(decision, "medium")
        
        assert result.execution_path == "fast"
        assert result.budget_override_applied is False
    
    def test_no_downgrade_high_budget(self, constraint):
        """Test no downgrade when budget is high."""
        decision = RoutingDecision(execution_path="cautious")
        result = constraint.apply(decision, "high")
        
        assert result.execution_path == "cautious"
        assert result.budget_override_applied is False


class TestBudgetConstraintSafety:
    """Safety constraint tests."""
    
    @pytest.fixture
    def constraint(self):
        return BudgetConstraint()
    
    def test_abstain_never_overridden_low_budget(self, constraint):
        """Test abstain is never overridden, even with low budget."""
        decision = RoutingDecision(execution_path="abstain")
        result = constraint.apply(decision, "low")
        
        assert result.execution_path == "abstain"
        assert result.budget_override_applied is False
    
    def test_abstain_never_overridden_medium_budget(self, constraint):
        """Test abstain is never overridden with medium budget."""
        decision = RoutingDecision(execution_path="abstain")
        result = constraint.apply(decision, "medium")
        
        assert result.execution_path == "abstain"
        assert result.budget_override_applied is False
    
    def test_abstain_never_overridden_high_budget(self, constraint):
        """Test abstain is never overridden with high budget."""
        decision = RoutingDecision(execution_path="abstain")
        result = constraint.apply(decision, "high")
        
        assert result.execution_path == "abstain"
        assert result.budget_override_applied is False
    
    def test_no_upgrade_allowed(self, constraint):
        """Test that constraint never upgrades paths."""
        # Even with high budget, standard stays standard
        decision = RoutingDecision(execution_path="standard")
        result = constraint.apply(decision, "high")
        
        assert result.execution_path == "standard"


class TestBudgetConstraintPathOrdering:
    """Tests for path ordering logic."""
    
    def test_path_order(self):
        """Test PATH_ORDER is correct."""
        constraint = BudgetConstraint()
        assert constraint.PATH_ORDER == ["fast", "standard", "cautious", "abstain"]
    
    def test_downgrade_path_method(self):
        """Test downgrade_path returns correct path."""
        constraint = BudgetConstraint()
        
        # When path <= max, return path
        assert constraint.downgrade_path("fast", "standard") == "fast"
        assert constraint.downgrade_path("standard", "standard") == "standard"
        
        # When path > max, return max
        assert constraint.downgrade_path("cautious", "standard") == "standard"
        assert constraint.downgrade_path("abstain", "cautious") == "cautious"
    
    def test_is_within_budget(self):
        """Test is_within_budget check."""
        constraint = BudgetConstraint()
        
        # Low budget (max standard)
        assert constraint.is_within_budget("fast", "low") is True
        assert constraint.is_within_budget("standard", "low") is True
        assert constraint.is_within_budget("cautious", "low") is False
        
        # Medium budget (max cautious)
        assert constraint.is_within_budget("cautious", "medium") is True
        assert constraint.is_within_budget("abstain", "medium") is False
        
        # High budget (no limit)
        assert constraint.is_within_budget("abstain", "high") is True


class TestBudgetConstraintDecisionPreservation:
    """Tests that decision metadata is preserved."""
    
    def test_metadata_preserved_on_override(self):
        """Test that rule match metadata is preserved when overriding."""
        constraint = BudgetConstraint()
        
        decision = RoutingDecision(
            execution_path="cautious",
            matched_rule_id="fragile_rule",
            matched_rule_priority=200,
            matched_rule_specificity=1,
            action={"execution_path": "cautious", "expand_retrieval": True}
        )
        
        result = constraint.apply(decision, "low")
        
        # Metadata preserved
        assert result.matched_rule_id == "fragile_rule"
        assert result.matched_rule_priority == 200
        assert result.matched_rule_specificity == 1
        assert result.action["expand_retrieval"] is True
        
        # Override tracking
        assert result.budget_override_applied is True
        assert result.requested_execution_path == "cautious"
        assert result.execution_path == "standard"
    
    def test_metadata_preserved_no_override(self):
        """Test that all metadata preserved when no override."""
        constraint = BudgetConstraint()
        
        decision = RoutingDecision(
            execution_path="standard",
            matched_rule_id="standard_rule",
            matched_rule_priority=100,
            fallback_used=True,
            fallback_reason="no_match"
        )
        
        result = constraint.apply(decision, "low")
        
        assert result.matched_rule_id == "standard_rule"
        assert result.matched_rule_priority == 100
        assert result.fallback_used is True
        assert result.fallback_reason == "no_match"
        assert result.budget_override_applied is False


class TestBudgetConstraintEdgeCases:
    """Edge case tests."""
    
    def test_unknown_budget_defaults_to_no_constraint(self):
        """Test unknown budget level."""
        constraint = BudgetConstraint()
        
        decision = RoutingDecision(execution_path="cautious")
        result = constraint.apply(decision, "unknown")
        
        # Unknown budget has no limit (None)
        assert result.execution_path == "cautious"
    
    def test_all_budget_levels_tested(self):
        """Test all budget/path combinations."""
        constraint = BudgetConstraint()
        
        budgets = ["low", "medium", "high"]
        paths = ["fast", "standard", "cautious", "abstain"]
        
        for budget in budgets:
            for path in paths:
                decision = RoutingDecision(execution_path=path)
                result = constraint.apply(decision, budget)
                
                # Should always return a valid decision
                assert result.execution_path in paths
                
                # Abstain should never change
                if path == "abstain":
                    assert result.execution_path == "abstain"
                    assert result.budget_override_applied is False

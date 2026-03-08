"""Unit tests for the Phase 5 rule engine.

Tests cover:
- Basic rule matching (exact match)
- List membership matching
- Specificity ordering (4 > 3 > 2 conditions)
- Priority ordering (same specificity)
- ID tie-break (same specificity + priority)
- Fallback behavior (no matches)
- Empty ruleset
- All rules disabled
- Invalid rules skipped
- Complex scenarios
"""

import pytest
from shared.routing_engine import (
    RoutingContext,
    RoutingRule,
    RoutingDecision,
    RuleEngine,
    InvalidRuleError
)


class TestRoutingContext:
    """Tests for RoutingContext dataclass."""
    
    def test_create_valid_context(self):
        """Test creating a valid routing context."""
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="SOLID",
            confidence_band="high",
            evidence_shape={"coverage_band": "high"},
            effort_budget="medium"
        )
        assert context.query_type == "exact_fact"
        assert context.retrieval_state == "SOLID"
        assert context.confidence_band == "high"
        assert context.effort_budget == "medium"
    
    def test_context_validation_valid(self):
        """Test validation passes for valid context."""
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="SOLID",
            confidence_band="high"
        )
        context.validate()  # Should not raise
    
    def test_context_validation_invalid_query_type(self):
        """Test validation fails for invalid query_type."""
        context = RoutingContext(
            query_type="invalid_type",
            retrieval_state="SOLID",
            confidence_band="high"
        )
        with pytest.raises(ValueError, match="Invalid query_type"):
            context.validate()
    
    def test_context_validation_invalid_retrieval_state(self):
        """Test validation fails for invalid retrieval_state."""
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="INVALID",
            confidence_band="high"
        )
        with pytest.raises(ValueError, match="Invalid retrieval_state"):
            context.validate()
    
    def test_context_validation_invalid_confidence_band(self):
        """Test validation fails for invalid confidence_band."""
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="SOLID",
            confidence_band="INVALID"
        )
        with pytest.raises(ValueError, match="Invalid confidence_band"):
            context.validate()
    
    def test_context_validation_invalid_effort_budget(self):
        """Test validation fails for invalid effort_budget."""
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="SOLID",
            confidence_band="high",
            effort_budget="INVALID"
        )
        with pytest.raises(ValueError, match="Invalid effort_budget"):
            context.validate()
    
    def test_context_to_dict(self):
        """Test context serialization."""
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="SOLID",
            confidence_band="high",
            evidence_shape={"coverage_band": "high"}
        )
        d = context.to_dict()
        assert d["query_type"] == "exact_fact"
        assert d["retrieval_state"] == "SOLID"
        assert d["evidence_shape"]["coverage_band"] == "high"


class TestRoutingRule:
    """Tests for RoutingRule dataclass."""
    
    def test_create_rule(self):
        """Test creating a routing rule."""
        rule = RoutingRule(
            id="test_rule",
            conditions={"query_type": "exact_fact"},
            action={"execution_path": "fast"},
            priority=100
        )
        assert rule.id == "test_rule"
        assert rule.specificity == 1
        assert rule.enabled is True
    
    def test_rule_specificity(self):
        """Test specificity calculation."""
        rule1 = RoutingRule(
            id="r1",
            conditions={"query_type": "exact_fact"},
            action={"execution_path": "fast"}
        )
        rule2 = RoutingRule(
            id="r2",
            conditions={"query_type": "exact_fact", "confidence_band": "high"},
            action={"execution_path": "fast"}
        )
        rule3 = RoutingRule(
            id="r3",
            conditions={"query_type": "exact_fact", "confidence_band": "high", 
                       "retrieval_state": "SOLID"},
            action={"execution_path": "fast"}
        )
        assert rule1.specificity == 1
        assert rule2.specificity == 2
        assert rule3.specificity == 3
    
    def test_rule_to_dict(self):
        """Test rule serialization."""
        rule = RoutingRule(
            id="test",
            conditions={"query_type": "exact_fact"},
            action={"execution_path": "fast"},
            priority=100,
            reason="Test rule"
        )
        d = rule.to_dict()
        assert d["id"] == "test"
        assert d["priority"] == 100
        assert d["reason"] == "Test rule"


class TestRoutingDecision:
    """Tests for RoutingDecision dataclass."""
    
    def test_create_decision(self):
        """Test creating a routing decision."""
        decision = RoutingDecision(
            execution_path="fast",
            matched_rule_id="rule1",
            matched_rule_priority=100,
            matched_rule_specificity=2
        )
        assert decision.execution_path == "fast"
        assert decision.matched_rule_id == "rule1"
    
    def test_fallback_decision(self):
        """Test creating a fallback decision."""
        decision = RoutingDecision(
            execution_path="cautious",
            fallback_used=True,
            fallback_reason="no_matching_rule"
        )
        assert decision.fallback_used is True
        assert decision.fallback_reason == "no_matching_rule"
        assert decision.matched_rule_id is None
    
    def test_decision_to_dict(self):
        """Test decision serialization."""
        decision = RoutingDecision(
            execution_path="fast",
            matched_rule_id="rule1",
            action={"execution_path": "fast"}
        )
        d = decision.to_dict()
        assert d["execution_path"] == "fast"
        assert d["matched_rule_id"] == "rule1"


class TestRuleEngineBasic:
    """Basic tests for RuleEngine."""
    
    def test_engine_initialization(self):
        """Test engine initializes with rules."""
        rules = [
            RoutingRule(
                id="r1",
                conditions={"query_type": "exact_fact"},
                action={"execution_path": "fast"}
            )
        ]
        engine = RuleEngine(rules)
        assert len(engine.rules) == 1
    
    def test_duplicate_rule_ids_raise_error(self):
        """Test that duplicate rule IDs raise InvalidRuleError."""
        rules = [
            RoutingRule(
                id="duplicate",
                conditions={"query_type": "exact_fact"},
                action={"execution_path": "fast"}
            ),
            RoutingRule(
                id="duplicate",
                conditions={"query_type": "comparison"},
                action={"execution_path": "standard"}
            )
        ]
        with pytest.raises(InvalidRuleError, match="Duplicate rule ID"):
            RuleEngine(rules)
    
    def test_invalid_rule_skipped(self):
        """Test that invalid rules are skipped."""
        rules = [
            RoutingRule(
                id="valid",
                conditions={"query_type": "exact_fact"},
                action={"execution_path": "fast"}
            ),
            RoutingRule(
                id="invalid",
                conditions={},
                action={"execution_path": "fast"}
            )
        ]
        engine = RuleEngine(rules)
        assert len(engine.rules) == 1
        assert engine.rules[0].id == "valid"


class TestRuleEngineMatching:
    """Tests for rule matching logic."""
    
    @pytest.fixture
    def engine(self):
        """Create an engine with test rules."""
        rules = [
            RoutingRule(
                id="exact_fact_rule",
                conditions={"query_type": "exact_fact"},
                action={"execution_path": "fast"}
            ),
            RoutingRule(
                id="comparison_rule",
                conditions={"query_type": "comparison"},
                action={"execution_path": "standard"}
            ),
            RoutingRule(
                id="high_confidence",
                conditions={"confidence_band": "high"},
                action={"execution_path": "fast"}
            )
        ]
        return RuleEngine(rules)
    
    def test_exact_match(self, engine):
        """Test exact condition matching."""
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="SOLID",
            confidence_band="medium"
        )
        decision = engine.route(context)
        assert decision.execution_path == "fast"
        assert decision.matched_rule_id == "exact_fact_rule"
    
    def test_list_membership_match(self):
        """Test list membership matching."""
        rules = [
            RoutingRule(
                id="multi_type",
                conditions={"query_type": ["exact_fact", "comparison"]},
                action={"execution_path": "standard"}
            )
        ]
        engine = RuleEngine(rules)
        
        # Should match exact_fact
        context1 = RoutingContext(
            query_type="exact_fact",
            retrieval_state="SOLID",
            confidence_band="medium"
        )
        decision1 = engine.route(context1)
        assert decision1.matched_rule_id == "multi_type"
        
        # Should match comparison
        context2 = RoutingContext(
            query_type="comparison",
            retrieval_state="SOLID",
            confidence_band="medium"
        )
        decision2 = engine.route(context2)
        assert decision2.matched_rule_id == "multi_type"
        
        # Should not match ambiguous
        context3 = RoutingContext(
            query_type="ambiguous",
            retrieval_state="SOLID",
            confidence_band="medium"
        )
        decision3 = engine.route(context3)
        assert decision3.fallback_used is True
    
    def test_missing_field_no_match(self):
        """Test that missing context fields don't match."""
        rules = [
            RoutingRule(
                id="needs_evidence",
                conditions={"evidence_shape": {"coverage_band": "high"}},
                action={"execution_path": "fast"}
            )
        ]
        engine = RuleEngine(rules)
        
        # Context without evidence_shape won't match the nested condition
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="SOLID",
            confidence_band="medium",
            evidence_shape={}  # Empty, so coverage_band is missing
        )
        decision = engine.route(context)
        assert decision.fallback_used is True


class TestRuleEnginePrecedence:
    """Tests for precedence algorithm (specificity > priority > ID)."""
    
    def test_specificity_tiebreak(self):
        """Test that more specific rules win."""
        rules = [
            RoutingRule(
                id="less_specific",
                conditions={"query_type": "exact_fact"},
                action={"execution_path": "standard"},
                priority=100
            ),
            RoutingRule(
                id="more_specific",
                conditions={"query_type": "exact_fact", "confidence_band": "high"},
                action={"execution_path": "fast"},
                priority=50  # Lower priority but more specific
            )
        ]
        engine = RuleEngine(rules)
        
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="SOLID",
            confidence_band="high"
        )
        decision = engine.route(context)
        assert decision.matched_rule_id == "more_specific"
        assert decision.matched_rule_specificity == 2
    
    def test_priority_tiebreak(self):
        """Test that higher priority wins when specificity is equal."""
        rules = [
            RoutingRule(
                id="low_priority",
                conditions={"query_type": "exact_fact"},
                action={"execution_path": "standard"},
                priority=50
            ),
            RoutingRule(
                id="high_priority",
                conditions={"query_type": "exact_fact"},
                action={"execution_path": "fast"},
                priority=100
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
    
    def test_id_tiebreak(self):
        """Test that ID breaks ties when specificity and priority are equal."""
        rules = [
            RoutingRule(
                id="b_rule",
                conditions={"query_type": "exact_fact"},
                action={"execution_path": "standard"},
                priority=50
            ),
            RoutingRule(
                id="a_rule",
                conditions={"query_type": "exact_fact"},
                action={"execution_path": "fast"},
                priority=50
            )
        ]
        engine = RuleEngine(rules)
        
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="SOLID",
            confidence_band="medium"
        )
        decision = engine.route(context)
        # a_rule should win (alphabetically first)
        assert decision.matched_rule_id == "a_rule"


class TestRuleEngineFallback:
    """Tests for fallback behavior."""
    
    def test_no_matches_fallback(self):
        """Test fallback when no rules match."""
        rules = [
            RoutingRule(
                id="exact_fact_only",
                conditions={"query_type": "exact_fact"},
                action={"execution_path": "fast"}
            )
        ]
        engine = RuleEngine(rules)
        
        context = RoutingContext(
            query_type="comparison",  # Won't match
            retrieval_state="SOLID",
            confidence_band="high"
        )
        decision = engine.route(context)
        assert decision.fallback_used is True
        assert decision.fallback_reason == "no_matching_rule"
        assert decision.execution_path == "fast"  # From confidence band default
    
    def test_fallback_by_confidence_band(self):
        """Test that fallback uses confidence band defaults."""
        engine = RuleEngine([])  # Empty rules
        
        test_cases = [
            ("high", "fast"),
            ("medium", "standard"),
            ("low", "cautious"),
            ("insufficient", "abstain")
        ]
        
        for band, expected_path in test_cases:
            context = RoutingContext(
                query_type="exact_fact",
                retrieval_state="SOLID",
                confidence_band=band
            )
            decision = engine.route(context)
            assert decision.execution_path == expected_path, f"Failed for band: {band}"
            assert decision.fallback_used is True
    
    def test_unknown_confidence_band(self):
        """Test handling of unknown confidence band."""
        engine = RuleEngine([])
        
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="SOLID",
            confidence_band="unknown"  # Invalid
        )
        decision = engine.route(context)
        # Should still return a decision (falls back to validation error handling)
        assert decision.fallback_used is True


class TestRuleEngineEdgeCases:
    """Edge case tests."""
    
    def test_empty_ruleset(self):
        """Test engine with no rules."""
        engine = RuleEngine([])
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="SOLID",
            confidence_band="high"
        )
        decision = engine.route(context)
        assert decision.fallback_used is True
        assert decision.execution_path == "fast"
    
    def test_all_rules_disabled(self):
        """Test engine with all rules disabled."""
        rules = [
            RoutingRule(
                id="disabled",
                conditions={"query_type": "exact_fact"},
                action={"execution_path": "fast"},
                enabled=False
            )
        ]
        engine = RuleEngine(rules)
        assert len(engine.rules) == 0
        
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="SOLID",
            confidence_band="high"
        )
        decision = engine.route(context)
        assert decision.fallback_used is True
    
    def test_invalid_context_handling(self):
        """Test engine handles invalid context gracefully."""
        rules = [
            RoutingRule(
                id="valid",
                conditions={"query_type": "exact_fact"},
                action={"execution_path": "fast"}
            )
        ]
        engine = RuleEngine(rules)
        
        context = RoutingContext(
            query_type="INVALID",  # Invalid value
            retrieval_state="SOLID",
            confidence_band="high"
        )
        decision = engine.route(context)
        assert decision.fallback_used is True
        assert "invalid_context" in decision.fallback_reason
    
    def test_complex_scenario(self):
        """Test a complex scenario with multiple rules matching."""
        rules = [
            # Specific rules
            RoutingRule(
                id="exact_fact_solid_high",
                conditions={"query_type": "exact_fact", "retrieval_state": "SOLID", 
                           "confidence_band": "high"},
                action={"execution_path": "fast"},
                priority=100
            ),
            # Broader rules
            RoutingRule(
                id="fragile_any",
                conditions={"retrieval_state": "FRAGILE"},
                action={"execution_path": "cautious"},
                priority=200  # High priority but less specific
            ),
            RoutingRule(
                id="exact_fact_any",
                conditions={"query_type": "exact_fact"},
                action={"execution_path": "standard"},
                priority=50
            )
        ]
        engine = RuleEngine(rules)
        
        # Should match exact_fact_solid_high (3 conditions, most specific)
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="SOLID",
            confidence_band="high"
        )
        decision = engine.route(context)
        assert decision.matched_rule_id == "exact_fact_solid_high"
        assert decision.matched_rule_specificity == 3
        
        # Should match fragile_any (1 condition but applies to fragile)
        context2 = RoutingContext(
            query_type="exact_fact",
            retrieval_state="FRAGILE",
            confidence_band="high"
        )
        decision2 = engine.route(context2)
        assert decision2.matched_rule_id == "fragile_any"
    
    def test_evidence_shape_matching(self):
        """Test matching on evidence_shape bands."""
        rules = [
            RoutingRule(
                id="high_coverage",
                conditions={"evidence_shape": {"coverage_band": "high"}},
                action={"execution_path": "fast"}
            )
        ]
        engine = RuleEngine(rules)
        
        context = RoutingContext(
            query_type="exact_fact",
            retrieval_state="SOLID",
            confidence_band="medium",
            evidence_shape={"coverage_band": "high"}
        )
        decision = engine.route(context)
        assert decision.matched_rule_id == "high_coverage"
    
    def test_get_rule_by_id(self):
        """Test retrieving a rule by ID."""
        rules = [
            RoutingRule(
                id="find_me",
                conditions={"query_type": "exact_fact"},
                action={"execution_path": "fast"}
            )
        ]
        engine = RuleEngine(rules)
        
        rule = engine.get_rule("find_me")
        assert rule is not None
        assert rule.id == "find_me"
        
        not_found = engine.get_rule("missing")
        assert not_found is None
    
    def test_list_rules(self):
        """Test listing rules."""
        rules = [
            RoutingRule(
                id="enabled",
                conditions={"query_type": "exact_fact"},
                action={"execution_path": "fast"}
            ),
            RoutingRule(
                id="disabled",
                conditions={"query_type": "comparison"},
                action={"execution_path": "standard"},
                enabled=False
            )
        ]
        engine = RuleEngine(rules)
        
        enabled_rules = engine.list_rules(include_disabled=False)
        assert len(enabled_rules) == 1
        
        all_rules = engine.list_rules(include_disabled=True)
        assert len(all_rules) == 2

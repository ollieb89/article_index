"""Tests for policy validation.

Tests cover:
- Valid policy passes validation
- Missing required fields detected
- Duplicate rule IDs detected
- Unknown condition fields warned
- Invalid rule structures caught
"""

import pytest
from shared.default_policies import (
    validate_policy,
    get_phase5_default_policy,
    get_minimal_policy
)


class TestValidatePolicyValid:
    """Tests for valid policies."""
    
    def test_validate_default_policy(self):
        """Test default policy is valid."""
        policy = get_phase5_default_policy()
        result = validate_policy(policy)
        
        assert result['valid'] is True
        assert len(result['errors']) == 0
        assert result['rule_count'] > 10
    
    def test_validate_minimal_policy(self):
        """Test minimal policy is valid."""
        policy = get_minimal_policy()
        result = validate_policy(policy)
        
        assert result['valid'] is True
        assert result['rule_count'] == 2
        assert result['enabled_rule_count'] == 2
    
    def test_validate_simple_valid_policy(self):
        """Test simple valid policy."""
        policy = {
            "policy_version": "test",
            "routing_defaults": {
                "by_confidence_band": {"high": "fast"}
            },
            "contextual_routing_rules": [
                {
                    "id": "test_rule",
                    "conditions": {"query_type": "exact_fact"},
                    "action": {"execution_path": "fast"}
                }
            ]
        }
        result = validate_policy(policy)
        
        assert result['valid'] is True
        assert result['rule_count'] == 1


class TestValidatePolicyMissingFields:
    """Tests for missing required fields."""
    
    def test_missing_policy_version(self):
        """Test validation catches missing policy_version."""
        policy = {
            "routing_defaults": {"by_confidence_band": {}},
            "contextual_routing_rules": []
        }
        result = validate_policy(policy)
        
        assert result['valid'] is False
        assert any("policy_version" in e for e in result['errors'])
    
    def test_missing_routing_defaults(self):
        """Test validation catches missing routing_defaults."""
        policy = {
            "policy_version": "test",
            "contextual_routing_rules": []
        }
        result = validate_policy(policy)
        
        assert result['valid'] is False
        assert any("routing_defaults" in e for e in result['errors'])
    
    def test_missing_by_confidence_band(self):
        """Test validation catches missing by_confidence_band."""
        policy = {
            "policy_version": "test",
            "routing_defaults": {},
            "contextual_routing_rules": []
        }
        result = validate_policy(policy)
        
        assert result['valid'] is False
        assert any("by_confidence_band" in e for e in result['errors'])


class TestValidatePolicyRuleErrors:
    """Tests for rule-level validation errors."""
    
    def test_rule_missing_id(self):
        """Test validation catches rule missing id."""
        policy = {
            "policy_version": "test",
            "routing_defaults": {"by_confidence_band": {}},
            "contextual_routing_rules": [
                {
                    "conditions": {"query_type": "exact_fact"},
                    "action": {"execution_path": "fast"}
                }
            ]
        }
        result = validate_policy(policy)
        
        assert result['valid'] is False
        assert any("id" in e for e in result['errors'])
    
    def test_rule_missing_conditions(self):
        """Test validation catches rule missing conditions."""
        policy = {
            "policy_version": "test",
            "routing_defaults": {"by_confidence_band": {}},
            "contextual_routing_rules": [
                {
                    "id": "test",
                    "action": {"execution_path": "fast"}
                }
            ]
        }
        result = validate_policy(policy)
        
        assert result['valid'] is False
        assert any("conditions" in e for e in result['errors'])
    
    def test_rule_missing_action(self):
        """Test validation catches rule missing action."""
        policy = {
            "policy_version": "test",
            "routing_defaults": {"by_confidence_band": {}},
            "contextual_routing_rules": [
                {
                    "id": "test",
                    "conditions": {"query_type": "exact_fact"}
                }
            ]
        }
        result = validate_policy(policy)
        
        assert result['valid'] is False
        assert any("action" in e for e in result['errors'])
    
    def test_rule_missing_execution_path(self):
        """Test validation catches action missing execution_path."""
        policy = {
            "policy_version": "test",
            "routing_defaults": {"by_confidence_band": {}},
            "contextual_routing_rules": [
                {
                    "id": "test",
                    "conditions": {"query_type": "exact_fact"},
                    "action": {}  # Missing execution_path
                }
            ]
        }
        result = validate_policy(policy)
        
        assert result['valid'] is False
        assert any("execution_path" in e for e in result['errors'])
    
    def test_duplicate_rule_ids(self):
        """Test validation catches duplicate rule IDs."""
        policy = {
            "policy_version": "test",
            "routing_defaults": {"by_confidence_band": {}},
            "contextual_routing_rules": [
                {
                    "id": "duplicate",
                    "conditions": {"query_type": "exact_fact"},
                    "action": {"execution_path": "fast"}
                },
                {
                    "id": "duplicate",
                    "conditions": {"query_type": "comparison"},
                    "action": {"execution_path": "standard"}
                }
            ]
        }
        result = validate_policy(policy)
        
        assert result['valid'] is False
        assert any("duplicate" in e.lower() for e in result['errors'])


class TestValidatePolicyWarnings:
    """Tests for validation warnings."""
    
    def test_unknown_condition_field_warning(self):
        """Test validation warns on unknown condition field."""
        policy = {
            "policy_version": "test",
            "routing_defaults": {"by_confidence_band": {}},
            "contextual_routing_rules": [
                {
                    "id": "test",
                    "conditions": {"unknown_field": "value"},
                    "action": {"execution_path": "fast"}
                }
            ]
        }
        result = validate_policy(policy)
        
        # Should be valid but have warning
        assert result['valid'] is True
        assert any("unknown_field" in w for w in result['warnings'])
    
    def test_high_priority_warning(self):
        """Test validation warns on unusually high priority."""
        policy = {
            "policy_version": "test",
            "routing_defaults": {"by_confidence_band": {}},
            "contextual_routing_rules": [
                {
                    "id": "test",
                    "priority": 5000,  # Very high
                    "conditions": {"query_type": "exact_fact"},
                    "action": {"execution_path": "fast"}
                }
            ]
        }
        result = validate_policy(policy)
        
        assert result['valid'] is True
        assert any("priority" in w.lower() for w in result['warnings'])
    
    def test_negative_priority_warning(self):
        """Test validation warns on negative priority."""
        policy = {
            "policy_version": "test",
            "routing_defaults": {"by_confidence_band": {}},
            "contextual_routing_rules": [
                {
                    "id": "test",
                    "priority": -100,
                    "conditions": {"query_type": "exact_fact"},
                    "action": {"execution_path": "fast"}
                }
            ]
        }
        result = validate_policy(policy)
        
        assert result['valid'] is True
        assert any("priority" in w.lower() for w in result['warnings'])


class TestValidatePolicyRuleCounts:
    """Tests for rule counting."""
    
    def test_enabled_rule_counting(self):
        """Test enabled rule counting."""
        policy = {
            "policy_version": "test",
            "routing_defaults": {"by_confidence_band": {}},
            "contextual_routing_rules": [
                {
                    "id": "enabled1",
                    "enabled": True,
                    "conditions": {"query_type": "exact_fact"},
                    "action": {"execution_path": "fast"}
                },
                {
                    "id": "enabled2",
                    "conditions": {"query_type": "comparison"},  # enabled defaults to True
                    "action": {"execution_path": "standard"}
                },
                {
                    "id": "disabled",
                    "enabled": False,
                    "conditions": {"query_type": "ambiguous"},
                    "action": {"execution_path": "cautious"}
                }
            ]
        }
        result = validate_policy(policy)
        
        assert result['rule_count'] == 3
        assert result['enabled_rule_count'] == 2


class TestValidatePolicyEdgeCases:
    """Edge case tests."""
    
    def test_empty_rules_list(self):
        """Test validation with empty rules list."""
        policy = {
            "policy_version": "test",
            "routing_defaults": {"by_confidence_band": {}},
            "contextual_routing_rules": []
        }
        result = validate_policy(policy)
        
        assert result['valid'] is True
        assert result['rule_count'] == 0
        assert result['enabled_rule_count'] == 0
    
    def test_rules_not_list(self):
        """Test validation when rules is not a list."""
        policy = {
            "policy_version": "test",
            "routing_defaults": {"by_confidence_band": {}},
            "contextual_routing_rules": "not a list"
        }
        result = validate_policy(policy)
        
        assert result['valid'] is False
        assert any("list" in e.lower() for e in result['errors'])
    
    def test_multiple_errors_reported(self):
        """Test that multiple errors are all reported."""
        policy = {
            # Missing policy_version
            "routing_defaults": {},  # Missing by_confidence_band
            "contextual_routing_rules": [
                {
                    # Missing id
                    "conditions": {"query_type": "exact_fact"}
                    # Missing action
                }
            ]
        }
        result = validate_policy(policy)
        
        assert result['valid'] is False
        assert len(result['errors']) >= 4  # Multiple errors

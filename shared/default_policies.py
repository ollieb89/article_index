"""Default policies for Phase 5 contextual routing.

This module provides the default Phase 5 policy with contextual routing rules
that extend Phase 4 behavior. The policy uses a declarative rule-table approach
for multidimensional routing.

Policy Structure:
    {
        "policy_version": "v5.0-contextual",
        "routing_defaults": {
            "by_confidence_band": {
                "high": "fast",
                "medium": "standard",
                "low": "cautious",
                "insufficient": "abstain"
            }
        },
        "contextual_routing_rules": [...]
    }

Rules are evaluated with precedence: specificity > priority > ID.
"""

from typing import Any, Dict, List


class MockPolicy:
    """Mock policy for testing and fallback scenarios.
    
    Wraps a policy dict to provide the interface expected by ContextualRouterV2.
    """
    
    def __init__(self, policy_dict: Dict[str, Any]):
        self.policy_version = policy_dict.get('policy_version', 'test')
        self.contextual_routing_rules = policy_dict.get('contextual_routing_rules', [])
        self.routing_defaults = policy_dict.get('routing_defaults', {})
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'policy_version': self.policy_version,
            'contextual_routing_rules': self.contextual_routing_rules,
            'routing_defaults': self.routing_defaults
        }


def get_phase5_default_policy() -> Dict[str, Any]:
    """Return the default Phase 5 contextual policy.
    
    This policy provides sensible defaults that extend Phase 4 behavior
    with contextual awareness of query type, retrieval state, and evidence.
    
    Returns:
        Policy dictionary with routing rules
    """
    return {
        "policy_version": "v5.0-contextual",
        "policy_hash": None,  # Computed on load
        "routing_defaults": {
            "by_confidence_band": {
                "high": "fast",
                "medium": "standard",
                "low": "cautious",
                "insufficient": "abstain"
            }
        },
        "contextual_routing_rules": [
            # ============================================================
            # FAST PATH RULES
            # High-confidence exact facts with solid retrieval
            # ============================================================
            {
                "id": "exact_fact_solid_high",
                "enabled": True,
                "priority": 100,
                "conditions": {
                    "query_type": "exact_fact",
                    "retrieval_state": "SOLID",
                    "confidence_band": "high"
                },
                "action": {"execution_path": "fast"},
                "reason": "Fast path for exact fact with strong evidence"
            },
            {
                "id": "exact_fact_solid_medium",
                "enabled": True,
                "priority": 90,
                "conditions": {
                    "query_type": "exact_fact",
                    "retrieval_state": "SOLID",
                    "confidence_band": "medium"
                },
                "action": {"execution_path": "fast"},
                "reason": "Fast path for exact fact with solid retrieval even at medium confidence"
            },
            
            # ============================================================
            # CAUTIOUS PATH RULES (Safety Guardrails)
            # These have high priority and apply broadly
            # ============================================================
            {
                "id": "fragile_guardrail",
                "enabled": True,
                "priority": 200,
                "conditions": {"retrieval_state": "FRAGILE"},
                "action": {
                    "execution_path": "cautious",
                    "expand_retrieval": True,
                    "invoke_reranker": True
                },
                "reason": "Fragile retrieval forces cautious handling with expansion and reranking"
            },
            {
                "id": "conflicted_guardrail",
                "enabled": True,
                "priority": 200,
                "conditions": {"retrieval_state": "CONFLICTED"},
                "action": {
                    "execution_path": "cautious",
                    "expand_retrieval": True,
                    "invoke_reranker": True
                },
                "reason": "Conflicting evidence requires careful handling with expansion and reranking"
            },
            {
                "id": "low_confidence_cautious",
                "enabled": True,
                "priority": 150,
                "conditions": {"confidence_band": "low"},
                "action": {"execution_path": "cautious"},
                "reason": "Low confidence requires cautious approach"
            },
            
            # ============================================================
            # ABSTAIN RULES (Safety Critical)
            # Highest priority - never answer without evidence
            # ============================================================
            {
                "id": "empty_abstain",
                "enabled": True,
                "priority": 300,
                "conditions": {"retrieval_state": "EMPTY"},
                "action": {
                    "execution_path": "abstain",
                    "generation_skipped": True
                },
                "reason": "No evidence available - must abstain"
            },
            {
                "id": "insufficient_confidence_abstain",
                "enabled": True,
                "priority": 250,
                "conditions": {"confidence_band": "insufficient"},
                "action": {
                    "execution_path": "abstain",
                    "generation_skipped": True
                },
                "reason": "Insufficient confidence to provide reliable answer"
            },
            
            # ============================================================
            # COMPARISON RULES
            # Comparisons need solid evidence
            # ============================================================
            {
                "id": "comparison_solid",
                "enabled": True,
                "priority": 100,
                "conditions": {
                    "query_type": "comparison",
                    "retrieval_state": "SOLID"
                },
                "action": {"execution_path": "standard"},
                "reason": "Standard handling for comparisons with solid evidence"
            },
            {
                "id": "comparison_not_solid",
                "enabled": True,
                "priority": 120,
                "conditions": {
                    "query_type": "comparison",
                    "retrieval_state": ["FRAGILE", "CONFLICTED"]
                },
                "action": {
                    "execution_path": "cautious",
                    "invoke_reranker": True
                },
                "reason": "Comparisons with weak evidence need cautious handling"
            },
            
            # ============================================================
            # MULTI-HOP RULES
            # Multi-hop reasoning needs good evidence coverage
            # ============================================================
            {
                "id": "multi_hop_solid",
                "enabled": True,
                "priority": 100,
                "conditions": {
                    "query_type": "multi_hop",
                    "retrieval_state": "SOLID"
                },
                "action": {"execution_path": "standard"},
                "reason": "Standard handling for multi-hop with solid evidence"
            },
            
            # ============================================================
            # SUMMARIZATION RULES
            # Summarization can work with lower confidence if recoverable
            # ============================================================
            {
                "id": "summarization_solid",
                "enabled": True,
                "priority": 100,
                "conditions": {
                    "query_type": "summarization",
                    "retrieval_state": "SOLID"
                },
                "action": {"execution_path": "standard"},
                "reason": "Standard handling for summarization with solid evidence"
            },
            
            # ============================================================
            # AMBIGUOUS QUERY RULES
            # Ambiguous queries need careful handling
            # ============================================================
            {
                "id": "ambiguous_cautious",
                "enabled": True,
                "priority": 110,
                "conditions": {"query_type": "ambiguous"},
                "action": {"execution_path": "cautious"},
                "reason": "Ambiguous queries require cautious handling"
            },
            
            # ============================================================
            # EVIDENCE SHAPE RULES
            # High coverage + high agreement = faster path
            # ============================================================
            {
                "id": "strong_evidence_shape",
                "enabled": True,
                "priority": 95,
                "conditions": {
                    "evidence_shape": {"coverage_band": "high", "agreement_band": "high"},
                    "confidence_band": "high"
                },
                "action": {"execution_path": "fast"},
                "reason": "Strong evidence shape allows fast path"
            }
        ]
    }


def get_minimal_policy() -> Dict[str, Any]:
    """Return a minimal policy with only essential rules.
    
    This is useful for testing or when you want minimal routing logic.
    
    Returns:
        Minimal policy dictionary
    """
    return {
        "policy_version": "v5.0-minimal",
        "policy_hash": None,
        "routing_defaults": {
            "by_confidence_band": {
                "high": "fast",
                "medium": "standard",
                "low": "cautious",
                "insufficient": "abstain"
            }
        },
        "contextual_routing_rules": [
            {
                "id": "fragile_guardrail",
                "enabled": True,
                "priority": 200,
                "conditions": {"retrieval_state": "FRAGILE"},
                "action": {"execution_path": "cautious"},
                "reason": "Fragile retrieval forces cautious handling"
            },
            {
                "id": "empty_abstain",
                "enabled": True,
                "priority": 300,
                "conditions": {"retrieval_state": "EMPTY"},
                "action": {"execution_path": "abstain"},
                "reason": "No evidence available"
            }
        ]
    }


def validate_policy(policy: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a Phase 5 policy.
    
    Args:
        policy: Policy dictionary to validate
        
    Returns:
        Validation result with 'valid', 'errors', and 'warnings'
    """
    errors = []
    warnings = []
    
    # Check required top-level fields
    if 'policy_version' not in policy:
        errors.append("Missing required field: policy_version")
    
    if 'routing_defaults' not in policy:
        errors.append("Missing required field: routing_defaults")
    elif 'by_confidence_band' not in policy.get('routing_defaults', {}):
        errors.append("Missing routing_defaults.by_confidence_band")
    
    # Validate rules
    rules = policy.get('contextual_routing_rules', [])
    if not isinstance(rules, list):
        errors.append("contextual_routing_rules must be a list")
        rules = []  # Prevent iteration error below
    else:
        seen_ids = set()
        for i, rule in enumerate(rules):
            # Check required fields
            if 'id' not in rule:
                errors.append(f"Rule {i}: missing required field 'id'")
            elif rule['id'] in seen_ids:
                errors.append(f"Rule {i}: duplicate ID '{rule['id']}'")
            else:
                seen_ids.add(rule['id'])
            
            if 'conditions' not in rule:
                errors.append(f"Rule {rule.get('id', i)}: missing required field 'conditions'")
            
            if 'action' not in rule:
                errors.append(f"Rule {rule.get('id', i)}: missing required field 'action'")
            elif 'execution_path' not in rule.get('action', {}):
                errors.append(f"Rule {rule.get('id', i)}: action missing 'execution_path'")
            
            # Validate priority
            priority = rule.get('priority', 0)
            if not isinstance(priority, int):
                warnings.append(f"Rule {rule.get('id', i)}: priority should be an integer")
            elif priority < 0 or priority > 1000:
                warnings.append(f"Rule {rule.get('id', i)}: priority {priority} outside typical range (0-1000)")
            
            # Check for unknown condition fields
            valid_fields = {'query_type', 'retrieval_state', 'confidence_band', 
                          'evidence_shape', 'effort_budget'}
            for field in rule.get('conditions', {}).keys():
                if field not in valid_fields:
                    warnings.append(f"Rule {rule.get('id', i)}: unknown condition field '{field}'")
    
    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "rule_count": len(rules),
        "enabled_rule_count": sum(1 for r in rules if r.get('enabled', True))
    }

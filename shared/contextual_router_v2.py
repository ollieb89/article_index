"""ContextualRouterV2 - Phase 5 rule-based contextual routing.

This module implements ContextualRouterV2 which integrates the rule engine
with the policy system. It replaces the confidence-band-only routing with
multidimensional contextual rules.

Usage Example:
    ```python
    from shared.policy import RAGPolicy
    from shared.contextual_router_v2 import ContextualRouterV2
    
    # Load policy with contextual rules
    policy = RAGPolicy.from_file("policies/v5-contextual.json")
    
    # Create router
    router = ContextualRouterV2(policy)
    
    # Route a context
    from shared.routing_engine import RoutingContext
    context = RoutingContext(
        query_type="exact_fact",
        retrieval_state="SOLID",
        confidence_band="high",
        evidence_shape={"coverage_band": "high"},
        effort_budget="medium"
    )
    
    decision = router.route(context)
    print(decision.execution_path)  # "fast" (if rule matches)
    ```
"""

import logging
from typing import Any, Dict, List, Optional
from shared.routing_engine import (
    RoutingContext, 
    RoutingRule, 
    RoutingDecision, 
    RuleEngine
)
from shared.policy import RAGPolicy

logger = logging.getLogger(__name__)


class ContextualRouterV2:
    """Phase 5 contextual router using rule-table engine.
    
    This router extends the Phase 4 confidence-band routing with contextual
    rules that consider query type, retrieval state, and evidence shape.
    
    Attributes:
        policy: The RAGPolicy containing contextual_routing_rules
        engine: The RuleEngine instance
        defaults: Default routing by confidence band
    """
    
    def __init__(self, policy: RAGPolicy):
        """Initialize router from policy.
        
        Args:
            policy: RAGPolicy with contextual_routing_rules
        """
        self.policy = policy
        
        # Parse rules from policy
        rules_data = getattr(policy, 'contextual_routing_rules', None)
        if rules_data is None:
            # Try from policy dict
            if hasattr(policy, 'to_dict'):
                policy_dict = policy.to_dict()
            else:
                policy_dict = {}
            rules_data = policy_dict.get('contextual_routing_rules', [])
        
        rules = self._parse_rules(rules_data)
        
        # Get defaults
        self.defaults = self._extract_defaults(policy)
        
        # Create rule engine
        self.engine = RuleEngine(rules, self.defaults)
        
        logger.info(
            f"ContextualRouterV2 initialized with {len(rules)} rules "
            f"from policy {getattr(policy, 'policy_version', 'unknown')}"
        )
    
    def _parse_rules(self, rules_data: List[Dict[str, Any]]) -> List[RoutingRule]:
        """Parse rule dicts into RoutingRule objects.
        
        Args:
            rules_data: List of rule dictionaries from policy
            
        Returns:
            List of RoutingRule objects
        """
        rules = []
        
        for rule_dict in rules_data:
            try:
                rule = RoutingRule(
                    id=rule_dict['id'],
                    conditions=rule_dict.get('conditions', {}),
                    action=rule_dict.get('action', {}),
                    enabled=rule_dict.get('enabled', True),
                    priority=rule_dict.get('priority', 0),
                    reason=rule_dict.get('reason')
                )
                rules.append(rule)
            except (KeyError, TypeError) as e:
                logger.error(f"Failed to parse rule: {rule_dict}, error: {e}")
        
        return rules
    
    def _extract_defaults(self, policy: RAGPolicy) -> Dict[str, Any]:
        """Extract default routing from policy.
        
        Args:
            policy: RAGPolicy
            
        Returns:
            Defaults dict for fallback
        """
        # Try to get from policy
        if hasattr(policy, 'routing_defaults'):
            return policy.routing_defaults
        
        # Try from policy dict
        if hasattr(policy, 'to_dict'):
            policy_dict = policy.to_dict()
            return policy_dict.get('routing_defaults', self._default_fallback())
        
        return self._default_fallback()
    
    def _default_fallback(self) -> Dict[str, Any]:
        """Return default fallback routing."""
        return {
            "by_confidence_band": {
                "high": "fast",
                "medium": "standard",
                "low": "cautious",
                "insufficient": "abstain"
            }
        }
    
    def route(self, context: RoutingContext) -> RoutingDecision:
        """Route a context through the rule engine.
        
        Args:
            context: RoutingContext with all routing dimensions
            
        Returns:
            RoutingDecision with execution_path and metadata
        """
        logger.debug(
            f"Routing: type={context.query_type}, "
            f"state={context.retrieval_state}, "
            f"band={context.confidence_band}"
        )
        
        decision = self.engine.route(context)
        
        logger.debug(
            f"Routing result: path={decision.execution_path}, "
            f"rule={decision.matched_rule_id}, "
            f"fallback={decision.fallback_used}"
        )
        
        return decision
    
    def get_rule(self, rule_id: str) -> Optional[RoutingRule]:
        """Get a rule by ID.
        
        Args:
            rule_id: Rule identifier
            
        Returns:
            RoutingRule if found, None otherwise
        """
        return self.engine.get_rule(rule_id)
    
    def list_rules(self, include_disabled: bool = False) -> List[RoutingRule]:
        """List all rules.
        
        Args:
            include_disabled: Whether to include disabled rules
            
        Returns:
            List of RoutingRule objects
        """
        return self.engine.list_rules(include_disabled)
    
    def reload_policy(self, policy: RAGPolicy) -> None:
        """Reload with a new policy.
        
        Args:
            policy: New RAGPolicy
        """
        self.__init__(policy)

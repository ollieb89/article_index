"""Core rule engine for contextual policy routing (Phase 5).

This module implements a declarative rule-table routing engine with specificity/priority
precedence. It replaces nested conditional logic with an auditable, configurable rule set.

Usage Example:
    ```python
    # Define rules
    rules = [
        RoutingRule(
            id="exact_fact_fast",
            priority=100,
            conditions={"query_type": "exact_fact", "confidence_band": "high"},
            action={"execution_path": "fast"}
        ),
        RoutingRule(
            id="fragile_cautious",
            priority=200,
            conditions={"retrieval_state": "FRAGILE"},
            action={"execution_path": "cautious", "expand_retrieval": True}
        ),
    ]
    
    # Create engine
    defaults = {"by_confidence_band": {"high": "fast", "low": "cautious"}}
    engine = RuleEngine(rules, defaults)
    
    # Route a query
    context = RoutingContext(
        query_type="exact_fact",
        retrieval_state="SOLID",
        confidence_band="high",
        evidence_shape={"coverage_band": "high"},
        effort_budget="medium"
    )
    decision = engine.route(context)
    print(decision.execution_path)  # "fast"
    ```

Precedence Algorithm:
    1. Specificity (descending): Rules with more conditions win
    2. Priority (descending): Higher priority breaks ties
    3. ID (ascending): Final deterministic tie-break

Performance:
    - Rule evaluation is O(n) where n = number of rules
    - Typical deployment: < 50 rules
    - Target evaluation time: < 1ms
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class InvalidRuleError(Exception):
    """Raised when a rule has catastrophic errors that prevent engine initialization."""
    pass


@dataclass
class RoutingContext:
    """Normalized routing context passed to the rule engine.
    
    Attributes:
        query_type: Type of query (exact_fact, comparison, multi_hop, ambiguous, 
                    summarization, other)
        retrieval_state: State of retrieval (SOLID, FRAGILE, CONFLICTED, EMPTY)
        confidence_band: Confidence level (high, medium, low, insufficient)
        evidence_shape: Dict with categorical bands (coverage_band, agreement_band, 
                       spread_band)
        effort_budget: Effort budget level (low, medium, high)
    """
    query_type: str
    retrieval_state: str
    confidence_band: str
    evidence_shape: Dict[str, str] = field(default_factory=dict)
    effort_budget: str = "medium"
    
    # Valid enum values for validation
    VALID_QUERY_TYPES = {"exact_fact", "comparison", "multi_hop", "ambiguous", 
                         "summarization", "other"}
    VALID_RETRIEVAL_STATES = {"SOLID", "FRAGILE", "CONFLICTED", "EMPTY"}
    VALID_CONFIDENCE_BANDS = {"high", "medium", "low", "insufficient"}
    VALID_EFFORT_BUDGETS = {"low", "medium", "high"}
    
    def validate(self) -> None:
        """Validate that all enum values are from known sets.
        
        Raises:
            ValueError: If any field has an invalid value.
        """
        if self.query_type not in self.VALID_QUERY_TYPES:
            raise ValueError(f"Invalid query_type: {self.query_type}")
        if self.retrieval_state not in self.VALID_RETRIEVAL_STATES:
            raise ValueError(f"Invalid retrieval_state: {self.retrieval_state}")
        if self.confidence_band not in self.VALID_CONFIDENCE_BANDS:
            raise ValueError(f"Invalid confidence_band: {self.confidence_band}")
        if self.effort_budget not in self.VALID_EFFORT_BUDGETS:
            raise ValueError(f"Invalid effort_budget: {self.effort_budget}")
        
        # Validate evidence_shape bands if present
        valid_bands = {"high", "medium", "low", "narrow", "wide"}
        for key, value in self.evidence_shape.items():
            if value not in valid_bands:
                raise ValueError(f"Invalid evidence_shape.{key}: {value}")
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize context to dictionary."""
        return {
            "query_type": self.query_type,
            "retrieval_state": self.retrieval_state,
            "confidence_band": self.confidence_band,
            "evidence_shape": self.evidence_shape,
            "effort_budget": self.effort_budget
        }


@dataclass
class RoutingRule:
    """A single routing rule with conditions and action.
    
    Attributes:
        id: Unique identifier for the rule
        enabled: Whether the rule is active
        priority: Numeric priority (higher = more important)
        conditions: Dict of field matches (supports scalar or list values)
        action: Structured action object with execution_path and optional directives
        reason: Human-readable explanation
    """
    id: str
    conditions: Dict[str, Any]
    action: Dict[str, Any]
    enabled: bool = True
    priority: int = 0
    reason: Optional[str] = None
    
    @property
    def specificity(self) -> int:
        """Return the number of condition fields (higher = more specific)."""
        return len(self.conditions)
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize rule to dictionary."""
        return {
            "id": self.id,
            "enabled": self.enabled,
            "priority": self.priority,
            "conditions": self.conditions,
            "action": self.action,
            "reason": self.reason
        }


@dataclass
class RoutingDecision:
    """Result of a routing decision.
    
    Attributes:
        execution_path: The chosen execution path (fast/standard/cautious/abstain)
        matched_rule_id: ID of the winning rule (None if fallback)
        matched_rule_priority: Priority of winning rule
        matched_rule_specificity: Specificity of winning rule
        fallback_used: Whether fallback logic was applied
        fallback_reason: Reason for fallback (if applicable)
        action: Full action object from the winning rule
        budget_override_applied: Whether budget constraint downgraded the path
        requested_execution_path: Original path before budget override
    """
    execution_path: str
    matched_rule_id: Optional[str] = None
    matched_rule_priority: Optional[int] = None
    matched_rule_specificity: Optional[int] = None
    fallback_used: bool = False
    fallback_reason: Optional[str] = None
    action: Dict[str, Any] = field(default_factory=dict)
    budget_override_applied: bool = False
    requested_execution_path: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize decision to dictionary for telemetry."""
        return {
            "execution_path": self.execution_path,
            "matched_rule_id": self.matched_rule_id,
            "matched_rule_priority": self.matched_rule_priority,
            "matched_rule_specificity": self.matched_rule_specificity,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
            "action": self.action,
            "budget_override_applied": self.budget_override_applied,
            "requested_execution_path": self.requested_execution_path
        }


class RuleEngine:
    """Declarative rule-table routing engine.
    
    Evaluates rules against a RoutingContext and returns a RoutingDecision
    based on specificity > priority > ID precedence.
    
    Attributes:
        rules: List of enabled RoutingRule objects
        defaults: Default routing decisions by confidence band
        _all_rules: All rules including disabled (for inspection)
    """
    
    # Valid condition field names
    VALID_CONDITION_FIELDS = {
        "query_type", "retrieval_state", "confidence_band", 
        "evidence_shape", "effort_budget"
    }
    
    def __init__(
        self, 
        rules: List[RoutingRule], 
        defaults: Optional[Dict[str, Any]] = None
    ):
        """Initialize the rule engine.
        
        Args:
            rules: List of routing rules
            defaults: Default routing decisions by confidence band
            
        Raises:
            InvalidRuleError: If rules have duplicate IDs or other fatal errors
        """
        self._all_rules = list(rules)
        self.defaults = defaults or self._default_confidence_defaults()
        
        # Validate and filter rules
        self.rules: List[RoutingRule] = []
        seen_ids = set()
        invalid_count = 0
        
        for rule in rules:
            # Check for duplicate IDs
            if rule.id in seen_ids:
                raise InvalidRuleError(f"Duplicate rule ID: {rule.id}")
            seen_ids.add(rule.id)
            
            # Validate required fields
            if not self._validate_rule(rule):
                invalid_count += 1
                continue
            
            # Only include enabled rules
            if rule.enabled:
                self.rules.append(rule)
        
        if invalid_count > 0:
            logger.warning(f"Skipped {invalid_count} invalid rules")
        
        logger.info(
            f"RuleEngine initialized: {len(self.rules)} enabled rules "
            f"({len(self._all_rules)} total)"
        )
    
    def _default_confidence_defaults(self) -> Dict[str, Any]:
        """Return default routing by confidence band."""
        return {
            "by_confidence_band": {
                "high": "fast",
                "medium": "standard",
                "low": "cautious",
                "insufficient": "abstain"
            }
        }
    
    def _validate_rule(self, rule: RoutingRule) -> bool:
        """Validate a single rule.
        
        Returns:
            True if valid, False if should be skipped
            
        Raises:
            InvalidRuleError: For catastrophic errors
        """
        # Check required fields
        if not rule.id:
            logger.error("Rule missing required field: id")
            return False
        
        if not rule.conditions:
            logger.warning(f"Rule {rule.id} has no conditions")
            return False
        
        if not rule.action:
            logger.error(f"Rule {rule.id} missing required field: action")
            return False
        
        if "execution_path" not in rule.action:
            logger.error(f"Rule {rule.id} action missing execution_path")
            return False
        
        # Check condition field names
        for field_name in rule.conditions.keys():
            if field_name not in self.VALID_CONDITION_FIELDS:
                logger.warning(
                    f"Rule {rule.id} has unknown condition field: {field_name}"
                )
        
        return True
    
    def _evaluate_rule(self, rule: RoutingRule, context: RoutingContext) -> bool:
        """Check if a rule matches the given context.
        
        Condition matching logic:
        - If condition value is list: check membership (context[field] in list)
        - If condition value is scalar: check equality
        - Missing context field = no match (rule doesn't apply)
        
        Args:
            rule: The routing rule to evaluate
            context: The routing context
            
        Returns:
            True if all conditions match, False otherwise
        """
        context_dict = context.to_dict()
        
        for field_name, condition_value in rule.conditions.items():
            # Get context value
            if field_name == "evidence_shape":
                # Evidence shape conditions are nested
                if isinstance(condition_value, dict):
                    for shape_key, shape_value in condition_value.items():
                        actual_value = context.evidence_shape.get(shape_key)
                        if not self._match_value(actual_value, shape_value):
                            return False
                    continue
                else:
                    # Treat as regular field comparison
                    actual_value = context_dict.get(field_name)
            else:
                actual_value = context_dict.get(field_name)
            
            # Missing field = no match
            if actual_value is None:
                return False
            
            # Check match
            if not self._match_value(actual_value, condition_value):
                return False
        
        return True
    
    def _match_value(self, actual: Any, expected: Any) -> bool:
        """Match an actual value against an expected condition.
        
        Supports:
        - Scalar equality: actual == expected
        - List membership: actual in expected (if expected is list)
        """
        if isinstance(expected, (list, tuple)):
            return actual in expected
        return actual == expected
    
    def _compute_specificity(self, rule: RoutingRule) -> int:
        """Compute specificity score for a rule."""
        return rule.specificity
    
    def route(self, context: RoutingContext) -> RoutingDecision:
        """Route a context through the rule engine.
        
        Precedence:
        1. Filter to enabled rules (already done in __init__)
        2. Find all matching rules using _evaluate_rule()
        3. If no matches: return fallback decision
        4. Sort matches by: specificity desc, priority desc, id asc
        5. Winner = matches[0]
        6. Return RoutingDecision with winner's action
        
        Args:
            context: The routing context to route
            
        Returns:
            A RoutingDecision
        """
        try:
            context.validate()
        except ValueError as e:
            logger.error(f"Invalid routing context: {e}")
            return self._fallback_decision(
                context, 
                fallback_reason=f"invalid_context: {e}"
            )
        
        # Find all matching rules
        matches = [
            rule for rule in self.rules 
            if self._evaluate_rule(rule, context)
        ]
        
        # No matches → fallback
        if not matches:
            return self._fallback_decision(context, fallback_reason="no_matching_rule")
        
        # Sort by specificity desc, priority desc, id asc
        matches.sort(
            key=lambda r: (
                -self._compute_specificity(r),  # Higher specificity first
                -r.priority,                     # Higher priority first
                r.id                             # Ascending ID for stability
            )
        )
        
        winner = matches[0]
        
        logger.debug(
            f"Routing decision: rule={winner.id}, "
            f"specificity={winner.specificity}, priority={winner.priority}"
        )
        
        return RoutingDecision(
            execution_path=winner.action["execution_path"],
            matched_rule_id=winner.id,
            matched_rule_priority=winner.priority,
            matched_rule_specificity=winner.specificity,
            fallback_used=False,
            action=winner.action,
            fallback_reason=None
        )
    
    def _fallback_decision(
        self, 
        context: RoutingContext, 
        fallback_reason: str
    ) -> RoutingDecision:
        """Create a fallback decision based on confidence band.
        
        Uses defaults['by_confidence_band'][context.confidence_band] to
        preserve Phase 4 behavior when no contextual rules match.
        """
        band_defaults = self.defaults.get("by_confidence_band", {})
        
        # Get path for this confidence band
        execution_path = band_defaults.get(context.confidence_band)
        
        # Unknown band → cautious + warning
        if execution_path is None:
            logger.warning(
                f"Unknown confidence band: {context.confidence_band}, "
                f"defaulting to cautious"
            )
            execution_path = "cautious"
        
        logger.debug(
            f"Fallback routing: band={context.confidence_band}, "
            f"path={execution_path}, reason={fallback_reason}"
        )
        
        return RoutingDecision(
            execution_path=execution_path,
            matched_rule_id=None,
            matched_rule_priority=None,
            matched_rule_specificity=None,
            fallback_used=True,
            fallback_reason=fallback_reason,
            action={"execution_path": execution_path}
        )
    
    def get_rule(self, rule_id: str) -> Optional[RoutingRule]:
        """Get a rule by ID (including disabled rules)."""
        for rule in self._all_rules:
            if rule.id == rule_id:
                return rule
        return None
    
    def list_rules(self, include_disabled: bool = False) -> List[RoutingRule]:
        """List all rules."""
        if include_disabled:
            return list(self._all_rules)
        return list(self.rules)

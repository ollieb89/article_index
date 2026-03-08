"""Budget constraint layer for Phase 5 contextual routing.

The BudgetConstraint acts as a post-routing guardrail that enforces effort budgets
without complicating the rule engine. It can downgrade execution paths but never
upgrade them, and it protects abstention decisions as a safety constraint.

Usage Example:
    ```python
    constraint = BudgetConstraint()
    
    decision = RoutingDecision(execution_path="cautious", ...)
    budget = "low"  # low budget means max "standard" path
    
    constrained = constraint.apply(decision, budget)
    # constrained.execution_path == "standard" (downgraded)
    # constrained.budget_override_applied == True
    ```

Budget Levels:
    - low: max "standard" path
    - medium: max "cautious" path  
    - high: no limit

Safety Constraints:
    - Never upgrade paths (only downgrade or keep same)
    - Never override abstain decisions
    - Insufficient confidence always routes to abstain
"""

import logging
from typing import Dict
from shared.routing_engine import RoutingDecision

logger = logging.getLogger(__name__)


class BudgetConstraint:
    """Post-routing budget constraint layer.
    
    Enforces effort budgets by potentially downgrading execution paths
    while preserving safety guarantees.
    
    Attributes:
        budget_levels: Dict mapping budget level to max allowed path
        path_order: Ordered list of paths from least to most expensive
    """
    
    # Path ordering: least to most conservative/expensive
    PATH_ORDER = ["fast", "standard", "cautious", "abstain"]
    
    def __init__(self, budget_levels: Dict[str, str] = None):
        """Initialize budget constraint with configurable levels.
        
        Args:
            budget_levels: Dict mapping budget level to max allowed path.
                         Defaults: low→standard, medium→cautious, high→no limit
        """
        self.budget_levels = budget_levels or {
            "low": "standard",
            "medium": "cautious",
            "high": None  # No limit
        }
        
        logger.debug(
            f"BudgetConstraint initialized: {self.budget_levels}"
        )
    
    def apply(self, decision: RoutingDecision, budget: str) -> RoutingDecision:
        """Apply budget constraint to a routing decision.
        
        Flow:
        1. Check if decision.execution_path exceeds budget max
        2. Path ordering: fast < standard < cautious < abstain
        3. If over budget: downgrade to max allowed
        4. If abstain: never override (safety constraint)
        5. Update requested_execution_path for telemetry
        6. Return modified decision
        
        Args:
            decision: The routing decision from the rule engine
            budget: The effort budget level (low/medium/high)
            
        Returns:
            RoutingDecision (possibly downgraded)
        """
        # Safety: never override abstain
        if decision.execution_path == "abstain":
            logger.debug("Budget constraint: preserving abstain decision")
            return decision
        
        # Get max allowed path for this budget
        max_path = self.budget_levels.get(budget)
        
        # No limit (high budget) → return unchanged
        if max_path is None:
            logger.debug(f"Budget '{budget}' has no limit, decision unchanged")
            return decision
        
        # Check if current path exceeds budget
        current_idx = self.PATH_ORDER.index(decision.execution_path)
        max_idx = self.PATH_ORDER.index(max_path)
        
        if current_idx <= max_idx:
            # Within budget → no change
            logger.debug(
                f"Path '{decision.execution_path}' within budget '{budget}' "
                f"(max: {max_path})"
            )
            return decision
        
        # Over budget → downgrade
        downgraded_path = self.downgrade_path(decision.execution_path, max_path)
        
        logger.info(
            f"Budget constraint applied: {decision.execution_path} → {downgraded_path} "
            f"(budget: {budget}, max: {max_path})"
        )
        
        # Create new decision with override tracking
        return RoutingDecision(
            execution_path=downgraded_path,
            matched_rule_id=decision.matched_rule_id,
            matched_rule_priority=decision.matched_rule_priority,
            matched_rule_specificity=decision.matched_rule_specificity,
            fallback_used=decision.fallback_used,
            fallback_reason=decision.fallback_reason,
            action={**decision.action, "execution_path": downgraded_path},
            budget_override_applied=True,
            requested_execution_path=decision.execution_path
        )
    
    def downgrade_path(self, path: str, max_path: str) -> str:
        """Return the more conservative of two paths.
        
        Args:
            path: The requested execution path
            max_path: The maximum allowed execution path
            
        Returns:
            The more conservative path (lower in PATH_ORDER)
        """
        path_idx = self.PATH_ORDER.index(path)
        max_idx = self.PATH_ORDER.index(max_path)
        
        # Return the earlier (more conservative) path
        if path_idx <= max_idx:
            return path
        return max_path
    
    def is_within_budget(self, path: str, budget: str) -> bool:
        """Check if a path is within a given budget.
        
        Args:
            path: Execution path to check
            budget: Budget level
            
        Returns:
            True if path is within budget
        """
        max_path = self.budget_levels.get(budget)
        
        if max_path is None:
            return True  # No limit
        
        path_idx = self.PATH_ORDER.index(path)
        max_idx = self.PATH_ORDER.index(max_path)
        
        return path_idx <= max_idx

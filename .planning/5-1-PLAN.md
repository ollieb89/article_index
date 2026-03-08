# Phase 5 Plan 1: Core Rule Engine

<plan phase="5" plan="1">
  <overview>
    <phase_name>Contextual Policy Routing — Core Rule Engine</phase_name>
    <goal>Implement declarative rule-table routing engine with specificity/priority precedence</goal>
    <requirements>CTX-01, CTX-03 (partial)</requirements>
    <waves>1-2 (RuleEngine class, RoutingContext dataclass, precedence algorithm)</waves>
  </overview>
  
  <dependencies>
    <complete>Phase 4: Policy Infrastructure Hardening</complete>
    <complete>5-CONTEXT.md with architectural decisions locked</complete>
  </dependencies>
  
  <tasks>
    <task type="auto" priority="1">
      <name>Create RoutingContext dataclass</name>
      <files>shared/routing_context.py (new)</files>
      <action>
        Create RoutingContext dataclass with fields:
        - query_type: str (exact_fact, comparison, multi_hop, ambiguous, summarization, other)
        - retrieval_state: str (SOLID, FRAGILE, CONFLICTED, EMPTY)
        - confidence_band: str (high, medium, low, insufficient)
        - evidence_shape: Dict[str, str] with coverage_band, agreement_band, spread_band
        - effort_budget: str (low, medium, high)
        
        Add validation method validate() that checks enum values are valid.
        Add to_dict() for serialization.
      </action>
      <verify>Unit test: create context, validate, serialize, all enum values accepted</verify>
      <done>RoutingContext exists with all fields, validation works</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Create RoutingRule dataclass</name>
      <files>shared/routing_engine.py</files>
      <action>
        Create RoutingRule dataclass with fields:
        - id: str (unique identifier)
        - enabled: bool (default True)
        - priority: int (higher = more important)
        - conditions: Dict[str, Any] (field matches, supports scalar or list)
        - action: Dict[str, Any] (structured action object)
        - reason: Optional[str] (human-readable explanation)
        
        Add computed property specificity() that returns count of condition keys.
      </action>
      <verify>Unit test: create rule, specificity computed correctly, action is dict</verify>
      <done>RoutingRule exists with all fields, specificity works</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Create RoutingDecision dataclass</name>
      <files>shared/routing_engine.py</files>
      <action>
        Create RoutingDecision dataclass with fields:
        - execution_path: str (fast/standard/cautious/abstain)
        - matched_rule_id: Optional[str]
        - matched_rule_priority: Optional[int]
        - matched_rule_specificity: Optional[int]
        - fallback_used: bool
        - fallback_reason: Optional[str]
        - action: Dict[str, Any] (full action object)
        - budget_override_applied: bool (default False)
        - requested_execution_path: Optional[str] (for budget override telemetry)
        
        Add to_dict() for telemetry serialization.
      </action>
      <verify>Unit test: create decision, all fields present, serialization works</verify>
      <done>RoutingDecision exists with all Phase 5 telemetry fields</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Implement RuleEngine class with match evaluation</name>
      <files>shared/routing_engine.py</files>
      <action>
        Create RuleEngine class with:
        - __init__(rules: List[RoutingRule], defaults: Dict)
        - _evaluate_rule(rule, context) -> bool: checks if all conditions match
          - Supports scalar equality: conditions[field] == context[field]
          - Supports list membership: context[field] in conditions[field]
        - _compute_specificity(rule) -> int: returns len(rule.conditions)
        
        Condition matching logic:
        - If condition value is list: check membership
        - If condition value is scalar: check equality
        - Missing context field = no match (rule doesn't apply)
      </action>
      <verify>Unit test: various condition types, list membership, missing fields</verify>
      <done>RuleEngine evaluates rules correctly against context</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Implement specificity > priority > ID precedence algorithm</name>
      <files>shared/routing_engine.py</files>
      <action>
        Add route(context: RoutingContext) -> RoutingDecision method to RuleEngine:
        
        1. Filter to enabled rules only
        2. Find all matching rules using _evaluate_rule()
        3. If no matches: return fallback decision
        4. Sort matches by: specificity desc, priority desc, id asc
        5. Winner = matches[0]
        6. Return RoutingDecision with:
           - execution_path = winner.action['execution_path']
           - matched_rule_id = winner.id
           - matched_rule_priority = winner.priority
           - matched_rule_specificity = winner.specificity
           - fallback_used = False
           - action = winner.action
        
        Sorting must be stable and deterministic.
      </action>
      <verify>Unit test: 
        - specificity tie-break (4-cond beats 3-cond)
        - priority tie-break (same specificity)
        - ID tie-break (same specificity + priority)
        - no matches → fallback
      </verify>
      <done>Precedence algorithm works correctly for all tie-break scenarios</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Implement fallback to confidence-band defaults</name>
      <files>shared/routing_engine.py</files>
      <action>
        Implement fallback logic in route():
        
        If no rules match:
        - Use defaults['by_confidence_band'][context.confidence_band]
        - Return RoutingDecision with:
          - execution_path = default path
          - matched_rule_id = None
          - fallback_used = True
          - fallback_reason = "no_matching_rule"
        
        Defaults structure:
        {
          "by_confidence_band": {
            "high": "fast",
            "medium": "standard", 
            "low": "cautious",
            "insufficient": "abstain"
          }
        }
        
        If confidence_band not in defaults → route to "cautious" + log warning.
      </action>
      <verify>Unit test: fallback for each confidence band, unknown band handling</verify>
      <done>Fallback logic works, preserves Phase 4 behavior</done>
    </task>
    
    <task type="auto" priority="2">
      <name>Add rule validation and error handling</name>
      <files>shared/routing_engine.py</files>
      <action>
        Add validation to RuleEngine.__init__():
        - Check all rules have required fields (id, conditions, action)
        - Check rule IDs are unique
        - Log warning for rules with invalid condition field names
        - Skip invalid rules (don't crash engine)
        
        Add InvalidRuleError exception for catastrophic rule errors.
        
        Log at INFO: number of rules loaded, number enabled.
      </action>
      <verify>Unit test: duplicate ID detection, invalid rule skipped, valid rules work</verify>
      <done>Engine validates rules, handles errors gracefully</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Create comprehensive unit tests for RuleEngine</name>
      <files>tests/test_rule_engine.py (new)</files>
      <action>
        Create test suite covering:
        - Basic rule matching (exact match)
        - List membership matching
        - Specificity ordering (4 > 3 > 2 conditions)
        - Priority ordering (same specificity)
        - ID tie-break (same specificity + priority)
        - Fallback behavior (no matches)
        - Empty ruleset
        - All rules disabled
        - Invalid rules skipped
        - Complex scenario: multiple matches, correct winner selected
        
        Use parameterized tests where appropriate.
      </action>
      <verify>pytest tests/test_rule_engine.py -v passes 100%</verify>
      <done>RuleEngine has comprehensive test coverage</done>
    </task>
    
    <task type="auto" priority="2">
      <name>Document RuleEngine with examples</name>
      <files>shared/routing_engine.py</files>
      <action>
        Add module docstring with:
        - Usage example
        - Rule definition example
        - Precedence explanation
        - Performance characteristics
        
        Add docstrings to all public methods.
      </action>
      <verify>Docs build without errors, examples are runnable</verify>
      <done>RuleEngine is well-documented</done>
    </task>
  </tasks>
</plan>

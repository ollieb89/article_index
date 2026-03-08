# Phase 5 Plan 3: Integration & Budget Constraint

<plan phase="5" plan="3">
  <overview>
    <phase_name>Contextual Policy Routing — Integration & Budget Constraint</phase_name>
    <goal>Integrate rule engine into RAG pipeline, implement budget constraint layer, E2E tests</goal>
    <requirements>CTX-02 (effort budget), CTX-04 (end-to-end integration)</requirements>
    <waves>5-6 (Integration, Budget Guardrail, E2E Testing)</waves>
  </overview>
  
  <dependencies>
    <complete>Plan 5-1: Core Rule Engine</complete>
    <complete>Plan 5-2: Query Classification & Evidence Shape</complete>
    <requires>RuleEngine, RoutingContext, QueryClassifier, EvidenceShapeExtractor</requires>
  </dependencies>
  
  <tasks>
    <task type="auto" priority="1">
      <name>Create ContextualRouterV2 integrating rule engine</name>
      <files>api/contextual_router_v2.py (new)</files>
      <action>
        Create ContextualRouterV2 class that replaces/extends existing ContextualRouter:
        
        __init__(policy: RAGPolicy):
        - Parse policy.contextual_routing_rules into RoutingRule objects
        - Create RuleEngine with rules
        - Store defaults from policy.routing_defaults
        
        route(context: RoutingContext) -> RoutingDecision:
        - Delegate to RuleEngine.route()
        - Return RoutingDecision
        
        This is the bridge between policy JSON and rule engine.
      </action>
      <verify>Unit test: load policy with rules, route queries, correct decisions</verify>
      <done>ContextualRouterV2 integrates rule engine with policy</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Implement BudgetConstraint layer</name>
      <files>shared/budget_constraint.py (new)</files>
      <action>
        Create BudgetConstraint class:
        
        __init__(budget_levels: Dict[str, str]):
        - Configure max allowed path per budget level
        - Default: low→standard, medium→cautious, high→no limit
        
        apply(decision: RoutingDecision, budget: str) -> RoutingDecision:
        - Check if decision.execution_path exceeds budget max
        - Path ordering: fast &lt; standard &lt; cautious &lt; abstain
        - If over budget: downgrade to max allowed, set budget_override_applied=True
        - If abstain: never override (safety constraint)
        - Update requested_execution_path for telemetry
        - Return modified decision
        
        downgrade_path(path: str, max_path: str) -> str:
        - Return the more conservative of the two paths
        - Never upgrade, only downgrade or keep same
      </action>
      <verify>Unit test: various budget/path combinations, abstain protection</verify>
      <done>Budget constraint layer implemented with safety guards</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Integrate contextual routing into _rag_hybrid pipeline</name>
      <files>api/app.py</files>
      <action>
        Update _rag_hybrid() to use ContextualRouterV2:
        
        1. After evidence extraction, build RoutingContext:
           - query_type from classifier
           - retrieval_state from state_labeler  
           - confidence_band from evidence_scorer
           - evidence_shape from shape_extractor
           - effort_budget from policy or default
        
        2. Call contextual_router_v2.route(routing_context) → decision
        
        3. Apply budget constraint:
           - decision = budget_constraint.apply(decision, effort_budget)
        
        4. Use decision.execution_path for routing (fast/standard/cautious/abstain)
        
        5. Populate trace with decision fields:
           - matched_rule_id
           - matched_rule_priority
           - matched_rule_specificity
           - fallback_used
           - budget_override_applied
           - requested_execution_path (if overridden)
        
        Remove old confidence-band-only routing logic.
      </action>
      <verify>Run RAG query, verify contextual routing used, check telemetry</verify>
      <done>Contextual routing integrated into RAG pipeline</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Create default Phase 5 policy with contextual rules</name>
      <files>shared/default_policies.py (new or update)</files>
      <action>
        Create default Phase 5 policy JSON:
        
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
          "contextual_routing_rules": [
            {
              "id": "exact_fact_solid_high",
              "enabled": true,
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
              "id": "fragile_guardrail",
              "enabled": true,
              "priority": 200,
              "conditions": {"retrieval_state": "FRAGILE"},
              "action": {
                "execution_path": "cautious",
                "expand_retrieval": true,
                "invoke_reranker": true
              },
              "reason": "Fragile retrieval forces cautious handling"
            },
            {
              "id": "conflicted_guardrail",
              "enabled": true,
              "priority": 200,
              "conditions": {"retrieval_state": "CONFLICTED"},
              "action": {
                "execution_path": "cautious",
                "expand_retrieval": true,
                "invoke_reranker": true
              },
              "reason": "Conflicting evidence requires careful handling"
            },
            {
              "id": "empty_abstain",
              "enabled": true,
              "priority": 300,
              "conditions": {"retrieval_state": "EMPTY"},
              "action": {"execution_path": "abstain", "generation_skipped": true},
              "reason": "No evidence available"
            },
            {
              "id": "comparison_solid",
              "enabled": true,
              "priority": 100,
              "conditions": {
                "query_type": "comparison",
                "retrieval_state": "SOLID"
              },
              "action": {"execution_path": "standard"},
              "reason": "Standard handling for comparisons with solid evidence"
            },
            {
              "id": "low_confidence_cautious",
              "enabled": true,
              "priority": 150,
              "conditions": {"confidence_band": "low"},
              "action": {"execution_path": "cautious"},
              "reason": "Low confidence requires cautious approach"
            }
          ]
        }
        
        This provides sensible defaults that extend Phase 4 behavior.
      </action>
      <verify>Load policy, verify rules parse correctly, all have valid structure</verify>
      <done>Default Phase 5 policy with contextual rules created</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Create admin endpoint for policy validation</name>
      <files>api/app.py</files>
      <action>
        Add POST /admin/policy/validate endpoint:
        
        Accepts policy JSON, returns validation result:
        {
          "valid": true/false,
          "errors": ["error messages"],
          "warnings": ["warning messages"],
          "rule_count": 6,
          "enabled_rule_count": 6
        }
        
        Validation checks:
        - All rules have required fields
        - Rule IDs are unique
        - Condition field names are valid
        - Action has execution_path
        - Priority values are reasonable (0-1000)
      </action>
      <verify>Test with valid policy, invalid policy, verify errors reported</verify>
      <done>Policy validation endpoint available</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Create E2E test: contextual routing decisions</name>
      <files>tests/test_contextual_routing_e2e.py (new)</files>
      <action>
        Create E2E tests for key routing scenarios:
        
        - exact_fact + SOLID + high → fast path
        - comparison + SOLID → standard path
        - FRAGILE retrieval → cautious path (regardless of confidence)
        - CONFLICTED retrieval → cautious path
        - EMPTY retrieval → abstain
        - low confidence → cautious path
        - No rule match → fallback to confidence-band default
        
        Use forced context via headers (similar to Phase 3 CI override).
        Verify via telemetry: matched_rule_id, execution_path.
      </action>
      <verify>pytest tests/test_contextual_routing_e2e.py -v passes</verify>
      <done>Contextual routing E2E tests verify correct decisions</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Create E2E test: budget constraint</name>
      <files>tests/test_budget_constraint_e2e.py (new)</files>
      <action>
        Create E2E tests for budget constraint layer:
        
        - cautious path + low budget → downgraded to standard
        - standard path + low budget → unchanged (already ≤ max)
        - cautious path + medium budget → unchanged (cautious ≤ max)
        - abstain + any budget → unchanged (safety constraint)
        - budget_override_applied flag in telemetry
        - requested_execution_path vs final_execution_path
        
        Force budget via header: X-Test-Effort-Budget: low/medium/high
      </action>
      <verify>pytest tests/test_budget_constraint_e2e.py -v passes</verify>
      <done>Budget constraint E2E tests verify downgrades</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Create E2E test: rule precedence</name>
      <files>tests/test_rule_precedence_e2e.py (new)</files>
      <action>
        Create E2E tests verifying precedence algorithm:
        
        - More specific rule beats less specific (4-cond vs 3-cond)
        - Higher priority beats lower (same specificity)
        - Correct winner selected when multiple rules match
        - Fallback used when no rules match
        - matched_rule_specificity in telemetry matches expected
        
        Use test policy with conflicting rules to verify precedence.
      </action>
      <verify>pytest tests/test_rule_precedence_e2e.py -v passes</verify>
      <done>Rule precedence E2E tests verify algorithm</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Create E2E test: replay with Phase 5 context</name>
      <files>tests/test_phase5_replay_e2e.py (new)</files>
      <action>
        Extend replay tests for Phase 5:
        
        - Verify replay captures query_type, retrieval_state, evidence_shape
        - Verify replay captures matched_rule_id, fallback_used
        - Verify replay captures budget_override_applied
        - Verify deterministic routing from frozen Phase 5 context
        
        Ensure backward compatibility: old traces without Phase 5 fields still replay correctly.
      </action>
      <verify>pytest tests/test_phase5_replay_e2e.py -v passes</verify>
      <done>Phase 5 replay tests verify determinism</done>
    </task>
    
    <task type="auto" priority="2">
      <name>Update migration for Phase 5 telemetry fields</name>
      <files>migrations/008_phase5_contextual.sql (new)</files>
      <action>
        Create migration to add Phase 5 telemetry columns:
        
        ALTER TABLE intelligence.policy_telemetry
        ADD COLUMN IF NOT EXISTS query_type TEXT,
        ADD COLUMN IF NOT EXISTS retrieval_state TEXT,
        ADD COLUMN IF NOT EXISTS evidence_shape JSONB DEFAULT '{}'::jsonb,
        ADD COLUMN IF NOT EXISTS effort_budget TEXT,
        ADD COLUMN IF NOT EXISTS matched_rule_id TEXT,
        ADD COLUMN IF NOT EXISTS matched_rule_priority INTEGER,
        ADD COLUMN IF NOT EXISTS matched_rule_specificity INTEGER,
        ADD COLUMN IF NOT EXISTS fallback_used BOOLEAN DEFAULT FALSE,
        ADD COLUMN IF NOT EXISTS budget_override_applied BOOLEAN DEFAULT FALSE,
        ADD COLUMN IF NOT EXISTS requested_execution_path TEXT,
        ADD COLUMN IF NOT EXISTS telemetry_schema_version TEXT DEFAULT '1.1';
        
        Update policy_repo.log_telemetry() to insert new fields.
      </action>
      <verify>Migration runs successfully, new columns queryable</verify>
      <done>Database schema supports Phase 5 telemetry</done>
    </task>
    
    <task type="auto" priority="2">
      <name>Update documentation and examples</name>
      <files>docs/contextual_routing.md (new)</files>
      <action>
        Create documentation:
        - Overview of contextual routing
        - Query type taxonomy with examples
        - Evidence shape dimensions explained
        - Rule syntax and precedence
        - Budget constraint behavior
        - Example policies
        - Troubleshooting guide
        
        Update AGENTS.md with Phase 5 information.
      </action>
      <verify>Documentation reviewed for accuracy and completeness</verify>
      <done>Phase 5 documented for operators</done>
    </task>
    
    <task type="manual" priority="1">
      <name>Final verification checklist</name>
      <action>
        Run full verification:
        
        1. All unit tests pass
        2. All E2E tests pass
        3. Policy validation endpoint works
        4. Default policy loads correctly
        5. RAG queries use contextual routing
        6. Telemetry includes all Phase 5 fields
        7. Replay works for Phase 5 traces
        8. Budget constraint applies correctly
        9. Fallback behavior correct
        
        Update STATE.md and ROADMAP.md with Phase 5 completion.
      </action>
      <verify>All checklist items verified</verify>
      <done>Phase 5 complete and verified</done>
    </task>
  </tasks>
</plan>

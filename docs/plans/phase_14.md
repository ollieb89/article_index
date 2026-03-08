# Phase 14 — Contextual Policy Routing

## Objective

Upgrade the RAG control architecture from**confidence-driven routing (Phase 11–13)**to**context-aware routing**.

Instead of routing decisions based solely on confidence bands, the system will incorporate:

- query type


- retrieval evidence shape


- derived retrieval state


- latency/effort budgets


This allows the system to apply different retrieval and prompting strategies depending on**what kind of question is asked and how strong the evidence is**.

The Phase 14 system must remain:

- explainable


- versioned


- replayable via the Phase 13 policy replay harness


- compatible with existing telemetry and tuning infrastructure.


---

# Milestone Overview

MilestoneComponentGoalM1Query ClassificationIdentify operational query typesM2Evidence Shape ExtractionDescribe structure of retrieved evidenceM3Retrieval State LabelingConvert evidence signals into routing statesM4Contextual RouterRoute actions using query type + evidenceM5Policy Registry ExtensionAdd routing rules and effort budgetsM6Telemetry UpgradeLog contextual routing decisionsM7Replay IntegrationEvaluate routing via replay harnessM8Diagnostics & Admin APIsProvide route explanation and monitoringM9Integration TestsValidate routing behavior across scenarios---

# Milestone 1 — Query Classification

## Goal

Determine a lightweight**query_type**for each request.

## Query Types

- `exact_fact`


- `comparison`


- `summarization`


- `ambiguous`


- `multi_hop`


- `procedural`


- `likely_no_answer`


- `unknown`


## Tasks

Implement:

```
`api/query_classifier.py`
```

Add function:

```
`class QueryClassifier:
    def classify(query: str) -> QueryType`
```

Initial implementation may be**rule-based**using:

- WH-word patterns


- token length


- entity count


- comparison keywords


- clause count


## Deliverables

File:

```
`api/query_classifier.py`
```

Integration in`_rag_hybrid()`.

## Acceptance Criteria

1. All queries return a valid`query_type`.


1. Classification latency <**5ms**.


1. Telemetry records`query_type`.


1. Unit tests pass.


---

# Milestone 2 — Evidence Shape Extraction

## Goal

Describe structural characteristics of retrieved evidence before generation.

## Extracted Features

- `top1_score`


- `topk_mean_score`


- `score_gap_top1_top2`


- `chunk_agreement`


- `entity_overlap`


- `source_diversity`


- `coverage_ratio`


- `contradiction_flag`


- `retrieved_chunk_count`


## Tasks

Implement:

```
`api/evidence_shape.py`
```

Function:

```
`extract_evidence_shape(retrieval_result, query) -> EvidenceShape`
```

## Deliverables

Structured object:

```
`{
  top1_score,
  topk_mean_score,
  chunk_agreement,
  coverage_ratio,
  source_diversity,
  contradiction_flag
}`
```

## Acceptance Criteria

1. Evidence shape extraction <**10ms**.


1. Shape data logged in telemetry.


1. Contradiction detection triggers correctly in synthetic tests.


---

# Milestone 3 — Retrieval State Labeling

## Goal

Convert evidence signals into operational routing states.

## Retrieval States

- `strong`


- `recoverable`


- `fragile`


- `insufficient`


- `conflicted`


## Tasks

Implement:

```
`api/retrieval_state.py`
```

Function:

```
`label_retrieval_state(evidence_shape) -> RetrievalState`
```

Example rules:

```
`if contradiction_flag:
    conflicted
elif top1_score > threshold and chunk_agreement high:
    strong
elif moderate score and moderate overlap:
    recoverable
elif weak evidence:
    fragile
else:
    insufficient`
```

## Deliverables

Retrieval state label integrated into routing.

## Acceptance Criteria

1. All requests produce a retrieval state.


1. Unit tests cover each state.


1. Conflicted state triggers when contradictory chunks appear.


---

# Milestone 4 — Contextual Router

## Goal

Replace band-only routing with contextual routing.

## Inputs

```
`query_type
confidence_band
retrieval_state
evidence_shape
latency_budget
policy_version`
```

## Output

```
`RouteDecision`
```

Fields:

```
`action_taken
execution_path
prompt_variant
retrieval_budget`
```

## Tasks

Create:

```
`api/routing.py`
```

Core method:

```
`route(context) -> RouteDecision`
```

Routing must consult:

- query type


- confidence band


- retrieval state


- policy registry


## Deliverables

Contextual router integrated into`_rag_hybrid`.

## Acceptance Criteria

1. Router selects valid action for all combinations.


1. Routing decision <**5ms**.


1. Execution path logged in telemetry.


---

# Milestone 5 — Policy Registry Extension

## Goal

Extend the Phase 13 policy registry with contextual routing.

## Schema Updates

Add fields:

```
`routing_rules JSONB
effort_budgets JSONB
prompt_policies JSONB
query_type_threshold_overrides JSONB`
```

Example routing config:

```
`"routing_rules": {
  "exact_fact": {
    "high": "standard",
    "medium": "rerank_only",
    "low": "conservative",
    "insufficient": "abstain"
  }
}`
```

## Tasks

Update migration:

```
`migrations/006_contextual_routing.sql`
```

Modify:

```
`Policy.load_active_policy()`
```

## Acceptance Criteria

1. Policy registry loads routing rules correctly.


1. Backward compatibility with Phase 13 policy.


---

# Milestone 6 — Telemetry Upgrade

## Goal

Capture contextual routing decisions for evaluation and replay.

## New Telemetry Fields

```
`query_type
retrieval_state
top1_score
chunk_agreement
coverage_ratio
contradiction_flag
prompt_variant
retrieval_budget
execution_path`
```

## Tasks

Update:

```
`PolicyTrace
policy_telemetry table`
```

Modify async logging in`_rag_hybrid`.

## Acceptance Criteria

1. All fields present in telemetry.


1. Replay harness can reconstruct routing decision.


1. Logging overhead <**5ms**.


---

# Milestone 7 — Replay Integration

## Goal

Evaluate contextual routing using Phase 13 replay harness.

## Tasks

Update:

```
`scripts/replay_policy.py`
```

Add ability to replay:

```
`query_type
retrieval_state`
```

Compute:

- route regret


- expansion yield by query type


- latency vs route


- groundedness by route


## Acceptance Criteria

1. Replay supports contextual router.


1. Route regret computed per query type.


---

# Milestone 8 — Admin & Diagnostics APIs

## Goal

Provide introspection into routing decisions.

## Endpoint

```
`GET /admin/policy_route_explain?query_id=`
```

Returns:

```
`{
  query_type,
  confidence_band,
  retrieval_state,
  action_taken,
  execution_path,
  route_reason
}`
```

Additional endpoints:

```
`/admin/route_distribution
/admin/route_regret`
```

## Acceptance Criteria

1. Route explanation matches replay logic.


1. Diagnostics show distribution by query type.


---

# Milestone 9 — Testing

## Unit Tests

```
`tests/test_query_classifier.py
tests/test_evidence_shape.py
tests/test_retrieval_state.py
tests/test_contextual_router.py
tests/test_prompt_policy_manager.py`
```

## Integration Tests

```
`tests/test_phase14_exact_fact_fast_path.py
tests/test_phase14_ambiguous_expands.py
tests/test_phase14_multi_hop_deep_retrieval.py
tests/test_phase14_conflicted_evidence_safe.py
tests/test_phase14_no_answer_early_abstain.py`
```

## Replay Tests

```
`tests/test_route_replay_regret.py
tests/test_query_type_route_delta.py`
```

## Acceptance Criteria

1. All tests pass.


1. Routing logic reproducible in replay.


1. No regressions in Phase 11 guardrails.


---

# Success Criteria

Phase 14 is complete when:

1. Routing decisions incorporate**query type and evidence state**.


1. Exact fact queries show**lower latency without accuracy loss**.


1. Ambiguous and multi-hop queries show**higher expansion yield**.


1. Conflicted evidence reduces unsupported claims.


1. No-answer queries abstain earlier with high precision.


1. Route decisions are**fully logged and replayable**.


---

# Deliverables

New modules:

```
`api/query_classifier.py
api/evidence_shape.py
api/retrieval_state.py
api/routing.py`
```

Updated components:

```
`api/app.py
policy_registry
policy_telemetry
replay_policy.py`
```

Admin APIs:

```
`/admin/policy_route_explain
/admin/route_distribution
/admin/route_regret`
```

---

# Estimated Complexity

ComponentDifficultyQuery classifierLowEvidence shape extractionMediumRetrieval state labelingLowContextual routerMediumTelemetry upgradeLowReplay integrationMediumEstimated effort:**2–4 development days**

---

# Phase 14 Summary

Phase 14 upgrades the RAG pipeline from:

**confidence-based control → contextual routing control**

Routing decisions now consider:

- question type


- evidence structure


- retrieval confidence


- latency budgets


This enables the system to allocate effort where it provides the most benefit while preserving safety and explainability.

---
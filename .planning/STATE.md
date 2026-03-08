# Project State

**Project:** Article Index — Control Architecture Milestone
**Initialized:** 2026-03-08
**Current Phase:** 04

## Current Status

| Field | Value |
|-------|-------|
| Active Phase | 5: Contextual Routing |
| Phase Status | Planning Complete — Context locked, 3 plans ready to execute |
| Last Action | Phase 5 design complete — Rule engine architecture, query taxonomy, evidence shape, budget constraint locked |
| Blocking Issues | None |

## Phase 4 Plans

| Plan | Waves | Goal | Status |
|------|-------|------|--------|
| [4-1-PLAN.md](4-1-PLAN.md) | 1-3 | Foundation: Policy versioning, hashing, telemetry | Ready |
| [4-2-PLAN.md](4-2-PLAN.md) | 4-5 | Replay harness, admin endpoints | Ready |
| [4-3-PLAN.md](4-3-PLAN.md) | 6 | Integration testing, PLCY verification | Ready |

### Execution Order
1. **Plan 4-1** → Foundation (can start immediately)
2. **Plan 4-2** → Replay & Admin (starts after 4-1 complete)
3. **Plan 4-3** → Verification (starts after 4-2 complete)

## Phase Progress

| Phase | Status | Started | Completed |
|-------|--------|---------|-----------|
| 1: Startup Fix | ✓ COMPLETE | 2026-03-08 | 2026-03-08 |
| 2: Confidence-Driven Control | ✓ COMPLETE | 2026-03-08 | 2026-03-08 |
| 3: CI Verification | ✓ COMPLETE | 2026-03-08 | 2026-03-08 |
| 4: Policy Hardening | ✓ COMPLETE | 2026-03-08 | 2026-03-08 |
| 5: Contextual Routing | 📝 Planning complete | 2026-03-08 | — |

## Notes

- Project is brownfield — extensive existing implementation in place
- **Phase 1: COMPLETE** — Fixed lifespan double-yield defect; all components now initialize at startup
  - All 5 pipeline components set on app.state during startup
  - Health check, search, and RAG endpoints all working correctly
- **Phase 2: COMPLETE** — Confidence-driven control loop fully implemented
  - All 4 execution paths operational (fast/standard/cautious/abstain)
  - Uncertainty gates for Standard path (score gap, weak evidence, conflict detection)
  - Confidence band thresholds: HIGH=0.85, MEDIUM=0.65, LOW=0.45
  - Abstention response with structured format
  - Telemetry instrumentation for all paths
  - Prompt variants for each confidence band
- **Phase 3: COMPLETE** — CI Verification infrastructure and tests
  - PolicyRepository methods for policy management (create_policy, set_active_policy)
  - /admin/policy/reload endpoint for dynamic policy loading
  - CI header handling (X-CI-Test-Mode, X-CI-Override-Confidence)
  - Enhanced telemetry with stage_flags and confidence_override
  - Pytest fixtures: policy_seed, make_ci_headers, routing_fixture_data
  - Comprehensive test suite: 17+ tests verifying all 4 execution paths, boundary conditions, policy reload
  - CTRL-05 (behavior verification) and CTRL-06 (policy updates) requirements satisfied
- **Phase 4: COMPLETE** — Policy Infrastructure Hardening
  - **PLCY-01**: Policy versioning with SHA-256 hashing
    - compute_policy_hash() in shared/policy.py
    - create_policy_with_hash(), activate_policy(), rollback_to_previous(), get_activation_history() in PolicyRepository
    - Admin endpoints: /admin/policy/create, /admin/policy/activate, /admin/policy/rollback, /admin/policy/history, /admin/policy/list
  - **PLCY-02**: Deterministic replay harness
    - DeterministicReplayer class in shared/replay.py
    - replay_audit() for single trace verification
    - replay_batch() for CI regression testing
    - Admin endpoints: /admin/replay/audit, /admin/replay/batch
  - **PLCY-03**: Complete telemetry instrumentation
    - Phase 4 fields in PolicyTrace: policy_hash, telemetry_schema_version, retrieval_items, retrieval_parameters
    - backfill_trace_fields() for old trace compatibility
    - validate_telemetry_health() for data quality
    - Frozen retrieval snapshots captured in _rag_hybrid()
  - E2E test suites: test_policy_versioning_e2e.py, test_replay_determinism_e2e.py, test_schema_migration_e2e.py, test_operational_scenarios.py, test_phase4_verification.py
  - CI script: scripts/test_replay_ci.py with `make test-replay` target
- **Phase 5: PLANNING COMPLETE** — Contextual Policy Routing
  - **5-CONTEXT.md**: Architectural decisions locked
    - Declarative rule-table engine with specificity > priority precedence
    - Query types: exact_fact, comparison, multi_hop, ambiguous, summarization, other
    - Evidence shape: coverage_band, agreement_band, spread_band
    - Effort budget: post-routing constraint (not rule condition)
  - **5-1-PLAN.md**: Core Rule Engine (Waves 1-2)
    - RoutingContext, RoutingRule, RoutingDecision dataclasses
    - RuleEngine with specificity/priority/ID precedence
    - Fallback to confidence-band defaults
  - **5-2-PLAN.md**: Query Classification & Evidence Shape (Waves 3-4)
    - QueryType taxonomy with 6 types
    - EvidenceShape with 3 dimensions
    - Categorical banding with configurable thresholds
  - **5-3-PLAN.md**: Integration & Budget Constraint (Waves 5-6)
    - ContextualRouterV2 integration
    - BudgetConstraint layer with safety guards
    - E2E tests for routing, budget, precedence, replay

---
*Last updated: 2026-03-08 | Phase 5 planning complete* 

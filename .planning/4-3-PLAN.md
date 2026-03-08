# Phase 4 Plan 3: Integration Testing & Verification

<plan phase="4" plan="3">
  <overview>
    <phase_name>Policy Infrastructure Hardening — Verification</phase_name>
    <goal>End-to-end testing and systematic verification of PLCY-01, PLCY-02, PLCY-03 requirements</goal>
    <requirements>PLCY-01, PLCY-02, PLCY-03</requirements>
    <waves>6 (Integration Testing)</waves>
  </overview>
  
  <dependencies>
    <complete>Plan 4-1: Foundation</complete>
    <complete>Plan 4-2: Replay & Admin</complete>
    <requires>All Waves 1-5 implementation complete</requires>
  </dependencies>
  
  <tasks>
    <task type="auto" priority="1">
      <name>Create policy versioning E2E test suite</name>
      <files>tests/test_policy_versioning_e2e.py (new)</files>
      <action>Test policy lifecycle: create → validate → activate → verify snapshot → activate second → verify history → rollback → verify prior reactivated. Test hash verification and concurrent activation conflict.</action>
      <verify>All 8 test scenarios pass; no flaky tests; CI integration confirmed</verify>
      <done>E2E test file exists, all tests pass, PLCY-01 coverage verified</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Create replay determinism E2E test suite</name>
      <files>tests/test_replay_determinism_e2e.py (new)</files>
      <action>Test replay audit (single trace success), replay audit (policy deleted → partial_replay), replay audit (divergent routing → mismatch), replay batch (10 traces aggregate), frozen retrieval prevents divergence.</action>
      <verify>All 5 test scenarios pass; determinism verified across 20+ historical traces</verify>
      <done>E2E test file exists, replay determinism proven, PLCY-02 coverage verified</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Create schema migration compatibility test</name>
      <files>tests/test_schema_migration_e2e.py (new)</files>
      <action>Test backward compatibility: load pre-Phase4 trace fixture, backfill fields, verify required fields present, replay audit returns partial_replay (not error), query by schema version distinguishes old vs new.</action>
      <verify>Old traces readable after Phase 4 deploy; no data loss; gradual migration works</verify>
      <done>Migration test passes, zero-downtime deployment verified</done>
    </task>
    
    <task type="auto" priority="2">
      <name>Create operational scenarios test suite</name>
      <files>tests/test_operational_scenarios.py (new)</files>
      <action>Test 3 scenarios: Emergency hotfix (create v2 → activate → verify), Rollback after bad policy (activate v3 → detect → rollback to v2), Audit incorrect routing (replay_audit identifies root cause).</action>
      <verify>All 3 operational scenarios pass; each scenario completes in &lt; 1s; procedures validated</verify>
      <done>Operational scenarios tested, production readiness confirmed</done>
    </task>
    
    <task type="manual" priority="2">
      <name>Create operational runbook documentation</name>
      <files>docs/OPERATIONAL_RUNBOOK_PHASE4.md (new)</files>
      <action>Document step-by-step procedures: Emergency hotfix deployment, Policy rollback procedure, Audit investigation process, Decision tree (rollback vs hotfix), Monitoring checklist using /admin/policy/status.</action>
      <verify>Review runbook with team; walk through each procedure in staging; verify clarity and completeness</verify>
      <done>Runbook created, reviewed, procedures validated in staging</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Implement PLCY verification test suite</name>
      <files>tests/test_phase4_verification.py (new)</files>
      <action>Create systematic verification: PLCY-01 (versioning, immutability, queryability, no data loss, rollback, audit trail), PLCY-02 (frozen inputs, deterministic routing, explicit failure modes, batch regression), PLCY-03 (required fields, schema versioning, backfill, forward compatibility).</action>
      <verify>All PLCY criteria have corresponding test assertions; run E2E suites; verify DB queries return expected results</verify>
      <done>All 3 PLCY requirements verified with automated tests</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Run full integration test suite</name>
      <files>tests/</files>
      <action>Execute: pytest tests/test_policy_versioning_e2e.py tests/test_replay_determinism_e2e.py tests/test_schema_migration_e2e.py tests/test_operational_scenarios.py tests/test_phase4_verification.py -v</action>
      <verify>100% pass rate; no flaky tests; all PLCY requirements covered</verify>
      <done>Full test suite passes, Phase 4 ready for completion</done>
    </task>
    
    <task type="manual" priority="1">
      <name>Final verification checklist</name>
      <files>.planning/STATE.md, .planning/ROADMAP.md</files>
      <action>Verify: Policy hashes on all policies, activation history queryable, replay produces same decisions across 20+ traces, every /rag request has telemetry with required fields, schema versions distinguish old/new traces, CI regression test passes.</action>
      <verify>Query DB: SELECT COUNT(DISTINCT policy_hash) FROM policy_telemetry &gt; 0; SELECT * FROM policy_activations shows audit trail; POST /admin/replay/batch returns all passed</verify>
      <done>All success criteria met, Phase 4 marked complete in STATE.md and ROADMAP.md</done>
    </task>
  </tasks>
</plan>

# Phase 4 Plan 2: Replay Harness & Admin Infrastructure

<plan phase="4" plan="2">
  <overview>
    <phase_name>Policy Infrastructure Hardening — Replay & Admin</phase_name>
    <goal>Build deterministic replay harness and admin endpoints for policy management and audit</goal>
    <requirements>PLCY-02</requirements>
    <waves>4-5 (Replay Harness, Admin Endpoints)</waves>
  </overview>
  
  <dependencies>
    <complete>Plan 4-1: Foundation</complete>
    <requires>Policy hashing, PolicyRepository versioning methods, PolicyTrace Phase 4 fields</requires>
  </dependencies>
  
  <tasks>
    <task type="auto" priority="1">
      <name>Create DeterministicReplayer class</name>
      <files>shared/replay.py (new)</files>
      <action>Implement DeterministicReplayer with replay_audit(trace_id) -> Dict and replay_batch(limit=50) -> Dict methods. Reconstruct routing from frozen inputs, compare to stored decision, return explicit status (success|partial_replay|mismatch|policy_deleted|not_found).</action>
      <verify>Test audit success case, partial_replay (missing hash), mismatch (divergent routing), policy_deleted scenarios</verify>
      <done>Replayer class exists, all 5 failure modes handled, tests pass</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Add policy management admin endpoints</name>
      <files>api/app.py</files>
      <action>Create POST /admin/policy/create (version, content), POST /admin/policy/activate (version, reason), POST /admin/policy/rollback, GET /admin/policy/history (limit), GET /admin/policy/list. All require X-API-Key.</action>
      <verify>Test each endpoint with auth (success) and without (401); verify activation history updated</verify>
      <done>5 endpoints implemented, auth enforced, integration tests pass</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Add replay audit endpoint</name>
      <files>api/app.py</files>
      <action>Create POST /admin/replay/audit?trace_id=&lt;id&gt; endpoint. Requires API key. Calls replayer.replay_audit(), returns JSON with status, original_decision, reconstructed_decision, reason, trace_timestamp.</action>
      <verify>Audit valid trace returns 200 with success/partial status; invalid trace_id returns 404</verify>
      <done>Endpoint works, response structure correct, error handling verified</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Add replay batch endpoint for CI</name>
      <files>api/app.py</files>
      <action>Create POST /admin/replay/batch?limit=&lt;N&gt; endpoint. Requires API key. Calls replayer.replay_batch(), returns JSON with mode, total_replayed, passed, failed, partial, failures list. Returns 400 if any failed.</action>
      <verify>Batch replay 10 traces, verify counts and failure list; verify 400 returned if failures present</verify>
      <done>Batch endpoint works, aggregation correct, CI fail-on-error behavior verified</done>
    </task>
    
    <task type="auto" priority="2">
      <name>Add policy status endpoint</name>
      <files>api/app.py, shared/database.py</files>
      <action>Create GET /admin/policy/status endpoint (no auth required). Returns active_policy_version, active_policy_hash, policy_count, recent telemetry stats, trace schema versions, last_activation, activation_history_count.</action>
      <verify>Query endpoint, verify response structure and counts increase after telemetry logged</verify>
      <done>Status endpoint returns complete operational visibility data</done>
    </task>
    
    <task type="auto" priority="2">
      <name>Implement telemetry validation function</name>
      <files>shared/telemetry.py</files>
      <action>Add validate_telemetry_health(trace: Dict) -> Tuple[bool, List[str]] function. Checks required fields present, confidence_band valid, routing_action present, policy_version present.</action>
      <verify>Valid trace passes; invalid trace fails with specific error messages; test all field combinations</verify>
      <done>Validation function catches data quality issues, tests pass</done>
    </task>
    
    <task type="auto" priority="2">
      <name>Create CI replay regression test script</name>
      <files>scripts/test_replay_ci.py (new)</files>
      <action>Create script that calls POST /admin/replay/batch?limit=50, parses response, fails if failed &gt; 0, logs results. Add make test-replay target to Makefile.</action>
      <verify>Run script locally, verify it calls endpoint and interprets response correctly; verify Makefile target works</verify>
      <done>CI script exists, Makefile target added, manual test passed</done>
    </task>
  </tasks>
</plan>

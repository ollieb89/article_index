# Phase 4 Plan 1: Foundation — Policy Versioning & Telemetry

<plan phase="4" plan="1">
  <overview>
    <phase_name>Policy Infrastructure Hardening — Foundation</phase_name>
    <goal>Implement immutable policy versioning with SHA-256 hashing and complete telemetry instrumentation</goal>
    <requirements>PLCY-01, PLCY-03</requirements>
    <waves>1-3 (Schema, Versioning, Telemetry)</waves>
  </overview>
  
  <dependencies>
    <complete>Phase 3: CI Verification</complete>
    <complete>Wave 1 Schema: Migration 007_phase4_hardening.sql</complete>
    <note>Schema migration adds policy_hash, policy_activations table, telemetry schema_version</note>
  </dependencies>
  
  <tasks>
    <task type="auto" priority="1">
      <name>Fix dead code in PolicyRepository</name>
      <files>shared/database.py</files>
      <action>Remove unreachable return statement at line 529: "return str(result['query_id'])". Verify no other dead code in PolicyRepository class.</action>
      <verify>grep -n "return str(result" shared/database.py returns no results; Python syntax check passes</verify>
      <done>Dead code removed, file passes flake8/pylint</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Implement policy hashing function</name>
      <files>shared/policy.py</files>
      <action>Add compute_policy_hash(content: Dict) -> str function using hashlib.sha256 on canonical JSON (sort_keys=True, separators=(',', ':')). Returns "sha256:&lt;hexdigest&gt;".</action>
      <verify>Unit test: same content produces same hash, different content produces different hash, format is sha256: prefix</verify>
      <done>Function exists, tests pass, deterministic hashing verified</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Extend PolicyRepository with versioning methods</name>
      <files>shared/database.py</files>
      <action>Add create_policy() with hash computation, activate_policy() with SERIALIZABLE transaction and activation history, rollback_to_previous(), get_activation_history(). All methods return detailed status tuples.</action>
      <verify>Test: create policy → activate → verify history entry → rollback → verify prior reactivated</verify>
      <done>All 4 methods implemented, transactional safety verified</done>
    </task>
    
    <task type="auto" priority="2">
      <name>Implement policy schema validation</name>
      <files>shared/policy.py</files>
      <action>Add validate_policy_schema(content: Dict) -> List[str] function. Validates required sections (thresholds, routing_rules), threshold ranges (0-1), routing map completeness.</action>
      <verify>Test invalid policies return errors; valid policies return empty list; called in create_policy()</verify>
      <done>Validation function exists, integrated with create_policy, tests pass</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Extend PolicyTrace with Phase 4 fields</name>
      <files>shared/telemetry.py</files>
      <action>Add policy_hash, telemetry_schema_version="1.0", retrieval_items, retrieval_parameters fields to PolicyTrace dataclass. Update to_dict() to include all new fields.</action>
      <verify>Create trace, call to_dict(), verify all Phase 4 fields present in output</verify>
      <done>PolicyTrace updated, backward compatibility preserved, tests pass</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Implement frozen retrieval snapshot capture</name>
      <files>api/app.py</files>
      <action>After hybrid_retriever.retrieve(), create retrieval_snapshot dict with items (id, rank, score, source_doc, chunk_index), parameters (limit, threshold), total_candidates. Add to trace before logging.</action>
      <verify>Query /rag, inspect telemetry DB, verify retrieval_items populated with correct structure and rank order</verify>
      <done>Frozen snapshots captured on every request, structure validated</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Populate policy_hash and schema version in telemetry logging</name>
      <files>shared/database.py</files>
      <action>Update PolicyRepository.log_telemetry() to accept/insert policy_hash and telemetry_schema_version. Read active policy hash or accept as parameter. Handle NULL for legacy traces.</action>
      <verify>Log telemetry, query DB, verify policy_hash and telemetry_schema_version populated correctly</verify>
      <done>All new traces include hash and schema version, legacy handling works</done>
    </task>
    
    <task type="auto" priority="2">
      <name>Implement telemetry backfill logic</name>
      <files>shared/telemetry.py</files>
      <action>Add backfill_trace_fields(trace: Dict, source_version="0.9") -> Dict function. Derives retrieval_state from confidence_band, stage_flags from execution_path, sets schema version if missing.</action>
      <verify>Load pre-Phase4 trace, backfill, verify all required fields present; new trace backfilled idempotently</verify>
      <done>Backfill function works for old traces, idempotent for new traces</done>
    </task>
  </tasks>
</plan>

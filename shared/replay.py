"""Deterministic replay harness for policy audit and regression testing.

This module provides the DeterministicReplayer class for recreating routing
decisions from stored telemetry traces and verifying policy determinism.
"""

import logging
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class ReplayStatus(str, Enum):
    """Status codes for replay audit results."""
    SUCCESS = "success"
    PARTIAL_REPLAY = "partial_replay"
    MISMATCH = "mismatch"
    POLICY_DELETED = "policy_deleted"
    NOT_FOUND = "not_found"


@dataclass
class ReplayResult:
    """Result of a single replay audit."""
    status: ReplayStatus
    trace_id: str
    original_decision: Dict[str, Any]
    reconstructed_decision: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None
    trace_timestamp: Optional[str] = None


class DeterministicReplayer:
    """Reconstruct routing decisions from stored telemetry traces.
    
    Provides deterministic replay capabilities for:
    - Audit: Verify a single trace produces the same routing decision
    - Batch regression: Test multiple traces for policy stability
    """
    
    def __init__(self, policy_repo):
        """Initialize replayer with policy repository.
        
        Args:
            policy_repo: PolicyRepository instance for policy lookups
        """
        self.policy_repo = policy_repo
    
    async def replay_audit(self, trace_id: str) -> Dict[str, Any]:
        """Replay a single trace and compare routing decisions.
        
        Reconstructs the routing decision from frozen inputs stored in the
        trace and compares to the recorded decision.
        
        Args:
            trace_id: UUID of the telemetry trace to replay
            
        Returns:
            Dict with status, original_decision, reconstructed_decision, reason
        """
        # Fetch trace from database
        trace = await self.policy_repo.get_telemetry_by_id(trace_id)
        if not trace:
            return {
                "status": ReplayStatus.NOT_FOUND,
                "trace_id": trace_id,
                "original_decision": None,
                "reconstructed_decision": None,
                "reason": f"Trace {trace_id} not found",
                "trace_timestamp": None
            }
        
        # Get policy hash from trace
        policy_hash = trace.get('policy_hash')
        policy_version = trace.get('policy_version', 'unknown')
        
        # Check if policy exists
        if policy_hash:
            policy = await self.policy_repo.get_policy_by_hash(policy_hash)
            if not policy:
                # Policy was deleted - partial replay only
                return {
                    "status": ReplayStatus.POLICY_DELETED,
                    "trace_id": trace_id,
                    "original_decision": {
                        "action_taken": trace.get('action_taken'),
                        "execution_path": trace.get('execution_path'),
                        "confidence_band": trace.get('confidence_band')
                    },
                    "reconstructed_decision": None,
                    "reason": f"Policy with hash {policy_hash} no longer exists",
                    "trace_timestamp": trace.get('created_at')
                }
        else:
            # Try to find by version
            policies = await self.policy_repo.list_policies(limit=100)
            policy = None
            for p in policies:
                if p['version'] == policy_version:
                    policy = p
                    break
            
            if not policy:
                return {
                    "status": ReplayStatus.POLICY_DELETED,
                    "trace_id": trace_id,
                    "original_decision": {
                        "action_taken": trace.get('action_taken'),
                        "execution_path": trace.get('execution_path'),
                        "confidence_band": trace.get('confidence_band')
                    },
                    "reconstructed_decision": None,
                    "reason": f"Policy version {policy_version} not found",
                    "trace_timestamp": trace.get('created_at')
                }
        
        # Reconstruct routing decision from frozen inputs
        reconstructed = self._reconstruct_routing(trace, policy)
        
        # Compare with original
        original = {
            "action_taken": trace.get('action_taken'),
            "execution_path": trace.get('execution_path'),
            "confidence_band": trace.get('confidence_band'),
            "retrieval_state": trace.get('retrieval_state')
        }
        
        # Check for mismatch
        mismatches = []
        for key in ['action_taken', 'execution_path', 'confidence_band']:
            if original.get(key) != reconstructed.get(key):
                mismatches.append(f"{key}: {original.get(key)} != {reconstructed.get(key)}")
        
        if mismatches:
            return {
                "status": ReplayStatus.MISMATCH,
                "trace_id": trace_id,
                "original_decision": original,
                "reconstructed_decision": reconstructed,
                "reason": f"Routing divergence detected: {'; '.join(mismatches)}",
                "trace_timestamp": trace.get('created_at')
            }
        
        return {
            "status": ReplayStatus.SUCCESS,
            "trace_id": trace_id,
            "original_decision": original,
            "reconstructed_decision": reconstructed,
            "reason": "Routing decisions match",
            "trace_timestamp": trace.get('created_at')
        }
    
    def _reconstruct_routing(self, trace: Dict, policy: Dict) -> Dict[str, Any]:
        """Reconstruct routing decision from trace and policy.
        
        Args:
            trace: Telemetry trace dict
            policy: Policy dict
            
        Returns:
            Reconstructed routing decision
        """
        # Get thresholds from policy
        thresholds = policy.get('thresholds', {})
        confidence_score = trace.get('confidence_score', 0.0)
        
        # Determine confidence band from score
        high_min = thresholds.get('high_min', 0.85)
        medium_min = thresholds.get('medium_min', 0.60)
        low_min = thresholds.get('low_min', 0.35)
        
        if confidence_score >= high_min:
            confidence_band = "high"
        elif confidence_score >= medium_min:
            confidence_band = "medium"
        elif confidence_score >= low_min:
            confidence_band = "low"
        else:
            confidence_band = "insufficient"
        
        # Get routing rules
        routing_rules = policy.get('routing_rules', {})
        query_type = trace.get('query_type', 'general')
        
        # Determine action based on routing rules or defaults
        query_rules = routing_rules.get('query_types', {}).get(query_type, {})
        if confidence_band in query_rules:
            action = query_rules[confidence_band]
        else:
            # Default behavior
            action = {
                'high': 'standard',
                'medium': 'expanded_retrieval',
                'low': 'conservative_prompt',
                'insufficient': 'abstain'
            }.get(confidence_band, 'standard')
        
        # Determine execution path
        execution_path = {
            'high': 'fast',
            'medium': 'standard',
            'low': 'cautious',
            'insufficient': 'abstain'
        }.get(confidence_band, 'standard')
        
        return {
            "action_taken": action,
            "execution_path": execution_path,
            "confidence_band": confidence_band,
            "retrieval_state": trace.get('retrieval_state', 'unknown')
        }
    
    async def replay_batch(self, limit: int = 50) -> Dict[str, Any]:
        """Run batch replay on recent traces for regression testing.
        
        Args:
            limit: Maximum number of traces to replay
            
        Returns:
            Dict with aggregate results: mode, total_replayed, passed, failed, partial
        """
        # Get recent telemetry
        traces = await self.policy_repo.get_recent_telemetry(limit=limit)
        
        if not traces:
            return {
                "mode": "batch",
                "total_replayed": 0,
                "passed": 0,
                "failed": 0,
                "partial": 0,
                "failures": [],
                "message": "No traces found for replay"
            }
        
        results = {
            "mode": "batch",
            "total_replayed": len(traces),
            "passed": 0,
            "failed": 0,
            "partial": 0,
            "failures": []
        }
        
        for trace in traces:
            trace_id = trace.get('query_id')
            if not trace_id:
                continue
                
            try:
                audit_result = await self.replay_audit(trace_id)
                status = audit_result.get('status')
                
                if status == ReplayStatus.SUCCESS:
                    results["passed"] += 1
                elif status == ReplayStatus.PARTIAL_REPLAY:
                    results["partial"] += 1
                else:
                    results["failed"] += 1
                    results["failures"].append({
                        "trace_id": trace_id,
                        "status": status,
                        "reason": audit_result.get('reason')
                    })
            except Exception as e:
                logger.error(f"Replay failed for trace {trace_id}: {e}")
                results["failed"] += 1
                results["failures"].append({
                    "trace_id": trace_id,
                    "status": "error",
                    "reason": str(e)
                })
        
        return results

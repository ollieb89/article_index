"""Policy management for adaptive RAG operations.

This module defines the RAGPolicy structure and the PolicyRegistry for
managing versioned control parameters like confidence thresholds and
routing rules.
"""

import hashlib
import json
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from shared.evidence_scorer import ConfidenceBand

logger = logging.getLogger(__name__)


def compute_policy_hash(content: Dict) -> str:
    """Compute SHA-256 hash of policy content for immutability verification.
    
    Uses canonical JSON format (sorted keys, tight spacing) for determinism.
    
    Args:
        content: Policy content dictionary
        
    Returns:
        Hash string in format "sha256:<hexdigest>"
    """
    canonical = json.dumps(content, sort_keys=True, separators=(',', ':'))
    hexdigest = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    return f"sha256:{hexdigest}"


def validate_policy_schema(content: Dict) -> List[str]:
    """Validate policy schema completeness and correctness.
    
    Args:
        content: Policy content dictionary
        
    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []
    
    # Check required sections
    if 'thresholds' not in content:
        errors.append("Missing required section: thresholds")
    else:
        thresholds = content['thresholds']
        # Validate threshold ranges (0-1)
        for band, value in thresholds.items():
            if not isinstance(value, (int, float)):
                errors.append(f"Threshold '{band}' must be a number")
            elif value < 0 or value > 1:
                errors.append(f"Threshold '{band}' must be between 0 and 1, got {value}")
    
    if 'routing_rules' not in content:
        errors.append("Missing required section: routing_rules")
    
    # Check routing map completeness (should have entries for all confidence bands)
    if 'routing_rules' in content:
        routing_rules = content['routing_rules']
        if 'query_types' not in routing_rules:
            errors.append("Missing routing_rules.query_types")
    
    return errors

@dataclass
class RAGPolicy:
    """Control parameters for RAG behavior."""
    version: str
    thresholds: Dict[str, float] = field(default_factory=lambda: {
        "high": 0.75,
        "medium": 0.50,
        "low": 0.25,
        "insufficient": 0.0
    })
    routing_rules: Dict[str, Any] = field(default_factory=lambda: {
        "default": "standard",
        "query_types": {}
    })
    contextual_thresholds: Dict[str, Dict[str, float]] = field(default_factory=dict)
    latency_budgets: Dict[str, int] = field(default_factory=lambda: {
        "general": 2000,
        "exact_fact": 1000,
        "ambiguous": 3000,
        "summarization": 4000
    })
    
    def get_threshold(self, band: str, query_type: str = "general") -> float:
        """Get threshold for a specific band, with contextual override."""
        band = band.lower()
        # 1. Check contextual override
        type_thresholds = self.contextual_thresholds.get(query_type, {})
        if band in type_thresholds:
            return type_thresholds[band]
            
        # 2. Fallback to global threshold
        return self.thresholds.get(band, 0.0)

    def get_latency_budget(self, query_type: str = "general") -> int:
        """Get latency budget for a specific query type."""
        return self.latency_budgets.get(query_type, self.latency_budgets.get("general", 2000))

    def get_action(self, band: str, query_type: str = "general") -> str:
        """Determine the action to take based on band and query type."""
        band = band.lower()
        query_rules = self.routing_rules.get("query_types", {}).get(query_type, {})
        
        # Check for specific query_type + band override
        if band in query_rules:
            return query_rules[band]
            
        # Default behavior (Phase 11 baseline)
        if band == "high":
            return "standard"
        elif band == "medium":
            return "expanded_retrieval"
        elif band == "low":
            return "conservative_prompt"
        else:
            return "abstain"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for storage/API."""
        return {
            "version": self.version,
            "thresholds": self.thresholds,
            "routing_rules": self.routing_rules,
            "contextual_thresholds": self.contextual_thresholds,
            "latency_budgets": self.latency_budgets
        }

    @classmethod
    def from_db_row(cls, row: Dict[str, Any]) -> 'RAGPolicy':
        """Create policy from database row."""
        return cls(
            version=row['version'],
            thresholds=row.get('thresholds', {}),
            routing_rules=row.get('routing_rules', {}),
            contextual_thresholds=row.get('contextual_thresholds', {}),
            latency_budgets=row.get('latency_budgets', {})
        )

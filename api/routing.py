import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass
from shared.policy import RAGPolicy
from .query_classifier import QueryType
from .retrieval_state import RetrievalState

logger = logging.getLogger(__name__)

@dataclass
class RoutingContext:
    query_type: QueryType
    confidence_band: str
    retrieval_state: RetrievalState
    latency_budget: int
    policy: RAGPolicy

@dataclass
class RouteDecision:
    action: str
    execution_path: str
    reason: str

class ContextualRouter:
    """Routes RAG actions based on contextual signals."""

    def route(self, context: RoutingContext) -> RouteDecision:
        """Determine the best action for the given context.
        
        Args:
            context: The RoutingContext containing query and evidence signals.
            
        Returns:
            A RouteDecision with the chosen action and execution path.
        """
        qtype = context.query_type
        band = context.confidence_band
        state = context.retrieval_state
        policy = context.policy
        
        # 1. Check Policy-defined routing rules (Milestone 5 integration)
        action = policy.get_action(band, qtype)
        
        # 2. Contextual overrides / Sanity checks
        
        # Conflicted evidence ALWAYS conservative or abstain
        if state == RetrievalState.CONFLICTED:
            if band == "high":
                return RouteDecision("conservative_prompt", "conflicted_safe_path", "Evidence is conflicted despite high score")
            return RouteDecision("abstain", "conflicted_abstention", "Evidence is conflicted")
            
        # Exact facts require higher certainty
        if qtype == QueryType.EXACT_FACT:
            if state == RetrievalState.FRAGILE:
                return RouteDecision("expanded_retrieval", "fact_check_expansion", "Fragile evidence for exact fact")
            if band == "low":
                return RouteDecision("abstain", "fact_abstention", "Low confidence for exact fact")
                
        # Summarization can work with lower confidence but needs coverage
        if qtype == QueryType.SUMMARIZATION:
            if state == RetrievalState.RECOVERABLE and band == "low":
                return RouteDecision("standard", "summarization_relaxed_path", "Summarization allows lower confidence if recoverable")
                
        # Default to policy action with determined path
        return RouteDecision(action, f"contextual_{action}", f"Policy action for {qtype}/{band}/{state}")

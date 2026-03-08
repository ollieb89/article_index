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

    async def route_with_confidence(
        self,
        context: RoutingContext,
        chunks: Optional[Any] = None,
        evidence_shape: Optional[Any] = None,
        uncertainty_detector: Optional[Any] = None
    ) -> RouteDecision:
        """
        Route based on confidence band with Standard-path uncertainty gates (Phase 2).
        
        Phase 2 routing model:
        - High confidence (>= 0.85) → Fast path (skip reranking/expansion)
        - Medium confidence (0.65-0.84) → Standard path (conditional reranking/expansion)
        - Low confidence (0.45-0.64) → Cautious path (mandatory reranking/expansion)
        - Insufficient (< 0.45) → Abstain path (no retrieval)
        
        Args:
            context: RoutingContext with query_type, confidence_band, retrieval_state, policy
            chunks: Retrieved chunks (needed for uncertainty gates)
            evidence_shape: Pre-extracted EvidenceShape
            uncertainty_detector: UncertaintyDetector instance
            
        Returns:
            RouteDecision with execution_path set to "fast" / "standard" / "cautious" / "abstain"
        """
        band = context.confidence_band
        logger.info(f"Phase 2 routing with confidence band: {band}")
        
        # Insufficient → Abstain immediately
        if band == "insufficient":
            return RouteDecision(
                action="abstain",
                execution_path="abstain",
                reason="Insufficient confidence to answer"
            )
        
        # Low Confidence → Cautious Path (mandatory reranking)
        if band == "low":
            logger.debug("Low confidence → routing to CAUTIOUS path (mandatory reranking)")
            return RouteDecision(
                action="expanded_retrieval_and_reranking",
                execution_path="cautious",
                reason="Low confidence requires expanded retrieval and reranking"
            )
        
        # High Confidence → Fast Path (skip reranking/expansion)
        if band == "high":
            logger.debug("High confidence → routing to FAST path (base retrieval only)")
            return RouteDecision(
                action="direct_generation",
                execution_path="fast",
                reason="High confidence allows direct generation from base retrieval"
            )
        
        # Medium Confidence → Standard Path with Uncertainty Gates
        if band == "medium":
            logger.debug("Medium confidence → checking STANDARD path uncertainty gates")
            
            # Initialize uncertainty detector if not provided
            if uncertainty_detector is None:
                from api.uncertainty_gates import UncertaintyDetector
                uncertainty_detector = UncertaintyDetector()
            
            # Check gates only if we have chunks
            is_uncertain = False
            gate_triggered = None
            
            if chunks:
                is_uncertain, gate_triggered = uncertainty_detector.detect_uncertainty(
                    chunks, evidence_shape
                )
            
            if is_uncertain:
                logger.info(f"Standard path uncertainty detected: {gate_triggered}")
                return RouteDecision(
                    action="conditional_reranking",
                    execution_path="standard",
                    reason=f"Standard path: uncertainty gate triggered ({gate_triggered})"
                )
            else:
                logger.debug("Standard path: all uncertainty gates passed, using base evidence")
                return RouteDecision(
                    action="direct_generation",
                    execution_path="standard",
                    reason="Standard path: uncertainty gates passed, base evidence sufficient"
                )
        
        # Fallback (shouldn't happen)
        logger.warning(f"Unrecognized confidence band: {band}, defaulting to standard")
        return RouteDecision(
            action="direct_generation",
            execution_path="standard",
            reason="Confidence band not recognized, using standard path"
        )

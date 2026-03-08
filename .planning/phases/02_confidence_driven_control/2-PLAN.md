# Phase 2 Plan: Confidence-Driven Control Loop

**Created:** 2026-03-08  
**Decisions Locked:** 2-CONTEXT.md  
**Research Complete:** 2-RESEARCH.md  
**Status:** Ready for execution  

---

## Overview

Phase 2 converts the RAG pipeline from static execution to **confidence-driven policy routing**, implementing four distinct execution paths (Fast / Standard / Cautious / Abstain) based on calibrated confidence bands.

**Inputs:**
- Existing hybrid RAG pipeline (Phase 1 startup fix)
- ContextualRouter framework
- EvidenceScorer with component metrics
- QueryTransformer for query expansion
- Reranker for answer enhancement

**Outputs:**
- Configurable confidence thresholds (0.85 / 0.65 / 0.45)
- Uncertainty detection gates for Standard path
- Four execution paths with distinct behavior
- Updated abstention response format
- Phase 2 telemetry instrumentation
- Prompt templates for each band

---

## Task Breakdown

### Wave 1: Confidence Band Configuration (Finalize Scoring)

**Goal:** Update confidence score boundaries to Phase 2 spec and make configurable.

#### Task 1.1: Update Confidence Band Thresholds

**File:** `shared/evidence_scorer.py`

**Current state:**
- `high_confidence_threshold = 0.75`
- `medium_threshold = 0.50`
- `min_confidence_threshold = 0.25`

**What to do:**
1. Add environment variable loader at module level:
   ```python
   import os
   
   CONFIDENCE_HIGH = float(os.getenv("CONFIDENCE_HIGH", "0.85"))
   CONFIDENCE_MEDIUM = float(os.getenv("CONFIDENCE_MEDIUM", "0.65"))
   CONFIDENCE_LOW = float(os.getenv("CONFIDENCE_LOW", "0.45"))
   ```

2. Update `EvidenceScorer.__init__()` to use configurable thresholds:
   ```python
   def __init__(
       self,
       high_confidence_threshold: float = None,  # Will use env or default
       min_confidence_threshold: float = None,
   ):
       self.high_confidence_threshold = high_confidence_threshold or CONFIDENCE_HIGH
       self.min_confidence_threshold = min_confidence_threshold or CONFIDENCE_LOW
   ```

3. Add `.env.example` entries:
   ```
   CONFIDENCE_HIGH=0.85
   CONFIDENCE_MEDIUM=0.65
   CONFIDENCE_LOW=0.45
   ```

4. Log configuration at startup:
   ```python
   logger.info(f"Confidence thresholds: HIGH={self.high_confidence_threshold}, "
               f"MEDIUM={medium_threshold}, LOW={self.min_confidence_threshold}")
   ```

**Verification:**
- `score_evidence()` returns band="high" for scores >= 0.85
- `score_evidence()` returns band="medium" for 0.65 <= score < 0.85
- `score_evidence()` returns band="low" for 0.45 <= score < 0.65
- `score_evidence()` returns band="insufficient" for score < 0.45
- Environment variables override defaults

---

### Wave 2: Uncertainty Detection Gates (Standard Path Logic)

**Goal:** Implement numeric gates that determine whether Standard path should invoke reranker.

#### Task 2.1: Create UncertaintyDetector Class

**File:** `api/retrieval_state.py` (or new `api/uncertainty_gates.py`)

**Implementation:**

```python
class UncertaintyDetector:
    """Detect uncertainty in Standard-path evidence using numeric gates."""
    
    def __init__(
        self,
        score_gap_threshold: float = None,
        min_top_strength: float = None,
    ):
        """Initialize uncertainty detector with configurable gates.
        
        Args:
            score_gap_threshold: If top1 - top2 score gap < this, evidence is uncertain
                Default: 0.15 (configurable via env UNCERTAINTY_SCORE_GAP_THRESHOLD)
            min_top_strength: If top-1 evidence score < this, evidence is weak
                Default: 0.6 (configurable via env UNCERTAINTY_MIN_TOP_STRENGTH)
        """
        self.score_gap_threshold = (
            score_gap_threshold or 
            float(os.getenv("UNCERTAINTY_SCORE_GAP_THRESHOLD", "0.15"))
        )
        self.min_top_strength = (
            min_top_strength or
            float(os.getenv("UNCERTAINTY_MIN_TOP_STRENGTH", "0.6"))
        )
        logger.info(
            f"UncertaintyDetector configured: "
            f"score_gap_threshold={self.score_gap_threshold}, "
            f"min_top_strength={self.min_top_strength}"
        )
    
    def detect_uncertainty(
        self, 
        chunks: List[Dict[str, Any]],
        evidence_shape: Optional[EvidenceShape] = None
    ) -> Tuple[bool, Optional[str]]:
        """Detect if evidence is uncertain using numeric gates.
        
        Args:
            chunks: Retrieved chunks with scores
            evidence_shape: Pre-extracted EvidenceShape (optional, will extract if None)
            
        Returns:
            Tuple of (is_uncertain, gate_that_triggered)
            - is_uncertain: True if any gate triggers
            - gate_that_triggered: String indicating which gate (e.g., "score_gap", 
                                   "weak_evidence", "conflict") or None if no gates trigger
        """
        if not chunks or len(chunks) < 2:
            return False, None
        
        # Extract or use provided evidence shape
        if evidence_shape is None:
            from .evidence_shape import EvidenceShapeExtractor
            extractor = EvidenceShapeExtractor()
            evidence_shape = extractor.extract(chunks, "")
        
        # Gate 1: Score gap between top-1 and top-2
        score_gap = evidence_shape.score_gap
        if score_gap < self.score_gap_threshold:
            logger.debug(f"Uncertainty gate triggered: score_gap={score_gap} < {self.score_gap_threshold}")
            return True, "score_gap"
        
        # Gate 2: Top evidence strength
        top_strength = evidence_shape.top1_score
        if top_strength < self.min_top_strength:
            logger.debug(f"Uncertainty gate triggered: top_strength={top_strength} < {self.min_top_strength}")
            return True, "weak_evidence"
        
        # Gate 3: Conflict detection
        if evidence_shape.contradiction_flag:
            logger.debug("Uncertainty gate triggered: contradictory passages detected")
            return True, "conflict"
        
        return False, None
```

**Verification:**
- Loads configuration from environment or defaults (0.15, 0.6)
- Returns (True, "score_gap") when score gap is below threshold
- Returns (True, "weak_evidence") when top score is below threshold
- Returns (True, "conflict") when contradiction_flag is True
- Returns (False, None) when all gates pass
- Logs which gate triggered for debugging

---

### Wave 3: Execution Path Routing (Band → Path Mapping)

**Goal:** Map confidence bands to execution paths and integrate uncertainty gates.

#### Task 3.1: Create ConfidenceRoutingPolicy

**File:** `api/routing.py` (extend existing ContextualRouter)

**Implementation:**

Add method to ContextualRouter after existing `route()` method:

```python
async def route_with_confidence(
    self,
    context: RoutingContext,
    chunks: List[Dict[str, Any]],
    evidence_shape: Optional[EvidenceShape] = None,
    uncertainty_detector: Optional['UncertaintyDetector'] = None
) -> RouteDecision:
    """
    Route based on confidence band with Standard-path uncertainty gates.
    
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
    logger.info(f"Routing with confidence band: {band}")
    
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
        action="standard",
        execution_path="standard",
        reason="Confidence band not recognized, using standard path"
    )
```

**Verification:**
- High confidence (>= 0.85) → fast / direct_generation
- Medium confidence without uncertainty gates → standard / direct_generation
- Medium confidence with uncertainty gates → standard / conditional_reranking
- Low confidence (0.45-0.64) → cautious / expanded_retrieval_and_reranking
- Insufficient (< 0.45) → abstain / abstain

---

### Wave 4: Telemetry Enhancement (Policy Trace Fields)

**Goal:** Add Phase 2-specific fields to PolicyTrace for observability.

#### Task 4.1: Extend PolicyTrace with Phase 2 Fields

**File:** `shared/telemetry.py`

**What to do:**

Add Phase 2 fields to PolicyTrace dataclass:

```python
@dataclass
class PolicyTrace:
    # ... existing fields ...
    
    # Phase 2: Confidence-driven control
    retrieval_depth: int = 0  # Number of candidates retrieved before ranking
    reranker_invoked: bool = False  # Whether reranker was called
    reranker_reason: Optional[str] = None  # Why: "score_gap", "weak_evidence", "conflict", etc.
    
    # Token accounting
    tokens_generated: int = 0  # Tokens in final answer
    tokens_total: int = 0  # All tokens (retrieval + generation context + answer)
    
    # Abstention tracking
    abstention_triggered: bool = False  # True if query returned abstention response
```

Update `to_dict()` to include new fields:

```python
def to_dict(self) -> Dict[str, Any]:
    """Convert to dict for JSON serialization."""
    d = {
        # ... existing fields ...
        "retrieval_depth": self.retrieval_depth,
        "reranker_invoked": self.reranker_invoked,
        "reranker_reason": self.reranker_reason,
        "tokens_generated": self.tokens_generated,
        "tokens_total": self.tokens_total,
        "abstention_triggered": self.abstention_triggered,
    }
    return d
```

**Verification:**
- PolicyTrace has all new Phase 2 fields
- Fields populated correctly in pipeline
- to_dict() includes all fields

---

### Wave 5: Pipeline Integration (Orchestrate Routing + Paths)

**Goal:** Wire confidence routing into _rag_hybrid() function with execution path logic.

#### Task 5.1: Update _rag_hybrid() to Use Phase 2 Routing

**File:** `api/app.py` (in `_rag_hybrid()` function)

**Current flow around line 1070-1160:**
```python
# Get routing from policy
confidence = evidence_scorer.score_evidence(chunks, ...)
band = confidence.band
route = router.route(routing_ctx) if router else None
action = route.action if route else policy.get_action(band, qtype_str)
execution_path = route.execution_path if route else "standard"

# Execute actions
if action == "expanded_retrieval" or action == "query_transformation":
    # Do expansion
elif action == "rerank_only" or action == "reranking":
    # Do reranking
```

**What to do:**

1. Import new modules at top of app.py:
   ```python
   from api.uncertainty_gates import UncertaintyDetector
   ```

2. Initialize detector in lifespan:
   ```python
   @app.lifespan
   async def lifespan(app: FastAPI):
       async with asynccontextmanager(...):
           # ... existing code ...
           
           # Phase 2: Uncertainty detector for confidence routing
           app.state.uncertainty_detector = UncertaintyDetector()
           
           yield
           # ... cleanup ...
   ```

3. Replace routing logic in `_rag_hybrid()` around line 1080:
   ```python
   # Stage 3: Confidence Routing (Phase 2)
   
   # Extract evidence metrics
   shape_extractor = getattr(request.app.state, 'evidence_shape_extractor', None)
   state_labeler = getattr(request.app.state, 'retrieval_state_labeler', None)
   router = getattr(request.app.state, 'contextual_router', None)
   uncertainty_detector = getattr(request.app.state, 'uncertainty_detector', None)
   
   evidence_shape = shape_extractor.extract(chunks, query.question) if shape_extractor else None
   retrieval_state = state_labeler.label(evidence_shape) if state_labeler else RetrievalState.RECOVERABLE
   
   # Get confidence score
   confidence = evidence_scorer.score_evidence(
       chunks,
       query.question,
       query_type=qtype_str,
       policy=policy
   )
   band = confidence.band
   
   # Phase 2: Confidence-driven routing with uncertainty gates
   routing_ctx = RoutingContext(
       query_type=qtype_str,
       confidence_band=band,
       retrieval_state=retrieval_state,
       latency_budget=policy.get_latency_budget(qtype_str),
       policy=policy
   )
   
   route = None
   if router and hasattr(router, 'route_with_confidence'):
       # Use new Phase 2 routing with uncertainty gates
       route = await router.route_with_confidence(
           routing_ctx,
           chunks,
           evidence_shape,
           uncertainty_detector
       )
   elif router:
       # Fallback to existing router
       route = router.route(routing_ctx)
   
   action = route.action if route else "direct_generation"
   execution_path = route.execution_path if route else "standard"
   
   logger.info(f"Phase 2 routing: {band} → {execution_path} ({action})")
   
   trace.confidence_score = confidence.score
   trace.confidence_band = band
   trace.retrieval_state = retrieval_state.value if hasattr(retrieval_state, 'value') else str(retrieval_state)
   trace.evidence_shape = evidence_shape.to_dict() if evidence_shape else {}
   trace.retrieval_depth = len(chunks)
   trace.action_taken = action
   trace.execution_path = execution_path
   ```

4. Update action execution logic (replacing existing around line 1098):
   ```python
   # Execute path-specific behavior
   
   if execution_path == "abstain":
       # Immediate abstention
       trace.abstention_triggered = True
       trace.latency_ms = int((time.time() - start_time) * 1000)
       if background_tasks:
           background_tasks.add_task(log_policy_telemetry, trace)
       return build_abstention_response(confidence.score)
   
   elif execution_path == "fast":
       # Fast path: no reranking, no expansion
       logger.debug("Fast path: using base retrieval only")
       # Jump to generation
       pass  # Continue to generation phase
   
   elif execution_path == "standard":
       # Standard path: conditional reranking based on uncertainty gates
       if action == "conditional_reranking":
           logger.info("Standard path: invoking reranker due to uncertainty")
           reranker = getattr(request.app.state, 'reranker', None)
           if reranker and reranker.policy.mode != RerankMode.OFF:
               chunks, _ = await reranker.rerank_with_decision(query.question, embedding)
               trace.reranker_invoked = True
               trace.reranker_reason = "uncertainty_gates_triggered"
               # Re-score after reranking
               confidence = evidence_scorer.score_evidence(
                   chunks, query.question, query_type=qtype_str, policy=policy
               )
               band = confidence.band
               trace.confidence_band = band
       else:
           logger.debug("Standard path: uncertainty gates passed, using base evidence")
   
   elif execution_path == "cautious":
       # Cautious path: mandatory reranking + expanded retrieval
       logger.info("Cautious path: expanded retrieval + reranking")
       
       # Query expansion
       query_transformer = getattr(request.app.state, 'query_transformer', None)
       if query_transformer and query_transformer.mode != TransformMode.OFF:
           chunks, _, _ = await retriever.retrieve_with_transform(
               query=query.question,
               query_transformer=query_transformer,
               query_embedding=embedding,
               k=query.context_limit,
               latency_budget_ms=policy.get_latency_budget(qtype_str)
           )
       
       # Mandatory reranking
       reranker = getattr(request.app.state, 'reranker', None)
       if reranker and reranker.policy.mode != RerankMode.OFF:
           chunks, _ = await reranker.rerank_with_decision(query.question, embedding)
           trace.reranker_invoked = True
           trace.reranker_reason = "cautious_path_mandatory"
       
       # Re-score after all processing
       confidence = evidence_scorer.score_evidence(
           chunks, query.question, query_type=qtype_str, policy=policy
       )
       band = confidence.band
       trace.confidence_band = band
   ```

5. Update final telemetry capture before logging:
   ```python
   # Finalize trace with token counts
   # (Assuming token counting is available from context_result or ollama client)
   trace.tokens_total = len(prompt.split()) + len(answer.split())  # Rough estimate
   trace.tokens_generated = len(answer.split())
   trace.latency_ms = int((time.time() - start_time) * 1000)
   trace.chunks_retrieved = len(chunks)
   ```

**Verification:**
- Fast path skips reranking
- Standard path applies uncertainty gates
- Cautious path always reranks + expands
- Abstain path returns early with abstention response
- Telemetry fields populated correctly

---

### Wave 6: Response Format (Abstention & Prompt Variants)

**Goal:** Implement Phase 2-compliant abstention response and prompt templates.

#### Task 6.1: Create Abstention Response Builder

**File:** `api/app.py` (new function)

**Implementation:**

```python
def build_abstention_response(
    confidence_score: float,
    confidence_band: str = "insufficient",
    retrieval_attempted: bool = True,
    suggestion: Optional[str] = None
) -> Dict[str, Any]:
    """
    Build Phase 2-compliant abstention response.
    
    Returns structured response with status field for reliable client detection.
    
    Args:
        confidence_score: Raw confidence score (0-1)
        confidence_band: Which band triggered abstention
        retrieval_attempted: Whether retrieval was performed
        suggestion: Optional suggestion for query refinement
        
    Returns:
        Dict with status, message, and metadata fields
    """
    return {
        "status": "insufficient_evidence",
        "confidence_band": confidence_band,
        "message": "I don't have enough reliable evidence in the retrieved material to answer that confidently.",
        "metadata": {
            "confidence_score": round(confidence_score, 3),
            "retrieval_attempted": retrieval_attempted,
            "suggestion": suggestion or "Try rephrasing your question or providing more context."
        }
    }
```

Usage in `_rag_hybrid()`:
```python
if band == "insufficient" or action == "abstain":
    trace.abstention_triggered = True
    trace.latency_ms = int((time.time() - start_time) * 1000)
    if background_tasks:
        background_tasks.add_task(log_policy_telemetry, trace)
    return build_abstention_response(
        confidence_score=confidence.score,
        confidence_band=band,
        retrieval_attempted=len(chunks) > 0
    )
```

**Verification:**
- Response has `status: "insufficient_evidence"`
- Response has `confidence_band` field
- Response has user-facing `message` field
- Response has `metadata` with confidence_score, retrieval_attempted, suggestion
- Clients can detect abstention via `response.status == "insufficient_evidence"`

#### Task 6.2: Add Medium-Confidence Prompt Template

**File:** `api/app.py`

**Current templates:**
```python
RAG_PROMPT_TEMPLATE = """You are a helpful assistant. ..."""  # Direct (high)
RAG_CONSERVATIVE_PROMPT_TEMPLATE = """..."""  # Conservative (low)
```

**What to do:**

Add medium-confidence template:

```python
RAG_MEDIUM_CONFIDENCE_PROMPT = """You are a helpful assistant. Answer the question based on the provided context.

**Guidelines:**
- Base your answer primarily on the retrieved sources
- Acknowledge when evidence is limited or from fewer sources
- Use phrases like "Based on the available sources..." or "The retrieved material suggests..."
- Indicate supporting vs. conflicting information if present
- Cite sources where they strengthen your answer

Context:
{context}

Question: {question}

Answer:"""
```

Update routing logic in `_rag_hybrid()` around line 1170:

```python
# Select prompt template based on confidence band
if execution_path == "abstain":
    # Handled earlier as early return
    pass
elif execution_path == "fast" or band == "high":
    prompt_template = RAG_PROMPT_TEMPLATE  # Direct, no hedging
    trace.execution_path = "fast_generation"
elif execution_path == "standard" or band == "medium":
    prompt_template = RAG_MEDIUM_CONFIDENCE_PROMPT  # Light hedging
    trace.execution_path = "standard_generation"
elif execution_path == "cautious" or band == "low":
    prompt_template = RAG_CONSERVATIVE_PROMPT_TEMPLATE  # Strong hedging
    trace.execution_path = "cautious_generation"
else:
    prompt_template = RAG_PROMPT_TEMPLATE  # Default
```

**Verification:**
- High confidence uses direct template
- Medium confidence uses hedged template
- Low confidence uses conservative template
- Prompt selected before generation call

---

### Wave 7: Conflict Detection Implementation

**Goal:** Make EvidenceShape.contradiction_flag functional instead of placeholder.

#### Task 7.1: Implement Real Conflict Detection

**File:** `api/evidence_shape.py`

**Current code (line ~80):**
```python
# Simple contradiction detection: if scores are high but sources disagree
# (Placeholder for more advanced logic)
contradiction_flag = False
if source_count > 1 and agreement < 0.2 and top1_score > 0.8:
    contradiction_flag = False # Keep safe for now unless specifically triggered
```

**What to do:**

Replace with rule-based conflict detection:

```python
# Conflict Detection: Simple rule-based approach
# If multiple top passages assert incompatible claims on same topic
contradiction_flag = self._detect_contradiction(chunks, top1_score)

# Add new method to class:
def _detect_contradiction(self, chunks: List[Dict[str, Any]], top_score: float) -> bool:
    """
    Detect contradictory claims in top passages.
    
    Simple rule-based approach:
    - Look for explicit negations in top-3 passages
    - Check for opposing entities or actions
    - Flag if found and all passages have high scores
    
    Returns:
        True if contradiction detected, False otherwise
    """
    if len(chunks) < 2 or top_score < 0.7:
        return False  # Can't have contradiction with low confidence
    
    import re
    
    # Patterns indicating opposing claims
    negation_patterns = [
        r'\b(?:no|not|never|neither|cannot|isnt|dont|doesnt|wont)\b',
        r'\b(?:false|incorrect|wrong|denial of)\b'
    ]
    
    top_chunks = chunks[:3]
    texts = [c.get('content', '') for c in top_chunks]
    
    # Count negations in each chunk
    negation_counts = []
    for text in texts:
        count = sum(
            len(re.findall(pattern, text, re.IGNORECASE))
            for pattern in negation_patterns
        )
        negation_counts.append(count)
    
    # If one chunk has high negations and another has few, likely contradiction
    has_strong_negation = max(negation_counts) > 2
    has_no_negation = min(negation_counts) == 0
    
    if has_strong_negation and has_no_negation:
        logger.debug(
            f"Contradiction detected: negation pattern mismatch in top passages"
        )
        return True
    
    return False
```

**Verification:**
- Sets contradiction_flag to True when negation pattern mismatch detected
- Sets to False by default (safe)
- Only triggers for high-confidence top evidence
- Logs when detected

---

### Wave 8: Configuration & Documentation

**Goal:** Make all Phase 2 thresholds configurable and document the feature.

#### Task 8.1: Add Configuration to .env.example

**File:** `.env.example`

Add Phase 2 configuration:

```bash
# Phase 2: Confidence-Driven Control Loop

# Confidence band thresholds (0.0 - 1.0)
CONFIDENCE_HIGH=0.85
CONFIDENCE_MEDIUM=0.65
CONFIDENCE_LOW=0.45

# Standard path uncertainty detection gates
UNCERTAINTY_SCORE_GAP_THRESHOLD=0.15  # If top1-top2 gap < this, uncertain
UNCERTAINTY_MIN_TOP_STRENGTH=0.6      # If top-1 score < this, weak evidence
```

#### Task 8.2: Update README with Phase 2 Behavior

**File:** `README.md`

Add section describing Phase 2 behavior:

```markdown
## Confidence-Driven Routing (Phase 2)

The RAG pipeline now routes queries through different execution paths based on calibrated confidence scores:

### Confidence Bands

| Band | Range | Behavior | Latency |
|------|-------|----------|---------|
| High | >= 0.85 | Fast path: base retrieval only | Lowest |
| Medium | 0.65-0.84 | Standard path: conditional reranking | Medium |
| Low | 0.45-0.64 | Cautious path: expanded + reranking | Highest |
| Insufficient | < 0.45 | Abstain: no answer generated | Fast (early exit) |

### Standard Path Uncertainty Gates

For medium-confidence queries, the system checks:

1. **Score Gap**: If top-1 and top-2 scores differ by < 0.15, invoke reranker
2. **Top Strength**: If top-1 score < 0.6, invoke reranker
3. **Conflict**: If contradictory passages detected, invoke reranker

### Configuration

See `.env.example` for tunable thresholds:
- `CONFIDENCE_HIGH`, `CONFIDENCE_MEDIUM`, `CONFIDENCE_LOW`
- `UNCERTAINTY_SCORE_GAP_THRESHOLD`, `UNCERTAINTY_MIN_TOP_STRENGTH`

### Observability

Enable telemetry to monitor:
- Which paths queries take
- Why reranker was invoked
- Token usage by path
- Abstention rate

Check logs for `execution_path`, `confidence_band`, `reranker_reason` fields.
```

---

## Dependency Map

```
Wave 1 (Config)
  ↓
Wave 2 (Uncertainty Gates)
  ↓
Wave 3 (Routing Policy)
  ├→ Wave 5 (Pipeline Integration) ← depends on 1, 2, 3
  ├→ Wave 6 (Response Format)
  └→ Wave 7 (Conflict Detection)
  ↓
Wave 4 (Telemetry) ← populates during Wave 5
Wave 8 (Documentation)
```

**Execution order:**
1. Wave 1: Thresholds (isolated change)
2. Wave 2: Uncertainty gates (isolated module)
3. Wave 3: Routing policy (extend router, no integration yet)
4. Wave 4: Telemetry (extend trace)
5. Wave 5: Pipeline integration (wires everything together)
6. Wave 6: Response format (response builders)
7. Wave 7: Conflict detection (improve evidence_shape)
8. Wave 8: Documentation (docs only)

---

## Implementation Effort Estimate

| Wave | File(s) | LOC | Complexity | Hours |
|------|---------|-----|------------|-------|
| 1 | evidence_scorer.py | ~30 | Low | 1 |
| 2 | retrieval_state.py | ~80 | Low | 1.5 |
| 3 | routing.py | ~120 | Medium | 2 |
| 4 | telemetry.py | ~20 | Low | 0.5 |
| 5 | app.py (_rag_hybrid) | ~200 | High | 3.5 |
| 6 | app.py | ~80 | Low | 1 |
| 7 | evidence_shape.py | ~50 | Medium | 1.5 |
| 8 | README.md, .env.example | ~40 | Low | 0.5 |
| **Total** | | **620** | | **11.5** |

---

## Verification Checklist

### Functional Verification

- [ ] Confidence band thresholds correctly configurable (0.85 / 0.65 / 0.45)
- [ ] EvidenceScorer returns correct band for each bracket
- [ ] UncertaintyDetector.detect_uncertainty() correctly identifies score gap < 0.15
- [ ] UncertaintyDetector.detect_uncertainty() correctly identifies top_strength < 0.6
- [ ] UncertaintyDetector.detect_uncertainty() detects contradictions
- [ ] ContextualRouter.route_with_confidence() maps bands to paths (fast/standard/cautious/abstain)
- [ ] Fast path skips reranking (verified in trace)
- [ ] Standard path does NOT rerank when gates pass (verified in trace)
- [ ] Standard path DOES rerank when gates fail (verified in trace)
- [ ] Cautious path always reranks (verified in trace)
- [ ] Abstraction path returns early before retrieval/generation
- [ ] Abstention response has `status: "insufficient_evidence"`
- [ ] Medium-confidence prompt template exists and used
- [ ] PolicyTrace captures all Phase 2 fields
- [ ] Telemetry logs reranker_invoked and reranker_reason

### Integration Verification

- [ ] Application starts without errors (lifespan initializes UncertaintyDetector)
- [ ] _rag_hybrid() path works end-to-end for high-confidence query
- [ ] _rag_hybrid() path works end-to-end for medium-confidence query (gates pass)
- [ ] _rag_hybrid() path works end-to-end for medium-confidence query (gates fail)
- [ ] _rag_hybrid() path works end-to-end for low-confidence query
- [ ] _rag_hybrid() path works end-to-end for insufficient-confidence query
- [ ] Prompt templates render without errors
- [ ] Telemetry fields populate and serialize to JSON

### Performance Verification

- [ ] Fast path completes faster than standard (latency < standard latency)
- [ ] Standard path with gates pass doesn't invoke reranker (token count lower)
- [ ] Standard path with gates fail reranks (token count higher, latency increased)
- [ ] Cautious path reranks and expands (highest token, highest latency)

### Configuration Verification

- [ ] Environment variables override defaults
- [ ] Missing env vars use defaults without error
- [ ] Invalid threshold values raise informative error
- [ ] Configuration logged at startup

---

## Known Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Uncertainty gates too sensitive (trigger too often) | Start with conservative thresholds (0.15 gap, 0.6 strength); tune based on Phase 3 metrics |
| Contradiction detection misses real conflicts | Keep simple rule-based for now; Phase 4+ can add LLM-based detection |
| Token counting inaccurate | Use rough estimation (word count) for Phase 2; Phase 3+ will add precise counting |
| Reranker becomes bottleneck in cautious path | Monitor latency in telemetry; Phase 3 tests will expose if needed |
| Medium-confidence template phrasing unclear | Review with team; Phase 3 evaluation will measure user satisfaction |

---

## Next Steps

1. **Execute Phase 2:** Run Wave 1 through Wave 8 in sequence
2. **Phase 3 (CI Verification):** Write tests to verify confidence-to-behavior mapping
3. **Phase 4 (Policy Infrastructure):** Harden policy registry and replay harness
4. **Phase 5 (Contextual Routing):** Extend routing to query types + evidence shape

---

## Success Criteria (from Phase 2 Requirements)

- ✅ High-confidence queries complete without reranker → observable via execution_path="fast"
- ✅ Medium-confidence queries with uncertainty gates invoke reranker → observable via reranker_invoked=true
- ✅ Low-confidence queries produce conservative answers → prompt template differs
- ✅ Insufficient-confidence queries return abstention → response.status == "insufficient_evidence"

All criteria will be verified in Phase 3 through automated CI tests.


# Phase 2 Research: Confidence-Driven Control Loop

**Completed:** 2026-03-08  
**Scope:** Understand current pipeline architecture, confidence scoring, telemetry instrumentation, and routing capability  
**Output:** Implementation gaps and integration points for Phase 2

---

## Current Pipeline Architecture

The RAG pipeline in `_rag_hybrid()` follows this flow:

```
User Query (RAGQuery)
    ↓
1. Query Classification (classifier.classify) → QueryType
    ↓
2. Embedding Generation (ollama.generate_embedding)
    ↓
3. Hybrid Retrieval (retriever.retrieve) → chunks with scores
    ↓
4. Evidence Shape Analysis (shape_extractor.extract) → EvidenceShape
    ↓
5. Retrieval State Labeling (state_labeler.label) → RetrievalState
    ↓
6. Confidence Scoring (evidence_scorer.score_evidence) → ConfidenceScore
    ↓
7. Contextual Routing (router.route) → RouteDecision
    ↓
8. Action Execution (expanded_retrieval | reranking | generation | abstain)
    ↓
9. Context Building (builder.build_context) → formatted context
    ↓
10. Answer Generation (ollama.generate_response) → answer
    ↓
11. Telemetry Logging (PolicyTrace) → background task
```

---

## Confidence Scoring (Current State)

### ConfidenceScore Dataclass
Location: `shared/evidence_scorer.py`

Currently captures:
- `score` (float 0–1): Overall confidence
- `band` (string): "high", "medium", "low", "insufficient"
- `evidence_strength` (string): Qualitative assessment
- `coverage_estimate` (float): Coverage of query
- `component_scores` (dict): Breakdown by signal type
- `recommendations` (list): Suggested actions

### Current Band Thresholds

Defined in `EvidenceScorer.__init__()` and used in `score_evidence()`:

```python
high_confidence_threshold = 0.75  # ← Phase 2 spec wants 0.85
medium_threshold = 0.50           # ← Phase 2 spec wants 0.65
min_confidence_threshold = 0.25   # ← Phase 2 spec wants 0.45
```

**Issue:** Current thresholds (0.75 / 0.50 / 0.25) don't match Phase 2 spec (0.85 / 0.65 / 0.45).

### Evidence Components Scored

1. **score_strength** (0.25 weight): How high are top-k scores?
2. **score_decay** (0.15 weight): How quickly scores drop?
3. **method_agreement** (0.15 weight): Lexical vs vector agreement
4. **source_diversity** (0.20 weight): Document count and spread
5. **rerank_confidence** (0.15 weight): From Phase 7 reranker
6. **transform_quality** (0.10 weight): From Phase 8 query expansion

**Note:** These signals exist but are weighted into an overall score. Phase 2 needs to *extract* specific metrics (score_gap, top_strength, conflict detection) for the uncertainty gates.

---

## Evidence Shape (Data Available)

Location: `api/evidence_shape.py`

Captures critical metrics Phase 2 needs:

```python
class EvidenceShape:
    top1_score: float          # ← Used for "top_strength" gate
    topk_mean_score: float
    score_gap: float           # ← Used for "score_gap" gate
    source_diversity: float
    source_count: int
    chunk_agreement: float     # ← Used for conflict detection
    contradiction_flag: bool   # ← Conflict indicator (currently placeholder)
```

**Gap:** `contradiction_flag` is currently always False (placeholder). Conflict detection needs real implementation in Phase 2.

---

## Telemetry Instrumentation (Current State)

### PolicyTrace (Location: `shared/telemetry.py`)

Currently captures:
```python
@dataclass
class PolicyTrace:
    query_text: str
    query_id: str
    query_type: str
    confidence_score: float
    confidence_band: str       # ← Already Phase 2 will populate
    action_taken: str          # ← Already exists
    execution_path: str        # ← Already exists
    retrieval_state: str       # ← Already exists
    policy_version: str
    retrieval_mode: str
    chunks_retrieved: int      # ← Already Phase 2 will use
    latency_ms: int            # ← Already Phase 2 will use
    groundedness_score: Optional[float]
    unsupported_claim_count: Optional[int]
    citation_accuracy: Optional[float]
    quality_score: Optional[float]
    evidence_shape: Dict       # ← Already populated with all metrics
    metadata: Dict
    created_at: str
```

**Status:** PolicyTrace is *mostly* ready for Phase 2. Missing fields from 2-CONTEXT.md:
- `reranker_invoked` (bool): Not currently tracked
- `reranker_reason` (str): Not currently tracked (e.g., "score_gap < 0.15")
- `tokens_generated` (int): Not currently tracked
- `tokens_total` (int): Not currently tracked
- `abstention_triggered` (bool): Not explicitly tracked
- `retrieval_depth` (int): Not tracked separately from chunks_retrieved

**Action for Phase 2:** Extend PolicyTrace with Phase 2-specific fields.

### Where Telemetry is Logged

In `_rag_hybrid()` around line 1200:
```python
trace = PolicyTrace(...)
trace.confidence_score = confidence.score
trace.confidence_band = band
trace.retrieval_state = retrieval_state.value
trace.evidence_shape = evidence_shape.to_dict()
trace.action_taken = action
trace.execution_path = execution_path
trace.chunks_retrieved = len(chunks)
trace.latency_ms = int((time.time() - start_time) * 1000)

if background_tasks:
    background_tasks.add_task(log_policy_telemetry, trace)
```

Logging happens in background via `log_policy_telemetry()` function (location: need to search).

---

## Routing Infrastructure (Current State)

### RoutingContext (Location: `api/routing.py`)

Already defined with Phase 2 fields:
```python
@dataclass
class RoutingContext:
    query_type: QueryType
    confidence_band: str       # ← Phase 2 needs this
    retrieval_state: RetrievalState
    latency_budget: int
    policy: RAGPolicy
```

### RouteDecision (Location: `api/routing.py`)

Already captures execution path:
```python
@dataclass
class RouteDecision:
    action: str                # What to do
    execution_path: str        # e.g., "fast", "standard", "cautious", "abstain"
    reason: str                # Why
```

### ContextualRouter.route() (Location: `api/routing.py`)

Already implemented with:
- Policy-based routing lookup
- Contextual overrides for CONFLICTED evidence
- Query-type-specific logic (EXACT_FACT requirements)
- Summarization-specific relaxations

**Current routing logic:**
1. Query policy for action based on band + qtype
2. Apply contextual overrides if CONFLICTED
3. Apply type-specific rules (EXACT_FACT → higher certainty requirement)
4. Return RouteDecision

**What's missing from Phase 2 spec:**
- No explicit mapping of band to execution path names (fast / standard / cautious)
- No uncertainty gates (score_gap, top_strength, conflict) for Standard path
- No reranker invocation logic in pipelines
- No query expansion for Standard path based on uncertainty

### How Routing Integrates into Pipeline

In `_rag_hybrid()` around line 1080:
```python
routing_ctx = RoutingContext(
    query_type=qtype_str,
    confidence_band=band,
    retrieval_state=retrieval_state,
    latency_budget=latency_budget,
    policy=policy
)

route = router.route(routing_ctx) if router else None
action = route.action if route else policy.get_action(band, qtype_str)
execution_path = route.execution_path if route else "standard"
```

Then actions are executed:
- `"expanded_retrieval"` → trigger query_transformer
- `"rerank_only"` → trigger reranker
- `"abstain"` → return early with abstention response

---

## Abstention Response (Current State)

### Current Format

Located at line 171 in `app.py`:
```python
RAG_ABSTAIN_RESPONSE = "I'm sorry, but I don't have enough reliable information in my database to answer your question accurately. Please try rephrasing your query or asking about a different topic."
```

Current response when abstaining (lines 1052, 1152):
```python
return {
    "question": query.question,
    "answer": RAG_ABSTAIN_RESPONSE,
    "sources": [],
    "source_citations": [],
    "confidence_band": "insufficient",
    "policy_version": policy.version,
    "hybrid_search": True
}
```

**Issues with current format:**
1. No `status` field — clients can't reliably detect abstention
2. Raw `confidence_band` is a string not matching spec
3. No `metadata` wrapper for internal fields
4. Message is always the same hardcoded response

**Phase 2 phase 2 spec wants:**
```json
{
  "status": "insufficient_evidence",
  "confidence_band": "insufficient",
  "message": "I don't have enough reliable evidence...",
  "metadata": {
    "confidence_score": 0.38,
    "retrieval_attempted": false,
    "suggestion": "Refine your question..."
  }
}
```

---

## Query Expansion (Current State)

### QueryTransformer Capability

Location: `shared/query_transformer.py`

**Exists and is ready:**
- `TransformMode` enum: OFF, ALWAYS, SELECTIVE
- `TransformDecision` dataclass with:
  - `should_transform`
  - `transformed_queries` (list)
  - `transform_types` (list of types applied)
- `QueryTransformer.transform()` method
- Query merging via `merge_results()`

### Current Integration in Pipeline

In `_rag_hybrid()` line 1098:
```python
if action == "expanded_retrieval" or action == "query_transformation":
    query_transformer = getattr(request.app.state, 'query_transformer', None)
    if query_transformer and query_transformer.mode != TransformMode.OFF:
        chunks, transform_decision, _ = await retriever.retrieve_with_transform(
            query=query.question,
            query_transformer=query_transformer,
            query_embedding=embedding,
            k=query.context_limit,
            latency_budget_ms=latency_budget
        )
        # Re-score after expansion
        confidence = evidence_scorer.score_evidence(chunks, ...)
        band = confidence.band
        control_actions.append("query_transformation")
```

**Current behavior:**
- Transformation is triggered by explicit action ("expanded_retrieval")
- Happens once for the query
- Re-scores after expansion
- Does NOT recursively apply confidence bands

**Phase 2 compatibility:**
✅ Query expansion does NOT recursively re-band
✅ Expansion is inherited from selected path
✅ Fits the "single-evaluation boundary" model

---

## Prompt Templates (Current State)

### Templates Defined

Line 143–169 in `app.py`:

1. **RAG_PROMPT_TEMPLATE** (standard, direct)
2. **RAG_CONSERVATIVE_PROMPT_TEMPLATE** (called but need to verify it exists)

### Current Usage

Line 1172 in `_rag_hybrid()`:
```python
if band == "low" or action == "conservative_prompt":
    logger.info(f"Conservative generation triggered")
    prompt_template = RAG_CONSERVATIVE_PROMPT_TEMPLATE
    execution_path = "conservative_generation"
else:
    prompt_template = RAG_PROMPT_TEMPLATE
```

**Phase 2 spec wants 4 prompt variants:**
1. High confidence (direct) → RAG_PROMPT_TEMPLATE
2. Medium confidence (light hedge) → Need to create
3. Low confidence (strong hedge) → RAG_CONSERVATIVE_PROMPT_TEMPLATE
4. Insufficient (abstention) → Handled separately as early return

**Gap:** Need to add medium-confidence hedged template.

---

## Reranker Pipeline (Current State)

### Reranker Capability

Available as:
```python
reranker = getattr(request.app.state, 'reranker', None)
```

### Current Integration

Line 1113 in `_rag_hybrid()`:
```python
elif action == "rerank_only" or action == "reranking":
    reranker = getattr(request.app.state, 'reranker', None)
    if reranker and reranker.policy.mode != RerankMode.OFF:
        chunks, _ = await reranker.rerank_with_decision(query.question, embedding)
```

**Phase 2 needs:**
- Conditional reranking for Standard path based on uncertainty gates
- Track whether reranker was invoked and why in telemetry

---

## Integration Points Summary

### What's Ready (Can Use as-is)

✅ ContextualRouter structure and integration point  
✅ RoutingContext with confidence_band field  
✅ EvidenceShape metrics for uncertainty detection  
✅ QueryTransformer for query expansion  
✅ Reranker for answer enhancement  
✅ PolicyTrace for telemetry  
✅ Background telemetry logging  
✅ Abstention message in pipeline  

### What Needs Implementation (Phase 2 Work)

❌ **Confidence band thresholds:** Update from (0.75/0.50/0.25) to (0.85/0.65/0.45)  
❌ **Uncertainty gates for Standard path:**
   - Score gap detection (< 0.15 → rerank)
   - Top evidence strength check (< 0.6 → rerank)
   - Conflict detection (contradictory passages → rerank)  
❌ **Execution path naming:** Map bands to "fast" / "standard" / "cautious" / "abstain"  
❌ **Medium-confidence template:** Create hedged prompt variant  
❌ **Abstention response format:** Add status + metadata wrapper  
❌ **Telemetry enhancements:**
   - Add `reranker_invoked` (bool)
   - Add `reranker_reason` (str)
   - Add `tokens_generated`, `tokens_total`
   - Add `abstention_triggered`
   - Separate `retrieval_depth` tracking  
❌ **Conflict detection implementation:** Make `contradiction_flag` functional  

---

## Key Files to Modify

| File | Change | Priority |
|------|--------|----------|
| `shared/evidence_scorer.py` | Update band thresholds (0.85/0.65/0.45) | High |
| `api/app.py` (_rag_hybrid) | Implement uncertainty gates + routing logic | High |
| `shared/telemetry.py` | Add Phase 2 telemetry fields | High |
| `api/app.py` | Update abstention response format | Medium |
| `api/app.py` | Add medium-confidence prompt template | Medium |
| `api/evidence_shape.py` | Implement conflict detection | Medium |
| `shared/database.py` | Log/store telemetry efficiently | Low (background) |

---

## Recommendations for Planner

### 1. Routing Decision Flow (New)

Create a method to encapsulate the Phase 2 routing logic:

```python
async def apply_confidence_routing(
    confidence_band: str,
    quality_metrics: EvidenceShape,
    chunks: List[Dict],
    flags: dict  # reranker_available, query_transformer_available, etc.
) -> RouteDecision:
    """
    Apply Phase 2 confidence routing with uncertainty gates.
    
    Returns RouteDecision with execution_path and reasons.
    """
```

This would:
1. Map band to base path (high→fast, medium→standard, low→cautious, insufficient→abstain)
2. For standard path: apply uncertainty gates
3. Return RouteDecision with execution path and telemetry reason

### 2. Uncertain Detection Gates (New)

Create utility to encapsulate the numeric gates:

```python
class UncertaintyDetector:
    def __init__(self, 
        score_gap_threshold: float = 0.15,
        min_top_strength: float = 0.6,
    ):
        pass
    
    def detect_uncertainty(self, chunks: List) -> Tuple[bool, str]:
        """
        Returns (is_uncertain, reason).
        Reason indicates which gate triggered: "score_gap", "weak_evidence", "conflict"
        """
```

### 3. Abstention Response (New)

Create response builder:

```python
def build_abstention_response(
    confidence_score: float,
    retrieval_attempted: bool,
    suggestion: Optional[str] = None
) -> Dict:
    """Build Phase 2-compliant abstention response."""
```

### 4. Prompt Variants (New)

Define medium-confidence template alongside existing:

```python
RAG_MEDIUM_CONFIDENCE_PROMPT = """..."""  # Light hedging variant
```

### 5. Telemetry Enhancement (New)

Extend PolicyTrace usage in `_rag_hybrid()` to capture all Phase 2 fields:

```python
trace.reranker_invoked = was_reranker_called
trace.reranker_reason = why_reranker_called
trace.tokens_generated = answer_token_count
trace.tokens_total = total_tokens_used
trace.abstention_triggered = is_abstaining
trace.retrieval_depth = initial_chunk_count_before_expansion
```

---

## Verification Checklist for Planner

Before submission, planner should verify:

- [ ] Band thresholds are configurable (env or config file)
- [ ] Confidence band boundaries match spec (0.85 / 0.65 / 0.45)
- [ ] Four execution paths distinct in code (fast / standard / cautious / abstain)
- [ ] Standard path uncertainty gates check score_gap AND top_strength AND conflict
- [ ] Reranker invocation conditional (only if uncertainty gates trigger)
- [ ] Query expansion happens but does NOT recursively apply confidence bands
- [ ] Abstention response includes both status and message fields
- [ ] All Phase 2 telemetry fields are captured in PolicyTrace
- [ ] Conflict detection logic is implemented (not placeholder)
- [ ] All prompt variants exist (direct / hedged / conservative)

---

## Discovered Gotchas

1. **Band thresholds in policy:** `policy.get_threshold()` method exists — check if it needs to be updated or if thresholds should live in environment only.

2. **Reranker mode check:** Code checks `reranker.policy.mode != RerankMode.OFF` — Phase 2 should honor this but also add confidence-driven triggers.

3. **QueryTransformer mode:** Similar pattern with `TransformMode.OFF` — Phase 2 respects but adds confidence-driven selection.

4. **Evidence shape contradiction:** Currently always False. Needs real implementation for conflict detection.

5. **Telemetry background task:** Logging is async in background. Need to ensure all Phase 2 fields are set BEFORE background task is scheduled.

---

## Next Step

**Planner is ready.** Pass 2-CONTEXT.md and 2-RESEARCH.md together to planner to design detailed implementation.


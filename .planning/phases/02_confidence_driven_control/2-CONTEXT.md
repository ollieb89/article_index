# Phase 2 Decision Context: Confidence-Driven Control Loop

**Created:** 2026-03-08  
**Locked Decisions:** Confidence band routing, uncertainty detection, abstention format, query expansion scope  
**Status:** Ready for research and planning

---

## Overview

Phase 2 converts the RAG pipeline from static execution into a **confidence-driven policy router** that selects execution paths (Fast / Standard / Cautious / Abstain) based on calibrated confidence signals.

All decisions below are **locked** — researcher should not revisit these choices, only implement them.

---

## Decision 1: Confidence Band Routing

### Thresholds (Configurable)

Confidence scores (range [0, 1]) are mapped to execution paths via numeric band thresholds:

| Band         | Range       | Execution Path | Retrieval Style         | Reranking | Prompt Tone |
|--------------|-------------|----------------|-------------------------|-----------|------------|
| High         | ≥ 0.85      | Fast Path      | Base only               | Skip      | Direct     |
| Medium       | 0.65–0.84   | Standard Path  | Expanded (smart)        | Conditional | Light hedge |
| Low          | 0.45–0.64   | Cautious Path  | Expanded (max depth)    | Mandatory | Strong hedge |
| Insufficient | < 0.45      | Abstain Path   | None                    | None      | Abstention |

**Guardrails:**

- Thresholds are **configurable** (environment or config file, not hardcoded)
- Thresholds are **static in production** — no runtime self-adjustment
- Any threshold change requires **offline evaluation replay validation** before deployment
- Thresholds are **calibrated offline** using evaluation datasets

### Configuration Structure

```python
# Example in config or env
CONFIDENCE_BANDS = {
    "high": 0.85,
    "medium": 0.65,
    "low": 0.45
}
```

---

## Decision 2: Standard Path Uncertainty Detection

### Numeric Gates (Primary Decision Logic)

When a query routes to **Standard Path** due to medium confidence, the system applies numeric thresholds to decide whether to invoke the reranker:

#### Gate 1: Score Gap Threshold

- **Metric:** Difference between top-1 and top-2 evidence scores
- **Rule:** If `score[0] - score[1] < threshold`, trigger reranking (uncertain between candidates)
- **Recommendation for researcher:** Start with threshold = 0.15 (configurable)

#### Gate 2: Minimum Top-Evidence Strength

- **Metric:** Absolute score of top-1 evidence
- **Rule:** If `score[0] < threshold`, evidence is weak → trigger reranking
- **Recommendation for researcher:** Start with threshold = 0.6 (configurable)

#### Gate 3: Conflict Detection Rule

- **Metric:** Explicit incompatibility between top passages
- **Rule:** If multiple top-3 passages assert contradictory facts on the same topic, mark as conflicted
- **Implementation approach:**
  - Parse top-3 evidence chunks for key entities/claims
  - Check for explicit negations or contradictory assertions
  - If conflict detected → trigger reranking
- **Recommendation for researcher:** This is rule-based logic; keep simple and explicit (e.g., regex patterns for negations, entity mismatch detection)

### Reranker Invocation Logic

```pseudo
if confidence_band == MEDIUM:
    score_gap = score[0] - score[1]
    top_strength = score[0]
    has_conflict = detect_conflicts(top_3_passages)
    
    if score_gap < 0.15 or top_strength < 0.6 or has_conflict:
        invoke_reranker()
    else:
        skip_reranker()
```

**Note:** All threshold values (0.15, 0.6, conflict rules) should be **configurable and documented** so they can be tuned during evaluation.

---

## Decision 3: Abstention Response Format

### Response Structure

When confidence lands in the **Insufficient band** (< 0.45), return a structured response with both **machine-readable status** and **user-facing message**:

```json
{
  "status": "insufficient_evidence",
  "confidence_band": "insufficient",
  "message": "I don't have enough reliable evidence in the retrieved material to answer that confidently.",
  "metadata": {
    "confidence_score": 0.38,
    "retrieval_attempted": false,
    "suggestion": "Refine your question or try different keywords."
  }
}
```

### Field Semantics

- **status** (string, required): Machine-readable signal for client code — value is always `"insufficient_evidence"` in Phase 2
- **confidence_band** (string, required): Which band triggered abstention — value is always `"insufficient"` in Phase 2
- **message** (string, required): User-facing natural language explanation — no jargon, clear statement of why answer cannot be provided
- **metadata** (object, optional): Internal telemetry — includes raw confidence_score, retrieval_attempted, and optional suggestion
  - **metadata.confidence_score**: Not shown to user directly, but available for telemetry/logging
  - **metadata.suggestion** (optional): Brief suggestion for query refinement (e.g., "Try a more specific question" or "The system lacks data on this topic")

### Client Interpretation

Clients can reliably detect abstention via:

```javascript
if (response.status === "insufficient_evidence") {
  // Handle as abstention, not error
  show_abstention_message(response.message);
} else {
  // Normal answer
  show_answer(response.answer);
}
```

### Out of Scope for Phase 2

- User-visible confidence scores in normal (non-insufficient) answers — Phase 5 may expose this for contextual routing
- Multi-turn clarification dialogue — beyond Phase 2 scope
- Fallback to web search or external data sources — separate capability

---

## Decision 4: Query Expansion — Single-Evaluation Boundary

### Routing Boundary Rule

**Confidence is evaluated exactly once per user query, at the routing boundary.**

- Original user query is scored and banded
- Band selection chooses execution path (Fast / Standard / Cautious / Abstain)
- Within the selected path, any expansion queries (e.g., Standard path generates 2–3 topic refinements) **inherit the parent path policy**, they are **not independently banded or routed**

### Standard Path Expansion Example

```
User Query: "What is the impact of climate change on agriculture?"
↓
confidence_score = 0.72 → MEDIUM band
↓
execution_path = STANDARD
↓
Standard path logic:
  - Generate 2–3 expansions:
    - "How does temperature change affect crop yields?"
    - "Water availability and agricultural productivity"
    - "Soil health and climate resilience"
  - Retrieve candidates for ALL (original + expansions)
  - Apply numeric gates (score gap, top strength, conflict)
  - Invoke reranker only if gates trigger uncertainty
  - Generate answer using expanded evidence
↓
All expansions used the STANDARD path policy, not independently routed
```

### Why Single-Evaluation

- **Stability:** Avoids nested routing decisions that could create explosive search space
- **Observability:** One confidence score per user interaction simplifies telemetry and replay
- **Simplicity:** Phase 2 focus is on four discrete execution paths, not per-expansion routing
- **Forward compatibility:** Phase 5 (Contextual Routing) may expand this to query-type-driven routing, but Phase 2 keeps it simple

### Fast Path (No Expansion)

Fast path **does not generate expansions** — it retrieves base evidence only and answers directly. This is fine because high confidence (≥ 0.85) means evidence is already strong.

### Insufficient Path (No Retrieval)

Insufficient confidence → Abstain path → no retrieval, no expansions. Short-circuit immediately.

---

## Telemetry & Observability

Every routing decision must be observable for evaluation replay and policy auditing:

### Required Telemetry Fields

Record with each request:

- **confidence_score**: Raw calibrated score (0.0–1.0)
- **confidence_band**: Which band ("high", "medium", "low", "insufficient")
- **routing_path**: Which execution path ("fast", "standard", "cautious", "abstain")
- **retrieval_depth**: How many candidates retrieved before ranking
- **reranker_invoked**: Boolean (true if reranker was called)
- **reranker_reason**: String (e.g., "score_gap < 0.15" or "top_strength < 0.6" or "conflict_detected")
- **latency_ms**: Total request latency
- **tokens_generated**: Tokens in final answer (if not abstain)
- **tokens_total**: All tokens (retrieval + generation)
- **abstention_triggered**: Boolean

### Policy Trace Attachment

Telemetry should be stored in a **policy trace** for replay and audit:

```json
{
  "request_id": "uuid",
  "timestamp": "2026-03-08T15:42:00Z",
  "query": "What is the impact of climate change on agriculture?",
  "confidence_score": 0.72,
  "confidence_band": "medium",
  "routing_path": "standard",
  "retrieval_depth": 15,
  "reranker_invoked": true,
  "reranker_reason": "conflict_detected: passages [2,4] assert incompatible facts",
  "latency_ms": 1240,
  "tokens_generated": 185,
  "tokens_total": 3521,
  "abstention_triggered": false
}
```

This trace enables:
- **Replay validation:** Re-run the same query + evidence with replay harness
- **Calibration audit:** Verify thresholds were correctly applied
- **Cost analysis:** Compare latency/tokens across paths
- **Threshold tuning:** Identify if current thresholds split queries correctly

---

## Implementation Priority

For researcher/planner:

1. **Core routing logic** — map confidence score → band → path (4 discrete paths)
2. **Numeric uncertainty gates** — implement score-gap, top-strength, conflict detection for Standard path
3. **Abstention response** — structured JSON with status + message
4. **Prompt adaptation** — simple templates for each path (hard-code initially, no LLM-generated tone)
5. **Telemetry instrumentation** — capture all required fields in policy trace
6. **Integration** — wire router into existing RAG pipeline before/after retrieval and generation phases

---

## Validation Checklist for Researcher

Before handing off to planner, researcher should confirm:

- [ ] Confidence band thresholds are configurable (not hardcoded)
- [ ] Standard path uncertainty gates sum to a simple decision rule (not complex heuristics)
- [ ] Abstention response includes both `status` and user-facing `message`
- [ ] Query expansions inherit parent path (no recursive routing)
- [ ] Telemetry fields are defined and can be logged at request boundaries
- [ ] Out-of-scope items (user-visible scores, dialogue, fallback search) are deferred
- [ ] Four execution paths are distinct enough to test independently

---

## Out of Scope (Explicitly Deferred)

- Adaptive threshold learning from signal data → Phase 4 (Policy Hardening) or later
- User-visible confidence scores in normal answers → Phase 5 (Contextual Routing)
- Multi-turn dialogue or clarification → Future phase
- Fallback to external data or web search → Separate system capability
- Per-query-type routing logic → Phase 5 (Contextual Routing)

---

## Next Steps

**Researcher:** Investigate how to instrument ContextualRouter to emit confidence scores compatible with this schema, and verify existing telemetry capture points.

**Planner:** Design execution path as pluggable policy; define prompt templates for each path; wire uncertainty gates into Standard path logic.


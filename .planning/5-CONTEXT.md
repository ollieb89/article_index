# Phase 5 Context: Contextual Policy Routing Design

**Status:** Design Review  
**Created:** 2026-03-08  
**Purpose:** Lock architectural decisions for multidimensional routing rule engine  

---

## 1. Executive Summary

Phase 5 extends routing from 1D (confidence_band → execution_path) to multidimensional:

```
query_type
    ×
retrieval_state
    ×
confidence_band
    ×
evidence_shape
    ↓
execution_path
```

This document locks the design contract for the rule engine, precedence model, policy schema, and fallback strategy.

---

## 2. Core Design Decisions

### DECISION-1: Declarative Rule-Table Engine

**Status:** ACCEPTED  
**Rationale:** Prevents nested conditional explosion, enables auditability, supports rule toggling  

The routing engine evaluates rules from a declarative table, not nested if/elif logic.

**Engine Flow:**

```
1. Build normalized routing context (categorical bands)
2. Evaluate all enabled rules against context
3. Collect matching rules
4. Rank by: specificity → priority → stable tie-break
5. Return winner.action or apply fallback
6. Emit telemetry: matched_rule_id, fallback_used, etc.
```

**Anti-pattern rejected:**
```python
# DO NOT DO THIS
if query_type == "exact_fact":
    if retrieval_state == "SOLID":
        if confidence_band == "high":
            return "fast"
```

---

### DECISION-2: Categorical Bands Over Raw Values

**Status:** ACCEPTED  
**Rationale:** Rules stay readable, stable, and explainable  

| Raw Value | Categorical Band |
|-----------|------------------|
| confidence_score | confidence_band (high/medium/low/insufficient) |
| coverage_score | coverage_band (high/medium/low) |
| agreement_score | agreement_band (high/medium/low) |
| spread | spread_band (narrow/medium/wide) |

**Router consumes bands, not floats.**

Telemetry still logs raw values for debugging.

---

### DECISION-3: Rule Precedence = Specificity > Priority > Stable Tie-Break

**Status:** ACCEPTED  
**Rationale:** Intuitive, deterministic, prevents broad rules from shadowing narrow safety rules  

**Specificity definition (Phase 5):**
```
specificity = count of condition fields in rule
```

4-condition rule beats 3-condition rule.

**Tie-breaking order:**
1. **Specificity** (descending): more conditions wins
2. **Priority** (descending): explicit numeric breaks ties
3. **ID** (ascending): final deterministic tie-break

**Example:**

```yaml
Rule A: {query_type: exact_fact, retrieval_state: SOLID, confidence_band: high} → specificity 3
Rule B: {query_type: exact_fact, confidence_band: high} → specificity 2

Both match → Rule A wins (more specific)
```

---

### DECISION-4: Action is Structured Object, Not String

**Status:** ACCEPTED  
**Rationale:** Future-proof, supports stage directives beyond execution_path  

**Phase 5 minimal action:**
```json
{
  "action": {
    "execution_path": "fast"
  }
}
```

**Future expansion (post-Phase 5):**
```json
{
  "action": {
    "execution_path": "cautious",
    "expand_retrieval": true,
    "invoke_reranker": true,
    "generation_mode": "conservative"
  }
}
```

---

### DECISION-5: Policy JSON Schema

**Status:** ACCEPTED  
**Schema version:** 1.0-contextual  

```json
{
  "policy_version": "2026-03-08T15:30:01Z",
  "policy_hash": "sha256:...",
  "routing_defaults": {
    "by_confidence_band": {
      "high": "fast",
      "medium": "standard",
      "low": "cautious",
      "insufficient": "abstain"
    }
  },
  "contextual_routing_rules": [
    {
      "id": "exact_fact_solid_high",
      "enabled": true,
      "priority": 100,
      "conditions": {
        "query_type": "exact_fact",
        "retrieval_state": "SOLID",
        "confidence_band": "high"
      },
      "action": {
        "execution_path": "fast"
      },
      "reason": "High-confidence exact fact with solid retrieval can use fast path"
    },
    {
      "id": "fragile_guardrail",
      "enabled": true,
      "priority": 200,
      "conditions": {
        "retrieval_state": "FRAGILE"
      },
      "action": {
        "execution_path": "cautious",
        "expand_retrieval": true,
        "invoke_reranker": true
      },
      "reason": "Fragile retrieval forces cautious handling"
    },
    {
      "id": "empty_abstain",
      "enabled": true,
      "priority": 300,
      "conditions": {
        "retrieval_state": "EMPTY"
      },
      "action": {
        "execution_path": "abstain",
        "generation_skipped": true
      },
      "reason": "No evidence available"
    }
  ]
}
```

**Required fields per rule:**
- `id` - unique identifier
- `enabled` - boolean toggle
- `priority` - numeric (higher = more important among same specificity)
- `conditions` - object with field matches
- `action` - structured action object

**Optional fields:**
- `reason` - human-readable explanation

---

### DECISION-6: Condition Semantics (Intentionally Simple)

**Status:** ACCEPTED  
**Rationale:** Explainable, no Boolean logic explosion  

**Supported in Phase 5:**
- Equality match: `"query_type": "exact_fact"`
- List membership: `"query_type": ["comparison", "multi_hop"]`

**NOT supported in Phase 5:**
- Arbitrary Boolean logic trees (and/or/not nesting)
- Regex patterns
- Numeric comparison operators (`>`, `<`, `>=`)
- Negation

**Rationale:** Rules should be explainable without a truth table.

---

### DECISION-7: Fallback Strategy (Layered)

**Status:** ACCEPTED  
**Rationale:** Extends proven Phase 3/4 behavior rather than replacing it  

**Fallback Layer 1: Confidence-band default**

If no contextual rule matches → use `routing_defaults.by_confidence_band`

This preserves the proven Phase 4 control loop.

**Fallback Layer 2: Hard safety**

If routing context is invalid/incomplete → route to `cautious` or `abstain`

| Scenario | Fallback |
|----------|----------|
| Missing confidence_band | cautious + log warning |
| Impossible context (e.g., high confidence + EMPTY state) | abstain + log error |
| Invalid enum values | cautious + log error |

**Why this matters:**
Without explicit fallback, contextual routing becomes brittle. With confidence-band fallback, Phase 5 is an **extension** not a **replacement**.

---

## 3. Routing Context Schema

**Normalized routing context (passed to engine):**

```python
@dataclass
class RoutingContext:
    # Core dimensions
    query_type: str                    # exact_fact, comparison, multi_hop, ambiguous, summarization, other
    retrieval_state: str               # SOLID, FRAGILE, CONFLICTED, EMPTY
    confidence_band: str               # high, medium, low, insufficient
    
    # Evidence shape (categorical bands)
    evidence_shape: Dict[str, str]     # coverage_band, agreement_band, spread_band
    
    # Budget constraints (for post-routing guardrail)
    effort_budget: str                 # low, medium, high
```

**Context validation:**
- All enum values must be from known set
- Required fields: query_type, retrieval_state, confidence_band
- Optional fields: evidence_shape bands, effort_budget

---

## 4. Telemetry Fields (Phase 5 Additions)

Every trace must include:

```json
{
  "query_type": "exact_fact",
  "retrieval_state": "SOLID",
  "evidence_shape": {
    "coverage_band": "high",
    "agreement_band": "high"
  },
  "effort_budget": "low",
  "matched_rule_id": "exact_fact_solid_high",
  "matched_rule_priority": 100,
  "matched_rule_specificity": 3,
  "fallback_used": false,
  "fallback_reason": null
}
```

**Critical for replay:**
- `matched_rule_id` - which rule was applied
- `fallback_used` - whether fallback was triggered
- `evidence_shape` - shape at routing time

---

## 5. Rule Engine Interface Contract

**Input:**
```python
route(
    context: RoutingContext,
    rules: List[RoutingRule],
    defaults: RoutingDefaults
) -> RoutingDecision
```

**Output:**
```python
@dataclass
class RoutingDecision:
    execution_path: str
    matched_rule_id: Optional[str]
    matched_rule_priority: Optional[int]
    matched_rule_specificity: Optional[int]
    fallback_used: bool
    fallback_reason: Optional[str]
    action: Dict[str, Any]  # Full action object
```

---

## 6. Example Rule Set (Reference)

### Fast Path Rules

| Rule ID | Conditions | Action | Priority |
|---------|-----------|--------|----------|
| exact_fact_solid_high | query_type=exact_fact, retrieval_state=SOLID, confidence_band=high | fast | 100 |
| exact_fact_solid_medium | query_type=exact_fact, retrieval_state=SOLID, confidence_band=medium | fast | 90 |

### Cautious Path Rules

| Rule ID | Conditions | Action | Priority |
|---------|-----------|--------|----------|
| fragile_guardrail | retrieval_state=FRAGILE | cautious + expand + rerank | 200 |
| conflicted_guardrail | retrieval_state=CONFLICTED | cautious + expand + rerank | 200 |
| low_confidence | confidence_band=low | cautious | 150 |

### Abstain Rules

| Rule ID | Conditions | Action | Priority |
|---------|-----------|--------|----------|
| empty_abstain | retrieval_state=EMPTY | abstain | 300 |
| insufficient_confidence | confidence_band=insufficient | abstain | 250 |

### Comparison Rules

| Rule ID | Conditions | Action | Priority |
|---------|-----------|--------|----------|
| comparison_solid | query_type=comparison, retrieval_state=SOLID | standard | 100 |
| comparison_fragile | query_type=comparison, retrieval_state=FRAGILE | cautious | 200 |

---

## 7. Design Constraints

### Performance
- Rule evaluation must be O(n) where n = number of rules
- Typical deployment: < 50 rules
- Target: < 1ms evaluation time

### Memory
- Rules loaded once at startup
- Context created per-request
- No caching of routing decisions (must be deterministic per-request)

### Safety
- All rules can be disabled via `enabled: false`
- Fallback always produces valid decision
- Invalid rules logged and skipped (don't crash engine)

---

## 8. Decisions Confirmed (2026-03-08)

### Q1: Query Type Taxonomy ✅ CONFIRMED

**Locked taxonomy for Phase 5:**

| Type | Definition |
|------|------------|
| `exact_fact` | One concrete answer expected, usually short and specific |
| `comparison` | User asks to compare entities, options, time periods, or alternatives |
| `multi_hop` | Answer requires combining multiple facts or reasoning across items |
| `ambiguous` | Query intent is unclear, underspecified, or could refer to multiple targets |
| `summarization` | User wants a synthesis, overview, or condensation of retrieved items |
| `other` | Fallback when classifier confidence is low or no category fits |

**Constraint:** Single-label classification only (not multi-label).

**Rationale:** `other` is clearer as catch-all than `general`, which sounds like a real semantic class.

### Q2: Evidence Shape Dimensions ✅ CONFIRMED

**Locked dimensions:**

| Dimension | Bands | Meaning |
|-----------|-------|---------|
| `coverage_band` | `high \| medium \| low` | How well retrieved evidence covers query needs |
| `agreement_band` | `high \| medium \| low` | How consistent top evidence items are with each other |
| `spread_band` | `narrow \| medium \| wide` | How concentrated or diffuse retrieval scores are |

**Configurable thresholds (provisional defaults):**

```yaml
coverage_band:
  high: ≥ 0.80
  medium: ≥ 0.50 and < 0.80
  low: < 0.50

agreement_band:
  high: ≥ 0.75
  medium: ≥ 0.45 and < 0.75
  low: < 0.45

spread_band:
  # Computed from normalized dispersion metric
  # Thresholds configurable, not hardcoded in design
```

**Phase 5 rule:** Support all three dimensions, but initial rule set may only use `coverage_band` and `agreement_band`, with `spread_band` logged for telemetry.

### Q3: Effort Budget ✅ CONFIRMED

**Approach:** Post-routing constraint layer (NOT a rule condition field).

**Flow:**
```
1. Classify routing context
2. Select route from contextual rules
3. Apply effort-budget guardrail
4. Downgrade if route exceeds budget
5. Log downgrade reason
```

**Budget levels:**
- `low` → max `standard`
- `medium` → max `cautious`
- `high` → no downgrade

**Safety constraint:** Budget is operational cap, not truth override.
- ✅ May downgrade `cautious` → `standard` (if safety model allows)
- ❌ Must NOT upgrade `abstain` → `standard`
- ❌ Must NOT override insufficient evidence

**Telemetry fields when budget override applied:**
```json
{
  "budget_override_applied": true,
  "requested_execution_path": "cautious",
  "final_execution_path": "standard",
  "budget_reason": "effort_cap_standard"
}
```

**Rationale:** Avoids rule explosion (exact_fact + SOLID + high + low_budget, etc.). Keeps rule engine focused on epistemic conditions.

---

## 9. Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-08 | Declarative rule-table engine | Prevents conditional explosion, auditability |
| 2026-03-08 | Specificity > Priority precedence | Intuitive, prevents shadowing |
| 2026-03-08 | Structured action objects | Future-proof, stage directives |
| 2026-03-08 | Simple condition semantics | Explainability |
| 2026-03-08 | Layered fallback | Extends proven Phase 4 behavior |
| 2026-03-08 | Categorical bands in router | Stability, readability |
| 2026-03-08 | Query type taxonomy (6 types) | Minimal focused set, single-label |
| 2026-03-08 | Evidence shape (3 dimensions) | Coverage, agreement, spread |
| 2026-03-08 | Effort budget as constraint | Avoids rule combinatorial explosion |

---

## 10. Sign-Off

This document locks Phase 5 architectural decisions. All open questions confirmed.

✅ **Q1: Query type taxonomy confirmed**  
✅ **Q2: Evidence shape dimensions confirmed**  
✅ **Q3: Effort budget approach confirmed**

**Status:** READY FOR IMPLEMENTATION PLANNING

**Next step:** Create 5-1-PLAN.md, 5-2-PLAN.md, 5-3-PLAN.md based on these decisions.

---

*Context document created: 2026-03-08*
*Decisions confirmed: 2026-03-08*

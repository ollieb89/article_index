# Wave 6: Response Format Implementation

## Abstention Response Builder

Added `build_abstention_response()` function in `api/app.py` that returns structured responses for insufficient-confidence queries:

```python
{
    "status": "insufficient_evidence",
    "confidence_band": "insufficient",
    "message": "I don't have enough reliable evidence in the retrieved material to answer that confidently.",
    "metadata": {
        "confidence_score": 0.382,
        "retrieval_attempted": true,
        "suggestion": "Try rephrasing your question or providing more context."
    }
}
```

Key features:
- Reliable client detection via `status: "insufficient_evidence"`
- Confidence score included for transparency
- Retrieval attempt tracking
- User-friendly suggestion for query refinement

## Medium-Confidence Prompt Template

Added `RAG_MEDIUM_CONFIDENCE_PROMPT` to `api/app.py` for band="medium" routing:

```
You are a helpful assistant. Answer the question based primarily on the provided context.

**Guidelines:**
- Base your answer on the retrieved sources
- Acknowledge when evidence is from multiple sources or comes from different perspectives
- Use phrases like "Based on the available sources..." or "The material suggests..."
- When evidence is limited, indicate the constraint
- Cite sources where appropriate
```

Key features:
- Light hedging compared to conservative template
- Encourages source attribution
- Allows for multiple-perspective discussions
- Signals uncertainty appropriately to users

## Integration

Both features are integrated into the Phase 2 execution path routing:

- **Fast path (high confidence)**: Uses direct `RAG_PROMPT_TEMPLATE` (assertive, no hedging)
- **Standard path (medium confidence)**: Uses `RAG_MEDIUM_CONFIDENCE_PROMPT` (light hedging)
- **Cautious path (low confidence)**: Uses `RAG_CONSERVATIVE_PROMPT_TEMPLATE` (strong hedging)
- **Abstain path (insufficient)**: Uses `build_abstention_response()` function

By routing confidence bands to distinct response templates, the system communicates appropriate levels of certainty to users and ensures answers are calibrated to evidence quality.

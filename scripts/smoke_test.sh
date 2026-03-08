#!/bin/bash
# Smoke test: verifies health, create article, list, search, and RAG.
# Run after rebuild to confirm the stack works.
# Usage: ./scripts/smoke_test.sh [API_BASE]
# Example: API_BASE=http://localhost:8001 ./scripts/smoke_test.sh

set -e

API_BASE="${1:-${API_BASE:-http://localhost:8001}}"
API_KEY="${API_KEY:-change-me-long-random}"

echo "Smoke test: $API_BASE"
echo "---"

# 1. Health
echo "1. Health..."
HEALTH=$(curl -sf "$API_BASE/health")
echo "$HEALTH" | jq .
STATUS=$(echo "$HEALTH" | jq -r .status)
if [ "$STATUS" != "healthy" ]; then
  echo "FAIL: health status is $STATUS"
  exit 1
fi
echo "OK"
echo ""

# 2. Create article (sync)
echo "2. Create article..."
ARTICLE=$(curl -sf -X POST "$API_BASE/articles/" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "title": "Smoke Test Article",
    "content": "This is a smoke test. Machine learning and neural networks are key AI concepts."
  }')
echo "$ARTICLE" | jq .
DOC_ID=$(echo "$ARTICLE" | jq -r .document_id)
if [ "$DOC_ID" = "null" ] || [ -z "$DOC_ID" ]; then
  echo "FAIL: no document_id in response"
  exit 1
fi
echo "OK (document_id=$DOC_ID)"
echo ""

# 3. List articles
echo "3. List articles..."
LIST=$(curl -sf "$API_BASE/articles/")
echo "$LIST" | jq .
COUNT=$(echo "$LIST" | jq 'length')
if [ "$COUNT" -lt 1 ]; then
  echo "FAIL: expected at least 1 article"
  exit 1
fi
echo "OK ($COUNT articles)"
echo ""

# 4. Search
echo "4. Search..."
SEARCH=$(curl -sf -X POST "$API_BASE/search" \
  -H "Content-Type: application/json" \
  -d '{"query": "neural networks", "limit": 3}')
echo "$SEARCH" | jq .
SEARCH_COUNT=$(echo "$SEARCH" | jq -r .count)
if [ "$SEARCH_COUNT" = "null" ]; then
  echo "FAIL: no search results"
  exit 1
fi
echo "OK"
echo ""

# 5. RAG
echo "5. RAG..."
RAG=$(curl -sf -X POST "$API_BASE/rag" \
  -H "Content-Type: application/json" \
  -d '{"question": "What is machine learning?", "context_limit": 3}')
echo "$RAG" | jq .
ANSWER=$(echo "$RAG" | jq -r .answer)
if [ -z "$ANSWER" ] || [ "$ANSWER" = "null" ]; then
  echo "FAIL: no RAG answer"
  exit 1
fi
echo "OK"
echo ""

echo "All smoke tests passed."

#!/bin/bash

# Test script for Article Index API

set -e

API_BASE="http://localhost:999"

echo "🚀 Testing Article Index API..."

# 1. Health check
echo "1. Health check..."
curl -s "$API_BASE/health" | jq .

# 2. Create a test article
echo -e "\n2. Creating test article..."
ARTICLE_RESPONSE=$(curl -s -X POST "$API_BASE/articles/" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Introduction to Machine Learning",
    "content": "Machine learning is a subset of artificial intelligence that enables systems to learn and improve from experience without being explicitly programmed. It focuses on developing computer programs that can access data and use it to learn for themselves. The process of learning begins with observations or data, such as examples, direct experience, or instruction, in order to look for patterns in data and make better decisions in the future based on the examples that we provide. Machine learning algorithms build a mathematical model based on sample data, known as training data, in order to make predictions or decisions without being explicitly programmed to do so. Machine learning is closely related to computational statistics, which focuses on making predictions using computers. The study of mathematical optimization delivers methods, theory and application domains to the field of machine learning."
  }')

echo "$ARTICLE_RESPONSE" | jq .
ARTICLE_ID=$(echo "$ARTICLE_RESPONSE" | jq -r .document_id)

# 3. Create another article
echo -e "\n3. Creating second test article..."
curl -s -X POST "$API_BASE/articles/" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Deep Learning and Neural Networks",
    "content": "Deep learning is part of a broader family of machine learning methods based on artificial neural networks. Neural networks are computing systems vaguely inspired by the biological neural networks that constitute animal brains. Such systems learn to perform tasks by considering examples, generally without being programmed with task-specific rules. A neural network consists of a collection of connected units or nodes called artificial neurons, which loosely model the neurons in a biological brain. Each connection, like the synapses in a biological brain, can transmit a signal to other neurons. An artificial neuron that receives a signal then processes it and can signal neurons connected to it. The signal at a connection is a real number, and the output of each neuron is computed by some non-linear function of the sum of its inputs. The connections are called edges. Neurons and edges typically have a weight that adjusts as learning proceeds."
  }' | jq .

# 4. List articles
echo -e "\n4. Listing articles..."
curl -s "$API_BASE/articles/" | jq .

# 5. Search for similar content
echo -e "\n5. Searching for 'neural networks'..."
curl -s -X POST "$API_BASE/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "neural networks",
    "limit": 3,
    "search_type": "chunks"
  }' | jq .

# 6. Search for documents
echo -e "\n6. Searching for documents about 'artificial intelligence'..."
curl -s -X POST "$API_BASE/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "artificial intelligence",
    "limit": 5,
    "search_type": "documents"
  }' | jq .

# 7. RAG question
echo -e "\n7. Asking RAG question: 'What is machine learning?'..."
curl -s -X POST "$API_BASE/rag" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What is machine learning?",
    "context_limit": 3,
    "similarity_threshold": 0.5
  }' | jq .

# 8. Another RAG question
echo -e "\n8. Asking RAG question: 'How do neural networks work?'..."
curl -s -X POST "$API_BASE/rag" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "How do neural networks work?",
    "context_limit": 3,
    "similarity_threshold": 0.5
  }' | jq .

# 9. Get specific article
echo -e "\n9. Getting article $ARTICLE_ID..."
curl -s "$API_BASE/articles/$ARTICLE_ID" | jq .

# 10. Get stats
echo -e "\n10. Database stats..."
curl -s "$API_BASE/stats" | jq .

echo -e "\n✅ All tests completed!"

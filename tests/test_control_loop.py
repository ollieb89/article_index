import sys
import os
from unittest.mock import MagicMock, AsyncMock, patch

# Mock DatabaseManager and other side-effect-heavy modules BEFORE they are imported by api.app
mock_db_manager = MagicMock()
mock_doc_repo = MagicMock()
sys.modules['shared.database'] = MagicMock(
    db_manager=mock_db_manager,
    document_repo=mock_doc_repo,
    get_db_connection=MagicMock()
)
sys.modules['shared.processor'] = MagicMock()
sys.modules['shared.celery_client'] = MagicMock()
sys.modules['auth'] = MagicMock()
sys.modules['shared.url_ingestion'] = MagicMock()

# Inject project root for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
from fastapi import Request
from shared.evidence_scorer import ConfidenceBand, ConfidenceScore
from shared.query_transformer import TransformMode
from shared.rerank_policy import RerankMode

from api.app import _rag_hybrid, RAG_ABSTAIN_RESPONSE

class MockRAGQuery:
    def __init__(self, question, context_limit=5, model=None):
        self.question = question
        self.context_limit = context_limit
        self.model = model

@pytest.fixture
def mock_request():
    request = MagicMock(spec=Request)
    request.app = MagicMock()
    
    # Mock states
    request.app.state.hybrid_retriever = AsyncMock()
    request.app.state.context_builder = MagicMock()
    request.app.state.evidence_scorer = MagicMock()
    request.app.state.query_transformer = MagicMock()
    request.app.state.reranker = MagicMock()
    
    return request

@pytest.mark.asyncio
async def test_control_loop_insufficient_abstains(mock_request):
    """Verify INSUFFICIENT confidence leads to early abstention."""
    query = MockRAGQuery("What is the meaning of life?")
    
    # Mock retrieval to return something
    mock_request.app.state.hybrid_retriever.retrieve.return_value = [{"content": "junk", "document_id": 1}]
    
    # Mock scorer to return INSUFFICIENT
    mock_request.app.state.evidence_scorer.score_evidence.return_value = ConfidenceScore(
        score=0.1,
        band="insufficient",
        evidence_strength="none",
        coverage_estimate=0.0
    )
    
    response = await _rag_hybrid(query, mock_request)
    
    assert response["answer"] == RAG_ABSTAIN_RESPONSE
    assert "abstention" in response["control_actions"]
    assert response["execution_path"] == "abstention"
    assert response["confidence"]["band"] == "insufficient"

@pytest.mark.asyncio
async def test_control_loop_medium_triggers_expansion(mock_request):
    """Verify MEDIUM confidence triggers expanded retrieval/transformation."""
    query = MockRAGQuery("Explain hybrid search")
    
    # Initial retrieval chunks
    initial_chunks = [{"content": "chunk1", "id": 1}]
    expanded_chunks = [{"content": "chunk1", "id": 1}, {"content": "chunk2", "id": 2}]
    
    mock_request.app.state.hybrid_retriever.retrieve.return_value = initial_chunks
    
    # Scorer logic: first call medium, second call high
    mock_request.app.state.evidence_scorer.score_evidence.side_effect = [
        ConfidenceScore(score=0.6, band="medium", evidence_strength="moderate", coverage_estimate=0.5),
        ConfidenceScore(score=0.8, band="high", evidence_strength="strong", coverage_estimate=0.9)
    ]
    
    # Mock Transformer
    mock_request.app.state.query_transformer.mode = TransformMode.SELECTIVE
    mock_request.app.state.hybrid_retriever.retrieve_with_transform.return_value = (expanded_chunks, MagicMock(), {})
    
    # Context builder and Ollama
    mock_request.app.state.context_builder.build_context.return_value = {"context": "ctx", "sources": []}
    
    with patch('api.app.OllamaClient') as MockOllama:
        mock_ollama = MockOllama.return_value
        mock_ollama.generate_embedding = AsyncMock(return_value=[0.1]*1536)
        mock_ollama.generate_response = AsyncMock(return_value="Expanded Answer")
        
        response = await _rag_hybrid(query, mock_request)
        
        # Verify expand calls
        assert "expanded_retrieval" in response["control_actions"]
        assert "query_transformation" in response["control_actions"]
        assert response["execution_path"] == "expanded_retrieval"
        assert response["confidence"]["band"] == "high" # Post-expansion band
        assert response["answer"] == "Expanded Answer"

@pytest.mark.asyncio
async def test_control_loop_low_uses_conservative_prompt(mock_request):
    """Verify LOW confidence uses the conservative prompt template."""
    query = MockRAGQuery("Risky question")
    
    mock_request.app.state.hybrid_retriever.retrieve.return_value = [{"content": "weak info", "id": 1}]
    
    # Mock scorer to return LOW
    mock_request.app.state.evidence_scorer.score_evidence.return_value = ConfidenceScore(
        score=0.35,
        band="low",
        evidence_strength="weak",
        coverage_estimate=0.3
    )
    
    mock_request.app.state.context_builder.build_context.return_value = {"context": "ctx", "sources": []}
    
    with patch('api.app.OllamaClient') as MockOllama:
        mock_ollama = MockOllama.return_value
        mock_ollama.generate_embedding = AsyncMock(return_value=[0.1]*1536)
        mock_ollama.generate_response = AsyncMock(return_value="Conservative Answer")
        
        response = await _rag_hybrid(query, mock_request)
        
        assert "conservative_prompt" in response["control_actions"]
        assert response["execution_path"] == "conservative_generation"
        assert response["answer"] == "Conservative Answer"
        
        # Check if conservative prompt was used
        from api.app import RAG_CONSERVATIVE_PROMPT_TEMPLATE
        mock_ollama.generate_response.assert_called_once()
        args, kwargs = mock_ollama.generate_response.call_args
        assert kwargs['prompt'].startswith("You are a highly cautious assistant.") # Start of conservative prompt

@pytest.mark.asyncio
async def test_control_loop_high_proceeds_normally(mock_request):
    """Verify HIGH confidence proceeds without extra actions."""
    query = MockRAGQuery("Perfect question")
    
    mock_request.app.state.hybrid_retriever.retrieve.return_value = [{"content": "great info", "id": 1}]
    
    # Mock scorer to return HIGH
    mock_request.app.state.evidence_scorer.score_evidence.return_value = ConfidenceScore(
        score=0.9,
        band="high",
        evidence_strength="strong",
        coverage_estimate=0.95
    )
    
    mock_request.app.state.context_builder.build_context.return_value = {"context": "ctx", "sources": []}
    
    with patch('api.app.OllamaClient') as MockOllama:
        mock_ollama = MockOllama.return_value
        mock_ollama.generate_embedding = AsyncMock(return_value=[0.1]*1536)
        mock_ollama.generate_response = AsyncMock(return_value="Normal Answer")
        
        response = await _rag_hybrid(query, mock_request)
        
        assert response["control_actions"] == []
        assert response["execution_path"] == "standard_generation"
        assert response["answer"] == "Normal Answer"

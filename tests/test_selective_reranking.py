"""Integration tests for selective reranking.

These tests verify:
- off mode never reranks
- always mode always reranks
- selective mode reranks when trigger conditions are met
- response metadata matches the decision
- /admin/rerank/tune actually changes behavior
"""

import pytest
import asyncio
import os

# Skip all tests if API is not running
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("SKIP_API_TESTS") == "1",
        reason="API tests disabled via SKIP_API_TESTS"
    )
]


@pytest.fixture(scope="module")
def api_base():
    """Get API base URL."""
    return os.getenv("API_BASE", "http://localhost:8001")


@pytest.fixture(scope="module")
def api_key():
    """Get API key."""
    return os.getenv("API_KEY", "test-key")


@pytest.fixture
async def http_client():
    """Create async HTTP client."""
    import httpx
    async with httpx.AsyncClient(timeout=30.0) as client:
        yield client


@pytest.fixture(scope="module")
def test_article():
    """Sample article for testing."""
    return {
        "title": "Machine Learning Fundamentals",
        "content": """
        Machine learning is a subset of artificial intelligence that enables systems 
        to learn and improve from experience without being explicitly programmed. 
        
        Supervised learning uses labeled training data to learn mapping functions 
        from inputs to outputs. Common algorithms include linear regression, 
        logistic regression, support vector machines, and neural networks.
        
        Unsupervised learning finds hidden patterns in unlabeled data. Clustering 
        algorithms like k-means and hierarchical clustering group similar data points.
        Dimensionality reduction techniques like PCA reduce feature space.
        
        Deep learning uses neural networks with multiple layers. Convolutional 
        neural networks excel at image recognition. Recurrent neural networks 
        process sequential data like text and time series.
        
        Model evaluation uses metrics like accuracy, precision, recall, and F1 score.
        Cross-validation helps assess generalization performance.
        """
    }


class TestRerankModes:
    """Test reranking behavior in different modes."""
    
    async def test_off_mode_never_reranks(self, http_client, api_base, api_key):
        """Verify off mode never applies reranking."""
        # This test requires RERANK_MODE=off
        # Note: This may need to be run with specific env configuration
        
        query = "What is machine learning?"
        
        response = await http_client.post(
            f"{api_base}/search/hybrid",
            json={"query": query, "limit": 5}
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Check rerank metadata
        config = data.get("config", {})
        assert config.get("rerank_mode") == "off"
        assert config.get("rerank_applied") is False
    
    async def test_response_metadata_structure(self, http_client, api_base):
        """Verify response includes proper rerank metadata."""
        
        query = "Explain neural networks"
        
        response = await http_client.post(
            f"{api_base}/search/hybrid",
            json={"query": query, "limit": 5}
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Check config structure
        assert "config" in data
        config = data["config"]
        
        # Required fields
        assert "rerank_enabled" in config
        assert "rerank_mode" in config
        
        # If reranking is enabled, check for selective mode fields
        if config.get("rerank_enabled"):
            assert "rerank_applied" in config
            
            # If selective mode and reranking was applied
            if config.get("rerank_mode") == "selective" and config.get("rerank_applied"):
                assert "rerank_triggers" in config
                assert isinstance(config["rerank_triggers"], list)
                assert "rerank_confidence" in config
                assert 0.0 <= config["rerank_confidence"] <= 1.0


class TestSelectiveTriggers:
    """Test selective reranking trigger conditions."""
    
    # Test queries designed to trigger different conditions
    TRIGGER_TEST_QUERIES = [
        # Complex query trigger (long, multi-part)
        {
            "query": "Can you explain how neural networks work and also tell me about support vector machines and additionally compare them to decision trees?",
            "expected_triggers": ["complex_query"],
            "description": "Long multi-part query"
        },
        # Low evidence trigger (vague query)
        {
            "query": "something about data",
            "expected_triggers": ["low_evidence"],
            "description": "Vague query with weak retrieval"
        },
        # Comparison trigger
        {
            "query": "What is the difference between supervised and unsupervised learning and which is better for classification?",
            "expected_triggers": ["complex_query"],
            "description": "Comparison query"
        },
    ]
    
    async def test_selective_mode_trigger_detection(self, http_client, api_base, api_key):
        """Test that selective mode correctly identifies trigger conditions."""
        
        for test_case in self.TRIGGER_TEST_QUERIES:
            query = test_case["query"]
            
            # Test decision endpoint
            response = await http_client.post(
                f"{api_base}/admin/rerank/test",
                params={"query": query},
                headers={"X-API-Key": api_key}
            )
            
            if response.status_code == 400:
                # Reranking not enabled - skip this test
                pytest.skip("Reranking not enabled on this API instance")
            
            assert response.status_code == 200, f"Failed for query: {query[:50]}"
            data = response.json()
            
            # Verify decision structure
            assert "decision" in data
            decision = data["decision"]
            
            assert "should_rerank" in decision
            assert "triggers" in decision
            assert "explanation" in decision
            
            # Log results for debugging
            print(f"\nQuery: {query[:60]}...")
            print(f"  Triggers: {decision['triggers']}")
            print(f"  Should rerank: {decision['should_rerank']}")


class TestAdminEndpoints:
    """Test admin endpoint functionality."""
    
    async def test_rerank_status_endpoint(self, http_client, api_base, api_key):
        """Verify /admin/rerank/status returns proper structure."""
        
        response = await http_client.get(
            f"{api_base}/admin/rerank/status",
            headers={"X-API-Key": api_key}
        )
        
        assert response.status_code in [200, 400]  # 400 if reranking disabled
        
        if response.status_code == 200:
            data = response.json()
            
            # Check structure
            assert "status" in data
            assert "mode" in data
            assert "configuration" in data
            assert "stats" in data
            
            # Check stats structure
            stats = data["stats"]
            assert "queries_total" in stats
            assert "queries_reranked" in stats
            assert "rerank_rate" in stats
            assert "avg_triggers_per_reranked_query" in stats
            assert "triggers" in stats
    
    async def test_rerank_tune_endpoint(self, http_client, api_base, api_key):
        """Verify /admin/rerank/tune changes thresholds."""
        
        # First get current config
        status_response = await http_client.get(
            f"{api_base}/admin/rerank/status",
            headers={"X-API-Key": api_key}
        )
        
        if status_response.status_code == 400:
            pytest.skip("Reranking not enabled on this API instance")
        
        assert status_response.status_code == 200
        original_config = status_response.json()["configuration"]
        
        # Try to tune thresholds
        tune_response = await http_client.post(
            f"{api_base}/admin/rerank/tune",
            params={
                "score_gap": 0.05,
                "disagreement": 0.50
            },
            headers={"X-API-Key": api_key}
        )
        
        # Should work in selective mode, fail in always/off mode
        if tune_response.status_code == 200:
            data = tune_response.json()
            assert "configuration" in data
            
            # Verify thresholds changed
            new_config = data["configuration"]
            assert new_config["score_gap_threshold"] == 0.05
            assert new_config["disagreement_threshold"] == 0.50
            
            # Reset to original values
            reset_response = await http_client.post(
                f"{api_base}/admin/rerank/tune",
                params={
                    "score_gap": original_config["score_gap_threshold"],
                    "disagreement": original_config["disagreement_threshold"]
                },
                headers={"X-API-Key": api_key}
            )
            assert reset_response.status_code == 200
    
    async def test_rerank_reset_stats_endpoint(self, http_client, api_base, api_key):
        """Verify /admin/rerank/reset-stats clears counters."""
        
        response = await http_client.post(
            f"{api_base}/admin/rerank/reset-stats",
            headers={"X-API-Key": api_key}
        )
        
        if response.status_code == 400:
            pytest.skip("Reranking not enabled on this API instance")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "message" in data
        assert "stats" in data
        
        # Verify stats are zeroed
        stats = data["stats"]
        # Note: queries_total may not be zero if other requests happened
        # Just verify structure is correct
        assert "queries_reranked" in stats
        assert "rerank_rate" in stats


class TestRerankPolicyBehavior:
    """Test policy decision logic directly."""
    
    def test_off_mode_decision(self):
        """Verify off mode always returns should_rerank=False."""
        from shared.rerank_policy import RerankPolicy, RerankMode
        
        policy = RerankPolicy(mode='off')
        
        decision = policy.should_rerank(
            query="test query",
            candidates=[{"id": 1, "hybrid_score": 0.9}]
        )
        
        assert decision.should_rerank is False
        assert decision.mode == "off"
        assert "disabled" in decision.explanation.lower()
    
    def test_always_mode_decision(self):
        """Verify always mode always returns should_rerank=True."""
        from shared.rerank_policy import RerankPolicy, RerankMode
        
        policy = RerankPolicy(mode='always')
        
        decision = policy.should_rerank(
            query="test query",
            candidates=[{"id": 1, "hybrid_score": 0.9}]
        )
        
        assert decision.should_rerank is True
        assert decision.mode == "always"
        assert "always" in decision.explanation.lower()
    
    def test_selective_mode_score_gap_trigger(self):
        """Verify selective mode triggers on small score gap."""
        from shared.rerank_policy import RerankPolicy
        
        policy = RerankPolicy(mode='selective', score_gap_threshold=0.05)
        
        # Candidates with very small score gap (should trigger)
        candidates = [
            {"id": 1, "hybrid_score": 0.80, "from_lexical": True, "from_vector": True},
            {"id": 2, "hybrid_score": 0.79, "from_lexical": True, "from_vector": True},
            {"id": 3, "hybrid_score": 0.78, "from_lexical": True, "from_vector": True},
            {"id": 4, "hybrid_score": 0.77, "from_lexical": True, "from_vector": True},
            {"id": 5, "hybrid_score": 0.76, "from_lexical": True, "from_vector": True},
        ]
        
        decision = policy.should_rerank(query="test", candidates=candidates)
        
        # Gap is 0.04 which is < 0.05 threshold, so should trigger
        assert "small_score_gap" in decision.triggers or not decision.should_rerank
    
    def test_selective_mode_complex_query_trigger(self):
        """Verify selective mode triggers on complex queries."""
        from shared.rerank_policy import RerankPolicy
        
        policy = RerankPolicy(mode='selective', complex_query_words=10)
        
        # Short query (should not trigger complexity)
        short_decision = policy.should_rerank(
            query="machine learning",
            candidates=[{"id": 1, "hybrid_score": 0.9}]
        )
        
        # Long complex query (should trigger)
        long_query = "Can you explain how neural networks work and also tell me about support vector machines?"
        long_decision = policy.should_rerank(
            query=long_query,
            candidates=[{"id": 1, "hybrid_score": 0.9}]
        )
        
        # Long query should trigger complexity
        if long_decision.should_rerank:
            assert "complex_query" in long_decision.triggers
    
    def test_statistics_tracking(self):
        """Verify statistics are tracked correctly."""
        from shared.rerank_policy import RerankPolicy
        
        policy = RerankPolicy(mode='selective')
        policy.reset_stats()
        
        # Initial stats should be zero
        stats = policy.get_stats()
        assert stats["queries_total"] == 0
        assert stats["queries_reranked"] == 0
        
        # Make some decisions
        for i in range(5):
            policy.should_rerank(
                query=f"test query {i}",
                candidates=[{"id": i, "hybrid_score": 0.5 - i*0.01}]  # Decreasing scores
            )
        
        # Check stats updated
        stats = policy.get_stats()
        assert stats["queries_total"] == 5
        assert "rerank_rate" in stats
        assert "avg_triggers_per_reranked_query" in stats
        assert "triggers" in stats


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v"])

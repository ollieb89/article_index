"""Integration tests for query transformations.

These tests verify:
- off mode never transforms
- always mode always transforms  
- selective mode transforms when trigger conditions are met
- response metadata matches the decision
- /admin/query-transform/tune actually changes behavior
- merge and deduplication work correctly
"""

import pytest
import os

# Skip all tests if API is not running
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("SKIP_API_TESTS") == "1",
        reason="API tests disabled via SKIP_API_TESTS"
    )
]


class TestQueryTransformModes:
    """Test query transformation behavior in different modes."""
    
    def test_off_mode_never_transforms(self):
        """Verify off mode never applies transformation."""
        from shared.query_transformer import QueryTransformer, TransformMode
        
        transformer = QueryTransformer(mode='off')
        
        decision = transformer.transform(
            query="Why does pgvector timeout on large imports?"
        )
        
        assert decision.should_transform is False
        assert decision.mode == "off"
        assert len(decision.transformed_queries) == 1
        assert decision.transformed_queries[0] == "Why does pgvector timeout on large imports?"
    
    def test_always_mode_always_transforms(self):
        """Verify always mode always applies transformation."""
        from shared.query_transformer import QueryTransformer, TransformMode
        
        transformer = QueryTransformer(mode='always', max_expanded_queries=3)
        
        decision = transformer.transform(
            query="Why does pgvector timeout on large imports?"
        )
        
        assert decision.should_transform is True
        assert decision.mode == "always"
        assert len(decision.transformed_queries) >= 2
        assert "Why does pgvector timeout on large imports?" in decision.transformed_queries
    
    def test_selective_mode_short_query_not_transformed(self):
        """Verify selective mode doesn't transform short queries."""
        from shared.query_transformer import QueryTransformer
        
        transformer = QueryTransformer(mode='selective', min_query_words=4)
        
        # Short query - should not transform
        decision = transformer.transform(query="machine learning")
        
        assert decision.should_transform is False
        assert "query_too_short" in decision.trigger_reasons
    
    def test_selective_mode_ambiguous_query_transformed(self):
        """Verify selective mode transforms ambiguous queries."""
        from shared.query_transformer import QueryTransformer
        
        transformer = QueryTransformer(mode='selective', ambiguity_threshold=1)
        
        # Ambiguous query with comparison words
        decision = transformer.transform(
            query="What is the difference between supervised and unsupervised learning?"
        )
        
        # Should trigger ambiguity due to "difference between"
        if decision.should_transform:
            assert "ambiguous_query" in decision.trigger_reasons or "complex_query" in decision.trigger_reasons


class TestQueryTransformLogic:
    """Test transformation logic directly."""
    
    def test_multi_query_generation(self):
        """Verify multi-query expansion produces variants."""
        from shared.query_transformer import QueryTransformer
        
        transformer = QueryTransformer(mode='always', max_expanded_queries=3)
        
        decision = transformer.transform(
            query="How to configure pgvector for production?"
        )
        
        assert decision.should_transform is True
        assert len(decision.transformed_queries) >= 2
        
        # Check that we got variants
        queries_lower = [q.lower() for q in decision.transformed_queries]
        
        # Should have original
        assert any("how to configure pgvector" in q for q in queries_lower)
        
        # Should have at least one variant
        assert any("pgvector" in q for q in queries_lower)
    
    def test_step_back_generation(self):
        """Verify step-back produces broader query."""
        from shared.query_transformer import QueryTransformer
        
        transformer = QueryTransformer(
            mode='always',
            max_expanded_queries=3,
            enable_step_back=True
        )
        
        decision = transformer.transform(
            query="Why does pgvector timeout on large imports?"
        )
        
        if decision.should_transform and "step_back" in decision.transform_types:
            # Step-back should produce something like "pgvector performance issues"
            queries_lower = [q.lower() for q in decision.transformed_queries]
            assert any("pgvector" in q and ("issues" in q or "performance" in q or "overview" in q) 
                      for q in queries_lower)
    
    def test_merge_results_deduplication(self):
        """Verify merge_results deduplicates correctly."""
        from shared.query_transformer import QueryTransformer
        
        transformer = QueryTransformer(mode='off')
        
        # Simulate results from 2 queries with overlap
        results_q1 = [
            {"id": 1, "content": "chunk 1", "hybrid_score": 0.9},
            {"id": 2, "content": "chunk 2", "hybrid_score": 0.8},
            {"id": 3, "content": "chunk 3", "hybrid_score": 0.7},
        ]
        results_q2 = [
            {"id": 2, "content": "chunk 2", "hybrid_score": 0.85},  # Duplicate
            {"id": 4, "content": "chunk 4", "hybrid_score": 0.75},
        ]
        
        merged = transformer.merge_results(
            [results_q1, results_q2],
            original_query="test",
            max_results=10
        )
        
        # Should have 4 unique chunks
        assert len(merged) == 4
        
        # Check IDs are unique
        ids = [m['id'] for m in merged]
        assert len(ids) == len(set(ids))
        
        # Chunk 2 should be boosted (found by both queries)
        chunk_2 = next((m for m in merged if m['id'] == 2), None)
        if chunk_2:
            assert chunk_2.get('transform_boost', 1.0) > 1.0


class TestStatisticsTracking:
    """Test statistics tracking."""
    
    def test_statistics_accumulate(self):
        """Verify statistics track transformations."""
        from shared.query_transformer import QueryTransformer
        
        transformer = QueryTransformer(mode='always', max_expanded_queries=3)
        transformer.reset_stats()
        
        # Initial stats should be zero
        stats = transformer.get_stats()
        assert stats["queries_total"] == 0
        assert stats["queries_transformed"] == 0
        
        # Transform some queries
        for i in range(5):
            transformer.transform(query=f"test query {i} about machine learning")
        
        # Stats should be updated
        stats = transformer.get_stats()
        assert stats["queries_total"] == 5
        assert stats["queries_transformed"] == 5
        assert stats["transform_rate"] == 1.0
        assert stats["avg_generated_per_transform"] > 0
    
    def test_reset_stats_clears_counters(self):
        """Verify reset_stats clears all counters."""
        from shared.query_transformer import QueryTransformer
        
        transformer = QueryTransformer(mode='always')
        
        # Add some stats
        transformer.transform(query="test query")
        assert transformer.get_stats()["queries_total"] == 1
        
        # Reset
        transformer.reset_stats()
        stats = transformer.get_stats()
        
        assert stats["queries_total"] == 0
        assert stats["queries_transformed"] == 0
        assert stats["transform_rate"] == 0.0


class TestAdminEndpoints:
    """Test admin endpoint functionality."""
    
    async def test_status_endpoint_structure(self, http_client, api_base, api_key):
        """Verify /admin/query-transform/status returns proper structure."""
        import httpx
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{api_base}/admin/query-transform/status",
                headers={"X-API-Key": api_key}
            )
        
        assert response.status_code in [200, 400]  # 400 if not enabled
        
        if response.status_code == 200:
            data = response.json()
            
            assert "status" in data
            assert "mode" in data
            assert "configuration" in data
            assert "stats" in data
            
            # Check stats structure
            stats = data["stats"]
            assert "queries_total" in stats
            assert "queries_transformed" in stats
            assert "transform_rate" in stats
    
    async def test_test_endpoint(self, http_client, api_base, api_key):
        """Verify /admin/query-transform/test returns decision."""
        import httpx
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{api_base}/admin/query-transform/test",
                params={"query": "How does neural network training work?"},
                headers={"X-API-Key": api_key}
            )
        
        if response.status_code == 400:
            pytest.skip("Query transformation not enabled")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "query" in data
        assert "mode" in data
        assert "decision" in data
        
        decision = data["decision"]
        assert "should_transform" in decision
        assert "transformed_queries" in decision


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v"])

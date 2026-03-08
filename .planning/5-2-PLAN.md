# Phase 5 Plan 2: Query Classification & Evidence Shape

<plan phase="5" plan="2">
  <overview>
    <phase_name>Contextual Policy Routing — Query Classification & Evidence Shape</phase_name>
    <goal>Implement query type classifier and evidence shape extraction with categorical bands</goal>
    <requirements>CTX-01 (query type), CTX-02 (evidence shape)</requirements>
    <waves>3-4 (Classifier, ShapeExtractor, banding logic)</waves>
  </overview>
  
  <dependencies>
    <complete>Plan 5-1: Core Rule Engine</complete>
    <requires>RoutingContext dataclass</requires>
  </dependencies>
  
  <tasks>
    <task type="auto" priority="1">
      <name>Implement QueryType enum and classifier</name>
      <files>shared/query_classifier.py (enhance)</files>
      <action>
        Enhance existing QueryClassifier to support Phase 5 taxonomy:
        
        Create QueryType enum:
        - EXACT_FACT
        - COMPARISON
        - MULTI_HOP
        - AMBIGUOUS
        - SUMMARIZATION
        - OTHER
        
        Enhance classify() method to return QueryType (not string).
        Add confidence score to classification result.
        
        Classification heuristics (rule-based for Phase 5):
        - EXACT_FACT: contains "what is", "who is", "when did", "where is"
        - COMPARISON: contains "compare", "difference between", "vs", "versus", "better than"
        - MULTI_HOP: contains "why did", "how did", "what caused" (causal chains)
        - SUMMARIZATION: contains "summarize", "overview", "explain", "describe"
        - AMBIGUOUS: very short (< 3 words) or contains "?" with vague terms
        - OTHER: default when confidence low
        
        Return tuple: (QueryType, confidence: float)
      </action>
      <verify>Unit test: classify sample queries, verify correct type assigned</verify>
      <done>QueryClassifier supports all 6 types with confidence scores</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Create EvidenceShape dataclass</name>
      <files>shared/evidence_shape.py (new)</files>
      <action>
        Create EvidenceShape dataclass with raw metrics:
        - coverage_score: float [0,1] - what fraction of query terms appear in evidence
        - agreement_score: float [0,1] - semantic similarity between top chunks
        - spread_score: float [0,1] - normalized dispersion of retrieval scores
        
        And categorical bands:
        - coverage_band: str (high/medium/low)
        - agreement_band: str (high/medium/low)
        - spread_band: str (narrow/medium/wide)
        
        Add to_bands_dict() that returns dict with band values.
        Add to_full_dict() that includes both raw scores and bands.
      </action>
      <verify>Unit test: create shape, bands computed, serialization works</verify>
      <done>EvidenceShape dataclass exists with all metrics and bands</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Implement EvidenceShapeExtractor with coverage metric</name>
      <files>shared/evidence_shape.py</files>
      <action>
        Add coverage_score calculation to EvidenceShapeExtractor:
        
        coverage_score = (unique query terms found in top-k chunks) / (total query terms)
        
        Implementation:
        - Tokenize query into terms (lowercase, remove stopwords)
        - For each chunk, check term presence
        - Count unique terms covered
        - Normalize by total query terms
        
        Thresholds (configurable, defaults):
        - high: ≥ 0.80
        - medium: ≥ 0.50
        - low: < 0.50
        
        Store thresholds in class config.
      </action>
      <verify>Unit test: coverage scores for various query/chunk combinations</verify>
      <done>Coverage metric implemented with banding</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Implement agreement metric</name>
      <files>shared/evidence_shape.py</files>
      <action>
        Add agreement_score calculation:
        
        agreement = average pairwise similarity between top-n chunks
        
        Implementation options:
        - Option A: Use existing embeddings, compute cosine similarity matrix
        - Option B: Use text overlap (Jaccard) as proxy
        
        For Phase 5, use Option A (embeddings already available):
        - Get embeddings for top-k chunks
        - Compute pairwise cosine similarities
        - Average upper triangle (excluding diagonal)
        
        Thresholds (configurable, defaults):
        - high: ≥ 0.75
        - medium: ≥ 0.45
        - low: < 0.45
      </action>
      <verify>Unit test: agreement for consistent vs conflicting evidence</verify>
      <done>Agreement metric implemented with banding</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Implement spread metric</name>
      <files>shared/evidence_shape.py</files>
      <action>
        Add spread_score calculation:
        
        spread = coefficient of variation of retrieval scores
        
        Implementation:
        - Get final_scores from top-k chunks
        - Compute standard deviation / mean
        - Normalize to [0,1] range (clip at reasonable max, e.g., 2.0)
        
        Thresholds (configurable, provisional):
        - narrow: < 0.3 (tightly clustered)
        - medium: 0.3 - 0.7
        - wide: > 0.7 (sharp dropoff)
        
        Note: These thresholds may need tuning based on real data.
        Log warning if spread calculation fails (e.g., all scores identical).
      </action>
      <verify>Unit test: spread for clustered vs dispersed scores</verify>
      <done>Spread metric implemented with banding</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Integrate with RoutingContext builder</name>
      <files>shared/routing_context.py</files>
      <action>
        Add factory method build_routing_context() that:
        
        1. Takes inputs:
           - query: str
           - query_type: QueryType (from classifier)
           - chunks: List[Dict] (retrieval results)
           - confidence_band: str (from EvidenceScorer)
           - retrieval_state: str (from RetrievalStateLabeler)
           - effort_budget: str (from policy or default)
        
        2. Creates EvidenceShape using EvidenceShapeExtractor
        
        3. Returns RoutingContext with all fields populated
        
        This is the glue between retrieval/evidence and the rule engine.
      </action>
      <verify>Integration test: build context from real query + chunks</verify>
      <done>RoutingContext can be built from pipeline components</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Create unit tests for query classification</name>
      <files>tests/test_query_classifier_phase5.py (new)</files>
      <action>
        Create test suite for all 6 query types:
        
        Test cases per type (2-3 examples each):
        - EXACT_FACT: "What is machine learning?", "Who invented Python?"
        - COMPARISON: "Compare Python and Java", "What's better, SQL or NoSQL?"
        - MULTI_HOP: "Why did the 2008 crisis happen?", "What caused WWI?"
        - SUMMARIZATION: "Summarize quantum computing", "Explain RAG"
        - AMBIGUOUS: "Apple?", "Python" (just the word)
        - OTHER: queries that don't fit above
        
        Assert correct type and reasonable confidence.
      </action>
      <verify>pytest tests/test_query_classifier_phase5.py -v passes</verify>
      <done>Query classification has test coverage</done>
    </task>
    
    <task type="auto" priority="1">
      <name>Create unit tests for evidence shape extraction</name>
      <files>tests/test_evidence_shape.py (new)</files>
      <action>
        Create test suite covering:
        - Coverage: query fully covered, partially covered, not covered
        - Agreement: chunks agree, chunks conflict, mixed
        - Spread: tightly clustered scores, dispersed scores
        - Band assignments: verify thresholds work correctly
        - Edge cases: empty chunks, single chunk, identical scores
        
        Use mock chunks with controlled content/scores.
      </action>
      <verify>pytest tests/test_evidence_shape.py -v passes</verify>
      <done>Evidence shape extraction has test coverage</done>
    </task>
    
    <task type="auto" priority="2">
      <name>Update PolicyTrace with Phase 5 fields</name>
      <files>shared/telemetry.py</files>
      <action>
        Add to PolicyTrace:
        - query_type: str
        - retrieval_state: str
        - evidence_shape: Dict[str, str] (bands only, not raw scores)
        - effort_budget: str
        - matched_rule_id: Optional[str]
        - matched_rule_priority: Optional[int]
        - matched_rule_specificity: Optional[int]
        - fallback_used: bool
        - budget_override_applied: bool
        - requested_execution_path: Optional[str]
        
        Update to_dict() to include all new fields.
        Update telemetry_schema_version to "1.1" for Phase 5.
      </action>
      <verify>Unit test: create trace with Phase 5 fields, serialize correctly</verify>
      <done>PolicyTrace supports all Phase 5 telemetry fields</done>
    </task>
    
    <task type="auto" priority="2">
      <name>Update telemetry logging in RAG pipeline</name>
      <files>api/app.py</files>
      <action>
        Update _rag_hybrid() to populate new PolicyTrace fields:
        - query_type from classifier
        - retrieval_state from state_labeler
        - evidence_shape from shape_extractor
        - effort_budget (from policy or default "medium")
        
        These fields are captured in the trace alongside existing Phase 4 fields.
      </action>
      <verify>Run RAG query, check telemetry has all Phase 5 fields</verify>
      <done>RAG pipeline logs full Phase 5 context</done>
    </task>
  </tasks>
</plan>

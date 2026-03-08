import logging
import re
from typing import List
from enum import Enum

logger = logging.getLogger(__name__)

class QueryType(str, Enum):
    EXACT_FACT = "exact_fact"
    COMPARISON = "comparison"
    SUMMARIZATION = "summarization"
    AMBIGUOUS = "ambiguous"
    MULTI_HOP = "multi_hop"
    PROCEDURAL = "procedural"
    LIKELY_NO_ANSWER = "likely_no_answer"
    UNKNOWN = "unknown"

class QueryClassifier:
    """Classifies user queries into operational types for contextual routing."""

    def __init__(self):
        # Patterns for different query types
        self.patterns = {
            QueryType.EXACT_FACT: [
                r"^(who|when|where|what is|how many|list|exactly)\b",
                r"\b(date|born|located|capital|height|weight|founder)\b"
            ],
            QueryType.COMPARISON: [
                r"\b(compare|versus|vs|difference between|better|worse|cheaper|faster|slower)\b",
                r"\b(compared to|relationship between)\b"
            ],
            QueryType.SUMMARIZATION: [
                r"\b(summarize|summary|overview|recap|tl;dr|main points|gist)\b",
                r"^(what are the|give me an|tell me about)\b"
            ],
            QueryType.PROCEDURAL: [
                r"^(how to|steps|process for|guide|tutorial|method to)\b",
                r"\b(instruction|workflow|recipe|procedure)\b"
            ],
            QueryType.MULTI_HOP: [
                r"\b(and|then|after|before|while)\b.*\b(who|what|where)\b",
                r"\b(connection between|impact of|consequence of)\b"
            ]
        }

    def classify(self, query: str) -> QueryType:
        """Classify a query string into a QueryType.
        
        Args:
            query: The user's question string.
            
        Returns:
            The detected QueryType.
        """
        if not query or len(query.strip()) == 0:
            return QueryType.UNKNOWN
            
        lowered_query = query.lower().strip()
        
        # 1. Check for Ambiguous (very short)
        words = lowered_query.split()
        if len(words) <= 2:
            return QueryType.AMBIGUOUS
            
        # 2. Check for Likely No Answer (out of scope patterns, if any)
        # For now, we'll leave this to the router/scorer, but could add negative patterns
        
        # 3. Pattern Matching
        for qtype, pattern_list in self.patterns.items():
            for pattern in pattern_list:
                if re.search(pattern, lowered_query):
                    # Special check for multi-hop vs others
                    if qtype == QueryType.MULTI_HOP and len(words) < 10:
                        continue # Multi-hop usually longer
                    return qtype
                    
        # 4. Fallback to general/unknown
        if "?" in query or any(w in lowered_query for w in ["why", "is", "can", "does"]):
            return QueryType.EXACT_FACT # Default fact-like
            
        return QueryType.UNKNOWN

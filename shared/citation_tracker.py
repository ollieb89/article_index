"""Citation tracking for answer provenance.

This module tracks which chunks and documents support generated answers,
enabling source-grounded responses and answer auditing.

Features:
- Chunk-to-answer citation mapping
- Document-level provenance
- Support ratio calculation
- Unsupported claim detection

Usage:
    from shared.citation_tracker import CitationTracker
    
    tracker = CitationTracker()
    
    # Track which chunks were used
    citations = tracker.track_citations(
        answer=generated_answer,
        chunks=retrieved_chunks
    )
    
    # Get citation report
    report = tracker.generate_report()
    
    # Check for unsupported claims
    if report.unsupported_ratio > 0.2:
        print("Warning: Answer has many unsupported claims")
"""

import logging
import re
from typing import List, Dict, Any, Optional, Set, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Citation:
    """Single citation mapping.
    
    Attributes:
        chunk_id: ID of the supporting chunk
        document_id: ID of the source document
        document_title: Title of the source document
        chunk_index: Position within document
        cited_text: Text from the chunk that supports the claim
        citation_number: Sequential citation number (for [1], [2], etc.)
    """
    chunk_id: int
    document_id: int
    document_title: str
    chunk_index: int
    cited_text: str
    citation_number: int
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'chunk_id': self.chunk_id,
            'document_id': self.document_id,
            'document_title': self.document_title,
            'chunk_index': self.chunk_index,
            'cited_text': self.cited_text[:200] if self.cited_text else "",  # Truncate
            'citation_number': self.citation_number
        }


@dataclass
class CitationReport:
    """Complete citation report for an answer.
    
    Attributes:
        citations: List of citations used
        citation_count: Total number of citations
        unique_documents: Number of unique source documents
        document_ids: List of document IDs cited
        supported_claim_ratio: Ratio of claims with citations
        unsupported_segments: Text segments without citations
        chunk_usage: How many chunks were actually cited
    """
    citations: List[Citation]
    citation_count: int
    unique_documents: int
    document_ids: List[int]
    supported_claim_ratio: float
    unsupported_segments: List[str]
    chunk_usage: Dict[int, bool]  # chunk_id -> was cited
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'citations': [c.to_dict() for c in self.citations],
            'citation_count': self.citation_count,
            'unique_documents': self.unique_documents,
            'document_ids': self.document_ids,
            'supported_claim_ratio': round(self.supported_claim_ratio, 3),
            'unsupported_segments': self.unsupported_segments[:5],  # Limit
            'chunks_cited': sum(1 for v in self.chunk_usage.values() if v),
            'chunks_unused': sum(1 for v in self.chunk_usage.values() if not v)
        }


class CitationTracker:
    """Track citations from retrieved chunks to generated answers.
    
    Maps answer text back to supporting chunks, enabling:
    - Source verification
    - Answer auditing
    - Unsupported claim detection
    - Provenance tracking
    
    Attributes:
        citation_format: Format for inline citations ([1], [2], etc.)
        min_overlap_threshold: Minimum text overlap to count as citation
    """
    
    def __init__(
        self,
        citation_format: str = "[{number}]",
        min_overlap_threshold: float = 0.3
    ):
        """Initialize the citation tracker.
        
        Args:
            citation_format: Format string for citation numbers
            min_overlap_threshold: Minimum text overlap for citation (0-1)
        """
        self.citation_format = citation_format
        self.min_overlap_threshold = min_overlap_threshold
        
        logger.info(
            f"CitationTracker initialized: format='{citation_format}', "
            f"min_overlap={min_overlap_threshold}"
        )
    
    def track_citations(
        self,
        answer: str,
        chunks: List[Dict[str, Any]],
        add_inline_citations: bool = True
    ) -> CitationReport:
        """Track which chunks support which parts of the answer.
        
        Args:
            answer: Generated answer text
            chunks: Retrieved chunks that were available
            add_inline_citations: Whether to insert [1], [2] into answer
            
        Returns:
            CitationReport with citation mappings
        """
        if not answer or not chunks:
            return CitationReport(
                citations=[],
                citation_count=0,
                unique_documents=0,
                document_ids=[],
                supported_claim_ratio=0.0,
                unsupported_segments=[],
                chunk_usage={}
            )
        
        citations = []
        citation_map = {}  # chunk_id -> citation_number
        next_citation_num = 1
        
        # Track which chunks were actually cited
        chunk_usage = {c.get('id'): False for c in chunks if c.get('id')}
        
        # Split answer into sentences/claims
        segments = self._segment_answer(answer)
        
        # For each segment, find supporting chunks
        for segment in segments:
            if not segment.strip():
                continue
            
            # Find best supporting chunk
            best_chunk = self._find_best_support(segment, chunks)
            
            if best_chunk:
                chunk_id = best_chunk.get('id')
                
                # Assign or reuse citation number
                if chunk_id not in citation_map:
                    citation_map[chunk_id] = next_citation_num
                    citation = Citation(
                        chunk_id=chunk_id,
                        document_id=best_chunk.get('document_id', 0),
                        document_title=best_chunk.get('title', 'Unknown'),
                        chunk_index=best_chunk.get('chunk_index', 0),
                        cited_text=segment,
                        citation_number=next_citation_num
                    )
                    citations.append(citation)
                    next_citation_num += 1
                
                # Mark chunk as used
                chunk_usage[chunk_id] = True
        
        # Calculate metrics
        unique_docs = set(c.document_id for c in citations)
        supported_ratio = len([s for s in segments if self._has_support(s, chunks)]) / len(segments) if segments else 0
        
        # Find unsupported segments
        unsupported = [
            s for s in segments 
            if not self._has_support(s, chunks) and len(s) > 20
        ]
        
        return CitationReport(
            citations=citations,
            citation_count=len(citations),
            unique_documents=len(unique_docs),
            document_ids=sorted(unique_docs),
            supported_claim_ratio=supported_ratio,
            unsupported_segments=unsupported,
            chunk_usage=chunk_usage
        )
    
    def add_citations_to_answer(
        self,
        answer: str,
        citations: List[Citation]
    ) -> str:
        """Add inline citation markers to answer text.
        
        Args:
            answer: Original answer text
            citations: Citation mappings
            
        Returns:
            Answer with [1], [2], etc. inserted
        """
        if not citations:
            return answer
        
        # Build citation marker map
        citation_by_chunk = {c.chunk_id: c.citation_number for c in citations}
        
        # This is a simplified implementation
        # In practice, you'd need more sophisticated text alignment
        # For now, append citations at the end
        
        citation_list = "\n\nSources:\n"
        for c in sorted(citations, key=lambda x: x.citation_number):
            citation_list += f"[{c.citation_number}] {c.document_title}\n"
        
        return answer + citation_list
    
    def validate_citations(
        self,
        citations: List[Citation],
        available_chunks: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Validate that citations reference actual retrieved chunks.
        
        Args:
            citations: Citations to validate
            available_chunks: Chunks that were available
            
        Returns:
            Validation report
        """
        available_ids = set(c.get('id') for c in available_chunks if c.get('id'))
        
        valid_citations = []
        invalid_citations = []
        
        for c in citations:
            if c.chunk_id in available_ids:
                valid_citations.append(c)
            else:
                invalid_citations.append(c)
        
        return {
            'valid_count': len(valid_citations),
            'invalid_count': len(invalid_citations),
            'valid_ratio': len(valid_citations) / len(citations) if citations else 0,
            'invalid_citations': [c.to_dict() for c in invalid_citations]
        }
    
    def _segment_answer(self, answer: str) -> List[str]:
        """Split answer into segments for citation tracking.
        
        Uses sentence segmentation.
        """
        # Simple sentence splitting
        # In production, use a proper NLP sentence tokenizer
        sentences = re.split(r'(?<=[.!?])\s+', answer)
        
        # Filter and clean
        segments = []
        for s in sentences:
            s = s.strip()
            if len(s) > 10:  # Skip very short fragments
                segments.append(s)
        
        return segments
    
    def _find_best_support(
        self,
        segment: str,
        chunks: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Find the chunk that best supports this answer segment."""
        best_chunk = None
        best_score = 0.0
        
        segment_words = set(self._tokenize(segment))
        
        for chunk in chunks:
            chunk_text = chunk.get('content', '')
            chunk_words = set(self._tokenize(chunk_text))
            
            # Calculate Jaccard similarity
            if not segment_words or not chunk_words:
                continue
            
            intersection = len(segment_words & chunk_words)
            union = len(segment_words | chunk_words)
            score = intersection / union if union > 0 else 0
            
            if score > best_score and score >= self.min_overlap_threshold:
                best_score = score
                best_chunk = chunk
        
        return best_chunk
    
    def _has_support(self, segment: str, chunks: List[Dict[str, Any]]) -> bool:
        """Check if this segment has any supporting chunk."""
        return self._find_best_support(segment, chunks) is not None
    
    def _tokenize(self, text: str) -> List[str]:
        """Simple tokenization for overlap calculation."""
        # Lowercase, remove punctuation, split
        text = re.sub(r'[^\w\s]', '', text.lower())
        return [w for w in text.split() if len(w) > 2]
    
    def generate_inline_citations(
        self,
        answer: str,
        chunks: List[Dict[str, Any]]
    ) -> Tuple[str, CitationReport]:
        """Generate answer with inline citations and full report.
        
        Args:
            answer: Original answer
            chunks: Supporting chunks
            
        Returns:
            Tuple of (annotated_answer, citation_report)
        """
        # Track citations
        report = self.track_citations(answer, chunks, add_inline_citations=True)
        
        # Build inline citation mapping
        chunk_to_citation = {c.chunk_id: c.citation_number for c in report.citations}
        
        # This is a simplified approach - append citation section
        annotated = self.add_citations_to_answer(answer, report.citations)
        
        return annotated, report
    
    def get_config(self) -> Dict[str, Any]:
        """Get current configuration."""
        return {
            'citation_format': self.citation_format,
            'min_overlap_threshold': self.min_overlap_threshold
        }

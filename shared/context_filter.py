"""Context filtering for high-quality evidence selection.

This module filters retrieved chunks to improve evidence density
and remove redundancy before passing to the LLM.

Filtering strategies:
- Near-duplicate removal (semantic similarity)
- Same-document chunk compression
- Low-score tail trimming
- Redundancy suppression
- Boilerplate detection

Usage:
    from shared.context_filter import ContextFilter
    
    filter = ContextFilter(
        mode='selective',
        dedup_threshold=0.85,
        max_chunks_per_doc=2
    )
    
    filtered = filter.filter_chunks(retrieved_chunks)
    
    # Filter metadata
    print(filter.get_stats())
"""

import logging
import re
from typing import List, Dict, Any, Optional, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class FilterMode(Enum):
    """Context filtering operation modes."""
    OFF = "off"           # No filtering
    ALWAYS = "always"     # Always apply all filters
    SELECTIVE = "selective"  # Apply based on context quality


@dataclass
class FilterResult:
    """Result of context filtering.
    
    Attributes:
        chunks: Filtered list of chunks
        filters_applied: List of filters that were applied
        removed_count: Number of chunks removed
        compression_ratio: Ratio of output to input chunks
        filter_metadata: Detailed filtering decisions
    """
    chunks: List[Dict[str, Any]]
    filters_applied: List[str]
    removed_count: int
    compression_ratio: float
    filter_metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            'chunks_kept': len(self.chunks),
            'chunks_removed': self.removed_count,
            'compression_ratio': round(self.compression_ratio, 2),
            'filters_applied': self.filters_applied,
            'metadata': self.filter_metadata
        }


class ContextFilter:
    """Filter retrieved chunks to improve evidence quality.
    
    Implements multiple filtering strategies:
    - Deduplication: Remove near-duplicate chunks
    - Per-document limit: Cap chunks from same source
    - Score threshold: Remove low-scoring tail
    - Redundancy: Suppress overlapping content
    - Boilerplate: Detect and remove generic text
    
    Attributes:
        mode: FilterMode (off, always, selective)
        dedup_threshold: Similarity threshold for dedup (default 0.85)
        max_chunks_per_doc: Max chunks from same document (default 2)
        min_score_threshold: Minimum score to keep (default 0.3)
        max_total_chunks: Hard limit on output chunks (default 8)
    """
    
    # Common boilerplate patterns to filter
    BOILERPLATE_PATTERNS = [
        r'^\s*table of contents\s*$',
        r'^\s*introduction\s*$',
        r'^\s*conclusion\s*$',
        r'^\s*references?\s*$',
        r'^\s*appendix\s*$',
        r'^\s*copyright\s+\d{4}',
        r'^\s*all rights reserved',
        r'^\s*last updated:',
        r'^\s*version \d+\.\d+',
        r'^\s*\d+\.\d+\.\d+',  # Semantic version at start
    ]
    
    def __init__(
        self,
        mode: str = 'off',
        dedup_threshold: float = 0.85,
        max_chunks_per_doc: int = 2,
        min_score_threshold: float = 0.3,
        max_total_chunks: int = 8,
        remove_boilerplate: bool = True,
        enable_compression: bool = True
    ):
        """Initialize the context filter.
        
        Args:
            mode: 'off', 'always', or 'selective'
            dedup_threshold: Similarity threshold for deduplication (0-1)
            max_chunks_per_doc: Maximum chunks per document
            min_score_threshold: Minimum score to keep chunk
            max_total_chunks: Hard limit on output chunks
            remove_boilerplate: Filter boilerplate text
            enable_compression: Compress adjacent chunks from same doc
        """
        try:
            self.mode = FilterMode(mode.lower())
        except ValueError:
            logger.warning(f"Invalid filter mode '{mode}', defaulting to 'off'")
            self.mode = FilterMode.OFF
        
        self.dedup_threshold = dedup_threshold
        self.max_chunks_per_doc = max_chunks_per_doc
        self.min_score_threshold = min_score_threshold
        self.max_total_chunks = max_total_chunks
        self.remove_boilerplate = remove_boilerplate
        self.enable_compression = enable_compression
        
        # Statistics tracking
        self._stats = {
            'chunks_in': 0,
            'chunks_out': 0,
            'filters_triggered': {
                'dedup': 0,
                'per_doc_limit': 0,
                'score_threshold': 0,
                'boilerplate': 0,
                'redundancy': 0,
            }
        }
        
        logger.info(
            f"ContextFilter initialized: mode={self.mode.value}, "
            f"dedup_threshold={dedup_threshold}, max_per_doc={max_chunks_per_doc}"
        )
    
    def _update_stats(self, chunks_in: int, chunks_out: int, filters_triggered: Dict[str, int]) -> None:
        """Update filtering statistics."""
        self._stats['chunks_in'] += chunks_in
        self._stats['chunks_out'] += chunks_out
        for filter_name, count in filters_triggered.items():
            self._stats['filters_triggered'][filter_name] += count
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current filtering statistics."""
        total_in = self._stats['chunks_in']
        total_out = self._stats['chunks_out']
        
        return {
            'chunks_in': total_in,
            'chunks_out': total_out,
            'chunks_removed': total_in - total_out,
            'compression_ratio': round(total_out / total_in, 2) if total_in > 0 else 0.0,
            'filters_triggered': self._stats['filters_triggered']
        }
    
    def reset_stats(self) -> None:
        """Reset all statistics."""
        self._stats = {
            'chunks_in': 0,
            'chunks_out': 0,
            'filters_triggered': {
                'dedup': 0,
                'per_doc_limit': 0,
                'score_threshold': 0,
                'boilerplate': 0,
                'redundancy': 0,
            }
        }
        logger.info("ContextFilter statistics reset")
    
    def filter_chunks(
        self,
        chunks: List[Dict[str, Any]],
        query: Optional[str] = None
    ) -> FilterResult:
        """Apply context filtering to retrieved chunks.
        
        Args:
            chunks: List of retrieved chunk dicts
            query: Original query (for context-aware filtering)
            
        Returns:
            FilterResult with filtered chunks and metadata
        """
        if not chunks:
            return FilterResult(
                chunks=[],
                filters_applied=[],
                removed_count=0,
                compression_ratio=1.0
            )
        
        # Mode-based early return
        if self.mode == FilterMode.OFF:
            return FilterResult(
                chunks=chunks[:self.max_total_chunks],
                filters_applied=[],
                removed_count=0,
                compression_ratio=1.0
            )
        
        # Apply filters
        filters_applied = []
        metadata = {
            'initial_count': len(chunks),
            'stages': []
        }
        filters_triggered = {k: 0 for k in self._stats['filters_triggered'].keys()}
        
        result = chunks[:]
        
        # Stage 1: Score threshold filter
        if self.min_score_threshold > 0:
            before = len(result)
            result = self._filter_by_score(result)
            removed = before - len(result)
            if removed > 0:
                filters_applied.append('score_threshold')
                filters_triggered['score_threshold'] = removed
                metadata['stages'].append({
                    'filter': 'score_threshold',
                    'removed': removed,
                    'remaining': len(result)
                })
        
        # Stage 2: Boilerplate filter
        if self.remove_boilerplate:
            before = len(result)
            result = self._filter_boilerplate(result)
            removed = before - len(result)
            if removed > 0:
                filters_applied.append('boilerplate')
                filters_triggered['boilerplate'] = removed
                metadata['stages'].append({
                    'filter': 'boilerplate',
                    'removed': removed,
                    'remaining': len(result)
                })
        
        # Stage 3: Deduplication (semantic similarity)
        before = len(result)
        result = self._deduplicate_chunks(result)
        removed = before - len(result)
        if removed > 0:
            filters_applied.append('dedup')
            filters_triggered['dedup'] = removed
            metadata['stages'].append({
                'filter': 'dedup',
                'removed': removed,
                'remaining': len(result)
            })
        
        # Stage 4: Per-document limit
        if self.max_chunks_per_doc > 0:
            before = len(result)
            result = self._limit_per_document(result)
            removed = before - len(result)
            if removed > 0:
                filters_applied.append('per_doc_limit')
                filters_triggered['per_doc_limit'] = removed
                metadata['stages'].append({
                    'filter': 'per_doc_limit',
                    'removed': removed,
                    'remaining': len(result)
                })
        
        # Stage 5: Redundancy suppression (content overlap)
        before = len(result)
        result = self._suppress_redundancy(result)
        removed = before - len(result)
        if removed > 0:
            filters_applied.append('redundancy')
            filters_triggered['redundancy'] = removed
            metadata['stages'].append({
                'filter': 'redundancy',
                'removed': removed,
                'remaining': len(result)
            })
        
        # Stage 6: Hard limit
        if len(result) > self.max_total_chunks:
            before = len(result)
            result = result[:self.max_total_chunks]
            removed = before - len(result)
            metadata['stages'].append({
                'filter': 'hard_limit',
                'removed': removed,
                'remaining': len(result)
            })
        
        # Update statistics
        self._update_stats(len(chunks), len(result), filters_triggered)
        
        # Calculate compression ratio
        compression = len(result) / len(chunks) if chunks else 1.0
        
        metadata['final_count'] = len(result)
        
        return FilterResult(
            chunks=result,
            filters_applied=filters_applied,
            removed_count=len(chunks) - len(result),
            compression_ratio=compression,
            filter_metadata=metadata
        )
    
    def _filter_by_score(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove chunks below score threshold."""
        return [
            c for c in chunks 
            if c.get('hybrid_score', 0.5) >= self.min_score_threshold
            or c.get('rrf_score', 0.5) >= self.min_score_threshold
        ]
    
    def _filter_boilerplate(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove chunks that match boilerplate patterns."""
        filtered = []
        for chunk in chunks:
            content = chunk.get('content', '')
            is_boilerplate = any(
                re.search(pattern, content, re.IGNORECASE)
                for pattern in self.BOILERPLATE_PATTERNS
            )
            if not is_boilerplate:
                filtered.append(chunk)
            else:
                logger.debug(f"Filtered boilerplate chunk: {content[:50]}...")
        return filtered
    
    def _deduplicate_chunks(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove near-duplicate chunks using content similarity.
        
        Uses simple token overlap for efficiency.
        """
        if not chunks:
            return []
        
        unique = []
        
        for chunk in chunks:
            content = chunk.get('content', '')
            is_duplicate = False
            
            for existing in unique:
                existing_content = existing.get('content', '')
                similarity = self._text_similarity(content, existing_content)
                
                if similarity >= self.dedup_threshold:
                    is_duplicate = True
                    # Keep the higher-scored chunk
                    if chunk.get('hybrid_score', 0) > existing.get('hybrid_score', 0):
                        unique[unique.index(existing)] = chunk
                    break
            
            if not is_duplicate:
                unique.append(chunk)
        
        return unique
    
    def _text_similarity(self, text1: str, text2: str) -> float:
        """Calculate simple token overlap similarity.
        
        Uses Jaccard similarity on word tokens.
        """
        # Normalize and tokenize
        words1 = set(re.findall(r'\w+', text1.lower()))
        words2 = set(re.findall(r'\w+', text2.lower()))
        
        if not words1 or not words2:
            return 0.0
        
        # Jaccard similarity
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        
        return intersection / union if union > 0 else 0.0
    
    def _limit_per_document(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Limit chunks per document, keeping highest-scored."""
        # Group by document
        doc_chunks: Dict[int, List[Dict]] = {}
        for chunk in chunks:
            doc_id = chunk.get('document_id')
            if doc_id not in doc_chunks:
                doc_chunks[doc_id] = []
            doc_chunks[doc_id].append(chunk)
        
        # Sort each group by score and take top N
        limited = []
        for doc_id, doc_chunk_list in doc_chunks.items():
            sorted_chunks = sorted(
                doc_chunk_list,
                key=lambda c: c.get('hybrid_score', 0),
                reverse=True
            )
            kept = sorted_chunks[:self.max_chunks_per_doc]
            
            # Mark chunks as compressed if we dropped some
            if len(sorted_chunks) > self.max_chunks_per_doc:
                for c in kept:
                    c['document_compressed'] = True
                    c['chunks_omitted'] = len(sorted_chunks) - self.max_chunks_per_doc
            
            limited.extend(kept)
        
        # Re-sort by score
        limited.sort(key=lambda c: c.get('hybrid_score', 0), reverse=True)
        return limited
    
    def _suppress_redundancy(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove redundant chunks that add little new information.
        
        Keeps chunks with unique content, removes high-overlap chunks.
        """
        if not chunks:
            return []
        
        # Keep first chunk always
        kept = [chunks[0]]
        
        for chunk in chunks[1:]:
            content = chunk.get('content', '')
            is_redundant = False
            
            for kept_chunk in kept:
                kept_content = kept_chunk.get('content', '')
                overlap = self._text_similarity(content, kept_content)
                
                # If >70% overlap with any kept chunk, consider redundant
                if overlap > 0.70:
                    is_redundant = True
                    break
            
            if not is_redundant:
                kept.append(chunk)
        
        return kept
    
    def get_config(self) -> Dict[str, Any]:
        """Get current configuration."""
        return {
            'mode': self.mode.value,
            'dedup_threshold': self.dedup_threshold,
            'max_chunks_per_doc': self.max_chunks_per_doc,
            'min_score_threshold': self.min_score_threshold,
            'max_total_chunks': self.max_total_chunks,
            'remove_boilerplate': self.remove_boilerplate,
            'enable_compression': self.enable_compression,
            'filters': {
                'dedup': f'Remove chunks >{self.dedup_threshold} similar',
                'per_doc_limit': f'Max {self.max_chunks_per_doc} per document',
                'score_threshold': f'Remove chunks <{self.min_score_threshold} score',
                'boilerplate': 'Remove table-of-contents, headers, etc.',
                'redundancy': 'Remove high-overlap content'
            }
        }

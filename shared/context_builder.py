"""Context builder for assembling retrieved chunks into LLM-ready context.

This module implements Phase 3: Prompt Assembly from the hybrid search plan:
- Enforces diversity constraints (max chunks per document)
- Collapses adjacent chunks from the same document
- Respects token budgets
- Formats context with citations for RAG
"""

import logging
from typing import List, Dict, Any, Optional
from itertools import groupby
from operator import itemgetter

try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False

logger = logging.getLogger(__name__)


class ContextBuilder:
    """Assembles retrieved chunks into a coherent, token-budget-compliant context.
    
    Implements the prompt assembly rules from Phase 3:
    - Max k chunks (default 8)
    - Max 2 chunks per document (diversity)
    - Collapse adjacent chunks when possible
    - Trim by token budget, not character count
    - Format with citations for attribution
    
    Attributes:
        max_context_tokens: Maximum tokens for assembled context
        max_per_document: Maximum chunks allowed per document
        collapse_adjacent: Whether to merge consecutive chunks
        include_citations: Whether to include citation markers
        tokenizer: tiktoken encoding for accurate token counting
    """
    
    DEFAULT_MAX_TOKENS = 3000
    DEFAULT_MAX_PER_DOC = 2
    
    def __init__(
        self,
        max_context_tokens: int = None,
        max_per_document: int = None,
        collapse_adjacent: bool = True,
        include_citations: bool = True,
        tokenizer_model: str = "cl100k_base"
    ):
        """Initialize the context builder.
        
        Args:
            max_context_tokens: Maximum tokens for context (default 3000)
            max_per_document: Max chunks per document (default 2)
            collapse_adjacent: Merge consecutive chunks (default True)
            include_citations: Add citation markers (default True)
            tokenizer_model: tiktoken model name (default cl100k_base)
        """
        self.max_tokens = max_context_tokens or self.DEFAULT_MAX_TOKENS
        self.max_per_doc = max_per_document or self.DEFAULT_MAX_PER_DOC
        self.collapse_adjacent = collapse_adjacent
        self.include_citations = include_citations
        
        # Initialize tokenizer
        if TIKTOKEN_AVAILABLE:
            try:
                self.tokenizer = tiktoken.get_encoding(tokenizer_model)
            except Exception:
                # Fallback to common encoding
                self.tokenizer = tiktoken.encoding_for_model("gpt-3.5-turbo")
        else:
            self.tokenizer = None
            logger.warning("tiktoken not available, using approximate token count")
    
    def _count_tokens(self, text: str) -> int:
        """Count tokens using tiktoken or approximation.
        
        Args:
            text: Text to count tokens for
            
        Returns:
            Token count (approximate if tiktoken unavailable)
        """
        if self.tokenizer:
            return len(self.tokenizer.encode(text))
        # Rough approximation: ~4 characters per token
        return len(text) // 4
    
    def _remove_overlap(self, text1: str, text2: str) -> str:
        """Remove overlapping text when merging chunks.
        
        Chunks may have overlap from original chunking (CHUNK_OVERLAP setting).
        This method detects and removes the overlap to avoid duplication.
        
        Args:
            text1: First text segment
            text2: Second text segment
            
        Returns:
            Merged text with overlap removed
        """
        # Check if text2 starts with end of text1
        max_overlap = min(len(text1), len(text2), 200)  # Max 200 char overlap
        
        for size in range(max_overlap, 0, -1):
            if text1[-size:] == text2[:size]:
                return text1 + text2[size:]
        
        # No overlap found, join with newline
        return text1 + '\n' + text2
    
    def _enforce_diversity(
        self, 
        chunks: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Limit chunks per document to max_per_doc.
        
        Prevents a single document from dominating the context.
        Keeps the highest-scored chunks from each document.
        
        Args:
            chunks: Retrieved chunks with hybrid_score
            
        Returns:
            Filtered chunks respecting per-document limit
        """
        # Group by document_id
        by_doc: Dict[int, List[Dict]] = {}
        for chunk in chunks:
            doc_id = chunk['document_id']
            by_doc.setdefault(doc_id, []).append(chunk)
        
        result = []
        dropped = 0
        
        for doc_id, doc_chunks in by_doc.items():
            # Sort by hybrid_score descending
            doc_chunks.sort(key=lambda x: x.get('hybrid_score', 0), reverse=True)
            
            kept = doc_chunks[:self.max_per_doc]
            dropped += len(doc_chunks) - len(kept)
            
            # Add metadata about position
            for i, chunk in enumerate(kept):
                chunk['_diversity_rank'] = i + 1
                chunk['_total_from_doc'] = len(doc_chunks)
            
            result.extend(kept)
        
        # Re-sort by original hybrid_score
        result.sort(key=lambda x: x.get('hybrid_score', 0), reverse=True)
        
        if dropped > 0:
            logger.info(f"Diversity filter: dropped {dropped} chunks, kept {len(result)}")
        
        return result
    
    def _collapse_adjacent(
        self, 
        chunks: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Merge consecutive chunk sequences from the same document.
        
        When chunks i and i+1 from the same document are both retrieved,
        merge them into a single contiguous passage for better coherence.
        
        Args:
            chunks: Chunks with document_id and chunk_index
            
        Returns:
            Chunks with adjacent sequences merged
        """
        if not chunks or len(chunks) < 2:
            return chunks
        
        # Group by document
        by_doc: Dict[int, List[Dict]] = {}
        for chunk in chunks:
            doc_id = chunk['document_id']
            by_doc.setdefault(doc_id, []).append(chunk)
        
        collapsed = []
        
        for doc_id, doc_chunks in by_doc.items():
            # Sort by chunk_index
            doc_chunks.sort(key=lambda x: x['chunk_index'])
            
            # Find consecutive sequences
            sequences = []
            current_seq = [doc_chunks[0]]
            
            for chunk in doc_chunks[1:]:
                last_idx = current_seq[-1]['chunk_index']
                if chunk['chunk_index'] == last_idx + 1:
                    current_seq.append(chunk)
                else:
                    sequences.append(current_seq)
                    current_seq = [chunk]
            sequences.append(current_seq)
            
            # Merge each sequence
            for seq in sequences:
                if len(seq) == 1:
                    collapsed.append(seq[0])
                else:
                    # Merge content with overlap removal
                    merged_content = seq[0]['content']
                    for chunk in seq[1:]:
                        merged_content = self._remove_overlap(
                            merged_content, 
                            chunk['content']
                        )
                    
                    # Use best score from sequence
                    best_score = max(c.get('hybrid_score', 0) for c in seq)
                    
                    merged = {
                        'id': seq[0]['id'],  # Use first chunk's ID
                        'document_id': doc_id,
                        'chunk_index': seq[0]['chunk_index'],
                        'chunk_indices': [c['chunk_index'] for c in seq],
                        'title': seq[0].get('title', 'Untitled'),
                        'content': merged_content,
                        'hybrid_score': best_score,
                        '_collapsed_from': len(seq),
                        'from_lexical': any(c.get('from_lexical') for c in seq),
                        'from_vector': any(c.get('from_vector') for c in seq),
                    }
                    collapsed.append(merged)
                    logger.debug(f"Collapsed {len(seq)} chunks from doc {doc_id}")
        
        # Re-sort by hybrid_score
        collapsed.sort(key=lambda x: x.get('hybrid_score', 0), reverse=True)
        return collapsed
    
    def _format_chunk(
        self, 
        chunk: Dict[str, Any], 
        index: int
    ) -> str:
        """Format a single chunk with citation.
        
        Args:
            chunk: Chunk dict with title and content
            index: Citation number (1-based)
            
        Returns:
            Formatted chunk string
        """
        title = chunk.get('title') or 'Untitled'
        
        if self.include_citations:
            return f"[{index}] Title: {title}\n{chunk['content']}"
        else:
            return f"Title: {title}\n{chunk['content']}"
    
    def _trim_to_budget(
        self, 
        formatted_chunks: List[str],
        chunks_metadata: List[Dict[str, Any]]
    ) -> tuple[List[str], List[Dict[str, Any]], int]:
        """Trim chunks to fit within token budget.
        
        Drops lowest-scored chunks first until budget is met.
        
        Args:
            formatted_chunks: Pre-formatted chunk strings
            chunks_metadata: Original chunk metadata
            
        Returns:
            Tuple of (kept chunks, kept metadata, total tokens)
        """
        total_tokens = 0
        result_chunks = []
        result_meta = []
        
        # Overhead for separators
        overhead_per_chunk = 10  # "\n\n" + citation formatting
        
        for chunk_text, meta in zip(formatted_chunks, chunks_metadata):
            chunk_tokens = self._count_tokens(chunk_text) + overhead_per_chunk
            
            if total_tokens + chunk_tokens <= self.max_tokens:
                result_chunks.append(chunk_text)
                result_meta.append(meta)
                total_tokens += chunk_tokens
            else:
                dropped = len(formatted_chunks) - len(result_chunks)
                logger.info(f"Token budget reached, dropped {dropped} chunks")
                break
        
        return result_chunks, result_meta, total_tokens
    
    def build_context(
        self,
        chunks: List[Dict[str, Any]],
        question: Optional[str] = None
    ) -> Dict[str, Any]:
        """Main entry point: transform chunks into formatted context.
        
        Implements the complete assembly pipeline:
        1. Enforce diversity constraints
        2. Collapse adjacent chunks
        3. Format with citations
        4. Trim to token budget
        
        Args:
            chunks: Retrieved chunks from HybridRetriever
            question: Optional question for context (logging only)
            
        Returns:
            Dict with:
                - context: Formatted context string for LLM
                - sources: Citation metadata
                - chunks_used: Number of chunks in final context
                - chunks_dropped: Number dropped by filters
                - token_count: Total tokens in context
                - documents_used: List of document IDs
                - stages: Detailed counts at each stage
        """
        if not chunks:
            return {
                'context': 'No relevant context found.',
                'sources': [],
                'chunks_used': 0,
                'chunks_dropped': 0,
                'token_count': 0,
                'documents_used': [],
                'stages': {
                    'retrieved': 0,
                    'after_diversity': 0,
                    'after_collapse': 0,
                    'after_budget': 0
                }
            }
        
        original_count = len(chunks)
        
        # Stage 1: Enforce diversity (max per document)
        chunks = self._enforce_diversity(chunks)
        after_diversity = len(chunks)
        
        # Stage 2: Collapse adjacent chunks
        if self.collapse_adjacent:
            chunks = self._collapse_adjacent(chunks)
        after_collapse = len(chunks)
        
        # Stage 3: Format with citations
        formatted = [self._format_chunk(c, i+1) for i, c in enumerate(chunks)]
        
        # Stage 4: Trim to token budget
        final_chunks, final_meta, token_count = self._trim_to_budget(
            formatted, chunks[:len(formatted)]
        )
        
        # Build sources metadata for citation tracking
        sources = []
        for i, chunk in enumerate(final_meta):
            sources.append({
                'citation_number': i + 1,
                'chunk_id': chunk['id'],
                'document_id': chunk['document_id'],
                'title': chunk.get('title', 'Untitled'),
                'score': chunk.get('hybrid_score', 0),
                'from_lexical': chunk.get('from_lexical', False),
                'from_vector': chunk.get('from_vector', False),
                'collapsed_from': chunk.get('_collapsed_from', 1)
            })
        
        # Build final context string
        context = '\n\n'.join(final_chunks)
        
        documents_used = list(set(s['document_id'] for s in sources))
        
        result = {
            'context': context,
            'sources': sources,
            'chunks_used': len(final_chunks),
            'chunks_dropped': original_count - len(final_chunks),
            'token_count': token_count,
            'documents_used': documents_used,
            'stages': {
                'retrieved': original_count,
                'after_diversity': after_diversity,
                'after_collapse': after_collapse,
                'after_budget': len(final_chunks)
            }
        }
        
        logger.info(
            f"Context built: {result['chunks_used']} chunks, "
            f"{result['token_count']} tokens, "
            f"{len(result['documents_used'])} documents"
        )
        
        return result

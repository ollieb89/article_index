import os
import httpx
import asyncio
from typing import List, Optional, Dict, Any
import numpy as np
import tiktoken


class OllamaClient:
    """Client for interacting with Ollama API for embeddings and text generation."""

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or os.getenv("OLLAMA_HOST", "http://localhost:11434")
        self.embedding_model = os.getenv("RAG_EMBEDDING_MODEL", "nomic-embed-text")
        self.chat_model = os.getenv("RAG_CHAT_MODEL", "llama3.2")

    async def generate_embedding(self, text: str, model: Optional[str] = None) -> List[float]:
        """Generate embedding for the given text using Ollama."""
        model = model or self.embedding_model

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.base_url}/api/embeddings",
                json={
                    "model": model,
                    "prompt": text
                }
            )

            if response.status_code != 200:
                raise Exception(f"Embedding generation failed: {response.status_code} - {response.text}")

            data = response.json()
            return data.get("embedding", [])

    async def generate_response(
        self,
        prompt: str,
        context: Optional[str] = None,
        model: Optional[str] = None,
        stream: bool = False
    ) -> str:
        """Generate text response using Ollama."""
        model = model or self.chat_model

        # Build the full prompt with context if provided
        if context:
            full_prompt = f"""Context: {context}

Question: {prompt}

Answer:"""
        else:
            full_prompt = prompt

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": full_prompt,
                    "stream": stream
                }
            )

            if response.status_code != 200:
                raise Exception(f"Text generation failed: {response.status_code} - {response.text}")

            data = response.json()
            return data.get("response", "")

    async def check_model_available(self, model: str) -> bool:
        """Check if a model is available in Ollama."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                if response.status_code == 200:
                    models = response.json().get("models", [])
                    return any(m.get("name") == model for m in models)
            return False
        except Exception:
            return False

    async def pull_model(self, model: str) -> bool:
        """Pull a model from Ollama registry."""
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:  # 5 minutes timeout
                response = await client.post(
                    f"{self.base_url}/api/pull",
                    json={"name": model}
                )
                return response.status_code == 200
        except Exception:
            return False


class TextProcessor:
    """Utility class for text processing operations."""

    def __init__(self):
        # Initialize tokenizer for text processing
        try:
            self.tokenizer = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self.tokenizer = None

    def count_tokens(self, text: str) -> int:
        """Count tokens in text using tiktoken."""
        if not self.tokenizer:
            return len(text.split())  # Fallback to word count
        return len(self.tokenizer.encode(text))

    def chunk_text(
        self,
        text: str,
        max_tokens: int = 500,
        overlap: int = 50
    ) -> List[str]:
        """Split text into chunks with specified token limits and overlap."""
        if not self.tokenizer:
            # Fallback chunking by characters
            return self._chunk_by_characters(text, max_chars=max_tokens * 4, overlap=overlap * 4)

        # Token-based chunking
        tokens = self.tokenizer.encode(text)
        chunks = []

        start = 0
        while start < len(tokens):
            end = min(start + max_tokens, len(tokens))
            chunk_tokens = tokens[start:end]
            chunk_text = self.tokenizer.decode(chunk_tokens)
            chunks.append(chunk_text)

            if end >= len(tokens):
                break

            start = end - overlap

        return chunks

    def _chunk_by_characters(self, text: str, max_chars: int = 2000, overlap: int = 200) -> List[str]:
        """Fallback chunking method by characters."""
        if len(text) <= max_chars:
            return [text]

        chunks = []
        start = 0

        while start < len(text):
            end = min(start + max_chars, len(text))

            # Try to break at word boundary
            if end < len(text):
                last_space = text.rfind(' ', start, end)
                if last_space > start:
                    end = last_space

            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)

            if end >= len(text):
                break

            start = max(0, end - overlap)

        return chunks

    def clean_text(self, text: str) -> str:
        """Clean and normalize text."""
        # Remove excessive whitespace
        text = ' '.join(text.split())

        # Remove other common issues
        text = text.replace('\t', ' ').replace('\r', '')

        return text.strip()


def format_vector(embedding: List[float]) -> str:
    """Format embedding list as PostgreSQL vector string."""
    return f"[{','.join(str(x) for x in embedding)}]"


def parse_vector(vector_str: str) -> List[float]:
    """Parse PostgreSQL vector string to list of floats."""
    # Remove brackets and split by comma
    if vector_str.startswith('[') and vector_str.endswith(']'):
        content = vector_str[1:-1]
        return [float(x.strip()) for x in content.split(',') if x.strip()]
    return []

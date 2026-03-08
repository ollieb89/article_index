import asyncio
import hashlib
import logging
from typing import List, Dict, Any, Optional
from bs4 import BeautifulSoup

from shared.ollama_client import OllamaClient, TextProcessor
from shared.database import document_repo

logger = logging.getLogger(__name__)


def compute_content_hash(title: str, content: str) -> str:
    """SHA256 hash of title + content for duplicate detection."""
    raw = f"{title}\n\n{content}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class ArticleProcessor:
    """Handles article processing including chunking and embedding generation."""

    def __init__(self):
        self.ollama = OllamaClient()
        self.text_processor = TextProcessor()

    async def process_article(
        self,
        title: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        chunk_size: int = 500,
        chunk_overlap: int = 50
    ) -> Dict[str, Any]:
        """Process a complete article: chunk, embed, and store."""
        try:
            # Clean the content
            cleaned_content = self.text_processor.clean_text(content)
            content_hash = compute_content_hash(title, cleaned_content)

            # Check for duplicate
            existing = await document_repo.get_document_by_content_hash(content_hash)
            if existing:
                return {
                    "document_id": existing["id"],
                    "duplicate": True,
                    "message": "Document already exists",
                }

            # Create document first
            document_id = await document_repo.create_document(
                title=title,
                content=cleaned_content,
                metadata=metadata,
                content_hash=content_hash,
            )

            # Generate document embedding
            document_embedding = await self.ollama.generate_embedding(
                f"{title}\n\n{cleaned_content}"
            )

            # Update document with embedding
            await document_repo.update_document_embedding(
                document_id,
                document_embedding
            )

            # Create chunks
            chunks = self.text_processor.chunk_text(
                cleaned_content,
                max_tokens=chunk_size,
                overlap=chunk_overlap
            )

            # Generate embeddings for chunks
            chunk_data = []
            for i, chunk_content in enumerate(chunks):
                chunk_embedding = await self.ollama.generate_embedding(chunk_content)
                chunk_data.append({
                    'content': chunk_content,
                    'embedding': chunk_embedding,
                    'chunk_index': i,
                    'title': title  # Pass title for hybrid search
                })

            # Store chunks with title
            chunk_ids = await document_repo.create_chunks(
                document_id,
                chunk_data,
                title=title
            )

            logger.info(f"Processed article '{title}': {len(chunks)} chunks created")

            return {
                'document_id': document_id,
                'chunk_count': len(chunks),
                'chunk_ids': chunk_ids,
                'document_embedding_dim': len(document_embedding),
                'chunk_embedding_dim': len(chunk_data[0]['embedding']) if chunk_data else 0
            }

        except Exception as e:
            logger.error(f"Error processing article '{title}': {str(e)}")
            raise

    async def process_html_article(
        self,
        title: str,
        html_content: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Process HTML article by extracting text content."""
        try:
            # Extract text from HTML
            soup = BeautifulSoup(html_content, 'html.parser')

            # Remove script and style elements
            for script in soup(["script", "style"]):
                script.decompose()

            # Get text content
            text_content = soup.get_text()

            # Clean up the text
            lines = (line.strip() for line in text_content.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text_content = ' '.join(chunk for chunk in chunks if chunk)

            return await self.process_article(title, text_content, metadata)

        except Exception as e:
            logger.error(f"Error processing HTML article '{title}': {str(e)}")
            raise

    async def update_embeddings_for_document(self, document_id: int) -> Dict[str, Any]:
        """Update embeddings for an existing document and its chunks."""
        try:
            # Get document
            document = await document_repo.get_document(document_id)
            if not document:
                raise ValueError(f"Document {document_id} not found")

            # Update document embedding
            document_embedding = await self.ollama.generate_embedding(
                f"{document['title']}\n\n{document['content']}"
            )
            await document_repo.update_document_embedding(
                document_id,
                document_embedding
            )

            # Get chunks
            chunks = await document_repo.get_document_chunks(document_id)

            # Update chunk embeddings
            updated_chunks = 0
            for chunk in chunks:
                chunk_embedding = await self.ollama.generate_embedding(chunk['content'])
                await document_repo.update_chunk_embedding(
                    chunk['id'],
                    chunk_embedding
                )
                updated_chunks += 1

            logger.info(f"Updated embeddings for document {document_id}: {updated_chunks} chunks")

            return {
                'document_id': document_id,
                'document_embedding_updated': True,
                'chunks_updated': updated_chunks
            }

        except Exception as e:
            logger.error(f"Error updating embeddings for document {document_id}: {str(e)}")
            raise

    async def batch_process_articles(
        self,
        articles: List[Dict[str, Any]],
        max_concurrent: int = 3
    ) -> List[Dict[str, Any]]:
        """Process multiple articles concurrently."""
        semaphore = asyncio.Semaphore(max_concurrent)

        async def process_with_semaphore(article_data):
            async with semaphore:
                return await self.process_article(**article_data)

        tasks = [process_with_semaphore(article) for article in articles]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results and handle exceptions
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Failed to process article {i}: {str(result)}")
                processed_results.append({
                    'error': str(result),
                    'article_index': i
                })
            else:
                processed_results.append(result)

        return processed_results


class EmbeddingManager:
    """Manages embedding operations and model availability."""

    def __init__(self):
        self.ollama = OllamaClient()

    async def ensure_models_available(self) -> Dict[str, bool]:
        """Check and pull required models if needed."""
        models_status = {}

        # Check embedding model
        embedding_model = self.ollama.embedding_model
        models_status['embedding_model'] = await self.ollama.check_model_available(embedding_model)

        if not models_status['embedding_model']:
            logger.info(f"Pulling embedding model: {embedding_model}")
            success = await self.ollama.pull_model(embedding_model)
            models_status['embedding_model'] = success
            if success:
                logger.info(f"Successfully pulled {embedding_model}")
            else:
                logger.error(f"Failed to pull {embedding_model}")

        # Check chat model
        chat_model = self.ollama.chat_model
        models_status['chat_model'] = await self.ollama.check_model_available(chat_model)

        if not models_status['chat_model']:
            logger.info(f"Pulling chat model: {chat_model}")
            success = await self.ollama.pull_model(chat_model)
            models_status['chat_model'] = success
            if success:
                logger.info(f"Successfully pulled {chat_model}")
            else:
                logger.error(f"Failed to pull {chat_model}")

        return models_status

    async def test_embedding_generation(self, test_text: str = "This is a test.") -> bool:
        """Test embedding generation."""
        try:
            embedding = await self.ollama.generate_embedding(test_text)
            return len(embedding) > 0
        except Exception as e:
            logger.error(f"Embedding test failed: {str(e)}")
            return False

    async def test_text_generation(self, test_prompt: str = "Say hello") -> bool:
        """Test text generation."""
        try:
            response = await self.ollama.generate_response(test_prompt)
            return len(response) > 0
        except Exception as e:
            logger.error(f"Text generation test failed: {str(e)}")
            return False


# Global instances
article_processor = ArticleProcessor()
embedding_manager = EmbeddingManager()

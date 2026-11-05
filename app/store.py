"""Where the slips live. Qdrant if QDRANT_URL is set, otherwise local JSON files.

This is the only file that knows about storage, so swapping one for the other
does not touch the chunking, the embeddings or the prompt.
"""

import os
from pathlib import Path

from llama_index.core import Settings, StorageContext
from llama_index.embeddings.fastembed import FastEmbedEmbedding

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
QDRANT_URL = os.getenv("QDRANT_URL")                    # e.g. http://qdrant:6333
COLLECTION = os.getenv("QDRANT_COLLECTION", "llmd_docs")
STORAGE_DIR = Path(os.getenv("STORAGE_DIR", Path(__file__).parent.parent / "storage"))


def configure() -> None:
    """Same embedding model on both sides -- indexing and querying."""
    Settings.embed_model = FastEmbedEmbedding(model_name=EMBED_MODEL)
    Settings.llm = None


def using_qdrant() -> bool:
    return bool(QDRANT_URL)


def vector_store():
    from llama_index.vector_stores.qdrant import QdrantVectorStore
    from qdrant_client import QdrantClient

    return QdrantVectorStore(
        client=QdrantClient(url=QDRANT_URL, timeout=120),
        collection_name=COLLECTION,
    )


def storage_context_for_writing() -> StorageContext:
    if using_qdrant():
        return StorageContext.from_defaults(vector_store=vector_store())
    return StorageContext.from_defaults()


def load_index():
    """Open the existing index for querying."""
    from llama_index.core import VectorStoreIndex, load_index_from_storage

    if using_qdrant():
        # Nothing to load into RAM: the slips stay in Qdrant, we just connect.
        return VectorStoreIndex.from_vector_store(vector_store())
    return load_index_from_storage(
        StorageContext.from_defaults(persist_dir=str(STORAGE_DIR))
    )

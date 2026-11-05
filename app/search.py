"""Station 4 test: find the pages closest in meaning to a question.
No LLM here at all -- this is only the 'R' of RAG."""

import sys
from pathlib import Path

from llama_index.core import Settings, StorageContext, load_index_from_storage
from llama_index.embeddings.fastembed import FastEmbedEmbedding

STORAGE = Path(__file__).parent.parent / "storage"

Settings.embed_model = FastEmbedEmbedding(model_name="BAAI/bge-small-en-v1.5")
Settings.llm = None

index = load_index_from_storage(
    StorageContext.from_defaults(persist_dir=str(STORAGE))
)
retriever = index.as_retriever(similarity_top_k=3)

question = " ".join(sys.argv[1:])
print(f"\nQ: {question}\n")
for i, node in enumerate(retriever.retrieve(question), 1):
    src = Path(node.metadata.get("file_path", "?")).name
    print(f"--- page {i}  score={node.score:.3f}  from {src}")
    print(node.text[:300].replace("\n", " "), "...\n")

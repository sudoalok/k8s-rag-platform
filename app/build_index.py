"""Station 4+6: read the llm-d docs, chop them into chunks, embed each chunk,
and store them -- in Qdrant if QDRANT_URL is set, otherwise in ./storage."""

from pathlib import Path

from llama_index.core import Settings, SimpleDirectoryReader, VectorStoreIndex
from llama_index.core.node_parser import SentenceSplitter

import store

DOCS = [
    Path.home() / "Documents/llm-d/docs",
    Path.home() / "Documents/llm-d/guides",
]

store.configure()
Settings.node_parser = SentenceSplitter(chunk_size=512, chunk_overlap=50)


def main() -> None:
    documents = []
    for folder in DOCS:
        documents += SimpleDirectoryReader(
            input_dir=str(folder), required_exts=[".md"], recursive=True
        ).load_data()
    print(f"loaded {len(documents)} files")

    ctx = store.storage_context_for_writing()
    index = VectorStoreIndex.from_documents(
        documents, storage_context=ctx, show_progress=True
    )

    if store.using_qdrant():
        print(f"pushed slips into Qdrant collection {store.COLLECTION!r}")
    else:
        ctx.persist(persist_dir=str(store.STORAGE_DIR))
        print(f"saved to {store.STORAGE_DIR}")


if __name__ == "__main__":
    main()

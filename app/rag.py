"""The RAG core, shared by the CLI (ask.py) and the web service (server.py)."""

import json
import os
import urllib.request
from pathlib import Path

import store

LLM_URL = os.getenv("LLM_URL", "http://localhost:8000") + "/v1/chat/completions"

MIN_SCORE = float(os.getenv("MIN_SCORE", "0.68"))
TOP_K = int(os.getenv("TOP_K", "3"))
NOT_FOUND = "I couldn't find this in the llm-d docs."

RULES = """Answer the question using ONLY the context below.
If the answer is not in the context, reply exactly:
"I couldn't find this in the llm-d docs."
Do not use any knowledge from outside the context."""

_retriever = None


def retriever():
    """Connect once, then reuse. With Qdrant nothing is loaded into our RAM."""
    global _retriever
    if _retriever is None:
        store.configure()
        _retriever = store.load_index().as_retriever(similarity_top_k=TOP_K)
    return _retriever


def write_letter(question: str, slips) -> str:
    context = "\n\n".join(
        f"--- from {Path(s.metadata.get('file_path', '?')).name} ---\n{s.text}"
        for s in slips
    )
    return f"{RULES}\n\nCONTEXT:\n{context}\n\nQUESTION: {question}"


def send_to_qwen(prompt: str) -> str:
    body = json.dumps({
        "model": "qwen0.5b",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 300,
    }).encode()
    req = urllib.request.Request(
        LLM_URL, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)["choices"][0]["message"]["content"]


def answer(question: str) -> dict:
    """The whole of RAG: retrieve, refuse if nothing matched, else generate."""
    slips = retriever().retrieve(question)
    sources = [
        {"file": Path(s.metadata.get("file_path", "?")).name,
         "score": round(s.score or 0, 3)}
        for s in slips
    ]
    best = max((s.score or 0) for s in slips) if slips else 0.0

    if best < MIN_SCORE:
        return {"question": question, "answer": NOT_FOUND,
                "grounded": False, "best_score": round(best, 3),
                "sources": sources}

    return {"question": question,
            "answer": send_to_qwen(write_letter(question, slips)).strip(),
            "grounded": True, "best_score": round(best, 3),
            "sources": sources}

"""Station 5 part 2: the /ask web service."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

import rag


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load the index at boot, so the first user doesn't pay for it.
    if os.getenv("WARMUP", "1") == "1":
        rag.retriever()
    yield


app = FastAPI(title="Ask the llm-d docs", lifespan=lifespan)


class Question(BaseModel):
    question: str


@app.get("/healthz")
def healthz():
    """Am I alive? Answers instantly, even while the index is still loading."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    """Am I ready for traffic? Only once the index is loaded."""
    rag.retriever()
    return {"status": "ready"}


@app.post("/ask")
def ask(q: Question):
    return rag.answer(q.question)

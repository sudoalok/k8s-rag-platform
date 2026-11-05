FROM python:3.12-slim

WORKDIR /srv
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FASTEMBED_CACHE_PATH=/srv/embed-cache

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the embedding model into the image, so a starting pod needs no
# internet and no 70 MB download before it can answer.
RUN python -c "from fastembed import TextEmbedding; \
    TextEmbedding(model_name='BAAI/bge-small-en-v1.5')"

# No storage/ here any more: the slips live in Qdrant, not in this image.
COPY app/ /srv/app/

ENV PYTHONPATH=/srv/app \
    LLM_URL=http://llm:8000 \
    QDRANT_URL=http://qdrant:6333

EXPOSE 8080
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]

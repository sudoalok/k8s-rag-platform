# Ask the llm-d docs

A RAG chatbot that answers questions about the [llm-d](https://github.com/llm-d/llm-d)
docs. The model, the vector DB and the API all run in a local Kubernetes
cluster. No hosted LLM, no API keys, nothing in the cloud.

I built it to learn the serving side of LLMs, so every piece is deployed by
hand instead of being a managed service.

```
  POST /ask
      |
  +---v------------+
  | ask      :8080 |   FastAPI: embeds the question, retrieves,
  +--+----------+--+   prompts the model, returns sources
     |          |
 +---v----+  +--v-------------+
 | qdrant |  | llm       :8000|
 |  :6333 |  | Qwen2.5-0.5B   |
 | 1375   |  | via llama.cpp  |
 | vectors|  +----------------+
 +---+----+
     |
 +---v-----+
 | PVC 2Gi |
 +---------+

  cluster created by Terraform (kind)
```

## Stack

- **Terraform + kind** for the cluster.
- **llama.cpp** serving `Qwen2.5-0.5B-Instruct` at Q4_K_M, about 400 MB,
  CPU only. I picked it mainly for the API: it speaks the OpenAI
  `/v1/chat/completions` format, so moving to vLLM on a GPU later is a
  base-URL change and not a rewrite.
- **bge-small-en-v1.5** for embeddings (384 dims, 67 MB) through fastembed,
  which runs on ONNX so there's no PyTorch in the image.
- **LlamaIndex** for loading, chunking and embedding. 185 markdown files
  become 1375 chunks.
- **Qdrant** for the vectors, on a PVC.
- **FastAPI** for `/ask`, with separate `/healthz` and `/readyz`.

## Example

```bash
curl -s localhost:8080/ask -H 'Content-Type: application/json' \
  -d '{"question":"what does the EPP endpoint picker do"}'
```

```json
{
  "answer": "The EPP endpoint picker selects which model server pod within an inference pool to process each inference request, using the internal state of model server pods (KV-cache utilization, prefix cache locality, request queue depth, and active request counts).",
  "grounded": true,
  "best_score": 0.839,
  "sources": [
    { "file": "epp.md", "score": 0.839 },
    { "file": "README.md", "score": 0.822 },
    { "file": "endpointpickerconfig.md", "score": 0.821 }
  ]
}
```

Anything that isn't in the docs gets refused before the model is called:

```json
{
  "answer": "I couldn't find this in the llm-d docs.",
  "grounded": false,
  "best_score": 0.517
}
```

## Things I got wrong, and what I did about them

### The grounding prompt didn't work

I had the usual `Answer the question using ONLY the context below` in the
prompt. Then I asked it for the capital of France. It was holding three
irrelevant chunks and it still said "The capital of France is Paris."

A prompt is a request, not a constraint, and a 0.5B model is not a strong
enough instruction follower to resist a continuation that predictable. So I
moved the rule out of the prompt: if the best retrieval score is below a
cutoff, the model never sees the question at all.

### I guessed the cutoff, and the guess was wrong

I started with 0.60 based on two data points. Later a question with no
matching docs scored 0.619, got through, and the model invented an answer.

So I measured it instead. Six questions I knew the docs answered, five I
knew they didn't:

```
  not in the docs                      in the docs
  0.498 ........ 0.619     gap      0.735 ........ 0.846
```

The two groups don't overlap, so the cutoff goes in the gap at 0.68. It's an
env var rather than a constant because it depends on the corpus, and if the
groups had overlapped no single number would have worked.

### The index was inside the container image

The first version had `storage/` baked into the image, which meant adding one
doc was re-index, docker build, kind load, kubectl apply. A docs change had
turned into a release.

Speed wasn't the reason to move to Qdrant, incidentally. Brute force over
300k vectors is around 16 ms on my laptop, and Qdrant won't even build an
HNSW graph below 10k vectors. The reasons were that every API replica would
load its own copy of the index into RAM, the index was frozen at build time,
and it died with the pod. Now adding a doc is one insert against the running
system.

### One failure I can't fix with a threshold

Asked how the scheduler picks pods, retrieval found the right chunk at 0.783.
That chunk says `queue-scorer` prefers endpoints with *shorter* queues. The
model told me the highest score goes to the pods that handled the most
requests, which is backwards.

The cutoff catches "nothing relevant was found". It can't catch the model
misreading something relevant. That needs a bigger model or a second model
checking the answer. Until then every response returns its sources so you
can check it yourself.

## Limitations

- **Chunk quality is the weakest part.** I chunked the markdown without
  stripping fenced code blocks, so the index has shell snippets that score
  well on questions about "requests" and "pods" without explaining anything.
  I watched one of the three retrieved slots go to a `curl` loop. Stripping
  code and keeping section headings in the chunks is the next thing to fix.
- **0.5B is small.** Both model failures above are size related.
- One node, one replica of everything. No autoscaling, no metrics yet, so I
  have no latency numbers to show.
- Model weights sit in an `emptyDir`, so every restart re-downloads 400 MB
  and the pod takes about 90 seconds to go ready. Should be a PVC.

## Running it

Needs Docker with ~5 GB, plus terraform, kubectl, kind and uv.

```bash
# cluster
cd infra && terraform init && terraform apply

# model and vector db
kubectl apply -f k8s/llm.yaml
kubectl apply -f k8s/qdrant.yaml
kubectl rollout status deploy/llm --timeout=600s

# build the index (needs the llm-d docs cloned locally)
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
kubectl port-forward svc/qdrant 6333:6333 &
PYTHONPATH=app QDRANT_URL=http://localhost:6333 .venv/bin/python app/build_index.py

# api
docker build -t ask-llmd:v2 .
kind load docker-image ask-llmd:v2 --name rag
kubectl apply -f k8s/ask.yaml

kubectl port-forward svc/ask 8080:8080 &
curl -s localhost:8080/ask -H 'Content-Type: application/json' \
  -d '{"question":"what is P/D disaggregation"}'
```

## Layout

```
infra/main.tf        the kind cluster
k8s/llm.yaml         Qwen via llama.cpp
k8s/qdrant.yaml      Qdrant, PVC included
k8s/ask.yaml         the API
app/store.py         the only file that knows where vectors live
app/build_index.py   load, chunk (512/50), embed, store
app/rag.py           retrieve, refuse or prompt, generate
app/server.py        FastAPI
app/search.py        retrieval-only CLI, handy for looking at chunks
app/ask.py           end-to-end CLI
docs/BUILD-LOG.md    notes per component
```

One detail I'd point out in the manifests: `/healthz` and `/readyz` are
separate on purpose. A pod that's loading its index is alive but not ready,
so it needs to get no traffic and also not be restarted, and you can't say
both with one endpoint.

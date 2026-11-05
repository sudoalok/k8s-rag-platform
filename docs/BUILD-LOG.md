# Build log

Notes per component, in the order I built them. Each one was deployed and
checked before I added the next.

## Cluster

`infra/main.tf` creates a one-node kind cluster called `rag` on Kubernetes
1.34.0, with `wait_for_ready = true` so apply doesn't return before the node
can schedule anything.

Terraform instead of a shell script because the state file makes apply
idempotent: run it twice against an unchanged config and you get "no
changes", not a second cluster.

`infra/rag-config` holds cluster credentials and is gitignored, along with
the tfstate.

## Model

`k8s/llm.yaml`, reachable in-cluster at `http://llm:8000`.

`Qwen/Qwen2.5-0.5B-Instruct-GGUF:Q4_K_M`, about 400 MB at 4-bit, pulled at
container start with llama.cpp's `-hf` flag, CPU only. What decided it was
the API rather than the model: llama.cpp exposes OpenAI-compatible
`/v1/chat/completions`, so the same client works against vLLM on a GPU.

`--ctx-size 4096` is the context budget, which is one of the two things that
pins down chunk size later.

The readiness probe on `/health` keeps the Service from routing to a pod
that's still downloading or loading weights. That's roughly 90 seconds from
container start to first served request.

The model keeps no state between requests, and the client resends the whole
conversation each time. That's what makes replicas interchangeable, which
matters for scaling later. It also means a request in flight is lost if the
pod dies: Kubernetes promises a replacement pod exists, not that the request
finishes, so retrying is the client's job.

Weights go to an `emptyDir`, so they re-download on every restart. A PVC
would cut cold start from ~90s to ~20s. Worth fixing before autoscaling,
since otherwise new capacity shows up after the spike is over.

## Chunking and embedding

`app/build_index.py` reads 185 markdown files from the llm-d `docs/` and
`guides/` trees, 255,211 words, and turns them into 1375 chunks.

Chunking is necessary because the corpus is roughly 85x the model's context
window, so the model can only ever see a fraction of it. The real question
isn't how to give it the corpus, it's which pieces to give it.

Keyword search doesn't answer that, because the docs and the questions don't
share vocabulary. "add more copies of the model" and "Scale the decode
Deployment by increasing `spec.replicas`" have no words in common. Cosine
similarity between those two is 0.658, against 0.453 for an unrelated
sentence, so embeddings rank the right text first where exact matching
scores it zero.

Embeddings are `BAAI/bge-small-en-v1.5` through fastembed: 384 dims, 67 MB,
ONNX rather than PyTorch to keep the image small.

`chunk_size=512` isn't a preference, it's where two limits meet:

- bge-small can't read past 512 tokens and truncates silently. I checked: a
  540-word text and a 900-word text that start the same way produce
  byte-identical embeddings. Bigger chunks would be partly invisible to
  search while still sitting in the index.
- Three 512-token chunks plus the instruction, the question and room for the
  answer have to fit in 4096 tokens.

`chunk_overlap=50` carries a bit of text across each cut so a sentence split
by a boundary still shows up whole somewhere. The splitter prefers sentence
boundaries anyway, so in practice the overlap is insurance rather than
something that fires every time.

One constraint to remember: indexing and querying must use the same
embedding model. Vectors of different length can't be compared at all, so
changing the model means a full re-index.

Two things I noticed here. Chunking markdown blindly keeps the fenced code
blocks, and a chunk of shell script containing "request" and "pod" can
outrank prose that actually answers the question. And retrieval always
returns top-k whether or not anything is relevant: "what is the capital of
France" came back with three chunks at 0.517, versus 0.798 for a real
question. The score is the signal, not the fact that you got results.

## API

`app/rag.py` is the pipeline: embed the question, retrieve top-k, refuse or
prompt, return the answer with sources. `app/server.py` serves it,
`app/ask.py` is the same thing on the command line, `k8s/ask.yaml` deploys
it at `http://ask:8080`.

The prompt is one message: the grounding instruction, the retrieved chunks
each labelled with its file, then the question. The model has no idea a
search happened.

`/healthz` answers immediately, `/readyz` only once the index is reachable.
A pod loading its index is alive but not ready, so it must get no traffic
and must not be restarted, and one endpoint can't say both. The index loads
once at startup through the `lifespan` hook so the first request doesn't pay
for it.

Everything configurable is an env var: `LLM_URL`, `QDRANT_URL`, `MIN_SCORE`,
`TOP_K`. Service DNS names throughout, never pod IPs.

The image bakes in the embedding model at build time so a starting pod needs
no network. `imagePullPolicy: Never`, with the image side-loaded by
`kind load docker-image`.

### The grounding prompt failed

With `Answer the question using ONLY the context below` in place, and three
irrelevant chunks in the context, "what is the capital of France" returned
"The capital of France is Paris."

A prompt is a request, not a constraint, and 0.5B isn't enough to resist a
continuation that predictable. Fixed with a cutoff in code: below
`MIN_SCORE`, the model is never called. Enforcement belongs in the app.

### Grounded and still wrong

Asked how the scheduler scores pods, retrieval returned the right chunk at
0.783. That chunk says `queue-scorer` prefers endpoints with shorter request
queues. The model said the highest score goes to pods that handled the most
requests, which is the opposite.

No threshold catches this. A threshold detects "nothing relevant came back";
it can't detect misreading something relevant. The fix is a bigger model, or
a second model checking the answer against the context. Meanwhile every
response carries its sources and scores.

## Qdrant

`k8s/qdrant.yaml`: a 2 Gi PVC, a Deployment and a Service at
`http://qdrant:6333`. `app/store.py` is the only file that knows which
backend is in use, Qdrant when `QDRANT_URL` is set and local JSON otherwise.
Chunking, embeddings and prompting don't change either way.

Speed wasn't the reason. Brute force over 300k 384-dim vectors is ~16 ms,
and Qdrant doesn't build an HNSW graph below 10k vectors anyway
(`indexed_vectors_count: 0` at this size). The reasons were:

- brute force needs every vector in RAM at once
- each API replica would load its own full copy
- the index was baked into the image, so adding a doc meant re-index, docker
  build, kind load, kubectl apply
- an in-process index dies with the pod

Two deployment details. `strategy: Recreate`, because a rolling update would
start a second pod before killing the first and two pods can't mount one
ReadWriteOnce volume. And client and server versions have to stay close:
qdrant-client 1.20 warns against server 1.16, so I moved the server to
v1.20.0 and rebuilt the collection.

I checked the swap two ways. Same question, same scores and sources from
Qdrant as from the JSON files (0.811 / 0.811 / 0.809), so the move didn't
change retrieval. Then I inserted a made-up doc into the live collection and
an already-running pod, never restarted, answered from it at 0.789, and one
filtered delete removed it again.

### Measuring the cutoff

Deleting that made-up doc is what showed the cutoff was too low. The same
question then scored 0.619 against a `MIN_SCORE` of 0.60, got through, and
the model made something up.

0.60 had come from two data points. I replaced it with a measurement over
six questions the docs answer and five they don't:

```
  not in the docs                      in the docs
  0.498 ........ 0.619     gap      0.735 ........ 0.846
```

Nothing between the two groups, so 0.68, mid-gap. Checked afterwards: both
out-of-corpus questions refuse, the in-corpus one answers at 0.846.

It's corpus-specific, so it's an env var and not a constant. If the groups
had overlapped there'd be no good single number and I'd need another signal.

## What's left

- **Chunk quality.** Strip or handle fenced code blocks separately, and keep
  section headings in the chunks. Biggest remaining win, given I watched a
  retrieval slot go to a `curl` loop.
- **Metrics and traces.** Prometheus and Grafana for rate, latency and
  refusal rate, plus OpenTelemetry spans to split latency across embedding,
  retrieval and generation. Right now I have no latency numbers at all.
- **Autoscaling.** KEDA on queue depth rather than CPU, since CPU shows how
  hard a replica is working and not how many requests are waiting. Needs the
  emptyDir to PVC fix first, otherwise a 90-second cold start means the new
  pod arrives late.
- **CI.** Build the image and run the pipeline against a fixed question set
  on every push.

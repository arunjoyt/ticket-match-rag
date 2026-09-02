# Ticket Match RAG

A retrieval service that surfaces similar past resolved helpdesk tickets — with the fix that
solved them — to an agent working a new ticket. Retrieval only: hybrid search → rerank → gate →
surface. No answer generation, so no hallucination-grounding problem; the hard problem here is
ranking quality.

## What It Does

An agent opens a ticket in Frappe Helpdesk; a "Similar Tickets" panel shows the closest past
Reusable Tickets, each with its Resolution Summary inline.

See [CONTEXT.md](CONTEXT.md) for the domain model (Ticket, Match, Root-Cause Similarity, Match
Threshold, Duplicate Cluster, Distractor).

## Architecture

At a glance:

```mermaid
flowchart LR
    HD[Frappe Helpdesk] -- "REST + webhooks" --> IDX[Index<br/>embed dense + BM25]
    IDX --> QD[(Vector Store - <br/>Qdrant)]
    Agent[Agent] --> UI[Helpdesk UI] --> BR[Bridge] --> API[[FastAPI]]
    API -- "hit" --> CACHE[(Match cache - <br/>Postgres)]
    API -- "miss" --> PIPE[Match pipeline<br/>embed → hybrid search → rerank → gate]
    PIPE --> QD
    CACHE & PIPE -- "matches + resolutions" --> API --> UI
```


And in detail:

```mermaid
flowchart LR
    HD["Frappe Helpdesk"]
    BRIDGE["Frappe bridge app<br/>(ticket-match-bridge)"]
    HDUI["Helpdesk agent UI"]

    subgraph API["FastAPI service"]
        direction TB
        INGEST["POST /ingest/full"]
        HOOK["POST /webhook/helpdesk<br/>HMAC-SHA256, fails closed"]
        IDX["indexing choke point<br/>index_ticket / deindex_ticket"]
        MATCHES["GET /tickets/{name}/matches<br/>API-key gated, cache-aside"]
        LIVE["compute_matches()<br/>embed → hybrid search → rerank → gate"]
        BGT["BackgroundRefresher<br/>single-ticket refresh, in-process"]
    end

    QD[(Qdrant<br/>dense + bm25 named vectors)]
    PG[(Postgres<br/>ticket_matches_cache · stale flag)]

    HD -- "REST: list / get tickets" --> INGEST
    HD -. "webhook on_update / on_trash" .-> HOOK
    INGEST --> IDX
    HOOK --> IDX
    IDX -- "idempotent uuid5 upsert — sync" --> QD
    IDX -- "UPDATE … SET stale = true — sync" --> PG
    HOOK -. "new Query Ticket → populate — async" .-> BGT

    HDUI --> BRIDGE
    BRIDGE -- "Bearer API_KEY" --> MATCHES
    MATCHES -- "row exists → serve as-is, fresh or stale" --> PG
    MATCHES -- "true miss → run live, then cache" --> LIVE
    MATCHES -. "stale hit → refresh this ticket — async" .-> BGT
    BGT --> LIVE
    LIVE --> QD
```

The subgraph is execution order, not code layout. **Every Qdrant mutation goes through one choke
point** (`retrieval/indexing.py`), which also flags every Match-cache row stale with a single
`UPDATE` (ADR 0011). **Reads are cache-aside**: a `GET /tickets/{name}/matches` request is served
from the cache whenever a row exists, fresh or stale (ADR 0007), and only runs the live
`embed → hybrid search → rerank → gate` pipeline on a true miss. A read that hits a stale row serves
it as-is and schedules a single-ticket background refresh (a FastAPI background task, in the API
process) so the *next* read of that ticket is fresh — one ticket at a time, driven by reads, no
sweep and no separate worker. A brand-new Query Ticket gets the same single-ticket populate from
the webhook handler, so a first agent open is a cache hit rather than a cold compute.

The one consumer of the API is Helpdesk's own agent ticket view, via a small Frappe app
([`ticket-match-bridge`](https://github.com/arunjoyt/ticket-match-bridge), ADR 0006/0008) that
overrides Helpdesk's stubbed `get_recent_similar_tickets()`. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
for the full data flow and [docs/PERFORMANCE.md](docs/PERFORMANCE.md) for measured latency under load.

## Tech Stack

| Layer | Technology |
|---|---|
| Helpdesk | Frappe Helpdesk (REST API + Webhooks) — no bench dependency, pulled over HTTP |
| Vector DB | Qdrant (self-hosted) — dense + BM25 sparse named vectors, server-side RRF fusion (ADR 0002) |
| Orchestration | Hand-rolled (`retrieval/matching.py`) — no LangChain, no chunking (a ticket embeds whole) |
| Embeddings | `EMBEDDING_MODEL`, default `BAAI/bge-small-en-v1.5` (local sentence-transformers) — see `config.py` |
| Sparse | `SPARSE_MODEL`, default `Qdrant/bm25` (fastembed) — asymmetric doc/query encode |
| Re-ranker | `RERANK_MODEL`, default `cross-encoder/stsb-roberta-base` — sentence-pair semantic equivalence, chosen over an ms-marco cross-encoder (ADR 0004) |
| LLM | **none** — retrieve-and-surface, no generation step anywhere |
| Match cache | Postgres (`ticket_matches_cache`, `stale` flag) — ADR 0006/0007/0011 |
| Background compute | in-process FastAPI background tasks — single-ticket refresh, deduped (ADR 0011) |
| Evaluation | `ranx` — recall@20 / precision@5 / MRR / nDCG@5, sliced by query variant (ADR 0003) |
| API | FastAPI, single shared API key (`Authorization: Bearer`, ADR 0009) |
| Helpdesk UI | Frappe bridge app overriding `get_recent_similar_tickets()` + one Vue snippet edit |
| Infra | Docker Compose + Nginx (envsubst template) |

## Project Structure

```
ticket-match-rag/
├── ingestion/
│   ├── helpdesk_client.py       # Frappe Helpdesk REST client (token auth)
│   ├── embedder.py              # Dense + sparse (BM25) embedding; match_text() = subject + description
│   └── webhook_handler.py       # POST /webhook/helpdesk — HMAC-SHA256 verify, incremental re-index
├── retrieval/
│   ├── vector_store.py          # Qdrant collection, upsert, delete, RRF fusion query
│   ├── hybrid_search.py         # Dense + sparse fused candidate pool
│   ├── reranker.py              # Cross-encoder rerank, returns scores (Match Threshold gates on them)
│   ├── matching.py              # Shared embed → search → rerank → gate pipeline + single-ticket refresh
│   └── indexing.py              # index_ticket() / deindex_ticket() — Qdrant-mutation choke point + mark_all_stale()
├── api/
│   ├── main.py                  # FastAPI app: /health, /ingest/full, /tickets/{name}/matches, webhook router
│   ├── auth.py                  # require_api_key — single shared key, hmac.compare_digest, fails closed
│   └── refresh.py               # BackgroundRefresher — in-process single-ticket refresh, dedup by ticket name
├── db/
│   ├── cache.py                 # MatchCache — Postgres get/put/mark_all_stale/delete
│   └── schema.sql               # ticket_matches_cache (ticket_name PK, matches JSONB, stale, computed_at)
├── eval/
│   ├── dataset.py               # Builds queries / qrels / near-miss lookups from data/seed_manifest.json
│   └── run.py                   # ranx runner — retrieval-stage vs. final-stage metrics + distractor leakage
├── scripts/
│   ├── seed_helpdesk.py         # Seeds synthetic Duplicate Clusters + Distractors, writes seed_manifest.json
│   ├── calibrate_threshold.py   # Sweeps MATCH_THRESHOLD, optimizes F0.5 against the seeded score distribution
│   ├── register_webhook.py      # Registers the two Frappe Webhook records (on_update, on_trash)
│   └── stress_test.py           # Latency / concurrency / background-refresh cost (docs/PERFORMANCE.md)
├── frappe_bridge/
│   ├── README.md                # Where the bridge app lives (its own repo) and why
│   └── helpdesk-vue-patch/      # Durable copy of the two hand-edited Helpdesk Vue files
├── data/
│   ├── seed_manifest.json       # Ground truth: ticket → cluster, variant, distractor kind (local dev)
│   └── seed_manifest.prod.json  # Same, for the production seed run
├── docs/
│   ├── ARCHITECTURE.md          # System design and data flow (as implemented)
│   ├── PERFORMANCE.md           # Reproducible latency & concurrency methodology
│   ├── PERFORMANCE_SUMMARY.md   # Evaluator-facing "does this fit?" summary
│   ├── DEPLOYMENT.md            # Executed production reference (Part B — AWS EC2)
│   ├── DEPLOYMENT_PLAN.md       # The plan that preceded DEPLOYMENT.md (Part A — Helpdesk on Contabo)
│   ├── PROPOSED_ARCHITECTURE.md # Retired stub — everything it proposed is now built
│   └── adr/                     # 0001–0011 — decision record, history not living docs
├── nginx/
│   ├── nginx.conf               # Base reverse-proxy config
│   └── templates/
│       └── ticket-match-rag.conf.template  # envsubst template for API_DOMAIN
├── config.py                    # Central env-var config — model names, MATCH_THRESHOLD, URLs, secrets
├── docker-compose.yml           # Base infra: qdrant, postgres (loopback-bound)
├── docker-compose.prod.yml      # Production overlay: adds app, nginx + restart policies
├── Dockerfile
├── requirements.txt
├── .env.example
└── .gitignore
```

## Quick Start (local development)

Prerequisites: a local Frappe Helpdesk instance (Docker), Docker Compose, and [uv](https://docs.astral.sh/uv/).

1. Copy `.env.example` to `.env`. For local dev the defaults work; fill in `HELPDESK_API_KEY` /
   `HELPDESK_API_SECRET` (generate a key for a Helpdesk user), and set `WEBHOOK_SECRET` and
   `API_KEY` to random values (`openssl rand -hex 32`).
2. Start infrastructure:
   ```bash
   docker compose up -d          # qdrant, postgres
   ```
3. Create the venv and install dependencies:
   ```bash
   uv venv && uv pip install -r requirements.txt
   ```
4. Start the API:
   ```bash
   uv run uvicorn api.main:app --reload
   ```
5. Seed the Helpdesk instance and run a full ingest:
   ```bash
   uv run python scripts/seed_helpdesk.py
   curl -X POST http://localhost:8000/ingest/full -H "Authorization: Bearer $API_KEY"
   ```
6. Register the incremental-sync webhooks (optional for a first look):
   ```bash
   uv run python scripts/register_webhook.py
   ```
7. Query a known ticket:
   ```bash
   curl http://localhost:8000/tickets/<ticket-name>/matches -H "Authorization: Bearer $API_KEY"
   ```
   Confirm the response matches a Duplicate Cluster from `data/seed_manifest.json`.

To see it inside Helpdesk's own agent UI, install the bridge app
([`ticket-match-bridge`](https://github.com/arunjoyt/ticket-match-bridge)) on the Helpdesk bench
and apply the Vue snippet from `frappe_bridge/helpdesk-vue-patch/` — see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) § Helpdesk-native UI.

## Production Deployment

`ticket-match-rag` runs on its own AWS EC2 instance; the Helpdesk instance it reads from is a
separate host. [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) is the executed step-by-step reference. The
short version:

1. Provision a `t3.medium` (Ubuntu 22.04), Elastic IP, security group open on 22/80/443 only.
2. Point `ticket-match.<domain>` at the Elastic IP; provision a TLS cert with `certbot certonly --standalone`.
3. Fill `.env` on the box — `HELPDESK_URL` + key/secret, `WEBHOOK_SECRET` (must match Helpdesk's
   Webhook records), `API_KEY`, and `API_DOMAIN=ticket-match.<domain>` (bare hostname — substituted
   into nginx's config at container start).
4. Launch:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
   ```
   Only nginx binds public ports (80/443); qdrant/postgres stay loopback-only.
5. Run the initial full ingest (`POST /ingest/full`) — webhooks only cover changes made after
   they are registered.

## Environment Variables

See `.env.example` for the full list. Key groups:

| Group | Variables | Purpose |
|---|---|---|
| Helpdesk | `HELPDESK_URL`, `HELPDESK_API_KEY`, `HELPDESK_API_SECRET` | Frappe Helpdesk REST access (token auth) |
| Webhook | `WEBHOOK_SECRET` | HMAC-SHA256 signature verification on `/webhook/helpdesk` — fails closed if unset |
| API auth | `API_KEY` | Single shared key for `/ingest/full` and `/tickets/{name}/matches` (ADR 0009) — fails closed if unset |
| Qdrant | `QDRANT_URL`, `QDRANT_COLLECTION` | Vector store |
| Match cache | `DATABASE_URL` | Postgres `ticket_matches_cache` |
| Models | `EMBEDDING_MODEL`, `RERANK_MODEL`, `SPARSE_MODEL` | Local models, loaded in-process; defaults in `config.py` |
| Match Threshold | `MATCH_THRESHOLD` | Reranker-score gate; calibrated per model + corpus (`scripts/calibrate_threshold.py`) — meaningless for a different `RERANK_MODEL` |
| Webhook target | `API_WEBHOOK_URL` | Where `scripts/register_webhook.py` points Helpdesk (local dev: `host.docker.internal`) |
| Production | `API_DOMAIN` | Bare hostname, drives the nginx template (`docker-compose.prod.yml` only) |

## Data Sources

| Doctype | Indexing | Notes |
|---|---|---|
| HD Ticket (Reusable Ticket) | Whole record, no chunking | Eligible once `resolution_details` is non-empty, independent of `status` (ADR 0001) |

The match vector is built from `subject + description` only (`match_text()` in
`ingestion/embedder.py`) — problem-to-problem matching. The Resolution Summary is stored and shown
alongside a Match but is never embedded (CONTEXT.md: Match).

## Incremental Indexing

`scripts/register_webhook.py` registers two Frappe Webhooks on HD Ticket — `on_update` and
`on_trash` (Frappe's `webhook_docevent` is single-select, so one record can't cover both). The
`/webhook/helpdesk` endpoint:

1. Verifies the HMAC-SHA256 signature (`X-Frappe-Webhook-Signature`), failing closed if `WEBHOOK_SECRET` is unset.
2. Refetches the full ticket via the Helpdesk REST API.
3. Re-indexes it if still eligible (non-empty `resolution_details`), or deletes its Qdrant point if it was trashed or made ineligible.
4. On any real index change, flags every Match-cache row stale (one `UPDATE`); reads reconcile them one ticket at a time (ADR 0011). A brand-new Query Ticket gets a single-ticket cache populate scheduled here.

## Evaluation

```bash
uv run python -m eval.run
```

Runs `ranx` IR metrics against `data/seed_manifest.json` — synthetic Duplicate Clusters (hold out
one member as the Query Ticket, check whether its siblings surface in the top-k) plus Distractors
seeded to be topically adjacent but not Root-Cause Similar (ADR 0003). Two stages are reported
separately so the breakdown shows which part of the pipeline is doing the work:

- **Retrieval stage** — `recall@20` on the fused dense+sparse candidate pool (a ceiling check: a relevant ticket missing from the pool can't be recovered by reranking).
- **Final stage** — `precision@5`, `MRR`, `nDCG@5` on the post-rerank ranking (what an agent actually sees, modulo the Match Threshold).

Each stage is sliced `overall` / `standard` / `low-lexical-overlap`, plus a near-miss distractor
leakage check (how often a hard negative clears the Match Threshold). This is a **manual local
run**, not CI — it needs a fully-seeded and ingested corpus first (`scripts/seed_helpdesk.py`,
then `POST /ingest/full`). `scripts/calibrate_threshold.py` re-picks `MATCH_THRESHOLD` against the
same corpus, optimizing F0.5 (precision-weighted, per CONTEXT.md's bias against low-confidence Matches).

## Performance

Measured, not estimated — see [docs/PERFORMANCE_SUMMARY.md](docs/PERFORMANCE_SUMMARY.md) for the
evaluator-facing view and [docs/PERFORMANCE.md](docs/PERFORMANCE.md) for methodology
(`scripts/stress_test.py`).

| Path | Time |
|---|---|
| Cached lookup, fresh or stale (Postgres) | ~10 ms |
| True miss (live pipeline) | ~0.3–0.6 s — ~90% in the cross-encoder rerank |
| One background refresh | ~0.3 s per ticket |
| `POST /ingest/full` re-index | ~40 ms per ticket |

The service runs as one process by default; a true-miss request blocks it for the full pipeline
duration (`requests`, `sentence-transformers`, `qdrant-client`, `psycopg2` are all synchronous —
`async def` is a FastAPI convention here, not concurrency). In normal use a ticket's row is
populated on creation and served on every read, so most agent traffic hits the fast path. Numbers
above were measured against the pre-ADR-0011 design (RQ worker); the read-path costs carry over,
but the lazy-revalidation load shape has not been re-measured (issue #18). Tested against a
67-ticket corpus on an M1 laptop — a small validation run, not a production-scale test.

---
status: implemented
---

# Switch RERANK_MODEL from ms-marco-MiniLM-L-6-v2 to stsb-roberta-base

Investigating #8 (near-miss distractors outranking true duplicates, surfaced by ADR 0003's hard-case fixtures) found the root cause was neither the fusion mechanism (ADR 0002's swap to Qdrant native sparse vectors produced bit-for-bit identical rerank scores, ruling it out) nor `MAX_MATCHES=5` (only 1/40 queries have more than 5 candidates clearing threshold at all). It was the reranker itself: across all 8 clusters, 35/39 queries (90%) had the near-miss distractor's rerank score beating at least one true cluster member's score, often all four, by large margins.

`cross-encoder/ms-marco-MiniLM-L-6-v2` is trained on real search-engine query/passage pairs, an **asymmetric** relationship (short query, longer answer passage) judged for **topical relevance** ("is this passage about what the query is asking"). This project's reranker input (`retrieval/reranker.py`) is `(query_text, candidate.match_text)` -- a Query Ticket's title+description against a candidate ticket's title+description, a **symmetric** ticket-vs-ticket pair, and the judgment needed is CONTEXT.md's Root-Cause Similarity ("the two tickets describe the same underlying problem"), which is explicitly stricter than topical similarity. Both the pair shape and the training objective were mismatched with the task.

## How it works

A cross-encoder like this has no notion of "match" baked into its architecture -- `CrossEncoder.predict()` tokenizes each `(query, candidate)` pair as `[CLS] query tokens [SEP] candidate tokens [SEP]`, runs it through a BERT-family encoder with full cross-attention between the two segments, and passes the final `[CLS]` hidden state through a one-unit linear head (`BertForSequenceClassification`, `num_labels=1`) to get a scalar score. What "match" means is entirely a function of the labeled pairs the head was fine-tuned against -- there's no instruction text anywhere in the input. Swapping `RERANK_MODEL` changes nothing about the retrieval logic; it changes which trained-in notion of "match" gets applied.

Each of the ~20 candidates per query is also scored in complete isolation -- one independent forward pass per pair, no attention or information flow between candidates. The "ranking" is not something the model computes; it's `sorted(...)` in Python over 20 unrelated absolute scores (`retrieval/reranker.py`). This is why a model whose absolute judgment criterion is miscalibrated for the task can consistently misrank every query the same way, and why a bad reranker can make the final ranking worse than the retrieval-stage signal it started from.

## Empirical comparison

Tested three off-the-shelf alternatives plus two "drop reranking entirely" baselines against the current model, all reusing the identical cached candidate pools for a fair comparison (outrank rate = fraction of near-miss-bearing queries where the near-miss's score beats at least one true cluster member's score; final-stage IR metrics via the eval harness, #2):

| Condition | outrank rate | mean margin | precision@5 | MRR | nDCG@5 |
|---|---|---|---|---|---|
| ms-marco-MiniLM-L-6-v2 (prior default) | 90% (35/39) | +4.565 | 0.670 | 0.838 | 0.785 |
| No rerank: hybrid RRF fusion score (free) | 72% | +0.127 | 0.685 | 0.892 | 0.820 |
| No rerank: pure dense cosine similarity | 82% | +0.050 | 0.710 | 0.912 | 0.859 |
| quora-distilroberta-base | 67% | +0.029 | 0.630 | 0.902 | 0.782 |
| **stsb-roberta-base** | **54% (21/39)** | **-0.012** | **0.730** | **0.950** | **0.892** |
| bge-reranker-base | 69% | +0.166 | 0.720 | 0.938 | 0.864 |

Both no-rerank baselines beat the prior default on every metric -- the old reranker wasn't just failing to help with near-misses, it was actively worse than doing nothing. `quora-distilroberta-base` (question-question duplicate detection, the closest task-shape match a priori) underperformed expectations, a reminder that structural fit is a prior, not a guarantee. `stsb-roberta-base` won every metric, including against both no-rerank baselines, and is the only condition with a negative mean margin (on average it correctly scores the near-miss *below* the worst true member, not above).

Being honest about the residual: 54% outrank rate is a real improvement (nearly halved from 90%) but not a full fix. A near-miss still beats a true positive in over half of cases even with the best off-the-shelf candidate tested -- see #9 for the follow-up.

## Considered alternative

Drop reranking entirely and use the free hybrid RRF fusion score (or pure dense cosine similarity) as the final ranking and threshold signal. Rejected despite both beating the prior default: `stsb-roberta-base` still beats both on every metric, and CONTEXT.md is explicit that fusion ranking exists to build a good candidate pool, not to produce a cross-query-comparable confidence value -- an RRF score's meaning depends on how many other candidates exist and their rank positions, not on an absolute measure of similarity, which the Match Threshold gate needs.

## Consequences

`MATCH_THRESHOLD` recalibrated via `scripts/calibrate_threshold.py` -- the old value (-0.3623) was meaningless for the new model's ~0-1 sigmoid-bounded score scale, versus the old model's unbounded raw logits. New calibrated value: `0.6221` (F0.5-optimized, same precision-weighted objective as before). Resulting precision/recall at threshold improved alongside the model swap: precision 0.745 -> 0.787, recall 0.637 -> 0.811.

Live spot-check (`GET /tickets/0121/matches`, the running `0121`/`vpn-crash-after-update` example from #8): the near-miss distractor (`0110`) no longer appears in the top 5 at all. But a *different* distractor -- `0120`, a cross-cluster-confusable (ADR 0003), not a near-miss -- now crowds out the same true member (`0055`) that `0110` used to. The fix worked against what it was tested against; a different distractor category still causes the same displacement pattern. Not addressed here.

No code changes beyond `config.py`/`.env`/`.env.example` -- `retrieval/reranker.py` already took the model name as a parameter with no other model-specific logic.

**Status: implemented.**

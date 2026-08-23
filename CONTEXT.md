# Ticket Match RAG

A retrieval system that surfaces similar past resolved helpdesk tickets to an agent working a new ticket, so they can reuse prior solutions. Retrieval only — no answer generation.

## Language

**Ticket**:
A single support request tracked in Frappe Helpdesk, from creation through resolution.
_Avoid_: Case, issue

**Resolution Summary**:
The final resolution note/comment recorded on a ticket when it's closed — describes how the issue was fixed. Distinct from the full conversation thread, which is excluded from the indexed record.
_Avoid_: Thread, resolution notes, close note

**Ticket Record**:
The whole-record payload for a Reusable Ticket — title + description + Resolution Summary — used for storage and display. Not what's embedded for matching; see Match for the matching text (title + description only). No chunking — a ticket is short enough to store whole.
_Avoid_: Document, chunk, passage

**Match**:
A past Ticket Record retrieved and shown to the agent for a new ticket. Similarity is computed on title + description only (problem-to-problem matching, not solution-text overlap); the Resolution Summary is never part of the matching vector but is always shown directly in the result — the agent never opens the original ticket to find the fix. A correct Match shares the Query Ticket's Root-Cause Similarity, not just its topic.
_Avoid_: Result, hit, candidate (candidate is fine pre-rerank/internally, but the agent-facing term is Match)

**Root-Cause Similarity**:
The bar a Match must clear: the two tickets describe the same underlying problem, such that the past ticket's fix would actually resolve the new one. Stricter than topical/category similarity (e.g., "both are networking issues" is not enough on its own) — this is near-duplicate detection, not general topical search.
_Avoid_: Topical similarity, category match, relatedness

**Duplicate Cluster**:
A set of Reusable Tickets that all share Root-Cause Similarity with each other — phrased differently, but describing the same underlying problem. Seeded deliberately in the synthetic dataset to serve as eval ground truth: hold out one member as the Query Ticket, and check whether retrieval surfaces the other members in the top-k.
_Avoid_: Duplicate group, similar set

**Distractor**:
A Reusable Ticket seeded to be topically adjacent to a Duplicate Cluster but deliberately lacking Root-Cause Similarity with it — e.g. a different networking issue sitting near a VPN Duplicate Cluster. Exists so eval measures real discrimination, not just "beat random unrelated tickets."
_Avoid_: Noise ticket, negative example

**Match Threshold**:
The minimum reranker score a candidate must clear to be shown as a Match. Gated on the cross-encoder's rerank score, not the hybrid-search fusion score — fusion ranking exists to build a good candidate pool, not to produce a cross-query-comparable confidence value. Below the threshold, the panel shows fewer than k results, or none — never forces a low-confidence Match onto the agent just to fill the panel. Exact value is a tunable implementation detail decided against real score distributions, not a fixed domain rule.
_Avoid_: Cutoff, confidence floor

**Query Ticket**:
The new, unresolved ticket an agent is currently working, used as the retrieval query. Embedded on title + description only — it has no Resolution Summary yet.
_Avoid_: Input ticket, new ticket (new ticket is fine in casual conversation, but Query Ticket is the canonical term in docs)

**Reusable Ticket**:
A ticket eligible to enter the index and appear as a Match — defined by having a non-empty Resolution Summary, independent of its status label. Status values come from a configurable field, not a fixed set, so status alone is never used to decide eligibility.
_Avoid_: Resolved ticket, closed ticket (those describe the status field, which is a separate, configurable concept — not eligibility)

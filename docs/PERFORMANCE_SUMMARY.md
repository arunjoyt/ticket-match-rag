# Ticket Match Performance

This page states the measured performance of Ticket Match. It helps a reader decide if the system fits their requirement. It does not explain the internal design. See [ARCHITECTURE.md](ARCHITECTURE.md) and [PERFORMANCE.md](PERFORMANCE.md) for the internal view.

Ticket Match finds similar past support tickets, with the fix that solved them, when an agent opens a new ticket. It does not generate an answer. It shows the closest past matches, ranked.

The numbers below come from timed requests against a running instance. They are not estimates. See [Test environment](#test-environment) for the exact test setup, and [Does this fit?](#does-this-fit) for what these numbers mean for your use case.

## At a glance

| Metric | Value | What it means |
|---|---|---|
| Repeat lookup | ~10ms | A ticket the system already scored |
| First-time lookup | ~0.3-0.6s | A ticket the system has not scored yet |
| Bulk re-score | ~40ms per ticket | Rebuild of the full index |
| Tested corpus | 67 tickets | A small validation run, not a production-scale test |

## Repeat vs. first-time lookups

Once the system scores a ticket, it stores the result and serves it back on the next request. This is a plain lookup.

A ticket the system has not seen yet needs a full score: read the ticket, compare it against the archive, and rank the results.

| Lookup type | Time | Why |
|---|---|---|
| Repeat | 9-13ms | Stored result, served as-is |
| First-time | 0.3-0.6s | Scored from scratch |

A first-time lookup takes about 35 times longer than a repeat lookup.

## Where a first-time lookup spends its time

A first-time lookup compares the new ticket against the archive fast. Ranking the candidates by how well they match takes most of the time.

| Stage | Time | Share |
|---|---|---|
| Find candidates | ~10ms | 3% |
| Read the new ticket | ~21ms | 7% |
| Rank results | ~270ms | 90% |

## Under concurrent load

This instance runs as one process by default. Requests queue instead of running side by side. Response time rises as more requests arrive at once, even for cheap, already-scored lookups stuck behind a slow one.

| Requests at once | Typical wait |
|---|---|
| 1 | ~10ms |
| 5 | ~44ms |
| 10 | ~95ms |
| 20 | ~650ms |

Throughput stayed flat at 16-30 requests per second across all four levels. The wait time per request rose, but the system did not process fewer requests per second overall.

### A realistic case: 20 agents open tickets at the same moment

| Scenario | Time for all 20 to complete |
|---|---|
| Mostly the same few tickets | ~2.5s |
| 20 genuinely different tickets | ~8.5s |

In normal use, a ticket is scored in the background the moment it is created, and every later view is served from that stored result. Most agent traffic hits the fast, repeat-lookup path, no matter how many agents are online. The slow case above only shows up in the first seconds after a cold start or a full re-index, before those views have been requested once.

### Adding more capacity is not automatic

We tested four parallel processes instead of one. We expected about four times the throughput. This did not happen. On the test machine, a laptop-class CPU, throughput stayed flat or dropped without more tuning.

Running more processes side by side is a reasonable path to more concurrent capacity. Test it on your own target hardware before you rely on it. This page does not have a proven number for that path yet.

## Keeping results fresh at scale

When the archive changes, the system flags every stored result for rescoring, then rescores each one the next time that ticket is opened — one ticket at a time, in the background, off the request the agent is waiting on. A ticket nobody opens is never rescored. The agent who opens a ticket right after the change sees the previous result; the next open of that ticket sees the updated one.

The cost of rescoring one ticket is ~0.3s. Rescoring N tickets after a change costs ~0.3s × N of background work in total, but spread across N separate views over time — not a single batch. These are projections from the measured per-ticket cost, not a live run at that scale.

| Tickets reopened after a change | Total background work |
|---|---|
| 50 | ~15s |
| 200 | ~1 min |
| 500 | ~2.5 min |

## Test environment

- **Hardware:** Apple M1 laptop, CPU only, no GPU. A server-class CPU or a GPU would change the numbers, most likely for the better.
- **Scale tested:** 67 archived tickets, 5 open at once. This is a small validation corpus, not a production-size archive. The background-work table above is a projection, not a live run at that scale.
- **Refresh model:** these numbers were measured against an earlier design that rescored every open ticket in one background batch on each change. The current design (lazy, per-view rescoring) has the same per-ticket cost but a different load shape; the concurrency numbers under that shape have not been re-measured yet.
- **Deployment tested:** one running instance, default configuration. No production infrastructure, such as a load balancer, multiple regions, or a managed database, is part of this test.
- **What "typical" means:** each range comes from at least two runs. We checked results across runs for consistency.

## Does this fit?

**A comfortable fit if:**
- Repeat lookups make up most of the traffic, which is the normal case once tickets have been opened once.
- A half-second wait on a brand-new ticket is acceptable.
- It is acceptable for the agent who opens a ticket right after a related change to see the previous result, with the update showing on the next open.
- Your open-ticket volume is in the hundreds, not the tens of thousands.
- Traffic comes from one agent at a time, or in small, spread-out bursts.

**Worth testing first:**
- Many agents could open brand-new tickets within the same few seconds. Test your own peak pattern.
- You need a guaranteed sub-second response on every request, including first-time ones.
- You need every ticket's result to reflect the latest change immediately, not on its next open.
- You plan to run more than one instance. The scaling caveat above applies to you directly.
- Your ticket archive is an order of magnitude larger than the 67 tickets tested here.

---

The test script and full methodology are in this repository (`scripts/stress_test.py`, [PERFORMANCE.md](PERFORMANCE.md)). These numbers reflect one point in time. Re-run the script against your own deployment before you treat any figure on this page as a guarantee.

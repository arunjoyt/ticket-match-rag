---
status: implemented
---

# Retire the standalone demo UI

ADR 0006 point 4 explicitly revisited and kept the standalone Streamlit demo UI (`ui/app.py`) alongside the new Helpdesk-native panel, reasoning that it stayed useful as a way to demo retrieval without the full stack (Postgres, Redis, worker, bridge app) running.

Mapping every real HTTP caller of the retrieval API while scoping [ADR 0009](0009-api-key-auth.md)'s auth work found that `ui/app.py` was the *only* caller of `GET /tickets/queryable`, and one of only two callers of `GET /tickets/{name}/matches` (the other being the Helpdesk bridge app, which now covers the demoable surface natively). Confirmed with the user: retire it — this reverses ADR 0006 point 4's call, logged here as a fresh decision rather than silent drift, not because the earlier reasoning was wrong at the time, but because the situation it was reasoning about (only the bridge app existed, Helpdesk's panel wasn't proven yet) no longer holds.

## Decision

Delete `ui/app.py` and the `ui/` directory, remove `streamlit` from `requirements.txt`, and remove `GET /tickets/queryable` (`api/main.py`) since it has no remaining caller — `HelpdeskClient.list_open_tickets()` itself stays, still used in-process by `worker/tasks.py`'s refresh sweep.

**Why not keep it as a lighter-weight fallback demo, per ADR 0006's original reasoning:** that reasoning assumed the Helpdesk-native panel might be more fragile or harder to stand up than a single `streamlit run`. It isn't — the panel is now the verified, cache-backed default path (ADR 0006, 0008), and keeping a second unauthenticated-until-ADR-0009 HTTP consumer around solely as a demo fallback is dead weight to maintain and secure for a benefit ("also demoable without Helpdesk") this project no longer needs, now that the Helpdesk dev bench itself is a scripted, reproducible piece of this project's setup.

## Consequences

One fewer route to test and authenticate, `config.py` loses `API_BASE_URL` (confirmed dead — no other reference in the repo), one fewer dependency. `docs/ARCHITECTURE.md`'s diagrams and "two consumers" prose are updated to reflect the single remaining consumer (Helpdesk's agent UI, via the bridge app). `docs/DEPLOYMENT_PLAN.md`'s nginx bullet, which had already gone stale once (originally written when the demo UI didn't exist yet, then the demo UI shipped without that bullet being corrected), is fixed to reflect this repo's history honestly rather than re-introducing the same staleness.

## Verification

`git status` confirms `ui/` and its Streamlit dependency are gone; `GET /tickets/queryable` returns 404. The Helpdesk-native panel (ADR 0006, 0008, 0009) remains the sole way to see Matches in a UI, browser-verified working end to end as part of ADR 0009's verification.

**Status: implemented.**

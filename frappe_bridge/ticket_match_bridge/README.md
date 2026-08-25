# Ticket Match Bridge

Frappe app that overrides Helpdesk's stubbed `get_recent_similar_tickets()` to
serve [Ticket Match RAG](../../README.md)'s Matches inside Helpdesk's own
agent ticket view, instead of only the standalone demo UI. See
[ADR 0006](../../docs/adr/0006-background-compute-and-helpdesk-integration.md)
and [docs/PROPOSED_ARCHITECTURE.md](../../docs/PROPOSED_ARCHITECTURE.md).

## Install

```bash
bench get-app /path/to/ticket_match_bridge
bench --site <site> install-app ticket_match_bridge
bench --site <site> set-config ticket_match_api_url http://host.docker.internal:8001
```

`ticket_match_api_url` defaults to `http://host.docker.internal:8001` if unset
(local-dev convention, matching `config.API_WEBHOOK_URL`'s direction in the
main project).

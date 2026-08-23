# Content-based ticket eligibility, not status-based

Frappe Helpdesk's `status` field is a Link to a separate, per-instance-configurable doctype, not a fixed enum — so there's no reliable "Resolved" value to filter on across instances. We considered gating index eligibility on status (e.g., `status == "Resolved"` or `"Closed"`), but decided a Reusable Ticket is instead defined purely by having a non-empty Resolution Summary, regardless of its status label. This is more robust to however an instance's statuses are configured, and matches what we actually need: a ticket is only useful as a Match if it has something to surface, independent of what its status happens to say.

## Consequences

Combined with the reused webhook-driven indexing pattern (HMAC-verified webhook → delete-by-id → reparse/embed/upsert), this means every HD Ticket update event re-runs the eligibility check: if still eligible, delete-by-id then re-embed/upsert (idempotent via `uuid5` key); if no longer eligible, delete-by-id and don't re-add.

## Known limitation: reopened tickets

Frappe doesn't necessarily clear `resolution_details` when a ticket is reopened (e.g., the customer reports the fix didn't work). Because eligibility is purely content-based, a reopened ticket whose resolution field is still populated stays indexed and can keep surfacing as a Match, even though its fix is now known to be wrong. We accepted this rather than adding status-transition tracking to catch reopens — the added complexity isn't worth it for a demo-scale project, and it's a rare edge case. Deliberate scope boundary, not an oversight.

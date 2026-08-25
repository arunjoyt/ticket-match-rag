# Helpdesk Vue patch

Durable copy of the two Helpdesk frontend files edited for ADR 0006 point 4
(issue #11) — Helpdesk's own source lives inside the Docker container's
writable layer, which is not backed by any git repository, so this is the
only record of the change if that container is ever recreated.

- `TicketDetailsTab.vue` — one added `<p>` in the similar-tickets list item,
  showing the resolution snippet (`t.resolution_details`) below the subject.
- `types.ts` — `SimilarTicket` widened with an optional `resolution_details`
  field so the template line type-checks.

Both are full copies of `apps/helpdesk/desk/src/...` at the paths named
above, taken after the edit — diff them against a fresh `frappe/helpdesk`
checkout to see exactly what changed. To reapply after the container is
recreated:

```bash
docker cp TicketDetailsTab.vue <container>:/home/frappe/frappe-bench/apps/helpdesk/desk/src/components/ticket-agent/TicketDetailsTab.vue
docker cp types.ts <container>:/home/frappe/frappe-bench/apps/helpdesk/desk/src/types.ts
docker exec <container> bash -lc "cd /home/frappe/frappe-bench && bench build --app helpdesk"
```

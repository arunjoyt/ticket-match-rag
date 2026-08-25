# frappe_bridge

Two things live here, on either side of the Helpdesk-native integration (ADR
0006, [ADR 0008](../docs/adr/0008-standalone-bridge-app-repo.md)):

- **The bridge app itself** — no longer in this repo. It's a real installable
  Frappe app, in its own repo:
  [`arunjoyt/ticket-match-bridge`](https://github.com/arunjoyt/ticket-match-bridge)
  (`bench get-app` needs a git repo to clone, which a subfolder here couldn't
  provide — see ADR 0008).
- **`helpdesk-vue-patch/`** — stays here. It's not a Frappe app, just a
  durable copy of the two Helpdesk-side Vue files hand-edited to show a
  resolution snippet (ADR 0006 point 4), so the change survives even if the
  dev Helpdesk instance is recreated. Helpdesk's own source isn't part of
  this repo, so this is the record of record for that edit.

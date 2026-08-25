# Production Deployment Plan

Two parts: a real Helpdesk instance on the existing Contabo VPS bench, and ticket-match-rag itself on a new AWS EC2 instance. The local Docker Helpdesk instance stays in place for iterating; this is the production target.

**Status: planned, not yet executed.** Nothing below has been run against production.

## New finding

`frappe/helpdesk`'s `pyproject.toml` (`main` branch) declares `telephony = ">=0.0.1,<1.0.0"` under `[tool.bench.frappe-dependencies]` — Helpdesk requires Telephony. Both apps go into `apps.json.tmpl` together, matching what the local dev instance's `init.sh` already installs.

## Part A — helpdesk.22logic.com on Contabo

Following `vps-ref-contabo/RUNBOOK.md`'s "Adding a brand-new app" + "Adding a brand-new site" procedures.

**A1. Add apps to the shared image:**
1. Add to `apps.json.tmpl` in `vps-ref-contabo`: `{"url": "https://github.com/frappe/helpdesk", "branch": "main"}` and `{"url": "https://github.com/frappe/telephony", "branch": "main"}` (both public, no PAT needed).
2. Commit, push to `main`.
3. Trigger: `gh workflow run build-and-deploy.yml --repo arunjoyt/vps-ref-contabo -f app=core -f tag=helpdesk-added`. **This rebuilds the shared image and redeploys (backup + force-recreate + migrate) all 3 currently-live production sites** — the unavoidable consequence of one shared bench/image, not a bug.
4. Verify via `docker compose -p frappe-bench ps` that nothing's stuck `Restarting`.

**A2. New site + data:**
1. Add `helpdesk.22logic.com` to `SITES_RULE` in `~/frappe_docker/frappe.env`.
2. Add an A record for `helpdesk.22logic.com` → the VPS's IP via the domain registrar's DNS panel.
3. Recreate the stack: `docker compose -p frappe-bench --env-file frappe.env -f compose.yaml -f overrides/compose.https.yaml up -d --force-recreate`.
4. `bench new-site helpdesk.22logic.com --mariadb-user-host-login-scope=% --db-root-password <pw> --admin-password <pw>`, then `install-app telephony`, `install-app helpdesk`, `migrate`.
5. Generate a service-account API key/secret: `bench --site helpdesk.22logic.com execute frappe.core.doctype.user.user.generate_keys --args "['Administrator']"` (same command used for the dev instance).
6. Seed data: extend `scripts/seed_helpdesk.py` with a `--base-url`/env-var override (currently hardcoded to `http://helpdesk.localhost:8000`), then run it against `https://helpdesk.22logic.com` with the new API creds.

**A3. Bridge app + Vue patch (ADR 0006, 0008, 0009):**
1. `bench get-app https://github.com/arunjoyt/ticket-match-bridge && bench --site helpdesk.22logic.com install-app ticket_match_bridge`.
2. `bench --site helpdesk.22logic.com set-config ticket_match_api_url https://ticket-match.22logic.com` and `set-config ticket_match_api_key <same API_KEY as Part B's .env>`.
3. Manually reapply the Vue edit: copy `frappe_bridge/helpdesk-vue-patch/TicketDetailsTab.vue` and `types.ts` over the matching files in the prod bench's `apps/helpdesk/desk/src/`. This is **not** a `bench` step and won't survive a future Helpdesk version bump on its own — it's an uncommitted in-place edit to Helpdesk's own source (confirmed on the dev bench: `git status` shows it as local modifications on top of Helpdesk's pinned commit, not a real patch file). Frappe has no override/hook mechanism for Helpdesk's Vue SPA the way it does for backend methods (`override_whitelisted_methods`, used for the bridge app's own half of this) or classic Desk forms (`Client Script`) — so after any `bench update` that touches Helpdesk, re-diff `TicketDetailsTab.vue`/`types.ts` against the copies in `frappe_bridge/helpdesk-vue-patch/` and reapply by hand before assuming the Similar Tickets panel still shows resolution snippets.

## Part B — ticket-match-rag on a new AWS EC2 instance

Mirrors Contract Intelligence's proven pattern (`contract-intelligence/docs/DEPLOYMENT.md`): manual console provisioning, no CI/CD, git-pull-and-build on the instance, certbot standalone TLS, plain `.env` secrets.

**B1. Repo artifacts (built directly in this repo, not yet done):**
- `nginx/nginx.conf` + `nginx/templates/ticket-match-rag.conf.template` — mirrors Contract Intelligence's `nginx/` structure, but single domain (`API_DOMAIN` only — the standalone demo UI was retired (ADR 0010), so there's no separate frontend to split traffic for).
- `docker-compose.prod.yml` — adds `app` (built from the existing `Dockerfile`) and `nginx`; only `nginx` binds host ports 80/443; `qdrant` stays loopback-only; every service `restart: unless-stopped`; `env_file: .env`.
- `docs/DEPLOYMENT.md` — the actual ops runbook once this is live (this file is the plan; that one will be the executed reference, mirroring how `vps-ref-contabo`'s own `CD-SETUP.md` describes itself as "the copy of record" after a plan became reality).
- `.env.example` gets an `API_DOMAIN` entry — it already has `API_KEY` (ADR 0009).

**B2. AWS provisioning (console, real billable action):**
1. Launch `t3.medium` (2 vCPU/4GB), Ubuntu 22.04 LTS, 20GB gp3, same region as Contract Intelligence's instance.
2. Allocate + associate an Elastic IP.
3. Security group: 22 (SSH, restricted to your IP), 80, 443 (0.0.0.0/0) only — Qdrant stays loopback-only via the compose port binding.
4. Add an A record for `ticket-match.22logic.com` → the Elastic IP via the registrar's DNS panel.

**B3. Bootstrap:**
```bash
ssh ubuntu@<elastic-ip>
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
sudo apt install -y docker-compose-plugin certbot
git clone https://github.com/arunjoyt/ticket-match-rag && cd ticket-match-rag
cp .env.example .env   # fill in: HELPDESK_URL=https://helpdesk.22logic.com,
                        # HELPDESK_API_KEY/SECRET from A2.5, WEBHOOK_SECRET via openssl rand -hex 32,
                        # API_KEY via openssl rand -hex 32 (ADR 0009 -- required, api/auth.py fails closed if unset),
                        # API_DOMAIN=ticket-match.22logic.com
sudo certbot certonly --standalone -d ticket-match.22logic.com
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
curl -X POST http://localhost:8000/ingest/full -H "Authorization: Bearer $API_KEY"
```
Cron: `0 3 * * * certbot renew --quiet && docker compose -f docker-compose.yml -f docker-compose.prod.yml exec nginx nginx -s reload`.

Also needed once Part A's Helpdesk site exists: `bench --site helpdesk.22logic.com set-config ticket_match_api_key <same API_KEY>`, alongside the existing `ticket_match_api_url` config — the bridge app (issue #5's own install step, not detailed here) sends this as `Authorization: Bearer` on every call into this API.

## Execution notes

- Part A1's trigger step (A1.3) redeploys live production — confirm explicitly right before running it, regardless of when the rest of this plan was agreed.
- No SSH key to the Contabo VPS and no working AWS CLI exist on this machine (the shell's `aws` is aliased to a personal SSH shortcut into Contract Intelligence's own instance) — Part A2, B2, and B3 need to run interactively with the user, or be executed by the user directly.

## Verification

- Part A: `bench --site helpdesk.22logic.com list-apps` shows `helpdesk` + `telephony` + `ticket_match_bridge`; browser-verify login; seed script reports 48 Reusable Tickets, matching the dev run; a manual `curl` against the new API key confirms auth works; open a real ticket in the agent UI and confirm the Similar Tickets panel shows resolution snippets (proves A3's manual Vue reapply actually landed, not just that the app installed).
- Part B: `curl https://ticket-match.22logic.com/health` (no auth needed); `POST /ingest/full` and spot-check `/tickets/{name}/matches` for known Duplicate Cluster members (both `-H "Authorization: Bearer $API_KEY"`), same shape as Phase 1's verification.

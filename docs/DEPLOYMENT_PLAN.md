# Production Deployment Plan

Two parts: a real Helpdesk instance on the existing Contabo VPS bench, and ticket-match-rag itself on a new AWS EC2 instance. The local Docker Helpdesk instance stays in place for iterating; this is the production target.

**Status: fully executed and verified in production, 2026-08-25.** Both parts are live: `helpdesk.22logic.com` (Contabo) and `ticket-match.22logic.com` (AWS EC2, `eu-central-1`), with the Similar Tickets panel confirmed showing real resolution snippets in the live agent UI. `docs/DEPLOYMENT.md` is the executed-reference runbook for Part B going forward. This file stays as the plan/rationale trail plus the execution findings below — real issues hit that the plan didn't anticipate.

## New finding

`frappe/helpdesk`'s `pyproject.toml` (`main` branch) declares `telephony = ">=0.0.1,<1.0.0"` under `[tool.bench.frappe-dependencies]` — Helpdesk requires Telephony. Both apps go into `apps.json.tmpl` together, matching what the local dev instance's `init.sh` already installs.

**Correction (caught by the first rebuild attempt, run 32857786699):** `frappe/telephony` has no `main` branch — its only branch is `develop`. The first `apps.json.tmpl` entry used `"branch": "main"`, which failed at `bench init`'s clone step (`Remote branch main not found in upstream upstream`) and aborted the build before the `deploy` job ran, so no live site was touched. Fixed to `"branch": "develop"` and re-pushed.

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

**B1. Repo artifacts (built directly in this repo -- done):**
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

- Part A: `bench --site helpdesk.22logic.com list-apps` shows `helpdesk` + `telephony` + `ticket_match_bridge`; browser-verify login; seed script reports 72 tickets total (67 Reusable Tickets: 40 cluster members + 27 distractors, plus 5 unresolved demo queries) -- verified against production 2026-08-25; a manual `curl` against the new API key confirms auth works; open a real ticket in the agent UI and confirm the Similar Tickets panel shows resolution snippets (proves A3's manual Vue reapply actually landed, not just that the app installed). **Done** -- ticket `0002`'s agent UI showed `#0005`/`#0004` with real resolution-snippet text (e.g. "Confirmed this was caused by the patch resetting net...", matching `resolution_details` exactly).
- Part B: `curl https://ticket-match.22logic.com/health` (no auth needed); `POST /ingest/full` and spot-check `/tickets/{name}/matches` for known Duplicate Cluster members (both `-H "Authorization: Bearer $API_KEY"`), same shape as Phase 1's verification. **Done** -- `/ingest/full` indexed 67 (matches the Reusable Ticket count above); `/tickets/0002/matches` returned `0005`/`0004`/`0003` (its real Duplicate Cluster siblings) all above the 0.6221 Match Threshold with sensible score ordering.

## Execution findings (production run, 2026-08-25)

Real issues hit that this plan didn't anticipate, in the order encountered. Worth reading before repeating any of A1/A2/A3 (e.g. after a Helpdesk version bump, per A3.3's own warning).

1. **`frappe/telephony` has no `main` branch** (only `develop`) -- see "New finding" above. Caught by the first `apps.json.tmpl` rebuild attempt failing before the `deploy` job ran, so no live site was touched by the bad config.
2. **A2 step ordering matters**: `bench new-site` before `SITES_RULE`/stack-recreate (A2.1/A2.3) leaves Traefik with no router for the new host, so it serves its own self-signed `TRAEFIK DEFAULT CERT` instead of requesting a real one from Let's Encrypt -- `openssl s_client` showing `issuer=CN=TRAEFIK DEFAULT CERT` is the tell. Fixed by doing A2.1/A2.3 first, then Traefik auto-issued a real multi-SAN cert covering all the bench's sites once a matching router existed.
3. **`bench get-app` crashes building `ticket_match_bridge`'s assets** (`esbuild.js`'s `get_all_files_to_build`, `TypeError: paths[0] ... Received undefined`) -- the app is backend-only (one `override_whitelisted_methods` hook, empty `public/`), and esbuild doesn't handle an app with zero frontend entry points gracefully. Fix: `bench get-app <url> --skip-assets`. That flag also skips the step that appends the app to `sites/apps.txt`, though -- add it manually (`echo appname >> sites/apps.txt`), and watch for the file's last line missing a trailing newline (it was), which silently concatenates the appended name onto the previous line (`expensoticket_match_bridge`) instead of adding a new one. Then `bench --site <site> install-app <appname>` (no `--skip-assets` here -- that's a `get-app`-only flag) installs cleanly.
4. **`bash -lc` drops `node` from `PATH`** -- wrapping `bench build` in `bash -lc "cd ... && bench build ..."` triggers a login shell that sources different profile files than the container's default shell, and `check_node_executable()` then fails with `node: not found` even though the same container's default shell resolves it fine. Fix: run bench commands directly (`docker compose exec backend bench build --app helpdesk`), no `bash -lc` wrapper, no `cd` needed since the image's `WORKDIR` is already `/home/frappe/frappe-bench`.
5. **Editable pip installs don't hot-load into already-running processes.** `uv pip install -e` (part of `get-app`) only updates the venv on disk -- the already-running `backend`/`queue-long`/`queue-short`/`scheduler`/`websocket` processes had `ticket_match_bridge` in their Python path only after a restart (`docker compose -p frappe-bench restart backend queue-long queue-short scheduler websocket`). Before that restart, every request 500'd site-wide (`ModuleNotFoundError: No module named 'ticket_match_bridge'` inside `setup_module_map`, since Frappe imports every installed app's modules on every request) -- a real, if brief, full-site outage caused by this deploy, not a pre-existing issue.
6. **`backend` and `frontend` do not share a `sites/assets` filesystem on this bench.** This is the big one for A3.3's Vue patch. Running `bench build --app helpdesk` inside `backend` only updates `backend`'s own copy of `sites/assets` -- `frontend` (the container that actually serves static files to the public) keeps serving whatever was baked into the shared image at the last CI build, with a different content hash for the same bundle (e.g. `backend` had `index-115ae3ec.js`, `frontend` still had `index-4fc3f462.js` from hours earlier). The result: the boot HTML (rendered/cached by `backend`) references a JS filename that doesn't exist on `frontend`, so every page load 404s the main bundle and the whole Vue SPA renders blank -- no console errors, just nothing mounts. Fixed for now with a one-off `docker cp` of `backend`'s current `sites/assets/helpdesk/desk/` into `frontend`. **This will not survive a `frontend` container recreation** -- broader than A3.3's existing warning about surviving a Helpdesk version bump; it means this manual sync step is needed after *any* `docker compose -p frappe-bench ... --force-recreate` on this bench (e.g. the next time RUNBOOK.md's "Adding a brand-new app" procedure runs for an unrelated app), not just a Helpdesk upgrade. Worth a real fix later (shared volume, or bake the patch into the CI image via `apps.json.tmpl` instead of a live in-place edit) rather than repeating this by hand indefinitely.
7. **Also hit and fixed along the way, not architecturally interesting:** the initial `docker compose up -d` on the EC2 box failed with `no space left on device` (`docker builder prune -af` freed 9.25GB of failed-attempt build cache, well within the 20GB volume); the EC2 security group's 80/443 rules were initially added to Outbound instead of Inbound, and Outbound was then briefly left with zero rules after correcting it -- both were console mistakes during setup, not something the plan or this codebase caused.

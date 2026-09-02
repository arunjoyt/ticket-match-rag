# Production Deployment

The executed reference for running `ticket-match-rag` in production. `docs/DEPLOYMENT_PLAN.md` is the plan that preceded this; this file is the copy of record once a step below actually runs.

This covers Part B of the plan only: `ticket-match-rag` on its own AWS EC2 instance. Part A (the `helpdesk.22logic.com` Helpdesk instance on the Contabo VPS bench) lives in `docs/DEPLOYMENT_PLAN.md` and stays there until it is executed and gets its own copy-of-record doc.

## Topology

```
Internet (443/80)
    └── Nginx
          └── ticket-match.<domain>  → FastAPI app (internal: 8000)
                  ├── Qdrant   (6333, loopback only)
                  └── Postgres (5432, loopback only) -- match cache

Contabo VPS (separate host)
    └── helpdesk.22logic.com
          └── Sends webhooks to https://ticket-match.<domain>/webhook/helpdesk
          └── Bridge app calls  https://ticket-match.<domain>/tickets/{name}/matches
```

Only nginx binds a public port. Every other service stays on Docker's default bridge network, reachable by service name (`app`, `qdrant`, `postgres`), with `qdrant`/`postgres` also loopback-bound on the host per `docker-compose.yml`.

ADR 0011 removed the Redis broker and the RQ `worker` container that the first production deploy ran — cache refreshes now run inside the `app` process. Redeploying onto that box needs `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build --remove-orphans` so the now-orphaned `redis` and `worker` containers are torn down.

## 1. Provision the instance

1. Launch a `t3.medium` (2 vCPU / 4GB) instance, Ubuntu 22.04 LTS, 20GB gp3.
2. Allocate an Elastic IP and associate it with the instance, so DNS survives a reboot or replacement.
3. Set the security group to allow inbound 22 (SSH, restrict to your IP), 80, and 443 (0.0.0.0/0) only. Do not open 6333 or 5432 — the compose port bindings already keep them loopback-only, so this is a second layer, not the only one.

## 2. DNS

Add an A record for `ticket-match.22logic.com` (or your domain) pointing at the Elastic IP, via the registrar's DNS panel. Wait for propagation before running `certbot` — it verifies ownership over HTTP on port 80.

## 3. Bootstrap the box

```bash
ssh ubuntu@<elastic-ip>
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER   # log out and back in after this
sudo apt install -y docker-compose-plugin certbot
```

## 4. Get the code and secrets onto the box

```bash
git clone https://github.com/arunjoyt/ticket-match-rag && cd ticket-match-rag
cp .env.example .env
```

Fill in `.env`:

- `HELPDESK_URL=https://helpdesk.22logic.com` (Part A's site).
- `HELPDESK_API_KEY` / `HELPDESK_API_SECRET` — from Part A's service-account key generation step.
- `WEBHOOK_SECRET` — generate with `openssl rand -hex 32`. Must match the secret set on Helpdesk's Webhook records (Part A).
- `API_KEY` — generate with `openssl rand -hex 32` (ADR 0009). `api/auth.py` fails closed if this is unset — every protected route returns 500, not a silent bypass. The bridge app sends this same value as `Authorization: Bearer <key>`.
- `API_DOMAIN=ticket-match.22logic.com` — bare hostname, no scheme. Drives `nginx/templates/ticket-match-rag.conf.template`.

Leave `QDRANT_URL` and `DATABASE_URL` as the `.env.example` defaults — `docker-compose.prod.yml` overrides them to the in-network service names (`qdrant`, `postgres`) for the `app` container.

## 5. TLS cert

```bash
sudo certbot certonly --standalone -d ticket-match.22logic.com
```

## 6. Launch

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps   # confirm all 4 services are up
```

## 7. Initial full ingest

Webhooks only cover tickets created or changed after they are registered. Existing Helpdesk data needs one manual full index:

```bash
curl -X POST https://ticket-match.22logic.com/ingest/full \
  -H "Authorization: Bearer <API_KEY>"
```

Watch progress with `docker compose -f docker-compose.yml -f docker-compose.prod.yml logs app -f`.

## 8. Verify

```bash
curl https://ticket-match.22logic.com/health   # no auth needed
curl -X POST https://ticket-match.22logic.com/ingest/full -H "Authorization: Bearer <API_KEY>"
curl https://ticket-match.22logic.com/tickets/<a-known-ticket-name>/matches -H "Authorization: Bearer <API_KEY>"
```

Confirm the response shape matches a known Duplicate Cluster from the seeded dataset — same check as the local dev verification.

Then, on the Helpdesk side (once Part A and the bridge app are wired up per `docs/DEPLOYMENT_PLAN.md`'s Part A3): open a real ticket in the agent UI and confirm the Similar Tickets panel shows resolution snippets.

## 9. Ongoing ops

- **Cert renewal cron**, on the host:
  ```
  0 3 * * * certbot renew --quiet && docker compose -f /home/ubuntu/ticket-match-rag/docker-compose.yml -f /home/ubuntu/ticket-match-rag/docker-compose.prod.yml exec nginx nginx -s reload
  ```
- **Deploys**: `git pull && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`.
- **`.env` changes vs. code changes** — `env_file: .env` only loads at container start. A value change (rotating `API_KEY` or `WEBHOOK_SECRET`) needs `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate app`. A code change needs a rebuild (`--build`), since the Dockerfile bakes the source into the image with `COPY . .` — `--force-recreate` alone reuses the old image and keeps running the old code.
- Every service has `restart: unless-stopped`, so the stack comes back up on its own after a host reboot or a Docker daemon restart.
- The Postgres match cache and the Qdrant index are both regenerable from Helpdesk data (`POST /ingest/full`, then reads repopulate cache rows on demand), so neither needs a backup schedule the way the Contabo Helpdesk site's data does. An EBS snapshot of the instance is enough if you want a faster recovery path than a full re-ingest.

## 10. Inspecting Qdrant / Postgres (SSH tunnel)

`docker-compose.yml` binds `qdrant` (6333) and `postgres` (5432) to `127.0.0.1` on the host, not `0.0.0.0` — reachable from your laptop over a tunnel, never from the public internet:

```bash
ssh -L 6333:localhost:6333 -L 5432:localhost:5432 ubuntu@<elastic-ip>
```

Then browse `http://localhost:6333/dashboard` for Qdrant, or connect a Postgres client to `localhost:5432` (`ticket_match` / `ticket_match`, database `ticket_match`) to inspect the match cache.

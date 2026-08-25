---
status: implemented
---

# Split ticket_match_bridge into its own installable Frappe app repo

Issue #12 (spun off from ADR 0006's Verification section): `frappe_bridge/ticket_match_bridge/` installed onto the Helpdesk bench via `pip install -e apps/ticket_match_bridge` + a manual `sites/apps.txt` edit, not `bench get-app`. ADR 0006 recorded that `bench get-app <path>` "failed against this bench version for a bare local path with no git remote," framed as a bench-version quirk.

Checked directly against the running dev bench (`ticket-rag-helpdesk-dev-frappe-1`): `apps/ticket_match_bridge` inside the container had no `.git` at all — it had been `docker cp`'d in by hand. That's the actual root cause, not a bench-version quirk: `bench get-app` clones a git repository. A directory that's just a subfolder of this monorepo, sharing its single `.git`, was never something `bench get-app` could point at, on any bench version.

## Decision

Give the bridge app its own repo: [`arunjoyt/ticket-match-bridge`](https://github.com/arunjoyt/ticket-match-bridge), public, kebab-case (matching this org's `ticket-match-rag` naming on GitHub — the Frappe `app_name`/Python module stays `ticket_match_bridge`, the usual relationship between a repo name and a module name for Frappe apps). It's now the single source of truth; the copy that used to live at `frappe_bridge/ticket_match_bridge/` in this repo is removed, not mirrored.

**Why remove rather than submodule/subtree:** both would keep two copies in sync for a four-file app with no doctypes. That's exactly the kind of tool oversized for the problem this project already avoids elsewhere (RQ over Celery, ADR 0006). `frappe_bridge/README.md` in this repo now just points at the new repo.

**Why a fresh initial commit instead of preserving history:** the app existed for two commits before this split (`820bb80`, `b1e61db`) — not enough history worth the complexity of a `git subtree split`/`filter-repo` extraction.

`frappe_bridge/helpdesk-vue-patch/` is unaffected — it isn't a Frappe app, just a durable copy of two hand-edited Helpdesk-side Vue files, and stays in this repo.

## Consequences

Install path is now the standard one, `bench get-app <url> && bench install-app`, in principle — in practice two more real bugs surfaced only once this bench actually tried to clone-and-build a from-scratch app, both fixed below rather than worked around. No more `pip install -e` / manual `apps.txt` workaround for the clone step itself, and no more special-casing this app's *source location* in setup instructions. `docs/ARCHITECTURE.md`'s key-files table and Helpdesk-native UI section point at the new repo. Production install (issue #5, not yet executed) uses this same install command against the real Helpdesk bench, and should hit the same two bugs below since they're about this bench version's `get-app` internals, not about where the app's source lives.

## Verification

Re-installed on the live dev bench, and found two more bugs beyond the git-repo fix itself:

**Bug 1 — `bench build` crashes on an app with zero frontend assets.** The hand-built app had no `public/` directory at all; `bench get-app`'s install step runs `bench build --app <name>` unconditionally, and esbuild's `get_all_files_to_build` threw `TypeError [ERR_INVALID_ARG_TYPE]` trying to resolve a path for an app with no `public/`. Fixed at the source: added an empty `ticket_match_bridge/public/.gitkeep` to the new repo, matching what `telephony` (a comparable install already on this bench with no real frontend) already has for the same reason.

**Bug 2 — `bench get-app` writes `sites/apps.json` but not `sites/apps.txt` before building.** Even with `public/.gitkeep` in place, the same crash recurred — `sites/apps.txt` (not `apps.json`) is what esbuild's `get_public_path()` reads to resolve an app's path, and `bench get-app`'s internal `install_app` call apparently runs `build_assets` before `apps.txt` is updated, so a genuinely new app isn't visible to its own first build. Confirmed by reading `esbuild/utils.js`. Worked around by manually appending `ticket_match_bridge` to `sites/apps.txt` between the (partially-completed) `get-app` clone and re-running `bench build --app ticket_match_bridge`, which then succeeded; `bench --site helpdesk.localhost install-app ticket_match_bridge` completed cleanly after that. This is a bench-tool ordering bug, unrelated to this app or this ADR's decision — noted here since it'll recur on the production install (issue #5) and shouldn't be mistaken for a repeat of Bug 1 or of ADR 0006's original finding.

**Also needed:** the container's `frappe serve` dev process had the old app's absence baked into its Python process state (`ModuleNotFoundError: No module named 'ticket_match_bridge'` on the first page load after install) — a `docker restart` on the Helpdesk dev container picked up the newly `pip install -e`'d module. Expected for any long-running dev server after installing a new app; not specific to this change.

Confirmed installed: `bench --site helpdesk.localhost list-apps` shows `ticket_match_bridge` at `main` (previously `UNVERSIONED`, evidence it's a real git checkout now, not a copy). `ticket_match_api_url` in `site_config.json` was untouched by the uninstall/reinstall. Browser-verified end-to-end: opened ticket `0121` in Helpdesk's agent UI, expanded "Similar Tickets," and got 5 Matches with resolution snippets, status, and date — same check ADR 0006 used, now against the app installed the standard way.

**Status: implemented.**

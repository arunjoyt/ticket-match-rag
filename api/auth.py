"""Static shared-secret API key, for service-to-service callers.

Every caller here (the Frappe bridge app, ops running /ingest/full by hand)
is a trusted backend/operator, not an individual end user -- unlike Contract
Intelligence's JWT-based per-user role gate, there's no login flow or
per-caller identity to model here. A single shared Bearer token matches that
trust model and this codebase's existing pattern for shared secrets
(WEBHOOK_SECRET's HMAC check in ingestion/webhook_handler.py).

Fails closed: an unset API_KEY rejects every protected request outright,
never validated against an empty-string key -- same rule as WEBHOOK_SECRET,
see contract_intelligence_carryforward memory item 2.
"""

from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import config

_bearer = HTTPBearer(auto_error=False)


def require_api_key(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    if not config.API_KEY:
        raise HTTPException(status_code=500, detail="API key not configured")
    if creds is None or not hmac.compare_digest(creds.credentials, config.API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

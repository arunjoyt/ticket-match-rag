"""Standalone demo UI: pick a Query Ticket, see its Matches.

Decoupled from Frappe Helpdesk's real interface -- a demo page, not a
Client Script embedded in Helpdesk's own agent UI (see the
open_decisions_resolved memory's "Option A"). Retrieval only, no
generation -- see CONTEXT.md for the domain model this renders and
api/main.py for the pipeline it calls.

Run with: streamlit run ui/app.py
Requires the API (api/main.py) already running and reachable at
config.API_BASE_URL.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import requests
import streamlit as st

# streamlit sets sys.path[0] to this file's own directory, not the project
# root -- add the root so `import config` resolves the same as everywhere
# else in the codebase.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

st.set_page_config(page_title="Ticket Match RAG", page_icon="🎫")
st.title("Ticket Match RAG")
st.caption("Pick a Query Ticket to see similar past resolutions. Retrieval only -- no generation.")


def strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "").strip()


@st.cache_data(ttl=30)
def fetch_queryable_tickets() -> list[dict]:
    resp = requests.get(f"{config.API_BASE_URL}/tickets/queryable", timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_matches(ticket_name: str) -> list[dict]:
    resp = requests.get(f"{config.API_BASE_URL}/tickets/{ticket_name}/matches", timeout=60)
    resp.raise_for_status()
    return resp.json()


try:
    tickets = fetch_queryable_tickets()
except requests.RequestException as exc:
    st.error(f"Can't reach the API at {config.API_BASE_URL}: {exc}")
    st.stop()

if not tickets:
    st.info("No open tickets in Helpdesk right now -- nothing to pick as a Query Ticket.")
    st.stop()

options = {f"{t['name']} — {t['subject']}": t for t in tickets}
selected_label = st.selectbox("Query Ticket", options.keys())
selected = options[selected_label]

st.subheader(selected["subject"])
st.write(strip_html(selected["description"]))

st.divider()

with st.spinner("Retrieving Matches (first run loads the models, can take a bit)..."):
    try:
        matches = fetch_matches(selected["name"])
    except requests.RequestException as exc:
        st.error(f"Match lookup failed: {exc}")
        st.stop()

if not matches:
    st.info("No Matches above the confidence threshold for this ticket.")
else:
    st.subheader(f"{len(matches)} Match{'es' if len(matches) != 1 else ''}")
    for match in matches:
        with st.container(border=True):
            st.markdown(f"**{match['subject']}**")
            st.caption(f"{match['ticket_name']} · confidence {match['score']:.3f}")
            st.write(strip_html(match["description"]))
            st.markdown("**Resolution:**")
            st.write(strip_html(match["resolution_details"]))

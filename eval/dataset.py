"""Builds eval queries, qrels, and near-miss lookups from data/seed_manifest.json.

Ticket text (subject/description) is fetched live from Helpdesk at eval time,
not duplicated into the manifest -- Helpdesk is the source of truth (see
ingestion/helpdesk_client.py). The manifest only records ground truth: which
ticket belongs to which Duplicate Cluster, each cluster member's variant, and
each distractor's kind (ADR 0003).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "data" / "seed_manifest.json"

RELEVANT_GRADE = 2


@dataclass
class EvalQuery:
    ticket_name: str
    cluster_id: str
    variant: str


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    return json.loads(path.read_text())


def build_queries(manifest: dict) -> list[EvalQuery]:
    """One query per Duplicate Cluster member (held out against its siblings)."""
    return [
        EvalQuery(ticket_name=member["ticket_name"], cluster_id=cluster["id"], variant=member["variant"])
        for cluster in manifest["clusters"]
        for member in cluster["members"]
    ]


def build_qrels(manifest: dict) -> dict[str, dict[str, int]]:
    """query ticket_name -> {relevant sibling ticket_name: grade}."""
    qrels: dict[str, dict[str, int]] = {}
    for cluster in manifest["clusters"]:
        names = [member["ticket_name"] for member in cluster["members"]]
        for query_name in names:
            qrels[query_name] = {other: RELEVANT_GRADE for other in names if other != query_name}
    return qrels


def near_miss_by_cluster(manifest: dict) -> dict[str, str]:
    """cluster_id -> ticket_name of its near-miss distractor (ADR 0003 hard negative)."""
    return {
        d["adjacent_to"]: d["ticket_name"]
        for d in manifest["distractors"]
        if d.get("kind") == "near-miss"
    }

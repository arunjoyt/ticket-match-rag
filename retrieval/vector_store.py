"""Qdrant wrapper.

Point IDs are deterministic: ``uuid5(NAMESPACE_DNS, ticket_name)`` -- no chunk
index, since a Ticket Record is whole-record, never chunked. This makes
upserts idempotent: re-indexing a ticket overwrites its existing point rather
than creating a duplicate.
"""

from __future__ import annotations

import uuid

from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PointStruct,
    VectorParams,
)

import config


def point_id(ticket_name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, ticket_name))


class VectorStore:
    def __init__(
        self,
        url: str = config.QDRANT_URL,
        collection: str = config.QDRANT_COLLECTION,
        vector_size: int | None = None,
    ) -> None:
        self._client = QdrantClient(url=url)
        self._collection = collection
        self._vector_size = vector_size

    def ensure_collection(self, vector_size: int) -> None:
        self._vector_size = vector_size
        existing = {c.name for c in self._client.get_collections().collections}
        if self._collection not in existing:
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            )

    def upsert_ticket(self, ticket_name: str, vector: list[float], payload: dict) -> None:
        point = PointStruct(id=point_id(ticket_name), vector=vector, payload=payload)
        self._client.upsert(collection_name=self._collection, points=[point])

    def delete_by_ticket_name(self, ticket_name: str) -> None:
        self._client.delete(
            collection_name=self._collection,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[FieldCondition(key="ticket_name", match=MatchValue(value=ticket_name))]
                )
            ),
        )

    def search(self, vector: list[float], top_k: int) -> list[dict]:
        results = self._client.search(
            collection_name=self._collection, query_vector=vector, limit=top_k
        )
        return [{**r.payload, "_qdrant_score": r.score} for r in results]

    def get_all_match_texts(self) -> list[dict]:
        """All indexed payloads -- used to rebuild the BM25 index from scratch."""
        docs: list[dict] = []
        offset = None
        while True:
            points, offset = self._client.scroll(
                collection_name=self._collection, limit=100, offset=offset
            )
            docs.extend(p.payload for p in points)
            if offset is None:
                break
        return docs

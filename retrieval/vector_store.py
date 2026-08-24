"""Qdrant wrapper.

Point IDs are deterministic: ``uuid5(NAMESPACE_DNS, ticket_name)`` -- no chunk
index, since a Ticket Record is whole-record, never chunked. This makes
upserts idempotent: re-indexing a ticket overwrites its existing point rather
than creating a duplicate.

Each point carries two named vectors, both incremental per-point
upsert/delete (see ADR 0002): a dense vector for semantic similarity and a
sparse (bm25) vector for lexical similarity. The sparse field's
``Modifier.IDF`` makes Qdrant maintain corpus-wide IDF statistics
server-side as points are upserted/deleted -- there is no full-corpus BM25
rebuild anywhere in this codebase. Querying fuses both sides server-side via
``query_points``' RRF fusion, replacing a hand-rolled Python RRF loop.
"""

from __future__ import annotations

import uuid

from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    Fusion,
    FusionQuery,
    MatchValue,
    Modifier,
    PointStruct,
    Prefetch,
    SparseVectorParams,
    VectorParams,
)
from qdrant_client.http.models import SparseVector as QdrantSparseVector

import config
from ingestion.embedder import SparseVector

DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "bm25"


def point_id(ticket_name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, ticket_name))


class VectorStore:
    def __init__(
        self,
        url: str = config.QDRANT_URL,
        collection: str = config.QDRANT_COLLECTION,
    ) -> None:
        self._client = QdrantClient(url=url)
        self._collection = collection

    def ensure_collection(self, dense_size: int) -> None:
        existing = {c.name for c in self._client.get_collections().collections}
        if self._collection not in existing:
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config={DENSE_VECTOR_NAME: VectorParams(size=dense_size, distance=Distance.COSINE)},
                sparse_vectors_config={SPARSE_VECTOR_NAME: SparseVectorParams(modifier=Modifier.IDF)},
            )

    def upsert_ticket(
        self, ticket_name: str, dense_vector: list[float], sparse_vector: SparseVector, payload: dict
    ) -> None:
        point = PointStruct(
            id=point_id(ticket_name),
            vector={
                DENSE_VECTOR_NAME: dense_vector,
                SPARSE_VECTOR_NAME: _to_qdrant_sparse(sparse_vector),
            },
            payload=payload,
        )
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

    def hybrid_search(self, dense_vector: list[float], sparse_vector: SparseVector, top_k: int) -> list[dict]:
        response = self._client.query_points(
            collection_name=self._collection,
            prefetch=[
                Prefetch(query=dense_vector, using=DENSE_VECTOR_NAME, limit=top_k),
                Prefetch(query=_to_qdrant_sparse(sparse_vector), using=SPARSE_VECTOR_NAME, limit=top_k),
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=top_k,
        )
        return [{**point.payload, "_qdrant_score": point.score} for point in response.points]


def _to_qdrant_sparse(sparse_vector: SparseVector) -> QdrantSparseVector:
    return QdrantSparseVector(indices=sparse_vector.indices, values=sparse_vector.values)

"""Local sentence-transformers embedding wrapper.

`match_text` is the one place the "title + description only, no resolution"
matching-text rule (CONTEXT.md: Match) is implemented -- it's used identically
by the indexing path and the query path, which is what keeps matching
symmetric (see the Ticket Match RAG grill session's Option B decision).
"""

from __future__ import annotations

from dataclasses import dataclass

from fastembed import SparseTextEmbedding
from sentence_transformers import SentenceTransformer

import config


def match_text(subject: str, description: str) -> str:
    return f"{subject}\n\n{_strip_html(description)}"


def _strip_html(html: str) -> str:
    import re

    return re.sub(r"<[^>]+>", " ", html or "").strip()


class Embedder:
    def __init__(self, model: str = config.EMBEDDING_MODEL) -> None:
        self._model = SentenceTransformer(model)

    def dimension(self) -> int:
        return self._model.get_sentence_embedding_dimension()

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._model.encode(texts).tolist()

    def embed_query(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]


@dataclass
class SparseVector:
    indices: list[int]
    values: list[float]


class SparseEmbedder:
    """BM25 sparse vectors via fastembed, upserted per-point like dense
    vectors (see ADR 0002). Document- and query-side encoding are distinct,
    unlike dense embedding: `embed_document` carries term-frequency +
    doc-length normalization only, no corpus knowledge; Qdrant's
    `Modifier.IDF` (retrieval/vector_store.py) supplies corpus-wide IDF
    server-side, incrementally, as points are upserted/deleted -- that's
    what eliminates the BM25Okapi full-corpus rebuild this replaces.
    """

    def __init__(self, model: str = config.SPARSE_MODEL) -> None:
        self._model = SparseTextEmbedding(model)

    def embed_documents(self, texts: list[str]) -> list[SparseVector]:
        if not texts:
            return []
        return [_to_sparse_vector(e) for e in self._model.embed(texts)]

    def embed_document(self, text: str) -> SparseVector:
        return self.embed_documents([text])[0]

    def embed_query(self, text: str) -> SparseVector:
        return _to_sparse_vector(next(iter(self._model.query_embed([text]))))


def _to_sparse_vector(embedding) -> SparseVector:
    return SparseVector(indices=embedding.indices.tolist(), values=embedding.values.tolist())

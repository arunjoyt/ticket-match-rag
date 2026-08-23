"""Local sentence-transformers embedding wrapper.

`match_text` is the one place the "title + description only, no resolution"
matching-text rule (CONTEXT.md: Match) is implemented -- it's used identically
by the indexing path and the query path, which is what keeps matching
symmetric (see the Ticket Match RAG grill session's Option B decision).
"""

from __future__ import annotations

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

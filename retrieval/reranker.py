"""Cross-encoder reranker.

Unlike Contract Intelligence's reranker, this one returns scores alongside
candidates -- Match Threshold (CONTEXT.md) gates on the reranker's score, not
the hybrid-search fusion score, so callers need the actual value.
"""

from __future__ import annotations

from sentence_transformers import CrossEncoder

import config


class Reranker:
    def __init__(self, model: str = config.RERANK_MODEL) -> None:
        self._model_name = model
        self._model: CrossEncoder | None = None

    def warm_up(self) -> None:
        if self._model is None:
            self._model = CrossEncoder(self._model_name)

    def rerank(self, query_text: str, candidates: list[dict]) -> list[tuple[dict, float]]:
        if not candidates:
            return []
        self.warm_up()
        pairs = [(query_text, c["match_text"]) for c in candidates]
        scores = self._model.predict(pairs)  # type: ignore[union-attr]
        ranked = sorted(zip(candidates, scores), key=lambda pair: pair[1], reverse=True)
        return [(candidate, float(score)) for candidate, score in ranked]

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from common.models import KnowledgeChunk
from common.utils import cosine_similarity


class LightweightVectorStore:
    DIMENSIONS = 64

    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.store_path = self.directory / "store.json"
        self.records: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if self.store_path.exists():
            with self.store_path.open("r", encoding="utf-8") as file:
                raw = json.load(file)
            if isinstance(raw, dict):
                self.records = raw

    def _persist(self) -> None:
        with self.store_path.open("w", encoding="utf-8") as file:
            json.dump(self.records, file, ensure_ascii=False, indent=2)

    def _tokenize(self, text: str) -> list[str]:
        return re.findall(r"[\u4e00-\u9fff]{1}|[a-z0-9_]+", text.lower())

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.DIMENSIONS
        tokens = self._tokenize(text)
        if not tokens:
            return vector

        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.DIMENSIONS
            sign = 1 if digest[4] % 2 == 0 else -1
            vector[index] += sign * (1.0 + len(token) / 10.0)

        magnitude = math.sqrt(sum(item * item for item in vector))
        if magnitude == 0:
            return vector
        return [item / magnitude for item in vector]

    def upsert_chunks(self, chunks: list[KnowledgeChunk]) -> None:
        for chunk in chunks:
            self.records[chunk.chunk_id] = {
                "chunk_id": chunk.chunk_id,
                "text": chunk.text,
                "vector": self.embed(chunk.text),
                "metadata": chunk.to_dict(),
            }
        self._persist()

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        query_vector = self.embed(query)
        scored: list[tuple[float, dict[str, Any]]] = []
        for record in self.records.values():
            scored.append((cosine_similarity(query_vector, record["vector"]), record))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [{"score": round(score, 4), **record} for score, record in scored[:top_k]]

    def find_duplicate(self, text: str, threshold: float = 0.9) -> dict[str, Any] | None:
        query_vector = self.embed(text)
        best_score = -1.0
        best_record: dict[str, Any] | None = None
        for record in self.records.values():
            score = cosine_similarity(query_vector, record["vector"])
            if score > best_score:
                best_score = score
                best_record = record
        if best_record and best_score >= threshold:
            return {"score": round(best_score, 4), **best_record}
        return None

    def size(self) -> int:
        return len(self.records)

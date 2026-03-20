from __future__ import annotations

from common.models import KnowledgeChunk, RawContent
from common.utils import clean_text, slugify, split_into_chunks

from agents.base import BaseAgent


class KnowledgeCurator(BaseAgent):
    def curate(self, raw_contents: list[RawContent]) -> dict[str, object]:
        new_chunks: list[KnowledgeChunk] = []
        updated = 0
        skipped = 0

        for content in raw_contents:
            cleaned_body = clean_text(content.body)
            quality_score = self._score_quality(cleaned_body, content.engagement)
            heat_score = self._score_heat(content.engagement)
            source_id = slugify(content.source_url)

            self.db.upsert_knowledge_source(
                source_id=source_id,
                source_url=content.source_url,
                title=content.title,
                body=cleaned_body,
                topic=content.topic or content.title,
                tags=content.tags,
                engagement=content.engagement,
                heat_score=heat_score,
                quality_score=quality_score,
                metadata={
                    "crawled_at": content.crawled_at,
                    "source_type": "public_note",
                },
            )

            chunks = split_into_chunks(cleaned_body)
            for index, chunk_text in enumerate(chunks):
                duplicate = self.vector_store.find_duplicate(chunk_text, threshold=0.95)
                if duplicate:
                    updated += 1
                    continue
                new_chunks.append(
                    KnowledgeChunk(
                        chunk_id=f"{source_id}-chunk-{index}",
                        topic=content.topic or content.title,
                        text=chunk_text,
                        tags=content.tags,
                        source_url=content.source_url,
                        heat_score=heat_score,
                        quality_score=quality_score,
                        metadata={"title": content.title},
                    )
                )
            if not chunks:
                skipped += 1

        if new_chunks:
            self.vector_store.upsert_chunks(new_chunks)

        stats = {
            "storage_path": str(self.vector_store.store_path),
            "dedupe": {"updated": updated, "skipped": skipped},
            "ingestion": {
                "new_chunks": len(new_chunks),
                "total_sources": len(raw_contents),
                "vector_store_size": self.vector_store.size(),
            },
        }
        self.logger.info("Curated %s sources into %s new chunks", len(raw_contents), len(new_chunks))
        return stats

    def _score_quality(self, body: str, engagement: dict[str, int]) -> float:
        info_density = min(len(body) / 20, 30)
        first_person_bonus = 15 if "我" in body else 0
        engagement_bonus = min(sum(engagement.values()) / 300, 35)
        return round(min(info_density + first_person_bonus + engagement_bonus, 100), 2)

    def _score_heat(self, engagement: dict[str, int]) -> float:
        weighted = engagement.get("likes", 0) + engagement.get("collects", 0) * 1.2 + engagement.get("comments", 0) * 1.5
        return round(min(weighted / 60, 100), 2)

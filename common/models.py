from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    RETRY = "RETRY"
    MANUAL_REVIEW = "MANUAL_REVIEW"


@dataclass(slots=True)
class TopicCandidate:
    keyword: str
    heat_score: int
    competition: str
    angle_suggestion: str
    sample_notes: list[str] = field(default_factory=list)
    source: str = "simulated"
    category: str = "general"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RawContent:
    source_url: str
    title: str
    body: str
    tags: list[str]
    engagement: dict[str, int]
    crawled_at: str
    topic: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class KnowledgeChunk:
    chunk_id: str
    topic: str
    text: str
    tags: list[str]
    source_url: str
    heat_score: float
    quality_score: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class GeneratedContent:
    title: str
    body: str
    tags: list[str]
    referenced_sources: list[str]
    persona: dict[str, str]
    review_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ImageInsight:
    image_path: str
    inferred_topic: str
    content_mode: str
    summary: str
    visible_text: list[str]
    keywords: list[str]
    facts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ReviewResult:
    passed: bool
    score: int
    issues: list[str]
    suggestions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CoverAsset:
    image_path: str
    html_path: str
    template_name: str
    palette: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PublishRecord:
    note_id: str
    publish_time: str
    status: str
    error_log: str
    engagement_24h: dict[str, int] = field(default_factory=dict)
    content_id: str = ""
    real_note_id: str = ""
    note_xsec_token: str = ""
    note_url: str = ""
    matched_via: str = ""
    synced_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

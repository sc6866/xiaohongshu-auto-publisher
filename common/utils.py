from __future__ import annotations

import hashlib
import math
import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


CHINESE_SENTENCE_BREAK = re.compile(r"(?<=[。！？!?；;])")


def now_local(tz_name: str) -> datetime:
    try:
        return datetime.now(ZoneInfo(tz_name))
    except ZoneInfoNotFoundError:
        if tz_name == "Asia/Shanghai":
            return datetime.now(timezone(timedelta(hours=8)))
        return datetime.now().astimezone()


def now_utc_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def slugify(text: str, fallback: str = "item") -> str:
    lowered = text.strip().lower()
    cleaned = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", lowered, flags=re.IGNORECASE)
    cleaned = cleaned.strip("-")
    return cleaned or fallback


def clean_text(text: str) -> str:
    text = text.replace("\u3000", " ")
    text = re.sub(r"[\r\t]+", " ", text)
    text = re.sub(r"[ ]{2,}", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_into_chunks(text: str, min_chars: int = 100, max_chars: int = 200) -> list[str]:
    text = clean_text(text)
    if len(text) <= max_chars:
        return [text] if text else []

    sentences: list[str] = []
    for block in text.split("\n"):
        block = block.strip()
        if not block:
            continue
        sentences.extend(part.strip() for part in CHINESE_SENTENCE_BREAK.split(block) if part.strip())

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current}{sentence}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current.strip())
        current = sentence

    if current.strip():
        chunks.append(current.strip())

    normalized: list[str] = []
    buffer = ""
    for chunk in chunks:
        if len(chunk) < min_chars:
            buffer = f"{buffer}{chunk}"
            if len(buffer) >= min_chars:
                normalized.append(buffer.strip())
                buffer = ""
            continue
        if buffer:
            normalized.append(buffer.strip())
            buffer = ""
        normalized.append(chunk.strip())

    if buffer.strip():
        normalized.append(buffer.strip())
    return normalized


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    mag_a = math.sqrt(sum(a * a for a in vec_a))
    mag_b = math.sqrt(sum(b * b for b in vec_b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def deterministic_score(text: str, floor: int = 60, ceiling: int = 95) -> int:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    span = max(ceiling - floor, 1)
    return floor + (int(digest[:8], 16) % (span + 1))


def within_publish_window(current_time: datetime, windows: list[str]) -> bool:
    for window in windows:
        start, end = window.split("-")
        start_hour, start_minute = [int(part) for part in start.split(":")]
        end_hour, end_minute = [int(part) for part in end.split(":")]
        start_dt = current_time.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
        end_dt = current_time.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
        if start_dt <= current_time <= end_dt:
            return True
    return False


def next_publish_time(current_time: datetime, windows: list[str]) -> datetime:
    if within_publish_window(current_time, windows):
        return current_time + timedelta(minutes=15)

    candidate_times: list[datetime] = []
    for window in windows:
        start, _ = window.split("-")
        start_hour, start_minute = [int(part) for part in start.split(":")]
        start_today = current_time.replace(
            hour=start_hour,
            minute=start_minute,
            second=0,
            microsecond=0,
        )
        if start_today > current_time:
            candidate_times.append(start_today)
        else:
            candidate_times.append(start_today + timedelta(days=1))
    return min(candidate_times)


def pick_template_by_tags(tags: list[str]) -> str:
    joined = " ".join(tags).lower()
    if any(token in joined for token in ("美食", "探店", "早餐", "夜市", "咖啡", "开封")):
        return "food_warm"
    if any(token in joined for token in ("ipad", "效率", "学习", "数码", "工具")):
        return "tech_cool"
    if any(token in joined for token in ("情绪", "治愈", "护肤", "穿搭", "情感")):
        return "emotion_pink"
    return "default"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

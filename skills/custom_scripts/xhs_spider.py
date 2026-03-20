from __future__ import annotations

from typing import Any

from common.config import Settings
from common.models import RawContent
from common.utils import clean_text, deterministic_score, now_utc_iso
from common.xhs_mcp_client import XhsMcpClient


class XhsSpider:
    """
    Safe first-version spider.

    It prefers real public-note fetching through the Xiaohongshu MCP service.
    When that path is unavailable, it falls back to structured mock notes so the
    rest of the pipeline can still be exercised locally.
    """

    def __init__(self, simulate: bool = True, settings: Settings | None = None, client: XhsMcpClient | None = None):
        self.simulate = simulate
        self.settings = settings
        self.client = client

    def crawl_topic(self, topic: str, limit: int = 3) -> list[RawContent]:
        if self.client and self.client.is_configured():
            try:
                self.client.ensure_ready()
                login_status = self.client.check_login_status()
                if login_status.get("ok"):
                    live_results = self._crawl_topic_live(topic, limit)
                    if live_results:
                        return live_results
            except Exception:
                if not self.simulate:
                    raise
        if not self.simulate:
            raise RuntimeError(f"Live crawl failed for topic: {topic}")
        return [self._build_sample_note(topic, index) for index in range(limit)]

    def _crawl_topic_live(self, topic: str, limit: int) -> list[RawContent]:
        search_result = self.client.search_feeds(topic)
        feeds = []
        if isinstance(search_result.get("data"), dict):
            feeds = search_result["data"].get("feeds") or []
        if not isinstance(feeds, list):
            return []

        items: list[RawContent] = []
        for feed in feeds[:limit]:
            if not isinstance(feed, dict):
                continue
            feed_id = str(feed.get("id") or "")
            xsec_token = str(feed.get("xsecToken") or "")
            if not feed_id or not xsec_token:
                continue

            detail_result = self.client.get_feed_detail(feed_id, xsec_token)
            detail_payload = detail_result.get("data")
            note_card = feed.get("noteCard") or {}
            interact = note_card.get("interactInfo") or {}

            title = self._pick_first_text(
                [
                    self._deep_get(detail_payload, {"title", "displayTitle", "noteTitle"}),
                    note_card.get("displayTitle"),
                ]
            )
            body = self._pick_first_text(
                [
                    self._deep_get(detail_payload, {"desc", "content", "description", "noteContent", "noteDesc"}),
                    title,
                ]
            )
            tags = self._extract_tags(detail_payload, topic)
            engagement = {
                "likes": self._safe_int(self._deep_get(detail_payload, {"likedCount", "likeCount"}), self._safe_int(interact.get("likedCount"), 0)),
                "collects": self._safe_int(self._deep_get(detail_payload, {"collectedCount", "collectCount"}), self._safe_int(interact.get("collectedCount"), 0)),
                "comments": self._safe_int(self._deep_get(detail_payload, {"commentCount"}), self._safe_int(interact.get("commentCount"), 0)),
            }

            if not body:
                continue

            items.append(
                RawContent(
                    source_url=f"https://www.xiaohongshu.com/explore/{feed_id}?xsec_token={xsec_token}",
                    title=title or f"{topic} 实测记录",
                    body=clean_text(body),
                    tags=tags,
                    engagement=engagement,
                    crawled_at=now_utc_iso(),
                    topic=topic,
                )
            )
        return items

    def _build_sample_note(self, topic: str, index: int) -> RawContent:
        likes = deterministic_score(f"{topic}-likes-{index}", 900, 3200)
        collects = deterministic_score(f"{topic}-collects-{index}", 200, 1800)
        comments = deterministic_score(f"{topic}-comments-{index}", 50, 400)
        price_a = 35 + index * 10
        price_b = 60 + index * 20
        title = f"{topic} 实测第 {index + 1} 条：哪些细节最影响体验"
        body = clean_text(
            f"""
            这条关于 {topic} 的样例素材，我故意保留了小红书常见的真人表达方式。

            我前后对比了 {index + 2} 种做法，最后发现真正决定体验的，通常不是最低价，而是有没有把流程问清楚。

            最容易被收藏的内容通常包含两个部分：一是 {price_a} 到 {price_b} 这种具体数字，二是“我当时以为……结果发现……”这种反差。

            如果你后面要生成正文，可以优先围绕价格、时间、路线、适合谁和不适合谁去组织结构，这样更像真实经验，不像空话总结。
            """
        )
        tags = [topic, "经验分享", "真实体验", "少踩坑"]
        return RawContent(
            source_url=f"https://www.xiaohongshu.com/explore/mock-{topic}-{index}",
            title=title,
            body=body,
            tags=tags,
            engagement={
                "likes": likes,
                "collects": collects,
                "comments": comments,
            },
            crawled_at=now_utc_iso(),
            topic=topic,
        )

    def _extract_tags(self, payload: Any, topic: str) -> list[str]:
        tags: list[str] = [topic]
        for tag in self._collect_values(payload, {"tag", "tags", "topic", "topics", "name"}):
            value = clean_text(str(tag)).strip("#")
            if 1 < len(value) <= 20 and value not in tags:
                tags.append(value)
            if len(tags) >= 5:
                break
        if "经验分享" not in tags:
            tags.append("经验分享")
        return tags[:5]

    def _collect_values(self, value: Any, target_keys: set[str]) -> list[Any]:
        found: list[Any] = []
        if isinstance(value, dict):
            for key, item in value.items():
                if key in target_keys:
                    if isinstance(item, list):
                        found.extend(item)
                    else:
                        found.append(item)
                found.extend(self._collect_values(item, target_keys))
        elif isinstance(value, list):
            for item in value:
                found.extend(self._collect_values(item, target_keys))
        return found

    def _deep_get(self, value: Any, target_keys: set[str]) -> Any:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in target_keys and item not in (None, "", [], {}):
                    return item
                nested = self._deep_get(item, target_keys)
                if nested not in (None, "", [], {}):
                    return nested
        elif isinstance(value, list):
            for item in value:
                nested = self._deep_get(item, target_keys)
                if nested not in (None, "", [], {}):
                    return nested
        return None

    def _pick_first_text(self, values: list[Any]) -> str:
        for value in values:
            if isinstance(value, str) and value.strip():
                return clean_text(value)
        return ""

    def _safe_int(self, value: Any, default: int = 0) -> int:
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return default

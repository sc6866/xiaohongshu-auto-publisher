from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from agents.base import BaseAgent
from agents.content_generator import ContentGenerator
from agents.cover_generator import CoverGenerator
from agents.image_insight_agent import ImageInsightAgent
from agents.knowledge_curator import KnowledgeCurator
from agents.originality_reviewer import OriginalityReviewer
from agents.publish_manager import PublishManager
from agents.trend_scanner import TrendScanner
from common.config import Settings
from common.db import Database
from common.models import GeneratedContent, TaskStatus
from common.vector_store import LightweightVectorStore
from common.xhs_mcp_client import XhsMcpClient
from skills.custom_scripts.xhs_spider import XhsSpider


class XiaohongshuManager(BaseAgent):
    def __init__(self, settings: Settings):
        db = Database(settings.db_path)
        vector_store = LightweightVectorStore(settings.vector_db_dir)
        super().__init__(settings, db, vector_store)
        self.xhs_client = XhsMcpClient(settings)
        self.trend_scanner = TrendScanner(settings, db, vector_store)
        self.spider = XhsSpider(
            simulate=settings.get("runtime", "simulate", True),
            settings=settings,
            client=self.xhs_client,
        )
        self.knowledge_curator = KnowledgeCurator(settings, db, vector_store)
        self.content_generator = ContentGenerator(settings, db, vector_store)
        self.image_insight_agent = ImageInsightAgent(settings, db, vector_store)
        self.originality_reviewer = OriginalityReviewer(settings, db, vector_store)
        self.cover_generator = CoverGenerator(settings, db, vector_store)
        self.publish_manager = PublishManager(settings, db, vector_store, client=self.xhs_client)

    def scan_and_ingest(self, manual_topics: list[str] | None = None) -> dict[str, object]:
        task_id = self.db.create_task("trend_scan", {"manual_topics": manual_topics or []})
        self.db.update_task_status(task_id, TaskStatus.PROCESSING)
        try:
            topics = self.trend_scanner.scan(manual_topics=manual_topics)
            ingested: list[dict[str, object]] = []
            samples_per_topic = int(self.settings.get("spider", "samples_per_topic", 3))
            for topic in topics[:3]:
                raw_contents = self.spider.crawl_topic(topic.keyword, limit=samples_per_topic)
                stats = self.knowledge_curator.curate(raw_contents)
                ingested.append({"topic": topic.to_dict(), "stats": stats})
            self.db.update_task_status(task_id, TaskStatus.COMPLETED)
            return {"topics": [item["topic"] for item in ingested], "ingested": ingested}
        except Exception as exc:  # noqa: BLE001
            self.db.update_task_status(task_id, TaskStatus.RETRY, error_message=str(exc), increment_retry=True)
            raise

    def produce_content(self, topic: str | None = None) -> dict[str, object]:
        if self.vector_store.size() == 0:
            self.scan_and_ingest(manual_topics=[topic] if topic else None)

        chosen_topic = topic or self._pick_best_topic()
        if topic or not self.db.list_knowledge_sources(topic=chosen_topic, limit=1):
            raw_contents = self.spider.crawl_topic(
                chosen_topic,
                limit=int(self.settings.get("spider", "samples_per_topic", 3)),
            )
            self.knowledge_curator.curate(raw_contents)

        task_id = self.db.create_task("content_production", {"topic": chosen_topic})
        self.db.update_task_status(task_id, TaskStatus.PROCESSING)
        try:
            content = self.content_generator.generate(chosen_topic)
            content, review = self._review_until_pass(chosen_topic, content)
            if not review.passed:
                self.db.update_task_status(task_id, TaskStatus.MANUAL_REVIEW, error_message="review_failed")
                return {
                    "status": "manual_review",
                    "topic": chosen_topic,
                    "content": content.to_dict(),
                    "review": review.to_dict(),
                }

            cover = self.cover_generator.generate(content)
            content_id = f"{self.settings.get('runtime', 'account_name', 'default')}:{uuid4().hex}"
            scheduled_time = self.publish_manager.suggest_publish_time()
            self.db.save_generated_content(
                content_id=content_id,
                content=content,
                review_score=review.score,
                status="APPROVED",
                scheduled_time=scheduled_time,
                cover_image_path=cover.image_path,
                cover_html_path=cover.html_path,
                publish_image_paths=[],
                image_analysis={},
                generation_meta={
                    "source": "topic",
                    "topic": chosen_topic,
                },
            )
            self.db.update_task_status(task_id, TaskStatus.COMPLETED)
            return {
                "status": "approved",
                "content_id": content_id,
                "scheduled_time": scheduled_time,
                "content": content.to_dict(),
                "review": review.to_dict(),
                "cover": cover.to_dict(),
            }
        except Exception as exc:  # noqa: BLE001
            self.db.update_task_status(task_id, TaskStatus.RETRY, error_message=str(exc), increment_retry=True)
            raise

    def produce_from_images(
        self,
        image_paths: list[str],
        angle: str | None = None,
        mode: str | None = None,
        style_strength: str | None = None,
    ) -> dict[str, object]:
        task_id = self.db.create_task(
            "image_content_production",
            {
                "image_paths": image_paths,
                "angle": angle,
                "mode": mode,
                "style_strength": style_strength,
            },
        )
        self.db.update_task_status(task_id, TaskStatus.PROCESSING)
        try:
            analysis = self.image_insight_agent.analyze(image_paths=image_paths, preferred_mode=mode)
            content = self.content_generator.generate_from_image_brief(
                analysis,
                angle=angle,
                style_strength=style_strength,
            )
            review = self.originality_reviewer.review(content)
            content.review_history.append(review.to_dict())

            if not review.passed:
                content = self.content_generator.rewrite_image_article(
                    analysis,
                    content,
                    review,
                    angle=angle,
                    style_strength=style_strength,
                )
                review = self.originality_reviewer.review(content)
                content.review_history.append(review.to_dict())

            if not review.passed:
                self.db.update_task_status(task_id, TaskStatus.MANUAL_REVIEW, error_message="review_failed")
                return {
                    "status": "manual_review",
                    "analysis": analysis,
                    "content": content.to_dict(),
                    "review": review.to_dict(),
                }

            cover = self.cover_generator.generate(content)
            content_id = f"{self.settings.get('runtime', 'account_name', 'default')}:{uuid4().hex}"
            scheduled_time = self.publish_manager.suggest_publish_time()
            self.db.save_generated_content(
                content_id=content_id,
                content=content,
                review_score=review.score,
                status="APPROVED",
                scheduled_time=scheduled_time,
                cover_image_path=cover.image_path,
                cover_html_path=cover.html_path,
                publish_image_paths=image_paths,
                image_analysis=analysis,
                generation_meta={
                    "source": "image",
                    "mode": mode or analysis.get("content_mode") or "",
                    "angle": angle or "",
                    "style_strength": style_strength or "平衡",
                },
            )
            self.db.update_task_status(task_id, TaskStatus.COMPLETED)
            return {
                "status": "approved",
                "content_id": content_id,
                "scheduled_time": scheduled_time,
                "analysis": analysis,
                "content": content.to_dict(),
                "review": review.to_dict(),
                "cover": cover.to_dict(),
            }
        except Exception as exc:  # noqa: BLE001
            self.db.update_task_status(task_id, TaskStatus.RETRY, error_message=str(exc), increment_retry=True)
            raise

    def attach_publish_images(self, content_id: str, image_paths: list[str]) -> dict[str, object]:
        row = self.db.get_generated_content(content_id)
        if not row:
            return {"status": "not_found", "message": "Content not found."}

        existing = self._decode_json_list(row.get("publish_images_json"))
        merged: list[str] = []
        for item in [*existing, *image_paths]:
            cleaned = str(item).strip()
            if cleaned and cleaned not in merged:
                merged.append(cleaned)
        self.db.update_generated_publish_images(content_id, merged)
        refreshed = self.db.get_generated_content(content_id) or row
        return {
            "status": "updated",
            "content_id": content_id,
            "publish_images": self._decode_json_list(refreshed.get("publish_images_json")),
        }

    def update_generated_content(
        self,
        content_id: str,
        title: str,
        body: str,
        tags: list[str],
    ) -> dict[str, object]:
        row = self.db.get_generated_content(content_id)
        if not row:
            return {"status": "not_found", "message": "Content not found."}

        cleaned_title = title.strip()
        cleaned_body = body.strip()
        cleaned_tags = []
        for tag in tags:
            value = str(tag).strip().lstrip("#")
            if value and value not in cleaned_tags:
                cleaned_tags.append(value)

        if not cleaned_title:
            return {"status": "invalid", "message": "Title cannot be empty."}
        if len(cleaned_body) < 30:
            return {"status": "invalid", "message": "Body is too short."}
        if not cleaned_tags:
            cleaned_tags = self._decode_json_list(row.get("tags_json"))

        self.db.update_generated_content_fields(
            content_id=content_id,
            title=cleaned_title,
            body=cleaned_body,
            tags=cleaned_tags[:5],
        )
        refreshed = self.db.get_generated_content(content_id) or row
        return {
            "status": "updated",
            "content_id": content_id,
            "title": str(refreshed.get("title") or ""),
            "body": str(refreshed.get("body") or ""),
            "tags": self._decode_json_list(refreshed.get("tags_json")),
        }

    def delete_generated_content(self, content_id: str) -> dict[str, object]:
        row = self.db.get_generated_content(content_id)
        if not row:
            return {"status": "not_found", "message": "Content not found."}
        self.db.delete_generated_content(content_id)
        deleted_files = self._cleanup_generated_assets(row)
        return {
            "status": "deleted",
            "content_id": content_id,
            "deleted_files": deleted_files,
        }

    def clear_generated_contents(self, preserve_published: bool = True) -> dict[str, object]:
        rows = self.db.list_clearable_generated_contents(preserve_published=preserve_published)
        deleted = self.db.clear_generated_contents(preserve_published=preserve_published)
        deleted_files: list[str] = []
        for row in rows:
            deleted_files.extend(self._cleanup_generated_assets(row))
        return {
            "status": "cleared",
            "deleted": deleted,
            "preserve_published": preserve_published,
            "deleted_files": deleted_files,
        }

    def publish_queue(self) -> dict[str, object]:
        task_id = self.db.create_task("publish_queue", {})
        self.db.update_task_status(task_id, TaskStatus.PROCESSING)
        try:
            result = self.publish_manager.publish_due()
            self.db.update_task_status(task_id, TaskStatus.COMPLETED)
            return result
        except Exception as exc:  # noqa: BLE001
            self.db.update_task_status(task_id, TaskStatus.RETRY, error_message=str(exc), increment_retry=True)
            raise

    def publish_one_live(
        self,
        content_id: str | None = None,
        visibility: str = "private",
    ) -> dict[str, object]:
        task_id = self.db.create_task(
            "publish_live",
            {
                "content_id": content_id,
                "visibility": visibility,
            },
        )
        self.db.update_task_status(task_id, TaskStatus.PROCESSING)
        try:
            result = self.publish_manager.publish_one_live(content_id=content_id, visibility=visibility)
            self.db.update_task_status(
                task_id,
                TaskStatus.COMPLETED if result.get("status") == "published" else TaskStatus.MANUAL_REVIEW,
                error_message="" if result.get("status") == "published" else str(result),
            )
            return result
        except Exception as exc:  # noqa: BLE001
            self.db.update_task_status(task_id, TaskStatus.RETRY, error_message=str(exc), increment_retry=True)
            raise

    def run_feedback_loop(self) -> dict[str, object]:
        task_id = self.db.create_task("data_feedback", {})
        self.db.update_task_status(task_id, TaskStatus.PROCESSING)
        try:
            result = self.publish_manager.feedback()
            self.db.update_task_status(task_id, TaskStatus.COMPLETED)
            return result
        except Exception as exc:  # noqa: BLE001
            self.db.update_task_status(task_id, TaskStatus.RETRY, error_message=str(exc), increment_retry=True)
            raise

    def sync_latest_posts(self, limit: int = 10) -> dict[str, object]:
        task_id = self.db.create_task("sync_latest_posts", {"limit": limit})
        self.db.update_task_status(task_id, TaskStatus.PROCESSING)
        try:
            result = self.publish_manager.sync_latest_posts(limit=limit)
            self.db.update_task_status(task_id, TaskStatus.COMPLETED)
            return result
        except Exception as exc:  # noqa: BLE001
            self.db.update_task_status(task_id, TaskStatus.RETRY, error_message=str(exc), increment_retry=True)
            raise

    def run_full_cycle(self, topic: str | None = None) -> dict[str, object]:
        if self.vector_store.size() == 0:
            self.scan_and_ingest(manual_topics=[topic] if topic else None)
        content_package = self.produce_content(topic=topic)
        if content_package.get("status") != "approved":
            return {
                "content_package": content_package,
                "publish_result": {"published": 0, "status": "skipped"},
            }
        publish_result = self.publish_queue()
        return {
            "content_package": content_package,
            "publish_result": publish_result,
        }

    def _pick_best_topic(self) -> str:
        topics = self.trend_scanner.scan()
        return topics[0].keyword if topics else "本地生活攻略"

    def _review_until_pass(self, topic: str, content: GeneratedContent) -> tuple[GeneratedContent, object]:
        max_rewrites = int(self.settings.get("quality", "max_rewrites", 3))
        review = self.originality_reviewer.review(content)
        content.review_history.append(review.to_dict())
        attempts = 0
        while not review.passed and attempts < max_rewrites:
            content = self.content_generator.rewrite(topic, content, review)
            review = self.originality_reviewer.review(content)
            content.review_history.append(review.to_dict())
            attempts += 1
        return content, review

    def debug_snapshot(self) -> dict[str, object]:
        return {
            "settings": self.settings.raw,
            "vector_store_size": self.vector_store.size(),
            "recent_publish_records": self.db.list_publish_records(),
            "recent_sources": self.db.list_knowledge_sources(limit=5),
        }

    def check_mcp_status(self) -> dict[str, object]:
        if not self.xhs_client.is_configured():
            return {"configured": False, "ready": False}
        try:
            self.xhs_client.ensure_ready()
            login = self.xhs_client.check_login_status()
            return {
                "configured": True,
                "ready": True,
                "login_ok": login.get("ok", False),
                "message": login.get("text", ""),
                "base_url": self.xhs_client.base_url,
                "working_dir": str(self.xhs_client.working_dir),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "configured": True,
                "ready": False,
                "login_ok": False,
                "error": str(exc),
                "base_url": self.xhs_client.base_url,
                "working_dir": str(self.xhs_client.working_dir),
            }

    def _decode_json_list(self, value: object) -> list[str]:
        if not isinstance(value, str) or not value.strip():
            return []
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item).strip() for item in parsed if str(item).strip()]

    def _cleanup_generated_assets(self, row: dict[str, object]) -> list[str]:
        deleted: list[str] = []
        candidates = [
            str(row.get("cover_image_path") or "").strip(),
            str(row.get("cover_html_path") or "").strip(),
            *self._decode_json_list(row.get("publish_images_json")),
        ]
        unique_candidates: list[str] = []
        for item in candidates:
            if item and item not in unique_candidates:
                unique_candidates.append(item)

        for asset_path in unique_candidates:
            deleted_path = self._delete_asset_if_unreferenced(asset_path)
            if deleted_path:
                deleted.append(deleted_path)
        return deleted

    def _delete_asset_if_unreferenced(self, asset_path: str) -> str | None:
        cleaned = asset_path.strip()
        if not cleaned:
            return None

        try:
            path = Path(cleaned)
            if not path.is_absolute():
                path = self.settings.resolve_path(path)
        except Exception:  # noqa: BLE001
            return None

        if not path.exists() or not path.is_file():
            return None
        if self.db.count_generated_asset_references(cleaned) > 1:
            return None

        allowed_roots = [
            self.settings.generated_cover_dir.resolve(),
            self.settings.web_upload_dir.resolve(),
        ]
        try:
            resolved = path.resolve()
        except FileNotFoundError:
            return None
        if not any(root == resolved or root in resolved.parents for root in allowed_roots):
            return None

        path.unlink(missing_ok=True)
        return str(path)

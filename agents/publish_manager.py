from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from common.models import PublishRecord
from common.utils import next_publish_time, now_local, now_utc_iso, slugify, within_publish_window
from common.xhs_mcp_client import XhsMcpClient
from common.xhs_profile_scraper import XhsProfileScraper

from agents.base import BaseAgent


class PublishManager(BaseAgent):
    def __init__(self, settings, db, vector_store, client: XhsMcpClient | None = None):
        super().__init__(settings, db, vector_store)
        self.client = client
        self.profile_scraper = XhsProfileScraper(settings)

    def suggest_publish_time(self) -> str:
        current_time = now_local(self.settings.timezone)
        windows = self.settings.get("publishing", "allowed_windows", [])
        return next_publish_time(current_time, windows).isoformat()

    def publish_due(self) -> dict[str, object]:
        current_local = now_local(self.settings.timezone)
        current_time_iso = current_local.isoformat()
        account_name = self.settings.get("runtime", "account_name", "default")
        state = self.db.get_account_state(account_name)
        daily_limit = int(self.settings.get("publishing", "daily_limit", 3))
        windows = self.settings.get("publishing", "allowed_windows", [])

        if state["cookie_status"] != "valid":
            return {"published": 0, "status": "blocked", "reason": "account_cookie_invalid"}

        published_today = self.db.count_published_today(current_local.date().isoformat(), account_name)
        if published_today >= daily_limit:
            return {"published": 0, "status": "blocked", "reason": "daily_limit_reached"}

        if not within_publish_window(current_local, windows):
            return {
                "published": 0,
                "status": "blocked",
                "reason": "outside_publish_window",
                "next_time": self.suggest_publish_time(),
            }

        due_items = self.db.list_due_generated_contents(current_time_iso)
        published_count = 0
        note_ids: list[str] = []

        for row in due_items:
            if published_today + published_count >= daily_limit:
                break
            content_id = row["id"]
            note_id, publish_status, error_log = self._publish_item(account_name, content_id, row)
            record = PublishRecord(
                note_id=note_id,
                content_id=content_id,
                publish_time=now_utc_iso(),
                status=publish_status,
                error_log=error_log,
                engagement_24h={"likes": 0, "collects": 0, "comments": 0},
            )
            self.db.save_publish_record(record)
            self.db.update_generated_asset(
                content_id=content_id,
                image_path=row["cover_image_path"],
                html_path=row["cover_html_path"],
                status="PUBLISHED" if not error_log else "MANUAL_REVIEW",
            )
            note_ids.append(note_id)
            if not error_log:
                published_count += 1
                self._reconcile_publish_record(note_id)

        self.db.set_account_state(account_name, state["cookie_status"], published_today + published_count)
        self.logger.info("Published %s queued items", published_count)
        return {"published": published_count, "note_ids": note_ids, "status": "ok"}

    def publish_one_live(
        self,
        content_id: str | None = None,
        visibility: str = "private",
    ) -> dict[str, object]:
        if content_id:
            row = self.db.get_generated_content(content_id)
        else:
            candidates = self.db.list_generated_contents(limit=1, statuses=["APPROVED", "QUEUED"])
            row = candidates[0] if candidates else None

        if not row:
            return {"status": "not_found", "message": "No approved content found for live publishing."}
        if not self.client or not self.client.is_configured():
            return {"status": "blocked", "message": "Xiaohongshu MCP is not configured."}

        login_status = self.client.check_login_status()
        if not login_status.get("ok"):
            return {"status": "blocked", "message": "Xiaohongshu MCP is not logged in."}

        image_path = str(row["cover_image_path"])
        if not self._is_publishable_image(image_path):
            return {"status": "blocked", "message": "Cover image must be a PNG/JPG/JPEG/WEBP file."}

        tags = self._load_tags(row.get("tags_json"))
        publish_images = self._collect_publish_images(row)
        result = self.client.publish_content(
            title=str(row["title"])[:20],
            content=str(row["body"]),
            images=publish_images,
            tags=tags,
            visibility=visibility,
            is_original=False,
        )
        note_id = self._extract_note_id(result) or self._simulate_publish(
            self.settings.get("runtime", "account_name", "default"),
            str(row["id"]),
            row,
        )
        record = PublishRecord(
            note_id=note_id,
            content_id=str(row["id"]),
            publish_time=now_utc_iso(),
            status="PUBLISHED",
            error_log="",
            engagement_24h={"likes": 0, "collects": 0, "comments": 0},
        )
        self.db.save_publish_record(record)
        self.db.update_generated_asset(
            content_id=str(row["id"]),
            image_path=str(row["cover_image_path"]),
            html_path=str(row["cover_html_path"]),
            status="PUBLISHED",
        )
        resolved = self._reconcile_publish_record(note_id)
        return {
            "status": "published",
            "note_id": note_id,
            "content_id": str(row["id"]),
            "visibility": visibility,
            "resolved_note": resolved,
            "raw_result": result,
        }

    def feedback(self) -> dict[str, object]:
        records = self.db.list_publish_records(limit=20)
        updated: list[dict[str, Any]] = []
        for record in records:
            try:
                resolution = self._reconcile_publish_record(record["note_id"])
                metrics = self._refresh_metrics(record["note_id"])
            except Exception as exc:  # noqa: BLE001
                updated.append(
                    {
                        "note_id": record["note_id"],
                        "real_note_id": record.get("real_note_id", ""),
                        "resolved": False,
                        "error": str(exc),
                    }
                )
                continue
            updated.append(
                {
                    "note_id": record["note_id"],
                    "real_note_id": resolution.get("real_note_id", ""),
                    "resolved": bool(resolution.get("real_note_id")),
                    "engagement_24h": metrics,
                }
            )
        return {"updated": len(updated), "records": updated}

    def sync_latest_posts(self, limit: int = 10) -> dict[str, object]:
        feeds = self._load_profile_feeds(limit=limit)
        matched: list[dict[str, Any]] = []
        for feed in feeds:
            record = self._best_publish_record_for_feed(feed)
            if not record:
                continue
            feed_id = str(feed.get("id", ""))
            self.db.update_publish_record_resolution(
                note_id=str(record["note_id"]),
                real_note_id=feed_id,
                note_xsec_token=str(feed.get("xsecToken", "")),
                note_url=self._feed_url(feed),
                matched_via="profile_latest",
            )
            for duplicate in self._duplicate_claims(feed_id, exclude_note_id=str(record["note_id"])):
                self.db.clear_publish_record_resolution(str(duplicate["note_id"]))
            matched.append(
                {
                    "content_id": record["content_id"],
                    "title": self._feed_title(feed),
                    "real_note_id": feed_id,
                }
            )
        return {
            "matched": len(matched),
            "items": matched,
            "profile_configured": bool(self._configured_user_id()),
            "profile_mode": self._profile_mode(),
        }

    def _simulate_publish(self, account_name: str, content_id: str, row: dict[str, object]) -> str:
        slug = slugify(str(row["title"]), fallback="note")[:32]
        return f"xhs_{slug}_{account_name}_{content_id[-6:]}"

    def _publish_item(self, account_name: str, content_id: str, row: dict[str, object]) -> tuple[str, str, str]:
        if self.settings.get("publishing", "dry_run", True):
            return self._simulate_publish(account_name, content_id, row), "PUBLISHED_SIMULATED", ""

        if not self.client or not self.client.is_configured():
            return self._simulate_publish(account_name, content_id, row), "MANUAL_REVIEW", "xhs_mcp_not_configured"

        login_status = self.client.check_login_status()
        if not login_status.get("ok"):
            return self._simulate_publish(account_name, content_id, row), "MANUAL_REVIEW", "xhs_mcp_not_logged_in"

        image_path = str(row["cover_image_path"])
        if not self._is_publishable_image(image_path):
            return (
                self._simulate_publish(account_name, content_id, row),
                "MANUAL_REVIEW",
                "cover_image_must_be_png_jpg_or_webp",
            )

        tags = self._load_tags(row.get("tags_json"))
        publish_images = self._collect_publish_images(row)
        result = self.client.publish_content(
            title=str(row["title"])[:20],
            content=str(row["body"]),
            images=publish_images,
            tags=tags,
            schedule_at=str(row["scheduled_time"]) if row.get("scheduled_time") else None,
            visibility=self.settings.get("publishing", "visibility", "public"),
            is_original=False,
        )
        note_id = self._extract_note_id(result) or self._simulate_publish(account_name, content_id, row)
        return note_id, "PUBLISHED", ""

    def _extract_note_id(self, result: dict[str, object]) -> str | None:
        data = result.get("data")
        if isinstance(data, dict):
            for key in ("note_id", "noteId", "id"):
                value = data.get(key)
                if value:
                    return str(value)
        text = str(result.get("text") or "")
        if "note_id" in text or "noteId" in text:
            return text
        return None

    def _load_tags(self, raw: object) -> list[str]:
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed[:5]]
            except json.JSONDecodeError:
                return [raw]
        if isinstance(raw, list):
            return [str(item) for item in raw[:5]]
        return []

    def _is_publishable_image(self, image_path: str) -> bool:
        suffix = Path(image_path).suffix.lower()
        return suffix in {".png", ".jpg", ".jpeg", ".webp"}

    def _collect_publish_images(self, row: dict[str, Any]) -> list[str]:
        extra_images: list[str] = []
        for item in self._decode_json_list(row.get("publish_images_json")):
            if item not in extra_images and self._is_publishable_image(item):
                extra_images.append(item)
        if extra_images:
            return extra_images

        image_paths: list[str] = []
        cover = str(row.get("cover_image_path") or "").strip()
        if cover and self._is_publishable_image(cover):
            image_paths.append(cover)
        return image_paths

    def _decode_json_list(self, raw: object) -> list[str]:
        if not isinstance(raw, str) or not raw.strip():
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item).strip() for item in parsed if str(item).strip()]

    def _reconcile_publish_record(self, note_id: str) -> dict[str, Any]:
        record = self.db.get_publish_record(note_id)
        if not record:
            return {}
        if record.get("real_note_id") and record.get("note_xsec_token"):
            return record
        if str(record.get("status") or "") != "PUBLISHED":
            return record

        generated = self.db.get_generated_content(record["content_id"])
        if not generated or not self.client or not self.client.is_configured():
            return record

        published_title = self._published_title(str(generated["title"]))
        candidate = self._match_from_search(published_title)
        matched_via = "search_title"

        if not candidate:
            candidate = self._match_from_profile(published_title)
            matched_via = "profile_latest"

        if not candidate:
            return record

        candidate_feed_id = str(candidate.get("id", "")).strip()
        if candidate_feed_id and self._feed_claimed_by_other(candidate_feed_id, exclude_note_id=note_id):
            return record

        self.db.update_publish_record_resolution(
            note_id=note_id,
            real_note_id=candidate_feed_id,
            note_xsec_token=str(candidate.get("xsecToken", "")),
            note_url=self._feed_url(candidate),
            matched_via=matched_via,
        )
        return self.db.get_publish_record(note_id) or record

    def _match_from_search(self, title: str) -> dict[str, Any] | None:
        if not self.client:
            return None
        result = self.client.search_feeds(title)
        feeds = self.client.extract_feeds(result)
        exact = [feed for feed in feeds if self._feed_title(feed) == title]
        if exact:
            return exact[0]
        return None

    def _match_from_profile(self, title: str) -> dict[str, Any] | None:
        feeds = self._load_profile_feeds(limit=15)
        for feed in feeds:
            if self._feed_title(feed) == title:
                return feed
        return None

    def _load_profile_feeds(self, limit: int = 10) -> list[dict[str, Any]]:
        binding = self._profile_binding()
        if binding and binding.get("xsec_token") and self.client:
            result = self.client.user_profile(binding["user_id"], binding["xsec_token"])
            feeds = self.client.extract_feeds(result)
            if feeds:
                return feeds[:limit]
        return self._load_self_profile_feeds(limit=limit)

    def _profile_binding(self) -> dict[str, str] | None:
        user_id = self._configured_user_id()
        xsec_token = str(self.settings.get("publisher_profile", "xsec_token", "") or "").strip()
        if user_id:
            return {"user_id": user_id, "xsec_token": xsec_token}
        return None

    def _configured_user_id(self) -> str:
        return str(self.settings.get("publisher_profile", "user_id", "") or "").strip()

    def _profile_mode(self) -> str:
        binding = self._profile_binding()
        if not binding:
            return "missing"
        if binding.get("xsec_token"):
            return "mcp_user_profile"
        return "self_profile_playwright"

    def _load_self_profile_feeds(self, limit: int = 10) -> list[dict[str, Any]]:
        user_id = self._configured_user_id()
        if not user_id or not self.profile_scraper.is_configured():
            return []
        profile_xsec_token = str(self.settings.get("publisher_profile", "xsec_token", "") or "").strip()
        result = self.profile_scraper.load_profile_notes(
            user_id,
            limit=limit,
            profile_xsec_token=profile_xsec_token,
        )
        notes = result.get("notes")
        if not isinstance(notes, list):
            return []
        feeds: list[dict[str, Any]] = []
        for item in notes:
            if not isinstance(item, dict):
                continue
            feeds.append(
                {
                    "id": str(item.get("id") or ""),
                    "xsecToken": str(item.get("xsecToken") or ""),
                    "noteCard": item.get("noteCard") or {},
                    "url": str(item.get("url") or ""),
                }
            )
        return feeds[:limit]

    def _refresh_metrics(self, note_id: str) -> dict[str, int]:
        record = self.db.get_publish_record(note_id)
        if not record:
            return {}

        resolved = record
        if not resolved.get("real_note_id") or not resolved.get("note_xsec_token"):
            resolved = self._reconcile_publish_record(note_id)

        metrics = {"likes": 0, "collects": 0, "comments": 0}
        if resolved.get("real_note_id") and resolved.get("note_xsec_token") and self.client:
            detail = self.client.get_feed_detail(
                str(resolved["real_note_id"]),
                str(resolved["note_xsec_token"]),
                load_all_comments=False,
            )
            detail_data = detail.get("data")
            metrics = {
                "likes": self._safe_int(self._deep_get(detail_data, {"likedCount", "likeCount"}), metrics["likes"]),
                "collects": self._safe_int(self._deep_get(detail_data, {"collectedCount", "collectCount"}), metrics["collects"]),
                "comments": self._safe_int(self._deep_get(detail_data, {"commentCount"}), metrics["comments"]),
            }

        self.db.update_publish_record_metrics(note_id, metrics, now_utc_iso())
        return metrics

    def _feed_title(self, feed: dict[str, Any]) -> str:
        note_card = feed.get("noteCard") or {}
        return str(note_card.get("displayTitle") or "").strip()

    def _published_title(self, title: str) -> str:
        return title[:20].strip()

    def _feed_url(self, feed: dict[str, Any]) -> str:
        direct_url = str(feed.get("url") or "").strip()
        if direct_url:
            return direct_url
        feed_id = str(feed.get("id") or "")
        xsec_token = str(feed.get("xsecToken") or "")
        if not feed_id:
            return ""
        if xsec_token:
            return f"https://www.xiaohongshu.com/explore/{feed_id}?xsec_token={xsec_token}"
        return f"https://www.xiaohongshu.com/explore/{feed_id}"

    def _safe_int(self, value: Any, default: int = 0) -> int:
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return default

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

    def _find_or_create_placeholder_record(self, generated_row: dict[str, Any]) -> str:
        existing = self.db.list_publish_records(limit=100)
        for record in existing:
            if record["content_id"] == generated_row["id"]:
                return str(record["note_id"])

        note_id = self._simulate_publish(
            self.settings.get("runtime", "account_name", "default"),
            str(generated_row["id"]),
            generated_row,
        )
        self.db.save_publish_record(
            PublishRecord(
                note_id=note_id,
                content_id=str(generated_row["id"]),
                publish_time=now_utc_iso(),
                status="DISCOVERED_FROM_PROFILE",
                error_log="",
                engagement_24h={"likes": 0, "collects": 0, "comments": 0},
            )
        )
        return note_id

    def _best_publish_record_for_feed(self, feed: dict[str, Any]) -> dict[str, Any] | None:
        title = self._feed_title(feed)
        feed_id = str(feed.get("id") or "").strip()
        if not title or not feed_id:
            return None

        exact_claim: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        for record in self.db.list_publish_records(limit=200):
            if str(record.get("status") or "") != "PUBLISHED":
                continue
            generated = self.db.get_generated_content(str(record["content_id"]))
            if not generated or str(generated.get("status") or "") != "PUBLISHED":
                continue
            if self._published_title(str(generated["title"])) != title:
                continue

            existing_real_note_id = str(record.get("real_note_id") or "").strip()
            candidate = {
                "note_id": str(record["note_id"]),
                "content_id": str(record["content_id"]),
                "publish_time": str(record.get("publish_time") or ""),
            }
            if existing_real_note_id == feed_id:
                exact_claim.append(candidate)
            elif not existing_real_note_id:
                unresolved.append(candidate)

        pool = exact_claim or unresolved
        if not pool:
            return None
        pool.sort(key=lambda item: item["publish_time"], reverse=True)
        return pool[0]

    def _duplicate_claims(self, feed_id: str, exclude_note_id: str) -> list[dict[str, Any]]:
        duplicates: list[dict[str, Any]] = []
        for record in self.db.list_publish_records(limit=200):
            if str(record["note_id"]) == exclude_note_id:
                continue
            if str(record.get("real_note_id") or "").strip() == feed_id:
                duplicates.append(record)
        return duplicates

    def _feed_claimed_by_other(self, feed_id: str, exclude_note_id: str) -> bool:
        return bool(self._duplicate_claims(feed_id, exclude_note_id=exclude_note_id))

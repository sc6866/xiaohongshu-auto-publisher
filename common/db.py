from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from common.models import GeneratedContent, PublishRecord, TaskStatus
from common.utils import now_utc_iso


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_schema()

    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                retries INTEGER NOT NULL DEFAULT 0,
                error_message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS knowledge_sources (
                id TEXT PRIMARY KEY,
                source_url TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                topic TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                likes INTEGER NOT NULL DEFAULT 0,
                collects INTEGER NOT NULL DEFAULT 0,
                comments INTEGER NOT NULL DEFAULT 0,
                heat_score REAL NOT NULL DEFAULT 0,
                quality_score REAL NOT NULL DEFAULT 0,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS generated_contents (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                referenced_sources_json TEXT NOT NULL,
                publish_images_json TEXT NOT NULL DEFAULT '[]',
                image_analysis_json TEXT NOT NULL DEFAULT '{}',
                generation_meta_json TEXT NOT NULL DEFAULT '{}',
                persona_json TEXT NOT NULL,
                review_history_json TEXT NOT NULL,
                review_score INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                scheduled_time TEXT NOT NULL,
                cover_image_path TEXT NOT NULL DEFAULT '',
                cover_html_path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS publish_records (
                note_id TEXT PRIMARY KEY,
                content_id TEXT NOT NULL,
                publish_time TEXT NOT NULL,
                status TEXT NOT NULL,
                error_log TEXT NOT NULL DEFAULT '',
                engagement_24h_json TEXT NOT NULL,
                real_note_id TEXT NOT NULL DEFAULT '',
                note_xsec_token TEXT NOT NULL DEFAULT '',
                note_url TEXT NOT NULL DEFAULT '',
                matched_via TEXT NOT NULL DEFAULT '',
                synced_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS account_state (
                account_name TEXT PRIMARY KEY,
                cookie_status TEXT NOT NULL DEFAULT 'valid',
                daily_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            """
        )
        self._ensure_column("publish_records", "real_note_id", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("publish_records", "note_xsec_token", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("publish_records", "note_url", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("publish_records", "matched_via", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("publish_records", "synced_at", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("generated_contents", "publish_images_json", "TEXT NOT NULL DEFAULT '[]'")
        self._ensure_column("generated_contents", "image_analysis_json", "TEXT NOT NULL DEFAULT '{}'")
        self._ensure_column("generated_contents", "generation_meta_json", "TEXT NOT NULL DEFAULT '{}'")
        self.conn.commit()

    def _ensure_column(self, table_name: str, column_name: str, column_sql: str) -> None:
        columns = {
            row["name"]
            for row in self.conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name not in columns:
            self.conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")

    def create_task(self, task_type: str, payload: dict[str, Any], status: TaskStatus = TaskStatus.PENDING) -> int:
        now = now_utc_iso()
        cursor = self.conn.execute(
            """
            INSERT INTO tasks(task_type, status, payload_json, retries, error_message, created_at, updated_at)
            VALUES (?, ?, ?, 0, '', ?, ?)
            """,
            (task_type, status.value, json.dumps(payload, ensure_ascii=False), now, now),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def update_task_status(
        self,
        task_id: int,
        status: TaskStatus,
        error_message: str = "",
        increment_retry: bool = False,
    ) -> None:
        if increment_retry:
            self.conn.execute(
                """
                UPDATE tasks
                SET status = ?, retries = retries + 1, error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (status.value, error_message, now_utc_iso(), task_id),
            )
        else:
            self.conn.execute(
                """
                UPDATE tasks
                SET status = ?, error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (status.value, error_message, now_utc_iso(), task_id),
            )
        self.conn.commit()

    def upsert_knowledge_source(
        self,
        source_id: str,
        source_url: str,
        title: str,
        body: str,
        topic: str,
        tags: list[str],
        engagement: dict[str, int],
        heat_score: float,
        quality_score: float,
        metadata: dict[str, Any],
    ) -> None:
        now = now_utc_iso()
        self.conn.execute(
            """
            INSERT INTO knowledge_sources(
                id, source_url, title, body, topic, tags_json, likes, collects, comments,
                heat_score, quality_score, metadata_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                source_url=excluded.source_url,
                title=excluded.title,
                body=excluded.body,
                topic=excluded.topic,
                tags_json=excluded.tags_json,
                likes=excluded.likes,
                collects=excluded.collects,
                comments=excluded.comments,
                heat_score=excluded.heat_score,
                quality_score=excluded.quality_score,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                source_id,
                source_url,
                title,
                body,
                topic,
                json.dumps(tags, ensure_ascii=False),
                engagement.get("likes", 0),
                engagement.get("collects", 0),
                engagement.get("comments", 0),
                heat_score,
                quality_score,
                json.dumps(metadata, ensure_ascii=False),
                now,
                now,
            ),
        )
        self.conn.commit()

    def list_knowledge_sources(self, topic: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if topic:
            rows = self.conn.execute(
                """
                SELECT * FROM knowledge_sources
                WHERE topic = ?
                ORDER BY heat_score DESC, updated_at DESC
                LIMIT ?
                """,
                (topic, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT * FROM knowledge_sources
                ORDER BY heat_score DESC, updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_generated_content(
        self,
        content_id: str,
        content: GeneratedContent,
        review_score: int,
        status: str,
        scheduled_time: str,
        cover_image_path: str = "",
        cover_html_path: str = "",
        publish_image_paths: list[str] | None = None,
        image_analysis: dict[str, Any] | None = None,
        generation_meta: dict[str, Any] | None = None,
    ) -> None:
        now = now_utc_iso()
        self.conn.execute(
            """
            INSERT INTO generated_contents(
                id, title, body, tags_json, referenced_sources_json, publish_images_json,
                image_analysis_json, generation_meta_json, persona_json,
                review_history_json, review_score, status, scheduled_time,
                cover_image_path, cover_html_path, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,
                body=excluded.body,
                tags_json=excluded.tags_json,
                referenced_sources_json=excluded.referenced_sources_json,
                publish_images_json=excluded.publish_images_json,
                image_analysis_json=excluded.image_analysis_json,
                generation_meta_json=excluded.generation_meta_json,
                persona_json=excluded.persona_json,
                review_history_json=excluded.review_history_json,
                review_score=excluded.review_score,
                status=excluded.status,
                scheduled_time=excluded.scheduled_time,
                cover_image_path=excluded.cover_image_path,
                cover_html_path=excluded.cover_html_path,
                updated_at=excluded.updated_at
            """,
            (
                content_id,
                content.title,
                content.body,
                json.dumps(content.tags, ensure_ascii=False),
                json.dumps(content.referenced_sources, ensure_ascii=False),
                json.dumps(publish_image_paths or [], ensure_ascii=False),
                json.dumps(image_analysis or {}, ensure_ascii=False),
                json.dumps(generation_meta or {}, ensure_ascii=False),
                json.dumps(content.persona, ensure_ascii=False),
                json.dumps(content.review_history, ensure_ascii=False),
                review_score,
                status,
                scheduled_time,
                cover_image_path,
                cover_html_path,
                now,
                now,
            ),
        )
        self.conn.commit()

    def update_generated_publish_images(self, content_id: str, image_paths: list[str]) -> None:
        self.conn.execute(
            """
            UPDATE generated_contents
            SET publish_images_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (json.dumps(image_paths, ensure_ascii=False), now_utc_iso(), content_id),
        )
        self.conn.commit()

    def update_generated_content_fields(
        self,
        content_id: str,
        title: str,
        body: str,
        tags: list[str],
    ) -> None:
        self.conn.execute(
            """
            UPDATE generated_contents
            SET title = ?, body = ?, tags_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (title, body, json.dumps(tags, ensure_ascii=False), now_utc_iso(), content_id),
        )
        self.conn.commit()

    def update_generated_asset(self, content_id: str, image_path: str, html_path: str, status: str) -> None:
        self.conn.execute(
            """
            UPDATE generated_contents
            SET cover_image_path = ?, cover_html_path = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (image_path, html_path, status, now_utc_iso(), content_id),
        )
        self.conn.commit()

    def list_due_generated_contents(self, current_time_iso: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM generated_contents
            WHERE status IN ('APPROVED', 'QUEUED') AND scheduled_time <= ?
            ORDER BY scheduled_time ASC
            """,
            (current_time_iso,),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_generated_contents(
        self,
        limit: int = 20,
        statuses: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            rows = self.conn.execute(
                f"""
                SELECT * FROM generated_contents
                WHERE status IN ({placeholders})
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (*statuses, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT * FROM generated_contents
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_generated_content(self, content_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT * FROM generated_contents
            WHERE id = ?
            """,
            (content_id,),
        ).fetchone()
        return dict(row) if row else None

    def delete_generated_content(self, content_id: str) -> None:
        self.conn.execute("DELETE FROM generated_contents WHERE id = ?", (content_id,))
        self.conn.commit()

    def list_clearable_generated_contents(self, preserve_published: bool = True) -> list[dict[str, Any]]:
        if preserve_published:
            rows = self.conn.execute(
                """
                SELECT * FROM generated_contents
                WHERE status != 'PUBLISHED'
                ORDER BY updated_at DESC
                """
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT * FROM generated_contents
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def clear_generated_contents(self, preserve_published: bool = True) -> int:
        if preserve_published:
            cursor = self.conn.execute("DELETE FROM generated_contents WHERE status != 'PUBLISHED'")
        else:
            cursor = self.conn.execute("DELETE FROM generated_contents")
        self.conn.commit()
        return int(cursor.rowcount or 0)

    def count_generated_asset_references(self, asset_path: str) -> int:
        if not asset_path.strip():
            return 0
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM generated_contents
            WHERE cover_image_path = ?
               OR cover_html_path = ?
               OR publish_images_json LIKE ?
            """,
            (asset_path, asset_path, f"%{asset_path}%"),
        ).fetchone()
        return int(row["total"]) if row else 0

    def count_published_today(self, date_prefix: str, account_name: str) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS total FROM publish_records
            WHERE publish_time LIKE ? AND content_id LIKE ?
              AND status IN ('PUBLISHED', 'PUBLISHED_SIMULATED')
            """,
            (f"{date_prefix}%", f"{account_name}:%"),
        ).fetchone()
        return int(row["total"]) if row else 0

    def save_publish_record(self, record: PublishRecord) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO publish_records(
                note_id, content_id, publish_time, status, error_log, engagement_24h_json,
                real_note_id, note_xsec_token, note_url, matched_via, synced_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.note_id,
                record.content_id,
                record.publish_time,
                record.status,
                record.error_log,
                json.dumps(record.engagement_24h, ensure_ascii=False),
                record.real_note_id,
                record.note_xsec_token,
                record.note_url,
                record.matched_via,
                record.synced_at,
                now_utc_iso(),
            ),
        )
        self.conn.commit()

    def update_publish_record_resolution(
        self,
        note_id: str,
        real_note_id: str,
        note_xsec_token: str,
        note_url: str,
        matched_via: str,
    ) -> None:
        self.conn.execute(
            """
            UPDATE publish_records
            SET real_note_id = ?, note_xsec_token = ?, note_url = ?, matched_via = ?
            WHERE note_id = ?
            """,
            (real_note_id, note_xsec_token, note_url, matched_via, note_id),
        )
        self.conn.commit()

    def clear_publish_record_resolution(self, note_id: str) -> None:
        self.conn.execute(
            """
            UPDATE publish_records
            SET real_note_id = '', note_xsec_token = '', note_url = '', matched_via = ''
            WHERE note_id = ?
            """,
            (note_id,),
        )
        self.conn.commit()

    def update_publish_record_metrics(
        self,
        note_id: str,
        engagement_24h: dict[str, int],
        synced_at: str,
    ) -> None:
        self.conn.execute(
            """
            UPDATE publish_records
            SET engagement_24h_json = ?, synced_at = ?
            WHERE note_id = ?
            """,
            (json.dumps(engagement_24h, ensure_ascii=False), synced_at, note_id),
        )
        self.conn.commit()

    def get_publish_record(self, note_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM publish_records WHERE note_id = ?",
            (note_id,),
        ).fetchone()
        return dict(row) if row else None

    def set_account_state(self, account_name: str, cookie_status: str, daily_count: int) -> None:
        self.conn.execute(
            """
            INSERT INTO account_state(account_name, cookie_status, daily_count, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(account_name) DO UPDATE SET
                cookie_status=excluded.cookie_status,
                daily_count=excluded.daily_count,
                updated_at=excluded.updated_at
            """,
            (account_name, cookie_status, daily_count, now_utc_iso()),
        )
        self.conn.commit()

    def get_account_state(self, account_name: str) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM account_state WHERE account_name = ?",
            (account_name,),
        ).fetchone()
        if not row:
            self.set_account_state(account_name, "valid", 0)
            row = self.conn.execute(
                "SELECT * FROM account_state WHERE account_name = ?",
                (account_name,),
            ).fetchone()
        return dict(row)

    def list_publish_records(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM publish_records ORDER BY publish_time DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

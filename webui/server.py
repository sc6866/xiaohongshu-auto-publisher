from __future__ import annotations

import json
import mimetypes
import threading
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from agents.xiaohongshu_manager import XiaohongshuManager
from common.config import Settings


def run_web_console(settings: Settings, host: str, port: int) -> None:
    app = WebConsoleApp(settings=settings)
    handler = app.build_handler()
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    app.logger.info("Web console is running at http://%s:%s", host, port)
    print(json.dumps({"status": "running", "url": f"http://{host}:{port}"}, ensure_ascii=False, indent=2))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


class WebConsoleApp:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.manager = XiaohongshuManager(settings)
        self.lock = threading.Lock()
        self.static_dir = Path(__file__).resolve().parent / "static"
        self.upload_dir = settings.web_upload_dir
        self.max_upload_bytes = int(settings.get("web", "max_upload_mb", 20)) * 1024 * 1024
        self.logger = self.manager.logger

    def build_handler(self) -> type[BaseHTTPRequestHandler]:
        app = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                app.handle_get(self)

            def do_POST(self) -> None:  # noqa: N802
                app.handle_post(self)

            def log_message(self, format: str, *args: object) -> None:
                app.logger.info("Web %s - %s", self.address_string(), format % args)

        return Handler

    def handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        route = parsed.path
        if route == "/":
            self._serve_file(
                handler,
                self.static_dir / "index.html",
                "text/html; charset=utf-8",
                cache_control="no-store",
            )
            return
        if route == "/app.js":
            self._serve_file(
                handler,
                self.static_dir / "app.js",
                "application/javascript; charset=utf-8",
                cache_control="public, max-age=300",
            )
            return
        if route == "/styles.css":
            self._serve_file(
                handler,
                self.static_dir / "styles.css",
                "text/css; charset=utf-8",
                cache_control="public, max-age=300",
            )
            return
        if route == "/api/dashboard":
            self._json_response(handler, self._dashboard_payload())
            return
        if route == "/healthz":
            self._json_response(
                handler,
                {
                    "status": "ok",
                    "service": "xiaohongshu-web",
                    "account_name": self.settings.get("runtime", "account_name", "default"),
                },
            )
            return
        if route == "/api/generated-detail":
            query = parse_qs(parsed.query)
            content_id = self._clean_text((query.get("content_id") or [""])[0])
            if not content_id:
                self._json_response(handler, {"error": "missing content_id"}, status=HTTPStatus.BAD_REQUEST)
                return
            row = self.manager.db.get_generated_content(content_id)
            if not row:
                self._json_response(handler, {"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._json_response(handler, {"detail": self._serialize_generated_detail(row)})
            return
        if route.startswith("/media/generated-covers/"):
            file_name = unquote(route.removeprefix("/media/generated-covers/"))
            self._serve_media(handler, self.settings.generated_cover_dir / file_name)
            return
        if route.startswith("/media/uploads/"):
            file_name = unquote(route.removeprefix("/media/uploads/"))
            self._serve_media(handler, self.upload_dir / file_name)
            return
        self._json_response(handler, {"error": "not_found"}, status=HTTPStatus.NOT_FOUND)

    def handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        route = parsed.path
        try:
            if route == "/api/mcp-check":
                self._json_response(handler, self.manager.check_mcp_status())
                return
            if route == "/api/scan":
                payload = self._parse_json_body(handler)
                with self.lock:
                    result = self.manager.scan_and_ingest(manual_topics=payload.get("topics"))
                self._json_response(handler, result)
                return
            if route == "/api/produce":
                payload = self._parse_json_body(handler)
                with self.lock:
                    result = self.manager.produce_content(topic=self._clean_text(payload.get("topic")))
                self._json_response(handler, result)
                return
            if route == "/api/produce-images":
                fields, files = self._parse_multipart(handler)
                image_paths = self._persist_uploaded_images(files)
                if not image_paths:
                    raise ValueError("Please upload at least one image.")
                with self.lock:
                    result = self.manager.produce_from_images(
                        image_paths=image_paths,
                        angle=self._clean_text(fields.get("angle")),
                        mode=self._clean_text(fields.get("mode")),
                        style_strength=self._clean_text(fields.get("style_strength")),
                    )
                self._json_response(handler, result)
                return
            if route == "/api/attach-publish-images":
                fields, files = self._parse_multipart(handler)
                content_id = self._clean_text(fields.get("content_id"))
                if not content_id:
                    raise ValueError("missing content_id")
                image_paths = self._persist_uploaded_images(files)
                if not image_paths:
                    raise ValueError("Please upload at least one image.")
                with self.lock:
                    result = self.manager.attach_publish_images(content_id=content_id, image_paths=image_paths)
                self._json_response(handler, result)
                return
            if route == "/api/generated-delete":
                payload = self._parse_json_body(handler)
                content_id = self._clean_text(payload.get("content_id"))
                if not content_id:
                    raise ValueError("missing content_id")
                with self.lock:
                    result = self.manager.delete_generated_content(content_id=content_id)
                self._json_response(handler, result)
                return
            if route == "/api/generated-update":
                payload = self._parse_json_body(handler)
                content_id = self._clean_text(payload.get("content_id"))
                if not content_id:
                    raise ValueError("missing content_id")
                raw_tags = payload.get("tags")
                tags = raw_tags if isinstance(raw_tags, list) else []
                with self.lock:
                    result = self.manager.update_generated_content(
                        content_id=content_id,
                        title=self._clean_text(payload.get("title")) or "",
                        body=self._clean_text(payload.get("body")) or "",
                        tags=[str(item) for item in tags],
                    )
                self._json_response(handler, result)
                return
            if route == "/api/generated-clear":
                payload = self._parse_json_body(handler)
                preserve_published = bool(payload.get("preserve_published", True))
                with self.lock:
                    result = self.manager.clear_generated_contents(preserve_published=preserve_published)
                self._json_response(handler, result)
                return
            if route == "/api/publish-live":
                payload = self._parse_json_body(handler)
                with self.lock:
                    result = self.manager.publish_one_live(
                        content_id=self._clean_text(payload.get("content_id")),
                        visibility=self._clean_text(payload.get("visibility")) or "仅自己可见",
                    )
                self._json_response(handler, result)
                return
            if route == "/api/sync-latest":
                payload = self._parse_json_body(handler)
                with self.lock:
                    result = self.manager.sync_latest_posts(limit=int(payload.get("limit") or 10))
                self._json_response(handler, result)
                return
            if route == "/api/feedback":
                with self.lock:
                    result = self.manager.run_feedback_loop()
                self._json_response(handler, result)
                return
        except Exception as exc:  # noqa: BLE001
            self.logger.exception("Web API failed: %s", exc)
            self._json_response(handler, {"error": str(exc), "route": route}, status=HTTPStatus.BAD_REQUEST)
            return
        self._json_response(handler, {"error": "not_found"}, status=HTTPStatus.NOT_FOUND)

    def _dashboard_payload(self) -> dict[str, Any]:
        generated_rows = self.manager.db.list_generated_contents(limit=10)
        publish_rows = self.manager.db.list_publish_records(limit=10)
        source_rows = self.manager.db.list_knowledge_sources(limit=10)
        return {
            "mcp": self.manager.check_mcp_status(),
            "generated_contents": [self._serialize_generated_summary(row) for row in generated_rows],
            "publish_records": [self._serialize_publish(row) for row in publish_rows],
            "knowledge_sources": [self._serialize_source(row) for row in source_rows],
            "web": {
                "host": self.settings.get("web", "host", "127.0.0.1"),
                "port": int(self.settings.get("web", "port", 8787)),
                "public_base_url": str(self.settings.get("web", "public_base_url", "") or ""),
            },
            "profile": {
                "account_name": self.settings.get("runtime", "account_name", "default"),
                "user_id": self.settings.get("publisher_profile", "user_id", ""),
            },
        }

    def _serialize_generated_summary(self, row: dict[str, Any]) -> dict[str, Any]:
        tags = self._decode_json(row.get("tags_json"), [])
        publish_images = self._decode_json(row.get("publish_images_json"), [])
        generation_meta = self._decode_json(row.get("generation_meta_json"), {})
        cover_path = str(row.get("cover_image_path") or "")
        cover_url = ""
        if cover_path:
            path = Path(cover_path)
            if path.exists():
                cover_url = f"/media/generated-covers/{path.name}"
        publish_image_urls = self._publish_image_urls(publish_images)
        body = str(row.get("body") or "")
        return {
            "id": str(row.get("id") or ""),
            "title": str(row.get("title") or ""),
            "body_preview": body[:120],
            "status": str(row.get("status") or ""),
            "scheduled_time": str(row.get("scheduled_time") or ""),
            "review_score": int(row.get("review_score") or 0),
            "tags": tags[:5],
            "display_cover_url": publish_image_urls[0] if publish_image_urls else cover_url,
            "cover_url": cover_url,
            "publish_image_count": len(publish_images),
            "publish_image_urls": publish_image_urls[:4],
            "generation_meta": generation_meta,
            "has_analysis": bool(self._decode_json(row.get("image_analysis_json"), {})),
        }

    def _serialize_generated_detail(self, row: dict[str, Any]) -> dict[str, Any]:
        tags = self._decode_json(row.get("tags_json"), [])
        persona = self._decode_json(row.get("persona_json"), {})
        review_history = self._decode_json(row.get("review_history_json"), [])
        publish_images = self._decode_json(row.get("publish_images_json"), [])
        image_analysis = self._decode_json(row.get("image_analysis_json"), {})
        generation_meta = self._decode_json(row.get("generation_meta_json"), {})
        cover_path = str(row.get("cover_image_path") or "")
        cover_url = ""
        if cover_path:
            path = Path(cover_path)
            if path.exists():
                cover_url = f"/media/generated-covers/{path.name}"
        publish_image_urls = self._publish_image_urls(publish_images)
        return {
            "id": str(row.get("id") or ""),
            "title": str(row.get("title") or ""),
            "body": str(row.get("body") or ""),
            "status": str(row.get("status") or ""),
            "scheduled_time": str(row.get("scheduled_time") or ""),
            "review_score": int(row.get("review_score") or 0),
            "tags": tags,
            "persona": persona,
            "review_history": review_history,
            "display_cover_url": publish_image_urls[0] if publish_image_urls else cover_url,
            "cover_url": cover_url,
            "cover_image_path": cover_path,
            "publish_images": publish_images,
            "publish_image_urls": publish_image_urls,
            "image_analysis": image_analysis,
            "generation_meta": generation_meta,
        }

    def _serialize_publish(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "note_id": str(row.get("note_id") or ""),
            "content_id": str(row.get("content_id") or ""),
            "publish_time": str(row.get("publish_time") or ""),
            "status": str(row.get("status") or ""),
            "real_note_id": str(row.get("real_note_id") or ""),
            "note_url": str(row.get("note_url") or ""),
            "matched_via": str(row.get("matched_via") or ""),
            "engagement_24h": self._decode_json(row.get("engagement_24h_json"), {}),
        }

    def _serialize_source(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "source_url": str(row.get("source_url") or ""),
            "title": str(row.get("title") or ""),
            "topic": str(row.get("topic") or ""),
            "heat_score": float(row.get("heat_score") or 0),
            "quality_score": float(row.get("quality_score") or 0),
            "tags": self._decode_json(row.get("tags_json"), [])[:5],
            "updated_at": str(row.get("updated_at") or ""),
        }

    def _publish_image_urls(self, publish_images: list[Any]) -> list[str]:
        urls: list[str] = []
        for item in publish_images:
            path = Path(str(item))
            if path.exists():
                urls.append(f"/media/uploads/{path.name}")
        return urls

    def _serve_file(
        self,
        handler: BaseHTTPRequestHandler,
        file_path: Path,
        content_type: str,
        *,
        cache_control: str = "no-store",
    ) -> None:
        if not file_path.exists():
            self._json_response(handler, {"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
            return
        content = file_path.read_bytes()
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(content)))
        handler.send_header("Cache-Control", cache_control)
        handler.end_headers()
        handler.wfile.write(content)

    def _serve_media(self, handler: BaseHTTPRequestHandler, file_path: Path) -> None:
        if not file_path.exists() or not file_path.is_file():
            self._json_response(handler, {"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self._serve_file(handler, file_path, content_type, cache_control="public, max-age=3600")

    def _json_response(
        self,
        handler: BaseHTTPRequestHandler,
        payload: dict[str, Any],
        *,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(body)

    def _parse_json_body(self, handler: BaseHTTPRequestHandler) -> dict[str, Any]:
        length = int(handler.headers.get("Content-Length", "0") or 0)
        raw = handler.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("JSON body must be an object.")
        return parsed

    def _parse_multipart(self, handler: BaseHTTPRequestHandler) -> tuple[dict[str, str], list[dict[str, Any]]]:
        content_type = handler.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            raise ValueError("Expected multipart/form-data.")
        length = int(handler.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            raise ValueError("Request body is empty.")
        if length > self.max_upload_bytes * 4:
            raise ValueError("Upload payload is too large.")
        raw = handler.rfile.read(length)

        boundary = ""
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part.split("=", 1)[1].strip().strip('"')
                break
        if not boundary:
            raise ValueError("Missing multipart boundary.")

        fields: dict[str, str] = {}
        files: list[dict[str, Any]] = []
        marker = f"--{boundary}".encode("utf-8")
        for chunk in raw.split(marker):
            part = chunk.strip()
            if not part or part == b"--":
                continue
            if part.endswith(b"--"):
                part = part[:-2].strip()
            header_blob, separator, body = part.partition(b"\r\n\r\n")
            if not separator:
                continue
            headers = self._parse_part_headers(header_blob.decode("utf-8", errors="replace"))
            disposition = headers.get("content-disposition", "")
            params = self._parse_disposition_params(disposition)
            name = params.get("name", "")
            filename = params.get("filename", "")
            if body.endswith(b"\r\n"):
                body = body[:-2]
            if filename:
                files.append(
                    {
                        "field_name": name,
                        "filename": filename,
                        "content_type": headers.get("content-type", "application/octet-stream"),
                        "body": body,
                    }
                )
            elif name:
                fields[name] = body.decode("utf-8", errors="replace").strip()
        return fields, files

    def _parse_part_headers(self, raw_headers: str) -> dict[str, str]:
        headers: dict[str, str] = {}
        for line in raw_headers.split("\r\n"):
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
        return headers

    def _parse_disposition_params(self, disposition: str) -> dict[str, str]:
        params: dict[str, str] = {}
        parts = [item.strip() for item in disposition.split(";") if item.strip()]
        for item in parts[1:]:
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            params[key.strip().lower()] = value.strip().strip('"')
        return params

    def _persist_uploaded_images(self, files: list[dict[str, Any]]) -> list[str]:
        allowed = {".png", ".jpg", ".jpeg", ".webp"}
        saved_paths: list[str] = []
        for file in files:
            original = Path(str(file.get("filename") or "upload.bin"))
            suffix = original.suffix.lower() or ".bin"
            if suffix not in allowed:
                continue
            filename = f"{uuid.uuid4().hex}{suffix}"
            target = self.upload_dir / filename
            target.write_bytes(file.get("body", b""))
            saved_paths.append(str(target))
        return saved_paths

    def _clean_text(self, value: Any) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    def _decode_json(self, value: Any, default: Any) -> Any:
        if not isinstance(value, str) or not value.strip():
            return default
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default

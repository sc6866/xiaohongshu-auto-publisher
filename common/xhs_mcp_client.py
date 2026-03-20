from __future__ import annotations

import json
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from common.config import Settings


class XhsMcpClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.enabled = bool(settings.get("xhs_mcp", "enabled", False))
        executable_value = str(settings.get("xhs_mcp", "executable_path", "") or "").strip()
        working_dir_value = str(settings.get("xhs_mcp", "working_dir", ".") or ".").strip()
        self.executable_path = settings.resolve_path(executable_value) if executable_value else Path()
        self.working_dir = settings.resolve_path(working_dir_value)
        self.port = int(settings.get("xhs_mcp", "port", 18060))
        self.headless = bool(settings.get("xhs_mcp", "headless", True))
        self.auto_start = bool(settings.get("xhs_mcp", "auto_start", True))
        self.startup_timeout_seconds = int(settings.get("xhs_mcp", "startup_timeout_seconds", 15))
        self.request_timeout_seconds = int(settings.get("xhs_mcp", "request_timeout_seconds", 45))
        configured_base_url = str(settings.get("xhs_mcp", "base_url", "") or "").strip().rstrip("/")
        self.base_url = configured_base_url or f"http://127.0.0.1:{self.port}"
        self.process: subprocess.Popen[str] | None = None
        self.session_id: str | None = None

    def is_configured(self) -> bool:
        if not self.enabled:
            return False
        if self.executable_path.exists():
            return True
        if str(self.settings.get("xhs_mcp", "base_url", "") or "").strip():
            return True
        return self._health_ok()

    def ensure_ready(self) -> bool:
        if not self.is_configured():
            return False
        if not self._health_ok():
            if self.auto_start and self.executable_path.exists():
                self._start_server()
            else:
                raise RuntimeError(f"Xiaohongshu MCP is not reachable at {self.base_url}.")
        self._initialize_session()
        return True

    def check_login_status(self) -> dict[str, Any]:
        result = self.call_tool("check_login_status", {})
        text = self._coerce_text(result)
        return {
            "ok": ("已登录" in text) and ("未登录" not in text),
            "text": text,
        }

    def list_feeds(self) -> dict[str, Any]:
        return self.call_tool("list_feeds", {})

    def search_feeds(self, keyword: str, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        arguments: dict[str, Any] = {"keyword": keyword}
        if filters:
            arguments["filters"] = filters
        return self.call_tool("search_feeds", arguments)

    def get_feed_detail(
        self,
        feed_id: str,
        xsec_token: str,
        load_all_comments: bool = False,
    ) -> dict[str, Any]:
        return self.call_tool(
            "get_feed_detail",
            {
                "feed_id": feed_id,
                "xsec_token": xsec_token,
                "load_all_comments": load_all_comments,
            },
        )

    def user_profile(self, user_id: str, xsec_token: str) -> dict[str, Any]:
        return self.call_tool(
            "user_profile",
            {
                "user_id": user_id,
                "xsec_token": xsec_token,
            },
        )

    def publish_content(
        self,
        title: str,
        content: str,
        images: list[str],
        tags: list[str] | None = None,
        schedule_at: str | None = None,
        visibility: str | None = None,
        is_original: bool | None = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "title": title,
            "content": content,
            "images": images,
        }
        if tags:
            arguments["tags"] = tags
        if schedule_at:
            arguments["schedule_at"] = schedule_at
        if visibility:
            arguments["visibility"] = self._normalize_visibility(visibility)
        if is_original is not None:
            arguments["is_original"] = is_original
        return self.call_tool("publish_content", arguments)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.ensure_ready():
            raise RuntimeError("Xiaohongshu MCP is not configured.")
        payload = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000) % 1_000_000_000,
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments,
            },
        }
        response = self._post_json("/mcp", payload, session_id=self.session_id)
        if "error" in response:
            raise RuntimeError(f"MCP tool call failed: {response['error']}")
        return self._normalize_tool_result(response.get("result", {}))

    def extract_feeds(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        data = result.get("data")
        if isinstance(data, dict):
            feeds = data.get("feeds")
            if isinstance(feeds, list):
                return [item for item in feeds if isinstance(item, dict)]
        return []

    def _initialize_session(self) -> None:
        if self.session_id:
            return
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {
                    "name": "xiaohongshu-auto-publisher",
                    "version": "0.1.0",
                },
            },
        }
        response, headers = self._post_json_with_headers("/mcp", payload)
        session_id = headers.get("Mcp-Session-Id")
        if not session_id:
            raise RuntimeError("MCP session was not created.")
        self.session_id = session_id
        if "error" in response:
            raise RuntimeError(f"MCP initialize failed: {response['error']}")

    def _start_server(self) -> None:
        if self.process and self.process.poll() is None:
            return
        if not self.executable_path.exists():
            raise FileNotFoundError(f"MCP executable not found: {self.executable_path}")

        args = [str(self.executable_path), "-port", f":{self.port}"]
        if self.headless:
            args.append("-headless")

        self.process = subprocess.Popen(
            args,
            cwd=self.working_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        deadline = time.time() + self.startup_timeout_seconds
        while time.time() < deadline:
            if self._health_ok():
                self.session_id = None
                return
            if self.process.poll() is not None:
                raise RuntimeError("Xiaohongshu MCP exited before becoming healthy.")
            time.sleep(0.5)
        raise TimeoutError("Timed out waiting for Xiaohongshu MCP to become healthy.")

    def _health_ok(self) -> bool:
        try:
            request = urllib.request.Request(f"{self.base_url}/health", method="GET")
            with urllib.request.urlopen(request, timeout=3) as response:
                if response.status != 200:
                    return False
                payload = json.loads(response.read().decode("utf-8"))
                return bool(payload.get("success"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, socket.timeout):
            return False

    def _post_json(self, path: str, payload: dict[str, Any], session_id: str | None = None) -> dict[str, Any]:
        response, _ = self._post_json_with_headers(path, payload, session_id=session_id)
        return response

    def _post_json_with_headers(
        self,
        path: str,
        payload: dict[str, Any],
        session_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json, text/event-stream")
        if session_id:
            request.add_header("Mcp-Session-Id", session_id)

        with urllib.request.urlopen(request, timeout=self.request_timeout_seconds) as response:
            text = response.read().decode("utf-8")
            return json.loads(text), dict(response.headers.items())

    def _normalize_tool_result(self, result: dict[str, Any]) -> dict[str, Any]:
        content = result.get("content") or []
        if not content:
            return {"raw": result, "text": "", "data": None}

        first = content[0]
        text = first.get("text", "") if isinstance(first, dict) else str(first)
        data: Any = None
        stripped = text.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError:
                data = None
        return {"raw": result, "text": text, "data": data}

    def _coerce_text(self, result: dict[str, Any]) -> str:
        text = result.get("text")
        if isinstance(text, str):
            return text
        data = result.get("data")
        if data is None:
            return ""
        return json.dumps(data, ensure_ascii=False)

    def _normalize_visibility(self, visibility: str) -> str:
        normalized = visibility.strip().lower()
        mapping = {
            "public": "公开可见",
            "private": "仅自己可见",
            "followers": "仅互关好友可见",
            "公开可见": "公开可见",
            "仅自己可见": "仅自己可见",
            "仅互关好友可见": "仅互关好友可见",
        }
        return mapping.get(normalized, visibility)

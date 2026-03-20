from __future__ import annotations

import base64
import os
import urllib.error
import urllib.request
from typing import Any

from common.config import Settings
from common.vision_utils import get_json, post_json, sleep_seconds


class QwenImageClient:
    provider_name = "qwen-image"

    def __init__(self, settings: Settings):
        self.enabled = bool(settings.get("cover", "image_enabled", True))
        self.base_url = str(
            settings.get(
                "cover",
                "qwen_base_url",
                settings.get("writer", "qwen_base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            )
        ).rstrip("/")
        self.model = str(settings.get("cover", "qwen_image_model", "wanx2.1-t2i-turbo"))
        self.size = str(settings.get("cover", "image_size", "768x1024"))
        self.timeout_seconds = int(settings.get("cover", "image_timeout_seconds", 120))
        self.api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip() or str(
            settings.get("cover", "qwen_api_key", settings.get("writer", "qwen_api_key", ""))
        ).strip()

    def is_configured(self) -> bool:
        return self.enabled and bool(self.api_key)

    def generate_image(
        self,
        prompt: str,
        *,
        negative_prompt: str = "",
    ) -> bytes:
        if not self.is_configured():
            raise RuntimeError("DASHSCOPE_API_KEY is not configured for cover image generation.")

        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "size": self.size,
            "response_format": "b64_json",
        }
        if negative_prompt.strip():
            payload["negative_prompt"] = negative_prompt.strip()

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            parsed = post_json(
                url=f"{self.base_url}/images/generations",
                payload=payload,
                headers=headers,
                timeout_seconds=self.timeout_seconds,
            )
            image_bytes = self._extract_image_bytes(parsed)
            if image_bytes:
                return image_bytes
        except Exception:
            pass

        return self._generate_via_dashscope_native(prompt=prompt, negative_prompt=negative_prompt)

    def _generate_via_dashscope_native(self, *, prompt: str, negative_prompt: str) -> bytes:
        url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
        payload: dict[str, Any] = {
            "model": self.model,
            "input": {
                "prompt": prompt,
            },
            "parameters": {
                "size": self.size.replace("x", "*"),
                "n": 1,
            },
        }
        if negative_prompt.strip():
            payload["input"]["negative_prompt"] = negative_prompt.strip()

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        parsed = post_json(
            url=url,
            payload=payload,
            headers=headers,
            timeout_seconds=self.timeout_seconds,
        )
        task_id = (
            str(parsed.get("output", {}).get("task_id") or "").strip()
            if isinstance(parsed.get("output"), dict)
            else ""
        )
        if task_id:
            return self._poll_dashscope_task(task_id)

        image_bytes = self._extract_image_bytes(parsed)
        if image_bytes:
            return image_bytes
        raise RuntimeError("DashScope image generator returned no task id or image payload.")

    def _poll_dashscope_task(self, task_id: str) -> bytes:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }
        status_url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
        for _ in range(20):
            payload = get_json(status_url, headers=headers, timeout_seconds=self.timeout_seconds)
            output = payload.get("output")
            if not isinstance(output, dict):
                sleep_seconds(2.0)
                continue
            task_status = str(output.get("task_status") or output.get("status") or "").upper()
            if task_status in {"SUCCEEDED", "SUCCESS"}:
                image_bytes = self._extract_image_bytes(payload)
                if image_bytes:
                    return image_bytes
                raise RuntimeError("DashScope task succeeded but returned no image payload.")
            if task_status in {"FAILED", "CANCELED"}:
                raise RuntimeError(f"DashScope task failed: {payload}")
            sleep_seconds(2.0)
        raise RuntimeError("DashScope image task timed out.")

    def _extract_image_bytes(self, payload: dict[str, Any]) -> bytes:
        data = payload.get("data")
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                b64_json = item.get("b64_json")
                if isinstance(b64_json, str) and b64_json.strip():
                    return base64.b64decode(b64_json)
                url = item.get("url")
                if isinstance(url, str) and url.strip():
                    return self._download(url.strip())
        output = payload.get("output")
        if isinstance(output, dict):
            results = output.get("results")
            if isinstance(results, list):
                for item in results:
                    if not isinstance(item, dict):
                        continue
                    url = item.get("url")
                    if isinstance(url, str) and url.strip():
                        return self._download(url.strip())
        return b""

    def _download(self, url: str) -> bytes:
        request = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(str(exc)) from exc

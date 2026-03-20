from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from common.config import Settings
from common.vision_utils import extract_chat_message_text, image_to_data_uri, parse_json_object, post_json


class QwenVisionClient:
    provider_name = "qwen"

    def __init__(self, settings: Settings):
        self.enabled = bool(settings.get("vision", "enabled", True))
        self.base_url = str(
            settings.get("vision", "qwen_base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        ).rstrip("/")
        self.model = str(settings.get("vision", "qwen_model", "qwen-vl-max-latest"))
        self.timeout_seconds = int(settings.get("vision", "timeout_seconds", 90))
        self.api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip() or str(
            settings.get("vision", "qwen_api_key", "")
        ).strip()

    def is_configured(self) -> bool:
        return self.enabled and bool(self.api_key)

    def analyze_images(self, image_paths: list[Path], prompt: str) -> dict[str, Any]:
        if not self.is_configured():
            raise RuntimeError("DASHSCOPE_API_KEY is not configured.")

        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for path in image_paths:
            content.append({"type": "image_url", "image_url": {"url": image_to_data_uri(path)}})

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": content,
                }
            ],
            "temperature": 0.2,
        }
        parsed = post_json(
            url=f"{self.base_url}/chat/completions",
            payload=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout_seconds=self.timeout_seconds,
        )
        text = extract_chat_message_text(parsed)
        if not text:
            raise RuntimeError("Qwen vision returned empty output.")
        return parse_json_object(text, "Qwen vision")

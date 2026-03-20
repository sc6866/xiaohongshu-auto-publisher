from __future__ import annotations

import os
from typing import Any

from common.config import Settings
from common.vision_utils import extract_chat_message_text, parse_json_object, post_json


class GlmTextClient:
    provider_name = "glm"

    def __init__(self, settings: Settings):
        self.enabled = bool(settings.get("writer", "enabled", True))
        self.base_url = str(
            settings.get(
                "writer",
                "glm_base_url",
                settings.get("vision", "glm_base_url", "https://open.bigmodel.cn/api/paas/v4"),
            )
        ).rstrip("/")
        self.model = str(settings.get("writer", "glm_model", "glm-4-flash"))
        self.timeout_seconds = int(settings.get("writer", "timeout_seconds", 90))
        self.api_key = os.environ.get("ZHIPU_API_KEY", "").strip() or str(
            settings.get("writer", "glm_api_key", settings.get("vision", "glm_api_key", ""))
        ).strip()

    def is_configured(self) -> bool:
        return self.enabled and bool(self.api_key)

    def generate_json(self, prompt: str, system_prompt: str) -> dict[str, Any]:
        if not self.is_configured():
            raise RuntimeError("ZHIPU_API_KEY is not configured for writer.")

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.85,
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
            raise RuntimeError("GLM writer returned empty output.")
        return parse_json_object(text, "GLM writer")

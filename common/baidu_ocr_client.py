from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any

from common.config import Settings
from common.vision_utils import post_form


class BaiduOcrClient:
    provider_name = "baidu"

    def __init__(self, settings: Settings):
        self.enabled = bool(settings.get("ocr", "enabled", True))
        self.token_url = str(settings.get("ocr", "baidu_token_url", "https://aip.baidubce.com/oauth/2.0/token"))
        self.general_url = str(
            settings.get("ocr", "baidu_general_url", "https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic")
        )
        self.timeout_seconds = int(settings.get("ocr", "timeout_seconds", 30))
        self.api_key = os.environ.get("BAIDU_OCR_API_KEY", "").strip() or str(
            settings.get("ocr", "baidu_api_key", "")
        ).strip()
        self.secret_key = os.environ.get("BAIDU_OCR_SECRET_KEY", "").strip() or str(
            settings.get("ocr", "baidu_secret_key", "")
        ).strip()
        self._cached_access_token = ""
        self._access_token_expires_at = 0.0

    def is_configured(self) -> bool:
        return self.enabled and bool(self.api_key) and bool(self.secret_key)

    def recognize_images(self, image_paths: list[Path]) -> list[dict[str, Any]]:
        return [self.recognize_image(path) for path in image_paths]

    def recognize_image(self, path: Path) -> dict[str, Any]:
        if not self.is_configured():
            raise RuntimeError("BAIDU_OCR_API_KEY / BAIDU_OCR_SECRET_KEY is not configured.")

        access_token = self._get_access_token()
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        parsed = post_form(
            url=f"{self.general_url}?access_token={access_token}",
            data={
                "image": encoded,
                "language_type": "CHN_ENG",
                "detect_direction": "true",
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout_seconds=self.timeout_seconds,
        )
        if parsed.get("error_code"):
            raise RuntimeError(f"Baidu OCR failed: {parsed.get('error_code')} {parsed.get('error_msg', '')}".strip())

        words_result = parsed.get("words_result")
        lines: list[str] = []
        if isinstance(words_result, list):
            for item in words_result:
                if not isinstance(item, dict):
                    continue
                words = str(item.get("words", "")).strip()
                if words:
                    lines.append(words)

        return {
            "image_path": str(path),
            "lines": lines,
            "words_count": int(parsed.get("words_result_num", len(lines)) or 0),
            "direction": parsed.get("direction"),
        }

    def _get_access_token(self) -> str:
        now = time.time()
        if self._cached_access_token and now < self._access_token_expires_at - 60:
            return self._cached_access_token

        parsed = post_form(
            url=self.token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.api_key,
                "client_secret": self.secret_key,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout_seconds=self.timeout_seconds,
        )
        access_token = str(parsed.get("access_token", "")).strip()
        if not access_token:
            raise RuntimeError("Baidu OCR access token response missing access_token.")
        expires_in = int(parsed.get("expires_in", 2592000) or 2592000)
        self._cached_access_token = access_token
        self._access_token_expires_at = now + expires_in
        return access_token

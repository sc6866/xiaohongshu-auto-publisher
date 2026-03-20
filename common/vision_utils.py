from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def guess_mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".gif":
        return "image/gif"
    return "application/octet-stream"


def image_to_data_uri(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{guess_mime_type(path)};base64,{encoded}"


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout_seconds: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
    )
    for key, value in headers.items():
        request.add_header(key, value)

    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc)) from exc

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON response: {body[:300]}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("JSON response must be an object.")
    return parsed


def post_form(url: str, data: dict[str, str], headers: dict[str, str], timeout_seconds: int) -> dict[str, Any]:
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(url, data=encoded, method="POST")
    for key, value in headers.items():
        request.add_header(key, value)

    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc)) from exc

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON response: {body[:300]}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("JSON response must be an object.")
    return parsed


def get_json(url: str, headers: dict[str, str], timeout_seconds: int) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    for key, value in headers.items():
        request.add_header(key, value)

    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc)) from exc

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON response: {body[:300]}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("JSON response must be an object.")
    return parsed


def sleep_seconds(seconds: float) -> None:
    time.sleep(seconds)


def extract_chat_message_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return ""

    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    chunks.append(text.strip())
            if chunks:
                return "\n".join(chunks)
    return ""


def parse_json_object(text: str, provider_name: str) -> dict[str, Any]:
    normalized = text.strip()
    if normalized.startswith("```json"):
        normalized = normalized[len("```json") :].strip()
    elif normalized.startswith("```JSON"):
        normalized = normalized[len("```JSON") :].strip()
    elif normalized.startswith("```"):
        normalized = normalized[len("```") :].strip()
    if normalized.endswith("```"):
        normalized = normalized[:-3].strip()

    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{provider_name} returned non-JSON output: {text[:300]}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{provider_name} returned a non-object JSON payload.")
    return parsed

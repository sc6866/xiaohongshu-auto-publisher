from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from agents.base import BaseAgent
from common.baidu_ocr_client import BaiduOcrClient
from common.glm_vision_client import GlmVisionClient
from common.image_support import prepare_local_image_path, register_heif_support
from common.models import ImageInsight
from common.qwen_vision_client import QwenVisionClient


class ImageInsightAgent(BaseAgent):
    def __init__(self, settings, db, vector_store):
        super().__init__(settings, db, vector_store)
        register_heif_support()
        self.qwen_vision_client = QwenVisionClient(settings)
        self.glm_vision_client = GlmVisionClient(settings)
        self.ocr_client = BaiduOcrClient(settings)

    def prepare_image_paths(self, image_paths: list[str]) -> list[str]:
        prepared_paths: list[str] = []
        for item in image_paths:
            path = self.settings.resolve_path(item) if not Path(item).is_absolute() else Path(item)
            if not path.exists():
                raise FileNotFoundError(f"Image not found: {path}")
            prepared = prepare_local_image_path(path, self.settings.web_upload_dir)
            prepared_paths.append(str(prepared))
        return prepared_paths

    def analyze(self, image_paths: list[str], preferred_mode: str | None = None) -> dict[str, Any]:
        paths = [self.settings.resolve_path(path) if not Path(path).is_absolute() else Path(path) for path in image_paths]
        for path in paths:
            if not path.exists():
                raise FileNotFoundError(f"Image not found: {path}")

        metadata = [self._read_local_metadata(path) for path in paths]
        warnings: list[str] = []

        vision_result: dict[str, Any] | None = None
        vision_client = self._select_vision_client()
        if vision_client is not None and vision_client.is_configured():
            try:
                vision_result = vision_client.analyze_images(paths, self._build_prompt(metadata, preferred_mode))
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"vision:{exc}")
                self.logger.warning("Vision analysis failed, fallback to OCR/local metadata: %s", exc)

        ocr_results: list[dict[str, Any]] = []
        if self.ocr_client.is_configured():
            try:
                ocr_results = self.ocr_client.recognize_images(paths)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"ocr:{exc}")
                self.logger.warning("OCR analysis failed, fallback to vision/local metadata: %s", exc)
        elif not (vision_client and vision_client.is_configured()):
            warnings.append("未配置视觉模型或 OCR 密钥，当前仅使用本地图片元数据与文件名推断。")

        insights = self._normalize_insights(paths, metadata, vision_result, ocr_results, preferred_mode)
        merged = self._merge_insights(insights, preferred_mode)
        self.logger.info("Analyzed %s image(s) into mode=%s topic=%s", len(paths), merged["content_mode"], merged["topic"])
        return {
            "topic": merged["topic"],
            "content_mode": merged["content_mode"],
            "summary": merged["summary"],
            "keywords": merged["keywords"],
            "visible_text": merged["visible_text"],
            "facts": merged["facts"],
            "insights": [item.to_dict() for item in insights],
            "vision_enabled": bool(vision_client and vision_client.is_configured()),
            "vision_provider": getattr(vision_client, "provider_name", "none") if vision_client else "none",
            "ocr_enabled": self.ocr_client.is_configured(),
            "ocr_provider": self.ocr_client.provider_name if self.ocr_client.is_configured() else "none",
            "analysis_mode": self._analysis_mode(vision_client, bool(vision_result), bool(ocr_results)),
            "analysis_warnings": warnings,
        }

    def _read_local_metadata(self, path: Path) -> dict[str, Any]:
        try:
            with Image.open(path) as image:
                width, height = image.size
                exif = image.getexif()
                exif_summary = {}
                if exif:
                    for key, value in exif.items():
                        if str(key) in {"271", "272", "306", "36867"}:
                            exif_summary[str(key)] = str(value)
                return {
                    "image_path": str(path),
                    "filename": path.name,
                    "width": width,
                    "height": height,
                    "aspect_ratio": round(width / max(height, 1), 3),
                    "exif": exif_summary,
                }
        except UnidentifiedImageError as exc:
            raise ValueError(f"Unsupported image file: {path}") from exc

    def _build_prompt(self, metadata: list[dict[str, Any]], preferred_mode: str | None) -> str:
        mode_hint = preferred_mode or "auto"
        return (
            "你是小红书图片内容分析助手。"
            "请基于输入图片判断更适合生成哪种内容：product_review、travel_guide、lifestyle_note。"
            "只返回 JSON，不要返回 markdown。"
            'JSON 顶层结构必须为 {"items": [...]}。'
            "每个 item 必须包含 image_path、inferred_topic、content_mode、summary、visible_text、keywords、facts。"
            "visible_text 为图片里可见文字数组。"
            "keywords 为 3-8 个短词。"
            "facts 尽量提取 price、product_type、scene、location、objects、selling_points、risk_points、audience。"
            f"当前模式偏好是：{mode_hint}。"
            f"本地元数据参考：{json.dumps(metadata, ensure_ascii=False)}"
        )

    def _normalize_insights(
        self,
        paths: list[Path],
        metadata: list[dict[str, Any]],
        remote_result: dict[str, Any] | None,
        ocr_results: list[dict[str, Any]],
        preferred_mode: str | None,
    ) -> list[ImageInsight]:
        remote_items: list[dict[str, Any]] = []
        if isinstance(remote_result, dict):
            candidate_items = remote_result.get("items")
            if isinstance(candidate_items, list):
                remote_items = [item for item in candidate_items if isinstance(item, dict)]

        ocr_map = {
            str(item.get("image_path")): item
            for item in ocr_results
            if isinstance(item, dict) and item.get("image_path")
        }
        insights: list[ImageInsight] = []
        for index, path in enumerate(paths):
            remote_item = remote_items[index] if index < len(remote_items) else {}
            local_item = metadata[index]
            ocr_item = ocr_map.get(str(path), {})
            ocr_lines = self._coerce_list(ocr_item.get("lines"))

            inferred_topic = str(remote_item.get("inferred_topic") or self._topic_from_ocr_lines(ocr_lines) or self._topic_from_filename(path))
            content_mode = str(
                remote_item.get("content_mode")
                or preferred_mode
                or self._mode_from_ocr_lines(ocr_lines)
                or self._mode_from_filename(path)
            )
            summary = str(remote_item.get("summary") or self._summary_from_ocr_lines(path, ocr_lines))
            visible_text = self._coerce_list(remote_item.get("visible_text"))
            for line in ocr_lines:
                if line not in visible_text:
                    visible_text.append(line)
            keywords = self._coerce_list(remote_item.get("keywords")) or self._keywords_from_ocr_lines(ocr_lines) or self._keywords_from_filename(path)
            facts = remote_item.get("facts") if isinstance(remote_item.get("facts"), dict) else {}
            facts = {
                **facts,
                "filename": path.name,
                "width": local_item["width"],
                "height": local_item["height"],
                "aspect_ratio": local_item["aspect_ratio"],
                "exif": local_item["exif"],
                "ocr_lines": ocr_lines,
                "ocr_words_count": int(ocr_item.get("words_count", 0) or 0),
            }
            insights.append(
                ImageInsight(
                    image_path=str(path),
                    inferred_topic=inferred_topic,
                    content_mode=content_mode,
                    summary=summary,
                    visible_text=visible_text,
                    keywords=keywords,
                    facts=facts,
                )
            )
        return insights

    def _merge_insights(self, insights: list[ImageInsight], preferred_mode: str | None) -> dict[str, Any]:
        first = insights[0]
        content_mode = preferred_mode or first.content_mode
        topic = first.inferred_topic
        keywords: list[str] = []
        visible_text: list[str] = []
        facts: dict[str, Any] = {"images": [item.image_path for item in insights]}
        summaries: list[str] = []

        for item in insights:
            summaries.append(item.summary)
            for keyword in item.keywords:
                if keyword not in keywords:
                    keywords.append(keyword)
            for text in item.visible_text:
                if text not in visible_text:
                    visible_text.append(text)
            for key, value in item.facts.items():
                if key not in facts:
                    facts[key] = value
        return {
            "topic": topic,
            "content_mode": content_mode,
            "summary": " ".join(summaries[:2]),
            "keywords": keywords[:8],
            "visible_text": visible_text[:12],
            "facts": facts,
        }

    def _topic_from_filename(self, path: Path) -> str:
        stem = path.stem.replace("_", " ").replace("-", " ").strip()
        if stem:
            return stem
        return "图片灵感内容"

    def _mode_from_filename(self, path: Path) -> str:
        lowered = path.name.lower()
        if any(token in lowered for token in ("travel", "trip", "景点", "旅行", "旅游")):
            return "travel_guide"
        if any(token in lowered for token in ("product", "sku", "商品", "产品", "detail", "main")):
            return "product_review"
        return "lifestyle_note"

    def _mode_from_ocr_lines(self, lines: list[str]) -> str | None:
        joined = " ".join(lines).lower()
        if not joined:
            return None
        if any(token in joined for token in ("到手", "券后", "旗舰店", "规格", "材质", "参数", "购买", "下单", "优惠")):
            return "product_review"
        if any(token in joined for token in ("景区", "路线", "古城", "夜市", "酒店", "门票", "交通", "旅行", "旅游")):
            return "travel_guide"
        return None

    def _keywords_from_filename(self, path: Path) -> list[str]:
        stem = path.stem.replace("_", " ").replace("-", " ").strip()
        words = [word for word in stem.split() if word]
        return words[:6] or ["图片灵感", "真实体验"]

    def _keywords_from_ocr_lines(self, lines: list[str]) -> list[str]:
        keywords: list[str] = []
        for line in lines:
            normalized = re.sub(r"[^\w\u4e00-\u9fff]+", " ", line).strip()
            for chunk in normalized.split():
                token = chunk.strip()
                if len(token) < 2 or len(token) > 16:
                    continue
                if token.isdigit():
                    continue
                if token not in keywords:
                    keywords.append(token)
                if len(keywords) >= 6:
                    return keywords
        return keywords

    def _topic_from_ocr_lines(self, lines: list[str]) -> str | None:
        for line in lines:
            candidate = re.sub(r"\s+", " ", line).strip(" -_|")
            if len(candidate) < 2:
                continue
            if sum(char.isdigit() for char in candidate) > len(candidate) // 2:
                continue
            return candidate[:18]
        return None

    def _summary_from_ocr_lines(self, path: Path, lines: list[str]) -> str:
        if lines:
            preview = "；".join(lines[:3])
            return f"图片里可见的关键信息包括：{preview}。"
        return f"基于图片 {path.name} 生成的小红书选题素材。"

    def _coerce_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def _select_vision_client(self) -> Any | None:
        provider = str(self.settings.get("vision", "provider", "qwen")).strip().lower()
        ordered_clients = {
            "qwen": self.qwen_vision_client,
            "glm": self.glm_vision_client,
        }
        if provider in ordered_clients:
            chosen = ordered_clients[provider]
            if chosen.is_configured():
                return chosen
            for name in ("qwen", "glm"):
                candidate = ordered_clients[name]
                if name != provider and candidate.is_configured():
                    self.logger.info("Vision provider %s not configured, fallback to %s.", provider, name)
                    return candidate
            return chosen

        for name in ("qwen", "glm"):
            candidate = ordered_clients[name]
            if candidate.is_configured():
                return candidate
        return ordered_clients["qwen"]

    def _analysis_mode(self, vision_client: Any | None, has_vision_result: bool, has_ocr_result: bool) -> str:
        if has_vision_result and has_ocr_result:
            return "vision+ocr"
        if has_vision_result and vision_client is not None:
            return f"{vision_client.provider_name}_only"
        if has_ocr_result:
            return "ocr_only"
        return "local_only"

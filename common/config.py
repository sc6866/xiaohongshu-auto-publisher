from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_SETTINGS: dict[str, Any] = {
    "runtime": {
        "timezone": "Asia/Shanghai",
        "simulate": False,
        "log_level": "INFO",
        "account_name": "default",
    },
    "paths": {
        "data_dir": "data",
        "logs_dir": "logs",
        "db_path": "data/knowledge.db",
        "sensitive_words_path": "data/sensitive_words.txt",
        "cover_template_dir": "data/cover_templates",
        "generated_cover_dir": "data/generated_covers",
        "vector_db_dir": "data/vector_db",
    },
    "scanner": {
        "min_heat_score": 60,
        "allowed_competition": ["low", "medium"],
        "default_seed_topics": [
            {"keyword": "租房桌面改造", "category": "home"},
            {"keyword": "通勤早八妆容", "category": "beauty"},
            {"keyword": "打工人午饭便当", "category": "food"},
            {"keyword": "秋招简历避坑", "category": "career"},
            {"keyword": "iPad学习流", "category": "tech"},
            {"keyword": "低预算卧室氛围灯", "category": "home"},
            {"keyword": "学生党护肤清单", "category": "beauty"},
        ],
    },
    "spider": {
        "samples_per_topic": 3,
        "request_delay_seconds": [2, 5],
    },
    "quality": {
        "originality_threshold": 70,
        "max_rewrites": 3,
    },
    "content": {
        "retrieval_days": 7,
        "retrieval_top_k": 5,
        "max_body_length": 1000,
        "default_tags": ["小红书运营", "经验分享", "真实体验"],
    },
    "publishing": {
        "daily_limit": 3,
        "allowed_windows": ["09:00-13:00", "18:00-21:00"],
        "retry_limit": 3,
        "dry_run": False,
        "visibility": "public",
    },
    "publisher_profile": {
        "user_id": "",
        "xsec_token": "",
    },
    "cover": {
        "default_template": "default",
        "enable_browser_render": False,
        "aspect_ratio": "3:4",
        "image_enabled": True,
        "image_provider": "qwen",
        "qwen_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen_image_model": "wanx2.1-t2i-turbo",
        "image_size": "768x1024",
        "image_timeout_seconds": 120,
    },
    "vision": {
        "enabled": True,
        "provider": "qwen",
        "qwen_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen_model": "qwen3-vl-plus",
        "glm_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "glm_model": "glm-4.6v-flash",
        "timeout_seconds": 90,
    },
    "ocr": {
        "enabled": True,
        "provider": "baidu",
        "baidu_token_url": "https://aip.baidubce.com/oauth/2.0/token",
        "baidu_general_url": "https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic",
        "timeout_seconds": 30,
    },
    "writer": {
        "enabled": True,
        "provider": "qwen",
        "qwen_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen_model": "qwen-plus-latest",
        "glm_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "glm_model": "glm-4-flash",
        "timeout_seconds": 90,
    },
    "web": {
        "enabled": True,
        "host": "127.0.0.1",
        "port": 8787,
        "upload_dir": "data/uploads/web",
        "max_upload_mb": 20,
        "public_base_url": "",
    },
    "xhs_mcp": {
        "enabled": True,
        "base_url": "",
        "auto_start": True,
        "executable_path": "C:/Users/Administrator/Desktop/xiaohongshu-mcp-windows-amd64/xiaohongshu-mcp-windows-amd64.exe",
        "working_dir": "C:/Users/Administrator/Desktop/xiaohongshu-mcp-windows-amd64",
        "port": 18090,
        "headless": True,
        "startup_timeout_seconds": 15,
        "request_timeout_seconds": 45,
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


@dataclass(slots=True)
class Settings:
    raw: dict[str, Any]
    root_dir: Path

    def get(self, section: str, key: str | None = None, default: Any = None) -> Any:
        section_value = self.raw.get(section, default)
        if key is None:
            return section_value
        if not isinstance(section_value, dict):
            return default
        return section_value.get(key, default)

    def resolve_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return self.root_dir / path

    @property
    def db_path(self) -> Path:
        return self.resolve_path(self.get("paths", "db_path"))

    @property
    def sensitive_words_path(self) -> Path:
        return self.resolve_path(self.get("paths", "sensitive_words_path"))

    @property
    def cover_template_dir(self) -> Path:
        return self.resolve_path(self.get("paths", "cover_template_dir"))

    @property
    def generated_cover_dir(self) -> Path:
        return self.resolve_path(self.get("paths", "generated_cover_dir"))

    @property
    def vector_db_dir(self) -> Path:
        return self.resolve_path(self.get("paths", "vector_db_dir"))

    @property
    def logs_dir(self) -> Path:
        return self.resolve_path(self.get("paths", "logs_dir"))

    @property
    def web_upload_dir(self) -> Path:
        return self.resolve_path(self.get("web", "upload_dir", "data/uploads/web"))

    @property
    def timezone(self) -> str:
        return self.get("runtime", "timezone", "Asia/Shanghai")


def _load_yaml_with_optional_dependency(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError:
        return {}

    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file must contain a mapping: {path}")
    return data


def _parse_bool_env(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def _apply_env_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    env_map: list[tuple[str, str, str, Any]] = [
        ("XHS_ACCOUNT_NAME", "runtime", "account_name", str),
        ("XHS_WEB_HOST", "web", "host", str),
        ("XHS_WEB_PORT", "web", "port", int),
        ("XHS_WEB_PUBLIC_BASE_URL", "web", "public_base_url", str),
        ("XHS_MCP_BASE_URL", "xhs_mcp", "base_url", str),
        ("XHS_MCP_PORT", "xhs_mcp", "port", int),
        ("XHS_MCP_EXECUTABLE_PATH", "xhs_mcp", "executable_path", str),
        ("XHS_MCP_WORKING_DIR", "xhs_mcp", "working_dir", str),
        ("XHS_PUBLISHER_USER_ID", "publisher_profile", "user_id", str),
        ("XHS_PUBLISHER_XSEC_TOKEN", "publisher_profile", "xsec_token", str),
    ]
    for env_name, section, key, caster in env_map:
        value = os.environ.get(env_name)
        if value is None or value == "":
            continue
        raw.setdefault(section, {})
        raw[section][key] = caster(value)

    for env_name, section, key in (
        ("XHS_MCP_ENABLED", "xhs_mcp", "enabled"),
        ("XHS_MCP_AUTO_START", "xhs_mcp", "auto_start"),
        ("XHS_RUNTIME_SIMULATE", "runtime", "simulate"),
    ):
        parsed = _parse_bool_env(os.environ.get(env_name))
        if parsed is None:
            continue
        raw.setdefault(section, {})
        raw[section][key] = parsed
    return raw


def load_settings(path: str | Path) -> Settings:
    config_path = Path(path)
    root_dir = config_path.parent.parent.resolve() if config_path.parent.name == "config" else Path.cwd()

    raw = copy.deepcopy(DEFAULT_SETTINGS)
    if config_path.exists():
        file_settings = _load_yaml_with_optional_dependency(config_path)
        raw = _deep_merge(raw, file_settings)
        local_override_path = config_path.with_name(f"{config_path.stem}.local{config_path.suffix}")
        if local_override_path.exists():
            local_settings = _load_yaml_with_optional_dependency(local_override_path)
            raw = _deep_merge(raw, local_settings)
    raw = _apply_env_overrides(raw)

    settings = Settings(raw=raw, root_dir=root_dir)
    for target in (
        settings.db_path.parent,
        settings.vector_db_dir,
        settings.logs_dir,
        settings.cover_template_dir,
        settings.generated_cover_dir,
        settings.web_upload_dir,
    ):
        target.mkdir(parents=True, exist_ok=True)
    return settings


def dump_settings_json(settings: Settings) -> str:
    return json.dumps(settings.raw, ensure_ascii=False, indent=2)

"""合并磁盘上的 config/models.yml 与环境变量覆盖（便于 Docker / 编排注入密钥与端点）。"""
from __future__ import annotations

import copy
import os
from typing import Any

import yaml

from app.paths import PROJECT_ROOT

_MODELS_PATH = os.path.join(PROJECT_ROOT, "config", "models.yml")


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(data) if data else {}
    llm = out.setdefault("llm", {})
    nuextract = out.setdefault("nuextract", {})

    if (v := os.environ.get("INVOICE_COLLECT_LLM_BASE_URL", "").strip()):
        llm["base_url"] = v
    if (v := os.environ.get("INVOICE_COLLECT_LLM_API_KEY", "").strip()):
        llm["api_key"] = v
    if (v := os.environ.get("INVOICE_COLLECT_LLM_MODEL", "").strip()):
        llm["model"] = v
    if (v := os.environ.get("INVOICE_COLLECT_LLM_TIMEOUT", "").strip()):
        try:
            llm["timeout"] = int(v)
        except ValueError:
            pass

    if (v := os.environ.get("INVOICE_COLLECT_NUEXTRACT_HOST", "").strip()):
        nuextract["host"] = v
    if (v := os.environ.get("INVOICE_COLLECT_NUEXTRACT_PORT", "").strip()):
        try:
            nuextract["port"] = int(v)
        except ValueError:
            nuextract["port"] = v
    if (v := os.environ.get("INVOICE_COLLECT_NUEXTRACT_TIMEOUT", "").strip()):
        try:
            nuextract["timeout"] = int(v)
        except ValueError:
            pass

    return out


def load_models_config() -> dict[str, Any]:
    with open(_MODELS_PATH, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raw = {}
    return _apply_env_overrides(raw)

import os
import yaml
from fastapi import APIRouter, HTTPException
from app.schemas import (
    CategoriesConfig,
    CategoryConfig,
    MessageResponse,
    ModelsConfig,
    RulesConfig,
)
from app.services.llm_client import reset_llm_client
from app.services.extractor import reset_extractor

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = os.path.join(BASE_DIR, "config")

router = APIRouter(prefix="/api/config", tags=["config"])


def _read_yaml(filename: str) -> dict:
    path = os.path.join(CONFIG_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write_yaml(filename: str, data: dict) -> None:
    path = os.path.join(CONFIG_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _normalize_category_config(cat: dict) -> dict:
    """归一化费用大类配置：除 other 外默认支持分组。"""
    category = dict(cat or {})
    cid = str(category.get("id") or "").strip()
    category["id"] = cid

    if cid == "other":
        category["groupable"] = False
    else:
        category["groupable"] = bool(category.get("groupable", True))
    return category


# ─── 费用大类配置 ──────────────────────────────────────────────────────────────

@router.get("/categories", response_model=CategoriesConfig)
async def get_categories():
    """读取费用大类配置"""
    data = _read_yaml("categories.yml")
    normalized = [_normalize_category_config(c) for c in data.get("categories", [])]
    return CategoriesConfig(
        categories=[CategoryConfig(**c) for c in normalized]
    )


@router.put("/categories", response_model=MessageResponse)
async def update_categories(config: CategoriesConfig):
    """更新费用大类配置"""
    normalized = [_normalize_category_config(c.model_dump()) for c in config.categories]
    data = {"categories": normalized}
    _write_yaml("categories.yml", data)
    return MessageResponse(message="费用大类配置已保存")


# ─── 分类规则配置 ──────────────────────────────────────────────────────────────

@router.get("/rules", response_model=RulesConfig)
async def get_rules():
    """读取分类规则"""
    data = _read_yaml("rules.yml")
    return RulesConfig.model_validate(data)


@router.put("/rules", response_model=MessageResponse)
async def update_rules(config: RulesConfig):
    """更新分类规则"""
    data = config.model_dump()
    _write_yaml("rules.yml", data)
    return MessageResponse(message="分类规则已保存")


# ─── 模型服务配置 ──────────────────────────────────────────────────────────────

@router.get("/models", response_model=ModelsConfig)
async def get_models_config():
    """读取模型服务配置"""
    data = _read_yaml("models.yml")
    try:
        return ModelsConfig.model_validate(data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"配置文件格式错误：{e}")


@router.put("/models", response_model=MessageResponse)
async def update_models_config(config: ModelsConfig):
    """更新模型服务配置，并重置客户端实例"""
    data = config.model_dump()
    _write_yaml("models.yml", data)
    reset_llm_client()
    reset_extractor()
    return MessageResponse(message="模型服务配置已保存并生效")

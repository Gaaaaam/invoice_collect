import os
import yaml
import json
from fastapi import APIRouter, HTTPException
from app.schemas import (
    CategoriesConfig,
    CategoryConfig,
    MessageResponse,
    ModelsConfig,
    RulesConfig,
    TravelConfig,
    NuExtractTemplatesConfig,
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


def _read_json(filename: str) -> dict:
    path = os.path.join(CONFIG_DIR, filename)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(filename: str, data: dict) -> None:
    path = os.path.join(CONFIG_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def _validate_nuextract_templates(config: NuExtractTemplatesConfig) -> None:
    if not config.templates:
        raise HTTPException(status_code=400, detail="至少需要保留一个发票类型与对应抽取模板")

    seen_ids: set[str] = set()
    seen_invoice_types: set[str] = set()
    for idx, item in enumerate(config.templates, start=1):
        tpl_id = (item.id or "").strip()
        inv_type = (item.invoice_type or "").strip()
        schema = item.schema_definition

        if not tpl_id:
            raise HTTPException(status_code=400, detail=f"第 {idx} 个模板缺少 id")
        if tpl_id in seen_ids:
            raise HTTPException(status_code=400, detail=f"模板 id 重复：{tpl_id}")
        seen_ids.add(tpl_id)

        if not inv_type:
            raise HTTPException(status_code=400, detail=f"第 {idx} 个模板缺少发票类型名称")
        if inv_type in seen_invoice_types:
            raise HTTPException(status_code=400, detail=f"发票类型重复：{inv_type}")
        seen_invoice_types.add(inv_type)

        if not isinstance(schema, dict) or not schema:
            raise HTTPException(
                status_code=400,
                detail=f"第 {idx} 个模板（{inv_type}）的 schema 必须是非空 JSON 对象",
            )


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
    existing = _read_yaml("models.yml")
    merged = {**(existing if isinstance(existing, dict) else {}), **data}
    _write_yaml("models.yml", merged)
    reset_llm_client()
    reset_extractor()
    return MessageResponse(message="模型服务配置已保存并生效")


# ─── 差旅费设置 ────────────────────────────────────────────────────────────────

@router.get("/travel", response_model=TravelConfig)
async def get_travel_config():
    """读取差旅费归集设置（所在城市等）"""
    try:
        data = _read_yaml("travel.yml")
        return TravelConfig.model_validate(data)
    except FileNotFoundError:
        return TravelConfig()


@router.put("/travel", response_model=MessageResponse)
async def update_travel_config(config: TravelConfig):
    """更新差旅费归集设置"""
    _write_yaml("travel.yml", config.model_dump())
    return MessageResponse(message="差旅费设置已保存")


# ─── 抽取模板配置 ─────────────────────────────────────────────────────────────

@router.get("/nuextract-templates", response_model=NuExtractTemplatesConfig)
async def get_nuextract_templates():
    """读取抽取模板配置并自动向下兼容旧版 JSON"""
    data = _read_json("nuextract_templates.json")
    if not data:
        return NuExtractTemplatesConfig(templates=[])
    if "templates" in data:
        return NuExtractTemplatesConfig.model_validate(data)
    
    # 兼容老格式
    _LEGACY_MAP = {
        "regular_invoice_template": "电子发票（普通发票）",
        "railway_electronic_ticket_template": "电子发票（铁路电子客票）",
        "air_transportation_electronic_ticket_itinerary": "航空运输电子客票行程单",
        "train_physical_ticket": "火车票报销凭证"
    }
    templates = []
    for k, v in data.items():
        if k == "invoice_type":
            continue
        if isinstance(v, dict):
            inv_type = _LEGACY_MAP.get(k, k)
            templates.append({
                "id": k,
                "invoice_type": inv_type,
                "schema": v
            })
    return NuExtractTemplatesConfig(templates=templates)


@router.put("/nuextract-templates", response_model=MessageResponse)
async def update_nuextract_templates(config: NuExtractTemplatesConfig):
    """更新抽取模板配置"""
    _validate_nuextract_templates(config)
    data = config.model_dump(by_alias=True)
    _write_json("nuextract_templates.json", data)
    reset_extractor()
    return MessageResponse(message="抽取模板配置已保存并生效")


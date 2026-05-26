import logging
import os
import re
import yaml
from typing import Optional

from app.services.llm_client import get_llm_client

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RULES_CONFIG_PATH = os.path.join(BASE_DIR, "config", "rules.yml")
CATEGORIES_CONFIG_PATH = os.path.join(BASE_DIR, "config", "categories.yml")

_RULES_CACHE: dict = {"mtime": None, "data": []}
_CATEGORIES_CACHE: dict = {"mtime": None, "data": []}


def _load_yaml_list_with_cache(path: str, key: str, cache: dict) -> list[dict]:
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        logger.warning("配置文件不存在: %s", path)
        cache["mtime"] = None
        cache["data"] = []
        return []

    if cache.get("mtime") == mtime:
        return cache.get("data", [])

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    values = data.get(key, [])
    if not isinstance(values, list):
        values = []

    cache["mtime"] = mtime
    cache["data"] = values
    return values


def load_rules() -> list[dict]:
    rules = _load_yaml_list_with_cache(RULES_CONFIG_PATH, "rules", _RULES_CACHE)
    return sorted(rules, key=lambda r: r.get("priority", 50))


def load_categories() -> list[dict]:
    return _load_yaml_list_with_cache(CATEGORIES_CONFIG_PATH, "categories", _CATEGORIES_CACHE)


def _match_condition(invoice: dict, condition: dict) -> bool:
    """判断单条条件是否匹配发票字段"""
    field = condition.get("field", "")
    match_type = condition.get("match_type", "contains")
    value = condition.get("value", "")

    field_value = str(invoice.get(field) or "")

    if match_type == "contains":
        return value in field_value
    if match_type == "equals":
        return field_value.strip() == value.strip()
    if match_type == "regex":
        try:
            return bool(re.search(value, field_value))
        except re.error:
            return False
    return False


def _match_rule(invoice: dict, rule: dict) -> bool:
    """判断一条规则是否整体匹配"""
    conditions = rule.get("conditions", [])
    logic = rule.get("condition_logic", "OR").upper()

    if not conditions:
        return False

    results = [_match_condition(invoice, cond) for cond in conditions]

    if logic == "AND":
        return all(results)
    return any(results)


def _find_matching_rule(invoice: dict) -> Optional[dict]:
    rules = load_rules()
    for rule in rules:
        if _match_rule(invoice, rule):
            rid = rule.get("id", "")
            rname = rule.get("name", "")
            logger.debug(
                "rules_yml matched invoice_id=%s rule_id=%s rule_name=%s -> %s",
                invoice.get("id"),
                rid,
                rname,
                rule.get("target_category"),
            )
            return rule
    return None


def classify_by_rules(invoice: dict) -> Optional[str]:
    """
    规则匹配分类。
    按优先级依次尝试每条规则，首条命中的规则决定类别。
    返回 category_id 或 None（无规则命中）。
    """
    rule = _find_matching_rule(invoice)
    if rule:
        return rule.get("target_category")
    return None


_SUBCATEGORY_TO_CATEGORY = {
    "air": "travel",
    "train": "travel",
    "hotel": "travel",
    "taxi_local": "travel",
    "ridehailing_local": "travel",
    "meeting": "meeting",
    "material": "material",
}


def _log_invoice_debug_context(invoice: dict) -> None:
    if not logger.isEnabledFor(logging.DEBUG):
        return
    parts = [
        f"type={invoice.get('invoice_type')!r}",
        f"subcategory={invoice.get('invoice_subcategory')!r}",
        f"type_detected={invoice.get('invoice_type_detected')!r}",
        f"seller={invoice.get('seller_name')!r}",
        f"items={str(invoice.get('items_description') or '')[:120]!r}",
    ]
    logger.debug("invoice_id=%s context %s", invoice.get("id"), " ".join(parts))


async def classify_invoice(
    invoice: dict,
    use_subcategory: bool = True,
    use_rules: bool = True,
    use_llm: bool = True,
) -> tuple[str, str]:
    """
    对单张发票进行分类。
    优先级（可通过参数各自开关）：
      1. invoice_subcategory（电子发票普通发票内容分析结果）
      2. 规则匹配
      3. LLM 兜底
    返回 (category_id, classified_by)，classified_by 为 'rule'/'llm'/'default'。
    """
    inv_id = invoice.get("id")
    filename = invoice.get("filename") or ""

    # 1. 电子发票（普通发票）子类型直接映射
    if use_subcategory:
        subcategory = invoice.get("invoice_subcategory")
        if subcategory and subcategory in _SUBCATEGORY_TO_CATEGORY:
            cat = _SUBCATEGORY_TO_CATEGORY[subcategory]
            logger.info(
                "classify invoice_id=%s file=%s path=subcategory subcategory=%s -> category=%s (classified_by=rule)",
                inv_id,
                filename,
                subcategory,
                cat,
            )
            _log_invoice_debug_context(invoice)
            return cat, "rule"

    # 1b. 会议相关单据先归 meeting，后续在分组阶段可迁移到 travel 闭环
    detected = invoice.get("invoice_type_detected") or ""
    if detected in ("meeting_file", "meeting_invoice"):
        logger.info(
            "classify invoice_id=%s file=%s path=meeting_candidate detected=%s -> category=meeting (classified_by=rule)",
            inv_id,
            filename,
            detected,
        )
        _log_invoice_debug_context(invoice)
        return "meeting", "rule"

    # 1c. 抽取阶段已选用特定模板时，直接归差旅费（不依赖 invoice_subcategory）
    if detected in (
        "train_electronic",
        "train_physical",
        "ridehailing_itinerary",
    ):
        logger.info(
            "classify invoice_id=%s file=%s path=template_detected detected=%s -> category=travel (classified_by=rule)",
            inv_id,
            filename,
            detected,
        )
        _log_invoice_debug_context(invoice)
        return "travel", "rule"

    # 2. 规则匹配
    if use_rules:
        rule = _find_matching_rule(invoice)
        if rule:
            category_id = rule.get("target_category")
            logger.info(
                "classify invoice_id=%s file=%s path=rules_yml rule_id=%s rule_name=%r -> category=%s (classified_by=rule)",
                inv_id,
                filename,
                rule.get("id", ""),
                rule.get("name", ""),
                category_id,
            )
            _log_invoice_debug_context(invoice)
            return category_id, "rule"

    # 3. LLM 兜底
    if use_llm:
        categories = load_categories()
        llm = get_llm_client()
        llm_category, raw_snippet = await llm.classify_invoice_with_raw(invoice, categories)
        if llm_category:
            logger.info(
                "classify invoice_id=%s file=%s path=llm -> category=%s (classified_by=llm)",
                inv_id,
                filename,
                llm_category,
            )
            logger.debug(
                "classify invoice_id=%s llm_raw_snippet=%r",
                inv_id,
                raw_snippet,
            )
            _log_invoice_debug_context(invoice)
            return llm_category, "llm"

    logger.info(
        "classify invoice_id=%s file=%s path=default -> category=other (classified_by=default)",
        inv_id,
        filename,
    )
    _log_invoice_debug_context(invoice)
    return "other", "default"

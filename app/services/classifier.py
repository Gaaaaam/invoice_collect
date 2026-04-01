import os
import re
import yaml
from typing import Optional

from app.services.llm_client import get_llm_client

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RULES_CONFIG_PATH = os.path.join(BASE_DIR, "config", "rules.yml")
CATEGORIES_CONFIG_PATH = os.path.join(BASE_DIR, "config", "categories.yml")


def load_rules() -> list[dict]:
    with open(RULES_CONFIG_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    rules = data.get("rules", [])
    return sorted(rules, key=lambda r: r.get("priority", 50))


def load_categories() -> list[dict]:
    with open(CATEGORIES_CONFIG_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("categories", [])


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


def classify_by_rules(invoice: dict) -> Optional[str]:
    """
    规则匹配分类。
    按优先级依次尝试每条规则，首条命中的规则决定类别。
    返回 category_id 或 None（无规则命中）。
    """
    rules = load_rules()
    for rule in rules:
        if _match_rule(invoice, rule):
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
    # 1. 电子发票（普通发票）子类型直接映射
    if use_subcategory:
        subcategory = invoice.get("invoice_subcategory")
        if subcategory and subcategory in _SUBCATEGORY_TO_CATEGORY:
            return _SUBCATEGORY_TO_CATEGORY[subcategory], "rule"

    # 2. 规则匹配
    if use_rules:
        category_id = classify_by_rules(invoice)
        if category_id:
            return category_id, "rule"

    # 3. LLM 兜底
    if use_llm:
        categories = load_categories()
        llm = get_llm_client()
        llm_category = await llm.classify_invoice(invoice, categories)
        if llm_category:
            return llm_category, "llm"

    return "other", "default"

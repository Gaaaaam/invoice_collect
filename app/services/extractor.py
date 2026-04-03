"""
发票信息抽取模块（双阶段 NuExtract 流程）

处理流程：
  1. 【分类阶段】用 nuextract_templates.json 中的 invoice_type 列表作为模板，
     让 NuExtract 判别发票属于哪种类型。
  2. 【抽取阶段】根据判别结果选取对应的 JSON 模板，再次调用 NuExtract 精准抽取字段。

JSON 中定义的四种类型及对应模板：
  - 电子发票（普通发票）         → regular_invoice_template
  - 电子发票（铁路电子客票）     → railway_electronic_ticket_template
  - 航空运输电子客票行程单        → air_transportation_electronic_ticket_itinerary
  - 火车票报销凭证               → train_physical_ticket

对于出租车/网约车/酒店等 JSON 模板未覆盖的类型，仍走原有 Python 模板（1-step）。

电子发票（普通发票）的处理策略：
  - regular_invoice_template 中的「出行信息」字段直接携带出行人/出发地/到达地
  - 「出发城市」「到达城市」字段可直接给出城市名，无需再从备注做正则解析
  - 以上信息大幅提升差旅费识别与闭环检测的准确率
"""

from __future__ import annotations

import json
import os
import re
import sys
import yaml
from typing import Any, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)

from nuextract import NuExtractClient  # noqa: E402
from app.services.station_city_map import normalize_transport_city  # noqa: E402

MODELS_CONFIG_PATH = os.path.join(BASE_DIR, "config", "models.yml")
NUEXTRACT_TEMPLATES_JSON_PATH = os.path.join(BASE_DIR, "config", "nuextract_templates.json")

# ─────────────────────────────────────────────────────────────────────────────
# JSON 模板加载与类型映射
# ─────────────────────────────────────────────────────────────────────────────

_JSON_TEMPLATES_CACHE: Optional[dict] = None


def load_json_templates() -> dict:
    """加载 config/nuextract_templates.json，带内存缓存。"""
    global _JSON_TEMPLATES_CACHE
    if _JSON_TEMPLATES_CACHE is None:
        try:
            with open(NUEXTRACT_TEMPLATES_JSON_PATH, "r", encoding="utf-8") as f:
                _JSON_TEMPLATES_CACHE = json.load(f)
        except Exception as e:
            print(f"[extractor] 无法加载 nuextract_templates.json: {e}")
            _JSON_TEMPLATES_CACHE = {}
    return _JSON_TEMPLATES_CACHE


def reload_json_templates() -> dict:
    """强制重新加载 JSON 模板（用于热更新）。"""
    global _JSON_TEMPLATES_CACHE
    _JSON_TEMPLATES_CACHE = None
    return load_json_templates()


# JSON 中的中文类型名 → 内部 key
_JSON_TYPE_TO_KEY: dict[str, str] = {
    "电子发票（普通发票）": "general_invoice",
    "电子发票（铁路电子客票）": "train_electronic",
    "航空运输电子客票行程单": "air_itinerary",
    "火车票报销凭证": "train_physical",
}

# 内部 key → JSON 模板名
_KEY_TO_JSON_TEMPLATE_NAME: dict[str, str] = {
    "general_invoice": "regular_invoice_template",
    "train_electronic": "railway_electronic_ticket_template",
    "air_itinerary": "air_transportation_electronic_ticket_itinerary",
    "train_physical": "train_physical_ticket",
}

# 不走 JSON 2-step，使用原有 Python 模板的类型
_PYTHON_ONLY_TYPES = {"taxi", "ridehailing", "hotel"}

# ─────────────────────────────────────────────────────────────────────────────
# 原有 Python 模板定义（用于 taxi / ridehailing / hotel 等兜底）
# ─────────────────────────────────────────────────────────────────────────────

TEMPLATE_TRAIN_PHYSICAL = {
    "invoice_type": "verbatim-string",
    "ticket_number": "verbatim-string",
    "train_number": "verbatim-string",
    "departure_station": "verbatim-string",
    "arrival_station": "verbatim-string",
    "departure_date": "verbatim-string",
    "departure_time": "verbatim-string",
    "seat_class": "verbatim-string",
    "car_seat": "verbatim-string",
    "amount": "verbatim-string",
    "passenger_name": "verbatim-string",
    "id_number_partial": "verbatim-string",
}

TEMPLATE_TRAIN_ELECTRONIC = {
    "invoice_type": "verbatim-string",
    "invoice_number": "verbatim-string",
    "issue_date": "verbatim-string",
    "train_number": "verbatim-string",
    "departure_station": "verbatim-string",
    "arrival_station": "verbatim-string",
    "departure_date": "verbatim-string",
    "departure_time": "verbatim-string",
    "seat_class": "verbatim-string",
    "car_seat": "verbatim-string",
    "amount": "verbatim-string",
    "buyer_name": "verbatim-string",
    "passenger_name": "verbatim-string",
    "e_ticket_number": "verbatim-string",
}

TEMPLATE_AIR_ITINERARY = {
    "invoice_type": "verbatim-string",
    "passenger_name": "verbatim-string",
    "carrier": "verbatim-string",
    "flight_number": "verbatim-string",
    "departure_city_airport": "verbatim-string",
    "arrival_city_airport": "verbatim-string",
    "departure_date": "verbatim-string",
    "departure_time": "verbatim-string",
    "fare": "verbatim-string",
    "fuel_surcharge": "verbatim-string",
    "tax": "verbatim-string",
    "total_amount": "verbatim-string",
    "insurance": "verbatim-string",
    "issue_date": "verbatim-string",
    "seller_name": "verbatim-string",
}

TEMPLATE_AIR_ELECTRONIC = {
    "invoice_type": "verbatim-string",
    "invoice_number": "verbatim-string",
    "issue_date": "verbatim-string",
    "seller_name": "verbatim-string",
    "buyer_name": "verbatim-string",
    "passenger_name": "verbatim-string",
    "flight_number": "verbatim-string",
    "departure_city_airport": "verbatim-string",
    "arrival_city_airport": "verbatim-string",
    "departure_date": "verbatim-string",
    "departure_time": "verbatim-string",
    "amount": "verbatim-string",
    "tax_amount": "verbatim-string",
    "total_amount": "verbatim-string",
}

TEMPLATE_TAXI = {
    "invoice_type": "verbatim-string",
    "invoice_code": "verbatim-string",
    "invoice_number": "verbatim-string",
    "seller_name": "verbatim-string",
    "issue_date": "verbatim-string",
    "boarding_time": "verbatim-string",
    "alighting_time": "verbatim-string",
    "distance_km": "verbatim-string",
    "amount": "verbatim-string",
    "city": "verbatim-string",
}

TEMPLATE_RIDEHAILING = {
    "invoice_type": "verbatim-string",
    "invoice_number": "verbatim-string",
    "issue_date": "verbatim-string",
    "seller_name": "verbatim-string",
    "buyer_name": "verbatim-string",
    "trip_date": "verbatim-string",
    "boarding_address": "verbatim-string",
    "alighting_address": "verbatim-string",
    "departure_city": "verbatim-string",
    "amount": "verbatim-string",
    "tax_amount": "verbatim-string",
    "total_amount": "verbatim-string",
}

TEMPLATE_HOTEL = {
    "invoice_type": "verbatim-string",
    "invoice_number": "verbatim-string",
    "issue_date": "verbatim-string",
    "seller_name": "verbatim-string",
    "buyer_name": "verbatim-string",
    "check_in_date": "verbatim-string",
    "check_out_date": "verbatim-string",
    "nights": "verbatim-string",
    "room_type": "verbatim-string",
    "amount": "verbatim-string",
    "tax_amount": "verbatim-string",
    "total_amount": "verbatim-string",
    "hotel_city": "verbatim-string",
}

TEMPLATE_GENERAL_INVOICE = {
    "invoice_type": "verbatim-string",
    "invoice_number": "verbatim-string",
    "issue_date": "verbatim-string",
    "seller_name": "verbatim-string",
    "buyer_name": "verbatim-string",
    "items_description": "verbatim-string",
    "amount": "verbatim-string",
    "tax_amount": "verbatim-string",
    "total_amount": "verbatim-string",
    "remarks": "verbatim-string",
}

TEMPLATE_GENERAL = {
    "invoice_type": "verbatim-string",
    "invoice_number": "verbatim-string",
    "issue_date": "verbatim-string",
    "seller_name": "verbatim-string",
    "buyer_name": "verbatim-string",
    "amount": "verbatim-string",
    "tax_amount": "verbatim-string",
    "total_amount": "verbatim-string",
    "items_description": "verbatim-string",
    "remarks": "verbatim-string",
    "departure_city": "verbatim-string",
    "arrival_city": "verbatim-string",
    "departure_time": "verbatim-string",
    "arrival_time": "verbatim-string",
}

TEMPLATES: dict[str, dict] = {
    "train_physical": TEMPLATE_TRAIN_PHYSICAL,
    "train_electronic": TEMPLATE_TRAIN_ELECTRONIC,
    "air_itinerary": TEMPLATE_AIR_ITINERARY,
    "air_electronic": TEMPLATE_AIR_ELECTRONIC,
    "taxi": TEMPLATE_TAXI,
    "ridehailing": TEMPLATE_RIDEHAILING,
    "hotel": TEMPLATE_HOTEL,
    "general_invoice": TEMPLATE_GENERAL_INVOICE,
    "general": TEMPLATE_GENERAL,
}

INTERCITY_TRANSPORT_TYPES = {
    "train_physical", "train_electronic",
    "air_itinerary", "air_electronic",
}

# ─────────────────────────────────────────────────────────────────────────────
# 发票类型检测（文件名 / 内容启发式）
# ─────────────────────────────────────────────────────────────────────────────

_FILENAME_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(铁路电子客票|电子客票|railway.*e-?ticket)", re.I), "train_electronic"),
    (re.compile(r"(railway[_\-]?invoice|railway)", re.I), "train_electronic"),
    (re.compile(r"(高铁|动车).*电子发票|电子发票.*(高铁|动车)|高铁-电子|动车-电子", re.I), "train_electronic"),
    (re.compile(r"(动车票|高铁票|火车票|train\s*ticket)", re.I), "train_physical"),
    (re.compile(r"(行程单|itinerary|航空运输电子客票行程单)", re.I), "air_itinerary"),
    (re.compile(r"(滴滴|曹操出行|高德打车|嘀嗒|神州专车|网约车|ride\s*hailing)", re.I), "ridehailing"),
    (re.compile(r"(出租车发票|出租发票|taxi.*fare|车费发票)", re.I), "taxi"),
    (re.compile(r"(电子发票.*普通|普通.*电子发票)", re.I), "general_invoice"),
]

_CONTENT_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"电子发票[（(]铁路电子客票"), "train_electronic"),
    (re.compile(r"铁路电子客票"), "train_electronic"),
    (re.compile(r"(报销凭证|仅供报销使用|报销凭证\s*遗失不补)"), "train_physical"),
    (re.compile(r"(动车组|高速铁路|普速铁路|铁路客票)"), "train_physical"),
    (re.compile(
        r"[\u4e00-\u9fa5]{2,10}站\s+[GDCKTZgdcktz]\d{2,5}\s+[\u4e00-\u9fa5]{2,10}站",
        re.I,
    ), "train_physical"),
    (re.compile(r"\d{1,2}:\d{2}\s*开"), "train_physical"),
    (re.compile(r"检\s*票\s*[:：]\s*\S"), "train_physical"),
    (re.compile(
        r"(二等座|一等座|商务座|特等座|软卧|硬卧|软座|硬座).{0,60}[\u4e00-\u9fa5]{2,10}站",
        re.S,
    ), "train_physical"),
    (re.compile(r"航空运输电子客票行程单"), "air_itinerary"),
    (re.compile(r"(滴滴出行|曹操出行|高德打车|哈啰出行|T3出行|享道出行)"), "ridehailing"),
    (re.compile(r"(出租汽车公司|出租车|TAXI|车费发票)"), "taxi"),
    (re.compile(r"电子发票[（(]普通发票"), "general_invoice"),
]

_ITEMS_SUBCATEGORY_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(代订机票|代办机票|机票服务|机票费|订机票|购机票|航空服务|飞机票|代购机票|机场服务费)"), "air"),
    (re.compile(
        r"(代订火车票|代订高铁票|代订动车票|铁路客票|火车票服务|高铁服务|动车服务|代购火车票|"
        r"电子客票号|二等座|一等座|商务座|特等座|软卧|硬卧|软座|硬座|车次|报销凭证|仅供报销使用|"
        r"检票[:：]|退票改签|须交回车站)"
    ), "train"),
    (re.compile(r"(代订酒店|代订客房|住宿费|酒店服务|客房费|住宿服务|宾馆|旅馆|代订住宿)"), "hotel"),
    (re.compile(r"(出租车费|出租车服务|打车费|用车服务|城市交通费)"), "taxi_local"),
    (re.compile(r"(网约车|约车服务|专车服务|顺风车|客运服务费|运输服务\*?客运服务费)"), "ridehailing_local"),
    (re.compile(r"(会议服务|会务费|会议室|场地费|培训服务|研讨会|会议餐|会务服务)"), "meeting"),
    (re.compile(r"(办公用品|耗材|文具|打印|原材料|配件|零件|物料)"), "material"),
]


def detect_invoice_type(filename: str, content_hint: str = "") -> str:
    for pattern, inv_type in _FILENAME_RULES:
        if pattern.search(filename):
            return inv_type
    for pattern, inv_type in _CONTENT_RULES:
        if pattern.search(content_hint):
            return inv_type
    return "general"


def detect_items_subcategory(items_description: str, remarks: str = "") -> Optional[str]:
    text = (items_description or "") + " " + (remarks or "")
    for pattern, subcategory in _ITEMS_SUBCATEGORY_RULES:
        if pattern.search(text):
            return subcategory
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 分类结果解析：从 NuExtract 分类输出中解析内部类型 key
# ─────────────────────────────────────────────────────────────────────────────

def _parse_classified_type(raw: dict, filename: str = "") -> str:
    """
    解析 NuExtract 分类步骤返回的结果，得到内部类型 key。
    若无法确定则回退到文件名启发式检测。
    """
    inv_type_str = ""
    if isinstance(raw, dict):
        inv_type_str = str(raw.get("invoice_type") or "")

    # 精确匹配
    if inv_type_str in _JSON_TYPE_TO_KEY:
        return _JSON_TYPE_TO_KEY[inv_type_str]

    # 模糊匹配
    for name, key in _JSON_TYPE_TO_KEY.items():
        if name in inv_type_str:
            return key

    # 关键词兜底
    if "铁路" in inv_type_str or "客票" in inv_type_str:
        return "train_electronic"
    if "行程单" in inv_type_str or "航空" in inv_type_str:
        return "air_itinerary"
    if "报销凭证" in inv_type_str or "火车票" in inv_type_str:
        return "train_physical"
    if "普通" in inv_type_str or "电子发票" in inv_type_str:
        return "general_invoice"

    # 回退文件名检测
    return detect_invoice_type(filename)


# ─────────────────────────────────────────────────────────────────────────────
# NuExtract 响应结构兼容处理
# ─────────────────────────────────────────────────────────────────────────────

def _extract_nuextract_payload(raw: Any) -> dict:
    if isinstance(raw, list):
        if not raw:
            return {}
        return _extract_nuextract_payload(raw[0])
    if not isinstance(raw, dict):
        return {}
    if isinstance(raw.get("result"), dict):
        return raw["result"]
    data = raw.get("data")
    if isinstance(data, dict):
        if isinstance(data.get("result"), dict):
            return data["result"]
        return data
    results = raw.get("results")
    if isinstance(results, list) and results:
        return _extract_nuextract_payload(results[0])
    return raw


# ─────────────────────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────────────────────

def load_models_config() -> dict:
    with open(MODELS_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_nuextract_config() -> dict:
    return load_models_config().get("nuextract", {})


_DEFAULT_EXTRACTION: dict = {
    "provider": "auto",
    "use_llm_on_fallback": False,
    "ocr": {
        "engine": "rapidocr_onnx",
        "pdf_max_pages": 3,
        "min_text_chars": 80,
    },
}


def load_extraction_config() -> dict:
    raw = load_models_config().get("extraction") or {}
    out = {**_DEFAULT_EXTRACTION, **{k: v for k, v in raw.items() if k != "ocr"}}
    ocr_base = dict(_DEFAULT_EXTRACTION["ocr"])
    ocr_user = raw.get("ocr") if isinstance(raw.get("ocr"), dict) else {}
    ocr_base.update(ocr_user)
    out["ocr"] = ocr_base
    return out


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        cleaned = (
            str(value)
            .replace(",", "").replace("，", "")
            .replace("¥", "").replace("￥", "")
            .replace("CNY", "").replace("元", "")
            .strip()
        )
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _merge_datetime(date_str: Optional[str], time_str: Optional[str]) -> Optional[str]:
    if date_str and time_str:
        return f"{date_str.strip()} {time_str.strip()}"
    return date_str or time_str


# ─── 备注解析（用于兜底解析，JSON 模板已优先处理）────────────────────────────

_RE_CITY_PAIR = re.compile(
    r"([\u4e00-\u9fa5]{2,6})[－\-—→至到\s]+([\u4e00-\u9fa5]{2,6})"
)
_RE_STATION_SUFFIX = re.compile(r"(站|机场|国际机场|国内机场|港|北站|南站|东站|西站|虹桥|浦东|北京首都|北京大兴)$")
_RE_FLIGHT_NO = re.compile(r"\b([A-Z]{2}\d{3,4})\b")
_RE_TRAIN_NO = re.compile(r"\b([GDCKTZgdcktz]\d{2,5})\b")
_RE_DATE = re.compile(
    r"(\d{4}[年/\-\.]\d{1,2}[月/\-\.]\d{1,2}日?)"
    r"|(\d{4}/\d{1,2}/\d{1,2})"
    r"|(\d{4}-\d{2}-\d{2})"
)
_RE_TIME = re.compile(r"\b(\d{1,2}:\d{2})\b")
_RE_PASSENGER_AFTER_NO = re.compile(
    r"[A-Z]{2}\d{3,4}\s+([\u4e00-\u9fa5]{2,4})"
    r"|[GDCKTZgdcktz]\d{2,5}\s+([\u4e00-\u9fa5]{2,4})"
)
_RE_SEAT_CLASS = re.compile(r"(经济舱|商务舱|头等舱|公务舱|一等座|二等座|商务座|特等座|软卧|硬卧|软座|硬座)")


def parse_transport_from_remarks(remarks: str) -> dict:
    if not remarks:
        return {}
    result: dict[str, Any] = {}
    city_matches = list(_RE_CITY_PAIR.finditer(remarks))
    if city_matches:
        for m in city_matches:
            dep_raw, arr_raw = m.group(1), m.group(2)
            dep = _RE_STATION_SUFFIX.sub("", dep_raw).strip()
            arr = _RE_STATION_SUFFIX.sub("", arr_raw).strip()
            if len(dep) >= 2 and len(arr) >= 2:
                result["departure_city_raw"] = dep_raw
                result["arrival_city_raw"] = arr_raw
                break
    flight_match = _RE_FLIGHT_NO.search(remarks)
    if flight_match:
        result["flight_number"] = flight_match.group(1)
        result["transport_type"] = "air"
    if "flight_number" not in result:
        train_match = _RE_TRAIN_NO.search(remarks)
        if train_match:
            result["train_number"] = train_match.group(1)
            result["transport_type"] = "train"
    date_match = _RE_DATE.search(remarks)
    if date_match:
        result["trip_date"] = date_match.group(0)
    time_match = _RE_TIME.search(remarks)
    if time_match:
        result["trip_time"] = time_match.group(1)
    name_match = _RE_PASSENGER_AFTER_NO.search(remarks)
    if name_match:
        result["passenger_name"] = name_match.group(1) or name_match.group(2)
    seat_match = _RE_SEAT_CLASS.search(remarks)
    if seat_match:
        result["seat_class"] = seat_match.group(1)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# JSON 模板抽取结果归一化（将 JSON 中文字段 → 统一英文字段集）
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_json_general_invoice(raw: dict) -> dict:
    """
    归一化 regular_invoice_template 抽取结果。
    利用「出行信息」「出发城市」「到达城市」等字段直接获取差旅信息，
    不再依赖正则解析备注，准确率大幅提升。
    """
    result: dict[str, Any] = {}
    result["invoice_type_detected"] = "general_invoice"
    result["invoice_type"] = "电子发票（普通发票）"
    result["invoice_number"] = raw.get("发票号码")
    result["issue_date"] = raw.get("开票日期")

    buyer = raw.get("购买方信息") or {}
    result["buyer_name"] = buyer.get("名称") if isinstance(buyer, dict) else str(buyer or "") or None

    seller = raw.get("销售方信息") or {}
    result["seller_name"] = seller.get("名称") if isinstance(seller, dict) else str(seller or "") or None

    # 项目信息 → items_description
    items = raw.get("项目信息") or []
    item_names: list[str] = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("项目名称"):
                item_names.append(str(item["项目名称"]))
    result["items_description"] = "，".join(item_names) if item_names else ""

    # 金额
    result["amount"] = _safe_float(raw.get("合计金额"))
    result["tax_amount"] = _safe_float(raw.get("合计税额"))
    price_tax = raw.get("价税合计")
    if isinstance(price_tax, dict):
        result["total_amount"] = _safe_float(price_tax.get("小写"))
    else:
        result["total_amount"] = _safe_float(price_tax)

    # 开票人
    result["issuer"] = raw.get("开票人")

    # ── 出行信息处理 ──────────────────────────────────────────────────────────
    travel_infos = raw.get("出行信息") or []
    first_trip: dict = {}
    if isinstance(travel_infos, list) and travel_infos:
        first_trip = travel_infos[0] if isinstance(travel_infos[0], dict) else {}

    passenger_name = first_trip.get("出行人") if first_trip else None
    trip_dep_raw = first_trip.get("出发地") if first_trip else None
    trip_arr_raw = first_trip.get("到达地") if first_trip else None
    trip_date = first_trip.get("出发日期") if first_trip else None
    seat_class = (first_trip.get("等级") or first_trip.get("交通工具等级")) if first_trip else None

    # 直接城市字段（优先级：直接字段 > 出行信息 > 备注字段）
    dep_raw = (
        raw.get("出发城市")
        or raw.get("备注出发地")
        or trip_dep_raw
    )
    arr_raw = (
        raw.get("到达城市")
        or raw.get("备注目的地")
        or trip_arr_raw
    )

    dep_city = normalize_transport_city(dep_raw) if dep_raw else None
    arr_city = normalize_transport_city(arr_raw) if arr_raw else None

    # 子类别检测
    subcategory = detect_items_subcategory(result["items_description"], "")

    # 如果 NuExtract 直接抽出了城市但子类别未命中，从 items_description 内容再推断
    if subcategory is None and (dep_city or arr_city or first_trip):
        # 有出行信息则推断为交通类
        if first_trip and (trip_dep_raw or trip_arr_raw):
            subcategory = "air"  # 先标记为交通类，后续闭环逻辑会进一步区分
        elif dep_city and arr_city:
            subcategory = "air"

    # 额外兜底：铁路电子客票字样（有时 OCR 成普通发票）
    inv_type_raw = str(raw.get("invoice_type_field") or "")
    if subcategory is None and (
        "铁路电子客票" in inv_type_raw
        or ("铁路" in inv_type_raw and "客票" in inv_type_raw)
    ):
        subcategory = "train"

    result["invoice_subcategory"] = subcategory

    # ── 依据子类别填充交通相关字段 ───────────────────────────────────────────
    if subcategory in ("air", "train") or (dep_city and arr_city and dep_city != arr_city):
        result["departure_city"] = dep_city
        result["arrival_city"] = arr_city
        result["departure_time"] = trip_date or raw.get("备注中的日期") or result["issue_date"]
        result["arrival_time"] = None
        result["is_transport"] = bool(dep_city and arr_city and dep_city != arr_city)
        result["passenger_name"] = passenger_name
        result["seat_class"] = seat_class
        result["flight_number"] = None
        result["train_number"] = None

        # 丰富 items_description
        if dep_raw and arr_raw:
            route_str = f"{dep_raw}→{arr_raw}"
            base_desc = result["items_description"]
            result["items_description"] = f"{base_desc} {route_str}".strip() if base_desc else route_str

    elif subcategory == "hotel":
        result["departure_city"] = dep_city or arr_city
        result["arrival_city"] = result["departure_city"]
        result["departure_time"] = result["issue_date"]
        result["arrival_time"] = None
        result["is_transport"] = False

    else:
        result["departure_city"] = None
        result["arrival_city"] = None
        result["departure_time"] = None
        result["arrival_time"] = None
        result["is_transport"] = False

    result["remarks"] = raw.get("备注") or raw.get("备注中的日期") or ""
    result["extracted_data"] = raw
    return result


def _normalize_json_railway_electronic(raw: dict) -> dict:
    """归一化 railway_electronic_ticket_template 抽取结果。"""
    buyer = raw.get("购买方信息") or {}
    translated = {
        "invoice_number": raw.get("发票号码"),
        "issue_date": raw.get("开票日期"),
        "train_number": raw.get("车次"),
        "departure_station": raw.get("出发站"),
        "arrival_station": raw.get("到达站"),
        "departure_date": raw.get("出发日期"),
        "departure_time": raw.get("出发时间"),
        "seat_class": raw.get("座位等级"),
        "car_seat": raw.get("车厢座位号"),
        "amount": raw.get("票价"),
        "passenger_name": raw.get("乘客姓名"),
        "e_ticket_number": raw.get("电子客票号"),
        "buyer_name": buyer.get("名称") if isinstance(buyer, dict) else None,
    }
    result = _normalize_result(translated, "train_electronic")
    result["extracted_data"] = raw
    return result


def _normalize_json_air_itinerary(raw: dict) -> dict:
    """归一化 air_transportation_electronic_ticket_itinerary 抽取结果。"""
    # 座位等级(CLASS) 可能是列表值（NuExtract 从枚举中选一个）
    seat = raw.get("座位等级(CLASS)")
    if isinstance(seat, list):
        seat = seat[0] if seat else None

    translated = {
        "passenger_name": raw.get("旅客姓名"),
        "flight_number": raw.get("航班号(FLIGHT)"),
        "seat_class": seat,
        "departure_date": raw.get("航班日期(DATE)"),
        "departure_city_airport": raw.get("出发地点(自FROM)"),
        "arrival_city_airport": raw.get("到达地点(至TO)"),
        "total_amount": raw.get("合计(TOTAL)"),
        "fare": raw.get("合计(TOTAL)"),
        "insurance": raw.get("保险费(INSURANCE)"),
        "seller_name": raw.get("填开单位(ISSUED BY)"),
    }
    result = _normalize_result(translated, "air_itinerary")
    result["extracted_data"] = raw
    return result


def _normalize_json_train_physical(raw: dict) -> dict:
    """归一化 train_physical_ticket 抽取结果。"""
    translated = {
        "ticket_number": raw.get("票号"),
        "train_number": raw.get("车次"),
        "departure_station": raw.get("出发站"),
        "arrival_station": raw.get("到达站"),
        "departure_date": raw.get("出发日期"),
        "departure_time": raw.get("出发时间"),
        "seat_class": raw.get("座位等级"),
        "car_seat": raw.get("车厢座位号"),
        "amount": raw.get("票价"),
        "passenger_name": raw.get("乘客姓名"),
    }
    result = _normalize_result(translated, "train_physical")
    result["extracted_data"] = raw
    return result


def _normalize_from_json_templates(raw: dict, inv_type: str) -> dict:
    """根据 inv_type 选择对应的 JSON 模板归一化函数。"""
    if inv_type == "general_invoice":
        return _normalize_json_general_invoice(raw)
    elif inv_type == "train_electronic":
        return _normalize_json_railway_electronic(raw)
    elif inv_type == "air_itinerary":
        return _normalize_json_air_itinerary(raw)
    elif inv_type == "train_physical":
        return _normalize_json_train_physical(raw)
    # 未知类型：回退到原有 Python 模板归一化
    return _normalize_result(raw, inv_type)


# ─────────────────────────────────────────────────────────────────────────────
# 原有 Python 模板归一化（仍供 taxi/ridehailing/hotel 及翻译层使用）
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_result(raw: dict, inv_type: str) -> dict:
    """
    根据发票类型对 NuExtract 原始结果进行归一化，统一输出字段集合。
    """
    result: dict[str, Any] = {}
    result["invoice_type_detected"] = inv_type

    if inv_type in ("train_physical", "train_electronic"):
        result["invoice_type"] = raw.get("invoice_type") or (
            "铁路电子客票" if inv_type == "train_electronic" else "火车票"
        )
        result["invoice_number"] = raw.get("invoice_number") or raw.get("ticket_number")
        result["issue_date"] = raw.get("issue_date") or raw.get("departure_date")
        result["seller_name"] = "中国铁路"
        result["buyer_name"] = raw.get("buyer_name")
        result["items_description"] = (
            f"{raw.get('train_number', '')} "
            f"{raw.get('departure_station', '')}→{raw.get('arrival_station', '')} "
            f"{raw.get('seat_class', '')} {raw.get('car_seat', '')}"
        ).strip()
        result["amount"] = _safe_float(raw.get("amount"))
        result["tax_amount"] = None
        result["total_amount"] = result["amount"]
        result["departure_city"] = normalize_transport_city(raw.get("departure_station"))
        result["arrival_city"] = normalize_transport_city(raw.get("arrival_station"))
        result["departure_time"] = _merge_datetime(
            raw.get("departure_date"), raw.get("departure_time")
        )
        result["arrival_time"] = None
        result["is_transport"] = True
        result["passenger_name"] = raw.get("passenger_name")
        result["train_number"] = raw.get("train_number")

    elif inv_type in ("air_itinerary", "air_electronic"):
        result["invoice_type"] = raw.get("invoice_type") or "航空运输电子客票行程单"
        result["invoice_number"] = raw.get("invoice_number")
        result["issue_date"] = raw.get("issue_date") or raw.get("departure_date")
        result["seller_name"] = raw.get("seller_name")
        result["buyer_name"] = raw.get("buyer_name")
        result["items_description"] = (
            f"{raw.get('carrier', '')} {raw.get('flight_number', '')} "
            f"{raw.get('departure_city_airport', '')}→{raw.get('arrival_city_airport', '')}"
        ).strip()
        result["amount"] = _safe_float(raw.get("fare") or raw.get("amount"))
        result["tax_amount"] = _safe_float(raw.get("tax_amount"))
        result["total_amount"] = _safe_float(
            raw.get("total_amount") or raw.get("fare")
        )
        result["departure_city"] = normalize_transport_city(
            raw.get("departure_city_airport") or raw.get("departure_city")
        )
        result["arrival_city"] = normalize_transport_city(
            raw.get("arrival_city_airport") or raw.get("arrival_city")
        )
        result["departure_time"] = _merge_datetime(
            raw.get("departure_date"), raw.get("departure_time")
        )
        result["arrival_time"] = None
        result["is_transport"] = True
        result["passenger_name"] = raw.get("passenger_name")
        result["flight_number"] = raw.get("flight_number")

    elif inv_type == "taxi":
        result["invoice_type"] = raw.get("invoice_type") or "出租车发票"
        result["invoice_number"] = raw.get("invoice_number")
        result["issue_date"] = raw.get("issue_date")
        result["seller_name"] = raw.get("seller_name")
        result["buyer_name"] = raw.get("buyer_name")
        result["items_description"] = (
            f"出租车 里程{raw.get('distance_km', '')}km"
            if raw.get("distance_km") else "出租车费"
        )
        result["amount"] = _safe_float(raw.get("amount"))
        result["tax_amount"] = None
        result["total_amount"] = result["amount"]
        city = normalize_transport_city(raw.get("city")) if raw.get("city") else None
        result["departure_city"] = city
        result["arrival_city"] = city
        result["departure_time"] = raw.get("boarding_time")
        result["arrival_time"] = raw.get("alighting_time")
        result["is_transport"] = False

    elif inv_type == "ridehailing":
        result["invoice_type"] = raw.get("invoice_type") or "网约车发票"
        result["invoice_number"] = raw.get("invoice_number")
        result["issue_date"] = raw.get("issue_date") or raw.get("trip_date")
        result["seller_name"] = raw.get("seller_name")
        result["buyer_name"] = raw.get("buyer_name")
        result["items_description"] = (
            f"网约车 {raw.get('boarding_address', '')}→{raw.get('alighting_address', '')}"
        ).strip()
        result["amount"] = _safe_float(raw.get("amount"))
        result["tax_amount"] = _safe_float(raw.get("tax_amount"))
        result["total_amount"] = _safe_float(raw.get("total_amount")) or result["amount"]
        city = normalize_transport_city(raw.get("departure_city"))
        result["departure_city"] = city
        result["arrival_city"] = city
        result["departure_time"] = raw.get("trip_date")
        result["arrival_time"] = None
        result["is_transport"] = False

    elif inv_type == "hotel":
        result["invoice_type"] = raw.get("invoice_type") or "住宿发票"
        result["invoice_number"] = raw.get("invoice_number")
        result["issue_date"] = raw.get("issue_date") or raw.get("check_out_date")
        result["seller_name"] = raw.get("seller_name")
        result["buyer_name"] = raw.get("buyer_name")
        result["items_description"] = (
            f"住宿 {raw.get('check_in_date', '')}~{raw.get('check_out_date', '')} "
            f"{raw.get('room_type', '')} {raw.get('nights', '')}晚"
        ).strip()
        result["amount"] = _safe_float(raw.get("amount"))
        result["tax_amount"] = _safe_float(raw.get("tax_amount"))
        result["total_amount"] = _safe_float(raw.get("total_amount")) or result["amount"]
        city = normalize_transport_city(raw.get("hotel_city"))
        result["departure_city"] = city
        result["arrival_city"] = city
        result["departure_time"] = raw.get("check_in_date")
        result["arrival_time"] = raw.get("check_out_date")
        result["is_transport"] = False

    elif inv_type == "general_invoice":
        items_desc = raw.get("items_description") or ""
        remarks = raw.get("remarks") or ""
        subcategory = detect_items_subcategory(items_desc, remarks)
        inv_type_raw = (raw.get("invoice_type") or "").strip()
        combined_text = f"{items_desc} {remarks} {inv_type_raw}"
        if subcategory is None and (
            "铁路电子客票" in inv_type_raw
            or ("铁路" in inv_type_raw and "客票" in inv_type_raw)
        ):
            subcategory = "train"
        if subcategory is None and re.search(
            r"(报销凭证|仅供报销使用|报销凭证\s*遗失不补)", combined_text
        ):
            subcategory = "train"

        result["invoice_type"] = raw.get("invoice_type") or "电子发票（普通发票）"
        result["invoice_number"] = raw.get("invoice_number")
        result["issue_date"] = raw.get("issue_date")
        result["seller_name"] = raw.get("seller_name")
        result["buyer_name"] = raw.get("buyer_name")
        result["items_description"] = items_desc
        result["remarks"] = remarks
        result["amount"] = _safe_float(raw.get("amount"))
        result["tax_amount"] = _safe_float(raw.get("tax_amount"))
        result["total_amount"] = _safe_float(raw.get("total_amount"))
        result["invoice_subcategory"] = subcategory

        if subcategory in ("air", "train"):
            trip = parse_transport_from_remarks(remarks)
            dep_raw = trip.get("departure_city_raw", "")
            arr_raw = trip.get("arrival_city_raw", "")
            result["departure_city"] = normalize_transport_city(dep_raw) if dep_raw else None
            result["arrival_city"] = normalize_transport_city(arr_raw) if arr_raw else None
            trip_date = trip.get("trip_date")
            trip_time = trip.get("trip_time")
            result["departure_time"] = _merge_datetime(trip_date, trip_time) or result["issue_date"]
            result["arrival_time"] = None
            result["is_transport"] = bool(result["departure_city"] and result["arrival_city"])
            result["passenger_name"] = trip.get("passenger_name")
            result["flight_number"] = trip.get("flight_number")
            result["train_number"] = trip.get("train_number")
            result["seat_class"] = trip.get("seat_class")
            no_str = trip.get("flight_number") or trip.get("train_number") or ""
            route = f"{dep_raw}→{arr_raw}" if dep_raw and arr_raw else ""
            result["items_description"] = f"{items_desc} {route} {no_str}".strip()
        elif subcategory == "hotel":
            city_hint = ""
            for m in _RE_CITY_PAIR.finditer(remarks + " " + items_desc):
                city_hint = m.group(1)
                break
            result["departure_city"] = normalize_transport_city(city_hint) if city_hint else None
            result["arrival_city"] = result["departure_city"]
            result["departure_time"] = raw.get("issue_date")
            result["arrival_time"] = None
            result["is_transport"] = False
        else:
            result["departure_city"] = None
            result["arrival_city"] = None
            result["departure_time"] = None
            result["arrival_time"] = None
            result["is_transport"] = False

    else:
        # general fallback
        result["invoice_type"] = raw.get("invoice_type")
        result["invoice_number"] = raw.get("invoice_number")
        result["issue_date"] = raw.get("issue_date")
        result["seller_name"] = raw.get("seller_name")
        result["buyer_name"] = raw.get("buyer_name")
        result["items_description"] = raw.get("items_description")
        result["remarks"] = raw.get("remarks")
        result["amount"] = _safe_float(raw.get("amount"))
        result["tax_amount"] = _safe_float(raw.get("tax_amount"))
        result["total_amount"] = _safe_float(raw.get("total_amount"))
        result["departure_city"] = normalize_transport_city(raw.get("departure_city"))
        result["arrival_city"] = normalize_transport_city(raw.get("arrival_city"))
        result["departure_time"] = raw.get("departure_time")
        result["arrival_time"] = raw.get("arrival_time")
        items_desc = raw.get("items_description") or ""
        remarks = raw.get("remarks") or ""
        result["invoice_subcategory"] = detect_items_subcategory(items_desc, remarks)
        result["is_transport"] = _is_intercity_transport(result)

    result["extracted_data"] = raw
    return result


def _is_intercity_transport(data: dict) -> bool:
    inv_type = (data.get("invoice_type") or "").lower()
    desc = (data.get("items_description") or "").lower()
    combined = inv_type + " " + desc
    dep = (data.get("departure_city") or "").strip()
    arr = (data.get("arrival_city") or "").strip()
    keywords = [
        "机票", "行程单", "火车票", "高铁", "动车", "船票", "轮船",
        "航空", "铁路", "汽车票", "长途客运",
        "报销凭证", "仅供报销使用",
    ]
    if any(kw in combined for kw in keywords):
        return True
    if dep and arr and normalize_transport_city(dep) != normalize_transport_city(arr):
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# 主抽取器
# ─────────────────────────────────────────────────────────────────────────────

class InvoiceExtractor:
    def __init__(self):
        cfg = load_nuextract_config()
        self.host = cfg.get("host", "localhost")
        self.port = cfg.get("port", 8000)
        self.timeout = cfg.get("timeout", 60)
        self.extraction_cfg = load_extraction_config()

    # ── 双阶段 NuExtract（路径）─────────────────────────────────────────────

    async def _nuextract_extract_from_path(
        self, file_path: str, filename_for_detect: str
    ) -> dict:
        """
        双阶段 NuExtract 抽取（文件路径版本）：
          Step 1：用 invoice_type 列表作为模板，判别发票类型
          Step 2：按判别结果选对应 JSON 模板进行精准抽取
        """
        # 文件名快速检测（用于 taxi/ridehailing/hotel 的直接分路）
        hinted_type = detect_invoice_type(filename_for_detect)

        async with NuExtractClient(self.host, self.port, self.timeout) as client:
            try:
                # taxi / ridehailing / hotel 不在 JSON 模板覆盖范围，走原有 Python 模板
                if hinted_type in _PYTHON_ONLY_TYPES:
                    template = TEMPLATES[hinted_type]
                    raw = self._pick_first_result(
                        await client.extract_from_files(
                            file_paths=[file_path], template=template
                        )
                    )
                    return _normalize_result(raw, hinted_type)

                json_tpls = load_json_templates()
                type_list = json_tpls.get("invoice_type", list(_JSON_TYPE_TO_KEY.keys()))

                # ── Step 1: 分类 ─────────────────────────────────────────────
                cls_raw = self._pick_first_result(
                    await client.extract_from_files(
                        file_paths=[file_path],
                        template={"invoice_type": type_list},
                    )
                )
                inv_type = _parse_classified_type(cls_raw, filename_for_detect)
                print(f"[InvoiceExtractor] 分类结果 {filename_for_detect!r} → {inv_type}")

                # ── Step 2: 精准抽取 ─────────────────────────────────────────
                tpl_name = _KEY_TO_JSON_TEMPLATE_NAME.get(inv_type)
                if tpl_name and tpl_name in json_tpls:
                    ext_raw = self._pick_first_result(
                        await client.extract_from_files(
                            file_paths=[file_path],
                            template=json_tpls[tpl_name],
                        )
                    )
                    return _normalize_from_json_templates(ext_raw, inv_type)

                # 回退：使用 Python 模板
                py_template = TEMPLATES.get(inv_type, TEMPLATE_GENERAL)
                raw = self._pick_first_result(
                    await client.extract_from_files(
                        file_paths=[file_path], template=py_template
                    )
                )
                return _normalize_result(raw, inv_type)

            except Exception as e:
                print(f"[InvoiceExtractor] NuExtract path error ({filename_for_detect}): {e}")
                return {"error": str(e), "is_transport": False}

    # ── 双阶段 NuExtract（base64）──────────────────────────────────────────

    async def _nuextract_extract_from_base64(self, b64_data: str, filename: str) -> dict:
        """
        双阶段 NuExtract 抽取（base64 版本）：
          Step 1：分类；Step 2：精准抽取
        """
        hinted_type = detect_invoice_type(filename)

        async with NuExtractClient(self.host, self.port, self.timeout) as client:
            try:
                if hinted_type in _PYTHON_ONLY_TYPES:
                    template = TEMPLATES[hinted_type]
                    raw = self._pick_first_result(
                        await client.extract_from_base64(
                            files_data=[b64_data],
                            storage_filenames=[filename],
                            template=template,
                        )
                    )
                    return _normalize_result(raw, hinted_type)

                json_tpls = load_json_templates()
                type_list = json_tpls.get("invoice_type", list(_JSON_TYPE_TO_KEY.keys()))

                # Step 1: 分类
                cls_raw = self._pick_first_result(
                    await client.extract_from_base64(
                        files_data=[b64_data],
                        storage_filenames=[filename],
                        template={"invoice_type": type_list},
                    )
                )
                inv_type = _parse_classified_type(cls_raw, filename)
                print(f"[InvoiceExtractor] 分类结果 {filename!r} → {inv_type}")

                # Step 2: 精准抽取
                tpl_name = _KEY_TO_JSON_TEMPLATE_NAME.get(inv_type)
                if tpl_name and tpl_name in json_tpls:
                    ext_raw = self._pick_first_result(
                        await client.extract_from_base64(
                            files_data=[b64_data],
                            storage_filenames=[filename],
                            template=json_tpls[tpl_name],
                        )
                    )
                    return _normalize_from_json_templates(ext_raw, inv_type)

                py_template = TEMPLATES.get(inv_type, TEMPLATE_GENERAL)
                raw = self._pick_first_result(
                    await client.extract_from_base64(
                        files_data=[b64_data],
                        storage_filenames=[filename],
                        template=py_template,
                    )
                )
                return _normalize_result(raw, inv_type)

            except Exception as e:
                print(f"[InvoiceExtractor] NuExtract base64 error ({filename}): {e}")
                return {"error": str(e), "is_transport": False}

    # ── 公开接口 ───────────────────────────────────────────────────────────

    async def extract_from_file(self, file_path: str, filename_hint: Optional[str] = None) -> dict:
        """按 extraction.provider 选择 NuExtract、本地 OCR 保底或自动降级。"""
        from app.services.fallback_extract import extract_invoice_local_fallback

        filename_for_detect = filename_hint or os.path.basename(file_path)
        provider = (self.extraction_cfg.get("provider") or "auto").strip().lower()
        ocr_cfg = self.extraction_cfg.get("ocr") or {}

        want_nx = provider in ("nuextract", "auto")
        want_fb = provider in ("ocr_fallback", "auto")

        nx_error: Optional[str] = None
        if want_nx:
            nx_res = await self._nuextract_extract_from_path(file_path, filename_for_detect)
            if not nx_res.get("error"):
                nx_res["extract_method"] = "nuextract"
                return nx_res
            nx_error = nx_res.get("error")

        if want_fb:
            fb = extract_invoice_local_fallback(file_path, filename_for_detect, ocr_cfg)
            if nx_error:
                fb["nuextract_error"] = nx_error
            if not fb.get("error"):
                fb["extract_method"] = "ocr_fallback"
            return fb

        err = nx_error or "NuExtract 不可用且未启用本地 OCR（extraction.provider=nuextract）"
        return {"error": err, "is_transport": False}

    async def extract_from_base64(self, b64_data: str, filename: str) -> dict:
        """从 base64 编码的发票数据中抽取结构化信息。"""
        from app.services.fallback_extract import extract_invoice_local_fallback_from_base64

        provider = (self.extraction_cfg.get("provider") or "auto").strip().lower()
        ocr_cfg = self.extraction_cfg.get("ocr") or {}

        want_nx = provider in ("nuextract", "auto")
        want_fb = provider in ("ocr_fallback", "auto")

        nx_error: Optional[str] = None
        if want_nx:
            nx_res = await self._nuextract_extract_from_base64(b64_data, filename)
            if not nx_res.get("error"):
                nx_res["extract_method"] = "nuextract"
                return nx_res
            nx_error = nx_res.get("error")

        if want_fb:
            fb = extract_invoice_local_fallback_from_base64(b64_data, filename, ocr_cfg)
            if nx_error:
                fb["nuextract_error"] = nx_error
            if not fb.get("error"):
                fb["extract_method"] = "ocr_fallback"
            return fb

        err = nx_error or "NuExtract 不可用且未启用本地 OCR（extraction.provider=nuextract）"
        return {"error": err, "is_transport": False}

    @staticmethod
    def _pick_first_result(raw_result: Any) -> dict:
        return _extract_nuextract_payload(raw_result)


_extractor: Optional[InvoiceExtractor] = None


def get_extractor() -> InvoiceExtractor:
    global _extractor
    if _extractor is None:
        _extractor = InvoiceExtractor()
    return _extractor


def reset_extractor() -> None:
    global _extractor
    _extractor = None

"""
发票信息抽取模块

根据发票类型选择对应的 NuExtract 模板，提升字段抽取精准度。
支持的发票类型：
  - train_physical     纸质火车/高铁/动车票（卡片式）
  - train_electronic   铁路电子客票（增值税电子发票格式）
  - air_itinerary      航空运输电子客票行程单（纸质/扫描）
  - air_electronic     航空电子发票
  - taxi               出租车发票（纸质小票）
  - ridehailing        网约车发票（滴滴/曹操等，通常为 PDF）
  - hotel              住宿/酒店发票
  - general_invoice    电子发票（普通发票）——需进一步解析项目名称和备注
  - general            通用兜底模板

电子发票（普通发票）处理策略：
  1. 用专用模板抽取：项目名称（items_description）+ 备注（remarks）
  2. 分析项目名称判断费用子类型（代订机票/代订火车票/住宿/会议等）
  3. 对交通类子类型，用正则从备注中解析：城市对、日期、航班/车次号
  4. 设置 is_transport 并填充 departure_city / arrival_city / departure_time
"""

from __future__ import annotations

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

# ─────────────────────────────────────────────────────────────────────────────
# NuExtract 抽取模板定义
# ─────────────────────────────────────────────────────────────────────────────

# 纸质火车/高铁/动车票（卡片式，如 E089640 样式）
TEMPLATE_TRAIN_PHYSICAL = {
    "invoice_type": "verbatim-string",        # 如 "火车票" / "高铁票"
    "ticket_number": "verbatim-string",       # 票号/检票号，如 E089640
    "train_number": "verbatim-string",        # 车次，如 D2906 / G261 / K101
    "departure_station": "verbatim-string",   # 出发站，如 上海站
    "arrival_station": "verbatim-string",     # 到达站，如 青岛北站
    "departure_date": "verbatim-string",      # 出发日期，如 2024年08月22日
    "departure_time": "verbatim-string",      # 出发时间，如 08:41
    "seat_class": "verbatim-string",          # 座位等级，如 二等座
    "car_seat": "verbatim-string",            # 车厢座位，如 02车05A号
    "amount": "verbatim-string",              # 票价，如 330.0
    "passenger_name": "verbatim-string",      # 乘客姓名
    "id_number_partial": "verbatim-string",   # 身份证号（可能脱敏）
}

# 铁路电子客票（增值税电子发票格式）
TEMPLATE_TRAIN_ELECTRONIC = {
    "invoice_type": "verbatim-string",        # 电子发票（铁路电子客票）
    "invoice_number": "verbatim-string",      # 发票号码
    "issue_date": "verbatim-string",          # 开票日期
    "train_number": "verbatim-string",        # 车次
    "departure_station": "verbatim-string",   # 出发站
    "arrival_station": "verbatim-string",     # 到达站
    "departure_date": "verbatim-string",      # 出发日期
    "departure_time": "verbatim-string",      # 出发时间
    "seat_class": "verbatim-string",          # 座位等级
    "car_seat": "verbatim-string",            # 车厢座位号
    "amount": "verbatim-string",              # 票价
    "buyer_name": "verbatim-string",          # 购买方名称
    "passenger_name": "verbatim-string",      # 乘客姓名
    "e_ticket_number": "verbatim-string",     # 电子客票号
}

# 航空运输电子客票行程单（纸质行程单/扫描件）
TEMPLATE_AIR_ITINERARY = {
    "invoice_type": "verbatim-string",        # 航空运输电子客票行程单
    "passenger_name": "verbatim-string",      # 旅客姓名
    "carrier": "verbatim-string",             # 承运人，如 东航/国航
    "flight_number": "verbatim-string",       # 航班号，如 MU5661
    "departure_city_airport": "verbatim-string",  # 出发地，如 福州-长乐
    "arrival_city_airport": "verbatim-string",    # 到达地，如 上海-浦东
    "departure_date": "verbatim-string",      # 出发日期
    "departure_time": "verbatim-string",      # 出发时间
    "fare": "verbatim-string",                # 票价
    "fuel_surcharge": "verbatim-string",      # 燃油附加费
    "tax": "verbatim-string",                 # 其他税费
    "total_amount": "verbatim-string",        # 合计金额
    "insurance": "verbatim-string",           # 保险费
    "issue_date": "verbatim-string",          # 填开日期
    "seller_name": "verbatim-string",         # 填开单位
}

# 航空电子发票（增值税电子发票 + 航空信息）
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

# 出租车发票（纸质小票）
TEMPLATE_TAXI = {
    "invoice_type": "verbatim-string",        # 车费发票 / 出租车发票
    "invoice_code": "verbatim-string",        # 发票代码
    "invoice_number": "verbatim-string",      # 发票号码
    "seller_name": "verbatim-string",         # 出租车公司名称
    "issue_date": "verbatim-string",          # 日期
    "boarding_time": "verbatim-string",       # 上车时间
    "alighting_time": "verbatim-string",      # 下车时间
    "distance_km": "verbatim-string",         # 里程（公里）
    "amount": "verbatim-string",              # 金额
    "city": "verbatim-string",                # 所在城市（从公司名或印章推断）
}

# 网约车发票（滴滴/曹操/高德等 PDF 电子发票）
TEMPLATE_RIDEHAILING = {
    "invoice_type": "verbatim-string",        # 电子发票/网约车发票
    "invoice_number": "verbatim-string",      # 发票号码
    "issue_date": "verbatim-string",          # 开票日期
    "seller_name": "verbatim-string",         # 开票方，如 滴滴出行
    "buyer_name": "verbatim-string",          # 购买方名称
    "trip_date": "verbatim-string",           # 行程日期
    "boarding_address": "verbatim-string",    # 上车地点/起点
    "alighting_address": "verbatim-string",   # 下车地点/终点
    "departure_city": "verbatim-string",      # 出发城市
    "amount": "verbatim-string",              # 金额
    "tax_amount": "verbatim-string",          # 税额
    "total_amount": "verbatim-string",        # 价税合计
}

# 住宿/酒店发票
TEMPLATE_HOTEL = {
    "invoice_type": "verbatim-string",
    "invoice_number": "verbatim-string",
    "issue_date": "verbatim-string",
    "seller_name": "verbatim-string",
    "buyer_name": "verbatim-string",
    "check_in_date": "verbatim-string",       # 入住日期
    "check_out_date": "verbatim-string",      # 离店日期
    "nights": "verbatim-string",              # 住宿天数
    "room_type": "verbatim-string",           # 房型
    "amount": "verbatim-string",
    "tax_amount": "verbatim-string",
    "total_amount": "verbatim-string",
    "hotel_city": "verbatim-string",          # 酒店所在城市
}

# 电子发票（普通发票）专用模板
# 重点捕获：项目名称 + 备注，这两个字段是判断费用类型和提取交通信息的关键
TEMPLATE_GENERAL_INVOICE = {
    "invoice_type": "verbatim-string",    # 电子发票（普通发票）
    "invoice_number": "verbatim-string",  # 发票号码
    "issue_date": "verbatim-string",      # 开票日期
    "seller_name": "verbatim-string",     # 销售方名称
    "buyer_name": "verbatim-string",      # 购买方名称
    "items_description": "verbatim-string",  # 所有项目名称合并，如 *经纪代理服务*代订机票产品
    "amount": "verbatim-string",          # 合计金额（不含税）
    "tax_amount": "verbatim-string",      # 税额
    "total_amount": "verbatim-string",    # 价税合计
    "remarks": "verbatim-string",         # 备注，如 携程订单:...,2025/3/3 上海-深圳 ZH9502 甘开宇 经济舱
}

# 通用兜底模板（含备注字段）
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

# 类型 → 模板 映射
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

# 用于判断是否为城市间交通票（参与闭环检测）
INTERCITY_TRANSPORT_TYPES = {
    "train_physical", "train_electronic",
    "air_itinerary", "air_electronic",
}

# ─────────────────────────────────────────────────────────────────────────────
# 发票类型检测
# ─────────────────────────────────────────────────────────────────────────────

# 文件名关键词 → 类型（高置信度，直接决定专用模板）
_FILENAME_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(铁路电子客票|电子客票|railway.*e-?ticket)", re.I), "train_electronic"),
    (re.compile(r"(railway[_\-]?invoice|railway)", re.I), "train_electronic"),
    (re.compile(r"(动车票|高铁票|火车票|train\s*ticket)", re.I), "train_physical"),
    (re.compile(r"(行程单|itinerary|航空运输电子客票行程单)", re.I), "air_itinerary"),
    (re.compile(r"(滴滴|曹操出行|高德打车|嘀嗒|神州专车|网约车|ride\s*hailing)", re.I), "ridehailing"),
    (re.compile(r"(出租车发票|出租发票|taxi.*fare|车费发票)", re.I), "taxi"),
    (re.compile(r"(电子发票.*普通|普通.*电子发票)", re.I), "general_invoice"),
]

# 内容字段关键词 → 类型（用于从 NuExtract 初步结果中二次判断）
_CONTENT_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"电子发票[（(]铁路电子客票"), "train_electronic"),
    (re.compile(r"(动车组|高速铁路|普速铁路|铁路客票)"), "train_physical"),
    (re.compile(r"航空运输电子客票行程单"), "air_itinerary"),
    (re.compile(r"(滴滴出行|曹操出行|高德打车|哈啰出行|T3出行|享道出行)"), "ridehailing"),
    (re.compile(r"(出租汽车公司|出租车|TAXI|车费发票)"), "taxi"),
    (re.compile(r"电子发票[（(]普通发票"), "general_invoice"),
]

# ── 电子发票（普通发票）项目名称 → 费用子类型 ──────────────────────────────────

# 每条规则：(pattern, subcategory)
# subcategory: "air" | "train" | "hotel" | "taxi_local" | "ridehailing_local"
#              | "meeting" | "material" | None（不确定）
_ITEMS_SUBCATEGORY_RULES: list[tuple[re.Pattern, str]] = [
    # 机票/航班相关
    (re.compile(r"(代订机票|代办机票|机票服务|机票费|订机票|购机票|航空服务|飞机票|代购机票|机场服务费)"), "air"),
    # 火车/高铁/动车相关
    (re.compile(r"(代订火车票|代订高铁票|代订动车票|铁路客票|火车票服务|高铁服务|动车服务|代购火车票)"), "train"),
    # 住宿相关
    (re.compile(r"(代订酒店|代订客房|住宿费|酒店服务|客房费|住宿服务|宾馆|旅馆|代订住宿)"), "hotel"),
    # 出租车/本地交通
    (re.compile(r"(出租车费|出租车服务|打车费|用车服务|城市交通费)"), "taxi_local"),
    # 网约车
    (re.compile(r"(网约车|约车服务|专车服务|顺风车|客运服务费|运输服务\*?客运服务费)"), "ridehailing_local"),
    # 会议相关
    (re.compile(r"(会议服务|会务费|会议室|场地费|培训服务|研讨会|会议餐|会务服务)"), "meeting"),
    # 材料/办公用品
    (re.compile(r"(办公用品|耗材|文具|打印|原材料|配件|零件|物料)"), "material"),
]


def detect_invoice_type(filename: str, content_hint: str = "") -> str:
    """
    根据文件名和内容提示检测发票类型。
    返回类型字符串（对应 TEMPLATES 的 key）。
    """
    for pattern, inv_type in _FILENAME_RULES:
        if pattern.search(filename):
            return inv_type
    for pattern, inv_type in _CONTENT_RULES:
        if pattern.search(content_hint):
            return inv_type
    return "general"


def detect_items_subcategory(items_description: str, remarks: str = "") -> Optional[str]:
    """
    分析电子发票（普通发票）的项目名称和备注，判断费用子类型。
    返回值：'air' | 'train' | 'hotel' | 'taxi_local' | 'ridehailing_local'
            | 'meeting' | 'material' | None
    """
    text = (items_description or "") + " " + (remarks or "")
    for pattern, subcategory in _ITEMS_SUBCATEGORY_RULES:
        if pattern.search(text):
            return subcategory
    return None


def _extract_nuextract_payload(raw: Any) -> dict:
    """
    兼容 NuExtract 的多种响应结构，提取真正的字段字典。
    常见格式：
      - {"code":200, "data":{"result": {...}}}
      - {"result": {...}}
      - {"results":[{...}]}
      - [{ "result": {...} }]
    """
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

def load_nuextract_config() -> dict:
    with open(MODELS_CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config.get("nuextract", {})


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
    """将分开的日期和时间字段合并为一个字符串"""
    if date_str and time_str:
        return f"{date_str.strip()} {time_str.strip()}"
    return date_str or time_str


# ─── 备注字段解析（用于电子发票普通发票中的交通信息提取）──────────────────────

# 城市对：支持 "上海-深圳" / "上海→深圳" / "上海—深圳" / "上海 深圳"（前后有数字/空格边界）
_RE_CITY_PAIR = re.compile(
    r"([\u4e00-\u9fa5]{2,6})[－\-—→至到\s]+([\u4e00-\u9fa5]{2,6})"
)
# 机场/车站修饰词（需去掉后才是城市名）
_RE_STATION_SUFFIX = re.compile(r"(站|机场|国际机场|国内机场|港|北站|南站|东站|西站|虹桥|浦东|北京首都|北京大兴)$")

# 航班号：2个大写字母 + 3-4位数字，如 ZH9502 / CA1234 / MU5661
_RE_FLIGHT_NO = re.compile(r"\b([A-Z]{2}\d{3,4})\b")
# 列车号：G/D/C/K/T/Z + 2-5位数字，如 G261 / D2906 / K101
_RE_TRAIN_NO = re.compile(r"\b([GDCKTZgdcktz]\d{2,5})\b")
# 日期：多种格式
_RE_DATE = re.compile(
    r"(\d{4}[年/\-\.]\d{1,2}[月/\-\.]\d{1,2}日?)"
    r"|(\d{4}/\d{1,2}/\d{1,2})"
    r"|(\d{4}-\d{2}-\d{2})"
)
# 时间：HH:MM
_RE_TIME = re.compile(r"\b(\d{1,2}:\d{2})\b")
# 乘客姓名（紧跟航班/列车号后的2-4个汉字）
_RE_PASSENGER_AFTER_NO = re.compile(
    r"[A-Z]{2}\d{3,4}\s+([\u4e00-\u9fa5]{2,4})"
    r"|[GDCKTZgdcktz]\d{2,5}\s+([\u4e00-\u9fa5]{2,4})"
)
# 座位等级
_RE_SEAT_CLASS = re.compile(r"(经济舱|商务舱|头等舱|公务舱|一等座|二等座|商务座|特等座|软卧|硬卧|软座|硬座)")


def parse_transport_from_remarks(remarks: str) -> dict:
    """
    从电子发票备注字段中解析交通行程信息。

    能处理的备注格式举例：
      - 携程订单:1129237126803726,2025/3/3 上海-深圳 ZH9502 甘开宇 经济舱
      - 订单号:XXXX 出行日期:2025-03-05 行程:北京-成都 CA4187 李明
      - 上海虹桥-北京南 G7 2025年03月05日 10:54 一等座
      - 2025/6/2 福州-上海 MU5661 19:25
    """
    if not remarks:
        return {}

    result: dict[str, Any] = {}

    # 1. 提取城市对
    city_matches = list(_RE_CITY_PAIR.finditer(remarks))
    if city_matches:
        # 取第一个有效城市对（过滤掉太短的噪声）
        for m in city_matches:
            dep_raw, arr_raw = m.group(1), m.group(2)
            dep = _RE_STATION_SUFFIX.sub("", dep_raw).strip()
            arr = _RE_STATION_SUFFIX.sub("", arr_raw).strip()
            # 过滤明显不是城市名的匹配（如 "经济舱" 这种2字词在其他上下文）
            if len(dep) >= 2 and len(arr) >= 2:
                result["departure_city_raw"] = dep_raw
                result["arrival_city_raw"] = arr_raw
                break

    # 2. 提取航班号
    flight_match = _RE_FLIGHT_NO.search(remarks)
    if flight_match:
        result["flight_number"] = flight_match.group(1)
        result["transport_type"] = "air"

    # 3. 提取列车号（仅当没有航班号时）
    if "flight_number" not in result:
        train_match = _RE_TRAIN_NO.search(remarks)
        if train_match:
            result["train_number"] = train_match.group(1)
            result["transport_type"] = "train"

    # 4. 提取日期
    date_match = _RE_DATE.search(remarks)
    if date_match:
        result["trip_date"] = date_match.group(0)

    # 5. 提取时间
    time_match = _RE_TIME.search(remarks)
    if time_match:
        result["trip_time"] = time_match.group(1)

    # 6. 提取乘客姓名
    name_match = _RE_PASSENGER_AFTER_NO.search(remarks)
    if name_match:
        result["passenger_name"] = name_match.group(1) or name_match.group(2)

    # 7. 提取座位等级
    seat_match = _RE_SEAT_CLASS.search(remarks)
    if seat_match:
        result["seat_class"] = seat_match.group(1)

    return result


def _normalize_result(raw: dict, inv_type: str) -> dict:
    """
    根据发票类型对 NuExtract 原始结果进行归一化，
    统一输出到通用字段集合（与数据库模型对齐）。
    同时利用 station_city_map 将站点名归一到城市名。
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
            f"{raw.get('train_number','')} "
            f"{raw.get('departure_station','')}→{raw.get('arrival_station','')} "
            f"{raw.get('seat_class','')} {raw.get('car_seat','')}"
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
            f"{raw.get('carrier','')} {raw.get('flight_number','')} "
            f"{raw.get('departure_city_airport','')}→{raw.get('arrival_city_airport','')}"
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
            f"出租车 里程{raw.get('distance_km','')}km"
            if raw.get("distance_km") else "出租车费"
        )
        result["amount"] = _safe_float(raw.get("amount"))
        result["tax_amount"] = None
        result["total_amount"] = result["amount"]
        # 出租车为城市内交通，不参与城市间闭环
        city = normalize_transport_city(raw.get("city")) if raw.get("city") else None
        result["departure_city"] = city
        result["arrival_city"] = city
        result["departure_time"] = raw.get("boarding_time")
        result["arrival_time"] = raw.get("alighting_time")
        result["is_transport"] = False  # 不是城市间交通，不参与闭环

    elif inv_type == "ridehailing":
        result["invoice_type"] = raw.get("invoice_type") or "网约车发票"
        result["invoice_number"] = raw.get("invoice_number")
        result["issue_date"] = raw.get("issue_date") or raw.get("trip_date")
        result["seller_name"] = raw.get("seller_name")
        result["buyer_name"] = raw.get("buyer_name")
        result["items_description"] = (
            f"网约车 {raw.get('boarding_address','')}→{raw.get('alighting_address','')}"
        ).strip()
        result["amount"] = _safe_float(raw.get("amount"))
        result["tax_amount"] = _safe_float(raw.get("tax_amount"))
        result["total_amount"] = _safe_float(raw.get("total_amount")) or result["amount"]
        # 网约车为城市内交通，不参与城市间闭环
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
            f"住宿 {raw.get('check_in_date','')}~{raw.get('check_out_date','')} "
            f"{raw.get('room_type','')} {raw.get('nights','')}晚"
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
        # 电子发票（普通发票）：通过项目名称+备注判断费用子类型
        items_desc = raw.get("items_description") or ""
        remarks = raw.get("remarks") or ""
        subcategory = detect_items_subcategory(items_desc, remarks)

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
        result["invoice_subcategory"] = subcategory  # 供分类器使用

        if subcategory in ("air", "train"):
            # 尝试从备注解析行程信息
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
            # 构建更丰富的描述
            no_str = trip.get("flight_number") or trip.get("train_number") or ""
            route = f"{dep_raw}→{arr_raw}" if dep_raw and arr_raw else ""
            result["items_description"] = f"{items_desc} {route} {no_str}".strip()

        elif subcategory == "hotel":
            # 从备注或项目尝试提取入住城市
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
            # 其他子类型（会议/材料/出租/网约/未知）
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
        # 通用时根据字段内容判断是否为城市间交通
        result["is_transport"] = _is_intercity_transport(result)

    result["extracted_data"] = raw
    return result


def _is_intercity_transport(data: dict) -> bool:
    """判断通用模板抽取结果是否为城市间交通"""
    inv_type = (data.get("invoice_type") or "").lower()
    desc = (data.get("items_description") or "").lower()
    combined = inv_type + " " + desc
    dep = (data.get("departure_city") or "").strip()
    arr = (data.get("arrival_city") or "").strip()
    keywords = [
        "机票", "行程单", "火车票", "高铁", "动车", "船票", "轮船",
        "航空", "铁路", "汽车票", "长途客运",
    ]
    if any(kw in combined for kw in keywords):
        return True
    # 纯电子发票场景里，类型字段可能只有“电子发票”，但会带起讫城市
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

    async def extract_from_file(self, file_path: str, filename_hint: Optional[str] = None) -> dict:
        """
        自动识别发票类型，选择对应模板，调用 NuExtract 抽取。
        """
        filename_for_detect = filename_hint or os.path.basename(file_path)
        inv_type = detect_invoice_type(filename_for_detect)

        async with NuExtractClient(self.host, self.port, self.timeout) as client:
            try:
                template = TEMPLATES.get(inv_type, TEMPLATE_GENERAL)
                raw_result = await client.extract_from_files(
                    file_paths=[file_path],
                    template=template,
                )
                raw = self._pick_first_result(raw_result)

                # 若文件名无法判断类型，用抽取内容二次确认
                if inv_type == "general":
                    content_hint = str(raw)
                    refined_type = detect_invoice_type("", content_hint)
                    if refined_type not in ("general",):
                        # 用精确模板重新抽取
                        raw_result2 = await client.extract_from_files(
                            file_paths=[file_path],
                            template=TEMPLATES[refined_type],
                        )
                        raw = self._pick_first_result(raw_result2)
                        inv_type = refined_type
                    else:
                        # 默认当作电子发票（普通发票）处理，捕获备注
                        inv_type = "general_invoice"

                return _normalize_result(raw, inv_type)
            except Exception as e:
                print(f"[InvoiceExtractor] extract_from_file error ({filename}): {e}")
                return {"error": str(e), "is_transport": False}

    async def extract_from_base64(self, b64_data: str, filename: str) -> dict:
        """从 base64 编码的发票数据中抽取结构化信息。"""
        inv_type = detect_invoice_type(filename)
        async with NuExtractClient(self.host, self.port, self.timeout) as client:
            try:
                template = TEMPLATES.get(inv_type, TEMPLATE_GENERAL)
                raw_result = await client.extract_from_base64(
                    files_data=[b64_data],
                    storage_filenames=[filename],
                    template=template,
                )
                raw = self._pick_first_result(raw_result)

                if inv_type == "general":
                    refined_type = detect_invoice_type("", str(raw))
                    if refined_type not in ("general",):
                        raw_result2 = await client.extract_from_base64(
                            files_data=[b64_data],
                            storage_filenames=[filename],
                            template=TEMPLATES[refined_type],
                        )
                        raw = self._pick_first_result(raw_result2)
                        inv_type = refined_type
                    else:
                        inv_type = "general_invoice"

                return _normalize_result(raw, inv_type)
            except Exception as e:
                print(f"[InvoiceExtractor] extract_from_base64 error ({filename}): {e}")
                return {"error": str(e), "is_transport": False}

    @staticmethod
    def _pick_first_result(raw_result: Any) -> dict:
        """从 NuExtract 响应中取出第一个文件的抽取结果"""
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

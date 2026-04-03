"""
差旅闭环检测 & 会议分组逻辑

差旅闭环算法：
  1. 筛选差旅费中的城市间交通类发票（is_transport=True），按出发时间排序
  2. 城市识别：通过 station_city_map 将车站/机场名统一到城市名
  3. 混合交通支持：飞机、火车、高铁、动车可混搭组成闭环
  4. 以每张交通票的"出发城市"为起点，贪心延伸行程
     - 优先从 home_city（用户所在城市）出发的票开始组链，避免"回家票"误消耗出发票
     - 下一张票的"出发城市" == 当前票的"到达城市" → 接续（在时间上位于链起始票之后）
     - 当某票的"到达城市" == 起点城市 → 闭环成立
  5. 将闭环时间段内的所有差旅费发票（含住宿/餐饮/出租等）归入同一组
  6. 未参与闭环的交通票各自成组（未闭合行程），无交通票的散票单独一组

会议分组：
  - 先用关键词/日期相邻启发式分组
  - 如已配置 LLM，则进一步用 LLM 细化分组
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Optional
from dateutil import parser as dateutil_parser

from app.services.llm_client import get_llm_client
from app.services.station_city_map import normalize_transport_city

logger = logging.getLogger(__name__)


# ─── 城市名标准化（接入映射表）────────────────────────────────────────────────

def _normalize_city(raw: Optional[str]) -> str:
    """
    将发票上的站点/机场/城市名统一到标准城市名。
    优先使用 station_city_map，再做简单清洗。
    """
    if not raw:
        return ""
    return normalize_transport_city(raw)


# ─── 日期解析 ─────────────────────────────────────────────────────────────────

def _parse_date(date_str: Optional[str]) -> Optional[date]:
    if not date_str:
        return None
    try:
        return dateutil_parser.parse(date_str, dayfirst=False).date()
    except Exception:
        return None


# ─── 差旅闭环检测 ─────────────────────────────────────────────────────────────

class TravelLoop:
    def __init__(
        self,
        transport_ids: list[int],
        start_city: str,
        end_city: str,
        start_date: Optional[date],
        end_date: Optional[date],
        cities_path: Optional[list[str]] = None,
        transport_modes: Optional[list[str]] = None,
    ):
        self.transport_ids: list[int] = transport_ids
        self.start_city: str = start_city
        self.end_city: str = end_city
        self.start_date: Optional[date] = start_date
        self.end_date: Optional[date] = end_date
        self.is_closed: bool = (
            bool(start_city)
            and _normalize_city(start_city) == _normalize_city(end_city)
        )
        self.all_invoice_ids: list[int] = list(transport_ids)
        # 行程路径，如 ["上海", "北京", "成都", "上海"]
        self.cities_path: list[str] = cities_path or []
        # 使用的交通方式，如 ["飞机", "高铁", "飞机"]
        self.transport_modes: list[str] = transport_modes or []

    def absorb_non_transport(self, invoices: list[dict]) -> None:
        """
        将闭环时间段内的非城市间交通发票（住宿/出租车/餐饮等）纳入本组。
        扩展时间窗口：出发前1天 ~ 返回后1天，覆盖出行前后的消费。
        """
        if self.start_date is None or self.end_date is None:
            return
        from datetime import timedelta
        window_start = self.start_date - timedelta(days=1)
        window_end = self.end_date + timedelta(days=1)

        for inv in invoices:
            if inv["id"] in self.all_invoice_ids:
                continue
            # 优先用出发时间，否则用开票日期
            d = _parse_date(inv.get("departure_time") or inv.get("issue_date"))
            if d and window_start <= d <= window_end:
                self.all_invoice_ids.append(inv["id"])


def _get_transport_mode(inv: dict) -> str:
    """从发票信息推断交通方式"""
    inv_type = (inv.get("invoice_type") or "").lower()
    type_detected = inv.get("invoice_type_detected") or ""
    desc = (inv.get("items_description") or "").lower()

    if type_detected in ("air_itinerary", "air_electronic") or any(
        k in inv_type + desc for k in ["机票", "航空", "飞机", "航班"]
    ):
        return "飞机"
    if type_detected in ("train_physical", "train_electronic") or any(
        k in inv_type + desc for k in ["高铁", "动车", "火车", "铁路", "高速铁路"]
    ):
        train_no = inv.get("train_number") or ""
        if train_no and train_no[0].upper() in ("G", "C"):
            return "高铁"
        if train_no and train_no[0].upper() == "D":
            return "动车"
        return "火车"
    if any(k in inv_type + desc for k in ["船票", "轮船", "渡轮", "客轮"]):
        return "轮船"
    if any(k in inv_type + desc for k in ["汽车票", "大巴", "长途客运"]):
        return "大巴"
    return "交通"


def detect_travel_loops(
    travel_invoices: list[dict],
    home_city: str = "上海",
) -> list[TravelLoop]:
    """
    对差旅费发票进行闭环检测，返回 TravelLoop 列表。

    参数：
        travel_invoices: 已分类为差旅费的全部发票字典列表。
            is_transport=True  → 城市间交通票（参与闭环检测）
            is_transport=False → 其他差旅相关票（住宿/出租/餐饮等，按时间归入闭环）
        home_city: 用户所在城市（归集设置中的"我所在的城市"），默认"上海"。
            用于优先以 home_city 出发的票作为行程起点，避免回程孤票
            提前"消耗"出发票，导致本该成环的行程被判为未闭合。
    """
    transport = [inv for inv in travel_invoices if inv.get("is_transport")]
    non_transport = [inv for inv in travel_invoices if not inv.get("is_transport")]

    logger.info(
        "travel_loops input total=%s transport=%s non_transport=%s home_city=%s",
        len(travel_invoices),
        len(transport),
        len(non_transport),
        home_city,
    )

    if not transport:
        if travel_invoices:
            lone = TravelLoop(
                transport_ids=[],
                start_city="", end_city="",
                start_date=None, end_date=None,
            )
            lone.is_closed = False
            lone.all_invoice_ids = [inv["id"] for inv in travel_invoices]
            return [lone]
        return []

    # 按出发时间排序
    def sort_key(inv: dict):
        d = _parse_date(inv.get("departure_time") or inv.get("issue_date"))
        return d or date.min

    transport_sorted = sorted(transport, key=sort_key)

    # 优先从 home_city 出发的票开始组链。
    # 这样可避免"上次出差的回程票"（如 BJ→SH）在时间上排在前面，
    # 贪心地将后续"本次出发票"（如 SH→BJ）纳入自己的链中，
    # 导致本该成环的行程被错误地标记为未闭合。
    home = _normalize_city(home_city) if home_city else ""
    if home:
        home_indices = [
            i for i, inv in enumerate(transport_sorted)
            if _normalize_city(inv.get("departure_city")) == home
        ]
        other_indices = [
            i for i, inv in enumerate(transport_sorted)
            if _normalize_city(inv.get("departure_city")) != home
        ]
        start_order = home_indices + other_indices
    else:
        start_order = list(range(len(transport_sorted)))

    loops: list[TravelLoop] = []
    used: set[int] = set()

    for start_idx in start_order:
        start_inv = transport_sorted[start_idx]
        if start_inv["id"] in used:
            continue

        origin = _normalize_city(start_inv.get("departure_city"))
        if not origin:
            # 出发城市不明的交通票单独成组
            used.add(start_inv["id"])
            lone = TravelLoop(
                transport_ids=[start_inv["id"]],
                start_city=start_inv.get("departure_city") or "",
                end_city=start_inv.get("arrival_city") or "",
                start_date=_parse_date(
                    start_inv.get("departure_time") or start_inv.get("issue_date")
                ),
                end_date=_parse_date(
                    start_inv.get("arrival_time") or start_inv.get("issue_date")
                ),
                transport_modes=[_get_transport_mode(start_inv)],
            )
            lone.is_closed = False
            lone.cities_path = [
                start_inv.get("departure_city") or "?",
                start_inv.get("arrival_city") or "?",
            ]
            loops.append(lone)
            continue

        chain: list[dict] = [start_inv]
        current_dest = _normalize_city(start_inv.get("arrival_city"))
        cities_path = [origin]
        modes = [_get_transport_mode(start_inv)]
        used.add(start_inv["id"])
        closed = (current_dest == origin)
        if current_dest:
            cities_path.append(current_dest)

        if not closed:
            # 在时间上位于链起始票之后的所有未使用交通票中寻找接续段。
            # 用 start_idx 定位起始票在时间排序中的位置，确保接续票时间上不早于出发。
            for inv in transport_sorted[start_idx + 1:]:
                if inv["id"] in used:
                    continue
                dep = _normalize_city(inv.get("departure_city"))

                if dep == current_dest:
                    chain.append(inv)
                    used.add(inv["id"])
                    modes.append(_get_transport_mode(inv))
                    current_dest = _normalize_city(inv.get("arrival_city"))
                    if current_dest and (not cities_path or cities_path[-1] != current_dest):
                        cities_path.append(current_dest)
                    if current_dest == origin:
                        closed = True
                        break

        start_date = _parse_date(
            chain[0].get("departure_time") or chain[0].get("issue_date")
        )
        end_date = _parse_date(
            chain[-1].get("arrival_time") or chain[-1].get("issue_date")
        )
        if end_date and start_date and end_date < start_date:
            end_date = start_date

        loop = TravelLoop(
            transport_ids=[inv["id"] for inv in chain],
            start_city=start_inv.get("departure_city") or "",
            end_city=chain[-1].get("arrival_city") or "",
            start_date=start_date,
            end_date=end_date,
            cities_path=cities_path,
            transport_modes=modes,
        )
        loop.is_closed = closed
        loops.append(loop)

    # 将非交通票按时间段归入对应闭环
    for loop in loops:
        loop.absorb_non_transport(non_transport)

    # 未被任何闭环吸收的非交通票单独归入散票组
    absorbed = {inv_id for loop in loops for inv_id in loop.all_invoice_ids}
    remaining = [inv for inv in non_transport if inv["id"] not in absorbed]
    if remaining:
        misc = TravelLoop(
            transport_ids=[],
            start_city="", end_city="",
            start_date=None, end_date=None,
        )
        misc.is_closed = False
        misc.all_invoice_ids = [inv["id"] for inv in remaining]
        loops.append(misc)

    closed_n = sum(1 for lp in loops if lp.is_closed)
    logger.info(
        "travel_loops result groups=%s closed_loops=%s",
        len(loops),
        closed_n,
    )
    for i, lp in enumerate(loops):
        logger.debug(
            "travel_loop[%s] closed=%s cities_path=%s invoice_ids=%s",
            i,
            lp.is_closed,
            lp.cities_path,
            lp.all_invoice_ids,
        )

    return loops


def build_travel_group_name(loop: TravelLoop, idx: int) -> str:
    """
    生成差旅分组名称。
    示例：
      差旅1：上海→北京→上海 闭环 2024-06-02（飞机/高铁）
      差旅2：上海→成都 2025-03-10（高铁，未闭合）
      差旅3：散票
    """
    if not loop.start_city and not loop.end_city:
        return f"差旅{idx + 1}：散票"

    date_str = loop.start_date.strftime("%Y-%m-%d") if loop.start_date else ""

    # 城市路径
    if loop.cities_path and len(loop.cities_path) >= 2:
        path_str = "→".join(loop.cities_path)
    elif loop.start_city and loop.end_city:
        path_str = f"{loop.start_city}→{loop.end_city}"
    else:
        path_str = loop.start_city or loop.end_city

    # 交通方式
    if loop.transport_modes:
        unique_modes = list(dict.fromkeys(loop.transport_modes))  # 保序去重
        mode_str = "/".join(unique_modes)
    else:
        mode_str = ""

    label = "闭环" if loop.is_closed else "未闭合"
    parts = [f"差旅{idx + 1}：{path_str}"]
    if date_str:
        parts.append(date_str)
    parts.append(f"（{label}" + (f"/{mode_str}" if mode_str else "") + "）")
    return " ".join(parts)


# ─── 会议分组 ─────────────────────────────────────────────────────────────────

_MEETING_KEYWORDS = re.compile(
    r"(会议|培训|研讨|论坛|峰会|年会|发布会|交流会|洽谈会|招聘会)"
)


def _meeting_key(inv: dict) -> str:
    """会议启发式分组 key：卖家名 + 开票日期（精确到天）"""
    seller = (inv.get("seller_name") or "").strip()
    date_str = (inv.get("issue_date") or "")[:10]
    return f"{seller}_{date_str}"


def group_meeting_invoices_heuristic(meeting_invoices: list[dict]) -> list[list[int]]:
    """
    启发式会议分组：同一卖家+同一天视为同一场会议。
    返回 [[id, ...], ...]
    """
    if not meeting_invoices:
        return []
    groups: dict[str, list[int]] = {}
    for inv in meeting_invoices:
        key = _meeting_key(inv)
        groups.setdefault(key, []).append(inv["id"])
    return list(groups.values())


async def group_meeting_invoices(meeting_invoices: list[dict]) -> list[list[int]]:
    """
    会议分组入口：先启发式，再用 LLM 细化（如可用）。
    """
    if not meeting_invoices:
        return []

    heuristic_groups = group_meeting_invoices_heuristic(meeting_invoices)
    logger.info(
        "meeting_groups heuristic invoice_count=%s group_count=%s",
        len(meeting_invoices),
        len(heuristic_groups),
    )

    if len(meeting_invoices) > 1:
        try:
            llm = get_llm_client()
            llm_groups = await llm.classify_meeting_group(meeting_invoices)
            if llm_groups:
                original_ids = {inv["id"] for inv in meeting_invoices}
                llm_ids = {inv_id for group in llm_groups for inv_id in group}
                if original_ids == llm_ids:
                    logger.info(
                        "meeting_groups using_llm group_count=%s",
                        len(llm_groups),
                    )
                    return llm_groups
                logger.info(
                    "meeting_groups llm_id_mismatch fallback_heuristic original_ids=%s llm_ids=%s",
                    sorted(original_ids),
                    sorted(llm_ids),
                )
        except Exception:
            logger.exception("meeting_groups LLM failed, using heuristic")

    return heuristic_groups


def build_meeting_group_name(group_invoices: list[dict], idx: int) -> str:
    """根据组内发票推断会议名称"""
    for inv in group_invoices:
        desc = inv.get("items_description") or ""
        seller = inv.get("seller_name") or ""
        m = _MEETING_KEYWORDS.search(desc) or _MEETING_KEYWORDS.search(seller)
        if m:
            date_str = (inv.get("issue_date") or "")[:10]
            return f"会议{idx + 1}：{seller[:12]} {date_str}".strip()
    first = group_invoices[0] if group_invoices else {}
    date_str = (first.get("issue_date") or "")[:10]
    return f"会议{idx + 1} {date_str}".strip()

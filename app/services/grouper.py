"""
差旅闭环检测 & 会议分组逻辑

差旅闭环算法：
  1. 筛选差旅费中的城市间交通类发票（is_transport=True），按乘车/航班事件时间排序
     （优先列字段 departure_time 中的年月日，其次 extracted_data 乘车/航班日期，最后才用开票日）
  2. 城市识别：通过 station_city_map 将车站/机场名统一到城市名
  3. 混合交通支持：飞机、火车、高铁、动车可混搭组成闭环
  4. 以每张交通票的"出发城市"为起点，贪心延伸行程
     - 优先从 home_city（用户所在城市）出发的票开始组链，避免"回家票"误消耗出发票
     - 下一张票的"出发城市"（归一化后）== 当前所在城市 → 接续；下一段在**全部未使用**交通票中
       按乘车日期选取（支持多城市 A→B→C→A，且纠正「仅按排序列表顺序」导致的断链）
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
from datetime import date, datetime, time, timedelta
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

_RE_CN_YMD = re.compile(
    r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?"
)


def _parse_date(date_str: Optional[str]) -> Optional[date]:
    if not date_str:
        return None
    raw = str(date_str).strip()
    m = _RE_CN_YMD.search(raw)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d)
        except ValueError:
            pass
    try:
        return dateutil_parser.parse(raw, dayfirst=False).date()
    except Exception:
        return None


_YEAR_IN_STR = re.compile(r"\d{4}")
_TIME_ONLY = re.compile(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$")
# 从「2025年06月04日 12:48开」等整段文字中提取时刻
_HHMM_IN_TEXT = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?!\d)")


def _hhmm_tuple_from_text(text: Optional[str]) -> Optional[tuple[int, int]]:
    if not text:
        return None
    m = _HHMM_IN_TEXT.search(str(text))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if 0 <= h < 24 and 0 <= mi < 60:
        return h, mi
    return None


def _extract_departure_date_str(ex: dict) -> str:
    if not ex:
        return ""
    for key in (
        "departure_date",
        "出发日期",
        "乘车日期",
        "乘车日期时间",
        "航班日期(DATE)",
        "航班日期",
    ):
        v = ex.get(key)
        if v and str(v).strip():
            return str(v).strip()
    return ""


def _extract_departure_time_str(ex: dict) -> str:
    if not ex:
        return ""
    for key in ("departure_time", "出发时间", "乘车时间"):
        v = ex.get(key)
        if v and str(v).strip():
            return str(v).strip()
    return ""


def _transport_event_datetime(inv: dict) -> Optional[datetime]:
    """
    差旅交通票的「事件时间」，用于排序与接链。
    严格优先：departure_time 中含年月日 → extracted_data 乘车/航班日期(+时间) →
    仅列上的可解析日期 → 最后才用 issue_date（开票日兜底）。
    """
    ex = inv.get("extracted_data") if isinstance(inv.get("extracted_data"), dict) else {}
    dep_col = (inv.get("departure_time") or "").strip()

    if dep_col and _YEAR_IN_STR.search(dep_col):
        try:
            return dateutil_parser.parse(dep_col, dayfirst=False)
        except Exception:
            d0 = _parse_date(dep_col)
            if d0:
                return datetime.combine(d0, time.min)

    dd = _extract_departure_date_str(ex)
    d_ex = _parse_date(dd) if dd else None

    tm_str = ""
    if dep_col and _TIME_ONLY.match(dep_col):
        tm_str = dep_col.strip()
    if not tm_str:
        tm_str = _extract_departure_time_str(ex).strip()

    if d_ex:
        hm = _hhmm_tuple_from_text(tm_str) if tm_str else None
        if not hm and dd:
            hm = _hhmm_tuple_from_text(dd)
        if hm:
            return datetime.combine(d_ex, time(hm[0], hm[1]))
        m = _TIME_ONLY.match(tm_str) if tm_str else None
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
            return datetime.combine(d_ex, time(h, mi))
        return datetime.combine(d_ex, time.min)

    if dep_col:
        d_col = _parse_date(dep_col)
        if d_col:
            return datetime.combine(d_col, time.min)

    issue = (inv.get("issue_date") or "").strip()
    idate = _parse_date(issue)
    if idate:
        return datetime.combine(idate, time.min)
    return None


def _sort_datetime_for_transport(inv: dict) -> datetime:
    """排序用：无事件时间的票排在最早，避免随机打散 home 起点顺序。"""
    dt = _transport_event_datetime(inv)
    return dt if dt else datetime(1970, 1, 1)


def _cities_chain_match(here_normalized: str, dep_raw: Optional[str]) -> bool:
    """同城不同表述（归一化不一致时的保守补救）：如一方包含另一方城市核心词。"""
    d = _normalize_city(dep_raw)
    if not here_normalized or not d or here_normalized == d:
        return False
    a, b = here_normalized.replace(" ", ""), d.replace(" ", "")
    if len(a) >= 2 and len(b) >= 2 and (a in b or b in a):
        return True
    return False


def _departure_is_home(home_norm: str, dep_raw: Optional[str]) -> bool:
    """票面出发地是否视为 base 地（归一化一致或同城别名）。"""
    if not home_norm:
        return False
    d = _normalize_city(dep_raw)
    if d == home_norm:
        return True
    return _cities_chain_match(home_norm, dep_raw)


def _non_transport_absorb_date(inv: dict) -> Optional[date]:
    """
    非交通票归入行程窗口时参考的日期：优先住宿入住等业务日，而非开票日。
    """
    ex = inv.get("extracted_data") if isinstance(inv.get("extracted_data"), dict) else {}
    for key in (
        "check_in_date",
        "入住日期",
        "住宿日期",
        "入住时间",
        "trip_date",
        "会议开始日期",
        "会议结束日期",
        "打车时间（上车时间）",
    ):
        v = ex.get(key)
        if v and str(v).strip():
            d = _parse_date(str(v).strip())
            if d:
                return d
    col_dep = (inv.get("departure_time") or "").strip()
    if col_dep and _YEAR_IN_STR.search(col_dep):
        d = _parse_date(col_dep)
        if d:
            return d
    ev = _transport_event_datetime(inv)
    if ev:
        return ev.date()
    return _parse_date(inv.get("departure_time") or inv.get("issue_date"))


def _collect_non_transport_candidate_cities(inv: dict) -> set[str]:
    """收集非交通票可用于归组匹配的城市候选。"""
    ex = inv.get("extracted_data") if isinstance(inv.get("extracted_data"), dict) else {}
    raw_values: list[str] = []

    for key in ("departure_city", "arrival_city"):
        v = inv.get(key)
        if v and str(v).strip():
            raw_values.append(str(v).strip())

    for key in (
        "会议城市",
        "会议地点",
        "城市",
        "上车地点（起点）",
        "下车地点（终点）",
        "出发地",
        "到达地",
        "出发地点(自FROM)",
        "到达地点(至TO)",
        "备注出发地",
        "备注目的地",
    ):
        v = ex.get(key)
        if v and str(v).strip():
            raw_values.append(str(v).strip())

    # 会议/住宿常见兜底：地点只出现在文本或销售方名称中
    for key in ("seller_name", "remarks", "items_description"):
        v = inv.get(key)
        if v and str(v).strip():
            raw_values.append(str(v).strip())

    seller_info = ex.get("销售方信息")
    if isinstance(seller_info, dict):
        name = seller_info.get("名称")
        if name and str(name).strip():
            raw_values.append(str(name).strip())

    buyer_info = ex.get("购买方信息")
    if isinstance(buyer_info, dict):
        name = buyer_info.get("名称")
        if name and str(name).strip():
            raw_values.append(str(name).strip())

    out: set[str] = set()
    for raw in raw_values:
        city = _normalize_city(raw)
        if city:
            out.add(city)
    return out


def _city_matches_loop(loop: "TravelLoop", city: str) -> bool:
    if not city:
        return False
    loop_cities = {
        c for c in (
            [_normalize_city(x) for x in loop.cities_path]
            + [_normalize_city(loop.start_city), _normalize_city(loop.end_city)]
        )
        if c
    }
    if not loop_cities:
        return False
    for lc in loop_cities:
        if city == lc:
            return True
        if _cities_chain_match(lc, city) or _cities_chain_match(city, lc):
            return True
    return False


def _non_transport_city_matches_loop(inv: dict, loop: "TravelLoop") -> bool:
    """非交通票城市需命中行程路径城市（含起止城市）。"""
    for city in _collect_non_transport_candidate_cities(inv):
        if _city_matches_loop(loop, city):
            return True
    return False


def _pick_next_transport_leg(
    chain: list[dict],
    current_city: str,
    origin: str,
    transport_sorted: list[dict],
    used: set[int],
) -> tuple[Optional[dict], str, bool]:
    """
    从全部未使用票中选取下一段：出发城市（归一化）与当前所在城市一致或可链接。
    优先选事件时间不早于上一段的票；若无，则在首段日起 30 天窗口内取最早一张。
    返回 (下一张票或 None, 新的所在城市, 是否已回到 origin)。
    """
    if not current_city:
        return None, current_city, False
    candidates = [
        inv
        for inv in transport_sorted
        if inv["id"] not in used
        and (
            _normalize_city(inv.get("departure_city")) == current_city
            or _cities_chain_match(current_city, inv.get("departure_city"))
        )
    ]
    if not candidates:
        return None, current_city, False

    last_dt = _transport_event_datetime(chain[-1])
    first_dt = _transport_event_datetime(chain[0])
    first_d = first_dt.date() if first_dt else date.min
    window_end = first_d + timedelta(days=30) if first_d != date.min else None

    def in_trip_window(dt: Optional[datetime]) -> bool:
        if not dt:
            return False
        d = dt.date()
        if first_d == date.min:
            return True
        if d < first_d:
            return False
        if window_end is not None and d > window_end:
            return False
        return True

    dated: list[tuple[dict, datetime]] = []
    for inv in candidates:
        dt = _transport_event_datetime(inv)
        if dt:
            dated.append((inv, dt))
    if not dated:
        return None, current_city, False

    ok = [
        (inv, dt)
        for inv, dt in dated
        if last_dt is None or dt >= last_dt
    ]
    ok = [(inv, dt) for inv, dt in ok if in_trip_window(dt)]
    if not ok and last_dt is not None:
        ok = [(inv, dt) for inv, dt in dated if in_trip_window(dt)]
    if not ok:
        ok = dated

    next_inv, next_dt = min(ok, key=lambda x: x[1])
    used.add(next_inv["id"])
    new_city = _normalize_city(next_inv.get("arrival_city"))
    closed = bool(new_city and new_city == origin)
    return next_inv, new_city, closed


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
        window_start = self.start_date - timedelta(days=1)
        window_end = self.end_date + timedelta(days=1)

        for inv in invoices:
            if inv["id"] in self.all_invoice_ids:
                continue
            d = _non_transport_absorb_date(inv)
            city_ok = _non_transport_city_matches_loop(inv, self)
            if d and window_start <= d <= window_end and city_ok:
                self.all_invoice_ids.append(inv["id"])


def match_invoice_to_travel_loop(
    invoice: dict,
    loops: list[TravelLoop],
    *,
    date_padding_days: int = 1,
) -> Optional[int]:
    """
    判断一张非交通票据可吸附到哪个差旅行程组。
    返回命中的 loops 下标；无命中返回 None。
    """
    d = _non_transport_absorb_date(invoice)
    if d is None:
        return None

    for idx, loop in enumerate(loops):
        if loop.start_date is None or loop.end_date is None:
            continue
        window_start = loop.start_date - timedelta(days=date_padding_days)
        window_end = loop.end_date + timedelta(days=date_padding_days)
        if not (window_start <= d <= window_end):
            continue
        if _non_transport_city_matches_loop(invoice, loop):
            return idx
    return None


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

    # 按乘车/航班事件时间排序（优先 departure_time 与 extracted 乘车日，而非开票日）
    transport_sorted = sorted(transport, key=_sort_datetime_for_transport)

    # 优先从 home_city 出发的票开始组链。
    # 这样可避免"上次出差的回程票"（如 BJ→SH）在时间上排在前面，
    # 贪心地将后续"本次出发票"（如 SH→BJ）纳入自己的链中，
    # 导致本该成环的行程被错误地标记为未闭合。
    home = _normalize_city(home_city) if home_city else ""
    if home:
        home_indices = [
            i for i, inv in enumerate(transport_sorted)
            if _departure_is_home(home, inv.get("departure_city"))
        ]
        other_indices = [
            i for i, inv in enumerate(transport_sorted)
            if not _departure_is_home(home, inv.get("departure_city"))
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
            lone_ev = _transport_event_datetime(start_inv)
            lone = TravelLoop(
                transport_ids=[start_inv["id"]],
                start_city=start_inv.get("departure_city") or "",
                end_city=start_inv.get("arrival_city") or "",
                start_date=lone_ev.date() if lone_ev else None,
                end_date=lone_ev.date() if lone_ev else None,
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

        while not closed and current_dest:
            nxt, current_dest, closed = _pick_next_transport_leg(
                chain, current_dest, origin, transport_sorted, used
            )
            if nxt is None:
                break
            chain.append(nxt)
            modes.append(_get_transport_mode(nxt))
            if current_dest and (not cities_path or cities_path[-1] != current_dest):
                cities_path.append(current_dest)

        sdt = _transport_event_datetime(chain[0])
        edt = _transport_event_datetime(chain[-1])
        start_date = sdt.date() if sdt else None
        end_date = edt.date() if edt else None
        arr_d = _parse_date(chain[-1].get("arrival_time"))
        if arr_d and end_date and arr_d > end_date:
            end_date = arr_d
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

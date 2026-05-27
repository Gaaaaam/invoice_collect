"""
无 NuExtract 时的本地保底：PDF 文字层（PyMuPDF）+ RapidOCR，再经启发式正则与既有 _normalize_result 归一化。
可选引擎 tesseract / easyocr 需在配置中指定并自行安装对应依赖（见 README）。
"""

from __future__ import annotations

import base64
import logging
import os
import re
import tempfile
from typing import Any, Callable, Optional

from app.services.extractor import _normalize_result, detect_invoice_type

logger = logging.getLogger(__name__)

_RE_TRAIN_NO = re.compile(r"\b([GDCKTZgdcktz]\d{2,5})\b")
_RE_FLIGHT_NO = re.compile(r"\b([A-Z]{2}\d{3,4})\b")
_RE_INVOICE_NO = re.compile(r"发票号码\s*[：:]\s*([0-9]{8,30})", re.I)
_RE_ISSUE_DATE = re.compile(
    r"开票日期\s*[：:]\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日?|\d{4}[-/年．]\d{1,2}[-/月．]\d{1,2}日?)",
    re.I,
)
_RE_TOTAL = re.compile(
    r"(?:价税合计|价税合计（大写）|（小写）|小写)\s*[（(][^）)]*[）)]?\s*[：:]*\s*[¥￥]?\s*([\d,，．\.]+)",
    re.I,
)
_RE_TOTAL2 = re.compile(r"[（(]小写[）)]\s*[：:]*\s*[¥￥]?\s*([\d,，．\.]+)", re.I)
_RE_SELLER_NAME = re.compile(
    r"销\s*售\s*方[^\n]*\n(?:.*\n){0,4}.*?名\s*称\s*[：:]\s*([^\n]{2,120})",
    re.I,
)
_RE_BUYER_NAME = re.compile(
    r"购\s*买\s*方[^\n]*\n(?:.*\n){0,4}.*?名\s*称\s*[：:]\s*([^\n]{2,120})",
    re.I,
)
_RE_REMARKS = re.compile(r"备\s*注\s*[：:]\s*(.+?)(?=\n\s*\n|\n[A-Z\u4e00-\u9fa5]{1,3}\s*[：:]|\Z)", re.S)

# 站名-箭头/连字符-站名（电子票/通用）
_RE_STATION_PAIR = re.compile(
    r"([\u4e00-\u9fa5]{2,12}站)\s*[→\-—至到]\s*([\u4e00-\u9fa5]{2,12}站)"
)
# 物理火车票版式：出发站 车次 到达站（同行或相邻行）
_RE_STATION_PAIR_PHYSICAL = re.compile(
    r"([\u4e00-\u9fa5]{2,10}站)\s+[GDCKTZgdcktz]\d{2,5}\s+([\u4e00-\u9fa5]{2,10}站)",
    re.I,
)
# 独立站名（用于备用提取）
_RE_STATION_SINGLE = re.compile(r"([\u4e00-\u9fa5]{2,10}站)")

# 物理火车票：发车日期+时间+开，如 "2024年08月25日14:02开"
_RE_TRAIN_DEPART_DT = re.compile(
    r"(\d{4}年\d{1,2}月\d{1,2}日)\s*(\d{1,2}:\d{2})\s*开"
)
# 金额：¥314.0元 或 ￥314.0元
_RE_TRAIN_AMOUNT_YUAN = re.compile(r"[¥￥]\s*([\d,，\.]+\.?\d*)\s*元")
# 座位等级
_RE_SEAT_CLASS = re.compile(r"(二等座|一等座|商务座|特等座|软卧|硬卧|软座|硬座)")
# 车厢座位号：如 14车09A号（OCR 有时误读"车"为"年"）
_RE_CAR_SEAT = re.compile(r"(\d{1,2})[车年]\s*(\d{2,3}[A-Z])\s*[号号]", re.I)
# 检票口信息（物理票特征）
_RE_TRAIN_CHECKIN = re.compile(r"检\s*票\s*[:：]")
# 票面序号：如 Z28U065543（字母+数字+字母+数字）
_RE_TICKET_SERIAL = re.compile(r"[A-Z]\d+[A-Z]\d{4,}")


def _score_train_physical(text: str) -> int:
    """
    对 OCR 全文进行多信号叠加评分，判断是否为物理火车票（报销凭证）。
    分值 >= 10 则认定为 train_physical。
    """
    score = 0
    # 最强信号：报销专用标识
    if re.search(r"(报销凭证|仅供报销使用)", text):
        score += 10
    # 车次号（G/D/C/K/T/Z + 2-5 位数字）
    if _RE_TRAIN_NO.search(text):
        score += 5
    # 座位等级
    if _RE_SEAT_CLASS.search(text):
        score += 5
    # 发车时间 "HH:MM开"
    if re.search(r"\d{1,2}:\d{2}\s*开", text):
        score += 4
    # 检票口信息
    if _RE_TRAIN_CHECKIN.search(text):
        score += 4
    # 站名
    stations = _RE_STATION_SINGLE.findall(text)
    if len(stations) >= 1:
        score += 3
    # 票面序号格式（字母+数字+字母+数字，如 Z28U065543）
    if _RE_TICKET_SERIAL.search(text):
        score += 3
    # 金额 ¥xxx.x元 格式
    if _RE_TRAIN_AMOUNT_YUAN.search(text):
        score += 3
    # 退票/改签提示（票背面文字）
    if re.search(r"(退票|改签|须交回车站)", text):
        score += 3
    return score


def _preprocess_image_for_ocr(image_path: str) -> Optional[str]:
    """
    对手机拍摄的图片进行预处理以提升 OCR 识别率。
    增强对比度和锐度，必要时放大低分辨率图片。
    返回预处理后的临时文件路径；若失败则返回 None，调用方应继续使用原路径。
    """
    try:
        from PIL import Image, ImageEnhance
        img = Image.open(image_path)
        if img.mode == "RGBA":
            img = img.convert("RGB")
        elif img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        # 低分辨率图片放大，保证 OCR 有足够像素
        w, h = img.size
        if min(w, h) < 1000:
            scale = 1000 / min(w, h)
            new_w, new_h = int(w * scale), int(h * scale)
            img = img.resize((new_w, new_h), Image.LANCZOS)

        # 增强对比度（1.5×）和锐度（2.0×）
        img = ImageEnhance.Contrast(img).enhance(1.5)
        img = ImageEnhance.Sharpness(img).enhance(2.0)

        fd, tmp = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        img.save(tmp, "PNG")
        return tmp
    except Exception as e:
        logger.debug("图像预处理失败（将使用原图）: %s", e)
        return None


_rapid_ocr_engine: Any = None


def _is_numpy_onnx_binary_mismatch(exc: BaseException) -> bool:
    parts: list[str] = []
    cur: Optional[BaseException] = exc
    for _ in range(8):
        if cur is None:
            break
        parts.append(str(cur))
        parts.append(repr(cur))
        cur = cur.__cause__ or cur.__context__
    blob = " ".join(parts).lower()
    return (
        "array_api" in blob
        or "multiarray" in blob
        or ("numpy" in blob and "1.x" in blob and "cannot be run" in blob)
    )


def _get_rapid_ocr() -> Any:
    global _rapid_ocr_engine
    if _rapid_ocr_engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except (ImportError, SystemError) as e:
            if _is_numpy_onnx_binary_mismatch(e):
                raise RuntimeError(
                    "ONNX Runtime 与当前 NumPy 不兼容（常见于 NumPy 2.x 与 onnxruntime<1.19 同时安装）。"
                    "请在当前虚拟环境中执行: pip install -U 'onnxruntime>=1.19.2'，"
                    "或重新执行 pip install -r requirements.txt。"
                    "也可在配置中将 extraction.ocr.engine 改为 tesseract 或 easyocr。"
                ) from e
            if isinstance(e, ImportError):
                raise RuntimeError(
                    "未安装 RapidOCR 依赖，请执行: pip install -r requirements.txt"
                    "（若仍失败可改用 extraction.ocr.engine: tesseract 或 easyocr）"
                ) from e
            raise
        _rapid_ocr_engine = RapidOCR()
    return _rapid_ocr_engine


def _ocr_lines_from_rapidocr(image_path: str) -> str:
    engine = _get_rapid_ocr()
    result, _elapsed = engine(image_path)
    if result is None or not result:
        return ""
    lines: list[str] = []
    for item in result:
        if isinstance(item, (list, tuple)) and len(item) >= 2 and item[1]:
            lines.append(str(item[1]).strip())
    return "\n".join(lines)


def _ocr_lines_from_tesseract(image_path: str) -> str:
    try:
        import pytesseract
        from PIL import Image
    except ImportError as e:
        raise RuntimeError("未安装 pytesseract / Pillow，请 pip install pytesseract pillow 并安装 Tesseract 可执行程序") from e
    img = Image.open(image_path)
    return pytesseract.image_to_string(img, lang="chi_sim+eng") or ""


_easyocr_reader: Any = None


def _ocr_lines_from_easyocr(image_path: str) -> str:
    global _easyocr_reader
    try:
        import easyocr
    except ImportError as e:
        raise RuntimeError("未安装 easyocr，请 pip install easyocr") from e
    if _easyocr_reader is None:
        _easyocr_reader = easyocr.Reader(["ch_sim", "en"], gpu=False)
    rows = _easyocr_reader.readtext(image_path, detail=0)
    return "\n".join(str(x) for x in rows if x)


def _select_ocr_fn(engine: str) -> Callable[[str], str]:
    e = (engine or "rapidocr_onnx").strip().lower().replace("-", "_")
    if e in ("rapidocr_onnx", "rapidocr", "onnx"):
        return _ocr_lines_from_rapidocr
    if e == "tesseract":
        return _ocr_lines_from_tesseract
    if e == "easyocr":
        return _ocr_lines_from_easyocr
    raise ValueError(f"不支持的 OCR 引擎: {engine}，可选: rapidocr_onnx, tesseract, easyocr")


def _pdf_text_then_ocr(path: str, ocr_cfg: dict, ocr_fn: Callable[[str], str]) -> str:
    import fitz

    min_chars = int(ocr_cfg.get("min_text_chars", 80))
    max_pages = int(ocr_cfg.get("pdf_max_pages", 3))
    doc = fitz.open(path)
    try:
        n = min(doc.page_count, max_pages)
        text_parts: list[str] = []
        for i in range(n):
            text_parts.append(doc.load_page(i).get_text() or "")
        merged = "\n".join(text_parts)
        if len(merged.strip()) >= min_chars:
            return merged

        ocr_chunks: list[str] = []
        mat = fitz.Matrix(1.5, 1.5)
        for i in range(n):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            fd, tmp = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            try:
                pix.save(tmp)
                ocr_chunks.append(ocr_fn(tmp) or "")
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        combined = "\n".join(ocr_chunks)
        return combined if combined.strip() else merged
    finally:
        doc.close()


def _image_to_text(path: str, ocr_fn: Callable[[str], str]) -> str:
    ext = os.path.splitext(path)[1].lower()
    # 手机拍摄的 JPEG 图片先做图像增强，提升 OCR 准确率
    preprocessed: Optional[str] = None
    if ext in (".jpg", ".jpeg"):
        preprocessed = _preprocess_image_for_ocr(path)

    if preprocessed:
        try:
            result = ocr_fn(preprocessed) or ""
            # 若预处理后识别结果比原图好（字符更多），使用预处理结果
            raw_result = ocr_fn(path) or ""
            return result if len(result) >= len(raw_result) else raw_result
        except Exception:
            return ocr_fn(path) or ""
        finally:
            try:
                os.unlink(preprocessed)
            except OSError:
                pass
    return ocr_fn(path) or ""


def extract_full_text_from_file(file_path: str, filename_hint: str, ocr_cfg: dict) -> str:
    ext = os.path.splitext(filename_hint or file_path)[1].lower()
    engine = str(ocr_cfg.get("engine", "rapidocr_onnx"))
    ocr_fn = _select_ocr_fn(engine)

    if ext == ".pdf":
        return _pdf_text_then_ocr(file_path, ocr_cfg, ocr_fn)
    if ext in (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"):
        return _image_to_text(file_path, ocr_fn)
    if ext == ".ofd":
        raise RuntimeError("OFD 格式暂不支持本地 OCR 保底，请转为 PDF/图片或部署 NuExtract")
    raise RuntimeError(f"不支持的扩展名: {ext}")


def extract_full_text_from_segment(file_path: str, filename_hint: str, ocr_cfg: dict) -> str:
    """从已拆分的 segment 文件抽取全文（单 segment，不跨文件合并）。"""
    return extract_full_text_from_file(file_path, filename_hint, ocr_cfg)


def find_all_invoice_numbers(text: str) -> list[str]:
    """在全文/页文本中查找全部发票号码（用于多票检测）。"""
    nums: list[str] = []
    for m in _RE_INVOICE_NO.finditer(text or ""):
        nums.append(m.group(1).strip())
    return nums


def _heuristic_raw(full_text: str, inv_type: str) -> dict[str, Any]:
    text = full_text.strip()
    raw: dict[str, Any] = {}

    def _clean(s: Optional[str]) -> Optional[str]:
        if not s:
            return None
        s = re.sub(r"\s+", " ", s.strip())
        return s[:500] if len(s) > 500 else s

    m = _RE_INVOICE_NO.search(text)
    if m:
        raw["invoice_number"] = m.group(1).strip()
    m = _RE_ISSUE_DATE.search(text)
    if m:
        raw["issue_date"] = m.group(1).strip().replace("．", ".")

    m = _RE_TOTAL.search(text)
    if not m:
        m = _RE_TOTAL2.search(text)
    if m:
        amt = m.group(1).strip()
        raw["total_amount"] = amt
        raw["amount"] = amt

    m = _RE_SELLER_NAME.search(text)
    if m:
        raw["seller_name"] = _clean(m.group(1))
    m = _RE_BUYER_NAME.search(text)
    if m:
        raw["buyer_name"] = _clean(m.group(1))

    m = _RE_REMARKS.search(text)
    remarks = _clean(m.group(1)) if m else None
    if remarks:
        raw["remarks"] = remarks

    if inv_type in ("train_physical", "train_electronic"):
        tm = _RE_TRAIN_NO.search(text)
        if tm:
            raw["train_number"] = tm.group(1).upper()

        # 站名提取：优先"站名→站名"格式，其次"站名 车次 站名"物理票版式，最后取前两个站名
        sm = _RE_STATION_PAIR.search(text)
        if sm:
            raw["departure_station"] = sm.group(1)
            raw["arrival_station"] = sm.group(2)
        else:
            sm2 = _RE_STATION_PAIR_PHYSICAL.search(text)
            if sm2:
                raw["departure_station"] = sm2.group(1)
                raw["arrival_station"] = sm2.group(2)
            else:
                stations = _RE_STATION_SINGLE.findall(text)
                if len(stations) >= 2:
                    raw["departure_station"] = stations[0]
                    raw["arrival_station"] = stations[1]

        raw["invoice_type"] = raw.get("invoice_type") or (
            "铁路电子客票" if inv_type == "train_electronic" else "火车票"
        )

        # 发车日期+时间（如 "2024年08月25日14:02开"）
        if not raw.get("departure_date"):
            dtm = _RE_TRAIN_DEPART_DT.search(text)
            if dtm:
                raw["departure_date"] = dtm.group(1)
                raw["departure_time"] = dtm.group(2)
            elif not raw.get("issue_date"):
                # 尝试通用日期格式
                gd = re.search(r"(\d{4}年\d{1,2}月\d{1,2}日)", text)
                if gd:
                    raw["departure_date"] = gd.group(1)

        # 金额提取：¥xxx.x元 / ￥xxx.x元 / 金额:
        if not raw.get("amount"):
            mx = _RE_TRAIN_AMOUNT_YUAN.search(text)
            if mx:
                raw["amount"] = mx.group(1).strip()
            else:
                for pat in (r"[¥￥]\s*([\d,，\.]+)", r"金额\s*[：:]\s*[¥￥]?\s*([\d,，\.]+)"):
                    mx2 = re.search(pat, text)
                    if mx2:
                        raw["amount"] = mx2.group(1).strip()
                        break

        # 座位等级
        if not raw.get("seat_class"):
            sc = _RE_SEAT_CLASS.search(text)
            if sc:
                raw["seat_class"] = sc.group(1)

        # 车厢座位号
        if not raw.get("car_seat"):
            cs = _RE_CAR_SEAT.search(text)
            if cs:
                raw["car_seat"] = f"{cs.group(1)}车{cs.group(2)}号"

    elif inv_type in ("air_itinerary", "air_electronic"):
        fm = _RE_FLIGHT_NO.search(text)
        if fm:
            raw["flight_number"] = fm.group(1)
        raw["invoice_type"] = raw.get("invoice_type") or "航空运输电子客票行程单"
        if not raw.get("total_amount"):
            mx = re.search(r"合计\s*[：:]\s*[¥￥]?\s*([\d,，．\.]+)", text)
            if mx:
                raw["total_amount"] = mx.group(1).strip()

    elif inv_type == "taxi":
        raw["invoice_type"] = raw.get("invoice_type") or "出租车发票"
        if not raw.get("amount"):
            mx = re.search(r"金额\s*[：:]\s*[¥￥]?\s*([\d,，．\.]+)", text)
            if mx:
                raw["amount"] = mx.group(1).strip()

    elif inv_type == "ridehailing":
        raw["invoice_type"] = raw.get("invoice_type") or "网约车发票"

    elif inv_type == "hotel":
        raw["invoice_type"] = raw.get("invoice_type") or "住宿发票"

    # 通用电子发票：长文本供子类型与备注解析
    if inv_type in ("general_invoice", "general"):
        raw.setdefault("invoice_type", "电子发票（普通发票）")
        raw["items_description"] = text[:4000] if text else ""
        if not raw.get("remarks") and text:
            raw["remarks"] = text[-2000:]
    elif not raw.get("items_description") and text:
        raw["items_description"] = text[:4000]

    return raw


def _refine_invoice_type(filename: str, full_text: str, inv_type: str) -> str:
    if inv_type == "general":
        refined = detect_invoice_type("", full_text)
        if refined != "general":
            logger.debug("_refine_invoice_type: content_rules -> %s", refined)
            return refined
        # 内容规则未命中时，使用多信号评分兜底识别物理火车票
        score = _score_train_physical(full_text)
        logger.debug("_refine_invoice_type: train_physical_score=%d", score)
        if score >= 10:
            return "train_physical"
        return "general_invoice"
    return inv_type


def extract_invoice_from_ocr_full_text(full_text: str, filename_hint: str) -> dict[str, Any]:
    """
    在已有 OCR/PDF 全文上：类型检测 → 启发式字段 → 归一化。
    供 extract_invoice_local_fallback 与 InvoiceExtractor._run_ocr_fallback_pipeline 复用。
    """
    if not (full_text or "").strip():
        return {"error": "未能识别到文本（请确认依赖已安装且文件清晰）", "is_transport": False}

    fn = filename_hint or ""
    inv_type = detect_invoice_type(fn, full_text)
    inv_type = _refine_invoice_type(fn, full_text, inv_type)
    raw = _heuristic_raw(full_text, inv_type)
    raw["_ocr_text_preview"] = full_text[:2000]
    return _normalize_result(raw, inv_type)


def extract_invoice_local_fallback(file_path: str, filename_hint: str, ocr_cfg: dict) -> dict[str, Any]:
    """
    从本地文件抽取：全文 OCR/PDF 文本 → 启发式字段 → _normalize_result。
    返回与 NuExtract 路径一致的结构化 dict（可能含 error）。
    """
    fn = filename_hint or os.path.basename(file_path)
    try:
        full_text = extract_full_text_from_file(file_path, fn, ocr_cfg)
    except Exception as e:
        return {"error": str(e), "is_transport": False}
    return extract_invoice_from_ocr_full_text(full_text, fn)


def extract_invoice_local_fallback_from_base64(b64_data: str, filename: str, ocr_cfg: dict) -> dict[str, Any]:
    try:
        raw_bytes = base64.b64decode(b64_data)
    except Exception as e:
        return {"error": f"Base64 解码失败: {e}", "is_transport": False}

    ext = os.path.splitext(filename)[1] or ".png"
    fd, path = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw_bytes)
        return extract_invoice_local_fallback(path, filename, ocr_cfg)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

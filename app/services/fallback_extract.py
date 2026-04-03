"""
无 NuExtract 时的本地保底：PDF 文字层（PyMuPDF）+ RapidOCR，再经启发式正则与既有 _normalize_result 归一化。
可选引擎 tesseract / easyocr 需在配置中指定并自行安装对应依赖（见 README）。
"""

from __future__ import annotations

import base64
import os
import re
import tempfile
from typing import Any, Callable, Optional

from app.services.extractor import _normalize_result, detect_invoice_type

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
_RE_STATION_PAIR = re.compile(
    r"([\u4e00-\u9fa5]{2,12}站)\s*[→\-—至到]\s*([\u4e00-\u9fa5]{2,12}站)"
)


_rapid_ocr_engine: Any = None


def _get_rapid_ocr() -> Any:
    global _rapid_ocr_engine
    if _rapid_ocr_engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as e:
            raise RuntimeError(
                "未安装 RapidOCR，请执行: pip install -r requirements-ocr.txt"
            ) from e
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
        sm = _RE_STATION_PAIR.search(text)
        if sm:
            raw["departure_station"] = sm.group(1)
            raw["arrival_station"] = sm.group(2)
        raw["invoice_type"] = raw.get("invoice_type") or (
            "铁路电子客票" if inv_type == "train_electronic" else "火车票"
        )
        if not raw.get("amount"):
            for pat in (r"￥\s*([\d,，．\.]+)\s*元", r"金额\s*[：:]\s*[¥￥]?\s*([\d,，．\.]+)"):
                mx = re.search(pat, text)
                if mx:
                    raw["amount"] = mx.group(1).strip()
                    break

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
            return refined
        return "general_invoice"
    return inv_type


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

    if not (full_text or "").strip():
        return {"error": "未能识别到文本（请确认依赖已安装且文件清晰）", "is_transport": False}

    inv_type = detect_invoice_type(fn, full_text)
    inv_type = _refine_invoice_type(fn, full_text, inv_type)
    raw = _heuristic_raw(full_text, inv_type)
    raw["_ocr_text_preview"] = full_text[:2000]

    return _normalize_result(raw, inv_type)


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

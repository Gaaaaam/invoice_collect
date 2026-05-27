"""
PDF 多发票分析：逐页锚点检测、跨页合并、一页多票区域裁剪。
"""
from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from typing import Optional

_RE_INVOICE_NO = re.compile(r"发票号码\s*[：:]\s*([0-9]{8,30})", re.I)
_RE_INVOICE_NO_ALT = re.compile(r"(?:发票代码|票据号码)\s*[：:]\s*([0-9]{8,30})", re.I)
_RE_TOTAL = re.compile(r"(?:价税合计|合计金额|总金额)\s*[：:¥￥]?\s*[\d,.]+", re.I)


@dataclass
class PageAnchor:
    page_no: int  # 0-based
    invoice_number: Optional[str]
    y0: float = 0.0
    y1: float = 0.0
    has_total: bool = False


@dataclass
class PdfSegmentSpec:
    page_start: int
    page_end: int
    region_bbox: Optional[tuple[float, float, float, float]] = None
    invoice_number: Optional[str] = None
    confidence: float = 1.0
    needs_review: bool = False


def _open_doc(path: str):
    import fitz
    return fitz.open(path)


def _find_invoice_numbers(text: str) -> list[str]:
    nums: list[str] = []
    for pat in (_RE_INVOICE_NO, _RE_INVOICE_NO_ALT):
        nums.extend(m.group(1).strip() for m in pat.finditer(text))
    return nums


def scan_pdf_anchors(path: str, max_pages: Optional[int] = None) -> list[PageAnchor]:
    doc = _open_doc(path)
    try:
        n = doc.page_count
        if max_pages is not None:
            n = min(n, max_pages)
        anchors: list[PageAnchor] = []
        for i in range(n):
            page = doc.load_page(i)
            text = page.get_text() or ""
            numbers = _find_invoice_numbers(text)
            has_total = bool(_RE_TOTAL.search(text))
            blocks = page.get_text("blocks") or []
            if numbers:
                for num in numbers:
                    y0, y1 = 0.0, page.rect.height
                    matched = [b for b in blocks if len(b) >= 5 and num in str(b[4])]
                    if matched:
                        y0 = min(float(b[1]) for b in matched)
                        y1 = max(float(b[3]) for b in matched)
                    anchors.append(PageAnchor(
                        page_no=i, invoice_number=num, y0=y0, y1=y1, has_total=has_total,
                    ))
        return anchors
    finally:
        doc.close()


def count_distinct_invoice_numbers(anchors: list[PageAnchor]) -> int:
    return len({a.invoice_number for a in anchors if a.invoice_number})


def _export_single_page_image(doc, page_no: int, segments_dir: str, clip=None) -> str:
    import fitz
    page = doc.load_page(page_no)
    mat = fitz.Matrix(1.5, 1.5)
    if clip is not None:
        pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
    else:
        pix = page.get_pixmap(matrix=mat, alpha=False)
    fname = f"p{page_no}_{uuid.uuid4().hex[:8]}.png"
    media = os.path.join(segments_dir, fname)
    pix.save(media)
    return media


def _export_page_range_pdf(doc, spec: PdfSegmentSpec, segments_dir: str) -> str:
    import fitz
    out_doc = fitz.open()
    try:
        for p in range(spec.page_start, spec.page_end + 1):
            out_doc.insert_pdf(doc, from_page=p, to_page=p)
        fname = f"p{spec.page_start}-{spec.page_end}_{uuid.uuid4().hex[:8]}.pdf"
        media = os.path.join(segments_dir, fname)
        out_doc.save(media)
        return media
    finally:
        out_doc.close()


def cluster_pdf_segments(
    path: str,
    anchors: list[PageAnchor],
    segments_dir: str,
) -> list[tuple[str, PdfSegmentSpec]]:
    import fitz

    distinct = count_distinct_invoice_numbers(anchors)
    if distinct < 2:
        return []

    doc = _open_doc(path)
    os.makedirs(segments_dir, exist_ok=True)
    out: list[tuple[str, PdfSegmentSpec]] = []

    try:
        by_page: dict[int, list[PageAnchor]] = {}
        for a in anchors:
            by_page.setdefault(a.page_no, []).append(a)

        specs: list[PdfSegmentSpec] = []

        # 1) 同页多票 → 区域裁剪
        for page_no, page_anchors in sorted(by_page.items()):
            numbered = [a for a in page_anchors if a.invoice_number]
            unique = {a.invoice_number for a in numbered}
            if len(unique) >= 2:
                page = doc.load_page(page_no)
                ph = page.rect.height
                sorted_a = sorted(numbered, key=lambda x: x.y0)
                for i, anc in enumerate(sorted_a):
                    y_top = sorted_a[i - 1].y1 if i > 0 else 0
                    y_bot = sorted_a[i + 1].y0 if i + 1 < len(sorted_a) else ph
                    clip = fitz.Rect(0, max(0, y_top - 10), page.rect.width, min(ph, y_bot + 10))
                    media = _export_single_page_image(doc, page_no, segments_dir, clip=clip)
                    spec = PdfSegmentSpec(
                        page_start=page_no, page_end=page_no,
                        region_bbox=(clip.x0, clip.y0, clip.x1, clip.y1),
                        invoice_number=anc.invoice_number, confidence=0.85,
                    )
                    out.append((media, spec))
                continue

            # 2) 单页单号
            if len(numbered) == 1:
                specs.append(PdfSegmentSpec(
                    page_start=page_no, page_end=page_no,
                    invoice_number=numbered[0].invoice_number, confidence=0.9,
                ))

        # 3) 跨页合并（相同发票号连续页）
        if specs:
            merged: list[PdfSegmentSpec] = []
            specs.sort(key=lambda s: s.page_start)
            i = 0
            while i < len(specs):
                cur = specs[i]
                j = i + 1
                while j < len(specs):
                    nxt = specs[j]
                    if (
                        cur.invoice_number
                        and cur.invoice_number == nxt.invoice_number
                        and nxt.page_start == cur.page_end + 1
                    ):
                        cur = PdfSegmentSpec(
                            page_start=cur.page_start, page_end=nxt.page_end,
                            invoice_number=cur.invoice_number, confidence=0.92,
                        )
                        j += 1
                    else:
                        break
                merged.append(cur)
                i = j
            specs = merged

        # 4) 导出未做区域裁剪的 specs
        region_pages = {s.page_start for _, s in out}
        for spec in specs:
            if spec.page_start in region_pages:
                continue
            if spec.page_start == spec.page_end:
                media = _export_single_page_image(doc, spec.page_start, segments_dir)
            else:
                media = _export_page_range_pdf(doc, spec, segments_dir)
            out.append((media, spec))

        # 5) 若仍无 segment：按页拆分（多页不同票、无文字层）
        if not out and doc.page_count >= 2:
            for p in range(doc.page_count):
                media = _export_single_page_image(doc, p, segments_dir)
                out.append((media, PdfSegmentSpec(
                    page_start=p, page_end=p, confidence=0.65, needs_review=True,
                )))

        return out
    finally:
        doc.close()


def should_split_pdf(path: str, split_enabled: bool) -> bool:
    if not split_enabled:
        return False
    anchors = scan_pdf_anchors(path, max_pages=None)
    return count_distinct_invoice_numbers(anchors) >= 2


def build_pdf_segments(
    path: str,
    segments_dir: str,
    split_enabled: bool = True,
) -> list[tuple[str, PdfSegmentSpec]]:
    if not split_enabled:
        return []
    anchors = scan_pdf_anchors(path, max_pages=None)
    if count_distinct_invoice_numbers(anchors) < 2:
        return []
    return cluster_pdf_segments(path, anchors, segments_dir)

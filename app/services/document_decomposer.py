"""
文档分解层：将上传文件拆为可独立抽取的 DocumentSegment。
单图/单票 PDF 走直通（Fast Path），多票 PDF / Word 多图才拆分。
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Optional

from app.models_runtime import load_models_config
from app.services.pdf_analyzer import PdfSegmentSpec, build_pdf_segments, should_split_pdf
from app.services.word_extractor import (
    extract_images_from_docx,
    resolve_docx_path,
)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".gif"}
PDF_EXTENSIONS = {".pdf"}
WORD_EXTENSIONS = {".docx", ".doc"}
PASSTHROUGH_EXTENSIONS = IMAGE_EXTENSIONS | {".ofd"} | PDF_EXTENSIONS | WORD_EXTENSIONS


@dataclass
class DocumentSegment:
    source_filename: str
    segment_index: int
    media_path: str
    segment_kind: str  # passthrough | pdf_page | pdf_region | word_image
    page_range: Optional[str] = None
    region_bbox: Optional[tuple[float, float, float, float]] = None
    invoice_number: Optional[str] = None
    confidence: float = 1.0
    needs_review: bool = False
    display_filename: Optional[str] = None


def _default_document_config() -> dict:
    return {
        "split_enabled": True,
        "pdf_process_all_pages": True,
        "min_images_for_word_split": 2,
    }


def load_document_config() -> dict:
    raw = load_models_config().get("document") or {}
    out = {**_default_document_config(), **raw}
    return out


def _format_page_range(spec: PdfSegmentSpec) -> str:
    if spec.page_start == spec.page_end:
        return str(spec.page_start + 1)
    return f"{spec.page_start + 1}-{spec.page_end + 1}"


def decompose(
    file_path: str,
    source_filename: str,
    segments_base_dir: str,
) -> list[DocumentSegment]:
    """
    分解上传文件为 segment 列表。
    单图/单票返回 1 个 segment（media_path 指向原文件或提取的单图）。
    """
    ext = os.path.splitext(file_path)[1].lower()
    cfg = load_document_config()
    split_enabled = bool(cfg.get("split_enabled", True))
    upload_id = uuid.uuid4().hex[:12]
    seg_dir = os.path.join(segments_base_dir, upload_id)
    os.makedirs(seg_dir, exist_ok=True)

    # ── 图片 / OFD：直通 ──
    if ext in IMAGE_EXTENSIONS or ext == ".ofd":
        return [DocumentSegment(
            source_filename=source_filename,
            segment_index=0,
            media_path=file_path,
            segment_kind="passthrough",
            display_filename=source_filename,
        )]

    # ── PDF ──
    if ext == ".pdf":
        if split_enabled and should_split_pdf(file_path, split_enabled=True):
            pairs = build_pdf_segments(file_path, seg_dir, split_enabled=True)
            if pairs:
                segments: list[DocumentSegment] = []
                for idx, (media, spec) in enumerate(pairs):
                    base = os.path.splitext(source_filename)[0]
                    pr = _format_page_range(spec)
                    disp = f"{base}#p{pr}{os.path.splitext(media)[1]}"
                    segments.append(DocumentSegment(
                        source_filename=source_filename,
                        segment_index=idx,
                        media_path=media,
                        segment_kind="pdf_region" if spec.region_bbox else "pdf_page",
                        page_range=pr,
                        region_bbox=spec.region_bbox,
                        invoice_number=spec.invoice_number,
                        confidence=spec.confidence,
                        needs_review=spec.needs_review,
                        display_filename=disp,
                    ))
                return segments
        return [DocumentSegment(
            source_filename=source_filename,
            segment_index=0,
            media_path=file_path,
            segment_kind="passthrough",
            display_filename=source_filename,
        )]

    # ── Word ──
    if ext in WORD_EXTENSIONS:
        docx_path, err = resolve_docx_path(file_path, seg_dir)
        if err:
            raise ValueError(err)
        images = extract_images_from_docx(docx_path, seg_dir)
        if not images:
            raise ValueError(f"Word 文档中未找到有效嵌入图片：{source_filename}")

        min_split = int(cfg.get("min_images_for_word_split", 2))
        if len(images) < min_split:
            media, idx = images[0]
            return [DocumentSegment(
                source_filename=source_filename,
                segment_index=0,
                media_path=media,
                segment_kind="word_image",
                display_filename=source_filename,
            )]

        segments = []
        base = os.path.splitext(source_filename)[0]
        for media, idx in images:
            ext_img = os.path.splitext(media)[1]
            segments.append(DocumentSegment(
                source_filename=source_filename,
                segment_index=idx,
                media_path=media,
                segment_kind="word_image",
                display_filename=f"{base}#图{idx + 1}{ext_img}",
            ))
        return segments

    raise ValueError(f"不支持的文件类型: {ext}")

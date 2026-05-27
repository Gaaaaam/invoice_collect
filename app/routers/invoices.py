import os
import shutil
import uuid
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import CollectionItem, Invoice
from app.paths import UPLOAD_DIR
from app.schemas import BatchDeleteInvoicesRequest, InvoiceResponse, MessageResponse
from app.services.document_decomposer import DocumentSegment, decompose
from app.services.extractor import get_extractor

os.makedirs(UPLOAD_DIR, exist_ok=True)
SEGMENTS_DIR = os.path.join(UPLOAD_DIR, "segments")
os.makedirs(SEGMENTS_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".ofd",
    ".docx", ".doc",
}

router = APIRouter(prefix="/api/invoices", tags=["invoices"])


def _allowed_file(filename: str) -> bool:
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_EXTENSIONS


def _apply_extracted(invoice: Invoice, data: dict, segment: Optional[DocumentSegment] = None) -> None:
    invoice.invoice_type = data.get("invoice_type")
    invoice.invoice_number = data.get("invoice_number")
    invoice.issue_date = data.get("issue_date")
    invoice.seller_name = data.get("seller_name")
    invoice.buyer_name = data.get("buyer_name")
    invoice.amount = data.get("amount")
    invoice.tax_amount = data.get("tax_amount")
    invoice.total_amount = data.get("total_amount")
    invoice.items_description = data.get("items_description")
    invoice.remarks = data.get("remarks")
    invoice.invoice_subcategory = data.get("invoice_subcategory")
    invoice.departure_city = data.get("departure_city")
    invoice.arrival_city = data.get("arrival_city")
    invoice.departure_time = data.get("departure_time")
    invoice.arrival_time = data.get("arrival_time")
    invoice.is_transport = bool(data.get("is_transport", False))
    if segment:
        meta = {
            "source_filename": segment.source_filename,
            "segment_index": segment.segment_index,
            "page_range": segment.page_range,
            "segment_kind": segment.segment_kind,
        }
        data = {**data, **{k: v for k, v in meta.items() if v is not None}}
    invoice.extracted_data = data
    if segment and segment.needs_review:
        invoice.needs_review = True
    elif not data.get("invoice_number") and not data.get("error"):
        invoice.needs_review = True


async def _create_invoice_from_segment(
    db: AsyncSession,
    segment: DocumentSegment,
    source_file_path: str,
    source_filename: str,
    ext: str,
) -> Invoice:
    display = segment.display_filename or source_filename
    invoice = Invoice(
        filename=display,
        file_path=segment.media_path,
        file_type=os.path.splitext(segment.media_path)[1].lstrip(".") or ext.lstrip("."),
        extract_status="pending",
        source_filename=source_filename,
        source_file_path=source_file_path,
        segment_index=segment.segment_index,
        page_range=segment.page_range,
        split_confidence=segment.confidence if segment.segment_kind != "passthrough" else None,
        needs_review=segment.needs_review,
    )
    db.add(invoice)
    await db.flush()

    try:
        extractor = get_extractor()
        extracted = await extractor.extract_from_file(
            segment.media_path,
            filename_hint=display,
        )
        _apply_extracted(invoice, extracted, segment)
        invoice.extract_status = "error" if extracted.get("error") else "done"
    except Exception as e:
        invoice.extract_status = "error"
        print(f"[invoices] extract error for {display}: {e}")
    return invoice


@router.post("/upload", response_model=list[InvoiceResponse])
async def upload_invoices(
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    """上传一张或多张发票文件，保存并触发 NuExtract 抽取。复合文档可拆分为多条 Invoice。"""
    results = []
    for file in files:
        if not _allowed_file(file.filename or ""):
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件类型：{file.filename}，仅支持 PDF/图片/OFD/Word",
            )

        ext = os.path.splitext(file.filename or "invoice")[1].lower()
        unique_name = f"{uuid.uuid4().hex}{ext}"
        save_path = os.path.join(UPLOAD_DIR, unique_name)
        source_filename = file.filename or unique_name

        with open(save_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        try:
            segments = decompose(save_path, source_filename, SEGMENTS_DIR)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        for segment in segments:
            invoice = await _create_invoice_from_segment(
                db, segment, save_path, source_filename, ext,
            )
            results.append(invoice)

    await db.commit()
    for inv in results:
        await db.refresh(inv)
    return results


@router.get("", response_model=list[InvoiceResponse])
async def list_invoices(
    extract_status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """列出所有已上传的发票"""
    stmt = select(Invoice).order_by(Invoice.uploaded_at.desc())
    if extract_status:
        stmt = stmt.where(Invoice.extract_status == extract_status)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/batch-delete", response_model=MessageResponse)
async def batch_delete_invoices(
    request: BatchDeleteInvoicesRequest,
    db: AsyncSession = Depends(get_db),
):
    """批量删除发票及其归集记录"""
    if not request.invoice_ids:
        raise HTTPException(status_code=400, detail="请至少选择一张发票")

    result = await db.execute(select(Invoice).where(Invoice.id.in_(request.invoice_ids)))
    invoices = list(result.scalars().all())
    if not invoices:
        raise HTTPException(status_code=404, detail="未找到可删除的发票")

    deleted = 0
    for invoice in invoices:
        await db.execute(delete(CollectionItem).where(CollectionItem.invoice_id == invoice.id))
        if os.path.exists(invoice.file_path):
            try:
                os.remove(invoice.file_path)
            except OSError:
                pass
        await db.delete(invoice)
        deleted += 1

    await db.commit()
    return MessageResponse(message=f"已删除 {deleted} 张发票")


@router.get("/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(invoice_id: int, db: AsyncSession = Depends(get_db)):
    invoice = await db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="发票不存在")
    return invoice


_MEDIA_TYPES: dict[str, str] = {
    ".pdf":  "application/pdf",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".bmp":  "image/bmp",
    ".tiff": "image/tiff",
    ".tif":  "image/tiff",
    ".ofd":  "application/ofd",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc":  "application/msword",
}


def _build_inline_content_disposition(filename: str, fallback_ext: str = "") -> str:
    """构造兼容中文文件名的 Content-Disposition（inline）头。"""
    safe_name = os.path.basename(filename or "").strip()
    if not safe_name:
        safe_name = f"invoice{fallback_ext}"

    ascii_fallback = safe_name.encode("ascii", "ignore").decode("ascii").strip()
    if not ascii_fallback:
        ascii_fallback = f"invoice{fallback_ext}"
    ascii_fallback = ascii_fallback.replace('"', "")

    utf8_name = quote(safe_name, safe="")
    return f'inline; filename="{ascii_fallback}"; filename*=UTF-8\'\'{utf8_name}'


@router.get("/{invoice_id}/file")
async def get_invoice_file(invoice_id: int, db: AsyncSession = Depends(get_db)):
    """返回发票片段/原件，用于前端预览。"""
    invoice = await db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="发票不存在")
    if not os.path.exists(invoice.file_path):
        raise HTTPException(status_code=404, detail="文件不存在，可能已被删除")

    ext = os.path.splitext(invoice.file_path)[1].lower()
    media_type = _MEDIA_TYPES.get(ext, "application/octet-stream")
    content_disposition = _build_inline_content_disposition(invoice.filename or "", ext)

    return FileResponse(
        invoice.file_path,
        media_type=media_type,
        headers={"Content-Disposition": content_disposition},
    )


@router.get("/{invoice_id}/source-file")
async def get_invoice_source_file(invoice_id: int, db: AsyncSession = Depends(get_db)):
    """返回拆分前的原始上传文件（若存在）。"""
    invoice = await db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="发票不存在")
    source_path = invoice.source_file_path or invoice.file_path
    if not os.path.exists(source_path):
        raise HTTPException(status_code=404, detail="原始文件不存在")
    ext = os.path.splitext(source_path)[1].lower()
    media_type = _MEDIA_TYPES.get(ext, "application/octet-stream")
    name = invoice.source_filename or invoice.filename
    content_disposition = _build_inline_content_disposition(name or "", ext)
    return FileResponse(
        source_path,
        media_type=media_type,
        headers={"Content-Disposition": content_disposition},
    )


@router.delete("/{invoice_id}", response_model=MessageResponse)
async def delete_invoice(invoice_id: int, db: AsyncSession = Depends(get_db)):
    invoice = await db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="发票不存在")

    await db.execute(delete(CollectionItem).where(CollectionItem.invoice_id == invoice_id))

    if os.path.exists(invoice.file_path):
        try:
            os.remove(invoice.file_path)
        except OSError:
            pass

    await db.delete(invoice)
    await db.commit()
    return MessageResponse(message=f"已删除发票：{invoice.filename}")


@router.post("/{invoice_id}/re-extract", response_model=InvoiceResponse)
async def re_extract_invoice(invoice_id: int, db: AsyncSession = Depends(get_db)):
    """对已上传发票重新执行 NuExtract 抽取"""
    invoice = await db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="发票不存在")

    try:
        extractor = get_extractor()
        extracted = await extractor.extract_from_file(
            invoice.file_path,
            filename_hint=invoice.filename,
        )
        _apply_extracted(invoice, extracted)
        if extracted.get("error"):
            invoice.extract_status = "error"
            await db.commit()
            await db.refresh(invoice)
            raise HTTPException(status_code=502, detail=f"抽取失败：{extracted.get('error')}")
        invoice.extract_status = "done"
    except HTTPException:
        raise
    except Exception as e:
        invoice.extract_status = "error"
        await db.commit()
        raise HTTPException(status_code=500, detail=f"抽取失败：{e}")

    await db.commit()
    await db.refresh(invoice)
    return invoice

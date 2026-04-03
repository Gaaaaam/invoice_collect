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
from app.schemas import BatchDeleteInvoicesRequest, InvoiceResponse, MessageResponse
from app.services.extractor import get_extractor

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".ofd"}

router = APIRouter(prefix="/api/invoices", tags=["invoices"])


def _allowed_file(filename: str) -> bool:
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_EXTENSIONS


@router.post("/upload", response_model=list[InvoiceResponse])
async def upload_invoices(
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    """上传一张或多张发票文件，保存并触发 NuExtract 抽取"""
    results = []
    for file in files:
        if not _allowed_file(file.filename or ""):
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件类型：{file.filename}，仅支持 PDF/图片/OFD",
            )

        ext = os.path.splitext(file.filename or "invoice")[1].lower()
        unique_name = f"{uuid.uuid4().hex}{ext}"
        save_path = os.path.join(UPLOAD_DIR, unique_name)

        with open(save_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        invoice = Invoice(
            filename=file.filename or unique_name,
            file_path=save_path,
            file_type=ext.lstrip("."),
            extract_status="pending",
        )
        db.add(invoice)
        await db.flush()

        # 异步抽取发票信息（NuExtract 与/或本地 OCR 保底）
        try:
            extractor = get_extractor()
            extracted = await extractor.extract_from_file(
                save_path,
                filename_hint=file.filename or unique_name,
            )
            _apply_extracted(invoice, extracted)
            invoice.extract_status = "error" if extracted.get("error") else "done"
        except Exception as e:
            invoice.extract_status = "error"
            print(f"[invoices] extract error for {file.filename}: {e}")

        results.append(invoice)

    await db.commit()
    for inv in results:
        await db.refresh(inv)
    return results


def _apply_extracted(invoice: Invoice, data: dict) -> None:
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
    invoice.extracted_data = data


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
    ".ofd":  "application/ofd",  # OFD 通常需要专用软件
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
    """返回发票原始文件，用于前端预览。"""
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


@router.delete("/{invoice_id}", response_model=MessageResponse)
async def delete_invoice(invoice_id: int, db: AsyncSession = Depends(get_db)):
    invoice = await db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="发票不存在")

    # 删除归集条目
    await db.execute(delete(CollectionItem).where(CollectionItem.invoice_id == invoice_id))

    # 删除文件
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

import asyncio
import json
import logging
import traceback
from datetime import datetime
from typing import Optional

import yaml
import os
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, AsyncSessionLocal
from app.models import CollectionGroup, CollectionItem, Invoice
from app.schemas import (
    BatchMoveInvoicesRequest,
    CategoryResult,
    CollectionGroupResponse,
    CollectionResult,
    CreateGroupRequest,
    MessageResponse,
    MoveInvoiceRequest,
    ProcessRequest,
    UpdateGroupRequest,
)
from app.services.classifier import classify_invoice
from app.services.grouper import (
    build_meeting_group_name,
    build_travel_group_name,
    detect_travel_loops,
    group_meeting_invoices,
)
from app.services.progress import ProgressEvent, progress_manager

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CATEGORIES_CONFIG_PATH = os.path.join(BASE_DIR, "config", "categories.yml")

router = APIRouter(prefix="/api/collections", tags=["collections"])
logger = logging.getLogger(__name__)


def _load_categories() -> list[dict]:
    with open(CATEGORIES_CONFIG_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    categories = data.get("categories", [])
    normalized: list[dict] = []
    for cat in categories:
        item = dict(cat or {})
        cid = str(item.get("id") or "").strip()
        item["id"] = cid
        # 业务约束：除“其他费用”外默认支持分组
        item["groupable"] = False if cid == "other" else bool(item.get("groupable", True))
        normalized.append(item)
    return normalized


def _invoice_to_dict(inv: Invoice) -> dict:
    extracted = inv.extracted_data if isinstance(inv.extracted_data, dict) else {}
    type_detected = extracted.get("invoice_type_detected")
    train_number = extracted.get("train_number")
    return {
        "id": inv.id,
        "invoice_type": inv.invoice_type,
        "invoice_number": inv.invoice_number,
        "issue_date": inv.issue_date,
        "seller_name": inv.seller_name,
        "buyer_name": inv.buyer_name,
        "amount": inv.amount,
        "tax_amount": inv.tax_amount,
        "total_amount": inv.total_amount,
        "items_description": inv.items_description,
        "invoice_subcategory": inv.invoice_subcategory,
        "invoice_type_detected": type_detected,
        "train_number": train_number,
        "departure_city": inv.departure_city,
        "arrival_city": inv.arrival_city,
        "departure_time": inv.departure_time,
        "arrival_time": inv.arrival_time,
        "is_transport": inv.is_transport,
        "filename": inv.filename,
    }


@router.post("/process", response_model=MessageResponse)
async def process_collection(request: ProcessRequest):
    """
    启动归集后台任务，立即返回 task_id。
    客户端通过 GET /api/collections/stream/{task_id} 订阅 SSE 进度流。
    """
    task_id = progress_manager.create_task()
    asyncio.create_task(_run_collection(task_id, request))
    return MessageResponse(message="归集任务已启动", data={"task_id": task_id})


@router.get("/stream/{task_id}")
async def stream_progress(task_id: str):
    """SSE 端点：实时推送归集进度到前端"""
    async def event_generator():
        async for event in progress_manager.subscribe(task_id):
            payload = json.dumps({
                "step": event.step,
                "total": event.total,
                "percent": event.percent,
                "title": event.title,
                "message": event.message,
                "status": event.status,
                "error": event.error,
            }, ensure_ascii=False)
            yield f"data: {payload}\n\n"
            if event.status in ("done", "error"):
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _run_collection(task_id: str, request: ProcessRequest) -> None:
    """实际归集逻辑，运行在后台 asyncio 任务中，通过 progress_manager 推送进度"""

    async def emit(step, total, percent, title, message, status="running", error=None):
        await progress_manager.emit(task_id, ProgressEvent(
            step=step, total=total, percent=percent,
            title=title, message=message, status=status, error=error,
        ))

    async with AsyncSessionLocal() as db:
        try:
            # ── Step 0: 查询待处理发票 ────────────────────────────────────────
            await emit(0, 5, 5, "准备中", "正在查询待处理发票…")

            if request.invoice_ids:
                stmt = select(Invoice).where(Invoice.id.in_(request.invoice_ids))
            else:
                stmt = select(Invoice).where(Invoice.extract_status == "done")

            result = await db.execute(stmt)
            invoices: list[Invoice] = list(result.scalars().all())

            if not invoices:
                await emit(0, 5, 100, "无可处理发票",
                           "没有找到已完成信息抽取的发票，请先上传发票并等待抽取完成。",
                           status="error",
                           error="没有可处理的发票（extract_status=done）")
                return

            total_inv = len(invoices)
            await emit(0, 5, 8, "准备中", f"共找到 {total_inv} 张可处理发票")

            logger.info(
                "collection task_id=%s invoices=%s force_reclassify=%s use_subcategory=%s use_rules=%s use_llm=%s",
                task_id,
                total_inv,
                request.force_reclassify,
                request.use_subcategory,
                request.use_rules,
                request.use_llm,
            )

            categories = _load_categories()
            category_map = {c["id"]: c for c in categories}

            # ── Step 1: 发票分类（规则 + LLM）────────────────────────────────
            await emit(1, 5, 10, "发票分类", f"开始对 {total_inv} 张发票进行分类…")

            category_buckets: dict[str, list[Invoice]] = {c["id"]: [] for c in categories}
            category_buckets["unclassified"] = []

            existing_ids_result = await db.execute(select(CollectionItem.invoice_id))
            existing_classified_ids = {row[0] for row in existing_ids_result.fetchall()}

            classify_step_range = (10, 65)  # 分类阶段占 10%~65%
            rule_count = llm_count = skip_count = 0

            for i, inv in enumerate(invoices):
                pct = classify_step_range[0] + int(
                    (i / total_inv) * (classify_step_range[1] - classify_step_range[0])
                )
                if request.force_reclassify or inv.id not in existing_classified_ids:
                    inv_dict = _invoice_to_dict(inv)
                    cat_id, classified_by = await classify_invoice(
                        inv_dict,
                        use_subcategory=request.use_subcategory,
                        use_rules=request.use_rules,
                        use_llm=request.use_llm,
                    )
                    if cat_id not in category_map:
                        cat_id = "other"
                    category_buckets.setdefault(cat_id, []).append(inv)

                    if classified_by == "rule":
                        rule_count += 1
                    elif classified_by == "llm":
                        llm_count += 1

                    await db.execute(
                        delete(CollectionItem).where(CollectionItem.invoice_id == inv.id)
                    )
                    item = CollectionItem(
                        invoice_id=inv.id,
                        category_id=cat_id,
                        classified_by=classified_by,
                        classified_at=datetime.utcnow(),
                    )
                    db.add(item)
                    method_label = "规则匹配" if classified_by == "rule" else ("AI判别" if classified_by == "llm" else "默认归类")
                    cat_name = category_map.get(cat_id, {}).get("name", cat_id)
                    await emit(1, 5, pct, "发票分类",
                               f"[{i+1}/{total_inv}] {inv.filename or inv.id} → {cat_name}（{method_label}）")
                else:
                    skip_count += 1
                    result2 = await db.execute(
                        select(CollectionItem).where(CollectionItem.invoice_id == inv.id)
                    )
                    existing = result2.scalar_one_or_none()
                    if existing:
                        category_buckets.setdefault(existing.category_id, []).append(inv)
                    await emit(1, 5, pct, "发票分类",
                               f"[{i+1}/{total_inv}] {inv.filename or inv.id} — 已有分类，跳过")

            await db.flush()

            summary = f"分类完成：规则匹配 {rule_count} 张，AI判别 {llm_count} 张，跳过 {skip_count} 张"
            await emit(1, 5, 65, "发票分类", summary)
            logger.info("collection task_id=%s classify_summary %s", task_id, summary)

            # ── Step 2: 差旅闭环分组 ──────────────────────────────────────────
            travel_invoices = category_buckets.get("travel", [])
            await emit(2, 5, 68, "差旅闭环分组",
                       f"共 {len(travel_invoices)} 张差旅费发票，开始检测闭环…")

            if travel_invoices:
                travel_dicts = [_invoice_to_dict(inv) for inv in travel_invoices]
                loops = detect_travel_loops(travel_dicts)

                old_groups = await db.execute(
                    select(CollectionGroup).where(CollectionGroup.category_id == "travel")
                )
                for g in old_groups.scalars().all():
                    await db.delete(g)
                await db.flush()

                closed = sum(1 for l in loops if l.is_closed)
                await emit(2, 5, 72, "差旅闭环分组",
                           f"检测到 {len(loops)} 个行程组（其中 {closed} 个完整闭环）")
                logger.info(
                    "collection task_id=%s travel_groups=%s closed_loops=%s",
                    task_id,
                    len(loops),
                    closed,
                )

                for idx, loop in enumerate(loops):
                    group = CollectionGroup(
                        name=build_travel_group_name(loop, idx),
                        category_id="travel",
                        group_type="travel_loop",
                        start_date=loop.start_date.isoformat() if loop.start_date else None,
                        end_date=loop.end_date.isoformat() if loop.end_date else None,
                        sort_order=idx,
                    )
                    db.add(group)
                    await db.flush()

                    for inv_id in loop.all_invoice_ids:
                        ci_result = await db.execute(
                            select(CollectionItem).where(CollectionItem.invoice_id == inv_id)
                        )
                        ci = ci_result.scalar_one_or_none()
                        if ci:
                            ci.group_id = group.id

                    loop_label = "✓闭环" if loop.is_closed else "行程"
                    await emit(2, 5, 72 + idx, "差旅闭环分组",
                               f"  {loop_label}：{group.name}（{len(loop.all_invoice_ids)} 张）")
            else:
                await emit(2, 5, 75, "差旅闭环分组", "无差旅费发票，跳过")

            # ── Step 3: 会议分组 ──────────────────────────────────────────────
            meeting_invoices = category_buckets.get("meeting", [])
            await emit(3, 5, 78, "会议分组",
                       f"共 {len(meeting_invoices)} 张会议费发票，开始分组…")

            if meeting_invoices:
                meeting_dicts = [_invoice_to_dict(inv) for inv in meeting_invoices]
                meeting_groups = await group_meeting_invoices(meeting_dicts)

                old_groups = await db.execute(
                    select(CollectionGroup).where(CollectionGroup.category_id == "meeting")
                )
                for g in old_groups.scalars().all():
                    await db.delete(g)
                await db.flush()

                inv_id_to_dict = {inv.id: _invoice_to_dict(inv) for inv in meeting_invoices}
                for idx, id_list in enumerate(meeting_groups):
                    group_inv_dicts = [inv_id_to_dict[i] for i in id_list if i in inv_id_to_dict]
                    group = CollectionGroup(
                        name=build_meeting_group_name(group_inv_dicts, idx),
                        category_id="meeting",
                        group_type="meeting",
                        sort_order=idx,
                    )
                    db.add(group)
                    await db.flush()

                    for inv_id in id_list:
                        ci_result = await db.execute(
                            select(CollectionItem).where(CollectionItem.invoice_id == inv_id)
                        )
                        ci = ci_result.scalar_one_or_none()
                        if ci:
                            ci.group_id = group.id

                await emit(3, 5, 88, "会议分组",
                           f"识别出 {len(meeting_groups)} 个会议分组")
                logger.info(
                    "collection task_id=%s meeting_groups=%s",
                    task_id,
                    len(meeting_groups),
                )
            else:
                await emit(3, 5, 88, "会议分组", "无会议费发票，跳过")

            # ── Step 4: 提交保存 ──────────────────────────────────────────────
            await emit(4, 5, 92, "保存结果", "正在写入数据库…")
            await db.commit()
            await emit(4, 5, 98, "保存结果", "数据库写入完成")

            # ── Done ──────────────────────────────────────────────────────────
            await emit(5, 5, 100,
                       "归集完成",
                       f"成功处理 {total_inv} 张发票，结果已更新",
                       status="done")
            logger.info(
                "collection task_id=%s done processed_invoices=%s",
                task_id,
                total_inv,
            )

        except Exception as exc:
            tb = traceback.format_exc()
            logger.exception("collection task_id=%s failed: %s", task_id, exc)
            await progress_manager.emit(task_id, ProgressEvent(
                step=0, total=5, percent=0,
                title="归集出错",
                message=str(exc),
                status="error",
                error=tb,
            ))



@router.get("/result", response_model=CollectionResult)
async def get_collection_result(db: AsyncSession = Depends(get_db)):
    """获取当前归集结果"""
    categories = _load_categories()
    category_map = {c["id"]: c for c in categories}

    all_items_result = await db.execute(
        select(CollectionItem)
    )
    all_items: list[CollectionItem] = list(all_items_result.scalars().all())

    all_invoices_result = await db.execute(select(Invoice))
    all_invoices: dict[int, Invoice] = {
        inv.id: inv for inv in all_invoices_result.scalars().all()
    }

    all_groups_result = await db.execute(
        select(CollectionGroup).order_by(CollectionGroup.sort_order)
    )
    all_groups: dict[int, CollectionGroup] = {
        g.id: g for g in all_groups_result.scalars().all()
    }

    # 按大类归集
    cat_items: dict[str, list[CollectionItem]] = {c["id"]: [] for c in categories}
    classified_invoice_ids: set[int] = set()
    for item in all_items:
        cat_items.setdefault(item.category_id, []).append(item)
        classified_invoice_ids.add(item.invoice_id)

    result_categories: list[CategoryResult] = []
    for cat in categories:
        cat_id = cat["id"]
        groupable = cat.get("groupable", False)
        items = cat_items.get(cat_id, [])

        # 按分组整理
        groups_dict: dict[int, list[Invoice]] = {}
        ungrouped: list[Invoice] = []
        total_amount = 0.0

        for item in items:
            inv = all_invoices.get(item.invoice_id)
            if not inv:
                continue
            total_amount += inv.total_amount or 0.0
            if groupable and item.group_id:
                groups_dict.setdefault(item.group_id, []).append(inv)
            else:
                ungrouped.append(inv)

        group_responses: list[CollectionGroupResponse] = []
        for gid, ginvs in groups_dict.items():
            grp = all_groups.get(gid)
            if not grp:
                continue
            group_responses.append(
                CollectionGroupResponse(
                    id=grp.id,
                    name=grp.name,
                    category_id=grp.category_id,
                    group_type=grp.group_type,
                    description=grp.description,
                    start_date=grp.start_date,
                    end_date=grp.end_date,
                    sort_order=grp.sort_order,
                    invoices=ginvs,
                )
            )
        group_responses.sort(key=lambda g: g.sort_order)

        result_categories.append(
            CategoryResult(
                category_id=cat_id,
                category_name=cat["name"],
                groupable=groupable,
                groups=group_responses,
                ungrouped_invoices=ungrouped,
                total_amount=round(total_amount, 2),
            )
        )

    unclassified = [
        inv for inv in all_invoices.values()
        if inv.id not in classified_invoice_ids
    ]

    return CollectionResult(
        categories=result_categories,
        unclassified_invoices=unclassified,
        processed_at=datetime.utcnow(),
    )


@router.patch("/move", response_model=MessageResponse)
async def move_invoice(
    request: MoveInvoiceRequest,
    db: AsyncSession = Depends(get_db),
):
    """拖拽操作：将发票移动到指定大类和分组"""
    categories = _load_categories()
    valid_cat_ids = {c["id"] for c in categories}
    if request.target_category_id not in valid_cat_ids:
        raise HTTPException(status_code=400, detail="无效的费用大类")

    # 验证目标分组存在（如有）
    if request.target_group_id is not None:
        grp = await db.get(CollectionGroup, request.target_group_id)
        if not grp:
            raise HTTPException(status_code=404, detail="目标分组不存在")
        if grp.category_id != request.target_category_id:
            raise HTTPException(status_code=400, detail="目标分组与大类不匹配")

    result = await db.execute(
        select(CollectionItem).where(CollectionItem.invoice_id == request.invoice_id)
    )
    item = result.scalar_one_or_none()

    if item:
        item.category_id = request.target_category_id
        item.group_id = request.target_group_id
        item.classified_by = "manual"
        item.classified_at = datetime.utcnow()
        if request.note:
            item.note = request.note
    else:
        item = CollectionItem(
            invoice_id=request.invoice_id,
            category_id=request.target_category_id,
            group_id=request.target_group_id,
            classified_by="manual",
            classified_at=datetime.utcnow(),
            note=request.note,
        )
        db.add(item)

    await db.commit()
    return MessageResponse(message="发票归属已更新")


@router.patch("/move/batch", response_model=MessageResponse)
async def move_invoices_batch(
    request: BatchMoveInvoicesRequest,
    db: AsyncSession = Depends(get_db),
):
    """批量拖拽：将多张发票移动到指定大类和分组"""
    if not request.invoice_ids:
        raise HTTPException(status_code=400, detail="请至少选择一张发票")

    categories = _load_categories()
    valid_cat_ids = {c["id"] for c in categories}
    if request.target_category_id not in valid_cat_ids:
        raise HTTPException(status_code=400, detail="无效的费用大类")

    # 验证目标分组存在（如有）
    if request.target_group_id is not None:
        grp = await db.get(CollectionGroup, request.target_group_id)
        if not grp:
            raise HTTPException(status_code=404, detail="目标分组不存在")
        if grp.category_id != request.target_category_id:
            raise HTTPException(status_code=400, detail="目标分组与大类不匹配")

    # 仅处理真实存在的发票 ID
    inv_result = await db.execute(
        select(Invoice.id).where(Invoice.id.in_(request.invoice_ids))
    )
    existing_ids = {row[0] for row in inv_result.fetchall()}
    if not existing_ids:
        raise HTTPException(status_code=404, detail="未找到可移动的发票")

    item_result = await db.execute(
        select(CollectionItem).where(CollectionItem.invoice_id.in_(existing_ids))
    )
    item_map = {item.invoice_id: item for item in item_result.scalars().all()}

    moved = 0
    for invoice_id in existing_ids:
        item = item_map.get(invoice_id)
        if item:
            item.category_id = request.target_category_id
            item.group_id = request.target_group_id
            item.classified_by = "manual"
            item.classified_at = datetime.utcnow()
            if request.note:
                item.note = request.note
        else:
            db.add(
                CollectionItem(
                    invoice_id=invoice_id,
                    category_id=request.target_category_id,
                    group_id=request.target_group_id,
                    classified_by="manual",
                    classified_at=datetime.utcnow(),
                    note=request.note,
                )
            )
        moved += 1

    await db.commit()
    return MessageResponse(message=f"已批量更新 {moved} 张发票归属")


@router.post("/groups", response_model=CollectionGroupResponse)
async def create_group(
    request: CreateGroupRequest,
    db: AsyncSession = Depends(get_db),
):
    """手动新建一个归集分组"""
    group = CollectionGroup(
        name=request.name,
        category_id=request.category_id,
        group_type=request.group_type,
        description=request.description,
        sort_order=0,
    )
    db.add(group)
    await db.commit()
    await db.refresh(group)
    return CollectionGroupResponse(
        id=group.id,
        name=group.name,
        category_id=group.category_id,
        group_type=group.group_type,
        description=group.description,
        start_date=group.start_date,
        end_date=group.end_date,
        sort_order=group.sort_order,
        invoices=[],
    )


@router.patch("/groups/{group_id}", response_model=MessageResponse)
async def update_group(
    group_id: int,
    request: UpdateGroupRequest,
    db: AsyncSession = Depends(get_db),
):
    """修改分组名称或描述"""
    grp = await db.get(CollectionGroup, group_id)
    if not grp:
        raise HTTPException(status_code=404, detail="分组不存在")
    if request.name is not None:
        grp.name = request.name
    if request.description is not None:
        grp.description = request.description
    await db.commit()
    return MessageResponse(message="分组已更新")


@router.delete("/groups/{group_id}", response_model=MessageResponse)
async def delete_group(
    group_id: int,
    db: AsyncSession = Depends(get_db),
):
    """删除分组（组内发票变为未分组状态）"""
    grp = await db.get(CollectionGroup, group_id)
    if not grp:
        raise HTTPException(status_code=404, detail="分组不存在")

    # 将组内发票的 group_id 清空
    items_result = await db.execute(
        select(CollectionItem).where(CollectionItem.group_id == group_id)
    )
    for item in items_result.scalars().all():
        item.group_id = None

    await db.delete(grp)
    await db.commit()
    return MessageResponse(message="分组已删除")

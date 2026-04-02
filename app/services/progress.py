"""
进度管理器：通过 asyncio.Queue 为每个归集任务维护一个事件队列，
供 SSE 端点消费并推送到前端。
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ProgressEvent:
    step: int               # 当前步骤序号（0-based）
    total: int              # 总步骤数
    percent: int            # 0~100
    title: str              # 当前步骤标题
    message: str            # 详细描述
    status: str = "running" # running / done / error / cancelled
    error: Optional[str] = None  # 仅 status=error 时有值


class ProgressManager:
    """单例进度管理器，维护 task_id → asyncio.Queue 的映射"""

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[ProgressEvent]] = {}
        self._cancelled_tasks: set[str] = set()

    def create_task(self) -> str:
        task_id = uuid.uuid4().hex
        self._queues[task_id] = asyncio.Queue()
        return task_id

    def request_cancel(self, task_id: str) -> bool:
        """请求取消进行中的归集任务。若任务不存在或已结束则返回 False。"""
        if task_id not in self._queues:
            return False
        self._cancelled_tasks.add(task_id)
        return True

    def is_cancelled(self, task_id: str) -> bool:
        return task_id in self._cancelled_tasks

    async def emit(self, task_id: str, event: ProgressEvent) -> None:
        queue = self._queues.get(task_id)
        if queue is not None:
            await queue.put(event)

    async def subscribe(self, task_id: str):
        """异步生成器，逐个 yield 事件直到 done/error"""
        queue = self._queues.get(task_id)
        if queue is None:
            return
        try:
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=120)
                yield event
                if event.status in ("done", "error", "cancelled"):
                    break
        except asyncio.TimeoutError:
            yield ProgressEvent(
                step=0, total=1, percent=0,
                title="超时", message="任务超时，请重试",
                status="error", error="任务等待超时（120s）",
            )
        finally:
            self._queues.pop(task_id, None)
            self._cancelled_tasks.discard(task_id)

    def cleanup(self, task_id: str) -> None:
        self._queues.pop(task_id, None)


# 全局单例
progress_manager = ProgressManager()

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
import os
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATABASE_URL = f"sqlite+aiosqlite:///{os.path.join(BASE_DIR, 'invoice_collect.db')}"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    from app.models import Invoice, CollectionGroup, CollectionItem  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # 对已存在的数据库做增量迁移（新增列）
        await conn.run_sync(_migrate_add_columns)


def _migrate_add_columns(conn):
    """为旧数据库补充新增字段，使用 ALTER TABLE ... ADD COLUMN IF NOT EXISTS 风格。"""
    migrations = [
        "ALTER TABLE invoices ADD COLUMN remarks TEXT",
        "ALTER TABLE invoices ADD COLUMN invoice_subcategory VARCHAR(50)",
    ]
    for sql in migrations:
        try:
            conn.execute(text(sql))
        except Exception as e:
            # 列已存在时 SQLite 会报 "duplicate column name"；其余错误保留并抛出
            if "duplicate column name" in str(e).lower():
                continue
            logger.exception("数据库迁移失败: %s", sql)
            raise

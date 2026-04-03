"""应用路径。设置 INVOICE_COLLECT_DATA_DIR 可将数据库与上传目录放到同一持久化卷（如 Docker）。"""
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _data_dir() -> str:
    raw = os.environ.get("INVOICE_COLLECT_DATA_DIR", PROJECT_ROOT)
    return os.path.abspath(raw)


DATA_DIR = _data_dir()
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
DATABASE_FILE = os.path.join(DATA_DIR, "invoice_collect.db")

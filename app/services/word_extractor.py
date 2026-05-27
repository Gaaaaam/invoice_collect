"""
Word 文档内嵌图片提取（.docx / .doc）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from typing import Optional

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

MIN_IMAGE_WIDTH = 200
MIN_IMAGE_HEIGHT = 200
MIN_IMAGE_BYTES = 10 * 1024

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tiff", ".tif", ".webp"}


def _image_dimensions(path: str) -> tuple[int, int]:
    if not HAS_PIL:
        try:
            size = os.path.getsize(path)
            if size < MIN_IMAGE_BYTES:
                return 0, 0
            return MIN_IMAGE_WIDTH, MIN_IMAGE_HEIGHT
        except OSError:
            return 0, 0
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:
        return 0, 0


def _is_valid_invoice_image(path: str) -> bool:
    if not os.path.isfile(path):
        return False
    if os.path.getsize(path) < MIN_IMAGE_BYTES:
        return False
    w, h = _image_dimensions(path)
    if w == 0 and h == 0:
        return os.path.getsize(path) >= MIN_IMAGE_BYTES * 2
    return w >= MIN_IMAGE_WIDTH or h >= MIN_IMAGE_HEIGHT


def convert_doc_to_docx(doc_path: str, out_dir: str) -> Optional[str]:
    """使用 LibreOffice 将 .doc 转为 .docx。"""
    soffice = _find_soffice()
    if not soffice:
        return None
    os.makedirs(out_dir, exist_ok=True)
    try:
        subprocess.run(
            [
                soffice,
                "--headless",
                "--convert-to",
                "docx",
                "--outdir",
                out_dir,
                doc_path,
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None
    base = os.path.splitext(os.path.basename(doc_path))[0]
    candidate = os.path.join(out_dir, base + ".docx")
    return candidate if os.path.isfile(candidate) else None


def _find_soffice() -> Optional[str]:
    candidates = [
        os.environ.get("LIBREOFFICE_PATH"),
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/usr/bin/soffice",
        "/usr/bin/libreoffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return shutil.which("soffice") or shutil.which("libreoffice")


def resolve_docx_path(file_path: str, work_dir: str) -> tuple[str, Optional[str]]:
    """
    返回可用于提取的 docx 路径。
    第二项为错误信息（仅 .doc 转换失败时）。
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".docx":
        return file_path, None
    if ext == ".doc":
        converted = convert_doc_to_docx(file_path, work_dir)
        if converted:
            return converted, None
        return file_path, (
            "无法解析 .doc 文件：未检测到 LibreOffice。"
            "请将文件另存为 .docx 后上传，或安装 LibreOffice。"
        )
    return file_path, f"不支持的 Word 格式: {ext}"


def extract_images_from_docx(docx_path: str, out_dir: str) -> list[tuple[str, int]]:
    """
    从 docx 提取嵌入图片。
    返回 [(image_path, index), ...] 按文档内顺序。
    """
    os.makedirs(out_dir, exist_ok=True)
    results: list[tuple[str, int]] = []
    index = 0

    # 优先：解压 word/media/
    try:
        with zipfile.ZipFile(docx_path, "r") as zf:
            media_files = sorted(
                n for n in zf.namelist()
                if n.startswith("word/media/") and os.path.splitext(n)[1].lower() in IMAGE_EXTENSIONS
            )
            for name in media_files:
                ext = os.path.splitext(name)[1].lower() or ".png"
                out_name = f"img_{index}_{uuid.uuid4().hex[:8]}{ext}"
                out_path = os.path.join(out_dir, out_name)
                with zf.open(name) as src, open(out_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                if _is_valid_invoice_image(out_path):
                    results.append((out_path, index))
                    index += 1
                else:
                    try:
                        os.unlink(out_path)
                    except OSError:
                        pass
    except zipfile.BadZipFile:
        pass

    if results:
        return results

    # 备选：python-docx inline shapes
    try:
        from docx import Document
        from docx.opc.constants import RELATIONSHIP_TYPE as RT

        doc = Document(docx_path)
        for rel in doc.part.rels.values():
            if "image" not in rel.reltype and rel.reltype != RT.IMAGE:
                continue
            try:
                blob = rel.target_part.blob
            except Exception:
                continue
            ext = os.path.splitext(rel.target_ref)[1].lower() or ".png"
            out_path = os.path.join(out_dir, f"img_{index}_{uuid.uuid4().hex[:8]}{ext}")
            with open(out_path, "wb") as f:
                f.write(blob)
            if _is_valid_invoice_image(out_path):
                results.append((out_path, index))
                index += 1
            else:
                try:
                    os.unlink(out_path)
                except OSError:
                    pass
    except ImportError:
        pass
    except Exception:
        pass

    return results

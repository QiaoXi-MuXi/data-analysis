# -*- coding: utf-8 -*-
"""只读单页PDF渲染；限制内存并串行调用PDFium。"""
from dataclasses import dataclass
import math
from pathlib import Path
from threading import Lock

from PySide6.QtGui import QImage

_PDF_LOCK = Lock()
MAX_BYTES = 64 * 1024 * 1024
MAX_EDGE = 1800


@dataclass(frozen=True)
class PdfFrame:
    image: QImage
    page: int
    count: int


def render_pdf(path, page_number=1):
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('PDF超过64MB预览上限，请使用外部打开。')
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('PDF超过64MB预览上限，请使用外部打开。')
    # PDFium跨文档也不可并发；所有GUI页面共用同一把锁。
    with _PDF_LOCK:
        import pypdfium2 as pdfium
        document = page = bitmap = None
        try:
            document = pdfium.PdfDocument(raw)
            document.init_forms()
            count = len(document)
            if count == 0:
                raise ValueError('PDF没有可显示的页面。')
            if not 1 <= page_number <= count:
                raise ValueError(f'页码应在1至{count}之间。')
            page = document[page_number - 1]
            width, height = page.get_size()
            if not all(math.isfinite(v) and v > 0 for v in (width, height)):
                raise ValueError('PDF页面尺寸无效，请使用外部打开。')
            scale = min(2.0, (MAX_EDGE - 1) / max(width, height))
            bitmap = page.render(scale=scale, rev_byteorder=True)
            with bitmap.to_pil() as image, image.convert('RGB') as rgb:
                # 独立复制，返回后不持有已关闭的PDFium位图缓冲区。
                result = QImage(rgb.tobytes(), rgb.width, rgb.height, rgb.width * 3,
                                QImage.Format.Format_RGB888).copy()
            if result.isNull():
                raise ValueError('PDF页面渲染失败，请使用外部打开。')
            return PdfFrame(result, page_number, count)
        except pdfium.PdfiumError as exc:
            raise ValueError('PDF无法预览，可能已损坏、加密或不受支持；请使用外部打开。') from exc
        finally:
            if bitmap is not None:
                bitmap.close()
            if page is not None:
                page.close()
            if document is not None:
                document.close()

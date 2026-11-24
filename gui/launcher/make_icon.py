# -*- coding: utf-8 -*-
"""由同一SVG生成资源管理器所需的多尺寸ICO。"""
import io
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

assets = Path(__file__).resolve().parents[1] / 'assets'
renderer = QSvgRenderer(str(assets / 'app.svg'))
if not renderer.isValid():
    raise ValueError('应用SVG图标无效')
image = QImage(256, 256, QImage.Format.Format_ARGB32)
image.fill(0)
painter = QPainter(image)
renderer.render(painter)
painter.end()
data = QByteArray()
buffer = QBuffer(data)
buffer.open(QIODevice.OpenModeFlag.WriteOnly)
if not image.save(buffer, 'PNG'):
    raise RuntimeError('应用图标渲染失败')
with Image.open(io.BytesIO(bytes(data))) as raster:
    raster.save(assets / 'app.ico', sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])
print('已生成 gui/assets/app.ico')

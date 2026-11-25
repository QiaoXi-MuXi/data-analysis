# -*- coding: utf-8 -*-
"""把 data 目录里的发票图片和 PDF 合并成一个每页统一 A4 的多页 PDF，便于整批打印。"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageOps

BASE = Path(__file__).resolve().parent
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff', '.webp'}
PDF_EXTS = {'.pdf'}
ALL_EXTS = IMAGE_EXTS | PDF_EXTS
# A4 横向按 150 DPI 的像素尺寸；统一画布，保证每页大小一致。
A4_SIZE = (1754, 1240)
RESOLUTION = 150.0


def natural_key(name):
    """文件名自然排序：2-1 排在 10-1 前，数字段按整数比较。"""
    text = str(name)
    key = []
    for part in re.split(r"(\d+)", text):
        if part.isdigit():
            key.append((0, int(part), len(part)))
        else:
            key.append((1, part.casefold()))
    return (key, text.casefold())


def collect_files(folder):
    """只取图片和 PDF，按文件名自然排序。"""
    if not folder.is_dir():
        raise ValueError(f'输入目录不存在：{folder}')
    files = sorted((p for p in folder.iterdir()
                    if p.is_file() and p.suffix.lower() in ALL_EXTS),
                   key=lambda p: natural_key(p.name))
    if not files:
        raise ValueError(f'目录里没有图片或 PDF：{folder}')
    return files


def iter_pages(files):
    """逐个产出图片页；PDF 每页渲染为图片，保持输入顺序。"""
    for path in files:
        if path.suffix.lower() in PDF_EXTS:
            doc = pdfium.PdfDocument(str(path))
            try:
                for i in range(len(doc)):
                    yield doc[i].render(scale=RESOLUTION / 72).to_pil().convert('RGB')
            finally:
                doc.close()
        else:
            with Image.open(path) as raw:
                yield ImageOps.exif_transpose(raw).convert('RGB')


def build_pdf(files, output):
    """把完整内容缩放到 A4 横向画布，铺满页面且不裁剪边缘。"""
    pages = []
    for img in iter_pages(files):
        pages.append(img.resize(A4_SIZE, Image.LANCZOS))
    output.parent.mkdir(parents=True, exist_ok=True)
    pages[0].save(output, 'PDF', save_all=True, append_images=pages[1:],
                  resolution=RESOLUTION)
    return len(pages)


def main():
    parser = argparse.ArgumentParser(description='把图片和 PDF 合并成每页 A4 横向的 PDF。')
    parser.add_argument('--input', type=Path, default=BASE / 'data',
                        help='输入目录，默认 data')
    parser.add_argument('--output', type=Path,
                        help='输出 PDF，默认 ER/物品验收单_时间戳.pdf')
    parser.add_argument('--no-pause', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()

    folder = args.input if args.input.is_absolute() else BASE / args.input
    output = args.output or BASE / (
        '物品验收单_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.pdf')
    if not output.is_absolute():
        output = BASE / output

    try:
        files = collect_files(folder)
        count = build_pdf(files, output)
    except Exception as exc:
        print(f'失败：{exc}', file=sys.stderr)
        return 1
    print(f'已合并 {count} 个文件 → {output}')
    for path in files:
        print('  -', path.name)
    return 0


if __name__ == '__main__':
    sys.exit(main())

# -*- coding: utf-8 -*-
"""
Zygo 轮廓仪界面截图 -> 面粗糙度 Ra 批量提取

从 Zygo NewView 的 "Measure Controls" 面板读取面粗糙度 (Ra, nm),
按区域分组 (区域 = 文件名中最后一个 "-" 之前的前缀, 如 3-4.bmp 属区域 3,
Si-2.bmp 属区域 Si; 名字不是 前缀-序号 格式时每 5 张一组) 求平均,
输出 Excel 表格;
同时把每张图的 Oblique Plot (三维表面图) 裁出来存成 PNG, 便于贴到报告里。

设计要点:
  1. 全部使用相对路径 (以脚本自身所在目录为基准), 整个文件夹可直接搬走使用。
  2. 速度优先: BMP 按行偏移直接 seek 读取 ROI, 不解码整幅图。
     单文件仅需 4 次小 IO, 10 个文件整体 < 0.2 s。
  3. 界面布局固定, ROI 全部为常量; 同时对单位区与识别结果做一致性自检, 异常即告警。

输出:
    ra_results.xlsx          主表格
    out/*.png                各图的三维表面图裁剪 (与原图同名, 左上角标注该图 Ra);
                             每个区域两张代表图加后缀: 离区域均值最近的一张 _medoid,
                             Ra 最小的一张 _min (同一张同时满足时带两个后缀)
    out/min/ out/medoid/     上面两张代表图的副本, 按类别分开存放

用法:
    python extract_ra.py                  # 处理 data 文件夹下的所有图片 (每个区域全读)
    python extract_ra.py --batch 5        # 每个区域只读序号 1~5 的图; 名字不是 前缀-序号 时每 5 张一组
    python extract_ra.py --dir ../Ra      # 指定输入目录 (默认脚本同级的 data)
    python extract_ra.py --out ./result   # 指定输出目录
    python extract_ra.py --no-plot        # 不导出三维表面图

每次运行会自动把 run_ra.bat 规范化成 GBK + CRLF (cmd 的要求),
所以改完 bat 不用再单独跑转码工具。
"""

from __future__ import annotations

import argparse
import re
import shutil
import struct
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def _setup_console_encoding():
    """
    兼容中文 Windows 控制台 (cmd 默认代码页 936/GBK)。

    本机 locale.getpreferredencoding() 返回 utf-8, 但 cmd 的代码页是 936,
    中文会以 UTF-8 字节写进 936 控制台 -> 乱码。
    这里按控制台实际代码页重配 stdout/stderr, 保证双击运行中文正常。
    """
    for stream in (sys.stdout, sys.stderr):
        enc = getattr(stream, 'encoding', None)
        if not enc:
            continue
        try:
            stream.reconfigure(encoding=enc, errors='replace')
        except Exception:                                        # noqa: BLE001
            pass


_setup_console_encoding()

# ----------------------------------------------------------------------------
# 界面 ROI 常量 (基于 Zygo NewView 1917x1003 截图, 已在样本集上逐张核验)
# ----------------------------------------------------------------------------
IMG_W, IMG_H = 1917, 1003

ROW_PV = (330, 349)      # Measure Controls: PV 行
ROW_RMS = (358, 377)     # Measure Controls: rms 行
ROW_RA = (386, 405)      # Measure Controls: Ra 行  <-- 面粗糙度

VAL_X = (326, 462)       # 数值列 (右对齐, 右端固定在 x=451)
UNIT_X = (488, 516)      # 单位列 (nm)

INK_THR = 140           # 白字二值化阈值

# Oblique Plot (三维表面图) 的裁剪框: 左, 上, 右, 下
# 纯黑绘图区, 含色标与色标数字、三轴刻度, 不含界面边框。
# 已在 10 张样本上核验: 位置一致, 尺寸 602x325。
PLOT_BOX = (669, 75, 1271, 400)
PLOT_DIR = 'out'                # 裁剪图的输出子文件夹
TAG_DIRS = ('min', 'medoid')    # 代表图分类子文件夹: out/min、out/medoid (顺序即文件名后缀顺序)
INPUT_DIR = 'data'              # 待处理截图的默认目录 (脚本同级的 data 文件夹)

# 本脚本自身的输出产物, 扫描输入时必须排除 (避免输出被当成下一轮的输入)
OUTPUT_NAMES = {'ra_results.xlsx'}

# ----------------------------------------------------------------------------
# 字形模板库: 数字 0-9 与小数点
# (10x6/10x7 点阵, 由样本图连通域聚类后人工确认)
# ----------------------------------------------------------------------------
TEMPLATES = {
    '0': ['.####.', '#....#', '#....#', '#....#', '#....#', '#....#', '#....#', '#....#', '#....#', '.####.'],
    '1': ['..##...', '##.#...', '...#...', '...#...', '...#...', '...#...', '...#...', '...#...', '...#...', '#######'],
    '2': ['.#####.', '#.....#', '#.....#', '......#', '.....#.', '...##..', '..#....', '.#.....', '#.....#', '#######'],
    '3': ['..####.', '##....#', '......#', '......#', '...###.', '.....#.', '......#', '......#', '#.....#', '.#####.'],
    '4': ['....#.', '...##.', '..#.#.', '.#..#.', '.#..#.', '#...#.', '######', '....#.', '....#.', '..####'],
    '5': ['.#####.', '.#.....', '.#.....', '.#.....', '.#####.', '......#', '......#', '......#', '#.....#', '.#####.'],
    '6': ['...####', '..#....', '.#.....', '#......', '#.####.', '##....#', '#.....#', '#.....#', '#.....#', '.#####.'],
    '7': ['#######', '#.....#', '......#', '.....#.', '.....#.', '....#..', '....#..', '....#..', '...#...', '...#...'],
    '8': ['.####.', '#....#', '#....#', '#....#', '.####.', '#....#', '#....#', '#....#', '#....#', '.####.'],
    '9': ['.####.', '#....#', '#....#', '#....#', '#...##', '.###.#', '.....#', '....#.', '...#..', '###...'],
    '.': ['##', '##'],
}

# 单位字形 (nm / um), 供单位一致性自检
UNIT_TEMPLATES = {
    'n': ['##.###..', '.##...#.', '.#....#.', '.#....#.', '.#....#.', '.#....#.', '###..###'],
    'm': ['##.#..#..', '.##.##.#.', '.#..#..#.', '.#..#..#.', '.#..#..#.', '.#..#..#.', '###.##.##'],
}

NORM_W, NORM_H, NORM_THR = 12, 16, 110


# ----------------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------------
def normalize(mask: np.ndarray) -> np.ndarray:
    """二值字形缩放到统一尺寸, 返回 0/1 浮点特征"""
    img = Image.fromarray((mask * 255).astype(np.uint8)).resize((NORM_W, NORM_H), Image.LANCZOS)
    return (np.array(img) > NORM_THR).astype(np.float32)


def _build(art_map):
    feats = {}
    for ch, art in art_map.items():
        w = max(len(row) for row in art)
        m = np.zeros((len(art), w), dtype=np.uint8)
        for r, row in enumerate(art):
            for c, chx in enumerate(row):
                m[r, c] = 1 if chx == '#' else 0
        feats[ch] = normalize(m)
    return feats


FEATS = _build(TEMPLATES)
UNIT_FEATS = _build(UNIT_TEMPLATES)


# ----------------------------------------------------------------------------
# 图像读取: BMP 走按行 seek 的快路径, 其它格式走 PIL
# ----------------------------------------------------------------------------
def read_roi_bmp(path: Path, y0: int, y1: int, x0: int, x1: int):
    """直接按 BMP 行偏移读取 ROI 亮度, 不解码整图"""
    with open(path, 'rb') as f:
        head = f.read(54)
        if head[:2] != b'BM':
            raise ValueError('不是 BMP 文件')
        offset = struct.unpack('<I', head[10:14])[0]
        w, h = struct.unpack('<ii', head[18:26])
        bpp = struct.unpack('<H', head[28:30])[0]
        compression = struct.unpack('<I', head[30:34])[0]
        if compression != 0 or bpp not in (24, 32):
            raise ValueError(f'不支持的 BMP 格式 (bpp={bpp}, compression={compression})')
        if (w, h) != (IMG_W, IMG_H):
            raise ValueError(f'图像尺寸 {w}x{h} 与预期 {IMG_W}x{IMG_H} 不符')

        stride = ((w * bpp // 8) + 3) // 4 * 4
        step = bpp // 8
        rows = []
        for y in range(y0, y1 + 1):
            fy = h - 1 - y if h > 0 else y          # BMP 自下而上存储
            f.seek(offset + fy * stride + x0 * step)
            buf = np.frombuffer(f.read((x1 - x0 + 1) * step), dtype=np.uint8).reshape(-1, step)
            b, g_, r = buf[:, 0], buf[:, 1], buf[:, 2]
            rows.append(0.299 * r + 0.587 * g_ + 0.114 * b)
    return np.array(rows)


def read_roi_pil(path: Path, y0: int, y1: int, x0: int, x1: int):
    """PNG/JPG 等格式的兜底读取"""
    with Image.open(path) as im:
        a = np.array(im.convert('RGB')).astype(np.float32)
    roi = a[y0:y1 + 1, x0:x1 + 1]
    return 0.299 * roi[:, :, 0] + 0.587 * roi[:, :, 1] + 0.114 * roi[:, :, 2]


def read_roi(path: Path, y0: int, y1: int, x0: int, x1: int):
    if path.suffix.lower() == '.bmp':
        try:
            return read_roi_bmp(path, y0, y1, x0, x1)
        except ValueError:
            pass                                   # 格式不符则退回 PIL
    return read_roi_pil(path, y0, y1, x0, x1)


# ----------------------------------------------------------------------------
# Oblique Plot 裁剪 (导出三维表面图, 便于贴到报告/PPT)
# ----------------------------------------------------------------------------
def read_rgb_bmp(path: Path, x0: int, y0: int, x1: int, y1: int) -> np.ndarray:
    """
    读取 BMP 指定区域的 RGB 像素, 不解码整幅图。

    做法: 该区域在文件里是连续的一段 (含行尾补齐), 一次 read 取回后按 stride 切片,
    比逐行 seek 快得多。返回 shape=(y1-y0, x1-x0, 3) 的 uint8 数组。
    """
    with open(path, 'rb') as f:
        head = f.read(54)
        if head[:2] != b'BM':
            raise ValueError('不是 BMP 文件')
        offset = struct.unpack('<I', head[10:14])[0]
        w, h = struct.unpack('<ii', head[18:26])
        bpp = struct.unpack('<H', head[28:30])[0]
        compression = struct.unpack('<I', head[30:34])[0]
        if compression != 0 or bpp != 24:
            raise ValueError(f'不支持的 BMP 格式 (bpp={bpp}, compression={compression})')

        stride = ((w * 3) + 3) // 4 * 4
        n_rows = y1 - y0
        if h > 0:                                 # 自下而上存储
            f.seek(offset + (h - y1) * stride)
        else:                                     # 自上而下 (h<0)
            f.seek(offset + y0 * stride)
        buf = f.read(n_rows * stride)

    arr = np.frombuffer(buf, dtype=np.uint8).reshape(n_rows, stride)
    arr = arr[:, x0 * 3:x1 * 3].reshape(n_rows, x1 - x0, 3)
    if h > 0:
        arr = arr[::-1]                           # 翻转成自上而下
    return arr[:, :, ::-1]                        # BGR -> RGB


def _label_font(size: int):
    """微软雅黑粗体; 缺失时依次退回普通字重和 PIL 默认字体, 保证不因字体问题中断导出"""
    for name in ('msyhbd.ttc', 'msyh.ttc', 'simhei.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def export_plot(path: Path, out_path: Path, label: str | None = None):
    """
    裁出 Oblique Plot 区域并存成 PNG。

    PNG 为无损格式, 贴到 PPT/Word 里不会二次压缩失真。
    label 不为空时在左上角标注该图的 Ra。
    """
    x0, y0, x1, y1 = PLOT_BOX
    if path.suffix.lower() == '.bmp':
        try:
            arr = read_rgb_bmp(path, x0, y0, x1, y1)
        except ValueError:
            arr = None
    else:
        arr = None

    if arr is None:                               # 兜底: 整图解码再裁
        with Image.open(path) as im:
            arr = np.array(im.convert('RGB').crop(PLOT_BOX))

    img = Image.fromarray(arr)
    if label:
        # 左上角多为黑底, 但高起伏的图会顶到那里; 白字配黑描边保证任何背景都看得清
        ImageDraw.Draw(img).text((10, 4), label, font=_label_font(44),
                                 fill=(255, 255, 255), stroke_width=3,
                                 stroke_fill=(0, 0, 0))
    # compress_level=6 已能压得很小, optimize=True 会显著拖慢且收益有限
    img.save(out_path, format='PNG', compress_level=6)


def tagged_stem(stem: str, flags) -> str:
    """按标记拼出导出文件名: 1-3 -> 1-3_min / 1-3_medoid / 1-3_min_medoid"""
    return '_'.join([stem] + [f for f in TAG_DIRS if f in flags])


# ----------------------------------------------------------------------------
# 字符切分与识别
# ----------------------------------------------------------------------------
def segment(mask: np.ndarray):
    """按列投影把一行二值图切成若干字形"""
    col = mask.sum(axis=0)
    groups, start = [], None
    for x in range(mask.shape[1]):
        if col[x] > 0:
            if start is None:
                start = x
        elif start is not None:
            groups.append((start, x - 1))
            start = None
    if start is not None:
        groups.append((start, mask.shape[1] - 1))

    out = []
    for s, e in groups:
        g = mask[:, s:e + 1]
        row = g.sum(axis=1)
        nz = np.where(row > 0)[0]
        if len(nz) == 0:
            continue
        out.append(g[nz.min():nz.max() + 1])
    return out


def classify(glyph: np.ndarray, feats: dict):
    """字形 -> 字符, 返回 (字符, 最佳汉明距离, 与次佳的差值)"""
    if glyph.shape[0] <= 2 and glyph.shape[1] <= 2:
        return '.', 0.0, 99.0                       # 小数点
    f = normalize(glyph)
    dists = sorted((float(np.abs(f - t).sum()), ch) for ch, t in feats.items())
    best_d, best_ch = dists[0]
    margin = dists[1][0] - best_d if len(dists) > 1 else 99.0
    return best_ch, best_d, margin


def read_number(path: Path, row):
    """读取数值字段, 返回 (文本, [(字符, 距离, 裕度), ...])"""
    y0, y1 = row
    gray = read_roi(path, y0, y1, VAL_X[0], VAL_X[1])
    mask = gray > INK_THR
    chars, conf = [], []
    for g in segment(mask):
        ch, d, margin = classify(g, FEATS)
        chars.append(ch)
        conf.append((ch, round(d, 1), round(margin, 1)))
    return ''.join(chars), conf


def read_unit(path: Path, row):
    """读取单位, 返回 (单位字符串, 最佳距离)"""
    y0, y1 = row
    gray = read_roi(path, y0, y1, UNIT_X[0], UNIT_X[1])
    out, worst = '', 0.0
    for g in segment(gray > INK_THR):
        ch, d, _ = classify(g, UNIT_FEATS)
        out += ch
        worst = max(worst, d)
    return out, worst


# ----------------------------------------------------------------------------
# 表格输出
# ----------------------------------------------------------------------------
# 文件名形如 3-4.bmp 或 Si-1.bmp -> 前缀相同的算同一个区域, 前缀不同即不同区域。
# 前缀不限数字 (3、Si、AreaA 都行); 分组只看前缀, 不按张数切, 否则某个区域多一张/少一张
# 就会把后面所有区域整体错位 (3-4 和 3-5 会被分到两组)。--batch 只在分组后用来截取前 N 张。
REGION_NAME_RE = re.compile(r'^(.+)-(\d+)$')
DEFAULT_BATCH = 5          # 名字不是 前缀-序号 格式时的默认每区域张数


def split_groups(recs, batch_size):
    """按文件名前缀分组; 名字不是 前缀-序号 格式时退回每 batch_size 张一组"""
    if not all(REGION_NAME_RE.match(r['name']) for r in recs):
        batch_size = batch_size or DEFAULT_BATCH
        return [recs[i:i + batch_size] for i in range(0, len(recs), batch_size)]

    # 按首次出现顺序排列, 与 collect_files 的文件顺序一致
    groups = {}
    for r in recs:
        groups.setdefault(REGION_NAME_RE.match(r['name']).group(1), []).append(r)
    return list(groups.values())


def select_files(files, batch_size):
    """前缀-序号 命名且指定了张数时, 每区域只保留序号最小的前 batch_size 张 (XX-1 ~ XX-N);
    未指定张数则整个区域的图片全都读"""
    if not batch_size or not all(REGION_NAME_RE.match(p.stem) for p in files):
        return files

    groups = {}
    for p in files:
        m = REGION_NAME_RE.match(p.stem)
        groups.setdefault(m.group(1), []).append((int(m.group(2)), p))
    keep = []
    for items in groups.values():
        keep += [p for _, p in sorted(items, key=lambda t: t[0])[:batch_size]]
    return keep


def build_table(batches):
    """
    表格结构 (与示例一致):
      第 1 行: 标题 + 各区域编号
      第 2 行: 总体均值 + 各区域均值
      第 4 行起: 各文件明细
    """
    files = [r for b in batches for r in b]
    means = [float(np.mean([r['ra'] for r in b])) for b in batches]
    overall = float(np.mean([r['ra'] for r in files]))
    for i, b in enumerate(batches, start=1):
        for r in b:
            r['batch'] = i
            r['flags'] = set()
        # 每区两张代表图: 离区域均值最近的一张 (medoid) 与 Ra 最小的一张
        # 并列时 min() 取靠前的一张, 保证结果稳定可复现
        min(b, key=lambda r: abs(r['ra'] - means[i - 1]))['flags'].add('medoid')
        min(b, key=lambda r: r['ra'])['flags'].add('min')

    # 汇总列直接写成 "区域1" "区域2", 一眼看出每列对应哪一组
    header = ['粗糙度（平均）'] + [f'区域{i}' for i in range(1, len(batches) + 1)]
    # 数值保持 float, 由各 writer 自行决定格式 (Excel 需要真数值才能右对齐并求和)
    values = [overall] + means

    detail = [['文件', '区域', 'Ra (nm)', 'PV (nm)', 'rms (nm)', '单位']]
    for r in files:
        detail.append([r['name'], r['batch'], r['ra'], r['pv'], r['rms'], r['unit']])
    return header, values, detail, batches, means, overall


def write_xlsx(out_path: Path, header, values, detail, row_flags=None):
    """
    写 Excel。

    版式:
        第 1 行  汇总表头 (粗糙度（平均） | 1 | 2 | ...)
        第 2 行  汇总数值 (总体均值 + 各区域均值)
        第 3 行  空行
        第 4 行  明细表头
        第 5 行起 各文件明细
        末行     填色说明 (浅绿=_min, 浅橙=_medoid)

    统一的样式规则 (两块表格共用, 保证视觉一致):
        宽度   汇总区合并单元格铺满与明细区同宽, 两块边缘对齐
        边框   全黑细线, 覆盖每一个有内容的单元格 (合并区逐格设, 否则边框缺失)
        表头   加粗 + 浅蓝底 + 居中
        对齐   全部列居中
        数值   写真数值 + 0.000 格式 (避免 Excel 报"数字以文本形式存储")
        填色   row_flags 按明细行给出代表图标记, 整行填色 (min 优先于 medoid)

    注意: 行数与列数必须分开记。此前把列数当行数用, 导致只有前 7 行画了边框。
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    WB = Workbook()
    ws = WB.active
    ws.title = '粗糙度分析'

    NUM_FMT = '0.000'
    thin = Side(style='thin', color='000000')
    BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)
    HEAD_FILL = PatternFill('solid', fgColor='DCE6F1')
    SUM_FILL = PatternFill('solid', fgColor='F2F2F2')
    HEAD_FONT = Font(bold=True, size=11)
    # 两种代表图各一种底色, 与 out/ 里的文件名后缀一一对应
    TAG_FILLS = {
        'min': PatternFill('solid', fgColor='C6EFCE'),       # 浅绿: 区内 Ra 最小
        'medoid': PatternFill('solid', fgColor='FCE4D6'),    # 浅橙: 区内最接近均值
    }
    LEGEND_FONT = Font(size=9, color='595959')

    # 整表统一水平居中; 数值列不设 indent (缩进会挤占宽度, 列窄时显示 ######)
    A_CENTER = Alignment(horizontal='center', vertical='center')

    n_cols_det = len(detail[0])              # 明细区列数
    n_rows_det = len(detail)                 # 明细区行数 (含表头)
    n_batch = len(values) - 1                # 区域数
    ROW_SUM_HEAD, ROW_SUM_VAL = 1, 2
    ROW_DET_HEAD = 4
    ROW_DET_LAST = ROW_DET_HEAD + n_rows_det - 1

    # 整表宽度取两块表格的并集: 汇总区每个区域占相同列数, 区域之间列宽才一致;
    # 列数不够时向右补列, 补出的列平摊给明细区各数据列, 不留空白列。
    per = max(1, -(-(n_cols_det - 1) // n_batch)) if n_batch else 1
    n_cols_total = max(n_cols_det, 1 + per * n_batch)

    # ---- 汇总区各列跨度: 标题列占 1 列, 其余列平分给各区域 ----
    body_cols = n_cols_total - 1
    base, rem = divmod(body_cols, n_batch) if n_batch else (0, 0)
    spans = []
    cur = 2
    for i in range(n_batch):
        w = max(base + (1 if i < rem else 0), 1)
        spans.append((cur, min(cur + w - 1, n_cols_total)))
        cur += w

    # ---- 明细区各列跨度: 多出的列平摊给数据列 (文件列不参与) ----
    extra = n_cols_total - n_cols_det
    body_n = n_cols_det - 1
    q, r = divmod(extra, body_n) if body_n else (0, 0)
    detail_cols = [(1, 1)]
    cur = 2
    for j in range(body_n):
        w = 1 + q + (1 if j >= body_n - r else 0)
        detail_cols.append((cur, cur + w - 1))
        cur += w

    def put(r, c1, c2, value, border=True, fill=None, font=None,
            align=None, num_fmt=None):
        """写一个单元格 (必要时合并), 并给区域内每一格都设边框"""
        for c in range(c1, c2 + 1):
            cell = ws.cell(row=r, column=c)
            if border:
                cell.border = BORDER
            if fill is not None:
                cell.fill = fill
            if font is not None:
                cell.font = font
            if align is not None:
                cell.alignment = align
            if num_fmt is not None:
                cell.number_format = num_fmt
        ws.cell(row=r, column=c1).value = value
        if c2 > c1:
            ws.merge_cells(start_row=r, start_column=c1, end_row=r, end_column=c2)

    # ---- 汇总区 ----
    put(ROW_SUM_HEAD, 1, 1, header[0], fill=HEAD_FILL, font=HEAD_FONT, align=A_CENTER)
    put(ROW_SUM_VAL, 1, 1, values[0], fill=SUM_FILL, align=A_CENTER, num_fmt=NUM_FMT)
    for i, (c1, c2) in enumerate(spans):
        put(ROW_SUM_HEAD, c1, c2, header[i + 1],
            fill=HEAD_FILL, font=HEAD_FONT, align=A_CENTER)
        put(ROW_SUM_VAL, c1, c2, values[i + 1],
            fill=SUM_FILL, align=A_CENTER, num_fmt=NUM_FMT)

    # ---- 明细区: 整块统一边框 (行数用 n_rows_det, 不是列数) ----
    for i, row in enumerate(detail):
        r = ROW_DET_HEAD + i
        is_head = (i == 0)
        flags = row_flags[i - 1] if (row_flags and i > 0) else ()
        row_fill = TAG_FILLS['min'] if 'min' in flags else (
            TAG_FILLS['medoid'] if 'medoid' in flags else None)
        for j, value in enumerate(row):
            c1, c2 = detail_cols[j]
            put(r, c1, c2, value,
                fill=HEAD_FILL if is_head else row_fill,
                font=HEAD_FONT if is_head else None,
                align=A_CENTER,
                num_fmt=NUM_FMT if j in (2, 3, 4) else None)

    # ---- 填色说明: 两种底色没有图例就分不清, 补一行小字 ----
    if row_flags and any(row_flags):
        put(ROW_DET_LAST + 2, 1, n_cols_total,
            '填色说明：浅绿 = 区内 Ra 最小 (_min)；浅橙 = 区内最接近均值 (_medoid)',
            border=False, font=LEGEND_FONT,
            align=Alignment(horizontal='left', vertical='center'))

    # ---- 列宽 (两块表格共用同一套, 保证列对齐) ----
    # 统一的 12: 汇总区各区域跨同样多的列, 列宽相同才能保证区域之间等宽
    widths = {1: 16}
    for c in range(1, n_cols_total + 1):
        ws.column_dimensions[get_column_letter(c)].width = widths.get(c, 12)

    # ---- 行高 ----
    for r in range(1, ROW_DET_LAST + 1):
        ws.row_dimensions[r].height = 21

    # 冻结明细表头, 滚动时保持可见
    ws.freeze_panes = ws.cell(row=ROW_DET_HEAD + 1, column=1)

    WB.save(out_path)


def close_open_workbook(path: Path) -> bool:
    """
    关掉正打开着该文件的 Excel / WPS 工作簿 (不保存), 返回是否找到并关闭。

    只关闭这一个工作簿, 不退出 Excel, 也不影响用户其它窗口。
    """
    try:
        import win32com.client
    except ImportError:
        return False

    target = str(path.resolve()).lower()
    for progid in ('Excel.Application', 'KET.Application'):
        try:
            app = win32com.client.GetActiveObject(progid)
        except Exception:                                   # noqa: BLE001
            continue
        try:
            for wb in list(app.Workbooks):
                try:
                    if str(Path(wb.FullName).resolve()).lower() == target:
                        wb.Close(SaveChanges=False)
                        return True
                except Exception:                           # noqa: BLE001
                    continue
        except Exception:                                   # noqa: BLE001
            continue
    return False


def write_xlsx_safely(out_path: Path, header, values, detail, row_flags=None) -> bool:
    """写 Excel; 文件被 Excel 打开时先自动关闭该工作簿再重试"""
    try:
        write_xlsx(out_path, header, values, detail, row_flags)
        return True
    except PermissionError:
        pass

    if not close_open_workbook(out_path):
        return False
    print(f'  [提示] {out_path.name} 正被 Excel 打开, 已自动关闭该工作簿, 重新写入...')
    for _ in range(10):                                    # 等 Excel 释放文件句柄
        time.sleep(0.2)
        try:
            write_xlsx(out_path, header, values, detail, row_flags)
            return True
        except PermissionError:
            continue
    return False


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def collect_files(input_dir: Path):
    exts = {'.bmp', '.png', '.jpg', '.jpeg'}
    files = [p for p in input_dir.iterdir()
             if p.suffix.lower() in exts and not p.name.startswith('~')
             and p.name not in OUTPUT_NAMES]      # 排除本脚本自身的输出产物

    def key(p):
        digits = ''.join(ch for ch in p.stem if ch.isdigit())
        return (int(digits) if digits else 10 ** 9, p.name)

    return sorted(files, key=key)


def ensure_bat_encoding() -> bool:
    """
    确保同目录的 run_ra.bat 是 GBK 编码 + CRLF 换行, 需要时自动改写。

    为什么必须有这一步:
      1. cmd.exe 按系统 ANSI 码页 (本机 936/GBK) 解析 .bat。存成 UTF-8 时,
         中文注释的字节会被拆成乱码命令, 双击直接启动失败
         (报 "'?' 不是内部或外部命令")。
      2. cmd 要求 CRLF 换行。纯 LF 会让括号块解析错乱 (报 "此时不应有 ----")。

    每次运行自动检查, 只在确实需要时写盘。原先独立的 _to_gbk.py 已并入此处。
    返回是否做了修正。
    """
    bat = Path(__file__).resolve().parent / 'run_ra.bat'
    if not bat.exists():
        return False

    raw = bat.read_bytes()
    try:
        text = raw.decode('utf-8')
        is_utf8 = True
    except UnicodeDecodeError:
        text = raw.decode('gbk', errors='replace')
        is_utf8 = False

    # 统一成 LF 后再按 GBK + CRLF 重建
    norm = text.replace('\r\n', '\n').replace('\r', '\n')
    fixed = b'\r\n'.join(line.encode('gbk', errors='replace') for line in norm.split('\n'))

    # 纯 ASCII 的 UTF-8 与 GBK 等价, 不算需要转换; 只有含中文的 UTF-8 才必须转
    has_non_ascii = any(ord(c) > 127 for c in text)
    need = (is_utf8 and has_non_ascii) or (fixed != raw)
    if not need:
        return False

    bat.write_bytes(fixed)
    src = 'UTF-8' if (is_utf8 and has_non_ascii) else '非标准换行/编码'
    print(f'[提示] 已自动修正 run_ra.bat ({src} -> GBK + CRLF)')
    return True


def main():
    ap = argparse.ArgumentParser(description='从 Zygo 截图批量提取面粗糙度 Ra 并按区域平均')
    here = Path(__file__).resolve().parent
    ap.add_argument('--dir', default=None,
                    help=f'输入目录 (默认: 脚本同级的 {INPUT_DIR} 文件夹)')
    ap.add_argument('--out', default=None, help='输出目录 (默认: 脚本所在目录)')
    ap.add_argument('--batch', type=int, default=None,
                    help='每个区域取几张图 (5 = 只取序号 1~5); 不给则整个区域全读。'
                        '文件名不是「前缀-序号」格式时, 作为分批张数 (默认 5)')
    ap.add_argument('--no-plot', action='store_true', help='不导出三维表面图 PNG')
    args = ap.parse_args()
    if args.batch is not None and args.batch < 1:
        ap.error('--batch 必须为正整数')

    # 顺手保证启动器编码正确 (原 _to_gbk.py 的职责)
    ensure_bat_encoding()

    input_dir = Path(args.dir).resolve() if args.dir else here / INPUT_DIR
    out_dir = Path(args.out).resolve() if args.out else here
    out_dir.mkdir(parents=True, exist_ok=True)

    if not input_dir.is_dir():
        print(f'[错误] 找不到输入目录 {input_dir}\n'
              f'       请把 Zygo 截图放进该文件夹, 或用 --dir 指定其它目录',
              file=sys.stderr)
        return 2

    files = collect_files(input_dir)
    if not files:
        print(f'[错误] {input_dir} 下没有找到图片文件', file=sys.stderr)
        return 2

    print(f'输入目录: {input_dir}')
    if all(REGION_NAME_RE.match(p.stem) for p in files):
        n_file_all = len(files)
        files = select_files(files, args.batch)
        n_region = len({REGION_NAME_RE.match(p.stem).group(1) for p in files})
        pick = f'每区只取序号 1~{args.batch} 张' if args.batch else '每区全部图片'
        print(f'共 {n_file_all} 个文件, 按文件名前缀分 {n_region} 个区域, '
              f'{pick} -> 实际读取 {len(files)} 个\n')
    else:
        batch = args.batch or DEFAULT_BATCH
        n_batch = (len(files) + batch - 1) // batch
        print(f'共 {len(files)} 个文件, 每区域 {batch} 张, 共 {n_batch} 个区域\n')

    plot_dir = out_dir / PLOT_DIR
    plot_done, plot_fail = [], []
    if not args.no_plot:
        plot_dir.mkdir(parents=True, exist_ok=True)

    recs, ref_unit = [], None

    def export_one(path: Path, label, stem=None):
        """导出三维表面图; 目标 PNG 被占用或裁剪失败只告警, 不影响 Ra 提取"""
        if args.no_plot:
            return
        stem = stem or path.stem
        try:
            export_plot(path, plot_dir / f'{stem}.png', label)
            plot_done.append(stem)
        except OSError as e:
            # Windows 下目标 PNG 被看图程序占用时, Pillow 抛的是 EINVAL(22) 而非 EACCES
            if e.errno in (13, 22):
                plot_fail.append((path.name, '目标 PNG 正被其他程序打开'))
            else:
                plot_fail.append((path.name, str(e)))
        except Exception as e:                               # noqa: BLE001
            plot_fail.append((path.name, str(e)))

    for p in files:
        # 先做界面版本自检: 尺寸必须匹配, 否则直接跳过并说明原因
        try:
            with Image.open(p) as im:
                size = im.size
        except Exception as e:                                   # noqa: BLE001
            print(f'[跳过] {p.name}: 图片无法读取 ({e})')
            continue
        if size != (IMG_W, IMG_H):
            print(f'[跳过] {p.name}: 图像尺寸 {size[0]}x{size[1]} 与 Zygo 界面截图 '
                  f'{IMG_W}x{IMG_H} 不符, 非本流程可处理的截图')
            continue

        try:
            ra_txt, conf = read_number(p, ROW_RA)
            pv_txt, _ = read_number(p, ROW_PV)
            rms_txt, _ = read_number(p, ROW_RMS)
            unit, unit_d = read_unit(p, ROW_RA)
        except Exception as e:                                   # noqa: BLE001
            print(f'[跳过] {p.name}: {e}')
            export_one(p, None)                                  # Ra 未读出, 图仍导出但不标注
            continue

        if ref_unit is None:
            ref_unit = unit
        elif unit != ref_unit:
            print(f'[警告] {p.name} 单位为 "{unit}", 与首张 "{ref_unit}" 不一致, 请确认界面版本')

        try:
            ra, pv, rms = float(ra_txt), float(pv_txt), float(rms_txt)
        except ValueError:
            print(f'[跳过] {p.name}: 未能识别出有效的 Ra 数值 '
                  f'(读到 Ra="{ra_txt}", PV="{pv_txt}", rms="{rms_txt}"), '
                  f'请确认截图包含 "Measure Controls" 面板')
            export_one(p, None)
            continue

        # 置信度: 用"与次优模板的最小裕度"衡量判别清晰度。
        # 样本集实测: 全部字形最佳距离为 0, 最小裕度 24 (出现在 0/8 之间), 判别无歧义。
        worst_dist = max((c[1] for c in conf), default=0.0)
        worst_margin = min((c[2] for c in conf), default=99.0)
        recs.append({
            'name': p.name, 'path': p, 'ra': ra, 'pv': pv, 'rms': rms,
            'unit': unit,
        })
        flag = ''
        if worst_dist > 40 or worst_margin < 10:
            flag = '   [低置信, 建议与截图核对]'
        print(f'  {p.name:<12} Ra = {ra:>9.3f} nm    PV = {pv:>11.3f}    rms = {rms:>8.3f}{flag}')

    if not recs:
        print('[错误] 没有成功提取到任何数据', file=sys.stderr)
        return 3

    header, values, detail, batches, means, overall = build_table(
        split_groups(recs, args.batch))

    # 代表图要等全部 Ra 算完才知道是哪张, 所以导出放到这里, 并直接带上 min / medoid 后缀
    row_flags = [r['flags'] for r in recs]
    for r in recs:
        export_one(r['path'], f'Ra={r["ra"]:.3f} {r["unit"]}',
                   tagged_stem(r['path'].stem, r['flags']))

    # 代表图后缀会随数据变化 (上次的 _min 这次可能变成 _medoid), 所以 out/ 必须与本次产物
    # 严格对齐: 删掉所有没写出来的旧 PNG, 否则会残留两个 _min 之类的旧图。
    # 同时把代表图各复制一份到 out/min、out/medoid, 翻图时不用在几十张里挑。
    if not args.no_plot:
        keep = {f'{s}.png' for s in plot_done}
        sub_keep = {d: set() for d in TAG_DIRS}
        for r in recs:
            name = f'{tagged_stem(r["path"].stem, r["flags"])}.png'
            if name not in keep:
                continue                     # 导出失败的不复制, 子文件夹里也不该有它
            for d in TAG_DIRS:
                if d in r['flags']:
                    (plot_dir / d).mkdir(exist_ok=True)
                    shutil.copyfile(plot_dir / name, plot_dir / d / name)
                    sub_keep[d].add(name)

        stuck = []
        for folder, names in [(plot_dir, keep)] + [(plot_dir / d, sub_keep[d])
                                                   for d in TAG_DIRS]:
            for old in folder.glob('*.png'):
                if old.name in names:
                    continue
                try:
                    old.unlink()
                except OSError:
                    stuck.append(f'{folder.name}/{old.name}')   # 被看图软件占用时删不掉
        if stuck:
            print('[警告] 以下旧图删不掉 (可能正被看图软件打开), 会与本次结果混在一起, '
                  '请关闭后重跑: ' + ', '.join(stuck), file=sys.stderr)

    # 先把结果打到屏幕, 即使写盘失败用户也能看到数值
    print('\n--- 粗糙度分析 (面粗糙度 Ra, nm) ---')
    print('区域: ' + '  '.join(f'{i + 1}' for i in range(len(batches))))
    print('均值: ' + '  '.join(f'{m:.3f}' for m in means))
    print(f'总体均值: {overall:.3f} nm  (共 {len(recs)} 个文件)')
    for i, b in enumerate(batches, start=1):
        med = next(r for r in b if 'medoid' in r['flags'])
        low = next(r for r in b if 'min' in r['flags'])
        print(f'  区域{i}: medoid = {med["name"]} (Ra={med["ra"]:.3f})'
              f'    min = {low["name"]} (Ra={low["ra"]:.3f})')

    # 唯一产物: xlsx
    xlsx_path = out_dir / 'ra_results.xlsx'
    if not write_xlsx_safely(xlsx_path, header, values, detail, row_flags):
        print(f'\n[错误] 无法写入 {xlsx_path.name} —— 该文件正被占用'
              f'(已尝试自动关闭 Excel 工作簿, 仍失败)。\n'
              f'       请手动关闭 Excel 后重新运行。',
              file=sys.stderr)
        return 4

    print(f'\n已输出: {xlsx_path}')

    if plot_done:
        print(f'已导出三维表面图: {plot_dir}  ({len(plot_done)} 张 PNG)')
        print('          ' + ', '.join(f'{n}.png' for n in plot_done[:10])
              + (' ...' if len(plot_done) > 10 else ''))
        print(f'代表图副本: {plot_dir / "min"} 与 {plot_dir / "medoid"}')
    for name, why in plot_fail:
        print(f'[警告] 三维表面图导出失败 {name}: {why}', file=sys.stderr)

    return 0


if __name__ == '__main__':
    sys.exit(main())

# -*- coding: utf-8 -*-
"""
Zygo 截图 Ra 提取与 DAT 周期分离、四联图重建

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
    out/bmp/*.png                各图的三维表面图裁剪 (与原图同名, 左上角标注该图 Ra);
                             每个区域两张代表图加后缀: 离区域均值最近的一张 _medoid,
                             Ra 最小的一张 _min (同一张同时满足时带两个后缀)
    out/bmp/min/ out/bmp/medoid/     上面两张代表图的副本, 按类别分开存放

用法:
    python extract_ra.py                  # 处理 data 文件夹下的所有图片 (每个区域全读)
    python extract_ra.py --batch 5        # 每个区域只读序号 1~5 的图; 名字不是 前缀-序号 时每 5 张一组
    python extract_ra.py --dir ../Ra      # 指定输入目录 (默认脚本同级的 data)
    python extract_ra.py --out ./result   # 指定输出目录
    python extract_ra.py --no-plot        # 不导出三维表面图

每次运行会自动把 run.bat 规范化成 GBK + CRLF (cmd 的要求),
所以改完 bat 不用再单独跑转码工具。
"""

from __future__ import annotations

import re
import struct
import sys
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
PLOT_DIR = 'out/bmp'            # 截图三维图输出子文件夹
TAG_DIRS = ('min', 'medoid')    # 代表图分类子文件夹（顺序即文件名后缀顺序）
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


def export_plot(path: Path, out_path: Path, label: str | None = None, label_style=None):
    """
    裁出 Oblique Plot 区域并存成 PNG。

    PNG 为无损格式, 贴到 PPT/Word 里不会二次压缩失真。
    label 不为空时在左上角标注该图的 Ra。
    """
    from plot_labels import annotate
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
    annotate(img, label, label_style)
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
    if not all(REGION_NAME_RE.match(Path(r['name']).stem) for r in recs):
        batch_size = batch_size or DEFAULT_BATCH
        return [recs[i:i + batch_size] for i in range(0, len(recs), batch_size)]

    # 按首次出现顺序排列, 与 collect_files 的文件顺序一致
    groups = {}
    for r in recs:
        groups.setdefault(REGION_NAME_RE.match(Path(r['name']).stem).group(1), []).append(r)
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


def write_xlsx(out_path: Path, header, values, detail, row_flags=None, workbook=None):
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

    WB = workbook if workbook is not None else Workbook()
    if workbook is not None and '粗糙度分析' in WB.sheetnames:
        del WB['粗糙度分析']
    ws = WB.create_sheet('粗糙度分析',0) if workbook is not None else WB.active
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

    if out_path is not None:
        WB.save(out_path)
    return WB






# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
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


def collect_files(input_dir: Path):
    exts = {'.bmp', '.png', '.jpg', '.jpeg'}
    files = [p for p in input_dir.iterdir()
             if p.suffix.lower() in exts and not p.name.startswith('~')
             and p.name not in OUTPUT_NAMES]      # 排除本脚本自身的输出产物
    return sorted(files, key=lambda p: natural_key(p.name))


def ensure_bat_encoding() -> bool:
    """
    确保同目录的 run.bat 是 GBK 编码 + CRLF 换行, 需要时自动改写。

    为什么必须有这一步:
      1. cmd.exe 按系统 ANSI 码页 (本机 936/GBK) 解析 .bat。存成 UTF-8 时,
         中文注释的字节会被拆成乱码命令, 双击直接启动失败
         (报 "'?' 不是内部或外部命令")。
      2. cmd 要求 CRLF 换行。纯 LF 会让括号块解析错乱 (报 "此时不应有 ----")。

    每次运行自动检查, 只在确实需要时写盘。原先独立的 _to_gbk.py 已并入此处。
    返回是否做了修正。
    """
    bat = Path(__file__).resolve().parent / 'run.bat'
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
    print(f'[提示] 已自动修正 run.bat ({src} -> GBK + CRLF)')
    return True


def main():
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    import ra_workflow
    return ra_workflow.run(sys.modules[__name__])


if __name__ == '__main__':
    sys.exit(main())

# -*- coding: utf-8 -*-
"""Raman spectra plotter.

Reads two-column .txt files (rel. 1/cm, CCD cts) from a data folder, plots the
requested wavenumber window in the house style, and labels every peak whose
height above baseline clears a threshold.

Peak height is defined as

    H = I_max - I_baseline

where I_max is the measured intensity at the peak maximum and I_baseline is the
estimated background under it, taken as the higher of the two local minima that
flank the peak (the saddle points of the profile). This is an estimate from the
data, not an instrument baseline measurement.

Usage:
    python plot_raman.py --xmin 500 --xmax 1000 --height 30
    python plot_raman.py --xmin 200 --xmax 1200 --height 80 --src data --out out
"""
import argparse
import concurrent.futures as cf
import csv
import glob
import os
import re
import sys
import time

try:
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator, MultipleLocator
    from matplotlib.transforms import blended_transform_factory
    from matplotlib import font_manager
except ImportError as exc:
    # 依赖检查放在这里而不是 bat 里: 单独跑一次 python -c "import numpy,
    # matplotlib" 要多付一个解释器启动 + 全部导入 (~1 s), 而本脚本反正要
    # 导入它们, 缺了直接报清楚缺哪个就行。
    sys.stderr.write("ERROR: 缺少依赖包 %s\n请执行:\n  \"%s\" -m pip install "
                     "numpy matplotlib openpyxl\n" % (exc.name, sys.executable))
    raise SystemExit(1)

# ---------------------------------------------------------------- house style
# Canvas and layout were measured off the reference figure: 2048x1024 px with
# the frame at x 83..2043 and y 3..944.
PX_W, PX_H = 2048, 1024
DPI = 200
FIG_W, FIG_H = PX_W / DPI, PX_H / DPI

AX_L = 83.5 / 2048.0
AX_R = 2042.5 / 2048.0
AX_B = 1 - 943.0 / 1024.0
AX_T = 1 - 4.0 / 1024.0

FS = 13.0
LW_FRAME = 1.08
LW_CURVE = 0.42
TICK_LEN = 8.6
RED = (1.0, 0.0, 0.0)
BLACK = (0.0, 0.0, 0.0)

XL_POS = (0.51900, 0.01306)   # "rel. 1/cm" ink centre, figure fraction
YL_POS = (0.01110, 0.53300)   # "CCD cts" ink centre, figure fraction
YTICK_X = 0.0276              # centre of the rotated y tick labels

PAD_TEXT = 0.114              # label bottom, in fractions of the y-span above the peak
STEM_LO, STEM_HI = 0.038, 0.100

FIT_WIN = 30.0                # +- window for the peak-centre fit, cm-1
BASE_WIN = 60.0               # +- window for the baseline search, cm-1
MERGE_DX = 15.0               # maxima closer than this are one peak
MAX_LABELS = 10
# 激光线（瑞利散射）漏光：0 cm-1 附近那个很尖的假峰不是拉曼峰，排除掉。
# 排除以 zero_at（校正后的激光线位置）为中心、半宽 ZERO_GUARD cm-1 的区间。
# 设 0 可关闭排除。
ZERO_GUARD = 50.0

# 自适应峰高阈值: thr = max(AUTO_K_SIGMA * 噪声σ, AUTO_PEAK_RATIO * 最强峰H)。
# 噪声下限压掉随机毛刺(弱信号谱靠它兜底), 相对主峰比例压掉高动态谱里的中等
# 杂散峰。σ 用差分 MAD 稳健估计。参数经多谱标定, 细节见 auto_threshold()。
AUTO_K_SIGMA = 2.5
AUTO_PEAK_RATIO = 0.011


def pick_font():
    names = {f.name for f in font_manager.fontManager.ttflist}
    for name in ("Arial", "Helvetica", "Liberation Sans", "DejaVu Sans"):
        if name in names:
            return name
    return "DejaVu Sans"


def pick_cjk_font():
    """A font that actually has CJK glyphs, for user-supplied curve labels."""
    names = {f.name for f in font_manager.fontManager.ttflist}
    for name in ("Microsoft YaHei", "Noto Sans SC", "SimHei", "DengXian",
                 "WenQuanYi Micro Hei", "Microsoft JhengHei"):
        if name in names:
            return name
    return None


FONT = pick_font()
CJK_FONT = pick_cjk_font()
plt.rcParams["font.family"] = FONT
plt.rcParams["axes.unicode_minus"] = False


# ------------------------------------------------------------------ analysis
def smooth(y, k=5):
    if k <= 1 or len(y) < k:
        return y
    pad = k // 2
    yp = np.r_[np.full(pad, y[0]), y, np.full(pad, y[-1])]
    return np.convolve(yp, np.ones(k) / k, mode="same")[pad:pad + len(y)]


def local_maxima(ys):
    return [i for i in range(2, len(ys) - 2)
            if ys[i] >= ys[i - 1] and ys[i] > ys[i + 1]
            and ys[i] > ys[i - 2] and ys[i] >= ys[i + 2]]


def baseline_under(x, ys, i, win=BASE_WIN):
    """I_baseline: the higher of the two local minima flanking the peak."""
    lo = int(np.searchsorted(x, x[i] - win, side="left"))
    hi = int(np.searchsorted(x, x[i] + win, side="right"))
    left = ys[lo:i + 1]
    right = ys[i:hi]
    lmin = float(left.min()) if left.size else float(ys[i])
    rmin = float(right.min()) if right.size else float(ys[i])
    return max(lmin, rmin)


def gauss_fit(x, y, mu0, s0):
    """Gaussian on a linear background: A*exp(-(x-mu)^2/2s^2) + b + c*(x-mu)."""
    p = np.array([y.max() - y.min(), mu0, s0, y.min(), 0.0], float)
    for _ in range(500):
        A, mu, s, b, c = p
        s = abs(s) + 1e-6
        e = np.exp(-(x - mu) ** 2 / (2 * s * s))
        r = y - (A * e + b + c * (x - mu))
        J = np.column_stack([e, A * e * (x - mu) / s ** 2,
                             A * e * (x - mu) ** 2 / s ** 3,
                             np.ones_like(x), (x - mu)])
        try:
            dp = np.linalg.lstsq(J, r, rcond=None)[0]
        except Exception:
            break
        p = p + 0.3 * dp
        if np.max(np.abs(dp)) < 1e-10:
            break
    return p


def parabolic_center(x, ys, i):
    if i < 1 or i >= len(x) - 1:
        return x[i]
    y1, y2, y3 = ys[i - 1], ys[i], ys[i + 1]
    d = y1 - 2 * y2 + y3
    if d == 0:
        return x[i]
    return x[i] + 0.5 * (x[i + 1] - x[i - 1]) * (y1 - y3) / (2 * d)


def peak_center(x, y, ys, i):
    """Gaussian + linear-background fit over +-FIT_WIN, else a 3-point parabola."""
    w = FIT_WIN
    m = (x >= x[i] - w) & (x <= x[i] + w)
    if m.sum() < 6:
        return parabolic_center(x, ys, i)
    p = gauss_fit(x[m], y[m], x[i], max(2.0, w / 3.0))
    mu = p[1]
    if not np.isfinite(mu) or abs(mu - x[i]) > 0.5 * w:
        return parabolic_center(x, ys, i)
    return mu


def noise_sigma(y):
    """用差分的中位数绝对偏差稳健估计噪声标准差。

    白噪声下 diff 的 MAD*1.4826 ≈ sigma*sqrt(2), 故再除以 sqrt(2)。
    MAD 抗异常值, 尖峰和基线漂移不会把它拉高。读原始 y (非平滑) 更准。
    """
    d = np.diff(y)
    med = np.median(d)
    return float(1.4826 * np.median(np.abs(d - med)) / np.sqrt(2.0))


def auto_threshold(x, y, k_sigma=AUTO_K_SIGMA, ratio=AUTO_PEAK_RATIO,
                   zero_at=0.0, zero_guard=ZERO_GUARD):
    """为一条谱算自适应峰高阈值: max(k_sigma*σ, ratio*最强峰H)。

    两条约束缺一不可:
      - 噪声下限 k_sigma*σ 让弱信号谱也有一个统计下限, 不至于把随机毛刺当峰;
      - 相对主峰比例 ratio*H_max 在高动态范围的谱上自动抬高阈值, 压掉中等
        杂散峰 —— 这是固定噪声倍数做不到的 (最弱真峰可能比杂峰还矮, 但它们
        与主峰的强度比稳定)。
    σ 用 noise_sigma() 稳健估计; 最强峰 H 用与 find_peaks 一致的
    baseline_under 定义在排除激光线后的局部极大里取。
    返回 (阈值, σ, 最强峰H); 没有候选峰时退回 k_sigma*σ。
    """
    ys = smooth(y)
    sig = noise_sigma(y)
    hs = []
    for i in local_maxima(ys):
        if zero_guard and abs(x[i] - zero_at) <= zero_guard:
            continue
        hs.append(float(y[i]) - baseline_under(x, ys, i))
    hmax = max(hs) if hs else 0.0
    return max(k_sigma * sig, ratio * hmax), sig, hmax


def find_peaks(x, y, ys, h_min, max_labels=MAX_LABELS,
               zero_at=0.0, zero_guard=ZERO_GUARD):
    """Peaks whose height above baseline H = I_max - I_baseline clears h_min.

    max_labels keeps the tallest few, so a crowded plot labels the peaks that
    matter instead of the leftmost ones.

    zero_at / zero_guard 排除激光线（瑞利散射）漏光造成的假峰：0 cm-1 附近
    那个又尖又窄的峰不是拉曼峰。zero_at 是激光线所在的波数（单谱模式校正后
    等于 shift，堆叠模式为 0），zero_guard 是排除半宽；用 0 关闭排除。
    """
    found = []
    for i in local_maxima(ys):
        # 激光线漏光：落在排除区内的局部极大直接丢掉，不做拟合
        if zero_guard and abs(x[i] - zero_at) <= zero_guard:
            continue
        base = baseline_under(x, ys, i)
        h = float(y[i]) - base
        if h >= h_min:
            found.append((h, i, base))
    if not found:
        return []
    # one label per feature
    found.sort(key=lambda t: -t[0])
    kept = []
    for h, i, base in found:
        if all(abs(x[i] - x[j]) > MERGE_DX for _, j, _ in kept):
            kept.append((h, i, base))
    kept.sort(key=lambda t: x[t[1]])
    if len(kept) > max_labels:
        # 超出上限时按峰高筛，不是按横轴位置截断
        tall = sorted(kept, key=lambda t: -t[0])[:max_labels]
        kept = sorted(tall, key=lambda t: x[t[1]])
    return kept


# -------------------------------------------------------------------- figure
def label_peaks(ax, fig, ax_rect, lo, hi, color, fs, items,
                pad, stem_lo, stem_hi, step_up):
    """Leader line + value above each peak, shared by both figure types.

    items: [(centre, y at the peak), ...] already sorted left to right.
    Offsets are passed in data units, so the caller keeps its own spacing style
    (single plots use fractions of the y-span, stacked ones fractions of the
    per-curve band). Labels closer than the text is wide step up a level instead
    of being dropped, and labels near the frame edges align inward so the canvas
    does not clip them.
    """
    fig_w, fig_h = fig.get_size_inches()
    ax_w_pt = ax_rect[2] * fig_w * 72.0
    text_w_pt = 0.62 * fs * 6.0               # "1281.79"
    min_dx = text_w_pt / ax_w_pt * (hi - lo) * 1.20

    last_x, level = None, 0
    for c, yp in items:
        if last_x is not None and c - last_x < min_dx:
            level = (level + 1) % 3
        else:
            level = 0
        last_x = c
        ha = "center"
        if c - lo < 0.5 * min_dx:
            ha = "left"
        elif hi - c < 0.5 * min_dx:
            ha = "right"
        ax.plot([c, c], [yp + stem_lo, yp + stem_hi], color=color, lw=LW_CURVE,
                solid_capstyle="butt", zorder=3, clip_on=True)
        ax.text(c, yp + pad + level * step_up, "%.2f" % c, color=color,
                fontsize=fs, fontfamily=FONT, ha=ha, va="bottom", zorder=4,
                clip_on=True)


def style_axes(ax, fig, lo, hi, y_numbers=True, y_ticks=True,
               ytick_x=YTICK_X, xl_pos=XL_POS):
    """House-style frame, ticks and axis labels. The caller sets the y limits."""
    ax.set_xlim(lo, hi)

    span_x = hi - lo
    step = 100.0 if span_x <= 1200 else (200.0 if span_x <= 3000 else 300.0)
    ax.xaxis.set_major_locator(MultipleLocator(step))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=6, steps=[1, 2, 2.5, 3, 5, 10]))

    for sp in ax.spines.values():
        sp.set_linewidth(LW_FRAME)
        sp.set_color(BLACK)

    ax.tick_params(axis="x", which="major", direction="inout", length=TICK_LEN,
                   width=LW_FRAME, color=BLACK, labelsize=FS, pad=2.0,
                   top=False, bottom=True)
    ax.tick_params(axis="y", which="major", direction="inout", length=TICK_LEN,
                   width=LW_FRAME, color=BLACK, labelleft=False,
                   right=False, left=y_ticks)
    # 刻度标签的字体和颜色由 rcParams 统一给（font.family 已是 FONT，刻度默认
    # 黑色），不必逐个取回标签对象：get_xticklabels() 会为每个标签重新量一次
    # 字宽，26 张图累计多花约 0.5 s。
    # 只有当刻度正好落在区间端点上时才需要内缩对齐，端点位置直接算，不取标签。
    for val, ha in ((lo, "left"), (hi, "right")):
        if abs(val / step - round(val / step)) > 1e-6:
            continue                     # 端点不是整数刻度，没有标签要挪
        for lab in ax.get_xticklabels():
            try:
                tv = float(lab.get_position()[0])
            except Exception:
                continue
            if abs(tv - val) < step * 1e-6:
                lab.set_ha(ha)
                break

    # y tick labels are drawn by hand: matplotlib measures the unrotated box for
    # rotated ticks, which puts them on the wrong pixels
    if y_numbers:
        ylo, yhi = ax.get_ylim()
        ytrans = blended_transform_factory(fig.transFigure, ax.transData)
        for val in ax.get_yticks():
            if not (ylo <= val <= yhi):
                continue
            ax.text(ytick_x, val, "%d" % round(val), transform=ytrans, rotation=90,
                    rotation_mode="anchor", ha="center", va="center", fontsize=FS,
                    fontfamily=FONT, color=BLACK, zorder=3)

    ax.set_xlabel("rel. 1/cm", fontsize=FS, fontfamily=FONT, color=BLACK, labelpad=0,
                  ha="center", va="center", rotation_mode="anchor")
    ax.set_ylabel("CCD cts", fontsize=FS, fontfamily=FONT, color=BLACK, labelpad=0,
                  ha="center", va="center", rotation_mode="anchor")
    ax.xaxis.set_label_coords(xl_pos[0], xl_pos[1], transform=fig.transFigure)
    ax.yaxis.set_label_coords(YL_POS[0], YL_POS[1], transform=fig.transFigure)


def render(x, y, peaks, out_path, xlim=None, centers=None, normalize=False):
    """centers: 峰位, 由调用方算好传进来; 不给就自己算 (会重复做高斯拟合)."""
    if normalize:
        # 单图跟着 Excel 的 normalize 走, 同一个文件的两张图纵轴才对得上
        span = float(y.max() - y.min())
        y = (y - y.min()) / (span if span else 1.0)
    ymin, ymax = float(y.min()), float(y.max())
    span = max(ymax - ymin, 1e-9)

    fig = plt.figure(figsize=(FIG_W, FIG_H), dpi=DPI, facecolor="white")
    rect = [AX_L, AX_B, AX_R - AX_L, AX_T - AX_B]
    ax = fig.add_axes(rect)

    ax.plot(x, y, color=RED, lw=LW_CURVE, solid_joinstyle="miter",
            solid_capstyle="butt", antialiased=False, zorder=2)

    # Pin the axis to the requested window rather than the data extent: the first
    # and last samples rarely land exactly on the limits, and letting them set the
    # range pushes the boundary tick labels out of view.
    # 单边 xlim: 空的那端回退到数据边界
    if xlim is None:
        lo, hi = x[0], x[-1]
    else:
        lo = float(xlim[0]) if xlim[0] is not None else float(x[0])
        hi = float(xlim[1]) if xlim[1] is not None else float(x[-1])
    ax.set_ylim(ymin - 0.02 * span, ymax + 0.20 * span)
    style_axes(ax, fig, lo, hi, y_numbers=not normalize, y_ticks=not normalize)
    if normalize:
        ax.set_ylabel("norm.", fontsize=FS, fontfamily=FONT, color=BLACK,
                      labelpad=0, ha="center", va="center", rotation_mode="anchor")
        ax.yaxis.set_label_coords(YL_POS[0], YL_POS[1], transform=fig.transFigure)

    if centers is None:
        ys = smooth(y)
        centers = [peak_center(x, y, ys, i) for _, i, _ in peaks]
    items = sorted((centers[k], float(y[i])) for k, (_, i, _) in enumerate(peaks))
    label_peaks(ax, fig, rect, lo, hi, RED, FS, items,
                pad=PAD_TEXT * span, stem_lo=STEM_LO * span,
                stem_hi=STEM_HI * span, step_up=0.14 * span)

    save_figure(fig, out_path)
    plt.close(fig)


# ------------------------------------------------------------------- stacked
STACK_COLORS = ["#c0392b", "#2b5fa8", "#2e7d32", "#8e44ad",
                "#c47c10", "#12776a", "#7f8c8d", "#ad1457"]

# stack.xlsx 的表头写给人看：全中文，带 0/1 的列顺便把取值提示挂在括号里。
# 读表时统一还原成下面这些键，所以列顺序、中英文表头都不影响。
STACK_HEADERS = ["文件", "图例名称", "归一化(0/1)", "标注峰位(0/1)",
                 "峰高阈值", "纵向偏移", "区间下限", "区间上限"]
STACK_KEYS = ["file", "label", "normalize", "peaks", "height", "offset",
              "xmin", "xmax"]
HEADER_ALIASES = {
    "文件": "file", "文件名": "file", "file": "file",
    "图例": "label", "图例名称": "label", "名称": "label", "label": "label",
    "归一化": "normalize", "normalize": "normalize",
    "标注峰位": "peaks", "峰位": "peaks", "peaks": "peaks",
    "峰高阈值": "height", "峰高": "height", "阈值": "height", "height": "height",
    "纵向偏移": "offset", "偏移": "offset", "offset": "offset",
    "区间下限": "xmin", "起点": "xmin", "xmin": "xmin",
    "区间上限": "xmax", "终点": "xmax", "xmax": "xmax",
}


def norm_header(name):
    """表头 -> 键：去掉 (0/1) 这类提示和空白，再查中英文别名。

    认不出的原样返回，所以拼错的列名不会悄悄匹配到别的列。
    """
    s = "" if name is None else str(name)
    s = re.sub(r"[(（][^)）]*[)）]", "", s)
    s = s.replace(" ", "").replace("\u3000", "").strip().lower()
    return HEADER_ALIASES.get(s, s)


def load_stack(path):
    """Read the stack workbook.

    Only the first sheet is read; the header row names the columns, so their
    order does not matter. Headers may be written in Chinese or carry a value
    hint like "normalize(0/1)" - norm_header maps them back to these keys:
        file       txt file name in data\\ (required; .txt may be omitted)
        label      text drawn in the legend; "|" breaks the line, empty = file stem
        normalize  1/0 - scale this curve to its own 0..1 range
        offset     vertical offset; empty = automatic spacing
        peaks      1/0 - label this curve's peak positions
        height     peak threshold H, counts on the raw data; empty = --height
        xmin/xmax  optional shared axis window; either side may be left empty
                   (that side falls back to the data boundary); both empty = full range
    Blank rows and rows whose first cell starts with # are ignored.
    """
    try:
        import openpyxl
    except ImportError:
        raise ValueError("reading stack.xlsx needs openpyxl: "
                         "python -m pip install openpyxl")
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        header, rows = None, []
        window = [None, None]
        for raw in wb.worksheets[0].iter_rows(values_only=True):
            cells = ["" if c is None else str(c).strip() for c in raw]
            if not any(cells):
                continue
            if header is None:
                header = [norm_header(c) for c in cells]
                idx = {n: i for i, n in enumerate(header) if n}
                continue
            if cells[0].startswith("#"):
                continue
            get = lambda name: (cells[idx[name]]
                                if name in idx and idx[name] < len(cells) else "")
            name = get("file")
            if not name:
                continue
            for k, col in enumerate(("xmin", "xmax")):
                raw_v = get(col)
                if raw_v and window[k] is None:
                    window[k] = float(raw_v)
            raw_off = get("offset")
            raw_h = get("height")
            raw_pk = get("peaks").lower()
            rows.append({
                "file": name,
                "label": get("label"),
                "normalize": get("normalize").lower()
                             in ("1", "1.0", "y", "yes", "true", "是"),
                "offset": float(raw_off) if raw_off else None,
                # 没有 peaks 列或留空时默认标峰: 堆叠图不标峰反而更奇怪
                "peaks": raw_pk not in ("0", "0.0", "n", "no", "false", "否"),
                "height": float(raw_h) if raw_h else None,
            })
    finally:
        wb.close()
    if not rows:
        raise ValueError("no data rows in %s" % path)
    if len({r["normalize"] for r in rows}) > 1:
        # 归一化和原始强度混在一张图里，弱的谱会被压成一条线，没法比
        raise ValueError("normalize must be the same (all 1 or all 0) for every "
                         "row in %s" % path)
    # 允许只填一端: 空的那端在切窗/画轴时自动取数据边界, 不必两端都填。
    # 两端都空时返回 None, 沿用"整段数据"的语义。
    if window[0] is None and window[1] is None:
        return rows, None
    return rows, (window[0], window[1])


def resolve_src(src, name):
    """Excel 里可以只写文件名, 省略 .txt; 也允许绝对路径."""
    path = name if os.path.isabs(name) else os.path.join(src, name)
    if not os.path.exists(path) and not path.lower().endswith(".txt"):
        path += ".txt"
    return path


def render_stack(rows, det, out_path, xlim=None):
    """Draw every row on one axis, each curve offset above the previous one.

    det comes from render_one: the windowed data and every detected peak, so the
    per-file single figures already used the same load and the same detection.
    Peak detection ran on the raw counts (before normalisation) so the height
    threshold keeps its "counts" meaning; the label is placed at the
    offset+normalised position of the same point.
    """
    curves = []
    for r, d in zip(rows, det):
        y = d["y"]
        y_raw = y
        if r["normalize"]:
            # 0..1 per curve, so every sample keeps the same visual weight
            span = float(y.max() - y.min())
            y = (y - y.min()) / (span if span else 1.0)
        curves.append((r, d["path"], d["x"], y, y_raw))

    # 单边 xlim: 空的那端用数据边界补齐
    data_lo = min(c[2].min() for c in curves)
    data_hi = max(c[2].max() for c in curves)
    if xlim is None:
        lo, hi = data_lo, data_hi
    else:
        lo = float(xlim[0]) if xlim[0] is not None else data_lo
        hi = float(xlim[1]) if xlim[1] is not None else data_hi

    spans = [float(y.max() - y.min()) or 1.0 for _, _, _, y, _ in curves]
    # 要标峰位时留出"峰顶到上一条谱基线"的空间，否则标签会被上一条谱的峰穿过
    gap_ratio = 0.75 if any(r["peaks"] for r in rows) else 0.35
    gap = gap_ratio * max(spans)
    offs, top = [], 0.0
    for r, sp in zip(rows, spans):
        off = r["offset"] if r["offset"] is not None else top
        offs.append(off)
        top = off + sp + gap

    y0 = min(o + float(y.min()) for o, (_, _, _, y, _) in zip(offs, curves))
    y1 = max(o + float(y.max()) for o, (_, _, _, y, _) in zip(offs, curves))
    span_y = max(y1 - y0, 1e-9)

    n = len(curves)
    # 谱越多画布越高：固定的 1024 px 塞十几条谱，标注必然糊成一片
    px_h = max(PX_H, 300 + 62 * n)
    fig_h = px_h / float(DPI)
    # 图例条目多了放不进框里，改到右侧框外，并相应加宽画布
    legend_out = n > 10
    fig_w = FIG_W + (2.6 if legend_out else 0.0)
    fig = plt.figure(figsize=(fig_w, fig_h), dpi=DPI, facecolor="white")
    ax_rect = [AX_L * FIG_W / fig_w, AX_B,
               (AX_R - AX_L) * FIG_W / fig_w, AX_T - AX_B]
    ax = fig.add_axes(ax_rect)

    # 标注尺寸全部按"字号折合成数据单位"来定：谱多、画布窄时字自动变小，
    # 间距也跟着字走，不会像固定比例那样在密集排布下压成一团
    ax_h_pt = ax_rect[3] * fig_h * 72.0
    band_pt = ax_h_pt / (n * (1.0 + gap / max(spans)))
    fs_peak = max(5.0, min(FS * 0.92, 0.55 * band_pt))
    label_h = fs_peak / ax_h_pt * span_y        # 一行字在 y 方向占多少
    pad = 0.40 * label_h
    stem_lo, stem_hi = 0.15 * label_h, 1.05 * label_h
    step_up = 1.30 * label_h                    # 错层一格的位移
    # 谱多的时候每条只标最高的几个，标满 8 个纯属互相遮挡
    max_labels = 8 if n <= 6 else (5 if n <= 12 else 3)

    handles, labels = [], []
    for k, ((r, path, x, y, y_raw), off) in enumerate(zip(curves, offs)):
        color = STACK_COLORS[k % len(STACK_COLORS)]
        ln, = ax.plot(x, y + off, color=color, lw=1.15, antialiased=True,
                      solid_capstyle="round", zorder=2)
        handles.append(ln)
        labels.append((r["label"] or
                       os.path.splitext(os.path.basename(path))[0]).replace("|", "\n"))

        # 峰已经在上一步按文件并行检测过了, 这里只按谱条数截到最高的几个:
        # 谱多时标满 10 个纯属互相遮挡。
        peaks, centers = det[k]["peaks"], det[k]["centers"]
        if peaks:
            idx = np.flatnonzero((x >= lo) & (x <= hi))  # 窗口点的原始下标
            if len(peaks) > max_labels:
                top = sorted(range(len(peaks)), key=lambda q: -peaks[q][0])[:max_labels]
                top.sort(key=lambda q: peaks[q][1])
                peaks = [peaks[q] for q in top]
                centers = [centers[q] for q in top]
            items = sorted((centers[q], float(y[idx[peaks[q][1]]]) + off)
                           for q in range(len(peaks)))
            label_peaks(ax, fig, ax_rect, lo, hi, color, fs_peak, items,
                        pad=pad, stem_lo=stem_lo, stem_hi=stem_hi,
                        step_up=step_up)

    ax.set_ylim(y0 - 0.05 * span_y, y1 + 0.12 * span_y)
    # Normalised curves have no counts to report, so the y numbers are dropped
    # instead of showing meaningless offset values.
    y_numbers = not any(r["normalize"] for r in rows)
    # 画布变宽后，轴刻度数字和横轴标题要跟着新位置走，否则会跑到框外
    ytick_x = ax_rect[0] - 0.0055 if legend_out else YTICK_X
    xl_pos = (ax_rect[0] + 0.5 * ax_rect[2], XL_POS[1]) if legend_out else XL_POS
    style_axes(ax, fig, lo, hi, y_numbers=y_numbers, y_ticks=y_numbers,
               ytick_x=ytick_x, xl_pos=xl_pos)
    if y_numbers:
        ax.set_ylabel("CCD cts", fontsize=FS, fontfamily=FONT, color=BLACK,
                      labelpad=0, ha="center", va="center", rotation_mode="anchor")
    else:
        ax.set_ylabel("norm.", fontsize=FS, fontfamily=FONT, color=BLACK,
                      labelpad=0, ha="center", va="center", rotation_mode="anchor")
    ax.yaxis.set_label_coords(YL_POS[0], YL_POS[1], transform=fig.transFigure)

    # 图例：条目少放框内右上角，多了移到框外右侧，否则会溢出画布
    lfont = FONT if all(s.isascii() for s in labels) else (CJK_FONT or FONT)
    leg_kw = dict(frameon=False, handlelength=2.2, handletextpad=0.6,
                  labelspacing=0.5 if n <= 10 else 0.35,
                  fontsize=FS if n <= 10 else max(7.5, FS * 0.85))
    if legend_out:
        leg = ax.legend(handles, labels, loc="upper left",
                        bbox_to_anchor=(1.02, 1.0), borderaxespad=0.0, **leg_kw)
    else:
        leg = ax.legend(handles, labels, loc="upper right",
                        borderaxespad=0.35, **leg_kw)
    for txt in leg.get_texts():
        txt.set_fontfamily(lfont)
        txt.set_color(BLACK)

    save_figure(fig, out_path)
    plt.close(fig)


def save_figure(fig, out_path, attempts=3):
    """Write the png through a temp file, retrying a few times.

    Saving straight onto the target is fragile on Windows: an antivirus or sync
    filter can hold the file for a moment and the open() fails with a bare
    OSError. Rendering to a temp file first and swapping it in with os.replace
    keeps a half-written png from ever being left behind, and the retries ride
    out the transient locks.
    """
    tmp = out_path + ".part"
    # compress_level=1: PNG 大一点, 但每张省 ~20 ms (26 张省半秒), 肉眼看不出差别
    pil_kw = {"compress_level": 1}
    for n in range(attempts):
        try:
            fig.savefig(tmp, dpi=DPI, facecolor="white", format="png",
                        pil_kwargs=pil_kw)
            os.replace(tmp, out_path)
            return
        except OSError:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            time.sleep(0.4 * (n + 1))
    # last resort: write straight at the target, so a stuck rename does not cost
    # the figure
    fig.savefig(out_path, dpi=DPI, facecolor="white", format="png",
                pil_kwargs=pil_kw)


def safe_stem(name):
    """A file stem Windows will accept as a file name."""
    bad = '<>:"/\\|?*'
    stem = "".join("_" if c in bad else c for c in name)
    stem = stem.strip(" .")
    if not stem:
        stem = "untitled"
    reserved = {"CON", "PRN", "AUX", "NUL"}
    reserved |= {"COM%d" % i for i in range(1, 10)}
    reserved |= {"LPT%d" % i for i in range(1, 10)}
    if stem.upper() in reserved:
        stem = "_" + stem
    return stem


# ---------------------------------------------------------------------- main
def load_txt(path):
    a = np.loadtxt(path, comments="#")
    if a.ndim == 1:
        a = a.reshape(1, -1)
    if a.shape[1] < 2:
        raise ValueError("need at least two columns")
    return a[:, 0].astype(float), a[:, 1].astype(float)


def warm():
    """空任务: 让子进程真正导入 numpy/matplotlib。

    start_pool 之前提交 int() 是没用的 —— 子进程解包内置类型不需要 import
    本模块, numpy/matplotlib 要等到第一个真任务才加载。提交本模块的函数才会
    触发那次导入 (~0.4 s), 也就才能和主进程的读表重叠。
    """
    return None


# 每个 worker 都要重新导入一遍 numpy/matplotlib (~0.4 s), 所以不是越多越快:
# 16 核上实测 6 个 1716 ms, 8 个 1734~1856 ms, 16 个 2270 ms。
WORKERS = min(6, os.cpu_count() or 1)


def start_pool():
    """提前把 worker 拉起来但不等待。

    子进程要重新导入 numpy/matplotlib (~0.4 s), 先提交几个 warm 让它们开始
    导入, 主进程继续读表, 两件事重叠。池起不来就返回 None 走串行。
    """
    if WORKERS < 2:
        return None
    try:
        pool = cf.ProcessPoolExecutor(max_workers=WORKERS)
        for _ in range(WORKERS):
            pool.submit(warm)            # 只为触发子进程导入, 不取结果
        return pool
    except Exception:
        return None


def render_one(job):
    """子进程入口: 读一个 txt、切窗口、找峰、画单谱图, 把曲线数据带回去。

    这三步都只依赖一个文件, 且是最费 CPU 的部分 (26 个文件的高斯拟合约
    0.3 s, 单张 2048x1024 图约 76 ms), 所以合成一个任务按文件并行。主进程
    拿到返回的数据再画合并图, 读文件和找峰都只做一遍。

    job: (path, win_lo, win_hi, shift, do_peaks, thr, max_labels,
          out_path, normalize, xlim, zero_guard)
        win_lo/win_hi  None = 用数据自身范围
        shift          峰位校正量, 加到 xw 上
        xlim           画轴范围, None = 用窗口数据自身范围
        zero_guard     激光线排除半宽; 0 = 关闭
    读文件失败放 err, 写图失败放 werr, 都不抛异常, 免得一个坏文件毁掉整批。
    窗口内点数不足 8 个时不画图 (n 会小于 8), 由调用方决定怎么报。
    """
    (path, win_lo, win_hi, shift, do_peaks, thr, max_labels,
     out_path, normalize, xlim, zero_guard) = job
    try:
        x, y = load_txt(path)
    except Exception as exc:
        return {"path": path, "n": 0, "x": None, "y": None, "peaks": [],
                "centers": [], "err": str(exc), "werr": None}
    lo = float(x[0]) if win_lo is None else float(win_lo)
    hi = float(x[-1]) if win_hi is None else float(win_hi)
    m = (x >= lo) & (x <= hi)
    xw, yw = x[m], y[m]
    if shift:
        xw = xw + shift
    peaks, centers = [], []
    if do_peaks and len(xw) >= 8:
        ys = smooth(yw)
        peaks = find_peaks(xw, yw, ys, thr, max_labels=max_labels,
                           zero_at=shift, zero_guard=zero_guard)
        centers = [peak_center(xw, yw, ys, i) for _, i, _ in peaks]
    werr = None
    if out_path is not None and len(xw) >= 8:
        try:
            render(xw, yw, peaks, out_path, xlim=xlim, centers=centers,
                   normalize=normalize)
        except OSError as exc:
            werr = str(exc)
    return {"path": path, "n": len(xw), "x": xw, "y": yw, "peaks": peaks,
            "centers": centers, "err": None, "werr": werr}


def run_jobs(jobs, pool=None):
    """并行跑一批 render_one。数据量小的时候不值得付进程启动的代价。"""
    if len(jobs) < 5 or WORKERS < 2:
        return [render_one(j) for j in jobs]
    try:
        if pool is not None:
            return list(pool.map(render_one, jobs))
        with cf.ProcessPoolExecutor(max_workers=WORKERS) as ex:
            return list(ex.map(render_one, jobs))
    except Exception:
        # 进程池起不来 (受限环境 / 打包) 就退回串行, 别让出图整体失败
        return [render_one(j) for j in jobs]


def prepare_out(out_dir):
    """建立输出目录, 并清掉上次运行留下的旧文件 (png / csv 等)。

    不清空的话, data 里已经删掉的谱对应的旧 png 会一直躺在 out 里,
    让人误以为是这次生成的。只删普通文件, 不动子目录; 被占用的文件
    删不掉就警告并跳过, 不让整个出图失败。
    """
    os.makedirs(out_dir, exist_ok=True)
    removed, failed = 0, []
    for name in os.listdir(out_dir):
        path = os.path.join(out_dir, name)
        if not (os.path.isfile(path) or os.path.islink(path)):
            continue
        try:
            os.remove(path)
            removed += 1
        except OSError:
            failed.append(name)
    if removed:
        print("  cleared %d old file(s) from %s" % (removed, out_dir))
    if failed:
        print("  WARNING: 无法清除旧文件(可能被占用): %s"
              % ", ".join(failed), file=sys.stderr)
    return removed


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Plot Raman spectra over a window.")
    ap.add_argument("--xmin", type=float, default=None, help="left edge, cm-1")
    ap.add_argument("--xmax", type=float, default=None, help="right edge, cm-1")
    ap.add_argument("--height", type=float, default=5.0,
                    help="label peaks with H = I_max - I_baseline at least this")
    ap.add_argument("--shift", type=float, default=0.0,
                    help="peak-position correction in cm-1: every wavenumber is "
                         "moved by this amount, positive to the right")
    ap.add_argument("--zero-guard", type=float, default=ZERO_GUARD,
                    help="ignore peaks within this half-width (cm-1) of the "
                         "laser line at zero; 0 disables the guard")
    ap.add_argument("--stack", default=None,
                    help="xlsx listing spectra to stack on one figure; each row "
                         "also gets its own single-spectrum figure")
    ap.add_argument("--src", default=os.path.join(here, "data"), help="input folder")
    ap.add_argument("--out", default=os.path.join(here, "out"), help="output folder")
    args = ap.parse_args(argv)

    if args.stack:
        # 先拉 worker 再读表: 子进程导入 numpy/matplotlib (~0.4 s) 正好被下面的
        # openpyxl 导入和读表 (~0.2 s) 盖住, 否则这段时间纯等。池建不起来就串行。
        pool = start_pool()
        try:
            try:
                rows, window = load_stack(args.stack)
            except (OSError, ValueError) as exc:
                print("ERROR: %s" % exc, file=sys.stderr)
                return 1
            if args.xmin is not None and args.xmax is not None:
                window = (args.xmin, args.xmax)
            prepare_out(args.out)
            out_path = os.path.join(args.out, "stack.png")

            # 读文件、找峰、画单谱图三件事都只依赖一个文件, 合成一个任务按文件
            # 并行; 合并图直接复用返回的曲线和峰, 不重算。
            stems, jobs = [], []
            for r in rows:
                path = resolve_src(args.src, r["file"])
                if not os.path.exists(path):
                    print("ERROR: no such file: %s" % path, file=sys.stderr)
                    return 1
                thr = r["height"] if r["height"] is not None else args.height
                stem = safe_stem(os.path.splitext(os.path.basename(path))[0])
                stems.append(stem)
                jobs.append((path,
                             window[0] if window else None,
                             window[1] if window else None,
                             0.0, r["peaks"], thr, MAX_LABELS,
                             os.path.join(args.out, stem + ".png"),
                             r["normalize"], window, args.zero_guard))
            det = run_jobs(jobs, pool)
        finally:
            if pool is not None:
                pool.shutdown(wait=True)

        for d in det:
            if d["err"]:
                print("ERROR: %s: %s" % (d["path"], d["err"]), file=sys.stderr)
                return 1
        try:
            render_stack(rows, det, out_path, xlim=window)
        except (OSError, ValueError) as exc:
            print("ERROR: %s" % exc, file=sys.stderr)
            return 1
        print("stacked %d curve(s) -> %s" % (len(rows), out_path))

        # 单图只有一条谱, 按单谱标准标: 最多 MAX_LABELS 个峰、正常字号、
        # 纵轴留 counts, 不套用堆叠图那套自适应缩减。
        made, failed = 0, []
        for stem, d in zip(stems, det):
            if d["n"] < 8:
                msg = "only %d points in the plotted window" % d["n"]
                failed.append((stem, msg))
                print("  FAILED %s: %s" % (stem, msg))
                continue
            if d["werr"]:
                failed.append((stem, d["werr"]))
                print("  FAILED %s: %s" % (stem, d["werr"]))
                continue
            made += 1
            print("  %-12s %d peak(s)  %s" % (stem, len(d["centers"]),
                  " ".join("%.1f" % c for c in d["centers"])))

        print("\n%d single figure(s) -> %s" % (made, args.out))
        if failed:
            print("\n%d file(s) could not be written:" % len(failed))
            for nm, msg in failed:
                print("  %s: %s" % (nm, msg))
            # 2 = 缺几张图但其余正常, 1 = 一张都没写出来
            return 2 if made else 1
        return 0

    if args.xmin is None or args.xmax is None:
        print("ERROR: --xmin and --xmax are required without --stack", file=sys.stderr)
        return 2
    if args.xmax <= args.xmin:
        print("ERROR: --xmax must be greater than --xmin", file=sys.stderr)
        return 2

    files = sorted(glob.glob(os.path.join(args.src, "*.txt")))
    if not files:
        print("ERROR: no .txt files in %s" % args.src, file=sys.stderr)
        return 1
    prepare_out(args.out)

    # 先把 worker 拉起来不等待: 子进程导入 numpy/matplotlib 的 0.4 s 正好被
    # 下面的读文件和峰检测盖住, 否则并行省下的时间会被进程启动吃掉一半。
    pool = start_pool()
    if args.shift:
        print("peak-position correction: %+.1f cm-1" % args.shift)

    # Peak-position correction. The requested window is expressed in corrected
    # wavenumbers, so the same stretch of raw data is the window shifted the
    # other way; adding the shift back afterwards makes the axis, the labels and
    # the exported table all read in corrected wavenumbers while the axis still
    # runs exactly from --xmin to --xmax.
    lo = args.xmin - args.shift
    hi = args.xmax - args.shift
    jobs = [(path, lo, hi, args.shift, True, args.height, MAX_LABELS,
             os.path.join(args.out,
                          safe_stem(os.path.splitext(os.path.basename(path))[0])
                          + ".png"),
             False, (args.xmin, args.xmax), args.zero_guard) for path in files]
    try:
        res = run_jobs(jobs, pool)
    finally:
        if pool is not None:
            pool.shutdown(wait=True)

    rows = []
    made = 0
    failed = []
    for path, d in zip(files, res):
        name = os.path.splitext(os.path.basename(path))[0]
        if d["err"]:
            print("  skip %s: %s" % (name, d["err"]))
            continue
        if d["n"] < 8:
            print("  skip %s: only %d points in [%g, %g] (raw %g..%g)"
                  % (name, d["n"], args.xmin, args.xmax, lo, hi))
            continue
        if d["werr"]:
            failed.append((name, d["werr"]))
            print("  FAILED %s: %s" % (name, d["werr"]))
            continue
        made += 1
        yw, peaks, centers = d["y"], d["peaks"], d["centers"]
        for k, (h, i, base) in enumerate(peaks):
            rows.append([name, "%.2f" % centers[k],
                         "%.1f" % h, "%.1f" % yw[i], "%.1f" % base,
                         "%.1f" % args.shift])
        print("  %-12s %d peak(s)  %s" % (name, len(peaks),
              " ".join("%.1f" % c for c in centers)))

    if rows:
        csv_path = os.path.join(args.out, "peaks.csv")
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["file", "peak_cm-1", "H_counts", "I_max", "I_baseline",
                        "shift_cm-1"])
            w.writerows(rows)
        print("\npeak table -> %s" % csv_path)

    print("\n%d figure(s) -> %s" % (made, args.out))
    if failed:
        print("\n%d file(s) could not be written:" % len(failed))
        for nm, msg in failed:
            print("  %s: %s" % (nm, msg))
        # 2 = some figures are missing but the rest are fine, 1 = nothing written
        return 2 if made else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

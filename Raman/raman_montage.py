# -*- coding: utf-8 -*-
"""Raman 拼图模式：上方一张实物图，下方该样品各位置的拉曼谱。

自动扫描（``--montage`` 后面不写值）只认下面这一套命名：

    实物图  <组名>-1-1.png   （1-1-1.png、10-1-1.png …）
    光谱    <组名>-<位置序号>.txt（1-1.txt、1-2.txt、1-3.txt …）

于是 1-1-1.png 对应 1-1 / 1-2 / 1-3 三条谱，10-1-1.png 对应 10-*，组名里的
连字符不影响匹配（"1-" 不会误吃 10-1）。已经出好的谱图 ``<组名>-<序号>.png``
因为不以 "-1-1" 结尾，不会被当成实物图。

换成别的命名（例如实物图叫 sample.png）时写 montage.xlsx：一行一条光谱，
同「组名」的行合成一张拼图，实物图只需在该组任意一行写一次。

排版沿用单谱/堆叠图的房子风格（同样 2048 px 宽、同样字号和刻度），下半区是
一条坐标轴：各位置的谱按基线纵向错开、颜色区分，峰位用同一套高斯拟合标注。
纵向错开意味着纵刻度数字是叠加后的位置，只适合看单条谱自身的相对强弱；
跨位置比较峰高用导出的 montage_peaks.csv 里的 H。
"""
from __future__ import annotations

import csv
import os
import re

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import is_color_like

from plot_raman import (AX_L, AX_R, BLACK, CJK_FONT, DPI, FONT, FS, LW_FRAME,
                        PX_W, ZERO_GUARD, auto_threshold, find_peaks,
                        label_peaks, load_txt, natural_key,
                        norm_header, peak_center, prepare_out, resolve_src,
                        safe_stem, save_figure, smooth, style_axes)

# 实物图：常见位图格式；其它扩展名按配置表写绝对路径也能用
PHOTO_EXTS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")

# 位置 1/2/3 默认红/绿/蓝，正好对上实物图上常见的三个十字标记颜色；
# 位置更多的组继续往下循环。
MONTAGE_COLORS = ["#e01b24", "#0f9d58", "#1a5fd0", "#b07d10",
                  "#7b3fa0", "#12776a", "#5f6b73", "#8a1538"]

PHOTO_MAX_H = 7.2          # 竖长照片的高度上限；横片一般不触发，宽度优先和谱图区对齐
PANEL_H = 4.6              # 下方谱图区高度，英寸
GAP_PHOTO = 0.52           # 实物图与谱图之间留白（图例横排在这条缝里）
MARGIN_TOP = 0.10
MARGIN_BOTTOM = 0.62       # 留给横轴刻度和 "rel. 1/cm" 的高度
CURVE_LW = 1.05
MONTAGE_MAX_LABELS = 6     # 每条谱最多标几个峰，多了互相遮挡

# --------------------------------------------------------------- config table
# 一行一条光谱；同组名的行合成一张拼图。列顺序无所谓，中英文表头都认。
MONTAGE_HEADERS = ["组名", "实物图", "文件", "图例名称", "归一化(0/1)",
                   "标注峰位(0/1)", "峰高阈值", "颜色", "纵向偏移",
                   "区间下限", "区间上限"]
MONTAGE_KEYS = ["group", "photo", "file", "label", "normalize", "peaks",
                "height", "color", "offset", "xmin", "xmax"]
# plot_raman 的 norm_header 已经处理了 (0/1) 提示和文件/图例/归一化等
# 中文别名，这里只补拼图独有的三列。
MONTAGE_ALIASES = {
    "组名": "group", "分组": "group", "group": "group",
    "实物图": "photo", "实拍图": "photo", "图像": "photo",
    "photo": "photo", "image": "photo",
    "颜色": "color", "color": "color", "colour": "color",
}


def montage_header(name):
    s = norm_header(name)
    return MONTAGE_ALIASES.get(s, s)


# ------------------------------------------------------------------ discovery
def split_spectrum(stem):
    """'1-2' -> ('1', 2)；不是 <组名>-<序号> 形式时返回 None。"""
    m = re.match(r"^(?P<spot>.+)-(\d+)$", stem)
    if not m:
        return None
    return m.group("spot"), int(m.group(2))


def photo_spot(stem):
    """实物图主名 -> 组名：只认以 '-1-1' 结尾的名字。"""
    if stem.endswith("-1-1"):
        return stem[:-4]
    return None


def _scan_folder(src):
    """把 --src 里的光谱和实物图各扫一遍，先不配对。

    光谱按 (组名, 位置序号) 归堆；实物图按 strip 掉 '-1-1' 后的组名归堆。
    分开扫的原因是资源管理器里实物图可能排在光谱前面。
    """
    spectra, photos = {}, {}
    for entry in sorted(os.listdir(src), key=natural_key):
        if entry.startswith("~$"):
            continue
        path = os.path.join(src, entry)
        if not os.path.isfile(path):
            continue
        stem, ext = os.path.splitext(entry)
        ext = ext.lower()
        if ext == ".txt":
            parsed = split_spectrum(stem)
            if parsed:
                spectra.setdefault(parsed[0], []).append((parsed[1], stem))
        elif ext in PHOTO_EXTS:
            spot = photo_spot(stem)
            if spot:
                photos.setdefault(spot, []).append(path)
    for items in spectra.values():
        items.sort(key=lambda t: (t[0], natural_key(t[1])))
    for items in photos.values():
        items.sort(key=lambda p: natural_key(os.path.basename(p)))
    return spectra, photos


def default_row(path, label=None, index=0):
    """自动扫描得到的一行：原始强度、标峰、阈值留空（用自适应阈值）。"""
    return {
        "file": os.path.basename(path),
        "path": path,
        "label": label if label is not None else
                 os.path.splitext(os.path.basename(path))[0],
        "normalize": False,
        "peaks": True,
        "height": None,
        "color": MONTAGE_COLORS[index % len(MONTAGE_COLORS)],
        "offset": None,
        "xmin": None,
        "xmax": None,
    }


def discover_groups(src):
    """按命名约定自动配对，返回 (groups, notes)。

    每个 group: {"name", "photo", "rows", "window"}。没有实物图的组跳过并
    记一条 note —— 拼图缺上半张就没有意义，硬画只会误导。
    """
    spectra, photos = _scan_folder(src)
    groups, notes = [], []
    for spot in sorted(spectra, key=natural_key):
        items = spectra[spot]
        found = photos.get(spot)
        if not found:
            notes.append("跳过 %s：有 %d 条光谱，但没有 %s-1-1 实物图"
                         % (spot, len(items), spot))
            continue
        if len(found) > 1:
            notes.append("%s 有 %d 张实物图，用 %s，其余忽略"
                         % (spot, len(found), os.path.basename(found[0])))
        rows = [default_row(os.path.join(src, stem + ".txt"),
                            label=stem, index=k)
                for k, (_, stem) in enumerate(items)]
        groups.append({"name": spot, "photo": found[0], "rows": rows,
                       "window": None})
    return groups, notes


# --------------------------------------------------------------------- config
def _truthy(text):
    return text.strip().lower() in ("1", "1.0", "y", "yes", "true", "是")


def load_montage(path, src):
    """读 montage.xlsx，返回 groups 列表（同自动扫描的结构）。

    校验口径与 stack.xlsx 一致：表头按名字找列、空行与 # 行跳过、同一组
    归一化必须一致。另外这里多两条：每组必须有实物图；实物图和光谱都必须
    存在，报错时带文件名。
    """
    try:
        import openpyxl
    except ImportError:
        raise ValueError("reading montage.xlsx needs openpyxl: "
                         "python -m pip install openpyxl")
    folder = os.path.dirname(os.path.abspath(path))
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        header, idx = None, {}
        groups, order = {}, []
        for raw in wb.worksheets[0].iter_rows(values_only=True):
            cells = ["" if c is None else str(c).strip() for c in raw]
            if not any(cells):
                continue
            if header is None:
                header = [montage_header(c) for c in cells]
                idx = {n: i for i, n in enumerate(header) if n}
                continue
            if cells[0].startswith("#"):
                continue

            def get(name):
                i = idx.get(name)
                return cells[i] if i is not None and i < len(cells) else ""

            name = get("group")
            file_name = get("file")
            if not name:
                # 组名留空时按文件推：1-2.txt -> 1，好让表格可以少写一列
                parsed = split_spectrum(os.path.splitext(file_name)[0])
                name = parsed[0] if parsed else ""
            if not name and not file_name:
                continue
            if not name:
                raise ValueError("%s：组名和文件都空着，无法判断属于哪一组"
                                 % path)
            if not file_name:
                raise ValueError("%s：组 %s 有一行没填文件" % (path, name))

            photo = get("photo") or None
            if photo and not os.path.isabs(photo):
                photo = os.path.join(src, photo)
            if photo and not os.path.exists(photo):
                raise ValueError("%s：组 %s 的实物图不存在：%s"
                                 % (path, name, photo))
            spectrum = resolve_src(src, file_name)
            if not os.path.exists(spectrum):
                raise ValueError("%s：组 %s 的光谱不存在：%s"
                                 % (path, name, spectrum))

            raw_h = get("height")
            raw_off = get("offset")
            color = get("color")
            if color and not is_color_like(color):
                raise ValueError("%s：颜色 %r 认不出，请写 #RRGGBB 或 red 这类"
                                 "matplotlib 认识的名字" % (path, color))
            row = {
                "file": file_name,
                "path": spectrum,
                "label": get("label") or os.path.splitext(file_name)[0],
                "normalize": _truthy(get("normalize")),
                "peaks": get("peaks").strip() == "" or _truthy(get("peaks")),
                "height": float(raw_h) if raw_h else None,
                "color": color or None,
                "offset": float(raw_off) if raw_off else None,
                "xmin": float(get("xmin")) if get("xmin") else None,
                "xmax": float(get("xmax")) if get("xmax") else None,
            }
            g = groups.get(name)
            if g is None:
                g = {"name": name, "photo": None, "rows": [],
                     "window": [None, None]}
                groups[name] = g
                order.append(name)
            g["rows"].append(row)
            if photo and not g["photo"]:
                g["photo"] = photo
            for k, key in enumerate(("xmin", "xmax")):
                value = row[key]
                if value is None:
                    continue
                if g["window"][k] is not None and g["window"][k] != value:
                    raise ValueError("%s：组 %s 的%s写了两个不同的值（%g / %g）"
                                     % (path, name, "区间下限" if k == 0 else "区间上限",
                                        g["window"][k], value))
                g["window"][k] = value
    finally:
        wb.close()

    if not order:
        raise ValueError("%s 里没有数据行" % path)
    out = []
    for name in order:
        g = groups[name]
        if not g["photo"]:
            raise ValueError("%s：组 %s 没填实物图" % (path, name))
        if not os.path.exists(g["photo"]):
            raise ValueError("%s：组 %s 的实物图不存在：%s"
                             % (path, name, g["photo"]))
        if len({r["normalize"] for r in g["rows"]}) > 1:
            raise ValueError("%s：组 %s 的归一化必须一致（全 0 或全 1）"
                             % (path, name))
        for k, row in enumerate(g["rows"]):
            if not row["color"]:
                row["color"] = MONTAGE_COLORS[k % len(MONTAGE_COLORS)]
        window = g["window"]
        g["window"] = None if window == [None, None] else tuple(window)
        out.append(g)
    return out


# -------------------------------------------------------------------- render
def photo_notes(photo_path):
    """实物图像素尺寸恰好是出图尺寸时多半是误存的谱图，提醒一句。"""
    try:
        from PIL import Image
        with Image.open(photo_path) as im:
            size = im.size
    except Exception:
        return []
    if size == (2048, 1024):
        return ["%s 的尺寸是 2048x1024，和本工具出的谱图一样，"
                "确认一下它是不是实物图" % os.path.basename(photo_path)]
    return []


def _prepare_photo(path):
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGB")
        # 只在真的过大时缩放，2048 宽的显微照片原样进图，避免二次重采样
        im.thumbnail((PX_W * 2, PX_W * 2), Image.LANCZOS)
        arr = np.asarray(im)
    return arr


def _window(curves, xlim):
    """横轴范围：配置/命令行给了就用，空的那端回退到数据边界。"""
    lo_data = min(float(c["x"].min()) for c in curves)
    hi_data = max(float(c["x"].max()) for c in curves)
    if xlim is None:
        return lo_data, hi_data
    lo = lo_data if xlim[0] is None else float(xlim[0])
    hi = hi_data if xlim[1] is None else float(xlim[1])
    return lo, hi


def render_montage(photo_path, curves, out_path, xlim=None):
    """画一张拼图。curves 见 run_montage 里构造的字典。"""
    img = _prepare_photo(photo_path)
    img_h, img_w = img.shape[:2]
    aspect = img_h / float(img_w)

    fig_w = PX_W / float(DPI)
    # 照片左右边缘和下方谱图区对齐：上下一张图的宽度必须一样，否则两侧
    # 各留一条白边，看起来像两张图硬拼在一起。
    band_w = (AX_R - AX_L) * fig_w
    photo_w = band_w
    photo_h = photo_w * aspect
    if photo_h > PHOTO_MAX_H:
        # 只有极端竖长的照片才缩窄，避免画布被拉成一米长
        photo_h = PHOTO_MAX_H
        photo_w = photo_h / aspect
    fig_h = MARGIN_TOP + photo_h + GAP_PHOTO + PANEL_H + MARGIN_BOTTOM

    fig = plt.figure(figsize=(fig_w, fig_h), dpi=DPI, facecolor="white")

    # ---- 上半区：实物图。轴框按图片长宽比给，imshow 正好铺满，不留白边
    photo_rect = [AX_L + (band_w - photo_w) / 2.0 / fig_w,
                  (fig_h - MARGIN_TOP - photo_h) / fig_h,
                  photo_w / fig_w, photo_h / fig_h]
    ax_img = fig.add_axes(photo_rect)
    ax_img.imshow(img, interpolation="antialiased", aspect="auto")
    ax_img.set_xticks([])
    ax_img.set_yticks([])
    for sp in ax_img.spines.values():
        sp.set_linewidth(LW_FRAME)
        sp.set_color(BLACK)

    # ---- 下半区：谱图
    lo, hi = _window(curves, xlim)
    prepared = []
    for c in curves:
        y = np.asarray(c["y"], dtype=float)
        if c["normalize"]:
            span = float(y.max() - y.min())
            y = (y - y.min()) / (span if span else 1.0)
        prepared.append((c, y))

    spans = [float(y.max() - y.min()) or 1.0 for _, y in prepared]
    # 要标峰位时留出一条谱到上一条谱基线的距离，否则标签会压到上面那条谱
    gap_ratio = 0.75 if any(c["peaks"] for c, _ in prepared) else 0.35
    gap = gap_ratio * max(spans)
    offs, top = [], 0.0
    for c, span in zip([c for c, _ in prepared], spans):
        off = c["offset"] if c["offset"] is not None else top
        offs.append(off)
        top = off + span + gap

    y0 = min(off + float(y.min()) for off, (_, y) in zip(offs, prepared))
    y1 = max(off + float(y.max()) for off, (_, y) in zip(offs, prepared))
    span_y = max(y1 - y0, 1e-9)

    rect = [AX_L, MARGIN_BOTTOM / fig_h, AX_R - AX_L, PANEL_H / fig_h]
    ax = fig.add_axes(rect)

    ax_h_pt = rect[3] * fig_h * 72.0
    band_pt = ax_h_pt / (len(prepared) * (1.0 + gap / max(spans)))
    fs_peak = max(5.0, min(FS * 0.92, 0.55 * band_pt))
    label_h = fs_peak / ax_h_pt * span_y
    pad = 0.40 * label_h
    stem_lo, stem_hi = 0.15 * label_h, 1.05 * label_h
    step_up = 1.30 * label_h

    handles, labels = [], []
    for k, ((c, y), off) in enumerate(zip(prepared, offs)):
        color = c["color"] or MONTAGE_COLORS[k % len(MONTAGE_COLORS)]
        ln, = ax.plot(c["x"], y + off, color=color, lw=CURVE_LW,
                      antialiased=True, solid_capstyle="round", zorder=2)
        handles.append(ln)
        labels.append(str(c["label"]).replace("|", "\n"))
        if c["peaks"]:
            items = sorted((c["centers"][q], float(y[c["peaks"][q][1]]) + off)
                           for q in range(len(c["peaks"])))
            label_peaks(ax, fig, rect, lo, hi, color, fs_peak, items,
                        pad=pad, stem_lo=stem_lo, stem_hi=stem_hi,
                        step_up=step_up, levels=4)

    # 上方留白要盖住最密的错层：4 档标签最多往上走 3 格，再加一行字高，
    # 否则贴顶那条谱的标签会被上边框切掉。
    head = max(0.12 * span_y, 5.6 * label_h)
    ax.set_ylim(y0 - 0.05 * span_y, y1 + head)
    y_numbers = not any(c["normalize"] for c, _ in prepared)
    style_axes(ax, fig, lo, hi, y_numbers=y_numbers, y_ticks=y_numbers,
               ytick_x=AX_L - 0.0055,
               xl_pos=(rect[0] + 0.5 * rect[2], 0.012))
    ax.set_ylabel("CCD cts" if y_numbers else "norm.", fontsize=FS,
                  fontfamily=FONT, color=BLACK, labelpad=0, ha="center",
                  va="center", rotation_mode="anchor")
    ax.yaxis.set_label_coords(AX_L - 0.026, rect[1] + 0.5 * rect[3],
                             transform=fig.transFigure)

    lfont = FONT if all(s.isascii() for s in labels) else (CJK_FONT or FONT)
    # 图例放到坐标轴外上方、右对齐：谱线本身占满整幅，图例搁框里必和右侧
    # 的峰位标签打架（荧光背景强的谱右端总有一串小峰）。
    # 一行放下全部：超过 3 条时若折行，第二行会顶到上方实物图上
    leg = ax.legend(handles, labels, loc="lower right",
                    bbox_to_anchor=(1.0, 1.004),
                    ncol=min(len(labels), 5), borderaxespad=0.0, frameon=False,
                    handlelength=2.2, handletextpad=0.6, columnspacing=1.4,
                    fontsize=FS)
    for txt in leg.get_texts():
        txt.set_fontfamily(lfont)
        txt.set_color(BLACK)

    save_figure(fig, out_path, compress_level=6)
    plt.close(fig)


# ----------------------------------------------------------------------- run
def _guard_output(src, out, groups):
    """清空输出目录之前，先确认没有任何输入躺在里面。"""
    out_real = os.path.realpath(out)
    src_real = os.path.realpath(src)
    if src_real == out_real or src_real.startswith(out_real + os.sep):
        raise ValueError("输入目录位于将被清空的输出目录内：%s；请换一个 --out"
                         % src)
    for g in groups:
        for path in [g["photo"]] + [r["path"] for r in g["rows"]]:
            real = os.path.realpath(path)
            if real == out_real or real.startswith(out_real + os.sep):
                raise ValueError("输入文件位于将被清空的输出目录内：%s" % path)


def run_montage(config_path, src, out, xmin=None, xmax=None, height=None,
                shift=0.0, zero_guard=ZERO_GUARD):
    """拼图模式的入口，返回退出码（0 全成功 / 2 部分失败 / 1 全失败）。"""
    if config_path:
        if not os.path.exists(config_path):
            raise ValueError("montage 配置表不存在：%s" % config_path)
        groups = load_montage(config_path, src)
        notes = []
    else:
        if not os.path.isdir(src):
            raise ValueError("输入目录不存在：%s" % src)
        groups, notes = discover_groups(src)

    if not groups:
        print("ERROR: 没有可拼图的组（需要有 <组名>-1-1 实物图和 "
              "<组名>-<序号>.txt 光谱）", flush=True)
        return 1
    for note in notes:
        print("  提示：%s" % note)
    for g in groups:
        for extra in photo_notes(g["photo"]):
            print("  提示：%s" % extra)

    _guard_output(src, out, groups)
    prepare_out(out)

    made, failed, table = 0, [], []
    for g in groups:
        name = g["name"]
        data = []
        for row in g["rows"]:
            try:
                x, y = load_txt(row["path"])
            except Exception as exc:
                data = None
                failed.append((name, "%s: %s" % (row["file"], exc)))
                break
            if shift:
                x = x + shift
            data.append((row, x, y))
        if not data:
            continue

        # 命令行显式给了区间就压过表格，和 --stack 的覆盖规则一致
        if xmin is not None or xmax is not None:
            window = (xmin, xmax)
        elif g["window"] is not None:
            window = g["window"]
        else:
            window = (None, None)

        lo = min(float(x.min()) for _, x, _ in data)
        hi = max(float(x.max()) for _, x, _ in data)
        lo = lo if window[0] is None else float(window[0])
        hi = hi if window[1] is None else float(window[1])
        if hi <= lo:
            failed.append((name, "区间上限必须大于下限"))
            continue

        curves = []
        for row, x, y in data:
            m = (x >= lo) & (x <= hi)
            xw, yw = x[m], y[m]
            if len(xw) < 8:
                curves = None
                failed.append((name, "%s: 区间内只有 %d 个点"
                               % (row["file"], len(xw))))
                break
            peaks, centers = [], []
            if row["peaks"]:
                ys = smooth(yw)
                # 行里填了就用行里的（原始 counts，跨谱可比）；留空就按这条谱
                # 自己的噪声和最强峰算自适应阈值 max(5.5σ, 1.1%×H_max)——
                # 弱信号谱用固定 5 counts 会把噪声鼓包标成峰。命令行显式给
                # --height 时仍按给定值，口径与 stack.xlsx 一致。
                thr = row["height"] if row["height"] is not None else height
                if thr is None:
                    thr, _sig, _hmax = auto_threshold(xw, yw, zero_at=shift,
                                                     zero_guard=zero_guard)
                peaks = find_peaks(xw, yw, ys, thr,
                                   max_labels=MONTAGE_MAX_LABELS,
                                   zero_at=shift, zero_guard=zero_guard)
                centers = [peak_center(xw, yw, ys, i) for _, i, _ in peaks]
            curves.append({"x": xw, "y": yw, "label": row["label"],
                           "color": row["color"], "normalize": row["normalize"],
                           "offset": row["offset"], "peaks": peaks,
                           "centers": centers})

        if not curves:
            continue
        peak_rows = []
        for row, c in zip([d[0] for d in data], curves):
            for q, (h, i, _) in enumerate(c["peaks"]):
                peak_rows.append([name, row["file"],
                                  "%.2f" % c["centers"][q],
                                  "%.1f" % float(c["y"][i]), "%.1f" % h])

        out_path = os.path.join(out, safe_stem(name) + ".png")
        try:
            render_montage(g["photo"], curves, out_path, xlim=(lo, hi))
        except OSError as exc:
            failed.append((name, str(exc)))
            continue
        except Exception as exc:
            failed.append((name, "画图失败: %s" % exc))
            continue
        made += 1
        table.extend(peak_rows)
        print("  %-10s %s + %d 谱 -> %s" % (name,
              os.path.basename(g["photo"]), len(curves),
              os.path.basename(out_path)))

    if table:
        import csv
        csv_path = os.path.join(out, "montage_peaks.csv")
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["group", "file", "peak_cm-1", "I_max", "H_counts"])
            w.writerows(table)

    print("\n%d montage(s) -> %s" % (made, out))
    if failed:
        print("\n%d group(s) failed:" % len(failed))
        for nm, msg in failed:
            print("  %s: %s" % (nm, msg))
        return 2 if made else 1
    return 0

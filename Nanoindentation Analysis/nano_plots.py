# -*- coding: utf-8 -*-
"""纳米压痕结果绘图：总图、分组/配对对比、每个样品编号、每个文件单图、质控概览。"""
from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

from nano_core import mean_sd


def pick_cjk_font(candidates):
    names = {f.name for f in font_manager.fontManager.ttflist}
    for name in candidates:
        if name in names:
            return name
    return "DejaVu Sans"


def init_style(cfg):
    font = pick_cjk_font(cfg["绘图"]["字体候选"])
    plt.rcParams.update({
        "font.family": font,
        "axes.unicode_minus": False,
        "font.size": 10,
        "axes.titlesize": 12,
        "figure.dpi": cfg["绘图"]["dpi"],
        "savefig.dpi": cfg["绘图"]["dpi"],
        "savefig.bbox": "tight",
        "axes.grid": True,
        "grid.alpha": 0.25,
    })
    return font


def sample_color(cfg, sample):
    return cfg["绘图"]["配色"].get(str(sample), cfg["绘图"]["配色"]["其他"])


def _save(fig, out_dir, name):
    path = Path(out_dir) / name
    fig.savefig(path)
    plt.close(fig)
    return path


def file_label(f):
    """图例名称取 Excel 配置，自动附上样品编号，无需在图例名称里手写。"""
    label = str(f.get("label") or "").strip()
    if not label:
        return f["source"]
    sample = f.get("sample")
    if sample in (None, ""):
        return label
    s = str(sample).strip()
    if not s or re.search(r"[（(]\s*" + re.escape(s) + r"\s*[)）]\s*$", label):
        return label
    return f"{label}({s})"


def sample_order(analysis, cfg):
    groups = analysis["groups"]
    order = [g for g in cfg["组间比较"].get("样品编号顺序", []) if g in groups]
    order += [g for g in sorted(groups, key=str) if g not in order]
    return order


def _legend(labels, colors, loc="lower right"):
    handles = [plt.Line2D([], [], linestyle="none", marker="o", color=c, label=str(l))
               for l, c in zip(labels, colors)]
    plt.legend(handles=handles, loc=loc, fontsize=8, frameon=False)


def _safe(name):
    return re.sub(r'[\\/:*?"<>|]+', "_", str(name))


def _sample_mean(vals):
    m, _, _ = mean_sd(vals)
    return m if m == m else float("nan")


# --------------------------------------------------------------- 总图

def plot_overview(analysis, cfg, out_dir):
    """一张总图：全部 P-h 曲线 + E/H 单次压痕 + E-H 平面。"""
    indents = analysis["indents"]
    order = sample_order(analysis, cfg)
    colors = {g: sample_color(cfg, g) for g in order}
    fig, axes = plt.subplots(2, 2, figsize=(13.5, 9))
    ax = axes[0, 0]
    drawn = set()
    for it in indents:
        cur = it.get("curve_raw")
        if not cur or cur["h"].size < 10:
            continue
        key = it["sample"]
        ax.plot(cur["h"], cur["P"], color=colors.get(key, "#5c6b73"),
                lw=0.65, alpha=0.35, label=str(key) if key not in drawn else None)
        drawn.add(key)
    if drawn:
        _legend(list(drawn), [colors.get(k, "#5c6b73") for k in drawn])
    else:
        ax.text(0.5, 0.5, "无载荷-位移曲线数据（--no-curves）",
                ha="center", va="center", transform=ax.transAxes)
    ax.set(xlabel="压入深度 h (nm)", ylabel="载荷 P (mN)",
           title=f"全部载荷-位移曲线（{len(drawn)} 种样品编号）")

    for row, metric, title in ((0, "E", "弹性模量 E"), (1, "H", "硬度 H")):
        ax = axes[row, 1] if metric == "E" else axes[1, 0]
        for gi, g in enumerate(order):
            vals = [it[metric] for it in indents
                    if it["sample"] == g and it.get(metric) is not None]
            if not vals:
                continue
            x = np.full(len(vals), gi) + np.random.default_rng(gi + 11).uniform(-0.18, 0.18, len(vals))
            ax.scatter(x, vals, s=20, alpha=0.65, color=colors[g], zorder=3)
            ax.hlines(float(np.mean(vals)), gi - 0.28, gi + 0.28,
                      color="#222222", lw=1.6, zorder=4)
        ax.set_xticks(range(len(order)), [str(g) for g in order])
        ax.set(ylabel=f"{title} (GPa)", title=f"全部单次压痕：{title}（横线为均值）")

    ax = axes[1, 1]
    for g in order:
        pts = [(it["E"], it["H"]) for it in indents
               if it["sample"] == g and it.get("E") is not None and it.get("H") is not None]
        if pts:
            ax.scatter([p[0] for p in pts], [p[1] for p in pts], s=22, alpha=0.65,
                       color=colors[g], label=str(g))
    ax.set(xlabel="弹性模量 E (GPa)", ylabel="硬度 H (GPa)", title="全部压痕 E-H 分布")
    if order:
        ax.legend(frameon=False, fontsize=8)
    fig.suptitle(f"纳米压痕数据总览：{len(analysis['files'])} 个文件 / "
                 f"{len(indents)} 次压痕", fontsize=14)
    fig.tight_layout()
    return [_save(fig, out_dir, "overview_all.png")]


def plot_curves_overlay_numbered(analysis, cfg, out_dir):
    """所有压痕的 P-h 曲线叠放在同一张图，逐条编号。

    - 编号 = 配置表「图例名称」对应的序号；只标一次，放在曲线卸载末端的中部高度，
      用「序号带」统一排在绘图区上沿，细引线连回曲线，避免挤成黑块。
    - 图例分多列排在图下方，画布高度按条目数自适应，不再被单列 96 行撑成长条。
    - 所有曲线共享同一坐标系，不上下分层。
    """
    indents = [it for it in analysis["indents"] if it.get("curve_raw")]
    if not indents:
        return []
    file_idx = {f["source"]: n for n, f in enumerate(
        sorted(analysis["files"], key=lambda x: x["source"]))}
    indents = sorted(indents, key=lambda it: (file_idx.get(it["source"], 0),
                                              it.get("test_no") or 0))
    n = len(indents)
    cmap = plt.get_cmap("tab20")

    # ---- 画布自适应：图例按 4 列排布，高度由实际行数决定 ----
    ncols = 4
    per_col = int(math.ceil(n / ncols))
    legend_h = per_col * 0.15 + 0.35          # 8pt 字约 0.15 inch/行
    fig_w, fig_h = 13.0, max(5.6, 5.2 + legend_h)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    # 绘图区上方留标题，下方留多列图例
    fig.subplots_adjust(top=0.92, bottom=(legend_h + 0.72) / fig_h,
                        left=0.075, right=0.985)

    labels, dots, handles = [], [], []
    shown = set()
    for k, it in enumerate(indents, start=1):
        cur = it["curve_raw"]
        h, p = cur["h"], cur["P"]
        if h.size < 2:
            continue
        color = cmap((file_idx.get(it["source"], 0) % 20) / 20)
        ax.plot(h, p, color=color, lw=0.9, alpha=0.75, zorder=2)
        # 锚点：曲线 85% 载荷处（接近峰值平台），引线短、不横穿绘图区
        ip = int(np.argmin(np.abs(p - 0.85 * float(np.max(p)))))
        dots.append((float(h[ip]), float(p[ip]), k, color))
        label = file_label(it)
        if label not in shown:
            shown.add(label)
            handles.append(plt.Line2D([], [], color=color, lw=2))
            labels.append(label)

    pmax = max(float(np.max(it["curve_raw"]["P"])) for it in indents)
    ax.set_ylim(0, pmax * 1.18)              # 上方留白给序号带

    # ---- 序号带：编号按序号均匀排在顶部两行，引线连回各自曲线 ----
    # 不按峰值 x 排：曲线深度分布很不均匀（两端密集），按 x 排必然重叠。
    # 均匀排布保证任意条数都不堆叠，引线只在上沿短距离 connecting。
    n_per_row = int(math.ceil(n / 2))
    y_rows = [0.965, 0.925]
    for idx, (xh, yp, num, color) in enumerate(dots):
        row = 0 if idx < n_per_row else 1
        col = idx if row == 0 else idx - n_per_row
        count = n_per_row if row == 0 else n - n_per_row
        tx = 0.025 + (count and col + 0.5) / count * 0.95
        ax.annotate(str(num), xy=(xh, yp), xycoords="data",
                    xytext=(tx, y_rows[row]), textcoords="axes fraction",
                    ha="center", va="center", fontsize=7, color=color,
                    zorder=5, annotation_clip=False,
                    arrowprops={"arrowstyle": "-", "color": color, "lw": 0.35,
                                "alpha": 0.35, "shrinkA": 0, "shrinkB": 1})
    ax.set(xlabel="压入深度 h (nm)", ylabel="载荷 P (mN)",
           title=f"全部压痕载荷-位移曲线（{n} 条叠放；序号带见图上方、对照见图下方）")
    ax.grid(alpha=0.22)

    # ---- 图例多列排在绘图区下方：只显示 config 图例名称，每个名称一次 ----
    ax.legend(handles, labels, fontsize=8, frameon=False, loc="upper center",
              bbox_to_anchor=(0.5, -0.09), ncol=ncols,
              labelspacing=0.45, handlelength=1.8, columnspacing=1.4)
    return [_save(fig, out_dir, "curves_overlay_numbered.png")]


def plot_test_deviation(analysis, cfg, out_dir):
    """文件内重复压痕的偏差分析：每个文件 E_COV / H_COV 与合并阈值对照。"""
    mg = analysis.get("merge")
    if not mg or not mg["files"]:
        return []
    recs = mg["files"]
    fig, axes = plt.subplots(2, 1, figsize=(max(11, min(18, len(recs) * 0.75)), 7.2),
                             sharex=True)
    x = np.arange(len(recs))
    for ax, metric, lim in ((axes[0], "E_cov", mg["E上限"]),
                            (axes[1], "H_cov", mg["H上限"])):
        vals = [float(r[metric]) if math.isfinite(r[metric]) else 0.0 for r in recs]
        ok = [r["decision"] == "合并" for r in recs]
        colors = ["#2f8f4e" if o else "#c0504d" for o in ok]
        ax.bar(x, vals, color=colors, alpha=0.85, zorder=2)
        ax.axhline(lim, color="#333333", ls="--", lw=1.2, zorder=3,
                   label=f"合并上限 {lim:g}%")
        ax.set_ylabel(f"文件内 {metric[:1]} 变异系数 COV (%)")
        ax.legend(frameon=False, fontsize=8, loc="upper right")
        for xi, v in zip(x, vals):
            ax.text(xi, v, f"{v:.1f}", ha="center", va="bottom", fontsize=7)
    labels = [file_label(r) for r in recs]
    axes[1].set_xticks(x, labels, rotation=60, ha="right", fontsize=7)
    fig.suptitle("文件内重复压痕偏差分析（绿=合并为一条代表曲线，红=保留各次压痕）")
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    return [_save(fig, out_dir, "test_deviation.png")]


def plot_test_merge_overview(analysis, cfg, out_dir):
    """合并后的 E/H 对比：每文件一个点，合并的用圆点、未合并的用方块。"""
    mg = analysis.get("merge")
    if not mg or not mg["units"]:
        return []
    units = mg["units"]
    paths = []
    for metric, title in (("E", "弹性模量 E"), ("H", "硬度 H")):
        fig, ax = plt.subplots(figsize=(max(10, len(units) * 0.78), 4.8))
        ok_x, ok_v = [], []
        no_x, no_v = [], []
        for k, u in enumerate(units):
            v = u.get(metric + "_mean")
            if v is None or not math.isfinite(v):
                continue
            (ok_x if u["merged"] else no_x).append(k)
            (ok_v if u["merged"] else no_v).append(v)
        if ok_x:
            ax.scatter(ok_x, ok_v, s=68, color="#2f6fb5", zorder=4,
                       label=f"已合并为代表曲线 n={len(ok_x)}")
        if no_x:
            ax.scatter(no_x, no_v, s=62, color="#d1495b", marker="s", zorder=4,
                       label=f"保留各次压痕 n={len(no_x)}")
        ax.set_xticks(range(len(units)),
                      [file_label(u) for u in units],
                      rotation=60, ha="right", fontsize=7)
        ax.set_ylabel(f"{title} (GPa)")
        ax.set_title(f"文件内压痕合并后的{title}（E/H 数值未改变，只影响曲线聚合）")
        ax.legend(frameon=False, fontsize=8)
        fig.tight_layout()
        paths.append(_save(fig, out_dir, f"test_merge_{metric}.png"))
    return paths


def plot_merged_curves(analysis, cfg, out_dir):
    """按文件重画 P-h 曲线：已合并的文件只画 medoid 那一条，未合并的画全部。"""
    mg = analysis.get("merge")
    if not mg or not mg["units"]:
        return []
    units = [u for u in mg["units"] if u["curves"]]
    if not units:
        return []
    n = len(units)
    per_col = int(math.ceil(n / 4))
    legend_h = per_col * 0.15 + 0.35
    fig_h = max(5.6, 5.2 + legend_h)
    fig, ax = plt.subplots(figsize=(13.0, fig_h))
    fig.subplots_adjust(top=0.92, bottom=(legend_h + 0.72) / fig_h,
                        left=0.075, right=0.985)
    cm = plt.get_cmap("tab20")
    handles, labels = [], []
    for k, u in enumerate(units, start=1):
        color = cm((k - 1) % 20 / 20)
        curves = u["curves"]
        if u["merged"] and u["rep_test"] is not None:
            chosen = [c for c in curves if c["test_no"] == u["rep_test"]] or curves[:1]
        else:
            chosen = curves
        for c in chosen:
            cur = c["curve"]
            ax.plot(cur["h"], cur["P"], color=color, lw=0.95, alpha=0.75, zorder=2)
        handles.append(plt.Line2D([], [], color=color, lw=2))
        labels.append(file_label(u))
    pmax = max(float(np.max(c["curve"]["P"])) for u in units for c in u["curves"])
    ax.set_ylim(0, pmax * 1.06)
    ax.set(xlabel="压入深度 h (nm)", ylabel="载荷 P (mN)",
           title="载荷-位移曲线")
    ax.grid(alpha=0.22)
    ax.legend(handles, labels, fontsize=8, frameon=False, loc="upper center",
              bbox_to_anchor=(0.5, -0.09), ncol=4,
              labelspacing=0.45, handlelength=1.8, columnspacing=1.4)
    return [_save(fig, out_dir, "merged_curves.png")]


# ----------------------------------------------------------- 分组/配对

def plot_group_compare(analysis, cfg, out_dir):
    """按样品编号分组的 E / H 均值±标准差柱状图，叠加每个压痕散点。"""
    indents = analysis["indents"]
    order = sample_order(analysis, cfg)
    paths = []
    for metric, unit, title in (("E", "GPa", "弹性模量 E"), ("H", "GPa", "硬度 H")):
        fig, ax = plt.subplots(figsize=(7.2, 4.6))
        means, sds, labels = [], [], []
        for gi, g in enumerate(order):
            vals = [i[metric] for i in indents
                    if i["sample"] == g and i[metric] is not None]
            m, s, n = mean_sd(vals)
            means.append(m)
            sds.append(s)
            labels.append(f"{g}\n(n={n})")
            x = np.full(n, gi) + np.random.default_rng(gi + 7).uniform(-0.16, 0.16, n)
            ax.scatter(x, vals, color=sample_color(cfg, g), alpha=0.55, s=24, zorder=3)
        xs = np.arange(len(order))
        cols = [sample_color(cfg, g) for g in order]
        ax.bar(xs, means, width=0.52, color=cols, alpha=0.28,
               edgecolor=cols, linewidth=1.4, zorder=2)
        ax.errorbar(xs, means, yerr=sds, fmt="none", ecolor="#333333",
                    capsize=5, elinewidth=1.3, zorder=4)
        for x, m in zip(xs, means):
            ax.text(x, m, f"{m:.1f}", ha="center", va="bottom", fontsize=9)
        ax.set_xticks(xs, labels)
        ax.set_ylabel(f"{title} ({unit})")
        ax.set_title(f"不同样品编号的{title}对比（点=单次压痕，柱=均值±SD）")
        if means and all(m == m for m in means):
            ax.set_ylim(0, max(m + s for m, s in zip(means, sds)) * 1.22)
        fig.tight_layout()
        paths.append(_save(fig, out_dir, f"group_{metric}.png"))
    return paths


def _group_sort_key(name):
    """分组名带数字时按数值排序（0.39J/cm² 排在 3.54J/cm² 前）。"""
    s = str(name)
    m = re.search(r"\d+(?:\.\d+)?", s)
    return (0, float(m.group()), s) if m else (1, 0.0, s)


def plot_grouped_bar(analysis, cfg, out_dir):
    """按 config「分组」出柱状图：同组样品紧邻、组间留空、同一样品颜色一致。"""
    files = analysis["files"]
    if not files:
        return []
    groups, samples = [], []
    for f in files:
        g = str(f.get("group") or "未分组").strip()
        if g not in groups:
            groups.append(g)
        s = f["sample"]
        if s not in samples:
            samples.append(s)
    groups.sort(key=_group_sort_key)
    order = [s for s in sample_order(analysis, cfg) if s in samples]
    order += [s for s in samples if s not in order]
    if not groups or not order:
        return []

    slot, bar_w = 0.8, 0.68
    step = len(order) * slot + 0.9
    centers = np.arange(len(groups)) * step
    pos = {g: {s: centers[k] + (i - (len(order) - 1) / 2) * slot
               for i, s in enumerate(order)} for k, g in enumerate(groups)}

    fig_w = max(10.5, len(groups) * 1.45)
    fig, axes = plt.subplots(2, 1, figsize=(fig_w, 8.4), sharex=True)
    for ax, metric, title in ((axes[0], "E", "弹性模量 E"),
                              (axes[1], "H", "硬度 H")):
        for s in order:
            xs, means = [], []
            for g in groups:
                vals = [f[metric + "_mean"] for f in files
                        if str(f.get("group") or "未分组").strip() == g
                        and f["sample"] == s
                        and f.get(metric + "_mean") is not None
                        and math.isfinite(f[metric + "_mean"])]
                if not vals:
                    continue
                m, _, _ = mean_sd(vals)
                xs.append(pos[g][s])
                means.append(m)
            if not xs:
                continue
            ax.bar(xs, means, width=bar_w, color=sample_color(cfg, s),
                   label=str(s), alpha=0.9, edgecolor="white", zorder=3)
            for x, m in zip(xs, means):
                ax.text(x, m, f"{m:.1f}", ha="center", va="bottom",
                        fontsize=8, color="#333333")
        ax.set_ylabel(f"{title} (GPa)")
        ax.set_title("弹性模量" if metric == "E" else "硬度")
        ax.grid(axis="y", alpha=0.25)
        if means:
            ax.set_ylim(0, max(means) * 1.16)
    axes[0].legend(title="样品编号", frameon=False, fontsize=9, ncol=min(4, len(order)),
                   loc="upper right")
    axes[1].set_xticks(centers, groups, fontsize=9)
    # 只有标签宽度超过分组间距时才倾斜，避免分组少时无谓地斜排
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    tick_labels = axes[1].get_xticklabels()
    label_w = max((lab.get_window_extent(renderer).width for lab in tick_labels),
                  default=0.0)
    axis_w = axes[1].get_window_extent(renderer).width
    span = centers[-1] - centers[0] if len(centers) > 1 else step
    spacing_px = axis_w * step / (span + step) if span else axis_w
    if label_w > spacing_px * 0.85:
        for lab in tick_labels:
            lab.set_rotation(30)
            lab.set_horizontalalignment("right")
    else:
        for lab in tick_labels:
            lab.set_rotation(0)
            lab.set_horizontalalignment("center")
    fig.tight_layout()
    return [_save(fig, out_dir, "grouped_bar.png")]


def plot_paired(analysis, cfg, out_dir):
    """同测点下两种样品编号的配对差值图。"""
    pairs = analysis["paired"]
    if not pairs:
        return []
    sa, sb = str(pairs[0]["sample_a"]), str(pairs[0]["sample_b"])
    ca = sample_color(cfg, pairs[0]["sample_a"])
    cb = sample_color(cfg, pairs[0]["sample_b"])
    points = [p["point"] for p in pairs]
    paths = []
    for metric, unit, title in (("E", "GPa", "弹性模量 E"), ("H", "GPa", "硬度 H")):
        a = [p[f"{metric}_a"] for p in pairs]
        b = [p[f"{metric}_b"] for p in pairs]
        fig, ax = plt.subplots(figsize=(7.2, 4.6))
        x = np.arange(len(points))
        ax.plot(x, a, "o-", color=ca, label=f"样品 {sa}", lw=1.6)
        ax.plot(x, b, "s--", color=cb, label=f"样品 {sb}", lw=1.6)
        for xi, av, bv in zip(x, a, b):
            ax.annotate(f"{bv-av:+.1f}", (xi, max(av, bv)),
                        textcoords="offset points", xytext=(0, 7),
                        ha="center", fontsize=8, color="#555555")
        ax.set_xticks(x, [str(p) for p in points])
        ax.set_xlabel("测点编号（同一位置对比两种样品编号）")
        ax.set_ylabel(f"{title} ({unit})")
        ax.set_title(f"配对比较：同测点样品 {sa} 与 {sb} 的{title}（Δ={sb}−{sa}）")
        ax.legend(frameon=False)
        fig.tight_layout()
        paths.append(_save(fig, out_dir, f"paired_{metric}.png"))
    return paths


def plot_population_split(analysis, cfg, out_dir):
    """双峰分组散点：E-H 平面按高低两类着色。"""
    split = analysis.get("split")
    if not split:
        return []
    files = analysis["files"]
    fig, ax = plt.subplots(figsize=(6.8, 5.2))
    low_ids, high_ids = set(split["low_ids"]), set(split["high_ids"])
    for f in files:
        i = f["file_index"]
        color = ("#d1495b" if i in low_ids else "#2f6fb5") if f["H_mean"] else "#999999"
        ax.scatter(f["E_mean"], f["H_mean"], s=75, color=color, alpha=0.8,
                   edgecolor="white", linewidth=0.8, zorder=3)
        ax.annotate(f"P{f['point']}", (f["E_mean"], f["H_mean"]),
                    textcoords="offset points", xytext=(5, 4), fontsize=8)
    ax.set_xlabel("弹性模量 E (GPa)")
    ax.set_ylabel("硬度 H (GPa)")
    ax.set_title(f"E-H 分布：自动识别两类力学响应（间隙 {split['gap']:.1f} GPa，H）")
    ax.legend(handles=[
        plt.Line2D([], [], linestyle="none", marker="o", color="#d1495b",
                   label=f"低硬度组 n={len(low_ids)}"),
        plt.Line2D([], [], linestyle="none", marker="o", color="#2f6fb5",
                   label=f"高硬度组 n={len(high_ids)}"),
    ], frameon=False, loc="upper left")
    fig.tight_layout()
    return [_save(fig, out_dir, "population_split.png")]


def plot_qc(files, cfg, out_dir):
    """质控概览：载荷偏差、漂移、单次压痕 E/H 的 COV。"""
    fig, axes = plt.subplots(1, 3, figsize=(12, 4.2))
    labels = [file_label(f) for f in files]
    x = np.arange(len(files))
    axes[0].bar(x, [f["Pmax_dev_pct"] for f in files], color="#5b8c5a", alpha=0.8)
    axes[0].axhline(cfg["载荷容差_pct"], color="#c0504d", ls="--", lw=1)
    axes[0].set_title("最大载荷相对标称偏差 (%)")
    axes[1].bar(x, [f["drift_mean"] for f in files], color="#3f7c8a", alpha=0.8)
    axes[1].axhline(cfg["漂移上限_nm_s"], color="#c0504d", ls="--", lw=1)
    axes[1].set_title("仪器漂移修正均值 (nm/s)")
    axes[2].bar(x, [f["H_cov"] for f in files], color="#b9863b", alpha=0.8)
    axes[2].set_title("硬度批内变异系数 COV (%)")
    for ax in axes:
        ax.set_xticks(x, labels, rotation=45, ha="right", fontsize=8)
    fig.suptitle("数据质控概览（虚线为配置阈值）")
    fig.tight_layout()
    return [_save(fig, out_dir, "qc_overview.png")]


# --------------------------------------------------- 每个样品编号 / 每个文件

def plot_per_sample(analysis, cfg, out_dir):
    """每个样品编号一张图：该编号下全部 P-h 曲线与 E/H 分布。"""
    order = sample_order(analysis, cfg)
    paths = []
    for g in order:
        sub = [f for f in analysis["files"] if f["sample"] == g]
        its = [i for i in analysis["indents"] if i["sample"] == g]
        if not sub:
            continue
        fig, axes = plt.subplots(1, 3, figsize=(14.5, 4.6))
        cm = plt.get_cmap("tab20")
        ax = axes[0]
        curves = [it for it in its if it.get("curve_raw")]
        shown = set()
        for k, it in enumerate(curves):
            cur = it["curve_raw"]
            label = file_label(it)
            ax.plot(cur["h"], cur["P"], lw=0.9, alpha=0.8,
                    color=cm(k % 20), label=label if label not in shown else None)
            shown.add(label)
        if not curves:
            ax.text(0.5, 0.5, "无曲线数据", ha="center", va="center", transform=ax.transAxes)
        ax.set(xlabel="h (nm)", ylabel="P (mN)",
               title=f"样品 {g}：P-h 曲线（{len(curves)} 次）")
        if 1 < len(curves) <= 12:
            ax.legend(fontsize=6, frameon=False, loc="lower right")
        for j, (metric, title) in enumerate((("E", "弹性模量 E"), ("H", "硬度 H")), start=1):
            ax = axes[j]
            vals = [it[metric] for it in its if it.get(metric) is not None and it[metric] == it[metric]]
            if vals:
                ax.bar(range(len(vals)), vals, color=sample_color(cfg, g), alpha=0.55)
                ax.axhline(float(np.mean(vals)), color="#222222", ls="--", lw=1.4)
                ax.set_title(f"{title} 均值 {np.mean(vals):.1f} GPa")
            else:
                ax.set_title(title)
            ax.set_xticks(range(len(vals)), [f"#{k+1}" for k in range(len(vals))], fontsize=7)
            ax.set_ylabel(f"{title} (GPa)")
        fig.suptitle(f"样品编号 {g}：{len(sub)} 个文件 / {len(its)} 次压痕")
        fig.tight_layout()
        paths.append(_save(fig, out_dir, f"per_sample_{_safe(g)}.png"))
    return paths


def plot_per_file(analysis, cfg, out_dir):
    """每个文件一张图：该批 P-h 曲线 + 逐次 E/H，标题带用户设置的图例名称。"""
    folder = Path(out_dir) / "per_file"
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    sources = []
    for f in analysis["files"]:
        if f["source"] not in sources:
            sources.append(f["source"])
    for src in sources:
        meta = next(f for f in analysis["files"] if f["source"] == src)
        its = [i for i in analysis["indents"] if i["source"] == src]
        fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
        cm = plt.get_cmap("viridis")
        ax = axes[0]
        curves = [it for it in its if it.get("curve_raw")]
        for k, it in enumerate(curves):
            cur = it["curve_raw"]
            ax.plot(cur["h"], cur["P"], lw=1.0, alpha=0.85,
                    color=cm(k / max(1, len(curves) - 1)), label=f"Test {it['test_no']:03d}")
        if not curves:
            ax.text(0.5, 0.5, "无曲线数据", ha="center", va="center", transform=ax.transAxes)
        ax.set(xlabel="压入深度 h (nm)", ylabel="载荷 P (mN)",
               title=f"P-h 曲线（{len(curves)} 次压痕）")
        if 1 < len(curves) <= 10:
            ax.legend(fontsize=7, frameon=False, loc="lower right")

        ax = axes[1]
        xs = np.arange(len(its))
        e_vals = [it.get("E") or 0 for it in its]
        h_vals = [it.get("H") or 0 for it in its]
        ax.bar(xs - 0.18, e_vals, width=0.36, color="#2f6fb5", alpha=0.75)
        ax.set_ylabel("E (GPa)", color="#2f6fb5")
        ax2 = ax.twinx()
        ax2.bar(xs + 0.18, h_vals, width=0.36, color="#d1495b", alpha=0.75)
        ax2.set_ylabel("H (GPa)", color="#d1495b")
        ax.set_xticks(xs, [f"T{it['test_no']:03d}" for it in its], fontsize=8)
        ax.set_title("逐次压痕 E / H")
        ax.grid(axis="x", alpha=0.15)
        ax2.grid(False)
        flags = "; ".join(meta.get("flags") or [])
        fig.suptitle(f"{file_label(meta)}  |  {src}\n"
                     f"样品 {meta['sample']} · 测点 {meta['point']} · "
                     f"E={meta['E_mean']:.1f}±{meta['E_sd']:.1f} GPa · "
                     f"H={meta['H_mean']:.2f}±{meta['H_sd']:.2f} GPa"
                     + (f" · 质控：{flags}" if flags else ""), fontsize=10)
        fig.tight_layout()
        paths.append(_save(fig, folder, f"{_safe(src)}.png"))
    return paths


def write_figure_index(paths, out_dir):
    """写图片索引，方便从报告跳到总图和单图。"""
    lines = ["# 图片索引", "",
             "## 压痕合并（新增）", "",
             "- [文件内压痕偏差与合并判定](test_deviation.png)",
             "- [合并后 E 对比](test_merge_E.png)",
             "- [合并后 H 对比](test_merge_H.png)",
             "- [合并后压痕曲线](merged_curves.png)", "",
             "## 常规总图", "",
             "- [全部数据总图](overview_all.png)",
             "- [全部压痕曲线叠放（带编号）](curves_overlay_numbered.png)", "",
             "## 分组柱状图", "",
             "- [按分组+样品编号的 E/H 柱状图](grouped_bar.png)", "",
             "## 每个样品编号", ""]
    for p in paths:
        if p.name.startswith("per_sample_"):
            lines.append(f"- [{p.stem[len('per_sample_'):]}]({p.name})")
    lines += ["", "## 每个文件（含 P-h 曲线与逐次 E/H）", ""]
    for p in paths:
        if p.parent.name == "per_file":
            lines.append(f"- [{p.stem}](per_file/{p.name})")
    lines += ["", "## 对比与质控", ""]
    for p in paths:
        if p.name.startswith(("group_", "paired_", "population_", "qc_", "merged_")):
            lines.append(f"- [{p.stem}]({p.name})")
    lines.append("")
    (Path(out_dir) / "index.md").write_text("\n".join(lines), encoding="utf-8")

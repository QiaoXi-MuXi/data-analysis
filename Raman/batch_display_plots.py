# -*- coding: utf-8 -*-
"""Raman 分层批次展示：总览、点位、重复性与跨组配对图。

全部图沿用单谱/堆叠的 house style；总览不标峰、共享横轴，单点图可复用
montage 的实物图拼图。归一化只用于显示，不改变任何定量口径。
"""
from __future__ import annotations

import math
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from plot_raman import (AX_L, AX_R, BLACK, CJK_FONT, DPI, FONT, FS,
                        PX_W, save_figure, style_axes, find_peaks,
                        peak_center, smooth, auto_threshold, ZERO_GUARD)
from raman_montage import MONTAGE_COLORS, render_montage
from raman_compare import detect_spectrum, window_data, SpectrumError


def _norm(y):
    arr = np.asarray(y, dtype=float)
    span = float(arr.max() - arr.min())
    return (arr - arr.min()) / (span if span else 1.0)


def _title_font():
    return CJK_FONT or FONT


def _safe_title(text):
    return str(text).replace("|", " / ")


def _curve_color(index):
    return MONTAGE_COLORS[index % len(MONTAGE_COLORS)]


def _detect_peaks(x, y, config, threshold=None, max_labels=None):
    """在显示窗口内检测峰，供图面标签使用；完整分析峰仍走 compare 核心。"""
    limit = config.point_peaks if max_labels is None else max_labels
    if limit <= 0:
        return [], []
    ys = smooth(y)
    if threshold is None:
        threshold, _, _ = auto_threshold(x, y, zero_at=config.shift,
                                         zero_guard=config.zero_guard)
    found = find_peaks(x, y, ys, threshold, max_labels=limit,
                       zero_at=config.shift, zero_guard=config.zero_guard)
    if not found:
        return [], []
    centers = [peak_center(x, y, ys, index) for _, index, _ in found]
    return found, centers


def render_reference_overview(references, lo, hi, config, out_path):
    """5 条基准叠加；最多 5 色，默认不标峰。"""
    out_path = str(out_path)
    fig_w = PX_W / float(DPI)
    fig = plt.figure(figsize=(fig_w, fig_w / 2.0), dpi=DPI, facecolor="white")
    ax = fig.add_axes([AX_L, 0.15, AX_R - AX_L, 0.78])
    handles = []
    for index, (ref, spectrum) in enumerate(references):
        x, y = window_data(spectrum, lo, hi, config.shift)
        plot_y = _norm(y) if config.normalize else y
        line, = ax.plot(x, plot_y, color=_curve_color(index), lw=1.15,
                        antialiased=True, label=_safe_title(ref.display_label))
        handles.append(line)
        if config.overview_peaks > 0:
            found, centers = _detect_peaks(x, y, config, max_labels=config.overview_peaks)
            for center in centers[:config.overview_peaks]:
                ax.text(center, float(np.max(plot_y)), "%.0f" % center,
                        color=_curve_color(index), fontsize=max(6, FS * 0.7),
                        rotation=90, va="top", ha="center")
    ax.set_ylim(float(np.min([_norm(s.y).min() if config.normalize else s.y.min()
                              for _, s in references])),
                float(np.max([_norm(s.y).max() if config.normalize else s.y.max()
                              for _, s in references])) * 1.08)
    style_axes(ax, fig, lo, hi, y_numbers=not config.normalize,
               y_ticks=not config.normalize)
    ax.set_ylabel("norm." if config.normalize else "CCD cts",
                  fontproperties=_title_font(), fontsize=FS)
    ax.set_title("基准总览（%d 条，%s）" % (
        len(references),
        "normalized for display" if config.normalize else "raw intensity"),
        fontproperties=_title_font(), fontsize=FS + 1)
    ax.legend(handles, [h.get_label() for h in handles], loc="upper right",
              frameon=False, fontsize=FS, prop={"family": _title_font()})
    save_figure(fig, out_path, compress_level=6)
    plt.close(fig)


def render_contact(group, points, lo, hi, config, out_path):
    """一个组 20 点的 4x5 小多图；每格 1 点，不标峰，标题零填充点号。"""
    out_path = str(out_path)
    cols = max(1, config.overview_cols)
    n = len(points)
    rows = int(math.ceil(n / float(cols)))
    fig_w = PX_W / float(DPI)
    fig_h = fig_w * 0.62
    fig, axes = plt.subplots(rows, cols, figsize=(fig_w, fig_h), dpi=DPI,
                             facecolor="white", squeeze=False)
    flat = axes.ravel()
    for index, axis in enumerate(flat):
        axis.set_visible(False)
    for index, (point, items, rep) in enumerate(points):
        axis = flat[index]
        axis.set_visible(True)
        axis.set_title("%s-P%02d" % (group, point), fontsize=FS,
                       fontproperties=_title_font())
        for scan_index, item in enumerate(items):
            if item.status != "ok" or item.spectrum is None:
                continue
            x, y = window_data(item.spectrum, lo, hi, config.shift)
            axis.plot(x, _norm(y) if config.normalize else y,
                      color=_curve_color(scan_index), lw=0.85, antialiased=True)
        if rep is not None and rep["y"] is not None:
            axis.plot(rep["x"], _norm(rep["y"]) if config.normalize else rep["y"],
                      color="#222222", lw=1.35, antialiased=True)
        if not any(item.status == "ok" for item in items):
            axis.text(0.5, 0.5, "No valid spectrum", transform=axis.transAxes,
                      ha="center", va="center", fontsize=FS, color="#a00",
                      fontproperties=_title_font())
        axis.set_xlim(lo, hi)
        axis.set_yticks([])
        axis.tick_params(axis="x", labelsize=max(6, FS * 0.62))
        if index % cols:
            axis.set_yticklabels([])
        for spine in axis.spines.values():
            spine.set_linewidth(0.7)
            spine.set_color(BLACK)
    fig.suptitle("%s 组总览（%d 点；%s）" % (
        group, n, "normalized for display" if config.normalize else "raw intensity"),
        fontproperties=_title_font(), fontsize=FS + 2)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    save_figure(fig, out_path, compress_level=6)
    plt.close(fig)


def render_point(items, rep, photo_path, lo, hi, config, out_path):
    """点位详情：有实物图时复用 montage；否则画纯谱图。"""
    out_path = str(out_path)
    if photo_path and os.path.isfile(photo_path):
        curves = []
        for scan_index, item in enumerate(items):
            if item.status != "ok" or item.spectrum is None:
                continue
            x, y = window_data(item.spectrum, lo, hi, config.shift)
            found, centers = _detect_peaks(x, y, config)
            curves.append({"x": x, "y": y, "label": item.label,
                           "color": _curve_color(scan_index),
                           "normalize": config.normalize, "offset": None,
                           "peaks": found, "centers": centers})
        if curves:
            render_montage(photo_path, curves, out_path, xlim=(lo, hi))
            return
    fig_w = PX_W / float(DPI)
    fig = plt.figure(figsize=(fig_w, fig_w / 2.0), dpi=DPI, facecolor="white")
    ax = fig.add_axes([AX_L, 0.16, AX_R - AX_L, 0.76])
    values = []
    for scan_index, item in enumerate(items):
        if item.status != "ok" or item.spectrum is None:
            continue
        x, y = window_data(item.spectrum, lo, hi, config.shift)
        plot_y = _norm(y) if config.normalize else y
        values.append(plot_y)
        ax.plot(x, plot_y, color=_curve_color(scan_index), lw=1.1,
                label=item.label, antialiased=True)
    if rep is not None and rep["y"] is not None:
        ax.plot(rep["x"], _norm(rep["y"]) if config.normalize else rep["y"],
                color="#222222", lw=1.6, label=rep["label"], antialiased=True)
    if not values:
        ax.text(0.5, 0.5, "No valid spectrum", transform=ax.transAxes,
                ha="center", va="center", fontsize=FS + 2, color="#a00",
                fontproperties=_title_font())
    else:
        ax.set_ylim(min(float(v.min()) for v in values),
                    max(float(v.max()) for v in values) * 1.08)
    style_axes(ax, fig, lo, hi, y_numbers=not config.normalize,
               y_ticks=not config.normalize)
    ax.set_ylabel("norm." if config.normalize else "CCD cts",
                  fontproperties=_title_font(), fontsize=FS)
    status = rep["label"] if rep else ""
    ax.set_title(_safe_title(status), fontproperties=_title_font(), fontsize=FS + 1)
    if values:
        ax.legend(loc="upper right", frameon=False, fontsize=max(6, FS * 0.8),
                  prop={"family": _title_font()})
    save_figure(fig, out_path, compress_level=6)
    plt.close(fig)


def render_repeat(items, rep, lo, hi, config, out_path):
    """同点重复性：上叠加、下差值（display residual，不宣称扣仪器基线）。"""
    out_path = str(out_path)
    fig_w = PX_W / float(DPI)
    fig, (ax_top, ax_bottom) = plt.subplots(
        2, 1, figsize=(fig_w, fig_w * 0.62), dpi=DPI, facecolor="white",
        sharex=True, gridspec_kw={"height_ratios": [2, 1]})
    fig.subplots_adjust(left=AX_L, right=AX_R, top=0.94, bottom=0.12, hspace=0.10)
    plotted = []
    for scan_index, item in enumerate(items):
        if item.status != "ok" or item.spectrum is None:
            continue
        x, y = window_data(item.spectrum, lo, hi, config.shift)
        norm = _norm(y)
        plotted.append((x, norm, scan_index))
        ax_top.plot(x, norm, color=_curve_color(scan_index), lw=1.0,
                    label=item.label, antialiased=True)
    if rep is not None and rep["y"] is not None and plotted:
        grid = rep["x"]
        mean_curve = _norm(rep["y"])
        ax_top.plot(grid, mean_curve, color="#222222", lw=1.6,
                    label=rep["label"], antialiased=True)
        for x, norm, scan_index in plotted:
            residual = norm - np.interp(x, grid, mean_curve)
            ax_bottom.plot(x, residual, color=_curve_color(scan_index),
                           lw=0.9, antialiased=True)
        ax_bottom.axhline(0.0, color=BLACK, lw=0.8)
    if not plotted:
        for axis in (ax_top, ax_bottom):
            axis.text(0.5, 0.5, "No valid spectrum", transform=axis.transAxes,
                      ha="center", va="center", fontsize=FS, color="#a00",
                      fontproperties=_title_font())
    ax_top.set_ylabel("norm.", fontproperties=_title_font(), fontsize=FS)
    ax_bottom.set_ylabel("norm. diff.", fontproperties=_title_font(), fontsize=FS)
    ax_bottom.set_xlabel("rel. 1/cm", fontproperties=_title_font(), fontsize=FS)
    ax_top.set_title("同点重复性（display residual，非仪器基线扣除）",
                     fontproperties=_title_font(), fontsize=FS)
    for axis in (ax_top, ax_bottom):
        for spine in axis.spines.values():
            spine.set_linewidth(1.0)
            spine.set_color(BLACK)
        axis.tick_params(labelsize=max(7, FS * 0.8))
    ax_bottom.set_xlim(lo, hi)
    if plotted:
        ax_top.legend(loc="upper right", frameon=False,
                      fontsize=max(6, FS * 0.75),
                      prop={"family": _title_font()})
    save_figure(fig, out_path, compress_level=6)
    plt.close(fig)


def _paired_curve(items, rep, lo, hi, config):
    if rep is None or rep["y"] is None:
        return None, None
    x, y = rep["x"], rep["y"]
    return x, _norm(y) if config.normalize else y


def render_paired(left_items, left_rep, right_items, right_rep,
                  lo, hi, config, out_path, left_label="1100", right_label="1210"):
    """同点 1100/1210 配对：上叠加、下差值；任一侧无有效谱则不画伪图。"""
    out_path = str(out_path)
    x1, y1 = _paired_curve(left_items, left_rep, lo, hi, config)
    x2, y2 = _paired_curve(right_items, right_rep, lo, hi, config)
    fig_w = PX_W / float(DPI)
    fig, (ax_top, ax_bottom) = plt.subplots(
        2, 1, figsize=(fig_w, fig_w * 0.62), dpi=DPI, facecolor="white",
        sharex=True, gridspec_kw={"height_ratios": [2, 1]})
    fig.subplots_adjust(left=AX_L, right=AX_R, top=0.94, bottom=0.12, hspace=0.10)
    if x1 is not None and x2 is not None:
        grid = np.union1d(x1, x2)
        a = np.interp(grid, x1, y1)
        b = np.interp(grid, x2, y2)
        ax_top.plot(grid, a, color=_curve_color(0), lw=1.2, label=left_label)
        ax_top.plot(grid, b, color=_curve_color(1), lw=1.2, label=right_label)
        ax_bottom.plot(grid, b - a, color="#222222", lw=1.0)
        ax_bottom.axhline(0.0, color=BLACK, lw=0.8)
        ax_top.legend(loc="upper right", frameon=False, fontsize=FS,
                      prop={"family": _title_font()})
    else:
        for axis in (ax_top, ax_bottom):
            axis.text(0.5, 0.5, "No valid spectrum on one or both sides",
                      transform=axis.transAxes, ha="center", va="center",
                      fontsize=FS, color="#a00", fontproperties=_title_font())
    ax_top.set_ylabel("norm." if config.normalize else "CCD cts",
                      fontproperties=_title_font(), fontsize=FS)
    ax_bottom.set_ylabel("norm. diff." if config.normalize else "diff.",
                         fontproperties=_title_font(), fontsize=FS)
    ax_bottom.set_xlabel("rel. 1/cm", fontproperties=_title_font(), fontsize=FS)
    ax_top.set_title("同点跨组对比（同口径代表谱）",
                     fontproperties=_title_font(), fontsize=FS)
    for axis in (ax_top, ax_bottom):
        for spine in axis.spines.values():
            spine.set_linewidth(1.0)
            spine.set_color(BLACK)
        axis.tick_params(labelsize=max(7, FS * 0.8))
    ax_bottom.set_xlim(lo, hi)
    save_figure(fig, out_path, compress_level=6)
    plt.close(fig)

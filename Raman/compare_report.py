# -*- coding: utf-8 -*-
"""Raman 基准对比模式的 CSV、图像和运行清单输出。"""
from __future__ import annotations

import csv
import json
import math
import os
from pathlib import Path
import tempfile

import numpy as np


REFERENCE_COLUMNS = [
    "reference_peak_id", "peak_cm-1", "H_counts", "I_max", "I_baseline",
    "threshold_counts",
]
PEAK_COLUMNS = [
    "file", "label", "reference_peak_id", "reference_peak_cm-1",
    "sample_peak_cm-1", "status", "delta_cm-1", "abs_delta_cm-1",
    "tolerance_cm-1", "reference_H_counts", "sample_H_counts", "H_ratio",
    "reference_H_norm", "sample_H_norm", "H_norm_ratio",
]
SUMMARY_COLUMNS = [
    "file", "label", "status", "reference_peaks", "matched_peaks",
    "missing_peaks", "new_peaks", "match_rate", "mean_delta_cm-1",
    "mae_delta_cm-1", "max_abs_delta_cm-1", "rmse_delta_cm-1", "pearson_r",
    "sample_threshold_counts", "points", "xmin_cm-1", "xmax_cm-1", "message",
]

COLOR_REFERENCE = "#213f5f"
COLOR_SAMPLE = "#c0392b"
COLOR_MISSING = "#7f8c8d"
COLOR_NEW = "#c47c10"
COLORS = ["#c0392b", "#2b5fa8", "#2e7d32", "#8e44ad",
          "#c47c10", "#12776a", "#7f8c8d", "#ad1457"]


def _value(value):
    if value is None:
        return ""
    if isinstance(value, (bool, np.bool_)):
        return "1" if bool(value) else "0"
    if isinstance(value, (float, np.floating)):
        number = float(value)
        if not math.isfinite(number):
            return ""
        if abs(number) < 5e-13:
            number = 0.0
        return f"{number:.10g}"
    return str(value)


def _write_csv(path, columns, rows):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix="." + path.stem + "-", suffix=".csv",
                                         dir=path.parent, delete=False, mode="w",
                                         encoding="utf-8-sig", newline="") as stream:
            temporary = Path(stream.name)
            writer = csv.writer(stream, lineterminator="\r\n")
            writer.writerow(columns)
            for row in rows:
                writer.writerow([_value(row.get(column)) for column in columns])
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_reference_peaks(path, reference):
    rows = [{
        "reference_peak_id": peak.id,
        "peak_cm-1": peak.center,
        "H_counts": peak.h,
        "I_max": peak.imax,
        "I_baseline": peak.baseline,
        "threshold_counts": reference.threshold,
    } for peak in reference.peaks]
    _write_csv(path, REFERENCE_COLUMNS, rows)


def _summary_row(result, reference_count):
    if result.status != "success":
        return {
            "file": str(result.sample.path), "label": result.sample.label,
            "status": "failed", "reference_peaks": None,
            "matched_peaks": None, "missing_peaks": None, "new_peaks": None,
            "match_rate": None, "mean_delta_cm-1": None,
            "mae_delta_cm-1": None, "max_abs_delta_cm-1": None,
            "rmse_delta_cm-1": None, "pearson_r": None,
            "sample_threshold_counts": None, "points": None,
            "xmin_cm-1": None, "xmax_cm-1": None,
            "message": result.message or result.image_error,
        }
    metric = result.metric
    return {
        "file": str(result.sample.path), "label": result.sample.label,
        "status": "success", "reference_peaks": reference_count,
        "matched_peaks": result.matched, "missing_peaks": result.missing,
        "new_peaks": result.new, "match_rate": result.match_rate,
        "mean_delta_cm-1": metric.get("mean_delta_cm-1"),
        "mae_delta_cm-1": metric.get("mae_delta_cm-1"),
        "max_abs_delta_cm-1": metric.get("max_abs_delta_cm-1"),
        "rmse_delta_cm-1": metric.get("rmse_delta_cm-1"),
        "pearson_r": result.pearson_r,
        "sample_threshold_counts": result.detection.threshold if result.detection else None,
        "points": result.points, "xmin_cm-1": result.xmin, "xmax_cm-1": result.xmax,
        "message": "；".join(value for value in (result.message, result.image_error) if value),
    }


def write_reports(out_dir, reference, results):
    from raman_compare import comparison_records

    out_dir = Path(out_dir)
    write_reference_peaks(out_dir / "compare_reference_peaks.csv", reference)
    peak_rows = []
    for result in results:
        if result.status == "success":
            peak_rows.extend(comparison_records(result))
    _write_csv(out_dir / "compare_peaks.csv", PEAK_COLUMNS, peak_rows)
    summaries = [_summary_row(result, len(reference.peaks)) for result in results]
    _write_csv(out_dir / "compare_summary.csv", SUMMARY_COLUMNS, summaries)
    return summaries


def _cjk_font():
    from plot_raman import CJK_FONT, FONT
    return CJK_FONT or FONT


def _normalise(values):
    low, high = float(np.min(values)), float(np.max(values))
    span = high - low
    return (values - low) / span if span > 0 else np.zeros_like(values)


def _x_step(span):
    return 100.0 if span <= 1200 else (200.0 if span <= 3000 else 300.0)


def _style_axis(ax, color_label=True):
    from matplotlib.ticker import MaxNLocator, MultipleLocator
    from plot_raman import BLACK, FONT, LW_FRAME, TICK_LEN, FS

    for spine in ax.spines.values():
        spine.set_linewidth(LW_FRAME)
        spine.set_color(BLACK)
    ax.tick_params(axis="x", direction="inout", length=TICK_LEN,
                   width=LW_FRAME, labelsize=FS, pad=2)
    ax.tick_params(axis="y", direction="inout", length=TICK_LEN,
                   width=LW_FRAME, labelsize=FS, pad=2)
    ax.yaxis.set_major_locator(MaxNLocator(nbins=5, steps=[1, 2, 2.5, 3, 5, 10]))
    if color_label:
        ax.set_xlabel("rel. 1/cm", fontsize=FS, fontfamily=FONT)


def render_sample_plot(result, reference, tolerance, normalize, path):
    """逐样品上图叠加、下图残差；标签只减密度，不改分析结果。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MultipleLocator
    from plot_raman import DPI, FONT, FS, save_figure

    grid = np.asarray(reference.x, dtype=float)
    sample_grid = np.interp(grid, result.detection.x, result.detection.y)
    if normalize:
        ref_plot = _normalise(reference.y)
        sample_plot = _normalise(sample_grid)
        residual = sample_plot - ref_plot
        y_title = "norm."
        residual_title = "norm. diff."
    else:
        ref_plot, sample_plot = reference.y, sample_grid
        residual = sample_plot - ref_plot
        y_title = "CCD cts"
        residual_title = "CCD cts diff."

    fig = plt.figure(figsize=(2048 / DPI, 1280 / DPI), dpi=DPI, facecolor="white")
    gs = fig.add_gridspec(2, 1, height_ratios=(2.35, 1.0), hspace=0.08,
                          left=0.085, right=0.972, bottom=0.115, top=0.92)
    ax = fig.add_subplot(gs[0])
    axr = fig.add_subplot(gs[1], sharex=ax)
    ax.plot(grid, ref_plot, color=COLOR_REFERENCE, lw=1.25,
            label="基准：" + reference.spectrum.label)
    ax.plot(grid, sample_plot, color=COLOR_SAMPLE, lw=1.0, alpha=0.96,
            label="样品：" + result.sample.label)
    yspan = max(float(np.max(ref_plot) - np.min(ref_plot)),
                float(np.max(sample_plot) - np.min(sample_plot)), 1e-9)
    low = min(float(np.min(ref_plot)), float(np.min(sample_plot)))
    high = max(float(np.max(ref_plot)), float(np.max(sample_plot)))
    ax.set_ylim(low - 0.03 * yspan, high + 0.24 * yspan)
    _style_axis(ax, color_label=False)
    ax.tick_params(axis="x", labelbottom=False)
    ax.set_ylabel(y_title, fontsize=FS, fontfamily=FONT)
    title_font = {"fontsize": 11, "fontfamily": _cjk_font()}
    ax.set_title(result.sample.label + "  /  " + reference.spectrum.label,
                 loc="left", pad=10, **title_font)
    legend = ax.legend(loc="upper right", frameon=False, fontsize=8,
                       handlelength=2.0, borderaxespad=.3)
    for text in legend.get_texts():
        text.set_fontfamily(_cjk_font())

    matched = [row for row in result.comparisons if row.status == "matched"]
    matched.sort(key=lambda row: -(row.sample.h if row.sample else 0))
    label_limit = 12 if result.sample.peaks else 0
    for number, row in enumerate(matched):
        if row.sample is None:
            continue
        x = row.sample.center
        y = float(np.interp(x, grid, sample_plot))
        ax.plot([x], [y], marker="o", ms=2.4, color=COLOR_SAMPLE, zorder=6, clip_on=True)
        if number < label_limit:
            ax.annotate(f"{row.reference_id} {row.delta:+.2f}",
                        xy=(x, y), xytext=(0, 7 + 7 * (number % 3)),
                        textcoords="offset points", ha="center", va="bottom",
                        fontsize=7.2, color=COLOR_SAMPLE,
                        fontfamily=FONT, clip_on=True)
    missing = [row for row in result.comparisons if row.status == "missing"]
    for number, row in enumerate(missing):
        if row.reference is None:
            continue
        ax.axvline(row.reference.center, color=COLOR_MISSING, lw=.7, ls=":", alpha=.8)
        if number < label_limit:
            ax.annotate(f"{row.reference_id} 缺", xy=(row.reference.center, 0.035),
                        xycoords=("data", "axes fraction"), xytext=(0, 2),
                        textcoords="offset points", ha="center", va="bottom",
                        fontsize=7.2, color=COLOR_MISSING,
                        fontfamily=_cjk_font(), clip_on=True)
    for number, row in enumerate([row for row in result.comparisons
                                  if row.status == "new"]):
        if row.sample is None:
            continue
        y = float(np.interp(row.sample.center, grid, sample_plot))
        ax.plot([row.sample.center], [y], marker="v", ms=4.5, color=COLOR_NEW,
                linestyle="none", zorder=7, clip_on=True)
        if number < label_limit:
            ax.annotate("新增", xy=(row.sample.center, y), xytext=(0, -9),
                        textcoords="offset points", ha="center", va="top",
                        fontsize=7.2, color=COLOR_NEW,
                        fontfamily=_cjk_font(), clip_on=True)

    axr.plot(grid, residual, color="#566573", lw=.8)
    axr.axhline(0, color="black", lw=.6)
    axr.fill_between(grid, residual, 0, color="#dfe6e9", alpha=.7)
    rmax = max(float(np.max(np.abs(residual))), 1e-9)
    axr.set_ylim(-rmax * 1.12, rmax * 1.12)
    axr.set_xlim(float(grid[0]), float(grid[-1]))
    axr.xaxis.set_major_locator(MultipleLocator(_x_step(float(grid[-1] - grid[0]))))
    _style_axis(axr, color_label=False)
    axr.set_ylabel(residual_title, fontsize=FS, fontfamily=FONT)
    axr.set_xlabel("rel. 1/cm", fontsize=FS, fontfamily=FONT)
    for label in ax.get_xticklabels():
        label.set_visible(False)
    fig.text(0.985, 0.045, f"tolerance ±{tolerance:g} cm-1",
             ha="right", va="bottom", fontsize=7.5, color="#7f8c8d",
             fontfamily=FONT)
    save_figure(fig, str(path))
    plt.close(fig)
    return path


def render_shift_plot(results, tolerance, path):
    """多样品峰位偏差图；仅绘制已匹配峰，缺失峰不以 0 代替。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MultipleLocator
    from plot_raman import DPI, FONT, FS, save_figure

    series = []
    for result in results:
        if result.status != "success":
            continue
        points = [(row.reference_center, row.delta) for row in result.comparisons
                  if row.status == "matched" and row.reference_center is not None]
        if points:
            series.append((result.sample.label, points, result.sample.path))
    if not series:
        return None
    fig = plt.figure(figsize=(2048 / DPI, 1024 / DPI), dpi=DPI, facecolor="white")
    ax = fig.add_axes((0.085, 0.14, 0.84 if len(series) <= 8 else 0.78, 0.75))
    for index, (label, points, _path) in enumerate(series):
        color = COLORS[index % len(COLORS)]
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        ax.scatter(xs, ys, s=25, color=color, alpha=.82, edgecolors="none", label=label)
    ax.axhline(0, color="black", lw=.7)
    ax.axhline(tolerance, color="#b9775d", lw=.65, ls="--")
    ax.axhline(-tolerance, color="#b9775d", lw=.65, ls="--")
    ax.axhspan(-tolerance, tolerance, color="#e9f0e5", alpha=.45, zorder=0)
    _style_axis(ax)
    ax.set_xlabel("reference peak / cm-1", fontsize=FS, fontfamily=FONT)
    ax.set_ylabel("delta = sample - reference / cm-1", fontsize=FS, fontfamily=FONT)
    ax.set_title("匹配峰位偏差", loc="left", pad=10,
                 fontsize=11, fontfamily=_cjk_font())
    all_x = [point[0] for _label, points, _path in series for point in points]
    ax.xaxis.set_major_locator(MultipleLocator(
        _x_step(max(all_x) - min(all_x)) if len(all_x) > 1 else 100))
    if len(series) <= 8:
        legend = ax.legend(loc="upper right", frameon=False, fontsize=8,
                           handletextpad=.4, borderaxespad=.3)
    else:
        legend = ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0),
                           frameon=False, fontsize=max(6.5, 8 - len(series) / 10),
                           borderaxespad=0)
    for text in legend.get_texts():
        text.set_fontfamily(_cjk_font())
    fig.text(0.985, 0.04, f"shaded: ±{tolerance:g} cm-1", ha="right", va="bottom",
             fontsize=7.5, color="#7f8c8d", fontfamily=FONT)
    save_figure(fig, str(path))
    plt.close(fig)
    return path


def write_manifest(path, payload):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix="." + path.stem + "-", suffix=".json",
                                         dir=path.parent, delete=False, mode="w",
                                         encoding="utf-8") as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2,
                      allow_nan=False, default=str)
            stream.write("\n")
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

# -*- coding: utf-8 -*-
"""Publication-grade figures for nanoindentation results.

The plotting layer never recalculates or filters instrument results.  It only
organises the existing analysis dictionary into traceable plotting units and
exports PNG/PDF/SVG figures with a fixed publication geometry.

Main figures
------------
Fig1  mechanical properties versus fluence
Fig2  paired differences (1210 - 1100)
Fig3  load-displacement curves as small multiples
Fig4  E-H response map with a high-hardness zoom
Fig5  within-file reproducibility (COV)
FigS1 quality control

Additional outputs: one three-panel figure per source file, captions, index,
manifest and a machine-checked validation report.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from matplotlib.ticker import MaxNLocator

from nano_core import mean_sd


# --------------------------------------------------------------------------
# Global publication style
# --------------------------------------------------------------------------

DPI = 600
DOUBLE_COLUMN_MM = 178.012  # 4205 px at 600 dpi; nominal 178 mm double column
mm = 1.0 / 25.4
OUTPUT_FORMATS = ("png",)  # overridden by config: 绘图 -> 格式
SAMPLE_COLORS = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9",
                 "#332288", "#88CCEE", "#117733", "#DDAA33", "#999933", "#AA4499"]
OKABE_ITO = SAMPLE_COLORS[:6]  # kept for backward compatibility
MARKERS = ["o", "s", "^", "D", "v", "P"]
LINE_STYLES = ["-", (0, (4, 2)), (0, (1, 2)), (0, (5, 2)),
               (0, (3, 1, 1, 1)), (0, (2, 1))]
HATCHES = ["", "//", "xx", "\\\\", "..", "++"]
RESPONSE_COLORS = {"high": "#1A1A1A", "low": "#7A7A7A", "mixed": "#555555"}

# Default orderable cold-to-warm ramp.  Eight hand-picked anchors keep the
# saturation up in bar charts; any number of measured classes is sampled from
# this continuous axis, so the anchor count never limits how many classes can
# be shown.  A longer ramp can be supplied through 绘图 -> 能量色阶.
DEFAULT_ENERGY_RAMP = [
    "#08306B", "#1F4E9C", "#2F6DB5", "#5A9BD4",
    "#E3A857", "#D9772B", "#B93A25", "#8C1A1A",
]
DEFAULT_PALETTE_EDGE = 0.06  # skip the darkest 6% at both ends of the axis


def _available_sans_font():
    names = {f.name for f in font_manager.fontManager.ttflist}
    for name in ("Microsoft YaHei", "SimHei", "Arial Unicode MS", "Noto Sans CJK SC"):
        if name in names:
            return name
    for name in ("Arial", "Helvetica", "Liberation Sans", "DejaVu Sans"):
        if name in names:
            return name
    return "DejaVu Sans"


def _configured_formats(cfg=None):
    """Return a de-duplicated subset of png/pdf/svg; default is PNG only."""
    raw = ((cfg or {}).get("绘图") or {}).get("格式", ["png"])
    if isinstance(raw, str):
        raw = re.split(r"[;,，、\s]+", raw)
    valid = {"png", "pdf", "svg"}
    out = []
    for item in raw or []:
        token = str(item).strip().lower()
        if token in valid and token not in out:
            out.append(token)
    return tuple(out) or ("png",)


def init_style(cfg=None):
    """Initialise a deterministic, print-oriented Matplotlib style."""
    global OUTPUT_FORMATS
    OUTPUT_FORMATS = _configured_formats(cfg)
    font = _available_sans_font()
    plt.rcParams.update({
        "font.family": font,
        "font.size": 7.5,
        "axes.titlesize": 8.0,
        "axes.labelsize": 8.0,
        "xtick.labelsize": 7.0,
        "ytick.labelsize": 7.0,
        "legend.fontsize": 7.0,
        "figure.titlesize": 8.0,
        "axes.linewidth": 0.7,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.unicode_minus": True,
        "xtick.direction": "out",
        "ytick.direction": "out",
        "xtick.major.size": 2.8,
        "ytick.major.size": 2.8,
        "xtick.major.width": 0.6,
        "ytick.major.width": 0.6,
        "xtick.minor.size": 1.6,
        "ytick.minor.size": 1.6,
        "xtick.minor.width": 0.45,
        "ytick.minor.width": 0.45,
        "lines.linewidth": 0.75,
        "lines.markersize": 3.2,
        "patch.linewidth": 0.6,
        "hatch.linewidth": 0.45,
        "savefig.dpi": DPI,
        "figure.dpi": 150,
        "savefig.facecolor": "white",
        "savefig.edgecolor": "none",
        "savefig.transparent": False,
        "savefig.bbox": None,
        "svg.hashsalt": "nanoindentation-publication-v1",
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
        "mathtext.fontset": "dejavusans",
        "axes.axisbelow": True,
    })
    return font


def _new_figure(w_mm, h_mm, nrows=1, ncols=1, **kwargs):
    w = float(w_mm) * mm
    h = float(h_mm) * mm
    fig, axes = plt.subplots(nrows, ncols,
                             figsize=(w, h),
                             layout="constrained",
                             facecolor="white",
                             squeeze=False,
                             **kwargs)
    _reserve_left_margin(fig)
    return fig, axes


def _reserve_left_margin(fig, left=0.018):
    """Keep rotated Chinese axis labels inside the fixed-size canvas."""
    engine = fig.get_layout_engine()
    if engine is not None:
        try:
            engine.set(rect=(left, 0.0, 1.0 - left, 1.0))
        except Exception:
            pass


def _style_axis(ax, grid_axis="y"):
    ax.tick_params(axis="both", which="major", pad=2.0)
    if grid_axis:
        ax.grid(True, axis=grid_axis, color="#D9D9D9", linewidth=0.40, alpha=0.55)
        ax.grid(False, axis="x" if grid_axis == "y" else "y")
    else:
        ax.grid(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_linewidth(0.7)
        ax.spines[spine].set_color("#222222")


def _panel_label(ax, letter, outside=False, y=0.985):
    if outside:
        ax.text(0.0, 1.005, f"({letter})", transform=ax.transAxes,
                ha="left", va="bottom", fontsize=9.0, fontweight="bold",
                color="#111111", zorder=20)
        return
    ax.text(0.012, y, f"({letter})", transform=ax.transAxes,
            ha="left", va="top", fontsize=9.0, fontweight="bold",
            color="#111111", zorder=20,
            bbox=dict(facecolor="white", edgecolor="none", alpha=0.85,
                      boxstyle="square,pad=0.08"))


def _finite(v):
    try:
        return v is not None and math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _legend_ncols(n_handles, max_cols=4):
    """One legend row while it fits, then wrap; never one column per handle."""
    return max(1, min(int(n_handles), int(max_cols)))


def _bottom_corner(ax, xs, ys):
    """Return an axes-fraction anchor in the emptier bottom corner."""
    xlim = ax.get_xlim()
    ylim = ax.get_ylim()
    xf = (np.asarray(xs, dtype=float) - xlim[0]) / max(xlim[1] - xlim[0], 1e-9)
    yf = (np.asarray(ys, dtype=float) - ylim[0]) / max(ylim[1] - ylim[0], 1e-9)
    left = int(np.sum((xf < 0.35) & (yf < 0.30)))
    right = int(np.sum((xf > 0.65) & (yf < 0.30)))
    return (0.02, 0.04) if left <= right else (0.60, 0.04)


def _fmt_p(p):
    if not _finite(p):
        return "p = n.a."
    if float(p) < 0.001:
        return "p < 0.001"
    return f"p = {float(p):.4f}"


def _fmt_energy(v):
    return f"{float(v):.2f}".rstrip("0").rstrip(".")


def _condition_ticks(ax, conditions, max_labels=4):
    """Label representative fluences; minor ticks retain every measured point.

    Eight numeric labels (0.39, 0.79, 3.54, 3.93, 4.72, 5.54, 13.01, 13.75)
    cannot be read at 178 mm without collisions.  Four major labels preserve
    magnitude, while minor ticks show every measured condition.
    """
    all_x = [c["x"] for c in conditions]
    if len(conditions) <= max_labels:
        shown = conditions
        minor = []
    else:
        idx = [0,
               int(round((len(conditions) - 1) / 3)),
               int(round(2 * (len(conditions) - 1) / 3)),
               len(conditions) - 1]
        shown = [conditions[i] for i in sorted(set(idx))]
        minor = all_x
    ax.set_xticks([c["x"] for c in shown],
                  [_fmt_energy(c["value"]) for c in shown])
    if minor:
        ax.set_xticks(minor, minor=True)
    ax.tick_params(axis="x", which="major", labelsize=6.6, pad=2.0)
    ax.tick_params(axis="x", which="minor", length=1.8, width=0.45,
                   color="#555555")
    for label in ax.get_xticklabels():
        label.set_rotation(0)
        label.set_horizontalalignment("center")
        label.set_rotation_mode("anchor")


def _padded_limits(values, fraction=0.055):
    vals = np.asarray([float(v) for v in values if _finite(v)], dtype=float)
    if vals.size == 0:
        return 0.0, 1.0
    lo, hi = float(np.min(vals)), float(np.max(vals))
    span = hi - lo
    if span <= 0:
        span = max(abs(hi), 1.0)
    return lo - fraction * span, hi + fraction * span


def _safe(name):
    return re.sub(r'[\\/:*?"<>|]+', "_", str(name))


def sample_order(analysis, cfg):
    groups = analysis["groups"]
    order = [g for g in cfg["组间比较"].get("样品编号顺序", []) if g in groups]
    order += [g for g in sorted(groups, key=str) if g not in order]
    return order


def _sample_styles(analysis, cfg):
    """Colour/marker/linestyle per sample.

    Sample colours come from config (绘图 -> 样品配色) when given, otherwise
    from a 12-colour palette; markers and linestyles cycle on a second axis so
    that styles stay distinguishable well beyond six samples.
    """
    order = sample_order(analysis, cfg)
    conf = ((cfg or {}).get("绘图") or {}) or {}
    configured = conf.get("样品配色") or conf.get("配色") or {}
    default_color = configured.get("其他") or configured.get("other")
    styles = {}
    used_colors = set()
    for i, s in enumerate(order):
        key = str(s)
        color = configured.get(key) or configured.get(s)
        if not color and default_color and default_color not in used_colors:
            # "其他" is a fallback for a single unlisted sample only; assigning
            # it to several samples would make them indistinguishable.
            color = default_color
        if not color:
            for step in range(len(SAMPLE_COLORS)):
                candidate = SAMPLE_COLORS[(i + step) % len(SAMPLE_COLORS)]
                if candidate not in used_colors:
                    color = candidate
                    break
            else:
                color = SAMPLE_COLORS[i % len(SAMPLE_COLORS)]
        used_colors.add(color)
        cycle = i // len(SAMPLE_COLORS)
        styles[s] = {
            "color": color,
            "marker": MARKERS[(i + cycle) % len(MARKERS)],
            "linestyle": LINE_STYLES[(i + cycle) % len(LINE_STYLES)],
        }
    return styles


def file_label(f):
    """Concise label: configured condition + sample, with no duplicated sample."""
    label = str(f.get("label") or "").strip()
    if not label:
        label = f["source"]
    sample = f.get("sample")
    if sample in (None, ""):
        return label
    if re.search(r"[（(]\s*" + re.escape(str(sample)) + r"\s*[)）]\s*$", label):
        return label
    return f"{label} ({sample})"


# --------------------------------------------------------------------------
# Data preparation: energy conditions and file-level plotting units
# --------------------------------------------------------------------------

def _group_text(f):
    return str(f.get("group") or f.get("label") or "unassigned").strip()


def _energy_from_text(text):
    m = re.search(r"[-+]?\d+(?:\.\d+)?", str(text))
    if not m:
        return None
    try:
        v = float(m.group(0))
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def condition_table(analysis):
    """Order conditions by numeric fluence; fall back to categorical order."""
    groups = []
    for f in analysis["files"]:
        g = _group_text(f)
        if g not in groups:
            groups.append(g)
    parsed = [_energy_from_text(g) for g in groups]
    numeric = all(p is not None for p in parsed)
    records = []
    for i, (name, value) in enumerate(zip(groups, parsed)):
        records.append({
            "key": name,
            "label": name,
            "value": float(value) if numeric else float(i),
            "x": float(value) if numeric else float(i),
            "numeric": bool(numeric),
        })
    records.sort(key=lambda r: (r["value"], r["label"]))
    for i, r in enumerate(records):
        r["index"] = i
    return records


def _condition_map(analysis, conditions):
    return {c["key"]: c for c in conditions}


def _condition_for_file(f, lookup):
    return lookup.get(_group_text(f))


def _file_indents(analysis):
    by_source = {}
    for it in analysis["indents"]:
        by_source.setdefault(it["source"], []).append(it)
    return by_source


def _values(rows, metric):
    return np.asarray([float(r[metric]) for r in rows if _finite(r.get(metric))],
                      dtype=float)


def build_file_units(analysis, conditions):
    """One plotting unit per source file, with values recomputed at indent level."""
    lookup = _condition_map(analysis, conditions)
    by_source = _file_indents(analysis)
    units = []
    for f in analysis["files"]:
        rows = by_source.get(f["source"], [])
        e = _values(rows, "E")
        h = _values(rows, "H")
        em, esd, en = mean_sd(e.tolist())
        hm, hsd, hn = mean_sd(h.tolist())
        units.append({
            "source": f["source"],
            "label": file_label(f),
            "sample": f["sample"],
            "point": f["point"],
            "group": _group_text(f),
            "condition": _condition_for_file(f, lookup),
            "n_E": int(en),
            "n_H": int(hn),
            "E_mean": float(em) if en else float("nan"),
            "E_sd": float(esd) if en > 1 else float("nan"),
            "H_mean": float(hm) if hn else float("nan"),
            "H_sd": float(hsd) if hn > 1 else float("nan"),
            "E_cov": float(esd / em * 100) if en > 1 and em else float("nan"),
            "H_cov": float(hsd / hm * 100) if hn > 1 and hm else float("nan"),
            "E": e.tolist(),
            "H": h.tolist(),
            "tests": [r["test_no"] for r in rows],
            "rows": rows,
            "file": f,
        })
    return units


def _response_groups(analysis, units):
    split = analysis.get("split")
    if not split:
        return {}, set(), set()
    high = set(split.get("high_files") or [])
    low = set(split.get("low_files") or [])
    response = {}
    for u in units:
        if u["source"] in high:
            response[u["source"]] = "high"
        elif u["source"] in low:
            response[u["source"]] = "low"
        else:
            response[u["source"]] = "mixed"
    return response, high, low


def _points_for_units(units, metric):
    out = []
    for u in units:
        for row in u["rows"]:
            if _finite(row.get(metric)):
                out.append((float(row[metric]), u))
    return out


def _unit_groups(units):
    grouped = {}
    for u in units:
        key = (u["condition"]["index"] if u.get("condition") else 9999, u["sample"])
        grouped.setdefault(key, []).append(u)
    return grouped


# --------------------------------------------------------------------------
# Saving and manifest
# --------------------------------------------------------------------------

def _save_figure(fig, out_dir, stem, manifest):
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    paths = {}
    for suffix in OUTPUT_FORMATS:
        path = folder / f"{stem}.{suffix}"
        if suffix == "png":
            fig.savefig(path, dpi=DPI, facecolor="white", edgecolor="none",
                        bbox_inches=None, transparent=False)
        elif suffix == "pdf":
            fig.savefig(path, facecolor="white", edgecolor="none",
                        bbox_inches=None, transparent=False,
                        metadata={"CreationDate": None, "ModDate": None})
        else:
            fig.savefig(path, facecolor="white", edgecolor="none",
                        bbox_inches=None, transparent=False,
                        metadata={"Date": None})
        paths[suffix] = str(path)
    stem_id = stem.replace("\\", "/")
    plt.close(fig)
    entry = {"stem": stem, "paths": paths}
    manifest["figures"].append(entry)
    return paths


# --------------------------------------------------------------------------
# Fig 1: mechanical properties versus fluence
# --------------------------------------------------------------------------

def plot_mechanical_properties(analysis, cfg, out_dir, manifest):
    conditions = condition_table(analysis)
    units = build_file_units(analysis, conditions)
    styles = _sample_styles(analysis, cfg)

    fig, axes = _new_figure(DOUBLE_COLUMN_MM, 82, 1, 2)
    stats = {"raw_points": 0, "file_means": 0, "conditions": len(conditions)}
    for ax, metric, label, letter in (
            (axes[0, 0], "E", r"弹性模量 $E$ (GPa)", "a"),
            (axes[0, 1], "H", r"硬度 $H$ (GPa)", "b")):
        for sample in sample_order(analysis, cfg):
            sub = [u for u in units if u["sample"] == sample and u.get("condition")]
            sub.sort(key=lambda u: u["condition"]["value"])
            xs, means, sds = [], [], []
            for u in sub:
                if not _finite(u[f"{metric}_mean"]):
                    continue
                x = u["condition"]["x"]
                xs.append(x)
                means.append(u[f"{metric}_mean"])
                sds.append(u[f"{metric}_sd"] if _finite(u[f"{metric}_sd"]) else np.nan)
                raw = u[f"{metric}"]
                if raw:
                    ax.scatter(
                        np.full(len(raw), x),
                        raw,
                        s=5.2,
                        color=styles[sample]["color"],
                        alpha=0.28,
                        linewidths=0,
                        marker=styles[sample]["marker"],
                        zorder=2,
                    )
                    if metric == "E":
                        stats["raw_points"] += len(raw)
            if not xs:
                continue
            ax.errorbar(
                xs, means, yerr=sds,
                fmt=styles[sample]["marker"],
                ms=4.4,
                mfc=styles[sample]["color"],
                mec="#111111",
                mew=0.35,
                color=styles[sample]["color"],
                ecolor=styles[sample]["color"],
                elinewidth=0.65,
                capsize=1.8,
                capthick=0.65,
                linestyle=styles[sample]["linestyle"],
                lw=0.65,
                zorder=4,
            )
            if metric == "E":
                stats["file_means"] += len(xs)
        ax.set_xlabel(r"能量密度 (J cm$^{-2}$)")
        ax.set_ylabel(label)
        ax.xaxis.set_major_locator(MaxNLocator(nbins=8))
        _condition_ticks(ax, conditions)
        _style_axis(ax)
        _panel_label(ax, letter)

    all_e = [v for u in units for v in u["E"]]
    all_h = [v for u in units for v in u["H"]]
    if all_e:
        axes[0, 0].set_ylim(*_padded_limits(all_e, 0.08))
    if all_h:
        axes[0, 1].set_ylim(*_padded_limits(all_h, 0.08))

    legend_handles = [
        Line2D([], [], color=styles[s]["color"], marker=styles[s]["marker"],
               linestyle=styles[s]["linestyle"], lw=0.7, ms=4.0,
               mec="#111111", mew=0.3, label=str(s))
        for s in sample_order(analysis, cfg)
    ]
    legend_handles.append(
        Line2D([], [], color="#8C8C8C", marker="o", linestyle="none",
               ms=3.0, alpha=0.55, label="单次压痕")
    )
    fig.legend(handles=legend_handles, loc="outside upper center", ncols=_legend_ncols(len(legend_handles)),
               frameon=False, handletextpad=0.5, columnspacing=1.4)

    _save_figure(fig, out_dir, "Fig1_mechanical_properties", manifest)
    return stats


# --------------------------------------------------------------------------
# Fig 2: paired effects (sample_b - sample_a from the configured pairs)
# --------------------------------------------------------------------------

def _pair_samples(analysis, cfg):
    """Return the (a, b) pair actually used for Fig2 and its captions."""
    if analysis.get("paired"):
        first = analysis["paired"][0]
        return first["sample_a"], first["sample_b"]
    configured = [list(p) for p in cfg["组间比较"].get("关注样品对", []) if len(p) == 2]
    if configured:
        return configured[0][0], configured[0][1]
    order = sample_order(analysis, cfg)
    return (order[0], order[1]) if len(order) > 1 else ("A", "B")


def plot_paired_effects(analysis, cfg, out_dir, manifest):
    conditions = condition_table(analysis)
    units = build_file_units(analysis, conditions)
    response, _, _ = _response_groups(analysis, units)
    # Key by (point, sample): one point normally holds one file per sample, so a
    # plain {point: unit} dict would keep only the last sample and mis-colour the
    # response markers whenever a third sample shares the point.
    lookup = {(u["point"], str(u["sample"])): u for u in units}
    pair_a, pair_b = _pair_samples(analysis, cfg)

    fig, axes = _new_figure(DOUBLE_COLUMN_MM, 82, 1, 2)
    stats = {"pairs": 0, "low_conditions": 0}
    for ax, metric, label, letter in (
            (axes[0, 0], "E",
             rf"$\Delta E = E_{{{pair_b}}}-E_{{{pair_a}}}$ (GPa)", "a"),
            (axes[0, 1], "H",
             rf"$\Delta H = H_{{{pair_b}}}-H_{{{pair_a}}}$ (GPa)", "b")):
        ylabel = label
        rows, xs, ys, responses = [], [], [], []
        for p in sorted(analysis["paired"],
                        key=lambda q: lookup.get((q["point"], str(q["sample_b"])),
                                                 {}).get("condition", {}).get("value", 0)):
            unit = (lookup.get((p["point"], str(p["sample_b"])))
                    or lookup.get((p["point"], str(p["sample_a"]))))
            if unit is None or unit.get("condition") is None:
                continue
            x = unit["condition"]["x"]
            y = float(p[f"d{metric}"])
            rows.append((x, y))
            xs.append(x)
            ys.append(y)
            responses.append(response.get(unit["source"], "mixed"))
        if metric == "E":
            stats["pairs"] += len(rows)
            stats["low_conditions"] += sum(1 for r in responses if r == "low")

        low_x = [x for x, r in zip(xs, responses) if r == "low"]
        if low_x:
            left = min(low_x) - 0.22
            right = max(low_x) + 0.22
            ax.axvspan(left, right, color="#EEEEEE", alpha=0.55, lw=0, zorder=0)

        for x, y, r in zip(xs, ys, responses):
            if r == "low":
                ax.scatter([x], [y], s=24, facecolors="none", edgecolors=RESPONSE_COLORS["low"],
                           linewidths=0.9, marker="s", zorder=4)
            elif r == "high":
                ax.scatter([x], [y], s=22, facecolors=RESPONSE_COLORS["high"],
                           edgecolors=RESPONSE_COLORS["high"], linewidths=0.55,
                           marker="o", zorder=4)
            else:
                ax.scatter([x], [y], s=22, facecolors=RESPONSE_COLORS["mixed"],
                           edgecolors=RESPONSE_COLORS["mixed"], linewidths=0.55,
                           marker="D", zorder=4)

        ax.axhline(0, color="#333333", lw=0.75, ls="--", zorder=2)
        mean_d = None
        if ys:
            mean_d = float(np.mean(ys))
            ax.axhline(mean_d, color="#444444", lw=0.7, ls=(0, (1.5, 1.5)), zorder=3)
        ax.set_xlabel(r"能量密度 (J cm$^{-2}$)")
        ax.set_ylabel(ylabel)
        _condition_ticks(ax, conditions)
        _style_axis(ax)
        _panel_label(ax, letter, outside=True)
        st = analysis.get("paired_stats", {}).get(metric)
        if st:
            ax.text(0.965, 0.965,
                    f"双尾精确置换检验\n{_fmt_p(st.get('p'))} "
                    f"（n = {st.get('n')} 对）",
                    transform=ax.transAxes, ha="right", va="top",
                    fontsize=6.6, color="#222222",
                    bbox=dict(facecolor="white", edgecolor="none", alpha=0.80,
                              boxstyle="round,pad=0.18"))
        if ys:
            ax.set_ylim(*_padded_limits(ys + [0.0], 0.10))
        if mean_d is not None:
            # Put the mean label in the emptier of the two bottom corners, so
            # it does not land on the large-magnitude points at high fluence.
            ax.text(*_bottom_corner(ax, xs, ys),
                    f"平均 $\\Delta${metric} = {mean_d:+.2f}",
                    transform=ax.transAxes, ha="left", va="bottom",
                    fontsize=6.8, color="#444444")

    handles = [
        Line2D([], [], marker="o", linestyle="none", color=RESPONSE_COLORS["high"],
               ms=4.0, label="高硬度响应"),
        Line2D([], [], marker="s", linestyle="none", markerfacecolor="none",
               markeredgecolor=RESPONSE_COLORS["low"], ms=4.0,
               label="低硬度响应"),
        Patch(facecolor="#EEEEEE", edgecolor="none", alpha=0.55,
              label="低硬度条件"),
    ]
    fig.legend(handles=handles, loc="outside upper center", ncols=_legend_ncols(len(handles)),
               frameon=False, handletextpad=0.5, columnspacing=1.3)
    _save_figure(fig, out_dir, "Fig2_paired_effects", manifest)
    return stats


# --------------------------------------------------------------------------
# Fig 3: load-displacement small multiples
# --------------------------------------------------------------------------

def plot_load_displacement(analysis, cfg, out_dir, manifest):
    conditions = condition_table(analysis)
    lookup = _condition_map(analysis, conditions)
    styles = _sample_styles(analysis, cfg)
    samples_all = sample_order(analysis, cfg)
    units = build_file_units(analysis, conditions)
    by_source = {u["source"]: u for u in units}

    n_conditions = len(conditions)
    if n_conditions == 0:
        raise ValueError("Fig3 requires at least one fluence condition")
    ncols = min(4, n_conditions)
    nrows = int(math.ceil(n_conditions / ncols))
    fig, axes = _new_figure(DOUBLE_COLUMN_MM, 79.0 * nrows,
                            nrows, ncols, sharey=True)
    axes = np.atleast_2d(axes)
    flat = list(axes.ravel())
    for ax in flat[n_conditions:]:
        ax.set_visible(False)
    stats = {
        "curves": 0,
        "per_condition": {},
        "per_condition_sample": {},
        "panels": n_conditions,
    }
    ymax_global = 1.0
    letters = [chr(ord("a") + i) for i in range(n_conditions)]
    for ax, cond, letter in zip(flat[:n_conditions], conditions, letters):
        rows = []
        for it in analysis["indents"]:
            unit = by_source.get(it["source"])
            if unit is None or unit.get("condition") is None:
                continue
            if unit["condition"]["key"] != cond["key"]:
                continue
            cur = it.get("curve_raw")
            if not cur:
                continue
            h = np.asarray(cur.get("h", []), dtype=float)
            p = np.asarray(cur.get("P", []), dtype=float)
            if h.size < 2 or p.size < 2:
                continue
            rows.append((it, h, p, unit["sample"]))
            stats["curves"] += 1
            stats["per_condition"][cond["key"]] = stats["per_condition"].get(cond["key"], 0) + 1
            stats["per_condition_sample"][str(unit["sample"])] = (
                stats["per_condition_sample"].get(str(unit["sample"]), 0) + 1)
            ymax_global = max(ymax_global, float(np.nanmax(p)))
        for sample in sample_order(analysis, cfg):
            sample_rows = sorted([r for r in rows if r[3] == sample],
                                 key=lambda r: (r[0].get("test_no") or 0))
            for it, h, p, _ in sample_rows:
                ax.plot(h, p, color=styles[sample]["color"], lw=0.48,
                        alpha=0.52, ls=styles[sample]["linestyle"], zorder=2)
        counts = {str(s): sum(1 for r in rows if r[3] == s)
                  for s in sample_order(analysis, cfg)}
        if len(samples_all) <= 2:
            note = "  ".join(f"{s}：$n$={counts.get(str(s), 0)}"
                             for s in samples_all)
        else:
            # More than two samples: the per-sample counts do not fit inside a
            # single small-multiple panel, so report the panel total instead.
            note = f"$n$ = {sum(counts.values())} 条曲线"
        ax.text(0.965, 0.965, note,
                transform=ax.transAxes, ha="right", va="top", fontsize=6.0,
                color="#333333",
                bbox=dict(facecolor="white", edgecolor="none", alpha=0.62,
                          boxstyle="square,pad=0.12"))
        ax.set_title(f"{_fmt_energy(cond['value'])} J cm$^{{-2}}$", pad=2.5)
        max_h = max((float(np.nanmax(r[1])) for r in rows), default=1.0)
        ax.set_xlim(0, max(20.0, max_h * 1.03))
        _style_axis(ax)
        _panel_label(ax, letter)

    # shared y-scale and axis labels
    for ax in flat[:n_conditions]:
        ax.set_ylim(0, ymax_global * 1.055)
    for ax in axes[-1, :]:
        if ax.get_visible():
            ax.set_xlabel(r"位移 $h$ (nm)")
    for ax in axes[:, 0]:
        if ax.get_visible():
            ax.set_ylabel(r"载荷 $P$ (mN)")

    handles = [
        Line2D([], [], color=styles[s]["color"], lw=0.9,
               ls=styles[s]["linestyle"], label=str(s))
        for s in sample_order(analysis, cfg)
    ]
    fig.legend(handles=handles, loc="outside upper center", ncols=_legend_ncols(len(handles)),
               frameon=False, handletextpad=0.7, columnspacing=2.0)
    _save_figure(fig, out_dir, "Fig3_load_displacement_small_multiples", manifest)
    return stats


# --------------------------------------------------------------------------
# Retained overview forms: merged curves and grouped bars
# --------------------------------------------------------------------------

# Sequential energy palette: measured fluence increases from cold blue to
# warm red along one continuous axis.  The class count is decoupled from the
# number of anchors: n classes are sampled at n evenly spaced positions of the
# axis, so 2, 6, 12 or 20 groups all get their own step without redefining the
# ramp.  The ramp itself (and the margin at both ends) is configurable.
def _energy_ramp(cfg=None):
    raw = (((cfg or {}).get("绘图") or {}) or {}).get("能量色阶")
    if isinstance(raw, str):
        raw = re.split(r"[;,，、\s]+", raw)
    colors = []
    for item in raw or []:
        token = str(item).strip()
        if re.fullmatch(r"#?[0-9a-fA-F]{6}", token):
            colors.append(token if token.startswith("#") else "#" + token)
    return colors if len(colors) >= 2 else list(DEFAULT_ENERGY_RAMP)


def _energy_cmap(cfg=None):
    return LinearSegmentedColormap.from_list("energy_cold_warm", _energy_ramp(cfg))


def _palette_edge(cfg=None):
    raw = (((cfg or {}).get("绘图") or {}) or {}).get("端点边距", DEFAULT_PALETTE_EDGE)
    try:
        edge = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_PALETTE_EDGE
    if not math.isfinite(edge):
        return DEFAULT_PALETTE_EDGE
    return min(max(edge, 0.0), 0.35)


def _condition_palette(conditions, cfg=None):
    """Map measured fluence classes to evenly spaced points on the ramp.

    The sample positions run from ``edge`` to ``1 - edge`` so that a small
    number of classes does not land on the darkest ends of the axis.
    """
    n = len(conditions)
    cmap = _energy_cmap(cfg)
    edge = _palette_edge(cfg)
    if n <= 0:
        return {}
    if n == 1:
        return {conditions[0]["key"]: cmap(0.5)}
    span = 1.0 - 2.0 * edge
    return {c["key"]: cmap(edge + span * i / (n - 1))
            for i, c in enumerate(conditions)}


def _label_indices(n, max_labels=8):
    """Return evenly spaced label positions, always including both ends."""
    if n <= 0:
        return []
    if n <= max_labels:
        return list(range(n))
    raw = np.linspace(0, n - 1, max_labels)
    return sorted({int(round(v)) for v in raw})


def plot_merged_curves(analysis, cfg, out_dir, manifest):
    """Retain the all-curves overlay, with energy colour and sample linetype."""
    conditions = condition_table(analysis)
    lookup = _condition_map(analysis, conditions)
    styles = _sample_styles(analysis, cfg)
    units = build_file_units(analysis, conditions)
    by_source = {u["source"]: u for u in units}
    cond_colors = _condition_palette(conditions, cfg)

    fig = plt.figure(figsize=(DOUBLE_COLUMN_MM * mm, 105 * mm),
                     layout="constrained", facecolor="white")
    _reserve_left_margin(fig)
    gs = fig.add_gridspec(2, 1, height_ratios=[1.0, 0.072])
    ax = fig.add_subplot(gs[0, 0])
    cax = fig.add_subplot(gs[1, 0])

    stats = {"curves": 0}
    max_h = 1.0
    max_p = 1.0
    for it in analysis["indents"]:
        unit = by_source.get(it["source"])
        if unit is None or unit.get("condition") is None:
            continue
        cur = it.get("curve_raw")
        if not cur:
            continue
        h = np.asarray(cur.get("h", []), dtype=float)
        p = np.asarray(cur.get("P", []), dtype=float)
        if h.size < 2 or p.size < 2:
            continue
        ax.plot(h, p,
                color=cond_colors.get(unit["condition"]["key"], "#777777"),
                ls=styles[unit["sample"]]["linestyle"], lw=0.50, alpha=0.42,
                zorder=2)
        stats["curves"] += 1
        max_h = max(max_h, float(np.nanmax(h)))
        max_p = max(max_p, float(np.nanmax(p)))

    ax.set_xlim(0, max_h * 1.03)
    ax.set_ylim(0, max_p * 1.055)
    ax.set_xlabel(r"位移 $h$ (nm)")
    ax.set_ylabel(r"载荷 $P$ (mN)")
    _style_axis(ax)

    handles = [
        Line2D([], [], color="#555555", ls=styles[s]["linestyle"], lw=0.8,
               label=str(s))
        for s in sample_order(analysis, cfg)
    ]
    fig.legend(handles=handles, loc="outside upper center", ncols=_legend_ncols(len(handles)),
               frameon=False, handletextpad=0.7, columnspacing=2.0)

    for i, cond in enumerate(conditions):
        cax.add_patch(Rectangle((i, 0), 1, 1,
                                facecolor=cond_colors[cond["key"]],
                                edgecolor="none"))
    cax.set_xlim(0, len(conditions))
    cax.set_ylim(0, 1)
    cax.set_yticks([])
    cax.set_xticks([i + 0.5 for i in _label_indices(len(conditions))],
                   [_fmt_energy(conditions[i]["value"])
                    for i in _label_indices(len(conditions))])
    cax.tick_params(axis="x", length=0, pad=1.5, labelsize=6.2)
    cax.set_xlabel(r"实测能量密度 (J cm$^{-2}$)", labelpad=1.5)
    for spine in cax.spines.values():
        spine.set_visible(False)

    _save_figure(fig, out_dir, "merged_curves", manifest)
    return stats


def plot_grouped_bar(analysis, cfg, out_dir, manifest):
    """Retain energy-grouped bars, with SD, individual indents and no zero fill."""
    conditions = condition_table(analysis)
    units = build_file_units(analysis, conditions)
    samples = sample_order(analysis, cfg)
    cond_colors = _condition_palette(conditions, cfg)
    lookup = {(u["condition"]["key"], u["sample"]): u
              for u in units if u.get("condition") is not None}

    fig, axes = _new_figure(DOUBLE_COLUMN_MM, 122, 2, 1, sharex=True)
    pos = np.arange(len(conditions), dtype=float)
    if not len(conditions) or not len(samples):
        raise ValueError("grouped_bar requires at least one condition and one sample")
    # Keep the grouped bars inside one categorical slot regardless of how many
    # samples the next batch contains.
    width = min(0.36, 0.76 / len(samples))
    step = width + 0.04
    offsets = {s: (i - (len(samples) - 1) / 2.0) * step
               for i, s in enumerate(samples)}
    # Scale the value labels with the *physical* bar width.  At eight
    # conditions a five-character mean fills about 80% of one bar; with more
    # conditions both the bars and the labels shrink together.
    value_fs = 7.8 if len(conditions) <= 8 else max(4.4, 7.8 * 8.0 / len(conditions))
    tick_fs = 6.2 if len(conditions) <= 8 else (5.0 if len(conditions) <= 12 else 4.0)
    stats = {"bars": 0, "points": 0}

    for ax, metric, label, letter in (
            (axes[0, 0], "E", r"弹性模量 $E$ (GPa)", "a"),
            (axes[1, 0], "H", r"硬度 $H$ (GPa)", "b")):
        top = 0.0
        metric_bars = 0
        metric_points = 0
        for si, sample in enumerate(samples):
            means, sds = [], []
            for cond in conditions:
                u = lookup.get((cond["key"], sample))
                if u is None or not _finite(u[f"{metric}_mean"]):
                    means.append(np.nan)
                    sds.append(0.0)
                    continue
                means.append(float(u[f"{metric}_mean"]))
                sds.append(float(u[f"{metric}_sd"])
                           if _finite(u[f"{metric}_sd"]) else 0.0)
                top = max(top, float(u[f"{metric}_mean"]) +
                          (float(u[f"{metric}_sd"]) if _finite(u[f"{metric}_sd"]) else 0.0))
            x = pos + offsets[sample]
            hatch = HATCHES[si % len(HATCHES)]
            colors = [cond_colors[c["key"]] for c in conditions]
            # Same energy must be exactly the same fill colour for both
            # samples.  The solid/hatched distinction alone carries the sample
            # identity; do not change opacity or hue between the pair.
            bars = ax.bar(x, means, width=width, color=colors,
                          edgecolor="#222222", linewidth=0.4, alpha=0.92,
                          hatch=hatch, yerr=sds, capsize=1.8,
                          error_kw={"ecolor": "#222222", "elinewidth": 0.55,
                                    "capthick": 0.55}, zorder=3)
            if hatch:
                # Keep the hatch visible on both dark cold colours and light
                # warm colours without changing the bar's fill colour.
                for patch in bars:
                    r, g, b, _ = patch.get_facecolor()
                    lum = (0.2126 * (0.92 * r + 0.08)
                           + 0.7152 * (0.92 * g + 0.08)
                           + 0.0722 * (0.92 * b + 0.08))
                    patch.set_hatchcolor("#F4F4F4" if lum < 0.55 else "#333333")
            metric_bars += sum(1 for m in means if _finite(m))

            jitter = np.linspace(-0.24 * width, 0.24 * width, 6) if len(conditions) else []
            for j, cond in enumerate(conditions):
                u = lookup.get((cond["key"], sample))
                if u is None:
                    continue
                raw = np.asarray(u[metric], dtype=float)
                raw = raw[np.isfinite(raw)]
                if raw.size == 0:
                    continue
                jj = jitter[:raw.size] if raw.size <= len(jitter) else np.linspace(-0.24 * width, 0.24 * width, raw.size)
                ax.scatter(np.full(raw.size, x[j]) + jj, raw, s=2.8,
                           facecolors="#111111", edgecolors="none", alpha=0.45,
                           zorder=5)
                metric_points += raw.size

            # Standard bar-chart convention: centre the mean text over the
            # error-bar cap.  The label marks the top of the uncertainty range,
            # so readers do not confuse it with the bar height.
            for j, mean in enumerate(means):
                if not _finite(mean):
                    continue
                ax.annotate(f"{mean:.1f}",
                            xy=(x[j], mean + sds[j]),
                            xytext=(0, 2.5), textcoords="offset points",
                            ha="center", va="bottom",
                            fontsize=value_fs, color="#222222",
                            zorder=6, clip_on=False)

        stats["bars"] = max(stats["bars"], metric_bars)
        stats["points"] = max(stats["points"], metric_points)
        ax.set_ylabel(label)
        ax.set_ylim(0, max(1.0, top * 1.10))
        shown = _label_indices(len(conditions), max_labels=8)
        ax.set_xticks([pos[i] for i in shown],
                      [f"{_fmt_energy(conditions[i]['value'])}\nJ cm$^{{-2}}$"
                       for i in shown])
        ax.tick_params(axis="x", labelsize=tick_fs, pad=2.0)
        _style_axis(ax)
        _panel_label(ax, letter, y=0.955)

    axes[1, 0].set_xlabel(r"实测能量密度")
    handles = [
        Patch(facecolor="#7A7A7A", edgecolor="#222222",
              linewidth=0.35, alpha=0.92,
              hatch=HATCHES[i % len(HATCHES)], label=str(s))
        for i, s in enumerate(samples)
    ]
    for h in handles:
        if h.get_hatch():
            h.set_hatchcolor("#F4F4F4")
    handles.append(Line2D([], [], marker="o", linestyle="none", color="#111111",
                          ms=2.8, alpha=0.65, label="单次压痕"))
    fig.legend(handles=handles, loc="outside upper center",
               ncols=_legend_ncols(len(handles), 5), frameon=False, handletextpad=0.5,
               columnspacing=1.3)
    _save_figure(fig, out_dir, "grouped_bar", manifest)
    return stats


# --------------------------------------------------------------------------
# Fig 4: E-H response map
# --------------------------------------------------------------------------

def plot_eh_response_map(analysis, cfg, out_dir, manifest):
    conditions = condition_table(analysis)
    units = build_file_units(analysis, conditions)
    response, high_sources, _ = _response_groups(analysis, units)
    styles = _sample_styles(analysis, cfg)

    # The measured fluence levels are categorical; the eight ordered classes
    # run from cold blue to warm red without interpolating unmeasured levels.
    cond_colors = _condition_palette(conditions, cfg)

    fig = plt.figure(figsize=(DOUBLE_COLUMN_MM * mm, 96 * mm),
                     layout="constrained", facecolor="white")
    _reserve_left_margin(fig)
    gs = fig.add_gridspec(2, 2, height_ratios=[1.0, 0.075])
    ax_all = fig.add_subplot(gs[0, 0])
    ax_zoom = fig.add_subplot(gs[0, 1])
    cax = fig.add_subplot(gs[1, :])

    def draw_units(ax, selected):
        for u in selected:
            x = np.asarray(u["E"], dtype=float)
            y = np.asarray(u["H"], dtype=float)
            if x.size == 0 or y.size == 0:
                continue
            color = cond_colors.get(u["condition"]["key"], "#777777")
            ax.scatter(x, y, s=8.0, color=[color], marker=styles[u["sample"]]["marker"],
                       linewidths=0.25, edgecolors="#333333", alpha=0.72, zorder=3)
        # file-level means
        for u in selected:
            if _finite(u["E_mean"]) and _finite(u["H_mean"]):
                ax.scatter([u["E_mean"]], [u["H_mean"]], marker="X", s=34,
                           facecolors="white", edgecolors="#111111",
                           linewidths=0.65, zorder=5)

    draw_units(ax_all, units)
    all_x = [v for u in units for v in u["E"]]
    all_y = [v for u in units for v in u["H"]]
    ax_all.set_xlim(*_padded_limits(all_x, 0.055))
    ax_all.set_ylim(*_padded_limits(all_y, 0.055))
    ax_all.set_xlabel(r"弹性模量 $E$ (GPa)")
    ax_all.set_ylabel(r"硬度 $H$ (GPa)")
    _style_axis(ax_all, grid_axis="both")
    _panel_label(ax_all, "a")

    stats = {"all_points": 0, "zoom_points": 0, "file_means": 0}
    stats["all_points"] = sum(len(u["E"]) for u in units)
    stats["file_means"] = sum(1 for u in units if _finite(u["E_mean"]))

    if high_sources:
        zoom_units = [u for u in units if u["source"] in high_sources]
        zoom_title = "高硬度响应（局部放大）"
    else:
        zoom_units = list(units)
        zoom_title = "密集区域"
    draw_units(ax_zoom, zoom_units)
    zx = [v for u in zoom_units for v in u["E"]]
    zy = [v for u in zoom_units for v in u["H"]]
    if zx and zy:
        ax_zoom.set_xlim(*_padded_limits(zx, 0.055))
        ax_zoom.set_ylim(*_padded_limits(zy, 0.055))
    ax_zoom.set_xlabel(r"弹性模量 $E$ (GPa)")
    ax_zoom.set_ylabel(r"硬度 $H$ (GPa)")
    ax_zoom.set_title(zoom_title, pad=2.5)
    _style_axis(ax_zoom, grid_axis="both")
    _panel_label(ax_zoom, "b")
    stats["zoom_points"] = sum(len(u["E"]) for u in zoom_units)

    # Horizontal class bar, one block per measured fluence.  Rectangles keep
    # both PDF and SVG fully vector.
    for i, cond in enumerate(conditions):
        cax.add_patch(Rectangle((i, 0), 1, 1,
                                facecolor=cond_colors[cond["key"]],
                                edgecolor="none"))
    cax.set_xlim(0, len(conditions))
    cax.set_ylim(0, 1)
    cax.set_yticks([])
    cax.set_xticks([i + 0.5 for i in _label_indices(len(conditions))],
                   [_fmt_energy(conditions[i]["value"])
                    for i in _label_indices(len(conditions))])
    cax.tick_params(axis="x", length=0, pad=1.5, labelsize=6.4)
    cax.set_xlabel(r"实测能量密度 (J cm$^{-2}$)", labelpad=1.5)
    for spine in cax.spines.values():
        spine.set_visible(False)

    handles = [
        Line2D([], [], marker=styles[s]["marker"], linestyle="none",
               color="#555555", ms=3.4, label=str(s))
        for s in sample_order(analysis, cfg)
    ]
    handles.append(Line2D([], [], marker="X", linestyle="none", color="none",
                          markerfacecolor="white", markeredgecolor="#111111",
                          ms=5.0, label="文件均值"))
    fig.legend(handles=handles, loc="outside upper center", ncols=_legend_ncols(len(handles)),
               frameon=False, handletextpad=0.5, columnspacing=1.2)

    _save_figure(fig, out_dir, "Fig4_EH_response_map", manifest)
    return stats


# --------------------------------------------------------------------------
# Fig 5: within-file reproducibility
# --------------------------------------------------------------------------

def plot_reproducibility(analysis, cfg, out_dir, manifest):
    conditions = condition_table(analysis)
    units = build_file_units(analysis, conditions)
    styles = _sample_styles(analysis, cfg)

    thresholds = []
    mg = analysis.get("merge") or {}
    if _finite(mg.get("E上限")):
        thresholds.append(("E", float(mg["E上限"])))
    if _finite(mg.get("H上限")):
        thresholds.append(("H", float(mg["H上限"])))

    fig, axes = _new_figure(DOUBLE_COLUMN_MM, 82, 1, 2)
    stats = {"values": 0, "max_cov": 0.0}
    for ax, metric, letter in ((axes[0, 0], "E", "a"), (axes[0, 1], "H", "b")):
        for sample in sample_order(analysis, cfg):
            sub = sorted([u for u in units if u["sample"] == sample and u.get("condition")],
                         key=lambda u: u["condition"]["value"])
            xs = [u["condition"]["x"] for u in sub if _finite(u[f"{metric}_cov"])]
            ys = [float(u[f"{metric}_cov"]) for u in sub if _finite(u[f"{metric}_cov"])]
            if not xs:
                continue
            ax.plot(xs, ys, color=styles[sample]["color"],
                    ls=styles[sample]["linestyle"], lw=0.7,
                    marker=styles[sample]["marker"], ms=4.2,
                    mfc=styles[sample]["color"], mec="#111111", mew=0.3,
                    zorder=4)
            stats["values"] += len(ys)
            stats["max_cov"] = max([stats["max_cov"]] + ys)
        ax.set_xlabel(r"能量密度 (J cm$^{-2}$)")
        ax.set_ylabel(r"批内变异系数 (%)")
        _condition_ticks(ax, conditions)
        ax.set_ylim(0, max(48.0, stats["max_cov"] * 1.12))
        _style_axis(ax)
        ax.axhline(5.0, color="#555555", lw=0.65, ls=(0, (4, 2)), zorder=1)
        ax.axhline(10.0, color="#999999", lw=0.55, ls=(0, (1.5, 2)), zorder=1)
        ax.text(0.985, 5.9, "5% 合并判据", transform=ax.get_yaxis_transform(),
                ha="right", va="bottom", fontsize=5.8, color="#555555")
        ax.text(0.985, 10.9, "10% 质量提示", transform=ax.get_yaxis_transform(),
                ha="right", va="bottom", fontsize=5.8, color="#777777")
        _panel_label(ax, letter)

    handles = [
        Line2D([], [], color=styles[s]["color"], marker=styles[s]["marker"],
               linestyle=styles[s]["linestyle"], lw=0.7, ms=4.0,
               mec="#111111", mew=0.3, label=str(s))
        for s in sample_order(analysis, cfg)
    ]
    fig.legend(handles=handles, loc="outside upper center", ncols=_legend_ncols(len(handles)),
               frameon=False, handletextpad=0.6, columnspacing=1.6)
    _save_figure(fig, out_dir, "Fig5_reproducibility", manifest)
    return stats


# --------------------------------------------------------------------------
# Fig S1: quality control
# --------------------------------------------------------------------------

def plot_quality_control(analysis, cfg, out_dir, manifest):
    conditions = condition_table(analysis)
    units = build_file_units(analysis, conditions)
    styles = _sample_styles(analysis, cfg)

    tol = float(cfg["载荷容差_pct"])
    drift_lim = float(cfg["漂移上限_nm_s"])
    min_n = 3

    fig, axes = _new_figure(DOUBLE_COLUMN_MM, 76, 1, 3)
    stats = {"load_fail": 0, "drift_fail": 0, "n_fail": 0}
    for ax, metric, ylabel, limit, letter in (
            (axes[0, 0], "Pmax_dev_pct", r"载荷偏差 (%)", tol, "a"),
            (axes[0, 1], "drift_abs", r"漂移绝对值 (nm s$^{-1}$)", drift_lim, "b"),
            (axes[0, 2], "n", r"有效压痕数", min_n, "c")):
        for u in units:
            f = u["file"]
            if u.get("condition") is None:
                continue
            x = u["condition"]["x"]
            value = (abs(float(f.get("drift_mean") or 0.0))
                     if metric == "drift_abs" else float(f.get(metric) or 0.0))
            failed = (
                metric == "Pmax_dev_pct" and value > tol
                or metric == "drift_abs" and value > drift_lim
                or metric == "n" and int(value) < min_n
            )
            if failed:
                stats[{"Pmax_dev_pct": "load_fail",
                       "drift_abs": "drift_fail",
                       "n": "n_fail"}[metric]] += 1
            ax.scatter([x], [value], s=25,
                       facecolors="white" if failed else styles[u["sample"]]["color"],
                       edgecolors="#D55E00" if failed else "#222222",
                       linewidths=1.0 if failed else 0.3,
                       marker=styles[u["sample"]]["marker"], zorder=4)
        if metric != "n":
            ax.axhline(limit, color="#333333", lw=0.7, ls=(0, (4, 2)), zorder=1)
            ax.text(0.985, limit * 1.045, f"限值 {limit:g}", transform=ax.get_yaxis_transform(),
                    ha="right", va="bottom", fontsize=5.8, color="#333333")
        else:
            ax.axhline(min_n, color="#333333", lw=0.7, ls=(0, (4, 2)), zorder=1)
            ax.text(0.985, min_n + 0.18, "最少 n = 3", transform=ax.get_yaxis_transform(),
                    ha="right", va="bottom", fontsize=5.8, color="#333333")
            ax.set_ylim(0, max(7.0, min_n + 3))
        ax.set_xlabel(r"能量密度 (J cm$^{-2}$)")
        ax.set_ylabel(ylabel)
        _condition_ticks(ax, conditions, max_labels=4)
        _style_axis(ax)
        _panel_label(ax, letter, y=0.91)

    handles = [
        Line2D([], [], marker=styles[s]["marker"], linestyle="none",
               color=styles[s]["color"], ms=4.0, label=str(s))
        for s in sample_order(analysis, cfg)
    ]
    handles.append(Line2D([], [], marker="o", linestyle="none",
                          markerfacecolor="white", markeredgecolor="#D55E00",
                          ms=4.0, label="质控超限"))
    fig.legend(handles=handles, loc="outside upper center", ncols=_legend_ncols(len(handles)),
               frameon=False, handletextpad=0.5, columnspacing=1.2)
    _save_figure(fig, out_dir, "FigS1_quality_control", manifest)
    return stats


# --------------------------------------------------------------------------
# Per-file supplementary figures
# --------------------------------------------------------------------------

def plot_per_file(analysis, cfg, out_dir, manifest):
    conditions = condition_table(analysis)
    units = build_file_units(analysis, conditions)
    styles = _sample_styles(analysis, cfg)
    folder = Path(out_dir) / "per_file"
    folder.mkdir(parents=True, exist_ok=True)
    stats = {"files": 0, "curves": 0, "missing_values": 0}

    for u in units:
        f = u["file"]
        fig, axes = _new_figure(DOUBLE_COLUMN_MM, 102, 1, 3)
        fig.get_layout_engine().set(rect=(0.018, 0, 1 - 0.018, 0.93))
        ax = axes[0, 0]
        curves = []
        for it in u["rows"]:
            cur = it.get("curve_raw")
            if not cur:
                continue
            h = np.asarray(cur.get("h", []), dtype=float)
            p = np.asarray(cur.get("P", []), dtype=float)
            if h.size < 2 or p.size < 2:
                continue
            curves.append((it.get("test_no") or 0, h, p))
        curves.sort(key=lambda c: c[0])
        for k, (test_no, h, p) in enumerate(curves):
            ax.plot(h, p, color=styles[u["sample"]]["color"],
                    lw=0.55, alpha=0.52,
                    ls=styles[u["sample"]]["linestyle"], zorder=2)
            stats["curves"] += 1
        max_h = max((float(np.nanmax(c[1])) for c in curves), default=1.0)
        ax.set_xlim(0, max(20.0, max_h * 1.04))
        ax.set_ylim(0, 31.5)
        ax.set_xlabel(r"位移 $h$ (nm)")
        ax.set_ylabel(r"载荷 $P$ (mN)")
        ax.text(0.97, 0.96, f"$n$ = {len(curves)} 条曲线",
                transform=ax.transAxes, ha="right", va="top", fontsize=6.8)
        _style_axis(ax)
        _panel_label(ax, "a")

        for ax2, metric, label, letter in (
                (axes[0, 1], "E", r"弹性模量 $E$ (GPa)", "b"),
                (axes[0, 2], "H", r"硬度 $H$ (GPa)", "c")):
            pairs = [((it.get("test_no") or 0), float(it[metric]))
                     for it in u["rows"] if _finite(it.get(metric))]
            stats["missing_values"] += len(u["rows"]) - len(pairs)
            if pairs:
                xs = [p[0] for p in pairs]
                ys = [p[1] for p in pairs]
                ax2.plot(xs, ys, color=styles[u["sample"]]["color"],
                         lw=0.55, alpha=0.70)
                ax2.scatter(xs, ys, s=22, color=styles[u["sample"]]["color"],
                            edgecolors="#111111", linewidths=0.3, zorder=4)
                mean, sd, n = mean_sd(ys)
                ax2.axhline(mean, color="#111111", lw=0.75, ls="--", zorder=2)
                if n > 1 and _finite(sd):
                    ax2.axhspan(mean - sd, mean + sd, color=styles[u["sample"]]["color"],
                                alpha=0.10, lw=0, zorder=1)
                ax2.set_title(
                    f"均值 = {mean:.2f} GPa，$s$ = {sd:.2f} GPa（$n$ = {n}）",
                    loc="right", fontsize=6.2, pad=2.0
                )
                lo, hi = _padded_limits(ys, 0.09)
                ax2.set_ylim(lo, hi)
            else:
                ax2.text(0.5, 0.5, "无有效数据", transform=ax2.transAxes,
                         ha="center", va="center", fontsize=7)
                ax2.set_title("无有效数据", loc="right", fontsize=6.2, pad=2.0)
            ax2.set_xlabel("测试序号")
            ax2.set_ylabel(label)
            ax2.xaxis.set_major_locator(MaxNLocator(integer=True, nbins=6))
            _style_axis(ax2)
            _panel_label(ax2, letter)

        source = f["source"]
        energy = _fmt_energy(u["condition"]["value"]) if u.get("condition") else "n.a."
        fig.suptitle(f"样品 {u['sample']}  |  {energy} J cm$^{{-2}}$  |  {source}",
                     fontsize=7.2, y=0.992, va="top")
        stem = f"per_file/{_safe(Path(source).stem)}"
        safe_stem = _safe(Path(source).stem)
        _save_figure(fig, folder, safe_stem, manifest)
        manifest["figures"][-1]["stem"] = stem
        stats["files"] += 1
    return stats


# --------------------------------------------------------------------------
# Captions, index, validation
# --------------------------------------------------------------------------

def _image_png_info(path):
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return {}
    width, height = struct.unpack(">II", data[16:24])
    dpi_x = dpi_y = None
    pos = 8
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        if ctype == b"pHYs" and len(chunk) >= 9:
            ppux, ppuy, unit = struct.unpack(">IIB", chunk[:9])
            if unit == 1:
                dpi_x = ppux * 0.0254
                dpi_y = ppuy * 0.0254
            break
        pos += 12 + length
    return {"width": width, "height": height, "dpi_x": dpi_x, "dpi_y": dpi_y}


def _vector_check(path, suffix):
    path = Path(path)
    if not path.is_file() or path.stat().st_size < 100:
        return False, "missing or empty"
    data = path.read_bytes()
    if suffix == "pdf":
        if not data.startswith(b"%PDF"):
            return False, "not a PDF"
        if b"/Subtype /Image" in data:
            return False, "contains raster image"
        if b"/Font" not in data:
            return False, "no vector text/font object"
        return True, "vector PDF with font objects, no raster image"
    if suffix == "svg":
        text = data.decode("utf-8", errors="replace")
        if "<svg" not in text:
            return False, "not an SVG"
        if "<image" in text:
            return False, "contains raster image"
        if "<path" not in text:
            return False, "no vector path"
        if "<text" not in text:
            return False, "no editable text"
        return True, "vector SVG with paths and text, no raster image"
    return False, "unsupported suffix"


def _why_checks(analysis, units, fig_stats):
    checks = []
    conditions = condition_table(analysis)

    def add(category, item, expected, actual, ok, note=""):
        checks.append({
            "category": category,
            "item": item,
            "expected": expected,
            "actual": actual,
            "status": "Pass" if ok else "Fail",
            "note": note,
        })

    # Inputs / plotting units.  Expected counts are derived from the current
    # input rather than frozen to the eight-condition development batch.
    add("Data", "Input files", len(analysis["files"]), len(analysis["files"]),
        len(analysis["files"]) > 0, "Dynamic expected count from current input")
    total_n = sum(int(f["n"]) for f in analysis["files"])
    add("Data", "Valid indents", total_n, total_n, total_n > 0,
        "Dynamic expected count from current input")
    add("Data", "Plotting units", len(units), len(units), len(units) > 0)
    add("Data", "Fluence conditions", len(conditions), fig_stats["Fig1"]["conditions"],
        fig_stats["Fig1"]["conditions"] == len(conditions) and len(conditions) > 0)
    add("Data", "Raw points in Fig1", total_n, fig_stats["Fig1"]["raw_points"],
        fig_stats["Fig1"]["raw_points"] == total_n)
    add("Data", "File means in Fig1", len(units), fig_stats["Fig1"]["file_means"],
        fig_stats["Fig1"]["file_means"] == len(units))
    add("Data", "Pairs in Fig2", len(analysis["paired"]), fig_stats["Fig2"]["pairs"],
        fig_stats["Fig2"]["pairs"] == len(analysis["paired"]))
    expected_curves = sum(1 for it in analysis["indents"] if it.get("curve_raw"))
    add("Data", "Raw curves in Fig3", expected_curves, fig_stats["Fig3"]["curves"],
        fig_stats["Fig3"]["curves"] == expected_curves)
    add("Data", "Points in Fig4 full map", total_n, fig_stats["Fig4"]["all_points"],
        fig_stats["Fig4"]["all_points"] == total_n)
    add("Data", "COV results in Fig5", len(units) * 2, fig_stats["Fig5"]["values"],
        fig_stats["Fig5"]["values"] == len(units) * 2)
    add("Data", "Curves in merged_curves", expected_curves,
        fig_stats["merged_curves"]["curves"],
        fig_stats["merged_curves"]["curves"] == expected_curves)
    bars = fig_stats["grouped_bar"]
    add("Data", "Bars and indents in grouped_bar", f"{len(units)} bars / {total_n} points",
        f"{bars['bars']} bars / {bars['points']} points",
        bars["bars"] == len(units) and bars["points"] == total_n)
    add("Data", "Per-file figures", len(units), fig_stats["per_file"]["files"],
        fig_stats["per_file"]["files"] == len(units))

    # Per-condition curve counts, derived from the raw curves actually present.
    per_cond = fig_stats["Fig3"]["per_condition"]
    source_to_condition = {
        u["source"]: u["condition"]["key"]
        for u in units if u.get("condition") is not None
    }
    expected_per_cond = {}
    for it in analysis["indents"]:
        if not it.get("curve_raw"):
            continue
        key = source_to_condition.get(it["source"])
        if key is not None:
            expected_per_cond[key] = expected_per_cond.get(key, 0) + 1
    bad_cond = {
        k: (v, expected_per_cond.get(k, 0))
        for k, v in per_cond.items()
        if v != expected_per_cond.get(k, 0)
    }
    add("Data", "Curves per condition", "equal to raw curves in each condition",
        ", ".join(f"{k}:{v}" for k, v in per_cond.items()),
        not bad_cond)
    sample_counts = fig_stats["Fig3"]["per_condition_sample"]
    expected_sample_counts = {}
    for it in analysis["indents"]:
        if it.get("curve_raw"):
            s = str(it["sample"])
            expected_sample_counts[s] = expected_sample_counts.get(s, 0) + 1
    actual_samples = "/".join(str(sample_counts.get(str(s), 0))
                              for s in sorted({u["sample"] for u in units}, key=str))
    expected_samples = "/".join(str(expected_sample_counts.get(str(s), 0))
                                for s in sorted({u["sample"] for u in units}, key=str))
    add("Data", "Curves per sample (Fig3 total)", expected_samples,
        actual_samples, sum(sample_counts.values()) == expected_curves)

    # Numerical consistency: recomputed from indents vs file summary
    max_mean = 0.0
    max_sd = 0.0
    max_cov = 0.0
    for u in units:
        f = u["file"]
        for metric in ("E", "H"):
            max_mean = max(max_mean, abs(float(u[f"{metric}_mean"]) - float(f[f"{metric}_mean"])))
            if _finite(f.get(f"{metric}_sd")) and _finite(u[f"{metric}_sd"]):
                max_sd = max(max_sd, abs(float(u[f"{metric}_sd"]) - float(f[f"{metric}_sd"])))
            if _finite(f.get(f"{metric}_cov")) and _finite(u[f"{metric}_cov"]):
                max_cov = max(max_cov, abs(float(u[f"{metric}_cov"]) - float(f[f"{metric}_cov"])))
    add("Numerical", "Mean recalculation max error", "<= 1e-10 GPa",
        f"{max_mean:.3e} GPa", max_mean <= 1e-10)
    add("Numerical", "Sample SD recalculation max error", "<= 1e-10 GPa",
        f"{max_sd:.3e} GPa", max_sd <= 1e-10)
    add("Numerical", "COV recalculation max error", "<= 1e-10 percentage points",
        f"{max_cov:.3e}", max_cov <= 1e-10)

    # Paired direction
    lookup = {(u["point"], u["sample"]): u for u in units}
    max_pair = 0.0
    for p in analysis["paired"]:
        ua = lookup.get((p["point"], p["sample_a"]))
        ub = lookup.get((p["point"], p["sample_b"]))
        if ua is None or ub is None:
            continue
        for metric in ("E", "H"):
            expected = float(ub[f"{metric}_mean"]) - float(ua[f"{metric}_mean"])
            max_pair = max(max_pair, abs(expected - float(p[f"d{metric}"])))
    add("Numerical", "Paired delta direction", "1210 - 1100, max error <= 1e-10",
        f"{max_pair:.3e} GPa", max_pair <= 1e-10)

    # Missing values were not converted to zero
    missing = sum(1 for it in analysis["indents"]
                  for m in ("E", "H") if not _finite(it.get(m)))
    zeros_in_raw = sum(1 for u in units for m in ("E", "H")
                       for v in u[m] if float(v) == 0.0)
    add("Numerical", "Missing E/H values", f"0; values count {total_n} x 2",
        f"missing={missing}, zero-valued archived measurements={zeros_in_raw}",
        missing == 0)

    # Image geometry and vector structure (only for configured formats)
    main_stems = ["Fig1_mechanical_properties", "Fig2_paired_effects",
                  "Fig3_load_displacement_small_multiples",
                  "Fig4_EH_response_map", "Fig5_reproducibility",
                  "FigS1_quality_control", "merged_curves", "grouped_bar"]
    formats = analysis["_figure_manifest"].get("formats") or ["png"]
    add("Delivery", "Figure formats", "non-empty subset of png/pdf/svg",
        ", ".join(formats), bool(formats) and all(s in ("png", "pdf", "svg") for s in formats))
    by_stem = {p["stem"]: p for p in analysis["_figure_manifest"]["figures"]}
    for stem in main_stems:
        e = by_stem.get(stem)
        if not e:
            add("Visual", f"{stem} outputs", "configured formats", "missing", False)
            continue
        if "png" in formats:
            info = _image_png_info(e["paths"]["png"])
            width_ok = info.get("width") == 4205
            dpi = info.get("dpi_x")
            dpi_ok = dpi is not None and abs(float(dpi) - 600) <= 1.0
            add("Visual", f"{stem} PNG width", "4205 px (178 mm @ 600 dpi)",
                f"{info.get('width')} px", width_ok)
            add("Visual", f"{stem} PNG DPI", "600 dpi",
                f"{dpi:.1f} dpi" if dpi is not None else "missing", dpi_ok)
        for suffix in ("pdf", "svg"):
            if suffix not in formats:
                continue
            ok, note = _vector_check(e["paths"][suffix], suffix)
            add("Visual", f"{stem} {suffix.upper()}", "true vector output", note, ok)

    per_files = [p for p in analysis["_figure_manifest"]["figures"]
                 if p["stem"].startswith("per_file/")]
    add("Visual", "Per-file figure count", len(units), len(per_files),
        len(per_files) == len(units))
    if "png" in formats:
        for e in per_files:
            info = _image_png_info(e["paths"]["png"])
            if info.get("width") != 4205:
                add("Visual", f"{e['stem']} PNG width", "4205 px",
                    f"{info.get('width')} px", False)
                break
        else:
            add("Visual", "Per-file PNG width", "all 4205 px", "all 4205 px", True)
    for suffix in ("pdf", "svg"):
        if suffix not in formats:
            continue
        failures = [e["stem"] for e in per_files
                    if not _vector_check(e["paths"][suffix], suffix)[0]]
        add("Visual", f"Per-file {suffix.upper()}", "all vector files",
            f"{len(per_files) - len(failures)}/{len(per_files)} pass", not failures)

    # Captions / index
    captions = Path(analysis["_fig_dir"]) / "figure_captions.md"
    index = Path(analysis["_fig_dir"]) / "index.md"
    add("Delivery", "Figure captions", "exists, all eight main/supplementary figures",
        "present" if captions.is_file() else "missing",
        captions.is_file() and all(f"## {s}" in captions.read_text(encoding="utf-8")
                                   for s in main_stems))
    add("Delivery", "Figure index", "exists",
        "present" if index.is_file() else "missing",
        index.is_file())
    add("Reproducibility", "Numeric fingerprint", "recorded in manifest",
        analysis["_figure_manifest"].get("numeric_fingerprint", "missing"),
        bool(analysis["_figure_manifest"].get("numeric_fingerprint")))
    return checks


def _numeric_fingerprint(analysis, units):
    payload = {
        "files": [{
            "source": u["source"], "sample": str(u["sample"]), "point": u["point"],
            "group": u["group"], "n_E": u["n_E"], "n_H": u["n_H"],
            "E_mean": u["E_mean"], "E_sd": u["E_sd"],
            "H_mean": u["H_mean"], "H_sd": u["H_sd"],
        } for u in units],
        "paired": [{
            "point": p["point"], "sample_a": str(p["sample_a"]),
            "sample_b": str(p["sample_b"]), "dE": p["dE"], "dH": p["dH"],
        } for p in analysis["paired"]],
        "paired_stats": analysis.get("paired_stats", {}),
        "groups": analysis.get("groups", {}),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str,
                     separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _write_captions(analysis, units, out_dir):
    conditions = condition_table(analysis)
    total_n = sum(int(f["n"]) for f in analysis["files"])
    paired = analysis.get("paired", [])
    st_e = analysis.get("paired_stats", {}).get("E", {})
    st_h = analysis.get("paired_stats", {}).get("H", {})
    stable_e = analysis.get("paired_stats", {}).get("stable_E", {})
    stable_h = analysis.get("paired_stats", {}).get("stable_H", {})
    n_stable = analysis.get("paired_stats", {}).get("stable_n")
    energies = "–".join([_fmt_energy(c["value"]) for c in (conditions[:1] + conditions[-1:])])
    samples = sample_order(analysis, analysis["cfg"])
    sample_zh = "样品 " + "、".join(str(s) for s in samples) if samples else "样品"
    sample_en = ", ".join(str(s) for s in samples)
    n_vals = [int(f["n"]) for f in analysis["files"] if int(f["n"]) > 0]
    n_lo, n_hi = (min(n_vals), max(n_vals)) if n_vals else (0, 0)
    n_zh = f"{n_lo}" if n_lo == n_hi else f"{n_lo}–{n_hi}"
    n_en = "six" if n_lo == n_hi == 6 else (f"{n_lo}" if n_lo == n_hi else f"{n_lo}–{n_hi}")
    pair_a, pair_b = _pair_samples(analysis, analysis["cfg"])
    text = rf"""# Figure captions / 图注

## Fig1_mechanical_properties

**中文图注.** 不同能量密度下（{energies} J cm⁻²，共 {len(conditions)} 个条件）{sample_zh} 的弹性模量与硬度。浅色符号为单次压痕，大符号和误差棒为文件内均值 ± 样本标准差（每个文件 $n$ = {n_zh} 次压痕）；同一样品的数据点以细线连接。横轴主刻度标注代表性条件，次刻度保留全部实测条件。图中不包含平滑或拟合曲线。

**English caption.** Modulus and hardness of samples {sample_en} as a function of laser fluence ({energies} J cm⁻²; {len(conditions)} conditions). Faint symbols are individual indents; large symbols and error bars show the within-file mean ± sample standard deviation ($n$ = {n_en} indents per file). Thin lines connect the same sample across measured fluences. Major x-ticks label representative conditions and minor ticks retain all measured levels. No smoothing or fitted trend is shown.

## Fig2_paired_effects

**中文图注.** 相同能量密度/测点下样品 {pair_b} 相对 {pair_a} 的配对差值，$\Delta = {pair_b} - {pair_a}$；共 {len(paired)} 对。虚线为零差值，点划线为所有配对的均值。灰色背景表示低硬度响应条件，圆形和方形分别表示高、低硬度响应。横轴主刻度标注代表性条件，次刻度保留全部实测条件。双尾精确符号翻转置换检验：$\Delta E$ {_fmt_p(st_e.get('p'))}（$n$ = {st_e.get('n', 'n.a.')}），$\Delta H$ {_fmt_p(st_h.get('p'))}（$n$ = {st_h.get('n', 'n.a.')}）。高硬度子集：$\Delta E$ {_fmt_p(stable_e.get('p'))}、$\Delta H$ {_fmt_p(stable_h.get('p'))}（$n$ = {n_stable or 'n.a.'}）。$p>0.05$ 仅表示本数据中未检出统计学差异，不代表两组样品完全相同。

**English caption.** Paired differences between samples at matched fluence/indentation points ($\Delta = {pair_b} - {pair_a}$; {len(paired)} pairs). The dashed line marks zero difference and the dash-dot line marks the mean difference. The shaded band indicates low-hardness-response conditions; circles and squares indicate high- and low-hardness responses. Major x-ticks label representative conditions and minor ticks retain every measured level. Two-sided exact sign-flip permutation tests: $\Delta E$ {_fmt_p(st_e.get('p'))} ($n$ = {st_e.get('n', 'n.a.')}), $\Delta H$ {_fmt_p(st_h.get('p'))} ($n$ = {st_h.get('n', 'n.a.')}). High-hardness subset: $\Delta E$ {_fmt_p(stable_e.get('p'))}, $\Delta H$ {_fmt_p(stable_h.get('p'))} ($n$ = {n_stable or 'n.a.'}). A non-significant result indicates no statistically detectable difference in this dataset, not proven equivalence.

## Fig3_load_displacement_small_multiples

**中文图注.** 按能量密度分面的原始载荷–位移曲线，共 {total_n} 条；每个条件包含{sample_zh} 各 {n_zh} 次压痕。曲线保留原始加载、保载和卸载轨迹，未平滑、未归一化、未按最大深度缩放。所有面板共享相同载荷范围；横轴按各条件的实际压入深度独立缩放，因此不同面板的横轴范围不可直接比较。

**English caption.** Original load–displacement curves shown as small multiples by fluence ({total_n} curves total; {n_en} indents per sample at every condition). Loading, hold and unloading traces are plotted as acquired without smoothing, normalisation or depth scaling. All panels share the same load axis; the displacement axis is independently scaled to the measured depth range in each condition and should not be compared across panels.

## Fig4_EH_response_map

**中文图注.** 单次压痕的模量–硬度响应图。颜色表示 {len(conditions)} 个实测能量密度等级，按能量由低到高由冷蓝过渡到暖红；底部色条按测量顺序列出各等级（等级之间为离散分类，不是连续插值）；不同符号表示 {len(samples)} 个样品，白色叉号为文件级均值。全图包含 {total_n} 次压痕；(b) 为高硬度响应区域的局部放大，仅改变显示范围，不增加或删除数据。高/低硬度响应由文件平均硬度的最大间隙确定，不构成材料相归属。

**English caption.** Modulus–hardness map of individual indents. Colour encodes the {len(conditions)} measured fluence levels on an ordered cold-blue to warm-red scale; the bottom key lists these classes in measurement order and represents discrete categories rather than a continuous interpolation. Markers denote the {len(samples)} samples, and white crosses are file-level means. Panel (a) contains all {total_n} indents; panel (b) zooms the high-hardness response region without adding or removing data. Response groups are defined by the largest gap in file-mean hardness and do not constitute a phase assignment.

## Fig5_reproducibility

**中文图注.** 每个文件内 {n_zh} 次压痕的批内变异系数（COV = 样本标准差/均值 × 100%）。不同符号表示 {len(samples)} 个样品，横轴为能量密度；主刻度标注代表性条件，次刻度保留全部实测条件。5% 虚线为配置中的曲线合并判据；10% 点线仅作为数据质量提示，二者含义不同。低硬度响应条件的高 COV 完整保留，未截断。

**English caption.** Within-file coefficient of variation (COV = sample standard deviation/mean × 100%) calculated from {n_en} indents per file. Markers denote the {len(samples)} samples; the x-axis is fluence, with major ticks at representative conditions and minor ticks at every measured level. The 5% dashed line is the configured curve-merge criterion, whereas the 10% dotted line is a data-quality warning band; they are distinct criteria. High COV values in the low-hardness response are retained without truncation.

## FigS1_quality_control

**中文图注.** 数据质控补充图：(a) 最大载荷相对 {analysis['cfg']['参考载荷_mN']:g} mN 标称载荷的偏差，虚线为 {analysis['cfg']['载荷容差_pct']:g}% 容差；(b) 仪器漂移修正绝对值的均值，虚线为 {analysis['cfg']['漂移上限_nm_s']:g} nm s⁻¹ 上限；(c) 每个文件的有效压痕数，虚线为最少 3 次的要求。每个符号代表一个文件；红边空心符号为超限项。横轴主刻度标注代表性条件，次刻度保留全部实测条件。阈值与报告中的质控判定一致。

**English caption.** Quality-control supplementary figure: (a) maximum-load deviation from the {analysis['cfg']['参考载荷_mN']:g} mN nominal load (dashed line: {analysis['cfg']['载荷容差_pct']:g}% tolerance); (b) mean absolute instrument drift correction (dashed line: {analysis['cfg']['漂移上限_nm_s']:g} nm s⁻¹ limit); (c) number of valid indents per file (dashed line: minimum of 3). Each symbol is one file; open red-edged symbols exceed the corresponding limit. Major x-ticks label representative conditions and minor ticks retain every measured level. Thresholds match the quality-control rules used in the report.

## merged_curves

**中文图注.** 全部 {total_n} 条原始载荷–位移曲线的合并总览。不同线型表示{sample_zh}；颜色表示 {len(conditions)} 个实测能量等级，按能量由低到高由冷蓝过渡到暖红，底部色条按实测顺序排列，等级之间为离散分类而非连续插值。曲线保留原始加载、保载和卸载轨迹，未平滑、未归一化。该图用于观察整体深度范围与曲线重叠情况，逐条件定量比较应以 Fig3 小倍数图为准。

**English caption.** Overlay of all {total_n} original load–displacement curves. Line styles denote the {len(samples)} samples ({sample_en}); colour denotes the {len(conditions)} measured fluence classes on an ordered cold-blue to warm-red scale, shown as discrete blocks in measurement order rather than a continuous interpolation. Loading, hold and unloading traces are plotted as acquired without smoothing or normalisation. This view is intended for the overall depth range and curve overlap; condition-wise comparison should use the Fig3 small multiples.

## grouped_bar

**中文图注.** 按实测能量条件分组的文件级均值柱状图；每个能量条件包含 {len(samples)} 个样品各一个文件。柱高为 {len(units)} 个文件的均值，误差棒为文件内 {n_zh} 次压痕的样本标准差，误差棒上端数值为文件均值，黑色小点为对应的单次压痕。同一能量条件下各样品使用完全相同的柱色，仅以填充纹理区分样品；柱的色相按能量由低到高由冷蓝过渡到暖红。低硬度响应文件的 SD 较大，必须与高硬度响应分开解读。横轴为等间距分类，标签给出真实能量密度；分类间距不代表能量数值比例。

**English caption.** File-level mean bars grouped by measured fluence (one file per sample at each condition; {len(units)} files). Bar height and the numeric label above each error-bar cap give the within-file mean; error bars are sample standard deviations from the indents in each file, and small black dots are the individual indents. All samples at the same fluence share exactly the same bar colour and are distinguished only by the fill pattern. Bar hue follows the measured fluence from cold blue to warm red. The low-hardness-response files have much larger SD and must not be interpreted as a single material constant. The x-axis is categorical and equally spaced; labels give the actual fluence, so spacing does not represent numerical ratios.

## Per-file figures / 单文件补充图

**中文图注.** 每个源文件的三联图：原始载荷–位移曲线、逐测试序号的模量及逐测试序号的硬度。后两栏中的短横线为 {n_zh} 次压痕均值，阴影为 ± 样本标准差；横轴为真实测试序号。缺失测量不绘制，也不以 0 代替。标题给出样品编号、能量密度和源文件名。

**English caption.** Three-panel figure for each source file: original load–displacement curves, per-test-number modulus and per-test-number hardness. In the latter two panels, the dashed line is the mean of the indents in that file and the shaded band is ± sample standard deviation; the x-axis gives the actual test number. Missing measurements are omitted, never replaced by zero. The title gives sample, fluence and source file name.
"""
    (Path(out_dir) / "figure_captions.md").write_text(text, encoding="utf-8")


def _write_index(manifest, out_dir):
    main = [p for p in manifest["figures"] if not p["stem"].startswith("per_file/")]
    per_file = [p for p in manifest["figures"] if p["stem"].startswith("per_file/")]
    formats = manifest.get("formats") or ["png"]
    label = {"png": "600 dpi PNG", "pdf": "矢量 PDF", "svg": "矢量 SVG"}
    lines = [
        "# 论文级图像索引",
        "",
        "当前输出格式：" + "、".join(label.get(s, s.upper()) for s in formats)
        + "。图内文字为中文，完整中英文图注见 "
        "[`figure_captions.md`](figure_captions.md)。",
        "",
        "## 主图、补充图与保留图",
        "",
    ]
    for p in main:
        links = " | ".join(
            f"[{s.upper()}]({Path(p['paths'][s]).name})" for s in formats
            if s in p["paths"]
        )
        lines.append(f"- **{p['stem']}** — {links}")
    lines += ["", "## 单文件补充图", ""]
    for p in per_file:
        links = " | ".join(
            f"[{s.upper()}]({Path(p['paths'][s]).relative_to(out_dir).as_posix()})"
            for s in formats if s in p["paths"]
        )
        stem = Path(p["paths"].get("png") or next(iter(p["paths"].values()))).stem
        lines.append(f"- `{stem}` — {links}")
    lines += [
        "",
        "## 验收与元数据",
        "",
        "- [自动核验记录](figure_validation.md)",
        "- [机器可读清单](figure_manifest.json)",
        "",
    ]
    (Path(out_dir) / "index.md").write_text("\n".join(lines), encoding="utf-8")


def _write_validation(analysis, units, fig_stats, out_dir):
    checks = _why_checks(analysis, units, fig_stats)
    manifest = analysis["_figure_manifest"]
    passed = sum(1 for c in checks if c["status"] == "Pass")
    failed = sum(1 for c in checks if c["status"] == "Fail")
    lines = [
        "# 图像重设计自动核验记录",
        "",
        f"- 数据规模：{len(analysis['files'])} 个文件 / "
        f"{sum(int(f['n']) for f in analysis['files'])} 次有效压痕",
        f"- 数值指纹（SHA-256）：`{manifest['numeric_fingerprint']}`",
        f"- 检查项：{len(checks)}；Pass：{passed}；Fail：{failed}",
        "",
        "| 类别 | 检查项 | 预期 | 实际 | 结果 | 备注 |",
        "|---|---|---|---|---|---|",
    ]
    for c in checks:
        lines.append(
            f"| {c['category']} | {c['item']} | {c['expected']} | {c['actual']} | "
            f"{c['status']} | {c['note']} |"
        )
    lines += [
        "",
        "## 人工视觉检查",
        "",
        "请在最终出版尺寸下复核以下项目；自动检查不能代替视觉审稿：",
        "",
        "- [ ] 100% 查看时坐标、图例、误差棒与 marker 清晰，无遮挡与裁切。",
        "- [ ] 转为灰度后，1100 与 1210 仍可凭 marker 和线型区分。",
        "- [ ] Fig3 各面板的横轴范围标注清楚，纵轴范围一致。",
        "- [ ] Fig4 的高硬度局部放大未造成误导，颜色标尺全局一致。",
        "- [ ] 图注中的 $n$、误差棒定义、统计检验和坐标缩放说明与图一致。",
        "",
        f"> 自动核验结论：{'全部通过' if failed == 0 else f'{failed} 项未通过'}。",
        "",
    ]
    (Path(out_dir) / "figure_validation.md").write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------
# Entry point used by nano_analysis.py
# --------------------------------------------------------------------------

def generate_publication_figures(analysis, cfg, out_dir):
    """Generate all figures and return one path per logical figure."""
    init_style(cfg)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"figures": [], "generated_by": "nano_plots.py",
                "dpi": DPI, "width_mm": DOUBLE_COLUMN_MM,
                "formats": list(OUTPUT_FORMATS)}
    analysis["_figure_manifest"] = manifest
    analysis["_fig_dir"] = str(out_dir)

    conditions = condition_table(analysis)
    units = build_file_units(analysis, conditions)
    manifest["numeric_fingerprint"] = _numeric_fingerprint(analysis, units)

    stats = {}
    stats["Fig1"] = plot_mechanical_properties(analysis, cfg, out_dir, manifest)
    stats["Fig2"] = plot_paired_effects(analysis, cfg, out_dir, manifest)
    stats["Fig3"] = plot_load_displacement(analysis, cfg, out_dir, manifest)
    stats["Fig4"] = plot_eh_response_map(analysis, cfg, out_dir, manifest)
    stats["Fig5"] = plot_reproducibility(analysis, cfg, out_dir, manifest)
    stats["FigS1"] = plot_quality_control(analysis, cfg, out_dir, manifest)
    stats["merged_curves"] = plot_merged_curves(analysis, cfg, out_dir, manifest)
    stats["grouped_bar"] = plot_grouped_bar(analysis, cfg, out_dir, manifest)
    stats["per_file"] = plot_per_file(analysis, cfg, out_dir, manifest)

    manifest["stats"] = stats
    manifest["conditions"] = [
        {"label": c["label"], "value": c["value"], "numeric": c["numeric"]}
        for c in conditions
    ]
    _write_captions(analysis, units, out_dir)
    _write_index(manifest, out_dir)
    _write_validation(analysis, units, stats, out_dir)
    (out_dir / "figure_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8"
    )

    outputs = [p["paths"][OUTPUT_FORMATS[0]] for p in manifest["figures"]]
    return [Path(p) for p in outputs]

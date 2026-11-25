# -*- coding: utf-8 -*-
"""Raman 分层批次展示编排入口。

把 config / data / plots 三层组合成一次完整运行：三张总览、逐点与重复性、
同点跨组配对、逐基准定量指标、五张追溯表和静态索引。定量一律通过
raman_compare.compare_sample，不另写峰匹配或相似度算法。
"""
from __future__ import annotations

import html
import json
import math
import os
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from plot_raman import prepare_out, save_figure, DPI, PX_W, FONT, FS, CJK_FONT
from compare_report import _write_csv, render_shift_plot
from raman_compare import (SpectrumError, SampleResult, detect_spectrum,
                           compare_sample, comparison_records)

from batch_display_config import (CompareConfigError, BatchConfig, discover,
                                  load_batch_config, sha256_file, relative_name)
from batch_display_data import (load_scan, load_reference, make_spectrum,
                                group_scans, common_window, representative,
                                point_status, valid_spectra)
import batch_display_plots as plots

MANIFEST_COLUMNS = ["group", "point", "scan", "role", "file", "label",
                    "photo", "enabled", "status", "message", "sha256"]
SCAN_COLUMNS = ["reference_id", "group", "point", "scan", "file", "label",
                "spectrum_kind", "xmin_cm-1", "xmax_cm-1", "points",
                "threshold_counts", "reference_peaks", "matched_peaks",
                "missing_peaks", "new_peaks", "match_rate",
                "mean_delta_cm-1", "mae_delta_cm-1", "max_abs_delta_cm-1",
                "rmse_delta_cm-1", "pearson_r", "status", "message"]
POINT_COLUMNS = ["reference_id", "group", "point", "expected_scans",
                 "valid_scans", "failed_scans", "missing_scans", "data_status",
                 "spectrum_kind", "n_effective", "mean_match_rate",
                 "mean_pearson", "status"]
PAIR_COLUMNS = ["point", "left_group", "right_group", "left_valid_scans",
                "right_valid_scans", "both_valid", "xmin_cm-1", "xmax_cm-1",
                "spectrum_kind", "image", "status", "message"]
PEAK_EXTRA = ["group", "point", "scan", "reference_id", "spectrum_kind"]
PEAK_FIELDS = ["file", "label", "reference_peak_id", "reference_peak_cm-1",
               "sample_peak_cm-1", "status", "delta_cm-1", "abs_delta_cm-1",
               "tolerance_cm-1", "reference_H_counts", "sample_H_counts",
               "H_ratio", "reference_H_norm", "sample_H_norm", "H_norm_ratio"]


def _guard_output(src, out, config_path):
    """清空输出前确认配置和所有输入都不在待清空目录内。"""
    out_real = os.path.realpath(str(out))
    src_real = os.path.realpath(str(src))
    if src_real == out_real or src_real.startswith(out_real + os.sep):
        raise CompareConfigError("输入目录位于将被清空的输出目录内：%s" % src)
    if config_path:
        cfg = os.path.realpath(str(config_path))
        if cfg == out_real or cfg.startswith(out_real + os.sep):
            raise CompareConfigError("配置表位于将被清空的输出目录内：%s" % config_path)


def _value_or_none(series):
    finite = [float(v) for v in series if v is not None and math.isfinite(float(v))]
    return sum(finite) / len(finite) if finite else None


def _render_metric_bar(labels, values, title, ylabel, out_path):
    """点级指标条形图；无值处留空，不补 0。"""
    fig = plt.figure(figsize=(PX_W / float(DPI), 768 / float(DPI)),
                     dpi=DPI, facecolor="white")
    ax = fig.add_axes((0.09, 0.17, 0.88, 0.73))
    indices = [i for i, value in enumerate(values) if value is not None]
    bars = [value for value in values if value is not None]
    if bars:
        ax.bar(indices, bars, color="#315B50", width=0.70)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right",
                       fontsize=max(6, FS * 0.62))
    cjk = CJK_FONT or FONT
    ax.set_xlabel("point", fontsize=FS, fontfamily=FONT)
    ax.set_ylabel(ylabel, fontsize=FS, fontproperties=cjk)
    ax.set_title(title, fontsize=FS + 1, fontproperties=cjk)
    ax.grid(axis="y", alpha=0.25)
    for spine in ax.spines.values():
        spine.set_linewidth(1.0)
    save_figure(fig, str(out_path), compress_level=6)
    plt.close(fig)


def _relative_image(out, path):
    try:
        return relative_name(out, Path(path))
    except Exception:
        return os.path.basename(str(path))


def _html_link(href, text):
    return "href="" + html.escape(href, quote=True) + """


def build_index(out, overviews, contacts, points, repeats, paired, metrics,
                tables, warnings, failures):
    """生成离线可开的静态索引；所有链接均指向实际产物。"""
    lines = []
    lines.append("<!doctype html>")
    lines.append('<html lang="zh-CN"><head><meta charset="utf-8">')
    lines.append("<title>Raman 分层批次展示</title>")
    lines.append("<style>body{font-family:sans-serif;margin:24px;color:#222}")
    lines.append("h1{font-size:22px}h2{font-size:18px;margin-top:28px}")
    lines.append(".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px}")
    lines.append("a{color:#315B50}img{width:100%;border:1px solid #ccc}")
    lines.append(".warn{color:#a00}small{color:#666}</style></head><body>")
    lines.append("<h1>Raman 分层批次展示</h1>")
    lines.append("<p><small>首页三图定位 -> 点位页核查三次扫描 -> 同点页比较 1100/1210 -> "
                 "指标图进入逐基准峰级明细。显示归一化仅用于观察，不代表浓度。</small></p>")
    if warnings or failures:
        lines.append("<h2>状态</h2><ul>")
        for text in warnings + failures:
            lines.append('<li class="warn">' + html.escape(str(text)) + '</li>')
        lines.append("</ul>")
    for title, items in (("总览", overviews), ("分组总览", contacts),
                         ("点位详情", points), ("同点重复性", repeats),
                         ("同点跨组", paired), ("基准指标", metrics)):
        if not items:
            continue
        lines.append('<h2>' + html.escape(title) + '</h2><div class="grid">')
        for label, href in items:
            lines.append("<div><a " + _html_link(href, label) + ">" +
                         html.escape(label) + "</a></div>")
        lines.append("</div>")
    lines.append("<h2>数值表</h2><ul>")
    for label, href in tables:
        lines.append("<li><a " + _html_link(href, label) + ">" +
                     html.escape(label) + "</a></li>")
    lines.append("</ul></body></html>")
    path = Path(out) / "index.html"
    path.write_text(chr(10).join(lines), encoding="utf-8")
    return path


def run_batch_display(config_path, src, out, xmin=None, xmax=None):
    """批次展示主入口，返回 0 / 2 / 1。"""
    config_path = str(config_path).strip() if config_path else ""
    if config_path:
        if not os.path.isfile(config_path):
            raise CompareConfigError("批次配置不存在：%s" % config_path)
        config = load_batch_config(config_path, src)
    else:
        if not os.path.isdir(src):
            raise CompareConfigError("输入目录不存在：%s" % src)
        info = discover(src, expected_points=20, scans_per_point=3)
        groups = sorted(info["groups"])
        # 自动发现没有 Excel 参数表，pair_groups 需按数据组补默认值，
        # 否则跨组同点配对整段会被跳过（与 load_batch_config 口径一致）。
        pair_groups = (groups[0], groups[1]) if len(groups) >= 2 else None
        config = BatchConfig(
            path=None, src=info["src"], scans=info["scans"],
            references=info["references"], groups=info["groups"],
            warnings=info["warnings"], expected_points=20, scans_per_point=3,
            pair_groups=pair_groups)
    if xmin is not None or xmax is not None:
        if (xmin is None) != (xmax is None):
            raise CompareConfigError("区间下限和区间上限必须同时填写")
        if xmin >= xmax:
            raise CompareConfigError("区间下限必须小于区间上限")
        config.xmin, config.xmax = float(xmin), float(xmax)

    _guard_output(config.src, out, config_path)
    warnings = list(config.warnings)
    failures = []
    if not config.scans:
        raise CompareConfigError("没有可展示的光谱扫描")

    grouped_all = group_scans(config)
    loaded_refs = {}
    for ref in config.active_refs:
        try:
            loaded_refs[ref.ref_id] = load_reference(ref)
        except Exception as exc:
            failures.append("基准 %s 读取失败：%s" % (ref.ref_id, exc))

    spectra = [item.spectrum for items in grouped_all.values()
               for item in items if item.status == "ok" and item.spectrum is not None]
    spectra += list(loaded_refs.values())
    try:
        lo, hi = common_window(config, spectra, require_at_least=2)
    except SpectrumError as exc:
        raise CompareConfigError("无法确定共同分析窗口：%s" % exc) from None
    config._batch_lo, config._batch_hi = lo, hi

    reference_spectra = [(ref, loaded_refs[ref.ref_id])
                         for ref in config.active_refs if ref.ref_id in loaded_refs]
    if not reference_spectra:
        raise CompareConfigError("没有可用的基准谱，无法生成基准指标")

    # 每个点的显示代表谱与数据状态先算好，绘图和指标共用同一口径。
    point_reps, point_states = {}, {}
    for key, items in grouped_all.items():
        point_reps[key] = representative(config, items, lo, hi)
        point_states[key] = point_status(items)
        if point_states[key]["status"] == "failed":
            failures.append("%s P%02d 无有效扫描" % key)

    prepare_out(str(out))
    out_dir = Path(out)
    for sub in ("points", "repeats", "paired", "compare_points", "metrics", "tables"):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)

    overviews, contacts, points_links, repeats_links, paired_links = [], [], [], [], []
    metrics_links, manifest_rows = [], []

    # ---- 总览：基准 + 每组 4x5 ----
    overview_path = out_dir / "00_reference_overview.png"
    plots.render_reference_overview(reference_spectra, lo, hi, config, overview_path)
    overviews.append(("基准总览（%d 条）" % len(reference_spectra),
                      _relative_image(out_dir, overview_path)))
    for index, group in enumerate(config.groups, 1):
        gpoints = [(point, items, point_reps[(group, point)])
                   for (g, point), items in grouped_all.items() if g == group]
        if not gpoints:
            continue
        path = out_dir / ("%02d_%s_contact.png" % (index, group))
        plots.render_contact(group, gpoints, lo, hi, config, path)
        contacts.append(("%s 组总览（%d 点）" % (group, len(gpoints)),
                         _relative_image(out_dir, path)))

    # ---- 清单：基准与逐扫描 ----
    for ref in config.active_refs:
        path = loaded_refs.get(ref.ref_id)
        manifest_rows.append({
            "group": "reference", "point": "", "scan": "", "role": "reference",
            "file": relative_name(config.src, path.path) if path else ref.file,
            "label": ref.display_label, "photo": "", "enabled": int(ref.enabled),
            "status": "ok" if path else "failed",
            "message": "" if path else "读取失败",
            "sha256": sha256_file(path.path) if path else "",
        })
    for scan in config.scans:
        item = next((it for items in grouped_all.values() for it in items
                     if it.scan.key == scan.key), load_scan(scan))
        manifest_rows.append({
            "group": scan.group, "point": scan.point, "scan": scan.scan,
            "role": "scan", "file": relative_name(config.src, scan.path),
            "label": scan.display_label, "photo": scan.photo,
            "enabled": int(scan.enabled), "status": item.status,
            "message": item.message,
            "sha256": sha256_file(scan.path) if scan.status == "ok" else "",
        })
    _write_csv(out_dir / "batch_manifest.csv", MANIFEST_COLUMNS, manifest_rows)

    # ---- 逐点详情 / 重复性 ----
    for (group, point), items in grouped_all.items():
        rep = point_reps[(group, point)]
        photo = ""
        for item in items:
            if item.scan.photo:
                candidate = os.path.join(str(config.src), item.scan.photo)
                if os.path.isfile(candidate):
                    photo = candidate
                    break
        point_path = out_dir / "points" / group / ("P%02d.png" % point)
        point_path.parent.mkdir(parents=True, exist_ok=True)
        plots.render_point(items, rep, photo, lo, hi, config, point_path)
        label = "%s P%02d（%s）" % (group, point,
                                    point_states[(group, point)]["status"])
        points_links.append((label, _relative_image(out_dir, point_path)))
        if valid_spectra(items):
            repeat_path = out_dir / "repeats" / group / ("P%02d_repeat.png" % point)
            repeat_path.parent.mkdir(parents=True, exist_ok=True)
            plots.render_repeat(items, rep, lo, hi, config, repeat_path)
            repeats_links.append(("%s P%02d 重复性" % (group, point),
                                  _relative_image(out_dir, repeat_path)))

    # ---- 同点跨组配对 ----
    if config.enable_paired and config.pair_groups:
        group_a, group_b = config.pair_groups
        paired_points = sorted({p for (g, p) in grouped_all if g in (group_a, group_b)})
        pair_rows = []
        for point in paired_points:
            left = grouped_all.get((group_a, point), [])
            right = grouped_all.get((group_b, point), [])
            rep_a = point_reps.get((group_a, point))
            rep_b = point_reps.get((group_b, point))
            both = bool(valid_spectra(left)) and bool(valid_spectra(right))
            image = ""
            if both:
                path = out_dir / "paired" / ("P%02d_%s_vs_%s.png"
                                              % (point, group_a, group_b))
                plots.render_paired(left, rep_a, right, rep_b, lo, hi, config, path,
                                    left_label=group_a, right_label=group_b)
                image = _relative_image(out_dir, path)
                paired_links.append(("P%02d %s vs %s" % (point, group_a, group_b), image))
            pair_rows.append({
                "point": point, "left_group": group_a, "right_group": group_b,
                "left_valid_scans": len(valid_spectra(left)),
                "right_valid_scans": len(valid_spectra(right)),
                "both_valid": int(both), "xmin_cm-1": lo, "xmax_cm-1": hi,
                "spectrum_kind": (rep_a or {}).get("kind", ""),
                "image": image, "status": "ok" if both else "partial",
                "message": "" if both else "任一侧无有效扫描，未绘制差值",
            })
        _write_csv(out_dir / "tables" / "pair_summary.csv", PAIR_COLUMNS, pair_rows)

    # ---- 定量：逐基准 × 组 × 点 × 扫描 ----
    scan_rows, point_rows, peak_rows = [], [], []
    for ref in config.active_refs:
        detection = None
        try:
            detection = detect_spectrum(
                loaded_refs[ref.ref_id], lo, hi, shift=config.shift,
                threshold=ref.height, zero_guard=config.zero_guard, prefix="R")
        except Exception as exc:
            failures.append("基准 %s 峰检测失败：%s" % (ref.ref_id, exc))
        ref_dir = out_dir / "metrics" / ref.ref_id
        if detection is not None:
            ref_dir.mkdir(parents=True, exist_ok=True)
        results = []
        for (group, point), items in grouped_all.items():
            state = point_states[(group, point)]
            kind = point_reps[(group, point)]["kind"]
            point_results = []
            for item in items:
                sample_input = item.spectrum if item.spectrum is not None else make_spectrum(item.scan)
                result = None
                if detection is not None and item.status == "ok":
                    try:
                        sample_detection = detect_spectrum(
                            item.spectrum, lo, hi, shift=config.shift,
                            threshold=item.scan.height, zero_guard=config.zero_guard,
                            prefix="S")
                        result = compare_sample(detection, sample_detection, config.tolerance)
                    except Exception as exc:
                        result = SampleResult.failed(sample_input, str(exc))
                if result is None:
                    result = SampleResult.failed(sample_input, item.message or item.status)
                result._batch_group = group
                result._batch_point = point
                result._batch_scan = item.scan.scan
                result._batch_kind = kind
                results.append(result)
                if result.status == "success":
                    point_results.append(result)
                    metric = result.metric
                    scan_rows.append({
                        "reference_id": ref.ref_id, "group": group, "point": point,
                        "scan": item.scan.scan, "file": str(item.scan.path),
                        "label": item.scan.display_label, "spectrum_kind": kind,
                        "xmin_cm-1": result.xmin, "xmax_cm-1": result.xmax,
                        "points": result.points,
                        "threshold_counts": result.detection.threshold if result.detection else None,
                        "reference_peaks": len(detection.peaks),
                        "matched_peaks": result.matched, "missing_peaks": result.missing,
                        "new_peaks": result.new, "match_rate": result.match_rate,
                        "mean_delta_cm-1": metric.get("mean_delta_cm-1"),
                        "mae_delta_cm-1": metric.get("mae_delta_cm-1"),
                        "max_abs_delta_cm-1": metric.get("max_abs_delta_cm-1"),
                        "rmse_delta_cm-1": metric.get("rmse_delta_cm-1"),
                        "pearson_r": result.pearson_r, "status": result.status,
                        "message": result.message,
                    })
                    for row in comparison_records(result):
                        enriched = dict(row)
                        enriched.update({"group": group, "point": point,
                                         "scan": item.scan.scan,
                                         "reference_id": ref.ref_id,
                                         "spectrum_kind": kind})
                        peak_rows.append(enriched)
            point_rows.append({
                "reference_id": ref.ref_id, "group": group, "point": point,
                "expected_scans": state["expected_scans"],
                "valid_scans": state["valid_scans"],
                "failed_scans": state["failed_scans"],
                "missing_scans": state["missing_scans"],
                "data_status": state["status"], "spectrum_kind": kind,
                "n_effective": len(point_results),
                "mean_match_rate": _value_or_none([r.match_rate for r in point_results]),
                "mean_pearson": _value_or_none([r.pearson_r for r in point_results]),
                "status": "ok" if point_results else "failed",
            })
        if detection is None:
            continue
        shift_path = ref_dir / "peak_shift.png"
        if render_shift_plot(results, config.tolerance, shift_path):
            metrics_links.append(("%s 峰位偏差" % ref.display_label,
                                  _relative_image(out_dir, shift_path)))
        for metric_key, title, ylabel, file_name in (
                ("pearson_r", "Pearson r（全谱）", "Pearson r", "pearson.png"),
                ("match_rate", "匹配率（逐基准峰）", "match_rate", "match_rate.png")):
            labels, values = [], []
            for (group, point), items in grouped_all.items():
                subset = [r for r in results
                          if getattr(r, "_batch_group", None) == group
                          and getattr(r, "_batch_point", None) == point
                          and r.status == "success"]
                labels.append("P%02d" % point if len(config.groups) == 1
                              else "%s-P%02d" % (group, point))
                values.append(_value_or_none([getattr(r, metric_key, None)
                                              for r in subset]))
            path = ref_dir / file_name
            _render_metric_bar(labels, values, "%s：%s" % (ref.display_label, title),
                               ylabel, path)
            metrics_links.append(("%s %s" % (ref.display_label, title),
                                  _relative_image(out_dir, path)))
        # 每个基准的一张可下钻对比图，复用现有 compare_report 图面但落到点级目录。
        for point in sorted({p for (_g, p) in grouped_all}):
            group = config.groups[0]
            subset = [r for r in results if getattr(r, "_batch_group", None) == group
                      and getattr(r, "_batch_point", None) == point and r.status == "success"]
            if not subset:
                continue
            path = out_dir / "compare_points" / ref.ref_id / group / ("P%02d.png" % point)
            path.parent.mkdir(parents=True, exist_ok=True)
            render_shift_plot(subset, config.tolerance, path)

    _write_csv(out_dir / "tables" / "scan_metrics.csv", SCAN_COLUMNS, scan_rows)
    _write_csv(out_dir / "tables" / "point_summary.csv", POINT_COLUMNS, point_rows)
    _write_csv(out_dir / "tables" / "peak_matches.csv", PEAK_EXTRA + PEAK_FIELDS, peak_rows)

    tables = [("batch_manifest.csv", "batch_manifest.csv"),
              ("scan_metrics.csv", "tables/scan_metrics.csv"),
              ("point_summary.csv", "tables/point_summary.csv"),
              ("peak_matches.csv", "tables/peak_matches.csv")]
    if config.enable_paired:
        tables.append(("pair_summary.csv", "tables/pair_summary.csv"))
    build_index(out_dir, overviews, contacts, points_links, repeats_links,
                paired_links, metrics_links, tables, warnings, failures)

    manifest = {
        "algorithm": "raman_compare.compare_sample",
        "config": str(config.path) if config.path else None,
        "src": str(config.src),
        "output": str(out_dir),
        "representative": config.representative,
        "normalize_display_only": config.normalize,
        "window": {"xmin": lo, "xmax": hi, "shift": config.shift},
        "tolerance": config.tolerance,
        "zero_guard": config.zero_guard,
        "groups": list(config.groups),
        "references": [{"ref_id": r.ref_id, "file": r.file,
                        "sha256": sha256_file(loaded_refs[r.ref_id].path)
                        if r.ref_id in loaded_refs else ""}
                       for r in config.active_refs],
        "counts": {"scans": len(config.scans), "points": len(grouped_all),
                   "active_references": len(config.active_refs),
                   "successful_scan_results": len(scan_rows)},
        "warnings": warnings,
        "failures": failures,
        "outputs": {
            "index": "index.html",
            "overviews": [href for _label, href in overviews],
            "contacts": [href for _label, href in contacts],
            "points": [href for _label, href in points_links],
            "repeats": [href for _label, href in repeats_links],
            "paired": [href for _label, href in paired_links],
            "metrics": [href for _label, href in metrics_links],
            "tables": [href for _label, href in tables],
        },
    }
    (out_dir / "batch_run.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print("batch display: %d scan(s), %d point(s), %d reference(s) -> %s"
          % (len(config.scans), len(grouped_all), len(config.active_refs), out_dir))
    for note in warnings:
        print("  提示：%s" % note)
    for note in failures:
        print("  FAILED：%s" % note)
    any_failure = bool(failures) or any(
        state["status"] != "complete" for state in point_states.values())
    if not scan_rows:
        return 1
    return 2 if any_failure else 0

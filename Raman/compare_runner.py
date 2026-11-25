# -*- coding: utf-8 -*-
"""Raman 基准对比模式的命令行执行与文件编排。"""
from __future__ import annotations

from datetime import datetime
import hashlib
import os
from pathlib import Path
import sys

import numpy as np

from compare_config import CompareConfigError, load_compare_config
from compare_report import (render_sample_plot, render_shift_plot,
                            write_manifest, write_reports)
from raman_compare import (SampleResult, SpectrumError, SpectrumInput,
                           analysis_window, compare_sample, detect_spectrum,
                           load_spectrum)


def sha256(path):
    path = Path(path)
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inside(path, folder):
    try:
        Path(path).resolve().relative_to(Path(folder).resolve())
        return True
    except ValueError:
        return False


def _validate_paths(config, samples, reference, out_dir):
    inputs = [config.path, reference, *[sample[1] for sample in samples]]
    for path in inputs:
        if _inside(path, out_dir):
            raise CompareConfigError(
                f"输入或配置位于将被清空的结果目录中：{path}")


def _sample_inputs(config, src):
    from plot_raman import resolve_src

    active = config.active_samples
    if not active:
        raise CompareConfigError("样品表中没有启用的样品")
    records = []
    seen_sources = set()
    seen_stems = set()
    from plot_raman import safe_stem
    for sample in active:
        path = Path(resolve_src(str(src), sample.file)).resolve()
        key = os.path.normcase(str(path))
        if key in seen_sources:
            raise CompareConfigError(f"启用的样品文件重复：{path}")
        seen_sources.add(key)
        stem = safe_stem(path.stem).casefold()
        if stem in seen_stems:
            raise CompareConfigError(f"样品输出文件名冲突：{stem}.png")
        seen_stems.add(stem)
        records.append((sample, path, stem))
    return records


def _failed_result(sample, path, message):
    return SampleResult.failed(
        SpectrumInput(path=path, label=sample.display_label,
                      x=np.zeros(0), y=np.zeros(0), height=sample.height,
                      peaks=sample.peaks, source_row=sample.source_row),
        message)


def _resolve_numeric(value):
    return None if value is None else float(value)


def _output_names(samples, results, include_shift):
    names = ["compare_reference_peaks.csv", "compare_peaks.csv",
             "compare_summary.csv", "compare_run.json"]
    names.extend(f"compare_{stem}.png" for _sample, _path, stem in samples
                 if any(result.sample.path == _path and result.status == "success"
                        and not result.image_error for result in results))
    if include_shift:
        names.append("compare_shift.png")
    return names


def run_compare(compare_path, src, out_dir, xmin=None, xmax=None):
    """执行一次对比；返回 CLI 退出码。"""
    from plot_raman import prepare_out

    config = load_compare_config(compare_path)
    if (xmin is None) != (xmax is None):
        raise CompareConfigError("--xmin 和 --xmax 必须同时提供")
    if xmin is not None and xmin >= xmax:
        raise CompareConfigError("--xmin 必须小于 --xmax")
    if xmin is not None:
        config.xmin, config.xmax = float(xmin), float(xmax)
    reference_path = config.reference_path()
    if not reference_path.is_file():
        raise CompareConfigError(f"基准文件不存在：{reference_path}")
    sample_records = _sample_inputs(config, src)
    out_dir = Path(out_dir).resolve()
    reference_path = reference_path.resolve()
    _validate_paths(config, sample_records, reference_path, out_dir)

    input_manifest = {
        "config": {"path": str(config.path), "sha256": sha256(config.path)},
        "reference": {"path": str(reference_path), "sha256": sha256(reference_path),
                      "role": "reference"},
        "samples": [{"path": str(path), "label": sample.display_label,
                     "sha256": sha256(path), "role": "sample",
                     "enabled": sample.enabled}
                    for sample, path, _stem in sample_records],
    }
    try:
        reference_xy = load_spectrum(reference_path)
    except SpectrumError as exc:
        raise CompareConfigError(f"基准谱无法读取：{exc}") from None
    reference = SpectrumInput(reference_path, config.reference_display_label,
                              reference_xy[0], reference_xy[1], config.reference_height,
                              True)
    loaded = []
    failures = {}
    for index, (sample, path, _stem) in enumerate(sample_records):
        if not path.is_file():
            failures[index] = _failed_result(sample, path, f"样品文件不存在：{path}")
            continue
        try:
            x, y = load_spectrum(path)
        except SpectrumError as exc:
            failures[index] = _failed_result(sample, path, str(exc))
            continue
        loaded.append((index, SpectrumInput(path, sample.display_label, x, y,
                                            sample.height, sample.peaks,
                                            sample.source_row)))
    all_spectra = [reference] + [item[1] for item in loaded]
    lo, hi = analysis_window(all_spectra, config.xmin, config.xmax, config.shift)
    try:
        reference_detection = detect_spectrum(
            reference, lo, hi, config.shift, config.reference_height,
            config.zero_guard, max_labels=None, prefix="R")
    except (SpectrumError, ValueError, OSError) as exc:
        raise CompareConfigError(f"基准谱峰检测失败：{exc}") from None

    results_by_index = dict(failures)
    for index, spectrum in loaded:
        try:
            detection = detect_spectrum(
                spectrum, lo, hi, config.shift, spectrum.height,
                config.zero_guard, max_labels=None, prefix="S")
            results_by_index[index] = compare_sample(reference_detection, detection,
                                                      config.tolerance)
        except (SpectrumError, ValueError) as exc:
            results_by_index[index] = _failed_result(sample_records[index][0],
                                                     spectrum.path, str(exc))
    results = [results_by_index.get(index) or _failed_result(
        sample, path, "样品未完成分析") for index, (sample, path, _stem)
        in enumerate(sample_records)]
    successes = [result for result in results if result.status == "success"]

    prepare_out(str(out_dir))
    for index, (sample, path, stem) in enumerate(sample_records):
        result = results[index]
        if result.status != "success":
            continue
        image = out_dir / f"compare_{stem}.png"
        try:
            render_sample_plot(result, reference_detection, config.tolerance,
                               config.normalize, image)
            result.image_path = image
        except Exception as exc:
            result.image_error = f"{image.name} 写出失败：{exc}"
    shift_image = None
    shift_note = ""
    matched_total = sum(result.matched for result in successes)
    if not matched_total:
        shift_note = "全批没有匹配峰，未生成 compare_shift.png"
        print("WARNING: " + shift_note, file=sys.stderr)
    else:
        try:
            shift_image = render_shift_plot(successes, config.tolerance,
                                            out_dir / "compare_shift.png")
        except Exception as exc:
            shift_note = f"compare_shift.png 写出失败：{exc}"
            print("WARNING: " + shift_note, file=sys.stderr)
    write_reports(out_dir, reference_detection, results)

    include_shift = bool(shift_image is not None and Path(shift_image).is_file())
    outputs = _output_names(sample_records, results, include_shift)
    issue_count = sum(1 for result in results
                      if result.status != "success" or result.image_error)
    exit_code = 0 if not issue_count else (2 if successes else 1)
    for index, (sample, path, stem) in enumerate(sample_records):
        result = results[index]
        if result.status != "success":
            print(f"  FAILED {path.name}: {result.message}", file=sys.stderr)
            continue
        print("  %-18s threshold=%-.4g  R=%d matched=%d missing=%d new=%d  %s"
              % (sample.display_label, result.detection.threshold,
                 len(reference_detection.peaks), result.matched, result.missing,
                 result.new, result.image_error or f"compare_{stem}.png"))

    algorithm = {
        "detection": "plot_raman.smooth/find_peaks/peak_center",
        "smooth_kernel": 5,
        "fit_window_cm-1": 30.0,
        "baseline_window_cm-1": 60.0,
        "merge_distance_cm-1": 15.0,
        "threshold_policy": "explicit or auto max(5.5 sigma, 1.1% max H)",
        "zero_guard_cm-1": config.zero_guard,
        "matching": "ordered_dp_max_matches_then_min_abs_delta",
        "match_tolerance_cm-1": config.tolerance,
        "interpolation": "linear on reference grid, no extrapolation",
    }
    manifest = {
        "schema_version": 1,
        "mode": "compare",
        "created": datetime.now().isoformat(timespec="seconds"),
        "status": "success" if exit_code == 0 else "partial" if exit_code == 2 else "failed",
        "exit_code": exit_code,
        "config": input_manifest["config"],
        "reference": dict(input_manifest["reference"],
                          label=config.reference_display_label,
                          threshold_counts=reference_detection.threshold,
                          peaks=len(reference_detection.peaks)),
        "samples": input_manifest["samples"],
        "parameters": {
            "xmin_cm-1": config.xmin if config.xmin is not None else lo,
            "xmax_cm-1": config.xmax if config.xmax is not None else hi,
            "requested_xmin_cm-1": config.xmin,
            "requested_xmax_cm-1": config.xmax,
            "tolerance_cm-1": config.tolerance,
            "normalize": config.normalize,
            "shift_cm-1": config.shift,
            "zero_guard_cm-1": config.zero_guard,
            "reference_height_counts": config.reference_height,
        },
        "common_window": {"xmin_cm-1": lo, "xmax_cm-1": hi,
                          "grid_points": int(reference_detection.x.size)},
        "algorithm": algorithm,
        "samples_result": [{
            "path": str(result.sample.path),
            "label": result.sample.label,
            "status": result.status,
            "threshold_counts": result.detection.threshold if result.detection else None,
            "matched_peaks": result.matched,
            "missing_peaks": result.missing,
            "new_peaks": result.new,
            "match_rate": result.match_rate,
            "metrics": result.metric,
            "pearson_r": result.pearson_r,
            "message": "；".join(value for value in (result.message, result.image_error)
                                 if value),
        } for result in results],
        "outputs": outputs,
        "warnings": [value for value in
                     ([result.message for result in results if result.message]
                      + ([shift_note] if shift_note else [])) if value],
    }
    write_manifest(out_dir / "compare_run.json", manifest)
    print("comparison -> %s" % out_dir)
    print("  reference %s: %d peak(s), threshold=%.4g"
          % (reference.name, len(reference_detection.peaks),
             reference_detection.threshold))
    if exit_code == 2:
        print(f"  {issue_count} sample(s) failed or had image errors; see compare_summary.csv",
              file=sys.stderr)
    elif exit_code == 1:
        print("ERROR: no sample completed successfully", file=sys.stderr)
    return exit_code

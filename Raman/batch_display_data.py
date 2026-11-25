# -*- coding: utf-8 -*-
"""Raman 分层批次展示：光谱加载、共同窗口与显示代表谱。

定量一律走 raman_compare 的既有函数；本模块只负责把批次文件组织成
(组,点,扫描) 数据对象，并提供「显示用」代表谱，绝不替换原始谱。
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from raman_compare import (SpectrumError, SpectrumInput, analysis_window,
                           detect_spectrum, window_data)


@dataclass
class LoadedScan:
    scan: object
    spectrum: SpectrumInput | None = None
    status: str = "ok"
    message: str = ""

    @property
    def label(self):
        return getattr(self.scan, "display_label", "")

    @property
    def path(self):
        return getattr(self.scan, "path", None)


def make_spectrum(scan):
    """把 BatchScan 转成 compare 核心可用的 SpectrumInput（不移位）。"""
    return SpectrumInput(
        path=scan.path,
        label=scan.display_label,
        x=None, y=None,
        height=scan.height,
        peaks=True,
        source_row=scan.source_row,
    )


def load_scan(scan):
    """读取一条扫描；失败时標 status=failed 并保留原因。"""
    if scan.status == "missing":
        return LoadedScan(scan, status="missing", message=scan.message)
    try:
        x, y = load_spectrum_file(scan.path)
    except Exception as exc:
        return LoadedScan(scan, status="failed", message=str(exc))
    spectrum = SpectrumInput(
        path=scan.path, label=scan.display_label, x=x, y=y,
        height=scan.height, peaks=True, source_row=scan.source_row)
    return LoadedScan(scan, spectrum=spectrum, status="ok")


def load_spectrum_file(path):
    """严格两列读取：复用 raman_compare.load_spectrum 的数据口径。"""
    from raman_compare import load_spectrum
    return load_spectrum(path)


def load_reference(ref):
    """读取基准谱，返回 SpectrumInput；失败抛 SpectrumError。"""
    x, y = load_spectrum_file(ref.path)
    return SpectrumInput(
        path=ref.path, label=ref.display_label, x=x, y=y,
        height=ref.height, peaks=True, source_row=ref.source_row)


def group_scans(config):
    """按 (组,点) 归集已加载扫描，键用自然点号排序。"""
    groups = {}
    for scan in config.scans:
        if not scan.enabled:
            continue
        groups.setdefault((scan.group, scan.point), []).append(load_scan(scan))
    for items in groups.values():
        items.sort(key=lambda item: item.scan.scan)
    return dict(sorted(groups.items(), key=lambda pair: (pair[0][0], pair[0][1])))


def valid_spectra(loaded_items):
    return [item.spectrum for item in loaded_items
            if item.status == "ok" and item.spectrum is not None]


def common_window(config, spectra, require_at_least=2):
    """所有给定谱的共同窗口；显式窗口优先，不外推。"""
    if len(spectra) < require_at_least:
        raise SpectrumError("有效光谱不足 %d 条，无法确定共同窗口" % require_at_least)
    return analysis_window(spectra, config.xmin, config.xmax, config.shift)


def display_mean(spectra, lo, hi, shift):
    """在公共网格上对已移位的光谱逐点平均；网格取自第一条有效谱。"""
    if not spectra:
        return None, None, 0
    grids, values = [], []
    for spectrum in spectra:
        xw, yw = window_data(spectrum, lo, hi, shift)
        grids.append(xw)
        values.append(yw)
    grid = grids[0]
    for other in grids[1:]:
        if other.size != grid.size or not np.allclose(other, grid, rtol=1e-9, atol=1e-6):
            grid = np.union1d(grid, other)
            break
    stacked = [np.interp(grid, gx, gy) for gx, gy in zip(grids, values)]
    return grid, np.mean(np.vstack(stacked), axis=0), len(spectra)


def representative(config, loaded_items, lo, hi):
    """按配置生成显示代表谱。

    返回 dict(x, y, kind, n, expected, label, note)；method=none 时 y=None。
    """
    valid = valid_spectra(loaded_items)
    expected = len([i for i in loaded_items if i.scan.status != "missing"])
    method = config.representative
    if not valid:
        return {"x": None, "y": None, "kind": "none", "n": 0,
                "expected": expected, "label": "No valid spectrum",
                "note": "该点没有有效扫描"}
    if method == "none":
        return {"x": None, "y": None, "kind": "none", "n": len(valid),
                "expected": expected, "label": "raw scans only",
                "note": "按配置不绘制代表谱"}
    if method == "first":
        chosen = sorted(valid, key=lambda s: (s.source_row or 0, s.path.name))[0]
        x, y = window_data(chosen, lo, hi, config.shift)
        return {"x": x, "y": y, "kind": "first", "n": 1,
                "expected": expected, "label": "display first scan",
                "note": "使用最小扫描号；其它扫描仍逐条显示"}
    grid, mean, n = display_mean(valid, lo, hi, config.shift)
    return {"x": grid, "y": mean, "kind": "display_mean", "n": n,
            "expected": expected, "label": "display mean, n=%d/%d" % (n, expected),
            "note": "显示均值；原始扫描未被替换，不参与定量"}


def point_status(loaded_items):
    """点级数据状态；只描述数据完整性，不表达样品质量。"""
    expected = len(loaded_items)
    valid = sum(1 for item in loaded_items if item.status == "ok")
    failed = sum(1 for item in loaded_items if item.status == "failed")
    missing = sum(1 for item in loaded_items if item.status == "missing")
    if valid == expected:
        status = "complete"
    elif valid == 0:
        status = "failed"
    else:
        status = "partial"
    return {"expected_scans": expected, "valid_scans": valid,
            "failed_scans": failed, "missing_scans": missing,
            "status": status}


def detect_scan(spectrum, config, threshold=None, max_labels=None):
    """复用现有 detect_spectrum，保持与 compare 核心同一口径。"""
    return detect_spectrum(
        spectrum, config_window_lo(config), config_window_hi(config),
        shift=config.shift, threshold=threshold,
        zero_guard=config.zero_guard, max_labels=max_labels,
        prefix="R")


# 每个配置在 runner 里会绑定实际窗口；这两个占位由 runner 显式设置。
def config_window_lo(config):
    return getattr(config, "_batch_lo", None)


def config_window_hi(config):
    return getattr(config, "_batch_hi", None)

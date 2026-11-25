# -*- coding: utf-8 -*-
"""Raman 基准对比模式的数据、峰匹配和指标核心。

本模块不做文件写出，也不依赖 matplotlib。峰检测复用 plot_raman 的既有
平滑、基线、峰位拟合和自适应阈值，确保与单谱/堆叠模式同一口径。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
from typing import Iterable

import numpy as np


MIN_POINTS = 8


class SpectrumError(ValueError):
    """单个光谱不满足对比模式的数据要求。"""


@dataclass
class SpectrumInput:
    path: Path
    label: str
    x: np.ndarray
    y: np.ndarray
    height: float | None = None
    peaks: bool = True
    source_row: int | None = None

    @property
    def name(self):
        return self.path.name


@dataclass(frozen=True)
class Peak:
    id: str
    center: float
    h: float
    imax: float
    baseline: float
    index: int


@dataclass
class Detection:
    spectrum: SpectrumInput
    x: np.ndarray
    y: np.ndarray
    threshold: float
    peaks: list[Peak]


@dataclass
class MatchResult:
    pairs: list[tuple[int, int]]
    missing: list[int]
    new: list[int]


@dataclass
class PeakComparison:
    reference: Peak | None
    sample: Peak | None
    status: str
    delta: float | None
    abs_delta: float | None
    tolerance: float
    reference_h_norm: float | None = None
    sample_h_norm: float | None = None
    h_ratio: float | None = None
    h_norm_ratio: float | None = None

    @property
    def reference_id(self):
        return self.reference.id if self.reference is not None else None

    @property
    def reference_center(self):
        return self.reference.center if self.reference is not None else None

    @property
    def sample_center(self):
        return self.sample.center if self.sample is not None else None

    @property
    def reference_h(self):
        return self.reference.h if self.reference is not None else None

    @property
    def sample_h(self):
        return self.sample.h if self.sample is not None else None


@dataclass
class SampleResult:
    sample: SpectrumInput
    status: str = "failed"
    detection: Detection | None = None
    comparisons: list[PeakComparison] = field(default_factory=list)
    matched: int | None = None
    missing: int | None = None
    new: int | None = None
    match_rate: float | None = None
    metric: dict = field(default_factory=dict)
    pearson_r: float | None = None
    points: int | None = None
    xmin: float | None = None
    xmax: float | None = None
    message: str = ""
    image_error: str = ""
    image_path: Path | None = None

    @property
    def label(self):
        return self.sample.label

    @classmethod
    def failed(cls, sample, message):
        return cls(sample=sample, status="failed", message=message)


def load_spectrum(path):
    """严格读取两列光谱；只处理有限数和严格单调波数。"""
    path = Path(path).resolve()
    try:
        raw = np.loadtxt(path, comments="#")
    except Exception as exc:
        raise SpectrumError(f"{path.name} 读取失败：{exc}") from None
    if raw.ndim == 1:
        raw = raw.reshape(1, -1)
    if raw.ndim != 2 or raw.shape[1] < 2:
        raise SpectrumError(f"{path.name} 至少需要两列（波数、强度）")
    if raw.shape[0] < 2:
        raise SpectrumError(f"{path.name} 数据点不足")
    x = np.asarray(raw[:, 0], dtype=float)
    y = np.asarray(raw[:, 1], dtype=float)
    if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        raise SpectrumError(f"{path.name} 含 NaN 或 Inf")
    steps = np.diff(x)
    if steps.size == 0 or np.any(steps == 0):
        raise SpectrumError(f"{path.name} 波数存在重复值")
    if np.all(steps > 0):
        pass
    elif np.all(steps < 0):
        x, y = x[::-1].copy(), y[::-1].copy()
    else:
        raise SpectrumError(f"{path.name} 波数不是严格单调序列")
    return x, y


def analysis_window(spectra: Iterable[SpectrumInput], xmin=None, xmax=None, shift=0.0):
    """用户窗口，或所有谱可用范围的最大交集；显式窗口不外推。"""
    spectra = list(spectra)
    if not spectra:
        raise SpectrumError("没有可用于确定共同窗口的光谱")
    if (xmin is None) != (xmax is None):
        raise SpectrumError("区间下限和区间上限必须同时填写或同时留空")
    ranges = [(float(s.x[0]) + shift, float(s.x[-1]) + shift) for s in spectra]
    lo, hi = max(item[0] for item in ranges), min(item[1] for item in ranges)
    if xmin is not None and xmax is not None:
        lo, hi = float(xmin), float(xmax)
        for spectrum, available in zip(spectra, ranges):
            if lo < available[0] or hi > available[1]:
                raise SpectrumError(
                    f"{spectrum.name} 的可用范围 {available[0]:.2f}–{available[1]:.2f} cm-1 "
                    f"不覆盖请求区间 {lo:.2f}–{hi:.2f} cm-1")
    if not math.isfinite(lo) or not math.isfinite(hi) or hi <= lo:
        raise SpectrumError("共同分析区间无效或上下限相反")
    return float(lo), float(hi)


def window_data(spectrum, lo, hi, shift=0.0):
    raw_lo, raw_hi = float(lo) - shift, float(hi) - shift
    mask = (spectrum.x >= raw_lo) & (spectrum.x <= raw_hi)
    x, y = spectrum.x[mask], spectrum.y[mask]
    if x.size < MIN_POINTS:
        raise SpectrumError(
            f"{spectrum.name} 在 {lo:.2f}–{hi:.2f} cm-1 内只有 {x.size} 点，至少需要 {MIN_POINTS} 点")
    if x.size < 2 or not np.all(np.diff(x) > 0):
        raise SpectrumError(f"{spectrum.name} 窗口内波数顺序无效")
    return x + shift, y


def detect_spectrum(spectrum, lo, hi, shift=0.0, threshold=None,
                    zero_guard=50.0, max_labels=None, prefix="R"):
    """检测窗口内全部峰；max_labels=None 不截断分析峰。"""
    from plot_raman import auto_threshold, find_peaks, peak_center, smooth

    x, y = window_data(spectrum, lo, hi, shift)
    if threshold is None:
        threshold, _sigma, _hmax = auto_threshold(
            x, y, zero_at=shift, zero_guard=zero_guard)
    threshold = float(threshold)
    if not math.isfinite(threshold) or threshold < 0:
        raise SpectrumError(f"{spectrum.name} 峰高阈值无效")
    ys = smooth(y)
    found = find_peaks(x, y, ys, threshold, max_labels=max_labels,
                       zero_at=shift, zero_guard=zero_guard)
    centers = [peak_center(x, y, ys, index) for _h, index, _base in found]
    peaks = [Peak(f"{prefix}{number}", float(center), float(h), float(y[index]),
                  float(base), int(index))
             for number, ((h, index, base), center)
             in enumerate(zip(found, centers), 1)]
    return Detection(spectrum, x, y, threshold, peaks)


def _same_objective(left, right):
    return left[0] == right[0] and abs(left[1] - right[1]) <= 1e-12


def match_peaks(reference: list[Peak], sample: list[Peak], tolerance: float):
    """保持顺序的一一匹配：最大化对数，再最小化绝对偏差和。

    目标相同的情况下，回溯优先“匹配”，其次跳过样品峰，使最终配对序列按
    基准峰序号、样品峰序号字典序取最小，结果完全确定。
    """
    n, m = len(reference), len(sample)
    count = np.zeros((n + 1, m + 1), dtype=np.int32)
    cost = np.zeros((n + 1, m + 1), dtype=float)
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            best = (int(count[i + 1, j]), round(float(cost[i + 1, j]), 12))
            candidate = (int(count[i, j + 1]), round(float(cost[i, j + 1]), 12))
            if candidate[0] > best[0] or (candidate[0] == best[0] and candidate[1] < best[1]):
                best = candidate
            delta = abs(sample[j].center - reference[i].center)
            if delta <= tolerance:
                candidate = (int(count[i + 1, j + 1]) + 1,
                             round(float(cost[i + 1, j + 1]) + delta, 12))
                if candidate[0] > best[0] or (candidate[0] == best[0] and candidate[1] < best[1]):
                    best = candidate
            count[i, j], cost[i, j] = best[0], best[1]

    pairs = []
    i = j = 0
    target = (int(count[0, 0]), round(float(cost[0, 0]), 12))
    while target[0] > 0 and i < n and j < m:
        delta = abs(sample[j].center - reference[i].center)
        matched = (int(count[i + 1, j + 1]) + 1,
                   round(float(cost[i + 1, j + 1]) + delta, 12)) if delta <= tolerance else None
        if matched is not None and _same_objective(matched, target):
            pairs.append((i, j))
            target = (int(count[i + 1, j + 1]),
                      round(float(cost[i + 1, j + 1]), 12))
            i += 1
            j += 1
        elif j < m and _same_objective((int(count[i, j + 1]), round(float(cost[i, j + 1]), 12)),
                                       target):
            j += 1
        elif i < n and _same_objective((int(count[i + 1, j]), round(float(cost[i + 1, j]), 12)),
                                       target):
            i += 1
        else:
            raise RuntimeError("峰匹配回溯失败")
    used_ref = {pair[0] for pair in pairs}
    used_sample = {pair[1] for pair in pairs}
    return MatchResult(pairs=pairs,
                       missing=[index for index in range(n) if index not in used_ref],
                       new=[index for index in range(m) if index not in used_sample])


def pearson_r(reference_values, sample_values):
    """Pearson r；退化、点数不足或非有限值返回 None。"""
    x = np.asarray(reference_values, dtype=float)
    y = np.asarray(sample_values, dtype=float)
    if x.size != y.size or x.size < 2:
        return None
    if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        return None
    dx, dy = x - x.mean(), y - y.mean()
    if np.dot(dx, dx) <= 0 or np.dot(dy, dy) <= 0:
        return None
    value = float(np.dot(dx, dy) / math.sqrt(float(np.dot(dx, dx) * np.dot(dy, dy))))
    return value if math.isfinite(value) else None


def diff_stats(deltas):
    """匹配峰的带符号/绝对偏差统计；空集全部返回 None。"""
    values = [float(value) for value in deltas]
    if not values:
        return {"mean_delta_cm-1": None, "mae_delta_cm-1": None,
                "max_abs_delta_cm-1": None, "rmse_delta_cm-1": None}
    absolutes = [abs(value) for value in values]
    return {
        "mean_delta_cm-1": sum(values) / len(values),
        "mae_delta_cm-1": sum(absolutes) / len(values),
        "max_abs_delta_cm-1": max(absolutes),
        "rmse_delta_cm-1": math.sqrt(sum(value * value for value in values) / len(values)),
    }


def _height_norm(peak, span):
    return peak.h / span if peak is not None and span > 0 else None


def compare_sample(reference: Detection, sample: Detection, tolerance: float):
    """计算一个样品的完整匹配明细和指标。"""
    grid = np.asarray(reference.x, dtype=float)
    sample_on_grid = np.interp(grid, sample.x, sample.y)
    if grid.size < MIN_POINTS:
        raise SpectrumError(f"{sample.spectrum.name} 的公共网格不足 {MIN_POINTS} 点")
    matched = match_peaks(reference.peaks, sample.peaks, tolerance)
    ref_span = float(reference.y.max() - reference.y.min())
    sample_span = float(sample.y.max() - sample.y.min())
    comparisons = []
    for i, j in matched.pairs:
        ref_peak, sample_peak = reference.peaks[i], sample.peaks[j]
        delta = sample_peak.center - ref_peak.center
        h_ratio = sample_peak.h / ref_peak.h if ref_peak.h != 0 else None
        ref_norm = _height_norm(ref_peak, ref_span)
        sample_norm = _height_norm(sample_peak, sample_span)
        norm_ratio = (sample_norm / ref_norm
                      if ref_norm not in (None, 0) and sample_norm is not None else None)
        comparisons.append(PeakComparison(
            ref_peak, sample_peak, "matched", delta, abs(delta), tolerance,
            ref_norm, sample_norm, h_ratio, norm_ratio))
    for i in matched.missing:
        peak = reference.peaks[i]
        comparisons.append(PeakComparison(
            peak, None, "missing", None, None, tolerance,
            _height_norm(peak, ref_span), None))
    for j in matched.new:
        peak = sample.peaks[j]
        comparisons.append(PeakComparison(
            None, peak, "new", None, None, tolerance,
            None, _height_norm(peak, sample_span)))
    comparisons.sort(key=lambda row: (
        float("inf") if row.reference_center is None else row.reference_center,
        float("inf") if row.sample_center is None else row.sample_center))
    metrics = diff_stats([row.delta for row in comparisons if row.status == "matched"])
    corr = pearson_r(reference.y, sample_on_grid)
    warnings = []
    if ref_span <= 0:
        warnings.append("基准谱在共同窗口内为常量，归一化 H 与部分比值留空")
    if sample_span <= 0:
        warnings.append("样品谱在共同窗口内为常量，归一化 H 与部分比值留空")
    if corr is None:
        warnings.append("全谱 Pearson 无法计算（常量谱、点数不足或非有限值）")
    reference_count = len(reference.peaks)
    matched_count = len(matched.pairs)
    match_rate = matched_count / reference_count if reference_count else None
    return SampleResult(
        sample=sample.spectrum,
        status="success",
        detection=sample,
        comparisons=comparisons,
        matched=matched_count,
        missing=len(matched.missing),
        new=len(matched.new),
        match_rate=match_rate,
        metric=metrics,
        pearson_r=corr,
        points=int(grid.size),
        xmin=float(grid[0]),
        xmax=float(grid[-1]),
        message="；".join(warnings),
    )


def comparison_records(result: SampleResult):
    """把峰级结果转为报告字段；缺失/新增没有的字段保持 None。"""
    rows = []
    for row in result.comparisons:
        rows.append({
            "file": str(result.sample.path),
            "label": result.sample.label,
            "reference_peak_id": row.reference_id,
            "reference_peak_cm-1": row.reference_center,
            "sample_peak_cm-1": row.sample_center,
            "status": row.status,
            "delta_cm-1": row.delta,
            "abs_delta_cm-1": row.abs_delta,
            "tolerance_cm-1": row.tolerance,
            "reference_H_counts": row.reference_h,
            "sample_H_counts": row.sample_h,
            "H_ratio": row.h_ratio,
            "reference_H_norm": row.reference_h_norm,
            "sample_H_norm": row.sample_h_norm,
            "H_norm_ratio": row.h_norm_ratio,
        })
    return rows


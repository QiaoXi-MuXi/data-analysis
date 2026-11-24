# -*- coding: utf-8 -*-
"""
CLSM 三维形貌 -> 槽宽 / 槽深 批量测量

输入: data/*.txt 或 *.lsm（同名优先 TXT；LSM 高度换算须显式确认）
  - TXT 首行: 空单元格 + 各列 "X: <µm>" 列坐标
  - 其余行: 空单元格 + 该行各列的 Z(µm)，行方向即 Y 轴
  - TXT 内没有 Y 坐标，行距按 X 步距处理；LSM 使用文件 X/Y 标定（见 README）

测量原理（先在物理XY中校正直槽方向，剖面沿法向Y跨槽）:
  主指标：沿槽长分区随机取条带，宽深独立筛查和有界补测，保留全部候选供追溯。
  以下逐列方法保留作为深度空间分布与旧版对照：
  1. 每列取 Y 向剖面，滑动平均去噪
  2. 用槽两侧平台区（距槽心 70~165 px）做稳健二次曲面拟合，作为局部基准面
     → 扣除样品倾斜与低频起伏，得到去趋势剖面 d
  3. 槽心 = 槽心附近 ±45 px 内 d 的最小值（抛物线内插到亚像素）
  4. 槽深 = 基准面在槽心处的高度 − 槽底高度；深度有效性独立于槽宽
     同时输出基准面/平滑敏感性、连续深区标记
  5. 开口宽：噪声门限定位持续凹陷，浅壁拟合到局部基准面得到外侧边界
     保留 10% / 50% / 90% 深度宽作为辅助指标；开口定位不使用最深值设定高度
  6. 保留原始缺失掩码；仅修复局部孤立尖点，记录多槽、边界拟合及邻列跳变标记
  7. 逐列结果取中位数，输出筛查前后结果、四分位数和有效剖面数

仅使用 numpy / openpyxl / matplotlib，均由项目内 python/ 便携运行时提供。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
import sys
from pathlib import Path

import numpy as np

# 项目便携Python使用隔离路径，显式加载本脚本同目录的测量模块。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from profile_measurement import (jsonable, selftest as profile_selftest,
                                 PROFILE_POSITIONS, STRIP_WIDTH_UM)
from clsm_inputs import discover_inputs, read_lsm_height, add_input_arguments, lsm_options
from groove_orientation import (orient_input, orientation_note, resample_height, plot_orientation,
                                selftest as orientation_selftest)
from profile_sampling import (analyse_profiles, selftest as sampling_selftest,
                              DEFAULT_COUNT, DEFAULT_SEED, DEFAULT_ATTEMPTS)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")   # 控制台码页不含 µ 等字符时不崩溃

# ---- 测量参数（单位: px，除另注外） ----
SMOOTH_Y = 9          # Y 向滑动平均窗口
REF_IN, REF_OUT = 70, 165   # 基准面拟合区间: 距槽心 70~165 px 的平台区
CENTER_SEARCH = 45    # 槽心搜索半宽
BORDER = 8            # 忽略扫描边缘行数（共聚焦首末行有边缘跌落）
LEVELS = (0.1, 0.5, 0.9)    # 取宽度的深度水平
MIN_DEPTH_UM = 0.10   # 判定为有效槽的最小深度
MIN_SNR = 5.0         # 有效槽的最小深度/平台噪声比
ZERO_AS_MISSING = True  # 沿用旧版零值缺失假设；另输出零值为真实高度的敏感性
ALGORITHM_VERSION = "paper-v5-local"
OPEN_DROP_UM = 0.05   # 凹陷检测下限；仅用于定位外壁，不是测宽高度
OPEN_NOISE_K = 3.0    # 凹陷至少低于平台噪声的三倍
OPEN_RUN = 5          # 凹陷至少持续五个 Y 像素
OPEN_GAP = 3          # 仅连接最多三个像素的短间隙
MAX_WALL_UNCERTAINTY_PX = 8.0
OPEN_CENTER_MARGIN_PX = 3.0  # 允许参考槽心与槽口区间之间的小幅偏移；拒绝邻近凹陷冒充。


# ---------------------------------------------------------------- 读入
def _read_txt_3d(path: Path, return_mask=False):
    """读取矩阵；兼容原三元返回值，按需附带插值前的缺失掩码。"""
    raw = path.read_bytes().replace(b"\x00", b"").splitlines()
    head = raw[0].split(b"\t")[1:]
    if len(head) < 2 or b":" not in head[0]:
        raise ValueError("表头不是 'X: <数值>' 格式")
    x = np.array([float(h.split(b":")[1]) for h in head], float)
    if not np.all(np.isfinite(x)) or np.any(np.diff(x) <= 0):
        raise ValueError("X 坐标须有限且严格递增")
    rows = []
    for line_no, ln in enumerate(raw[1:], 2):
        if not ln.strip():
            continue
        values = ln.split(b"\t")[1:]
        if len(values) != x.size:
            raise ValueError(f"第 {line_no} 行列数错误，停止以避免 Y 坐标错位")
        rows.append([float(v) for v in values])
    if len(rows) < 2:
        raise ValueError("高度矩阵至少需要两行")
    Z = np.asarray(rows, float)
    missing = ~np.isfinite(Z) | ((Z == 0) if ZERO_AS_MISSING else False)
    Z[missing] = np.nan
    for j in range(Z.shape[1]):
        bad = missing[:, j]
        good = np.flatnonzero(~bad)
        if bad.any() and good.size:
            Z[bad, j] = np.interp(np.flatnonzero(bad), good, Z[good, j])
    dy = float(np.median(np.diff(x)))
    return (x, Z, dy, missing) if return_mask else (x, Z, dy)


def read_3d(path: Path, return_mask=False, return_source=False, **height_options):
    """两种格式统一返回物理坐标/高度；保留旧三元及四元接口。"""
    path = Path(path)
    if path.suffix.casefold() == ".txt":
        x, z, dy, missing = _read_txt_3d(path, return_mask=True)
        source = dict(format="txt", height_status="TXT导出高度（沿用原流程）",
                      height_basis="TXT中的Z按µm读取；未由LSM回归或重标定",
                      dy_source="按TXT的X中位步距假定", missing_policy="沿用TXT零值缺失假设")
    elif path.suffix.casefold() == ".lsm":
        x, z, dy, missing, source = read_lsm_height(path, **height_options)
        for j in range(z.shape[1]):
            bad, good = missing[:, j], np.flatnonzero(~missing[:, j])
            if bad.any() and good.size:
                z[bad, j] = np.interp(np.flatnonzero(bad), good, z[good, j])
    else:
        raise ValueError("仅支持TXT或LSM输入")
    if return_source:
        return x, z, dy, missing, source
    return (x, z, dy, missing) if return_mask else (x, z, dy)


def smooth_edge(a: np.ndarray, w: int) -> np.ndarray:
    """镜像填充的滑动平均，端点不塌陷。"""
    if w <= 1:
        return a.astype(float)
    h = w // 2
    return np.convolve(np.pad(a, h, mode="reflect"), np.ones(w) / w, "valid")


def robust_poly(y, v, mask, deg=2, iters=3, k=3.0):
    """在 mask 区域内做剔除离群的低阶多项式拟合。"""
    m = mask.copy()
    c = np.polyfit(y[m], v[m], deg)
    for _ in range(iters):
        r = v - np.polyval(c, y)
        rm = r[mask]
        s = 1.4826 * np.median(np.abs(rm - np.median(rm)))
        if not np.isfinite(s) or s <= 0:
            break
        nm = mask & (np.abs(r) < k * s)
        if nm.sum() < deg + 2 or np.array_equal(nm, m):
            break
        m = nm
        c = np.polyfit(y[m], v[m], deg)
    return c


def runs(mask):
    edges = np.diff(np.r_[False, mask, False].astype(int))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def _fit_shallow_wall(d, boundary, direction, noise):
    idx = boundary + direction * np.arange(-3, 16)
    idx = idx[(idx >= BORDER) & (idx < d.size - BORDER)]
    upper = max(noise, 0.02)
    lower = max(6 * noise, 0.65)
    idx = idx[(d[idx] < -upper) & (d[idx] > -lower)]
    # 限制到靠近槽口的连续浅壁段，避免深入槽底或跨到另一个谷。
    if idx.size < 4:
        return None, "槽壁支撑点不足"
    idx = idx[:9]
    xx = direction * (idx - boundary)
    yy = d[idx]
    fit = np.polyfit(xx, yy, 1)
    for _ in range(3):
        residual = yy - np.polyval(fit, xx)
        scale = max(1.4826 * np.median(abs(residual - np.median(residual))), noise, 0.01)
        keep = abs(residual) <= 3 * scale
        if keep.sum() < 4:
            return None, "槽壁拟合不稳定"
        fit = np.polyfit(xx[keep], yy[keep], 1)
    slope, intercept = fit
    if slope >= -max(noise / 6, 0.002):
        return None, "槽壁斜率不足"
    root = -intercept / slope
    residual = yy - np.polyval(fit, xx)
    rmse = float(np.sqrt(np.mean(residual ** 2)))
    if root > 2 or root < -16 or rmse > max(2 * noise, 0.08):
        return None, "槽壁外推不可靠"
    return dict(pos=float(boundary + direction * root),
                uncertainty_px=float(max(noise, rmse, 0.01) / abs(slope)),
                slope=float(direction * slope), points=idx.tolist(), rmse=rmse), ""


def _fit_steep_wall(d, boundary, direction, noise):
    idx = boundary + direction * np.arange(-8, 9)
    idx = idx[(idx >= BORDER) & (idx < d.size - BORDER)]
    xx = direction * (idx - boundary)
    yy = d[idx]
    if np.count_nonzero(yy < -max(noise, 0.02)) < 4:
        return None, "槽壁支撑点不足"
    # 局部连续折线：平台残差为 0，槽壁为向内下降的直线。
    # 槽口位置由折点决定；Huber 损失降低局部凸起和坏点影响。
    roots = np.arange(-10, 2.01, 0.25)
    h = np.maximum(0, xx[None, :] - roots[:, None])
    scale = max(noise, 0.02)
    weights = np.ones_like(h)
    for _ in range(4):
        denom = np.sum(weights * h * h, axis=1)
        slopes = np.maximum(0, -np.sum(weights * h * yy, axis=1) / np.maximum(denom, 1e-12))
        residual = yy + slopes[:, None] * h
        weights = np.minimum(1, 1.5 * scale / np.maximum(abs(residual), 1e-12))
    ar = abs(residual)
    loss = np.mean(np.where(ar <= 1.5 * scale, 0.5 * residual**2,
                            1.5 * scale * (ar - 0.75 * scale)), axis=1)
    k = int(np.argmin(loss))
    root, slope = roots[k], slopes[k]
    inside = (xx > root) & (yy < -max(noise, 0.02))
    if slope < max(noise / 6, 0.002) or inside.sum() < 4:
        return None, "槽壁斜率不足"
    rmse = float(np.sqrt(np.mean(residual[k, inside] ** 2)))
    if k in (0, roots.size - 1) or rmse > max(2 * noise, 0.10):
        return None, "槽壁拟合不可靠"
    return dict(pos=float(boundary + direction * root),
                uncertainty_px=float(max(noise, rmse, 0.01) / slope),
                slope=float(-direction * slope), points=idx.tolist(), rmse=rmse), ""


def measure_opening(d, y0, noise):
    cutoff = max(OPEN_DROP_UM, OPEN_NOISE_K * noise)
    mask = np.isfinite(d) & (d < -cutoff)
    mask[:BORDER] = mask[-BORDER:] = False
    # 仅闭合最多三个像素的短间隙，长平台不得无条件合并。
    for lo, hi in runs(~mask):
        if lo > BORDER and hi < d.size - BORDER and hi - lo <= OPEN_GAP:
            mask[lo:hi] = True
    candidates = [(lo, hi) for lo, hi in runs(mask)
                  if hi - lo >= OPEN_RUN and lo <= y0 + CENTER_SEARCH and hi > y0 - CENTER_SEARCH]
    if not candidates:
        return dict(valid=False, reason="无持续凹陷", cutoff=cutoff)
    # 优先包含参考槽心的连通凹陷；其次取与槽心距离最近的一段。
    lo, hi = min(candidates, key=lambda ab: max(ab[0] - y0, y0 - ab[1] + 1, 0))
    split = any((a, b) != (lo, hi) and min(abs(a-hi), abs(lo-b)) <= 20
                and np.max(d[min(hi,b):max(lo,a)]) > -noise
                for a, b in candidates if a >= hi or b <= lo)
    left, lr = _fit_opening_wall(d, lo, 1, noise)
    right, rr = _fit_opening_wall(d, hi - 1, -1, noise)
    if left is None or right is None:
        return dict(valid=False, reason=lr or rr, cutoff=cutoff, split=split)
    valid = BORDER < left['pos'] < lo < hi - 1 < right['pos'] < d.size - 1 - BORDER
    plateau_overlap = (y0 - left['pos'] >= REF_IN - 4
                       or right['pos'] - y0 >= REF_IN - 4)
    # 参考槽心来自整图中位剖面；不允许在目标槽变浅/断续时把旁边小凹陷当成槽口。
    center_mismatch = not (left['pos']-OPEN_CENTER_MARGIN_PX <= y0 <= right['pos']+OPEN_CENTER_MARGIN_PX)
    if (left['uncertainty_px'] > MAX_WALL_UNCERTAINTY_PX
            or right['uncertainty_px'] > MAX_WALL_UNCERTAINTY_PX):
        valid = False
    reason = ("多槽分裂候选" if split else "槽口未包围参考槽心（邻近凹陷干扰）" if center_mismatch
              else "槽口接近平台拟合区" if plateau_overlap
              else "" if valid else "开口边界不确定")
    return dict(valid=bool(valid and not split and not plateau_overlap and not center_mismatch), reason=reason,
                left=left, right=right, cutoff=cutoff, split=split, center_mismatch=center_mismatch)

def _fit_opening_wall(d, boundary, direction, noise):
    fit, reason = _fit_shallow_wall(d, boundary, direction, noise)
    if fit is not None and direction*(fit["pos"]-boundary) < 0:
        fit["method"] = "浅壁线性外推"
        return fit, reason
    if fit is not None:
        fit, reason = None, "槽壁交点位于检测边界内侧"
    crossing, _ = _fit_crossing_wall(d, boundary, direction, noise)
    if crossing is not None and direction*(crossing["pos"]-boundary) < 0:
        crossing["method"] = "跨基准面局部槽壁拟合"
        return crossing, ""
    # 陡壁的浅高度带像素不足时才改用平台-槽壁连续折线，保留拟合方法。
    if reason == "槽壁支撑点不足":
        fit, reason = _fit_steep_wall(d, boundary, direction, noise)
        if fit is not None:
            fit["method"] = "陡壁连续折线"
            if direction*(fit["pos"]-boundary) >= 0:
                fit, reason = None, "槽壁交点位于检测边界内侧"
    if fit is None:
        fit, _ = _locate_supported_transition(d, boundary, direction, noise)
        if fit is not None:
            fit["method"] = "平台支撑的槽缘过渡括区"
            return fit, ""
    return fit, reason


def _locate_supported_transition(d, boundary, direction, noise):
    """平坦平台紧接弯曲陡壁时，以有界过渡区定位；不向深槽壁外推直线。"""
    tolerance = max(3 * noise, .02)
    offsets = np.arange(-12, 7)
    idx = boundary + direction * offsets
    if np.any(idx < BORDER) or np.any(idx >= d.size-BORDER):
        return None, "过渡区超出扫描范围"
    values = d[idx]
    if not np.all(np.isfinite(values)):
        return None, "过渡区存在非有限高度"
    # 必须具有连续五点、接近远场零基准的真实平台，不能把凸起或斜壁当平台。
    for k in range(len(idx)-4, 3, -1):
        platform = values[k-4:k+1]
        if np.any(abs(platform) > tolerance) or np.ptp(platform) > tolerance:
            continue
        departures = np.flatnonzero(values[k+1:] < -tolerance)
        if not departures.size:
            continue
        j = k+1+int(departures[0])
        if not -3 <= offsets[j] <= 2 or j-k > 3 or j+2 >= len(idx):
            continue
        wall = values[j:j+3]
        # 至少三点连续下降且达到显著凹陷；拒绝孤立尖点、槽内起伏和缓慢漂移。
        if np.any(np.diff(wall) >= -max(noise/3, .002)):
            continue
        if wall[-1] > -max(5*noise, .10):
            continue
        if np.any(np.diff(values[k:j+3]) > max(noise, .01)):
            continue
        slope = (wall[-1]-values[k])/(j+2-k)
        half_interval = (j-k)/2
        # 加一像素作为阈值带/离散采样的分辨尺度，不宣称是统计置信区间。
        uncertainty = half_interval + 1 + max(noise, .01)/abs(slope)
        if uncertainty > 2:
            continue
        center = float((idx[k]+idx[j])/2)
        bracket = sorted((int(idx[k]), int(idx[j])))
        return dict(pos=center, uncertainty_px=float(uncertainty),
                    slope=float(direction*slope), points=idx[k-4:j+3].tolist(),
                    rmse=None, transition_bracket_px=bracket,
                    localization_interval_px=[center-uncertainty, center+uncertainty],
                    baseline_tolerance_um=float(tolerance)), ""
    return None, "缺少平坦平台与连续下降槽壁的有界过渡"


def _fit_crossing_wall(d, boundary, direction, noise):
    """凸起槽缘穿过远场基准时，直接用跨零壁段定位，避免假定局部平台为零。"""
    offsets = np.arange(-16, 5)
    idx = boundary + direction * offsets
    inside = (idx >= BORDER) & (idx < d.size-BORDER)
    offsets, idx = offsets[inside], idx[inside]
    values = d[idx]
    crossings = np.flatnonzero((values[:-1] >= 0) & (values[1:] < 0))
    for k in crossings[::-1]:
        if offsets[k] > 0 or k < 1 or k+2 >= len(idx):
            continue
        points = idx[k-1:k+3]
        xx = direction * (points-boundary)
        yy = d[points]
        # 两侧各有真实高度支撑，且壁段持续向槽内下降。
        if not (yy[0] > max(noise, .02) and yy[-1] < -max(noise, .02)):
            continue
        if np.any(np.diff(yy) > max(noise, .02)):
            continue
        slope, intercept = np.polyfit(xx, yy, 1)
        if slope >= -max(noise/6, .002):
            continue
        root = -intercept/slope
        rmse = float(np.sqrt(np.mean((yy-(slope*xx+intercept))**2)))
        if not offsets[k]-.5 <= root <= offsets[k+1]+.5 or rmse > max(2*noise, .08):
            continue
        return dict(pos=float(boundary+direction*root),
                    uncertainty_px=float(max(noise, rmse, .01)/abs(slope)),
                    slope=float(direction*slope), points=points.tolist(), rmse=rmse), ""
    return None, "未找到可靠跨基准槽壁"


def clean_isolated_pixels(Z, missing):
    """只替换局部孤立尖点；连续深坑保留，修复掩码供明细追溯。"""
    blocks = np.lib.stride_tricks.sliding_window_view(np.pad(Z, 1, mode="reflect"), (3, 3))
    import warnings
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', message='All-NaN slice encountered', category=RuntimeWarning)
        median = np.nanmedian(blocks, axis=(-2, -1))
        mad = 1.4826 * np.nanmedian(abs(blocks - median[..., None, None]), axis=(-2, -1))
    candidates = np.isfinite(Z) & ~missing & (abs(Z - median) > np.maximum(6 * mad, 0.10))
    counts = np.lib.stride_tricks.sliding_window_view(np.pad(candidates, 1), (3, 3)).sum(axis=(-2, -1))
    deviation = Z - median
    neighbors = blocks - median[..., None, None]
    # 连续深区的角点也可能被中值法挑出；有相似高度邻点支撑时保留。
    supported = ((neighbors * deviation[..., None, None] > 0)
                 & (abs(neighbors) >= .5 * abs(deviation[..., None, None])))
    support_count = supported.sum(axis=(-2,-1))
    repaired = candidates & (counts <= 2) & (support_count <= 2)
    repaired[[0, -1], :] = False
    repaired[:, [0, -1]] = False
    cleaned = Z.copy()
    cleaned[repaired] = median[repaired]
    return cleaned, repaired


def _quality_missing(mask, lo, hi):
    segment = mask[max(0, int(np.floor(lo)) - 4):min(mask.size, int(np.ceil(hi)) + 5)]
    return bool(segment.size == 0 or segment.mean() > 0.10
                or any(b - a > 3 for a, b in runs(segment)))


# ---------------------------------------------------------------- 单列测量
def measure_column(p: np.ndarray, dy: float, y0: int):
    """测量单条 Y 向剖面；返回 dict，无法测量时返回 None。"""
    n = p.size
    if not np.all(np.isfinite(p)):
        return None
    y = np.arange(n, dtype=float)
    inner = (y >= BORDER) & (y <= n - 1 - BORDER)
    ref = inner & (np.abs(y - y0) >= REF_IN) & (np.abs(y - y0) <= REF_OUT)
    if ref.sum() < 30 or np.count_nonzero(ref & (y < y0)) < 15 or np.count_nonzero(ref & (y > y0)) < 15:
        return None

    c = robust_poly(y, p, ref)
    d = p - np.polyval(c, y)
    r_ref = d[ref]
    noise = 1.4826 * np.median(np.abs(r_ref - np.median(r_ref)))

    band = np.where(inner & (np.abs(y - y0) <= CENTER_SEARCH))[0]
    if band.size < 5:
        return None
    i = int(band[np.argmin(d[band])])

    y1, y2, y3 = d[i - 1], d[i], d[i + 1]
    den = y1 - 2 * y2 + y3
    off = float(np.clip(0.5 * (y1 - y3) / den, -0.5, 0.5)) if den > 0 else 0.0
    depth = -y2 + 0.25 * (y1 - y3) * off
    yc = i + off

    def cross(frac: float, step: int):
        """从槽心向外找 d = -frac*depth 的交点，返回亚像素位置。"""
        lev = -frac * depth
        j = i
        while BORDER <= j + step <= n - 1 - BORDER and d[j] < lev:
            j += step
        if j == i or not (BORDER <= j < n - BORDER) or not (d[j - step] <= lev <= d[j]):
            return None
        a, b = d[j], d[j - step]
        return j - step + step * (lev - b) / (a - b) if a != b else float(j)

    w = {}
    edges = {}
    for frac in LEVELS:
        left, right = cross(frac, -1), cross(frac, 1)
        w[frac] = None if (left is None or right is None) else (right - left) * dy
        edges[frac] = (left, right)

    ok = (depth >= MIN_DEPTH_UM and depth >= MIN_SNR * max(noise, 1e-6)
          and all(v is not None for v in w.values()))
    reason = ""
    if depth < MIN_DEPTH_UM:
        reason = "深度过小"
    elif depth < MIN_SNR * max(noise, 1e-6):
        reason = "信噪比不足"
    elif not ok:
        reason = "槽壁交点缺失"

    opening = measure_opening(d, y0, noise)
    return dict(opening=opening, baseline_coeff=c, valid=ok, reason=reason, yc=yc, yc_um=yc * dy, depth=depth,
                w_top=w[0.1], w_half=w[0.5], w_bottom=w[0.9],
                edges=edges, noise=noise, plateau=float(np.polyval(c, yc)))


# ---------------------------------------------------------------- 单文件
def analyse_file(path: Path, cols_step: int = 1, dy_override=None,
                 strip_width_um=STRIP_WIDTH_UM, profile_positions=None,
                 profile_count=DEFAULT_COUNT, random_seed=DEFAULT_SEED, max_attempts=DEFAULT_ATTEMPTS,
                 input_data=None, groove_axis="auto", **height_options):
    if cols_step < 1:
        raise ValueError("列采样步长必须大于零")
    x, Z, dy, missing, source = input_data if input_data is not None else read_3d(path, return_source=True, **height_options)
    raw_surface=dict(x=x,Z=Z,dy=dy if dy_override is None else float(dy_override),missing=missing)
    x, Z, dy, missing, source = orient_input(x, Z, dy, missing, source, groove_axis, dy_override,
                                           measurement=(robust_poly,measure_opening))
    if Z.shape[0] < 2 * REF_OUT + 2 * BORDER:
        raise ValueError("横槽方向数据不足以支持当前两侧平台区（需要至少346像素）；请增大横槽扫描范围")
    Zclean, repaired = clean_isolated_pixels(Z, missing)
    Zs = np.apply_along_axis(smooth_edge, 0, Zclean, SMOOTH_Y)
    finite_cols = np.all(np.isfinite(Zs), axis=0)
    if not finite_cols.any():
        raise RuntimeError("无完整可分析剖面")
    med = smooth_edge(np.median(Zs[:, finite_cols], axis=1), 15)
    y0 = int(np.argmin(med[20:-20])) + 20
    cols, res = [], []
    for j in range(0, Z.shape[1], cols_step):
        r = measure_column(Zs[:, j], dy, y0)
        if r is not None:
            r.update(col=j, flags=[], missing_count=int(missing[:, j].sum()),
                     repaired_count=int(repaired[:, j].sum()))
            opening = r["opening"]
            if "left" in opening:
                left, right = opening["left"]["pos"], opening["right"]["pos"]
                r.update(w_open=(right - left) * dy, left_open_um=left * dy,
                         right_open_um=right * dy)
                wall_points = opening["left"]["points"] + opening["right"]["points"]
                # 平滑窗口半径内的缺失同样会影响槽壁拟合。
                near_missing = any(missing[max(0, k - 4):min(Z.shape[0], k + 5), j].any()
                                   for k in wall_points)
                if near_missing or _quality_missing(missing[:, j], left, right):
                    opening["valid"] = False
                    opening["reason"] = "槽口或拟合支撑区缺失"
            else:
                r.update(w_open=None, left_open_um=None, right_open_um=None)
            r["opening_valid_raw"] = bool(opening["valid"])
            r["opening_valid"] = bool(opening["valid"])
            if r["repaired_count"]:
                r["flags"].append("修复孤立尖点")
            if _quality_missing(missing[:, j], y0 - CENTER_SEARCH, y0 + CENTER_SEARCH):
                r["valid"] = False
                r["reason"] = "槽内连续缺失或缺失比例过高"
        cols.append(j)
        res.append(r)

    # 仅筛出单列突跳，两侧邻列须互相一致；连续几何变化保留。
    opening_before = [r for r in res if r and r["opening_valid_raw"]]
    snapshot = {r["col"]: r for r in opening_before}
    for r in opening_before:
        j = r["col"]
        if j - cols_step not in snapshot or j + cols_step not in snapshot:
            continue
        neighbors = [snapshot[k] for k in snapshot if 0 < abs(k - j) <= 10 * cols_step]
        if len(neighbors) < 8:
            continue
        for key in ("left_open_um", "right_open_um"):
            values = np.array([v[key] for v in neighbors])
            mid = float(np.median(values))
            scale = 1.4826 * np.median(abs(values - mid))
            limit = max(6 * scale, 5 * dy)
            a, b = snapshot[j - cols_step][key], snapshot[j + cols_step][key]
            if (abs(r[key] - mid) > limit and abs(a - b) < 2 * dy
                    and min(abs(r[key] - a), abs(r[key] - b)) > limit):
                r["opening_valid"] = False
                r["flags"].append("开口边界单列突跳候选")
                break
    valid = [r for r in res if r and r["valid"]]
    if valid:
        dep = np.array([r["depth"] for r in valid])
        mid = np.median(dep)
        scale = 1.4826 * np.median(abs(dep - mid))
        for r in valid:
            if scale > 0 and abs(r["depth"] - mid) > 3 * scale:
                r["flags"].append("槽深整体离群候选（保留）")
    opening_valid = [r for r in res if r and r["opening_valid"]]

    def stat(key, selected):
        v = np.array([r[key] for r in selected], float)
        if not v.size:
            return (float("nan"),) * 3
        return float(np.median(v)), float(v.mean()), float(v.std(ddof=1)) if v.size > 1 else 0.0

    values = np.array([r["w_open"] for r in opening_valid])
    q25, q75 = np.percentile(values, [25, 75]) if values.size else (float("nan"),) * 2
    reason_counts = Counter((r["opening"]["reason"] or "拟合及缺失检查通过") if r else "无有限剖面或平台不足" for r in res)
    result = dict(path=path, x=x, Z=Z, Zclean=Zclean, Zs=Zs, dy=dy, y0=y0,raw_surface=raw_surface,
                dy_assumed=source["orientation"]["original_y_assumed"] and (source["orientation"]["rectified"] or not source["orientation"]["transposed"]), source=source, missing=missing, repaired=repaired,
                cols=np.array(cols), res=res, valid=valid, opening_valid=opening_valid,
                n_all=len(res), n_valid=len(valid), n_open=len(opening_valid),
                n_open_before=len(opening_before), w_open=stat("w_open", opening_valid),
                w_open_before=stat("w_open", opening_before), q_open=(float(q25), float(q75)),
                depth=stat("depth", valid), w_top=stat("w_top", valid),
                w_half=stat("w_half", valid), w_bottom=stat("w_bottom", valid),
                yc=stat("yc_um", valid), med_profile=med, opening_reasons=dict(reason_counts))
    analyse_depth_quality(result)
    result["paper"] = analyse_profiles(result, robust_poly, measure_opening, strip_width_um, profile_positions,
                                       profile_count, random_seed, max_attempts)
    direction_flag=source['orientation']['evidence'].get('direction_review_flag','')
    if direction_flag:
        for metric in ('depth','width'):
            result['paper']['summary'][metric+'_status']+='；方向待复核'
            if result['paper']['summary'][metric+'_mean_um'] is not None:
                result['paper']['summary'][metric+'_confidence']='待复核（方向估计或局部策略）'
        for strip in result['paper']['strips']:
            strip['flags'].append(direction_flag)
            for metric in ('depth','width'):
                if strip[metric+'_selected']:
                    strip[metric+'_confidence']=result['paper']['summary'][metric+'_confidence']
    return result


def close_lock_warning(xlsx: Path):
    print(f"\n[错误] 无法写入 {xlsx.name}，文件可能正被 Excel / WPS 打开。")
    print("       请关闭该工作簿后重跑；本次结果未保存（下方屏幕结果不受影响）。")


def write_excel(results, xlsx: Path, skipped=()):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    if not results:
        raise ValueError("没有成功结果，不能生成汇总")
    if len(results) > 16382:
        raise ValueError("文件数量超过横向汇总的Excel列数上限16382")
    NUM = "0.000"
    thin = Side(style="thin", color="000000")
    BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)
    HEAD_FILL = PatternFill("solid", fgColor="DCE6F1")
    LABEL_FILL = PatternFill("solid", fgColor="F2F2F2")
    HEAD_FONT = Font(bold=True, size=11)
    NOTE_FONT = Font(size=9, color="595959")
    CENTER = Alignment(horizontal="center", vertical="center")
    LEFT = Alignment(horizontal="left", vertical="center")

    wb = Workbook()

    # ---- 核心结果置于首页，完整汇总保留在第二页 ----
    core = wb.active
    core.title = "核心结论"
    ws = wb.create_sheet("槽宽槽深汇总")

    names = [r["path"].stem for r in results]
    rows = [
        ("分区取样平均剖面槽深 均值（µm，主指标）", [r["paper"]["summary"]["depth_mean_um"] for r in results], NUM),
        ("入选槽深 空间标准差（µm）", [r["paper"]["summary"]["depth_position_sd_um"] for r in results], NUM),
        ("槽深筛查前首个有效位置均值（µm）", [r["paper"]["summary"]["depth_unfiltered_mean_um"] for r in results], NUM),
        ("槽深使用备用候选的分区数", [r["paper"]["summary"]["depth_replacements"] for r in results], None),
        ("槽深孤立偏差候选分区数", [r["paper"]["summary"]["depth_outlier_groups"] for r in results], None),
        ("平均剖面深度入选 / 计划分区数", [f"{r['paper']['summary']['n_depth']} / {r['paper']['summary']['n_planned']}" for r in results], None),
        ("分区取样平均剖面 开口均值（µm）", [r["paper"]["summary"]["width_mean_um"] for r in results], NUM),
        ("入选开口 空间标准差（µm）", [r["paper"]["summary"]["width_position_sd_um"] for r in results], NUM),
        ("槽宽筛查前首个有效位置均值（µm）", [r["paper"]["summary"]["width_unfiltered_mean_um"] for r in results], NUM),
        ("平均剖面开口入选 / 计划分区数", [f"{r['paper']['summary']['n_width']} / {r['paper']['summary']['n_planned']}" for r in results], None),
        ("宽深同剖面入选凹陷截面积 均值（µm²）", [r["paper"]["summary"]["area_mean_um2"] for r in results], NUM),
        ("等效截面平均深度A/W 均值（µm，辅助）", [r["paper"]["summary"]["equivalent_depth_mean_um"] for r in results], NUM),
        ("中央80%平均剖面槽深（µm，辅助候选）", [r["paper"]["central_80pct"]["depth_um"] for r in results], NUM),
        ("中央80%深度有效", ["是" if r["paper"]["central_80pct"]["valid"] else "否" for r in results], None),
        ("v3逐剖面最大槽深 空间中位数（µm）", [r["depth"][0] for r in results], NUM),
        ("槽深 P25（µm）", [r["q_depth"][0] for r in results], NUM),
        ("槽深 P75（µm）", [r["q_depth"][1] for r in results], NUM),
        ("槽深 空间标准差（µm）", [r["depth"][2] for r in results], NUM),
        ("深度有效 / 总列数", [f"{r['n_depth']} / {r['n_all']}" for r in results], None),
        ("全部候选深度 中位数（µm，含无效）", [r["depth_all"] for r in results], NUM),
        ("旧版宽深共同筛查 中位数（µm）", [r["depth_legacy"][0] for r in results], NUM),
        ("3MAD候选数（保留于主结果）", [r["n_depth_outliers"] for r in results], None),
        ("假设排除3MAD候选 中位数（仅敏感性）", [r["depth_without_candidates"] for r in results], NUM),
        ("线性平台70–165px（µm，同一批有效列）", [r["depth_sensitivity"]["linear_70_165"] for r in results], NUM),
        ("二次平台90–180px（µm，同一批有效列）", [r["depth_sensitivity"]["quadratic_90_180"] for r in results], NUM),
        ("二次平台110–200px（µm，同一批有效列）", [r["depth_sensitivity"]["quadratic_110_200"] for r in results], NUM),
        ("3像素平滑（µm，同一基准面/有效列）", [r["depth_sensitivity"]["smooth_3"] for r in results], NUM),
        ("15像素平滑（µm，同一基准面/有效列）", [r["depth_sensitivity"]["smooth_15"] for r in results], NUM),
        ("零值视为真实高度（µm，固定基准面/有效列）", [r["depth_sensitivity"]["zero_as_height"] for r in results], NUM),
        ("零值为真实高度且重筛列（µm，仅敏感性）", [r["zero_policy_alternative"]["median_um"] for r in results], NUM),
        ("v2逐列开口宽度 中位数（µm）", [r["w_open"][0] for r in results], NUM),
        ("开口宽度 P25（µm）", [r["q_open"][0] for r in results], NUM),
        ("开口宽度 P75（µm）", [r["q_open"][1] for r in results], NUM),
        ("开口宽度 标准差（µm）", [r["w_open"][2] for r in results], NUM),
        ("开口宽度 筛查前（µm）", [r["w_open_before"][0] for r in results], NUM),
        ("开口有效 / 筛查前 / 总列数", [f"{r['n_open']} / {r['n_open_before']} / {r['n_all']}" for r in results], None),
        ("槽宽 半深处（µm）", [r["w_half"][0] for r in results], NUM),
        ("槽宽 半深处标准差（µm）", [r["w_half"][2] for r in results], NUM),
        ("辅助宽度 10%深度处（µm）", [r["w_top"][0] for r in results], NUM),
        ("槽宽 底部（µm）", [r["w_bottom"][0] for r in results], NUM),
        ("槽中心 Y（µm）", [r["yc"][0] for r in results], NUM),
        ("有效剖面数 / 总列数", [f"{r['n_valid']} / {r['n_all']}" for r in results], None),
    ]
    rows.extend([
        ("槽宽使用备用候选的分区数", [r['paper']['summary']['width_replacements'] for r in results], None),
        ("槽宽孤立偏差候选分区数", [r['paper']['summary']['width_outlier_groups'] for r in results], None),
        ("固定25/50/75%对照槽深（µm，当前槽壁算法）", [r['paper']['fixed_reference']['summary']['depth_mean_um'] for r in results], NUM),
        ("固定25/50/75%对照槽宽（µm，当前槽壁算法）", [r['paper']['fixed_reference']['summary']['width_mean_um'] for r in results], NUM),
        ("槽深汇总状态", [r['paper']['summary']['depth_status'] for r in results], None),
        ("槽宽汇总状态", [r['paper']['summary']['width_status'] for r in results], None),
        ("局部有效槽深参考（µm，非整槽主均值）", [r['paper']['summary']['depth_partial_mean_um'] if r['paper']['summary']['depth_mean_um'] is None else None for r in results], NUM),
        ("局部有效槽宽参考（µm，非整槽主均值）", [r['paper']['summary']['width_partial_mean_um'] if r['paper']['summary']['width_mean_um'] is None else None for r in results], NUM),
    ])

    ws.cell(row=1, column=1, value="测量项")
    for j, name in enumerate(names, start=2):
        ws.cell(row=1, column=j, value=name)
    ws.cell(row=1, column=len(names) + 2, value="平均")

    for i, (label, vals, fmt) in enumerate(rows, start=2):
        ws.cell(row=i, column=1, value=label)
        for j, v in enumerate(vals, start=2):
            ws.cell(row=i, column=j, value=None if isinstance(v, float) and not np.isfinite(v) else v)
        if fmt:
            ws.cell(row=i, column=len(names) + 2,
                    value=f'=IF(COUNT(B{i}:{get_column_letter(len(names) + 1)}{i})=0,"",AVERAGE(B{i}:{get_column_letter(len(names) + 1)}{i}))').number_format = NUM

    n_rows = len(rows) + 1
    n_cols = len(names) + 2
    for r in range(1, n_rows + 1):
        for c in range(1, n_cols + 1):
            cell = ws.cell(row=r, column=c)
            cell.border = BORDER
            cell.alignment = CENTER
            if r == 1:
                cell.fill = HEAD_FILL
                cell.font = HEAD_FONT
            elif c == 1:
                cell.fill = LABEL_FILL
            if r > 1 and c > 1 and isinstance(cell.value, (int, float)) and cell.number_format != NUM:
                cell.number_format = NUM
        ws.row_dimensions[r].height = 21

    ws.column_dimensions["A"].width = 34
    for c in range(2, n_cols + 1):
        ws.column_dimensions[get_column_letter(c)].width = 14
    ws.freeze_panes = "B2"

    notes = [
        "主指标：中央80%图幅沿X分区随机取样，默认9区，每区预设最多4个候选；固定种子可复现，宽深各区独立选首个通过者。",
        "实际参数见明细和运行记录；至少2/3分区有效且覆盖沿槽前中后三段才汇总。无效/低于检出限仍保留，不代表零。",
        "多位置测量与平均剖面分别参考Materials 2023,16,4761和Scientific Reports DOI:10.1038/s41598-024-81233-0；条带宽、滤波和阈值为本项目参数。",
        "等效截面平均深度A/W是凹陷面积除以开口宽，包含槽壁贡献；不是槽底平台平均高度，也不是完整三维槽体积测量。",
        "旧版对照：逐列9像素平滑、两侧70–165px稳健二次基准面、±45px内最小值与三点抛物线修正；最终取有效列中位数。",
        "深度独立检查检出限、搜索边界、槽底平滑支撑及平台缺失；不要求10%/50%/90%宽度交点齐全。3MAD整体候选只标记。",
        "敏感性比较固定使用同一批深度有效列；换平台模型/区间时重算基准，换平滑窗/零值解释时固定主基准，不是额外实验测量。",
        "槽宽主指标：持续凹陷定位外壁；浅壁外推，或凸起槽缘跨基准面壁段拟合，或陡壁连续折线；保留原有噪声和拟合质量门限。",
        "开口定位门限=max(0.05 µm, 3×平台噪声)，仅用于定位，不作为测宽高度；不以槽深或编号单调性设定开口。",
        "P25/P75 和标准差反映同一形貌图内沿 X 的空间差异，不是独立重复实验的置信区间。",
        "主取样宽深分别筛查：偏差阈值=max(3.5×1.4826MAD,20%中位数,深度0.15µm/宽度2像素)；相邻偏离分区保留并标记。",
        "Y 步距默认按 X 步距假定，可用 --dy 指定真实值；修复尖点、缺失、多槽分裂和拟合失败在逐列明细中记录。",
        "筛查前仅包含边界拟合及缺失检查通过的列；筛查后另排除孤立单列边界突跳候选。",
    ]
    for i, t in enumerate(notes, start=n_rows + 2):
        cell = ws.cell(row=i, column=1, value=t)
        cell.font = NOTE_FONT
        cell.alignment = LEFT

    core.sheet_view.showGridLines = False
    core.sheet_view.zoomScale = 100
    core.sheet_properties.pageSetUpPr.fitToPage = True
    core.sheet_properties.tabColor = "305B80"
    core.merge_cells("A1:K1")
    core["A1"] = "参考槽槽深与槽宽核心结果"
    core["A1"].font = Font(name="微软雅黑", size=15, bold=True, color="243746")
    core["A1"].alignment = LEFT
    core['A2']='局部参考与异常说明（点击查看“质量与局部参考”页）'
    core['A2'].hyperlink="#'质量与局部参考'!A1"
    core['A2'].font=Font(name='微软雅黑',size=10,color='0563C1',underline='single')
    core.row_dimensions[1].height = 32
    headers = ["样品", "槽深均值\n（µm）", "槽深位置间SD\n（µm）",
               "槽宽均值\n（µm）", "槽宽位置间SD\n（µm）",
               "槽深筛查前\n（µm）", "槽宽筛查前\n（µm）", "槽深入选/计划", "槽宽入选/计划", "槽深状态", "槽宽状态"]
    for col, label in enumerate(headers, start=1):
        cell = core.cell(3, col, label)
        cell.font = Font(name="微软雅黑", size=11, bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="305B80")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    core.row_dimensions[3].height = 44
    # 结果引用第二页；空白必须保持空白，不能由直接引用变成0。
    for row, (name, source_col) in enumerate(zip(names, range(2, len(names)+2)), start=4):
        core.cell(row, 1, name)
        for col, source_row in ((2, 2), (3, 3), (4, 8), (5, 9), (6, 4), (7, 10)):
            ref = f"'槽宽槽深汇总'!{get_column_letter(source_col)}{source_row}"
            core.cell(row, col, f'=IF(ISNUMBER({ref}),{ref},"")').number_format = NUM
        summary = results[row-4]['paper']['summary']
        core.cell(row, 8, f"{summary['n_depth']} / {summary['n_planned']}")
        core.cell(row, 9, f"{summary['n_width']} / {summary['n_planned']}")
        core.cell(row, 10, summary['depth_status'])
        core.cell(row, 11, summary['width_status'])
        if results[row-4].get("source", {}).get("format") == "lsm":
            for status_col in (10, 11):
                core.cell(row, status_col, core.cell(row, status_col).value+"；"+results[row-4]["source"]["height_status"])
        for cell in core[row]:
            cell.font = Font(name="微软雅黑", size=11, bold=cell.column in (2, 4))
            cell.alignment = Alignment(horizontal="left" if cell.column == 1 else "right", vertical="center",
                                       wrap_text=cell.column in (10, 11))
            cell.border = Border(bottom=Side(style="hair", color="D9E2EA"))
        core.row_dimensions[row].height = 68 if results[row-4].get("source", {}).get("format") == "lsm" or results[row-4]['source']['orientation'].get('mapping_kind')=='local_normals' else 26
        for col,metric in ((2,'depth'),(4,'width')):
            if '待复核' in summary[metric+'_status'] or summary[metric+'_mean_um'] is None:
                core.cell(row,col).fill=PatternFill('solid',fgColor='FFF2CC')
                core.cell(row,10 if metric=='depth' else 11).fill=PatternFill('solid',fgColor='FFF2CC')
    miss_depth = [r["path"].stem for r in results if r["paper"]["summary"]["depth_mean_um"] is None]
    miss_width = [r["path"].stem for r in results if r["paper"]["summary"]["width_mean_um"] is None]
    lsm_names = [r["path"].name for r in results if r.get("source", {}).get("format") == "lsm"]
    if lsm_names:
        core["A1"] = "参考槽结果（含LSM，标定状态见本页说明）"
    core_notes = [
        '黄色结果或状态为待复核/覆盖不足；主均值留空时，局部有效区数值见“质量与局部参考”，不得当作零或完整计划范围结论。',
        "沿槽长分区随机取样，默认9区；区内有界补测，宽深独立入选。SD是同一扫描的位置差异，不是独立实验误差。",
        ("槽深有效性不足，均值留空：" + "、".join(miss_depth)) if miss_depth else "槽深均达到最少有效分区数和沿槽覆盖要求。",
        ("槽宽有效性不足，均值留空：" + "、".join(miss_width)) if miss_width else "槽宽均达到最少有效分区数和沿槽覆盖要求。",
        "筛查前列保留每区首个质量有效候选的均值；所有候选、偏差筛查、入选状态和补测坐标见“平均剖面明细”。",
    ]
    if lsm_names:
        core_notes.append("LSM高度换算未由本程序独立核验：" + "、".join(lsm_names) + "；详见取样参数与输入，试算不得直接作为正式测量。")
    last_note = len(names) + 5
    for row, text in enumerate(core_notes, start=last_note):
        core.merge_cells(start_row=row, start_column=1, end_row=row, end_column=11)
        cell = core.cell(row, 1, text)
        cell.font = Font(name="微软雅黑", size=10, color="595959")
        cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        core.row_dimensions[row].height = 27
    for col, width in zip("ABCDEFGHIJK", (14, 17, 18, 17, 18, 18, 18, 18, 18, 28, 28)):
        core.column_dimensions[col].width = width
    core.freeze_panes = "B4"
    core.auto_filter.ref = f"A3:K{len(names)+3}"
    core.print_title_rows = "1:3"
    core.print_area = f"A1:K{last_note+len(core_notes)-1}"
    core.page_setup.orientation = "landscape"
    core.page_setup.paperSize = core.PAPERSIZE_A4
    core.page_setup.fitToWidth = 1
    core.page_setup.fitToHeight = 0
    wb.active = 0

    # ---- 明细表: 每条剖面一行，便于溯源 ----
    ds = wb.create_sheet("逐列明细")
    head = ["文件", "列号", "X（µm）", "槽中心 Y（µm）", "槽深（µm）",
            "辅助10%宽（µm）", "半深宽（µm）", "底部宽（µm）", "辅助宽共同有效", "辅助宽说明",
            "开口宽（µm）", "左开口Y（µm）", "右开口Y（µm）", "开口筛查前有效", "开口最终有效",
            "开口原因", "质量标记", "缺失点数", "修复尖点数", "检测门限（µm）",
            "左拟合方法", "右拟合方法", "左定位尺度（µm）", "右定位尺度（µm）",
            "深度独立有效", "深度无效原因", "深度质量标记", "平台噪声（µm）",
            "线性平台深度（µm）", "二次90–180深度（µm）", "二次110–200深度（µm）",
            "3像素平滑深度（µm）", "15像素平滑深度（µm）", "零值为高度深度（µm）"]
    for c, h in enumerate(head, start=1):
        cell = ds.cell(row=1, column=c, value=h)
        cell.fill = HEAD_FILL
        cell.font = HEAD_FONT
        cell.border = BORDER
        cell.alignment = CENTER

    rr = 2
    for r in results:
        for j, m in zip(r["cols"], r["res"]):
            vals = [r["path"].stem, int(j), float(r["x"][j]),
                    None if m is None else m["yc_um"],
                    None if m is None else m["depth"],
                    None if m is None else m["w_top"],
                    None if m is None else m["w_half"],
                    None if m is None else m["w_bottom"],
                    "是" if (m and m["valid"]) else "否",
                    "无有限剖面或平台不足" if m is None else m["reason"],
                    None if m is None else m["w_open"],
                    None if m is None else m["left_open_um"],
                    None if m is None else m["right_open_um"],
                    "是" if m and m["opening_valid_raw"] else "否",
                    "是" if m and m["opening_valid"] else "否",
                    "无有限剖面或平台不足" if m is None else m["opening"]["reason"],
                    "" if m is None else "；".join(m["flags"]),
                    int(r["missing"][:, j].sum()), int(r["repaired"][:, j].sum()),
                    None if m is None else m["opening"]["cutoff"],
                    None if m is None else m["opening"].get("left", {}).get("method"),
                    None if m is None else m["opening"].get("right", {}).get("method"),
                    None if m is None or "left" not in m["opening"] else m["opening"]["left"]["uncertainty_px"] * r["dy"],
                    None if m is None or "right" not in m["opening"] else m["opening"]["right"]["uncertainty_px"] * r["dy"]]
            vals.extend([
                "是" if m and m["depth_valid"] else "否",
                "平台不足或无有限剖面" if m is None else m["depth_reason"],
                "" if m is None else "；".join(m["depth_flags"]),
                None if m is None else m["noise"],
                *[None if m is None else m["depth_variants"][k] for k in DEPTH_VARIANTS]])
            for c, v in enumerate(vals, start=1):
                cell = ds.cell(row=rr, column=c, value=v)
                cell.border = BORDER
                cell.alignment = CENTER
                if isinstance(v, float):
                    cell.number_format = NUM
            rr += 1

    for c, w in zip(range(1, 11), (10, 7, 10, 13, 11, 12, 12, 12, 6, 12)):
        ds.column_dimensions[get_column_letter(c)].width = w
    for c in range(11, len(head) + 1):
        ds.column_dimensions[get_column_letter(c)].width = 22 if c in (16, 17, 21, 22) else 16
    ds.auto_filter.ref = ds.dimensions
    ds.freeze_panes = "A2"

    ps = wb.create_sheet("平均剖面明细")
    ps.append(["文件", "位置比例", "中心列0起", "X（µm）", "首列0起", "末列0起", "条带列数", "条带跨度（µm）",
               "槽深（µm）", "深度有效", "深度原因", "开口（µm）", "开口有效", "开口原因",
               "平台噪声（µm）", "截面积（µm²）", "等效深度A/W（µm）", "槽底Y（px）", "中心漂移（µm）", "标记",
               "分区1起", "区内候选序号", "深度入选", "宽度入选", "深度偏差超限", "宽度偏差超限", "深度排除", "宽度排除"])
    pd = wb.create_sheet("平均剖面数据")
    pd.append(["文件", "位置比例", "Y（px）", "Y（µm）", "有效支撑列数（校正时为重采样列）", "支撑达80%", "平均高度（µm）",
               "基准高度（µm）", "去基准高度（µm）", "3px平滑去基准高度（µm）", "平台支撑点"])
    se = wb.create_sheet("平均剖面敏感性")
    max_positions = max(r['paper']['summary']['n_planned'] for r in results)
    se.append(["文件", "比较条件", "深度均值（µm）", "有效位置数",
               *[f"分区{i+1}候选（µm）" for i in range(max_positions)], "说明"])
    settings = wb.create_sheet("取样参数与输入")
    settings.append(["文件", "取样模式", "计划分区", "随机种子", "每区最多候选", "实际候选数", "槽深偏差阈值（µm）", "槽宽偏差阈值（µm）", "输入状态", "输入格式", "高度标定状态", "分析Y步距来源", "分析Y步距（µm）", "高度换算及缺失规则", "槽方向及坐标映射", "方向选项", "原始Y步距来源", "分析X步距来源", "槽线相对原始X角度（度）", "方向重采样", "分析像素至原始行列映射", "方向复核标记"])
    for name, reason in skipped:
        settings.append([name, None, None, None, None, None, None, None, "跳过："+reason])
    for result in results:
        paper=result["paper"]
        params, summary = paper['parameters'], paper['summary']
        settings.append([result['path'].name,params['sampling'],summary['n_planned'],params['random_seed'],
                         params['max_attempts'],summary['n_attempted'],summary['depth_screen_limit_um'],
                         summary['width_screen_limit_um'],"已分析；可信度见质量页",result["source"]["format"],result["source"]["height_status"],
                         result["source"]["dy_source"],result["dy"],result["source"]["height_basis"]+"；"+result["source"]["missing_policy"]+
                         ("；比例="+str(result["source"]["height_scale_um"])+"，偏移="+str(result["source"]["height_offset_um"])+"；"+
                          result["source"]["acquisition_validity"] if result["source"]["format"] == "lsm" else ""),
                         orientation_note(result["source"]), result["source"]["orientation"]["requested"],
                         result["source"]["orientation"]["original_y_source"],
                         ("物理XY方向校正后的沿槽步距" if result['source']['orientation']['rectified'] else
                          result["source"]["orientation"]["original_y_source"] if result["source"]["orientation"]["transposed"] else "原始X坐标"),
                         result['source']['orientation']['angle_deg'],result['source']['orientation']['rectified'],
                         ('非仿射局部法向映射：坐标与质量掩码见运行JSON' if result['source']['orientation'].get('mapping_kind')=='local_normals' else json.dumps(result['source']['orientation'].get('analysis_index_to_original_row_col'),ensure_ascii=False)),
                         result['source']['orientation']['evidence'].get('direction_review_flag','')])
        for strip in paper["strips"]:
            cols=strip["columns"]
            ps.append([result["path"].stem,strip["fraction"],strip["center_column"],strip["x_um"],
                       cols[0] if cols else None,cols[-1] if cols else None,len(cols),strip.get("actual_span_um"),
                       strip["depth_um"],"是" if strip["valid"] else "否",strip["reason"],strip["width_um"],
                       "是" if strip["width_valid"] else "否",strip["width_reason"],strip.get("noise_um"),
                       strip["area_um2"],strip["equivalent_depth_um"],strip.get("bottom_y_px"),strip.get("center_drift_um"),"；".join(strip["flags"]),
                       strip['group']+1,strip['attempt'],strip['depth_selected'],strip['width_selected'],
                       strip['depth_outlier_candidate'],strip['width_outlier_candidate'],strip['depth_excluded'],strip['width_excluded']])
            if "baseline_um" in strip:
                for k in range(len(strip["mean_profile_um"])):
                    pd.append(jsonable([result["path"].stem,strip["fraction"],k,k*result["dy"],strip["support_counts"][k],
                              bool(strip["row_valid"][k]),strip["mean_profile_um"][k],strip["baseline_um"][k],
                              strip["residual_um"][k],strip["smoothed_residual_um"][k],bool(strip["reference_mask"][k])]))
        for key,variant in paper["sensitivity"].items():
            se.append([result["path"].stem,key,variant["summary"]["depth_mean_um"],variant["summary"]["n_depth"],
                       *variant["depths_um"],*[None]*(max_positions-len(variant["depths_um"])),"；".join(variant["reasons"])])
    for sheet in (ps,pd,se,settings):
        sheet.freeze_panes="C2"
        sheet.auto_filter.ref=sheet.dimensions
        for cell in sheet[1]:
            cell.font,cell.fill,cell.alignment=HEAD_FONT,HEAD_FILL,CENTER
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value,float): cell.number_format=NUM
        for col in range(1,sheet.max_column+1): sheet.column_dimensions[get_column_letter(col)].width=18
    for col in ("K","N","T"): ps.column_dimensions[col].width=45
    se.column_dimensions["B"].width=30
    se.column_dimensions[get_column_letter(max_positions+5)].width=55
    settings.column_dimensions['I'].width=70
    for col, width in (("J", 14), ("K", 42), ("L", 28), ("M", 18), ("N", 85)):
        settings.column_dimensions[col].width = width
    for col in ("O", "P", "Q", "R"):
        settings.column_dimensions[col].width = 48
    settings.column_dimensions['U'].width = 75
    settings.column_dimensions['V'].width = 60
    for row in settings.iter_rows(min_row=2):
        if isinstance(row[8].value, str) and row[8].value.startswith("跳过："):
            row[8].alignment = Alignment(vertical="top", wrap_text=True)
            settings.row_dimensions[row[0].row].height = 90
        for cell in row[14:]:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        if row[9].value == "txt":
            settings.row_dimensions[row[0].row].height = 45
        if row[9].value == "lsm":
            for cell in row[10:]:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
            settings.row_dimensions[row[0].row].height = 90

    # 将可信度、局部参考值与排除原因置于可筛选的独立页，避免空白被误读为零。
    qs=wb.create_sheet('质量与局部参考')
    qs.append(['文件','方向策略','测量项','整槽主均值（µm）','局部有效区参考（µm，非整槽结论）',
               '有效分区','计划分区','质量失败候选','孤立偏差排除候选','可信度','汇总状态','说明'])
    for result in results:
        summary=result['paper']['summary']
        strategy=result['source']['orientation'].get('strategy','global_straight')
        for metric,label in (('depth','槽深'),('width','槽宽')):
            qs.append([result['path'].name,strategy,label,summary[metric+'_mean_um'],
                summary[metric+'_partial_mean_um'] if summary[metric+'_mean_um'] is None else None,
                summary['n_'+metric],summary['n_planned'],summary[metric+'_quality_failed_attempts'],
                summary[metric+'_excluded_attempts'],summary[metric+'_confidence'],summary[metric+'_status'],
                '局部参考只概括已通过区段；缺失/异常不当作零。方向和质量阈值为操作参数，未作独立仪器验证。'])
    qs.freeze_panes='D2';qs.auto_filter.ref=qs.dimensions
    for col,width in enumerate((15,23,12,22,29,12,12,18,23,38,48,80),1):
        qs.column_dimensions[get_column_letter(col)].width=width
    for cell in qs[1]:
        cell.font,cell.fill=HEAD_FONT,HEAD_FILL
    for row in qs.iter_rows(min_row=2):
        for cell in row:
            cell.alignment=Alignment(vertical='top',wrap_text=True)
            if isinstance(cell.value,float):cell.number_format=NUM
        qs.row_dimensions[row[0].row].height=65
        if '待复核' in row[9].value or row[3].value is None:
            for cell in row:cell.fill=PatternFill('solid',fgColor='FFF2CC')
    for col,label in enumerate(('取样策略','槽深可信度','槽宽可信度','方向有效'),29):
        ps.cell(1,col,label)
    rr=2
    for result in results:
        for strip in result['paper']['strips']:
            for col,value in enumerate((strip.get('sampling_strategy','standard_strip'),strip['depth_confidence'],
                                       strip['width_confidence'],strip.get('geometry_valid',True)),29):
                ps.cell(rr,col,value)
            if not strip['valid'] or not strip['width_valid'] or strip['depth_excluded'] or strip['width_excluded']:
                for col in (10,11,13,14,20,30,31):
                    ps.cell(rr,col).fill=PatternFill('solid',fgColor='FCE4D6')
            rr+=1
    for col in range(29,33):
        ps.column_dimensions[get_column_letter(col)].width=38
        ps.cell(1,col).font,ps.cell(1,col).fill=HEAD_FONT,HEAD_FILL
    ps.auto_filter.ref=ps.dimensions

    try:
        wb.save(xlsx)
        return True
    except PermissionError:
        close_lock_warning(xlsx)
        return False


# ---------------------------------------------------------------- 作图
def _lsm_plot_status(fig, results):
    """独立导出的诊断/比较图也携带标定状态，避免脱离审计后被当成正式测量。"""
    states = sorted({r["source"]["height_status"] for r in results
                     if r.get("source", {}).get("format") == "lsm"})
    notes = sorted({orientation_note(r.get("source", {})) for r in results} - {""})
    if len(results)>1 and any(r.get('source',{}).get('orientation',{}).get('rectified') for r in results):
        notes=['分级方向校正：整槽直线 / 局部法向；逐文件方向、支撑及可信度见Excel/JSON']
    if states or notes:
        text = ("LSM高度换算："+"；".join(states)+"；" if states else "") + "；".join(notes)
        fig.text(.5, .012, text, ha="center", fontsize=8, color="#9b3b20")
        return .055
    return 0


def plot_opening_file(r, out_png: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    x, Z, dy = r["x"], r["Z"], r["dy"]
    y = np.arange(Z.shape[0], dtype=float)
    xc, yc = x - x[0], y * dy
    fig, (am, ap, aw) = plt.subplots(1, 3, figsize=(18.4, 5.8), gridspec_kw={"width_ratios": [1, 1.15, 1]})
    lo, hi = np.nanpercentile(Z, [2, 98])
    im = am.imshow(Z, extent=(xc[0], xc[-1], yc[-1], yc[0]), cmap="terrain", vmin=lo, vmax=hi, aspect="equal")
    fig.colorbar(im, ax=am, fraction=0.046, pad=0.03).set_label("Z（µm）")
    xx = xc[r["cols"]]
    def series(key):
        return np.array([m[key] if m and m["opening_valid"] else np.nan for m in r["res"]])
    am.plot(xx, series("left_open_um"), "k--", lw=.85, label="拟合开口边界")
    am.plot(xx, series("right_open_um"), "k--", lw=.85)
    am.set(title=f"{r['path'].name} 开口边界", xlabel="X（µm）", ylabel="Y（µm）")
    am.legend(fontsize=8, loc="lower right")
    if r["opening_valid"]:
        rep = min(r["opening_valid"], key=lambda m: abs(m["w_open"] - r["w_open"][0]))
        j = rep["col"]
        d_raw = Z[:, j] - np.polyval(rep["baseline_coeff"], y)
        d_clean = r["Zs"][:, j] - np.polyval(rep["baseline_coeff"], y)
        ap.plot(yc, d_raw, color=".7", lw=.7, label="原始插值剖面（去基准）")
        ap.plot(yc, d_clean, color="C0", lw=1.2, label="清理并平滑后的剖面")
        ap.axhline(0, c="k", ls=":", lw=1, label="局部平台基准")
        for side, color in (("left", "C2"), ("right", "C3")):
            wall = rep["opening"][side]
            points = np.array(wall["points"])
            ap.scatter(yc[points], d_clean[points], s=8, c=color)
            end = max(points) if side == "left" else min(points)
            line_y = np.linspace(min(wall["pos"], end), max(wall["pos"], end), 30)
            ap.plot(line_y * dy, np.minimum(0, wall["slope"] * (line_y - wall["pos"])), color=color, lw=1.5)
            ap.plot(wall["pos"] * dy, 0, "o", color=color, ms=5)
        left, right = rep["left_open_um"], rep["right_open_um"]
        ap.annotate("", xy=(left, .10), xytext=(right, .10), arrowprops=dict(arrowstyle="<->", color="C1", lw=1.6))
        ap.text((left + right)/2, .13, f"该列开口 {rep['w_open']:.2f} µm", ha="center", fontsize=9)
        ap.set_xlim(max(0, left - 18), min(yc[-1], right + 18))
        ap.set_title(f"代表剖面：第 {j} 列（按开口宽选择）")
        am.plot(xc[j], (left + right)/2, "o", mfc="none", mec="b", ms=5)
    else:
        ap.text(.5, .5, "没有可靠开口剖面", ha="center", transform=ap.transAxes)
    ap.set(xlabel="Y（µm）", ylabel="相对平台高度（µm）")
    ap.grid(alpha=.25)
    if r["opening_valid"]:
        ap.legend(fontsize=7, loc="lower left")
    raw = np.array([m["w_open"] if m and m["w_open"] is not None else np.nan for m in r["res"]])
    aw.plot(xx, raw, color=".75", lw=.7, label="所有可拟合候选（含无效）")
    aw.plot(xx, series("w_open"), color="C0", lw=1, label="质量检查通过")
    if r["n_open"]:
        aw.axhline(r["w_open"][0], color="C1", ls="--", label=f"中位数 {r['w_open'][0]:.2f} µm")
        aw.axhspan(*r["q_open"], color="C0", alpha=.1, label="P25–P75 空间分布")
    aw.set(title=f"开口有效 {r['n_open']}/{r['n_all']}", xlabel="X（µm）", ylabel="开口宽度（µm）")
    aw.grid(alpha=.25)
    aw.legend(fontsize=8)
    note = "Y 步距按 X 假定" if r["dy_assumed"] else "Y 步距："+r.get("source", {}).get("dy_source", "已指定")
    if r.get("source", {}).get("format") == "lsm":
        note += "；"+r["source"]["height_status"]
    fig.suptitle(f"{ALGORITHM_VERSION} | {note}：{dy:.5g} µm | 孤立尖点修复 {int(r['repaired'].sum())}；多槽/缺失/拟合失败保留标记", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, .95))
    fig.savefig(out_png, dpi=150)
    plt.close(fig)


DEPTH_VARIANTS = ("linear_70_165", "quadratic_90_180", "quadratic_110_200",
                  "smooth_3", "smooth_15", "zero_as_height")


def _depth_minimum(profile, y0):
    """与主测量相同的最小值及亚像素修正，用于固定列集的敏感性比较。"""
    band = np.arange(max(BORDER, y0-CENTER_SEARCH), min(profile.size-BORDER, y0+CENTER_SEARCH+1))
    i = int(band[np.argmin(profile[band])])
    a, b, c = profile[i-1:i+2]
    den = a - 2*b + c
    off = float(np.clip(.5*(a-c)/den, -.5, .5)) if den > 0 else 0.0
    return float(-b + .25*(a-c)*off)


def _depth_quality(m, missing, y0):
    """深度仅由自身质量决定；宽度交点缺失不使深度失效。"""
    y = np.arange(missing.size)
    ref = (abs(y-y0) >= REF_IN) & (abs(y-y0) <= REF_OUT) & (y >= BORDER) & (y < y.size-BORDER)
    idx = int(round(m["yc"]))
    half = SMOOTH_Y//2 + 1
    support = missing[max(0,idx-half):min(y.size,idx+half+1)]
    if m["depth"] < MIN_DEPTH_UM:
        return "深度过小"
    if m["depth"] < MIN_SNR * max(m["noise"], 1e-6):
        return "信噪比不足"
    if idx <= max(BORDER, y0-CENTER_SEARCH) or idx >= min(y.size-1-BORDER,y0+CENTER_SEARCH):
        return "槽底位于搜索边界"
    if support.any():
        return "槽底平滑支撑含插值点"
    if missing[ref].any():
        return "平台基准支撑含插值点"
    return ""


def analyse_depth_quality(r):
    """补充独立深度筛查，不按编号或组间趋势删除观测。"""
    y = np.arange(r["Z"].shape[0], dtype=float)
    y0 = r["y0"]
    valid = []
    all_depths = []
    for m in r["res"]:
        if m is None:
            continue
        j = m["col"]
        m["depth_reason"] = _depth_quality(m, r["missing"][:,j], y0)
        m["depth_valid"] = not m["depth_reason"]
        m["depth_flags"] = []
        if m["opening"]["reason"] == "槽口接近平台拟合区":
            m["depth_flags"].append("槽口接近主基准区，需核对平台敏感性")
        all_depths.append(m["depth"])
        if m["depth_valid"]:
            valid.append(m)
        p = r["Zs"][:,j]
        variants = {}
        for key, deg, inner, outer in (("linear_70_165",1,70,165),
                                      ("quadratic_90_180",2,90,180),
                                      ("quadratic_110_200",2,110,200)):
            ref = (abs(y-y0) >= inner) & (abs(y-y0) <= outer) & (y >= BORDER) & (y < y.size-BORDER)
            coeff = robust_poly(y, p, ref, deg=deg)
            variants[key] = _depth_minimum(p-np.polyval(coeff,y), y0)
        base = np.polyval(m["baseline_coeff"],y)
        for window in (3,15):
            variants[f"smooth_{window}"] = _depth_minimum(smooth_edge(r["Zclean"][:,j],window)-base,y0)
        # 恢复被当作缺失值插值的有限零值；真正NaN仍沿用插值。
        # 当前导出均有限，零值只位于最低区，不能凭零值本身确认失测。
        variants["zero_as_height"] = m["depth"]  # 实际有限零值由下方原始掩码准确恢复。
        m["depth_variants"] = variants
    # 重新读取仅用于区分有限零值和非有限缺失，不改原始文件。
    if r.get("source", {}).get("format") == "lsm":
        original = r["Z"].copy()
        original[r["missing"]] = np.nan
    else:
        lines = r["path"].read_bytes().replace(b"\x00",b"").splitlines()[1:]
        original = np.array([[float(v) for v in ln.split(b"\t")[1:]] for ln in lines if ln.strip()])
        if r.get("source", {}).get("orientation", {}).get("rectified"):
            original,_=resample_height(original,~np.isfinite(original),r['source']['orientation'])
        elif r.get("source", {}).get("orientation", {}).get("transposed"):
            original = original.T
    zeros = ((r['missing'] & np.isfinite(original)) if r['source']['orientation'].get('rectified')
             and r['source']['format']=='txt' else np.isfinite(original) & (original == 0))
    r["zero_mask"] = zeros
    restored_heights=r['Zclean'].copy()
    restored_heights[zeros]=original[zeros]
    r['zero_restored_height']=restored_heights
    r['zero_restored_missing']=~np.isfinite(original)
    for m in r["res"]:
        if m is None:
            continue
        j = m["col"]
        restored = r["Zclean"][:,j].copy()
        restored[zeros[:,j]] = original[zeros[:,j],j]
        m["depth_variants"]["zero_as_height"] = _depth_minimum(
            smooth_edge(restored,SMOOTH_Y)-np.polyval(m["baseline_coeff"],y),y0)
        if zeros[:,j].any() and r.get("source", {}).get("format") != "lsm":
            m["depth_flags"].append("原始零值解释待确认")
    values = np.array([m["depth"] for m in valid])
    median = lambda v: float(np.median(v)) if len(v) else float("nan")
    mid = median(values)
    scale = float(1.4826*np.median(abs(values-mid))) if values.size else 0.0
    candidates = []
    for m in valid:
        if scale > 0 and abs(m["depth"]-mid) > 3*scale:
            m["depth_flags"].append("整体3MAD候选（保留）")
            candidates.append(m["col"])
    # 单列局部突跳只标记；不自动把连续深区当成失测。
    by_col = {m["col"]:m for m in valid}
    for j,m in by_col.items():
        if j-1 in by_col and j+1 in by_col:
            a,b = by_col[j-1]["depth"],by_col[j+1]["depth"]
            limit = max(.1,6*m["noise"])
            if abs(a-b) < limit/2 and min(abs(m["depth"]-a),abs(m["depth"]-b)) > limit:
                m["depth_flags"].append("深度孤立列突跳候选（保留）")
    counts = Counter(m["depth_reason"] or "深度质量通过" if m else "平台不足或无有限剖面" for m in r["res"])
    r.update(depth_legacy=r["depth"], depth_valid=valid, n_depth=len(valid),
             depth=(mid,float(values.mean()) if values.size else float("nan"),
                    float(values.std(ddof=1)) if values.size>1 else (0.0 if values.size else float("nan"))),
             q_depth=tuple(float(v) for v in np.percentile(values,[25,75])) if values.size else (float("nan"),)*2,
             depth_all=median(all_depths), n_depth_outliers=len(candidates), depth_candidates=candidates,
             depth_without_candidates=median([m["depth"] for m in valid if m["col"] not in candidates]),
             depth_sensitivity={k:median([m["depth_variants"][k] for m in valid]) for k in DEPTH_VARIANTS},
             depth_reasons=dict(counts), zero_pixels=int(zeros.sum()))
    alternative = [m["depth_variants"]["zero_as_height"] for m in r["res"] if m and
                   not _depth_quality(dict(m,depth=m["depth_variants"]["zero_as_height"]),
                                      ~np.isfinite(original[:,m["col"]]),y0)]
    r["zero_policy_alternative"] = dict(median_um=median(alternative),n_valid=len(alternative))


def plot_depth_v3_file(r, out_png):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei","SimHei","DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig,axes = plt.subplots(2,2,figsize=(13,9))
    am,ap,ad,ah = axes.flat
    x = r["x"]-r["x"][0]
    y = np.arange(r["Z"].shape[0],dtype=float)
    residual = np.full_like(r["Z"],np.nan)
    for m in r["res"]:
        if m:
            residual[:,m["col"]] = r["Z"][:,m["col"]]-np.polyval(m["baseline_coeff"],y)
    im = am.imshow(residual,extent=(x[0],x[-1],y[-1]*r["dy"],0),aspect="auto",cmap="RdBu_r",vmin=-6,vmax=1)
    fig.colorbar(im,ax=am,label="相对平台高度（µm），六组同色标")
    am.set(title="去除局部平台后的形貌；连续深区保留",xlabel="X（µm）",ylabel="Y（µm）")
    valid = r["depth_valid"]
    if valid:
        rep = min(valid,key=lambda m:abs(m["depth"]-r["depth"][0]))
        extreme = max(valid,key=lambda m:m["depth"])
        for m,color,label in ((rep,"C0","中位深度代表列"),(extreme,"C3","最深有效列")):
            j = m["col"]
            d = r["Z"][:,j]-np.polyval(m["baseline_coeff"],y)
            ap.plot(y*r["dy"],d,color=color,alpha=.3,lw=.7)
            ap.plot(y*r["dy"],r["Zs"][:,j]-np.polyval(m["baseline_coeff"],y),color=color,label=f"{label} {j}：{m['depth']:.2f} µm")
            ap.scatter(m["yc_um"],-m["depth"],color=color,s=20)
            am.axvline(x[j],color=color,lw=.8,ls="--")
        ap.set_xlim((r["y0"]-REF_OUT-10)*r["dy"],(r["y0"]+REF_OUT+10)*r["dy"])
    ap.axhline(0,c="k",ls=":",lw=1)
    ap.set(title="原始剖面（浅色）与9像素平滑（实线）",xlabel="Y（µm）",ylabel="相对平台高度（µm）")
    if valid: ap.legend(fontsize=8)
    for m in r["res"]:
        if m and not m["depth_valid"]:
            ad.plot(x[m["col"]],m["depth"],"x",color=".65",ms=3)
    ad.plot(x[r["cols"]],[m["depth"] if m and m["depth_valid"] else np.nan for m in r["res"]],
            lw=.9,label="深度质量通过（整体候选保留）")
    cand = [m for m in valid if m["col"] in r["depth_candidates"]]
    ad.scatter([x[m["col"]] for m in cand],[m["depth"] for m in cand],s=10,c="C3",label="3MAD候选（保留）")
    if valid:
        ad.axhline(r["depth"][0],c="C1",ls="--",label=f"中位数 {r['depth'][0]:.3f} µm")
        ad.axhspan(*r["q_depth"],color="C0",alpha=.1,label="P25–P75空间分布")
    ad.set(title=f"沿槽深度分布：有效 {r['n_depth']}/{r['n_all']}",xlabel="X（µm）",ylabel="槽深（µm）")
    ad.legend(fontsize=7)
    vals = [m["depth"] for m in valid]
    if vals: ah.hist(vals,bins=30,color="C0",alpha=.75)
    ah.set(title="有效列深度分布；非独立重复实验",xlabel="槽深（µm）",ylabel="列数")
    for ax in (ap,ad,ah): ax.grid(alpha=.2)
    fig.suptitle(f"{r['path'].stem} | {ALGORITHM_VERSION} | 槽深与开口宽度测量",fontsize=12)
    fig.tight_layout(rect=(0,_lsm_plot_status(fig, [r]),1,.96))
    fig.savefig(out_png,dpi=150)
    plt.close(fig)


def plot_depth_v3_comparison(results,out_png):
    import matplotlib.pyplot as plt
    fig,(ax,ass) = plt.subplots(1,2,figsize=(13,5.5))
    xx = np.arange(1,len(results)+1)
    med = np.array([r["depth"][0] for r in results])
    q = np.array([r["q_depth"] for r in results]).T
    ax.errorbar(xx,med,yerr=np.vstack((med-q[0],q[1]-med)),fmt="o-",capsize=5,label="槽深中位数及P25–P75")
    ax.plot(xx,[r["depth_without_candidates"] for r in results],"s--",label="假设排除整体3MAD候选（仅敏感性）")
    ax.set(title="功率随编号增加；不施加单调约束",xlabel="样品编号",ylabel="槽深（µm）",xticks=xx)
    for k,label in (("linear_70_165","线性平台70–165"),("quadratic_90_180","二次平台90–180"),
                    ("quadratic_110_200","二次平台110–200"),("smooth_3","3像素平滑"),("smooth_15","15像素平滑")):
        ass.plot(xx,[r["depth_sensitivity"][k] for r in results],"o-",ms=3,label=label)
    ass.plot(xx,med,"k--",lw=1.5,label="主结果：二次70–165 / 平滑9")
    ass.set(title="同一批有效列的算法敏感性",xlabel="样品编号",ylabel="深度中位数（µm）",xticks=xx)
    for a in (ax,ass): a.grid(alpha=.2); a.legend(fontsize=8)
    fig.suptitle("空间分布不是实验重复置信区间；样品间差异需独立重复实验验证",fontsize=11)
    fig.tight_layout(rect=(0,_lsm_plot_status(fig, results),1,.95))
    fig.savefig(out_png,dpi=160)
    plt.close(fig)


def write_depth_audit(results,path):
    def finite(v):
        return float(v) if np.isfinite(v) else None
    data = []
    for r in results:
        data.append(dict(file=r["path"].name, depth_median_um=finite(r["depth"][0]),
                         q_depth=[finite(v) for v in r["q_depth"]],
                         depth_legacy_um=finite(r["depth_legacy"][0]),depth_all_um=finite(r["depth_all"]),
                         without_candidates_um=finite(r["depth_without_candidates"]),
                         n_valid=r["n_depth"],n_all=r["n_all"],zero_pixels=r["zero_pixels"],
                         zero_policy_alternative=dict(median_um=finite(r["zero_policy_alternative"]["median_um"]),
                                                      n_valid=r["zero_policy_alternative"]["n_valid"]),
                         candidates_zero_based=r["depth_candidates"],reasons=r["depth_reasons"],
                         sensitivity={k:finite(v) for k,v in r["depth_sensitivity"].items()},
                         columns=[dict(col=m["col"],depth_um=m["depth"],valid=m["depth_valid"],
                                       reason=m["depth_reason"],flags=m["depth_flags"],noise_um=m["noise"],
                                       baseline_coeff=m["baseline_coeff"].tolist(),yc_px=m["yc"],
                                       variants=m["depth_variants"]) if m else None for m in r["res"]]))
        if r.get("source", {}).get("format") == "lsm":
            data[-1]["source"] = r["source"]
    path.write_text(json.dumps(dict(algorithm=ALGORITHM_VERSION,baseline="quadratic70-165",smooth_y=SMOOTH_Y,
                                   files=data),ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")


def _depth_selftest():
    m = dict(depth=1.5,noise=.02,yc=256.,valid=False,reason="槽壁交点缺失")
    mask = np.zeros(512,bool)
    assert _depth_quality(m,mask,256)=="", "深度不应依赖宽度交点"
    assert _depth_quality(dict(m,yc=301.),mask,256)=="槽底位于搜索边界"
    mask[256]=True
    assert _depth_quality(m,mask,256)=="槽底平滑支撑含插值点"
    yy=np.arange(512,dtype=float)
    base=-np.maximum(0,np.minimum(1.5,(40-abs(yy-256))*.1))
    z=np.tile((5+base)[:,None],(1,17))
    z[250:263,5:12]-=1.0
    clean,repair=clean_isolated_pixels(z,np.zeros_like(z,bool))
    assert np.allclose(clean,z) and not repair.any(), "连续深区不能被作为孤立尖点修复"
    for depth in (.3,1.,2.,5.):
        d=-np.maximum(0,np.minimum(depth,(40-abs(yy-256))*.2))
        v=_depth_minimum(smooth_edge(d,9),256)
        assert abs(v-depth)<.05*depth,("已知槽深反演误差",depth,v)
    print("  深度自检：0.3/1/2/5 µm已知槽深误差<5%；宽度缺失独立；边界/缺失识别；连续深区保留")


def plot_file(r, out_png):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"]=["Microsoft YaHei","SimHei","DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"]=False
    fig,axes=plt.subplots(2,2,figsize=(13,9))
    am,ap,ad,aw=axes.flat
    paper=r["paper"]; summary=paper["summary"]
    x=r["x"]-r["x"][0]
    y=np.arange(r["Z"].shape[0])*r["dy"]
    lo,hi=np.nanpercentile(r["Z"],[2,98])
    im=am.imshow(r["Z"],extent=(x[0],x[-1],y[-1],0),aspect="auto",cmap="terrain",vmin=lo,vmax=hi)
    fig.colorbar(im,ax=am,label="原始插值高度 Z（µm）")
    for strip in paper["strips"]:
        color=f"C{strip['group']%10}"
        selected=strip['depth_selected'] or strip['width_selected']
        if selected:
            columns=strip['columns']
            am.axvspan(x[columns[0]],x[columns[-1]],color=color,alpha=.20)
            if 'smoothed_residual_um' in strip:
                ap.plot(y,strip['smoothed_residual_um'],color=color,lw=.9,alpha=.8)
                if strip['width_selected']:
                    ap.scatter(np.array([strip['left_open_px'],strip['right_open_px']])*r['dy'],[0,0],s=12,color=color)
        for metric,ax,key,valid in (("depth",ad,"depth_um","valid"),("width",aw,"width_um","width_valid")):
            value=strip[key]
            if value is None: continue
            excluded=strip[metric+'_excluded']
            chosen=strip[metric+'_selected']
            ax.plot(x[strip['center_column']],value,'o' if chosen else 'x',
                    color=color if chosen else 'C3' if excluded else '.70',
                    alpha=1 if chosen else .45,ms=5 if chosen else 3)
    for metric,ax in (("depth",ad),("width",aw)):
        mean=summary[metric+'_mean_um']
        if mean is not None:
            ax.axhline(mean,color='k',ls='--',label=f"入选均值 {mean:.3f} µm")
            ax.legend(fontsize=8)
        ax.set_title(f"{metric} 入选 {summary['n_'+metric]}/{summary['n_planned']}；备用候选区 {summary[metric+'_replacements']}")
        ax.set_xlabel('沿槽位置 X（µm）')
        ax.set_ylabel('槽深（µm）' if metric=='depth' else '基准面开口宽度（µm）')
        ax.grid(alpha=.2)
    am.set(title="入选条带；每区每指标最多一个位置",xlabel="X（µm）",ylabel="Y（µm）")
    ap.axhline(0,c='k',ls=':',lw=1)
    ap.set_xlim(max(0,(r['y0']-100)*r['dy']),min(y[-1],(r['y0']+100)*r['dy']))
    ap.set(title="入选平均剖面；基准线上的点为开口交点",xlabel="Y（µm）",ylabel="相对平台高度（µm）")
    ap.grid(alpha=.2)
    fig.suptitle(f"{r['path'].stem} | {ALGORITHM_VERSION} | 分区随机取样；圆点入选，灰叉备用/无效，红叉统计排除",fontsize=11)
    fig.tight_layout(rect=(0,_lsm_plot_status(fig, [r]),1,.96))
    fig.savefig(out_png,dpi=150)
    plt.close(fig)


def plot_depth_comparison(results,out_png):
    import matplotlib.pyplot as plt
    fig,(ax,asens)=plt.subplots(1,2,figsize=(13,5.6))
    xx=np.arange(1,len(results)+1)
    get=lambda value: np.nan if value is None else value
    means=np.array([get(r["paper"]["summary"]["depth_mean_um"]) for r in results])
    ax.plot(xx,means,"o-",color="C0",label="分区平均剖面槽深 均值")
    for i,r in enumerate(results):
        p=r["paper"]
        val=p["summary"]["depth_mean_um"]
        if val is not None:
            ax.errorbar(xx[i],val,yerr=p["summary"]["depth_position_sd_um"],color="C0",capsize=4)
        for s in p["strips"]:
            if s["depth_um"] is not None:
                ax.plot(xx[i]+(s["fraction"]-.5)*.25,s["depth_um"],"." if s["valid"] else "x",color="C0",alpha=.5,ms=6)
    ax.plot(xx,[r["depth"][0] for r in results],"s--",color=".5",label="v3：逐剖面最大深度的空间中位数")
    ax.plot(xx,[r["paper"]["central_80pct"]["depth_um"] if r["paper"]["central_80pct"]["valid"] else np.nan for r in results],"^:",color="C2",label="中央80%平均剖面（辅助）")
    ax.set(title="处理顺序和取样定义改变；误差棒为入选位置SD",xlabel="样品编号",ylabel="槽深（µm）",xticks=xx)
    for key,label in (("half_width","半宽条带"),("double_width","双宽条带"),
                      ("linear_baseline","线性平台基准"),("outer_baseline","外移平台区90–180"),
                      ("smooth_1","不平滑"),("smooth_5","5像素平滑")):
        asens.plot(xx,[get(r["paper"]["sensitivity"][key]["summary"]["depth_mean_um"]) if key in r["paper"]["sensitivity"] else np.nan for r in results],"o-",ms=3,label=label)
    asens.plot(xx,means,"k--",lw=1.5,label="默认分区取样主结果")
    asens.set(title="方法敏感性；固定主取样位置和入选集",xlabel="样品编号",ylabel="分区平均槽深（µm）",xticks=xx)
    for a in (ax,asens):a.grid(alpha=.2);a.legend(fontsize=8)
    fig.suptitle("文献启发的平均剖面法；条带宽度与取样位置为本项目参数",fontsize=11)
    fig.tight_layout(rect=(0,_lsm_plot_status(fig, results),1,.95))
    fig.savefig(out_png,dpi=160)
    plt.close(fig)


def _transition_selftest():
    """解析弯曲槽壁与干扰反例；备用定位不能通过放松直线残差得到。"""
    y = np.arange(512, dtype=float)
    for power in (1.4, 2., 3.):
        for shift in (0., .3, .7):
            distance = np.minimum(y-(220+shift), (292+shift)-y)
            d = -np.minimum(20., .2*np.maximum(distance, 0)**power)
            opening = measure_opening(d, 256, .001)
            assert opening['valid'], "有平坦平台的连续弯曲陡壁应可定位"
            assert abs(opening['right']['pos']-opening['left']['pos']-72) < 2
            for side, truth in (("left", 220+shift), ("right", 292+shift)):
                wall = opening[side]
                assert abs(wall['pos']-truth) < 1.5
                assert abs(wall['pos']-truth) <= wall['uncertainty_px']
    for d, boundary in ((-.02*(y-220), 221),
                        (np.where(y < 220, 1., -(y-220)), 221),
                        (np.where(y == 221, -1., 0.), 221)):
        assert _locate_supported_transition(d, boundary, 1, .001)[0] is None
    # 凹陷位于参考槽心之外，仍须被邻槽保护拒绝。
    neighbor = -np.minimum(20., .2*np.maximum(np.minimum(y-270, 300-y), 0)**2)
    assert not measure_opening(neighbor, 256, .001)['valid']
    # 定位区的插补高度不能算真实支撑，必须在平均剖面层拒绝。
    from profile_measurement import measure_strip
    d = -np.minimum(20., .2*np.maximum(np.minimum(y-220, 292-y), 0)**2)
    z = np.tile(d[:, None]+25, (1, 31))
    missing = np.zeros(z.shape, bool)
    missing[216:225] = True
    strip = measure_strip(np.arange(31.), z, missing, 256, 1., robust_poly,
                          measure_opening, fraction=.5)
    assert not strip['width_valid'] and strip['width_reason'] == '槽口拟合真实支撑不足'
    print('  弯曲陡壁自检：9种已知宽度/亚像素位置、定位尺度、缓坡/凸起/坏点/邻槽/缺失保护通过')


def _selftest():
    """合成已知槽形，检查深度/宽度反演误差（<5%）。"""
    n, dy = 512, 0.63
    depth, a, b = 1.5, 8.0, 25.0
    y = np.arange(n) * dy
    c_um = 160.0
    t = np.abs(y - c_um)
    prof = np.where(t <= a, -depth,
                    np.where(t < b, -depth * (b - t) / (b - a), 0.0))
    truth = {0.1: 2 * (a + 0.9 * (b - a)),
             0.5: 2 * (a + 0.5 * (b - a)),
             0.9: 2 * (a + 0.1 * (b - a))}
    base = 5.0 + 0.004 * y - 1e-5 * (y - c_um) ** 2
    rng = np.random.default_rng(0)
    p = smooth_edge(base + prof + rng.normal(0, 0.02, n), SMOOTH_Y)
    r = measure_column(p, dy, int(round(c_um / dy)))
    assert r is not None and r["valid"], "自检失败: 未检出槽"
    checks = [("槽深", r["depth"], depth), ("顶部宽", r["w_top"], truth[0.1]),
              ("半深宽", r["w_half"], truth[0.5]), ("底部宽", r["w_bottom"], truth[0.9])]
    bad = [(k, v, t) for k, v, t in checks if abs(v - t) > 0.05 * t]
    for k, v, t in checks:
        print(f"  自检 {k}: 反演 {v:.3f} / 真值 {t:.3f} um （偏差 {100*(v-t)/t:+.2f}%）")
    if bad:
        print("[自检失败]", bad)
        return 1
    assert r["opening"]["valid"], "开口自检：未检测到已知槽口"
    measured = (r["opening"]["right"]["pos"] - r["opening"]["left"]["pos"]) * dy
    assert abs(measured - 2 * b) < 0.05 * 2 * b, ("开口宽误差超过5%", measured)
    print(f"  开口自检：反演 {measured:.3f} / 真值 {2*b:.3f} um")
    yy = np.arange(n, dtype=float)
    clean = np.where(abs(yy - 256) < 40, -np.minimum(1.5, (40 - abs(yy - 256)) * 0.06), 0.0)
    first = measure_opening(clean, 256, .01)
    pit = clean - 8 * np.exp(-((yy - 256) / 3) ** 2)
    second = measure_opening(pit, 256, .01)
    assert first["valid"] and second["valid"], "槽底深坑自检：未检出开口"
    widths = [(v["right"]["pos"] - v["left"]["pos"]) * dy for v in (first, second)]
    assert abs(widths[0] - widths[1]) < dy, ("深坑改变开口", widths)
    multi = clean.copy()
    multi[abs(yy - 256) < 5] = -.20
    multi_open = measure_opening(multi, 256, .01)
    assert multi_open["valid"], "同一开口内多谷自检失败"
    assert abs((multi_open["right"]["pos"] - multi_open["left"]["pos"]) * dy - widths[0]) < dy
    split = -np.maximum(0, np.minimum(1, (12 - abs(yy - 240)) / 5))
    split -= np.maximum(0, np.minimum(1, (12 - abs(yy - 268)) / 5))
    assert not measure_opening(split, 254, .01)["valid"], "分裂槽应标为不可靠"
    assert not measure_opening(np.zeros(n), 256, .01)["valid"], "平面应无开口"
    assert measure_column(np.full(n, np.nan), dy, 256) is None, "全缺失列应跳过"
    missing = np.zeros(n, bool)
    missing[220:230] = True
    assert _quality_missing(missing, 210, 300), "长缺失段应被识别"
    z = np.tile((5 + clean)[:, None], (1, 17))
    z[256, 8] -= 15
    cleaned, repair = clean_isolated_pixels(z, np.zeros_like(z, bool))
    assert repair[256, 8] and abs(cleaned[256, 8] - cleaned[256, 7]) < .01, "孤立负尖点应被识别并修复"
    triangle = 5 - np.maximum(0, 1 - abs(yy - 256) / 21.3) * 2
    tri = measure_column(triangle, dy, 256)
    assert abs(tri["w_half"] - 21.3 * dy) < 1e-9, "交点插值精度自检失败"
    print(f"  深坑不改开口：{widths[0]:.3f} / {widths[1]:.3f} um")
    _depth_selftest()
    profile_selftest(robust_poly, measure_opening)
    sampling_selftest(robust_poly, measure_opening)
    _transition_selftest()
    orientation_selftest(robust_poly,measure_opening)
    from groove_local import selftest as local_selftest
    local_selftest(robust_poly,measure_opening)
    print("[自检通过] 深度独立质量检查、搜索边界、缺失、连续深区保护；开口及辅助宽度回归")
    return 0


# ---------------------------------------------------------------- 主流程
def find_inputs(data_dir: Path, input_format="auto", samples=None):
    return discover_inputs(data_dir, input_format, samples)[0]


def clean_output_directory(out_root: Path, input_dir: Path):
    """核对边界并预检删除权限，再清空out内容；不跟随任何链接。"""
    import os
    import stat

    root = Path(os.path.abspath(out_root))
    if root.name.lower() != "out" or root.parent == root:
        raise ValueError("自动清理仅允许指定模块下名为out的目录")

    def is_link(path):
        info = path.lstat()
        return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0)
                                        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))

    if root.exists() or root.is_symlink():
        if is_link(root) or not root.is_dir():
            raise ValueError(f"out必须为普通目录，不能是文件或目录链接：{root}")
    if root.resolve() != root.parent.resolve() / "out":
        raise ValueError(f"out解析路径与预期边界不符：{root}")
    source = input_dir.resolve()
    resolved = root.resolve()
    if source == resolved or source.is_relative_to(resolved) or resolved.is_relative_to(source):
        raise ValueError("输入目录与out不能重叠，已停止清理")
    if not root.exists():
        root.mkdir()
        return 0

    files, directories = [], []

    def collect(folder):
        for path in folder.iterdir():
            if is_link(path):
                raise ValueError(f"out中包含链接，已停止清理：{path}")
            if not path.resolve().is_relative_to(resolved):
                raise ValueError(f"清理路径越界：{path}")
            if path.is_dir():
                collect(path)
                directories.append(path)
            elif path.is_file():
                if os.name == "nt" and path.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY:
                    raise PermissionError(f"out中有只读文件，请解除只读后再运行：{path}")
                files.append(path)
            else:
                raise ValueError(f"out中包含不支持的文件类型：{path}")

    collect(root)
    if os.name == "nt":
        # 在删除任何旧文件前，检查全部条目的DELETE权限与Windows共享锁。
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        invalid = wintypes.HANDLE(-1).value
        for path in files + directories:
            handle = kernel.CreateFileW(str(path), 0x10000, 0x7, None, 3, 0x02000000, None)
            if handle == invalid:
                error = ctypes.get_last_error()
                raise PermissionError(f"无法清理旧结果，请关闭占用它的Excel/程序并检查权限：{path}") from ctypes.WinError(error)
            kernel.CloseHandle(handle)
    # 根目录保留；子目录已按从内到外排序。不使用跨边界的递归删除。
    for path in files:
        path.unlink()
    for path in directories:
        path.rmdir()
    return len(files)


def main(argv=None):
    ap = argparse.ArgumentParser(description="CLSM 三维形貌槽宽/槽深批量测量")
    ap.add_argument("--dir", default=None, help="输入目录（默认脚本同级 data）")
    ap.add_argument("--out", default=None, help="图表与审计输出目录（默认 results/out/paper_v5）；指定时2D/3D图也写入该目录")
    ap.add_argument("--no-plot", action="store_true", help="只出 Excel，不导出示意图")
    ap.add_argument("--selftest", action="store_true", help="运行合成数据自检")
    ap.add_argument("--groove-axis", choices=("auto", "horizontal", "vertical"), default="auto",
                    help="默认auto识别直槽并在物理XY中校正，沿槽X/法向Y；horizontal/vertical仅限制主方向，仍校正斜切")
    ap.add_argument("--clean-out", action="store_true", help="写入结果前清空本模块 results/out；启动批处理自动启用")
    ap.add_argument("--xlsx", default=None, help="Excel 保存位置（默认脚本同级 results/groove_paper_results.xlsx）")
    ap.add_argument("--dy", type=float, default=None, help="原始Y步距（µm），在纵槽交换XY之前应用；TXT省略时按X步距假定；LSM省略时使用文件Y标定")
    ap.add_argument("--strip-width-um",type=float,default=STRIP_WIDTH_UM,help="平均剖面条带物理宽度µm，默认12.6")
    ap.add_argument("--profile-positions",type=float,nargs='+',default=None,help="可选固定初始位置，0–1严格递增；无效候选仍在各自分区内补测")
    ap.add_argument("--profile-count",type=int,default=DEFAULT_COUNT,help="分层随机分区数，默认9；固定位置模式以传入位置数为准")
    ap.add_argument("--random-seed",type=int,default=DEFAULT_SEED,help="随机种子，默认20261029；与目录文件数量/处理顺序无关")
    ap.add_argument("--max-attempts",type=int,default=DEFAULT_ATTEMPTS,help="每区最多预设候选数，默认4；不无限重抽")
    add_input_arguments(ap)
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    here = Path(__file__).resolve().parent
    data_dir = Path(args.dir) if args.dir else here / "data"
    out_dir = Path(args.out) if args.out else here / "results" / "out" / "paper_v5"
    xlsx = Path(args.xlsx) if args.xlsx else here / "results" / "groove_paper_results.xlsx"
    try:
        files, ignored = discover_inputs(data_dir, args.input_format, args.samples)
    except ValueError as exc:
        print(f"[错误] {exc}")
        return 2
    if not files:
        print(f"[错误] 目录中没有所选 TXT/LSM 数据: {data_dir}")
        return 2

    print(f"输入目录: {data_dir}")
    print(f"待处理文件: {len(files)} 个\n")

    if args.profile_count < 1 or args.random_seed < 0 or args.max_attempts < 1:
        ap.error("分区数、候选次数须为正整数，随机种子须非负")
    if args.lsm_height_scale_um is None and args.lsm_height_offset_um != 0:
        ap.error("LSM高度偏移须与 --lsm-height-scale-um 配套指定")
    for path, reason in ignored:
        print(f"[输入筛选] {path.name}: {reason}")
    # 在清理或写入前检查全部LSM；默认显式记录跳过项，不将未知高度当作有效输入。
    preloaded, preflight_errors = {}, []
    for path in files:
        if path.suffix.casefold() == ".lsm":
            try:
                preloaded[path] = read_3d(path, return_source=True, **lsm_options(args))
            except (ValueError, OSError) as exc:
                preflight_errors.append((path.name, str(exc)))
                continue
            print(f"[LSM] {path.name}: {preloaded[path][4]['height_status']}")
    if preflight_errors:
        for name, reason in preflight_errors:
            print(f"[{'错误' if args.strict_inputs else '跳过'}] {name}: {reason}")
        if args.strict_inputs or len(preflight_errors) == len(files):
            print(f"[预检查停止] {len(preflight_errors)}/{len(files)} 个输入尚不可定量；原始数据、旧out及工作簿未改动。")
            return 6
        failed_names = {name for name, _ in preflight_errors}
        files = [path for path in files if path.name not in failed_names]
        print(f"[预检查] 跳过 {len(preflight_errors)} 个不可处理输入，继续分析 {len(files)} 个文件。")
    results, skipped = [], ([(str(p.relative_to(data_dir)), reason) for p, reason in ignored]
                            + preflight_errors)
    for p in files:
        try:
            r = analyse_file(p, dy_override=args.dy, strip_width_um=args.strip_width_um, profile_positions=args.profile_positions,
                             profile_count=args.profile_count,random_seed=args.random_seed,max_attempts=args.max_attempts,
                             input_data=preloaded.get(p), groove_axis=args.groove_axis, **lsm_options(args))
        except Exception as exc:                          # noqa: BLE001
            print(f"[跳过] {p.name}: {exc}")
            skipped.append((p.name,str(exc)))
            continue
        results.append(r)
        print(f"[方向] {p.name}: {orientation_note(r['source'])}")
        paper=r["paper"]["summary"]
        value=paper["depth_mean_um"]
        width=paper["width_mean_um"]
        text=f"{value:.3f} um" if value is not None else "不可汇总（见状态）"
        wtext=f"{width:.3f} um" if width is not None else "不可汇总（见状态）"
        print(f"  {p.name:>12}  平均剖面深度 {text}  入选 {paper['n_depth']}/{paper['n_planned']}  "
              f"开口宽度 {wtext}  入选 {paper['n_width']}/{paper['n_planned']}  v3对照 {r['depth'][0]:.3f} um")
        if value is None or width is None:
            print(f"    槽深：{paper['depth_status']}；槽宽：{paper['width_status']}")
        if r["n_open"] < r["n_all"] / 2:
            print("    [注意] 可靠开口不足一半，请检查边界图和逐列原因。")

    if not results:
        print("[错误] 没有任何文件测量成功")
        return 3

    if args.clean_out:
        try:
            removed = clean_output_directory(here / "results" / "out", data_dir)
        except (OSError, ValueError) as exc:
            print(f"[错误] 旧结果清理失败，停止写入本次结果：{exc}")
            return 5
        print(f"\n已清理out中的 {removed} 个旧文件。")
        # 旧版本把工作簿写在模块根目录，残留会让人误读成本次结果
        for name in ("groove_results.xlsx", "~$groove_results.xlsx"):
            stale = here / name
            if stale.exists() and stale.resolve() != xlsx.resolve():
                try:
                    stale.unlink()
                    print(f"  已删除旧版工作簿 {stale.name}")
                except OSError as exc:
                    print(f"  [注意] 旧版工作簿 {stale.name} 未能删除（{exc}），请手动删除，勿以其为准。")
    out_dir.mkdir(parents=True, exist_ok=True)
    xlsx.parent.mkdir(parents=True, exist_ok=True)

    print("\n写入 Excel ...")
    ok = write_excel(results, xlsx, skipped)
    if not ok:
        return 4
    print(f"  {xlsx}")

    if not args.no_plot:
        out_dir.mkdir(parents=True, exist_ok=True)
        made = set()
        for r in results:
            png = out_dir / f"{r['path'].stem}.png"
            plot_file(r, png)
            plot_orientation(r,out_dir / f"{r['path'].stem}_orientation.png")
            made.add(png.name)
        plot_depth_comparison(results, out_dir / "depth_comparison.png")
        print(f"  深度诊断图 {len(made) + 1} 张 -> {out_dir}")
        from surface_plots import plot_surfaces
        surface_root = out_dir if args.out else here / "results" / "out"
        plot_surfaces(results, surface_root)
        print(f"  二维/三维形貌图各 {len(results)} 张 -> {surface_root / '2D'}、{surface_root / '3D'}")
        from profile_plots import plot_profiles
        plot_profiles(results,surface_root)
        print(f"  二维/三维剖面图各 {len(results)} 张 -> {surface_root / '2D'}、{surface_root / '3D'}")

    for r in results:
        changed = r["repaired"]
        np.savez_compressed(out_dir / f"{r['path'].stem}_quality_masks.npz",
                            missing=r["missing"], repaired=changed,
                            repaired_original=r["Z"][changed], repaired_values=r["Zclean"][changed])

    manifest = dict(algorithm=ALGORITHM_VERSION,
                    runtime=dict(python=sys.version,numpy=np.__version__,executable=sys.executable),
                    source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    profile_module_sha256=hashlib.sha256((here/"profile_measurement.py").read_bytes()).hexdigest(),
                    sampling_module_sha256=hashlib.sha256((here/"profile_sampling.py").read_bytes()).hexdigest(),
                    profile_plot_module_sha256=hashlib.sha256((here/"profile_plots.py").read_bytes()).hexdigest(),
                    input_module_sha256=hashlib.sha256((here/"clsm_inputs.py").read_bytes()).hexdigest(),
                    lsm_reader_sha256=hashlib.sha256((here/"inspect_zen_lsm.py").read_bytes()).hexdigest(),
                    orientation_module_sha256=hashlib.sha256((here/"groove_orientation.py").read_bytes()).hexdigest(),
                    local_module_sha256=hashlib.sha256((here/'groove_local.py').read_bytes()).hexdigest(),
                    input_selection=dict(mode=args.input_format, same_name_priority="txt", samples=args.samples,
                                         invalid_lsm_policy="stop" if args.strict_inputs else "skip"),
                    ignored_inputs=[dict(file=str(p.relative_to(data_dir)),reason=reason) for p,reason in ignored],
                    skipped_inputs=[dict(file=name,reason=reason) for name,reason in skipped],
                    parameters=dict(smooth_y=SMOOTH_Y, ref_in=REF_IN, ref_out=REF_OUT,
                                    opening_drop_um=OPEN_DROP_UM, opening_noise_k=OPEN_NOISE_K,
                                    opening_run=OPEN_RUN, opening_gap=OPEN_GAP, dy_override=args.dy,
                                    opening_center_margin_px=OPEN_CENTER_MARGIN_PX,
                                    depth_min_um=MIN_DEPTH_UM, depth_min_snr=MIN_SNR, center_search=CENTER_SEARCH,
                                    zero_as_missing=ZERO_AS_MISSING, depth_outlier_mad=3,
                                    sensitivity_fixed_selection=True, groove_axis=args.groove_axis, **lsm_options(args)),
                    files=[dict(name=r["path"].name, input_sha256=hashlib.sha256(r["path"].read_bytes()).hexdigest(),
                                dy_um=r["dy"], dy_assumed=r["dy_assumed"], source=r["source"],
                                opening_median_um=r["w_open"][0] if r["n_open"] else None,
                                opening_p25_um=r["q_open"][0] if r["n_open"] else None,
                                opening_p75_um=r["q_open"][1] if r["n_open"] else None,
                                depth_median_um=r["depth"][0] if r["n_depth"] else None,
                                paper_summary=jsonable(r["paper"]["summary"]),paper_parameters=jsonable(r["paper"]["parameters"]),
                                n_depth=r["n_depth"], depth_p25_um=r["q_depth"][0] if r["n_depth"] else None,
                                depth_p75_um=r["q_depth"][1] if r["n_depth"] else None,
                                depth_reasons=r["depth_reasons"], depth_candidates=r["depth_candidates"],
                                depth_sensitivity={k:v if np.isfinite(v) else None for k,v in r["depth_sensitivity"].items()},
                                n_open=r["n_open"], n_open_before=r["n_open_before"], n_all=r["n_all"],
                                missing_pixels=int(r["missing"].sum()), repaired_pixels=int(r["repaired"].sum()),
                                opening_reasons=r["opening_reasons"]) for r in results])
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    write_depth_audit(results, out_dir / "depth_audit.json")
    paper_audit=dict(algorithm=ALGORITHM_VERSION,source_sha256=manifest["source_sha256"],
                     profile_module_sha256=hashlib.sha256((here/"profile_measurement.py").read_bytes()).hexdigest(),
                     sampling_module_sha256=manifest["sampling_module_sha256"],
                     files=[dict(file=r["path"].name,**r["paper"],
                                 source=r["source"]) for r in results])
    (out_dir/"paper_profile_audit.json").write_text(json.dumps(jsonable(paper_audit),ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    print("完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

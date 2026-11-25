# -*- coding: utf-8 -*-
"""纳米压痕数据读取与统计核心。

数据来源为 MTS Nano Indenter 的 TestWorks 导出：
  *.xls   Results 表（仪器已算好的 E/H）+ Test NNN 表（载荷-位移-时间原始曲线）
  *.rtf   同源测试报告，仅用于提取方法/操作者等溯源信息
  *.mss   仪器原始二进制（OLE 复合文档），本模块不直接解析，保留原文件即可

核心原则：
  1. E/H 以仪器 Results 表为准（它用了标定过的针尖面积函数），曲线用于 QA 与机理线索；
  2. 统计检验不依赖 scipy，配对用精确符号翻转置换检验，独立组用固定种子置换检验；
  3. 先找分组差异再求平均，避免把两类材料平均成一个没有意义的数。
"""
from __future__ import annotations

import itertools
import json
import math
import re
from pathlib import Path

import numpy as np
import xlrd

# 文件名: 序号-样品编号-载荷-加载速率-保载时间-测点号
NAME_RE = re.compile(
    r"^(?P<seq>\d+)-(?P<sample>\d{3,4})-(?P<load>\d+(?:\.\d+)?)\s*m[nN]-"
    r"(?P<rate>\d+(?:\.\d+)?)\s*mns?-(?P<hold>\d+(?:\.\d+)?)\s*s-"
    r"(?P<point>\d+)$",
    re.IGNORECASE,
)

SENTINEL = 1.7976931348623157e30  # TestWorks 用这个值表示"无数据"


def load_config(path: str | Path | None = None) -> dict:
    """读取配置；未给路径时用模块自带 config.json。"""
    if path is None:
        path = Path(__file__).resolve().parent / "config.json"
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def parse_filename(name: str) -> dict:
    """解析文件名为实验条件元数据；解析失败时返回原始名，不中断整批分析。"""
    stem = Path(name).stem
    m = NAME_RE.match(stem)
    if not m:
        return {"source": stem, "name_ok": False, "point": None, "sample": None,
                "load_mn": None, "rate_mn_s": None, "hold_s": None, "seq": None}
    return {
        "source": stem,
        "name_ok": True,
        "seq": int(m.group("seq")),
        "sample": int(m.group("sample")),
        "load_mn": float(m.group("load")),
        "rate_mn_s": float(m.group("rate")),
        "hold_s": float(m.group("hold")),
        "point": int(m.group("point")),
    }


def natural_key(name):
    """文件名自然排序：2-1 排在 10-1 前，数字段按整数比较。"""
    text = str(name)
    key = []
    for part in re.split(r"(\d+)", text):
        if part.isdigit():
            key.append((0, int(part), len(part)))
        else:
            key.append((1, part.casefold()))
    return (key, text.casefold())


def discover_xls(data_dir: str | Path) -> list[Path]:
    """按文件名序号排序地列出输入目录中的 xls（忽略 Office 临时文件）。"""
    folder = Path(data_dir)
    if not folder.is_dir():
        raise FileNotFoundError(f"输入目录不存在: {folder}")
    files = [p for p in folder.glob("*.xls") if not p.name.startswith("~$")]
    files.sort(key=lambda p: (parse_filename(p.name).get("seq") or 10**9,
                              natural_key(p.name)))
    return files


def _f(value) -> float | None:
    """把单元格转成有限浮点；哨兵/文本/空值返回 None。"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v >= SENTINEL / 2 or abs(v) > SENTINEL / 2:
        return None
    return v


def read_results(path: str | Path) -> dict:
    """读 Results 工作表：每个压痕的 E/H/漂移/最大深度/最大载荷等。"""
    wb = xlrd.open_workbook(str(path))
    if "Results" not in wb.sheet_names():
        raise ValueError(f"{Path(path).name}: 缺少 Results 工作表")
    sh = wb.sheet_by_name("Results")
    header = [str(sh.cell_value(0, c)).strip() for c in range(sh.ncols)]
    units = [str(sh.cell_value(1, c)).strip() for c in range(sh.ncols)]

    def col(key: str) -> int | None:
        for i, h in enumerate(header):
            if key.lower() in h.lower():
                return i
        return None

    idx = {
        "test": col("Test"),
        "E": col("Modulus At Max Load"),
        "H": col("Hardness At Max Load"),
        "drift": col("Drift Correction"),
        "hmax": col("Disp at Max Load"),
        "Pmax": col("Load At Max Load"),
        "time": col("Time At Start"),
        "tip": col("Tip Name"),
        "area_c1": col("Area Coefficient 1"),
        "area_c2": col("Area Coefficient 2"),
    }
    indents, instrument = [], {}
    for r in range(2, sh.nrows):
        first = str(sh.cell_value(r, 0)).strip()
        low = first.lower()
        if low.startswith(("mean", "std", "% cov")):
            instrument[low] = {
                "E": _f(sh.cell_value(r, idx["E"])) if idx["E"] is not None else None,
                "H": _f(sh.cell_value(r, idx["H"])) if idx["H"] is not None else None,
                "hmax": _f(sh.cell_value(r, idx["hmax"])) if idx["hmax"] is not None else None,
            }
            continue
        # 只有数字编号行才是压痕
        if not re.fullmatch(r"\d+(?:\.0)?", first):
            continue
        row = {"test_no": int(float(first))}
        for key, c in idx.items():
            if key == "test" or c is None:
                continue
            raw = sh.cell_value(r, c)
            row[key] = raw.strip() if isinstance(raw, str) and key == "tip" else _f(raw)
        indents.append(row)
    return {"header": header, "units": units, "indents": indents,
            "instrument": instrument, "sheet_names": wb.sheet_names()}


def read_curve(path: str | Path, test_no: int) -> dict | None:
    """读单个 Test NNN 工作表，按段标记切分载荷-位移-时间序列。"""
    wb = xlrd.open_workbook(str(path))
    name = f"Test {test_no:03d}"
    if name not in wb.sheet_names():
        return None
    sh = wb.sheet_by_name(name)
    h, p, t, marks = [], [], [], {}
    for r in range(2, sh.nrows):
        tag = str(sh.cell_value(r, 0)).strip()
        hv = _f(sh.cell_value(r, 1))
        pv = _f(sh.cell_value(r, 2))
        tv = _f(sh.cell_value(r, 3))
        if tag:
            marks[tag] = len(h)
        if hv is None or pv is None or tv is None:
            continue
        h.append(hv)
        p.append(pv)
        t.append(tv)
    return {"sheet": name, "h": np.asarray(h), "P": np.asarray(p),
            "t": np.asarray(t), "marks": marks}


def _segment(cur: dict, start_key: str, end_keys: tuple[str, ...]) -> tuple[int, int]:
    marks = cur["marks"]
    start = next((v for k, v in marks.items() if start_key.lower() in k.lower()), None)
    if start is None:
        return 0, 0
    ends = [v for k, v in marks.items()
            if any(e.lower() in k.lower() for e in end_keys) and v > start]
    end = min(ends) if ends else len(cur["h"])
    return start, min(end, len(cur["h"]))


def curve_metrics(cur: dict) -> dict:
    """从原始曲线提取 QA 指标与机理线索（不重新计算绝对 E/H）。"""
    h, p, t = cur["h"], cur["P"], cur["t"]
    if h.size < 10:
        return {"curve_ok": False}
    a, b = _segment(cur, "Load Segment", ("Hold Segment", "Unload", "Thermal Drift"))
    c, d = _segment(cur, "Hold Segment", ("Unload", "Thermal Drift"))
    e, f = _segment(cur, "Unload From Peak", ("Thermal Drift",))
    g, k = _segment(cur, "Thermal Drift", ("END",))
    hmax = float(np.max(h[a:d])) if d > a else float(np.max(h))
    pmax = float(np.max(p[a:d])) if d > a else float(np.max(p))
    hf = float(h[f - 1]) if f > e else (float(h[-1]) if h.size else float("nan"))

    # 峰值保载蠕变：5 s 内的位移增量
    creep = float(h[b - 1] - h[c]) if b > c + 1 else 0.0
    hold_dt = float(t[b - 1] - t[c]) if b > c + 1 else 0.0
    creep_rate = creep / hold_dt if hold_dt > 0 else 0.0

    # 热漂移段的线性斜率，与仪器给出的漂移修正值交叉核对
    drift_rate = float("nan")
    if k - g >= 5:
        tt, hh = t[g:k], h[g:k]
        if float(np.ptp(tt)) > 0:
            drift_rate = float(np.polyfit(tt, hh, 1)[0])

    # 卸载刚度：幂律 P = B (h-hf)^m 取上部 20%~95% Pmax 拟合（Oliver-Pharr 思路）
    stiffness = float("nan")
    fit_m = float("nan")
    if f - e >= 8 and hmax > hf:
        sel = (p[e:f] >= 0.2 * pmax) & (p[e:f] <= 0.95 * pmax) & (h[e:f] > hf)
        if sel.sum() >= 5:
            x = np.log(h[e:f][sel] - hf)
            y = np.log(p[e:f][sel])
            slope, intercept = np.polyfit(x, y, 1)
            if slope > 0:
                fit_m = float(slope)
                stiffness = float(math.exp(intercept) * slope *
                                  (hmax - hf) ** (slope - 1))

    # 疑似 pop-in：加载段位移突增（经验阈值，需人工复核，不作为硬结论）
    popin_jump, popin_count = 0.0, 0
    if d - a >= 20 and pmax > 0:
        seg = slice(a, d)
        ok = (p[seg] > 0.1 * pmax) & (p[seg] < 0.95 * pmax)
        dh = np.diff(h[seg])
        med = float(np.median(dh[ok[:-1]])) if ok[:-1].sum() else 0.0
        mad = float(np.median(np.abs(dh[ok[:-1]] - med))) if ok[:-1].sum() else 0.0
        thr = max(10.0, med + 8 * 1.4826 * mad)
        jumps = dh[ok[:-1] & (dh > thr)]
        if jumps.size:
            popin_count = int(jumps.size)
            popin_jump = float(np.max(jumps))

    return {
        "curve_ok": True,
        "hmax_nm": hmax,
        "Pmax_mn": pmax,
        "hf_nm": hf,
        "recovery_ratio": (hmax - hf) / hmax if hmax > 0 else float("nan"),
        "creep_hold_nm": creep,
        "creep_rate_nm_s": creep_rate,
        "drift_rate_nm_s": drift_rate,
        "stiffness_fit_n_m": stiffness,
        "unload_exponent_m": fit_m,
        "popin_count": popin_count,
        "popin_max_jump_nm": popin_jump,
        "n_points": int(h.size),
    }


def read_rtf_meta(path: str | Path) -> dict:
    """从 RTF 提取报告抬头（方法、操作者、日期），仅作溯源，不取数值。"""
    p = Path(path)
    rtf = p.with_suffix(".rtf")
    out = {"rtf_present": rtf.is_file(), "method": None, "operator": None, "date": None}
    if not rtf.is_file():
        return out
    text = rtf.read_bytes()[:20000].decode("latin-1", errors="replace")
    for key, pattern in (
        ("method", r"Method:([\\a-zA-Z0-9 \-\.\_]+?)(?:Operator:|File Name:|$)"),
        ("operator", r"Operator:([\\a-zA-Z0-9 \-\.\_]+?)(?:File Name:|$)"),
        ("date", r"Test Date:([0-9/]+)"),
    ):
        m = re.search(pattern, text)
        if m:
            out[key] = m.group(1).strip().strip("\\").strip()
    return out


def read_one(path: str | Path, want_curves: bool = True) -> dict:
    """读取单个 xls，合并文件名元数据、Results、曲线与报告溯源。"""
    meta = parse_filename(Path(path).name)
    res = read_results(path)
    curves = {}
    if want_curves:
        for row in res["indents"]:
            cur = read_curve(path, row["test_no"])
            if cur is not None:
                curves[row["test_no"]] = curve_metrics(cur)
                curves[row["test_no"]]["_raw"] = cur
    meta.update({"path": str(path), "results": res, "curves": curves,
                 "rtf": read_rtf_meta(path)})
    return meta


# ---------------------------------------------------------------- 统计工具

def mean_sd(values) -> tuple[float, float, int]:
    a = np.asarray([v for v in values if v is not None and math.isfinite(float(v))], float)
    if a.size == 0:
        return float("nan"), float("nan"), 0
    if a.size == 1:
        return float(a[0]), 0.0, 1
    return float(np.mean(a)), float(np.std(a, ddof=1)), int(a.size)


def mad_sigma(values) -> float:
    """稳健尺度估计：1.4826 × 中位数绝对偏差。"""
    a = np.asarray([v for v in values if v is not None and math.isfinite(float(v))], float)
    if a.size < 2:
        return 0.0
    med = float(np.median(a))
    return float(1.4826 * np.median(np.abs(a - med)))


def flag_outliers(values, z_thresh: float = 3.5) -> list[bool]:
    """返回每个值是否为离群点（稳健 z 分数）。"""
    a = np.asarray([float("nan") if v is None else float(v) for v in values], float)
    med = float(np.nanmedian(a))
    sigma = mad_sigma([v for v in a if math.isfinite(v)])
    if sigma <= 0:
        return [False] * a.size
    z = np.abs(a - med) / sigma
    return [bool(math.isfinite(v) and v > z_thresh) for v in z]


def paired_signflip_test(diffs, rng_seed: int = 20261122) -> dict:
    """配对精确符号翻转置换检验（n<=20 枚举全部 2^n；n>20 随机抽样）。

    原假设：配对差值来自中点为 0 的对称分布。p 为双尾。
    """
    d = np.asarray([float(v) for v in diffs if v is not None and math.isfinite(float(v))], float)
    n = d.size
    if n == 0:
        return {"n": 0, "mean": float("nan"), "p": float("nan"), "method": "无有效配对"}
    obs = float(np.mean(d))
    if n <= 20:
        signs = np.asarray(list(itertools.product([-1.0, 1.0], repeat=n)))
        stats = signs @ d / n
        p = float(np.mean(np.abs(stats) >= abs(obs) - 1e-12))
        method = "精确符号翻转置换"
    else:
        rng = np.random.default_rng(rng_seed)
        signs = rng.choice([-1.0, 1.0], size=(20000, n))
        stats = signs @ d / n
        p = float((np.count_nonzero(np.abs(stats) >= abs(obs) - 1e-12) + 1) /
                  (stats.size + 1))
        method = "蒙特卡洛符号翻转置换(20000)"
    sd = float(np.std(d, ddof=1)) if n > 1 else 0.0
    return {"n": n, "mean": obs, "sd": sd, "p": p, "method": method,
            "cohens_dz": obs / sd if sd > 0 else float("nan")}


def independent_permutation_test(a, b, rng_seed: int = 20261122) -> dict:
    """独立两组均值差的置换检验（固定种子，结果可复现）。"""
    x = np.asarray([float(v) for v in a if v is not None and math.isfinite(float(v))], float)
    y = np.asarray([float(v) for v in b if v is not None and math.isfinite(float(v))], float)
    if x.size == 0 or y.size == 0:
        return {"n1": x.size, "n2": y.size, "diff": float("nan"), "p": float("nan")}
    obs = float(np.mean(x) - np.mean(y))
    pool = np.concatenate([x, y])
    rng = np.random.default_rng(rng_seed)
    counts = np.zeros(20000, dtype=float)
    for i in range(counts.size):
        perm = rng.permutation(pool)
        counts[i] = perm[:x.size].mean() - perm[x.size:].mean()
    p = float((np.count_nonzero(np.abs(counts) >= abs(obs) - 1e-12) + 1) /
              (counts.size + 1))
    s1 = float(np.std(x, ddof=1)) if x.size > 1 else 0.0
    s2 = float(np.std(y, ddof=1)) if y.size > 1 else 0.0
    pooled = math.sqrt(((x.size - 1) * s1**2 + (y.size - 1) * s2**2) /
                       max(1, x.size + y.size - 2))
    return {"n1": int(x.size), "n2": int(y.size), "mean1": float(np.mean(x)),
            "mean2": float(np.mean(y)), "diff": obs, "p": p,
            "cohens_d": obs / pooled if pooled > 0 else float("nan"),
            "method": "蒙特卡洛置换检验(20000, 固定种子)"}


def split_by_largest_gap(values, min_ratio: float = 0.25) -> dict | None:
    """按最大间隙把一维数据切成两簇；间隙不够大则不切。

    用于发现"两组材料"这类双峰分布，避免直接求全体平均掩盖真实差异。
    """
    arr = [(i, float(v)) for i, v in enumerate(values)
           if v is not None and math.isfinite(float(v))]
    if len(arr) < 4:
        return None
    arr.sort(key=lambda kv: kv[1])
    gaps = [(arr[i + 1][1] - arr[i][1], i) for i in range(len(arr) - 1)]
    gap, cut = max(gaps, key=lambda g: g[0])
    span = arr[-1][1] - arr[0][1]
    if span <= 0 or gap / span < min_ratio:
        return None
    low = arr[: cut + 1]
    high = arr[cut + 1:]
    return {
        "low_ids": [i for i, _ in low], "high_ids": [i for i, _ in high],
        "low_mean": float(np.mean([v for _, v in low])),
        "high_mean": float(np.mean([v for _, v in high])),
        "gap": gap, "gap_ratio": gap / span,
    }

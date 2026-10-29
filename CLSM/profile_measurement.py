# -*- coding: utf-8 -*-
"""文献启发的多位置条带平均剖面法；不依赖样品编号或预期单调趋势。"""
from __future__ import annotations

import numpy as np


PROFILE_POSITIONS = (0.25, 0.50, 0.75)
STRIP_WIDTH_UM = 12.6
PROFILE_SMOOTH_Y = 3
MIN_ROW_COVERAGE = 0.80
REF_IN, REF_OUT = 70, 165
CENTER_SEARCH, BORDER = 45, 8
MIN_DEPTH_UM, MIN_SNR = 0.10, 5.0
REFERENCES = (
    {"doi": "10.3390/ma16134761", "used_for": "沿槽长1/4、1/2、3/4多位置测量并平均"},
    {"doi": "10.1038/s41598-024-81233-0", "used_for": "从三维形貌提取平均剖面"},
    {"doi": "10.1016/j.jmrt.2026.01.163", "used_for": "两侧表面到槽底的深度及CMP前后比较"},
    {"doi": "10.3390/ma16062536", "used_for": "体积/投影面积的等效深度概念；本项目仅输出截面A/W辅助指标"},
)


def jsonable(value):
    """审计文件使用null表示不可测，禁止NaN/Infinity伪装成有效数。"""
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [jsonable(v) for v in value]
    if isinstance(value, (np.floating, float)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def _smooth(profile, window):
    return np.convolve(np.pad(profile, window//2, mode="reflect"), np.ones(window)/window, "valid")


def _empty(reason, **metadata):
    return dict(valid=False, reason=reason, depth_um=None, width_valid=False, width_um=None,
                width_reason="深度剖面不可测", area_um2=None, equivalent_depth_um=None,
                flags=[], **metadata)


def measure_strip(x, heights, missing, y0, dy, fit_baseline, opening_function,
                  fraction=None, width_um=STRIP_WIDTH_UM, column_range=None,
                  degree=2, ref_in=REF_IN, ref_out=REF_OUT, smooth_y=PROFILE_SMOOTH_Y):
    """先平均真实支撑点，再去基准、轻度平滑、测深；不先筛掉较浅的列。"""
    x = np.asarray(x, float)
    if column_range is None:
        location = x[0] + fraction*(x[-1]-x[0])
        center = int(np.argmin(abs(x-location)))
        columns = np.flatnonzero(abs(x-x[center]) <= width_um/2 + 1e-8)
    else:
        columns = np.arange(*column_range)
        center = int(columns[len(columns)//2]) if len(columns) else 0
    meta = dict(fraction=fraction, center_column=center,
                columns=columns.tolist(), requested_width_um=width_um,
                x_um=float(x[center]), baseline_degree=degree, ref_in=ref_in, ref_out=ref_out,
                smooth_y=smooth_y)
    if columns.size < 3:
        return _empty("条带不足3列", **meta)
    meta["actual_span_um"] = float(x[columns[-1]]-x[columns[0]])
    data = heights[:, columns]
    # 使用实际X采样间距作空间权重；等间距时与算术平均完全相同。
    weights = np.gradient(x[columns])
    support = np.isfinite(data) & ~missing[:, columns]
    counts = support.sum(axis=1)
    mass = (support*weights).sum(axis=1)
    mean = np.divide((np.where(support, data, 0)*weights).sum(axis=1), mass,
                     out=np.full(data.shape[0], np.nan), where=mass > 0)
    fraction_supported = mass/weights.sum()
    row_valid = (counts >= int(np.ceil(MIN_ROW_COVERAGE*len(columns)))) & (fraction_supported >= MIN_ROW_COVERAGE)
    y = np.arange(mean.size, dtype=float)
    inner = (y >= BORDER) & (y < mean.size-BORDER)
    ref = inner & (abs(y-y0) >= ref_in) & (abs(y-y0) <= ref_out) & row_valid
    meta.update(mean_profile_um=mean, support_counts=counts, support_fraction=fraction_supported,
                x_weights_um=weights, row_valid=row_valid)
    if np.count_nonzero(ref & (y < y0)) < 15 or np.count_nonzero(ref & (y > y0)) < 15:
        return _empty("两侧平台真实支撑不足", **meta)
    coeff = fit_baseline(y, mean, ref, deg=degree)
    base = np.polyval(coeff, y)
    # 噪声从未平滑的条带平均剖面估计，避免平滑本身压低检出阈值。
    residual = mean-base
    rv = residual[ref]
    noise = float(1.4826*np.median(abs(rv-np.median(rv))))
    complete = np.isfinite(residual) & row_valid
    good = np.flatnonzero(complete)
    # 仅为滤波/绘图补齐；实际测量窗口仍要求每行真实支撑达到80%。
    numerical = np.interp(y, good, residual[good])
    smooth = _smooth(numerical, smooth_y)
    band = np.flatnonzero(inner & (abs(y-y0) <= CENTER_SEARCH))
    if len(band) < 3:
        return _empty("槽底搜索区不足", **meta)
    idx = int(band[np.argmin(smooth[band])])
    a,b,c = smooth[idx-1:idx+2]
    den = a-2*b+c
    offset = float(np.clip(.5*(a-c)/den,-.5,.5)) if den > 0 else 0.0
    depth = float(-b + .25*(a-c)*offset)
    half = smooth_y//2+1
    check = complete[max(0,idx-half):min(mean.size,idx+half+1)]
    reason = ""
    if idx in (band[0],band[-1]):
        reason = "槽底位于搜索边界"
    elif not check.all() or not complete[band].all():
        reason = "槽底搜索区真实支撑不足"
    elif depth < MIN_DEPTH_UM:
        reason = "条带深度低于0.10µm"
    elif depth < MIN_SNR*max(noise,1e-6):
        reason = "条带深度低于5倍平台噪声"
    opening = opening_function(smooth, y0, noise)
    width_valid = bool(opening["valid"])
    left = opening.get("left",{}).get("pos")
    right = opening.get("right",{}).get("pos")
    width = None if left is None or right is None else float((right-left)*dy)
    width_reason = opening["reason"]
    flags = []
    for side in ("left","right"):
        if side not in opening:
            continue
        for k in opening[side]["points"]:
            if not complete[max(0,k-smooth_y//2):min(mean.size,k+smooth_y//2+1)].all():
                width_valid=False
                width_reason="槽口拟合真实支撑不足"
    area = equivalent = None
    if width_valid and not reason:
        points = np.r_[left, y[(y>left)&(y<right)], right]
        indices = np.arange(int(np.floor(left)),int(np.ceil(right))+1)
        if not complete[indices].all():
            flags.append("截面内部真实支撑不足，面积不输出")
        else:
            depression = np.maximum(0,-np.interp(points,y,smooth))
            area = float(np.trapezoid(depression,points*dy))
            equivalent = area/width
    if opening["reason"] == "槽口接近平台拟合区":
        flags.append("槽口接近平台基准区，需核对基准敏感性")
    # 记录条带内的槽底位置漂移，避免把平均剖面误认为消除了真实起伏。
    centers = []
    for j in columns:
        pp = heights[:,j]-base
        valid_band = band[np.isfinite(pp[band]) & ~missing[band,j]]
        if valid_band.size:
            centers.append(int(valid_band[np.argmin(pp[valid_band])]))
    drift = float(np.ptp(centers)*dy) if centers else None
    if centers and np.ptp(centers)>3:
        flags.append("条带内槽底位置变化超过3像素，平均轮廓可能展宽")
    return dict(**meta, valid=not reason, reason=reason, depth_um=depth,
                bottom_y_px=float(idx+offset), bottom_row=idx, baseline_coeff=coeff,
                baseline_um=base, residual_um=residual, smoothed_residual_um=smooth,
                reference_mask=ref, noise_um=noise, center_drift_um=drift,
                width_valid=width_valid, width_reason=width_reason, width_um=width,
                left_open_px=left, right_open_px=right,
                area_um2=area, equivalent_depth_um=equivalent, flags=flags)


def _summary(strips):
    depths=[s["depth_um"] for s in strips if s["valid"]]
    widths=[s["width_um"] for s in strips if s["width_valid"]]
    areas=[s["area_um2"] for s in strips if s["area_um2"] is not None]
    eq=[s["equivalent_depth_um"] for s in strips if s["equivalent_depth_um"] is not None]
    n=len(strips)
    # 固定位置缺一不静默平均其余位置，否则会再次筛选掉浅槽。
    full=lambda values:float(np.mean(values)) if len(values)==n and n else None
    return dict(n_planned=n,n_depth=len(depths),n_width=len(widths),
                depth_mean_um=full(depths),depth_position_sd_um=float(np.std(depths,ddof=1)) if len(depths)==n and n>1 else None,
                depth_partial_mean_um=float(np.mean(depths)) if depths else None,
                width_mean_um=full(widths),area_mean_um2=full(areas),equivalent_depth_mean_um=full(eq))


def analyse_profiles(r, fit_baseline, opening_function, strip_width_um=STRIP_WIDTH_UM,
                     positions=PROFILE_POSITIONS):
    positions=tuple(float(v) for v in positions)
    if len(positions)!=3 or any(not np.isfinite(v) or not 0<v<1 for v in positions) or any(a>=b for a,b in zip(positions,positions[1:])):
        raise ValueError("取样位置须为3个严格递增的0–1小数")
    if not np.isfinite(strip_width_um) or strip_width_um<=0:
        raise ValueError("条带宽度须为有限正数，单位µm")
    if len(r["x"])<3:
        raise ValueError("平均剖面法至少需要3个X采样列")
    real_missing=r["missing"] | r.get("repaired",False)
    def sample(fractions=positions,width=strip_width_um,degree=2,ref_in=70,ref_out=165,
               heights=None,missing=None,smooth_y=PROFILE_SMOOTH_Y):
        return [measure_strip(r["x"],r["Zclean"] if heights is None else heights,
                              real_missing if missing is None else missing,r["y0"],r["dy"],
                              fit_baseline,opening_function,fraction=f,width_um=width,
                              degree=degree,ref_in=ref_in,ref_out=ref_out,smooth_y=smooth_y) for f in fractions]
    strips=sample()
    overlap=any(set(a["columns"]) & set(b["columns"]) for a,b in zip(strips,strips[1:]))
    if overlap:
        raise ValueError("三个取样条带重叠，请减小条带宽度或增大取样位置间距")
    sensitivity={}
    plans=(("half_width",dict(width=strip_width_um/2)),("double_width",dict(width=strip_width_um*2)),
           ("linear_baseline",dict(degree=1)),("outer_baseline",dict(ref_in=90,ref_out=180)),
           ("smooth_1",dict(smooth_y=1)),("smooth_5",dict(smooth_y=5)))
    for key,kwargs in plans:
        other=sample(**kwargs)
        sensitivity[key]=dict(summary=_summary(other),depths_um=[s["depth_um"] for s in other],
                              valid=[s["valid"] for s in other],reasons=[s["reason"] for s in other])
    for shift,key in ((-.05,"positions_minus_5pct"),(.05,"positions_plus_5pct")):
        fractions=tuple(v+shift for v in positions)
        if all(0<v<1 for v in fractions):
            other=sample(fractions=fractions)
            sensitivity[key]=dict(summary=_summary(other),depths_um=[s["depth_um"] for s in other],
                                  valid=[s["valid"] for s in other],reasons=[s["reason"] for s in other])
    if "zero_mask" in r:
        restored=r["Zclean"].copy()
        restored[r["zero_mask"]]=0.0
        other=sample(heights=restored,missing=(r["missing"] & ~r["zero_mask"]) | r.get("repaired",False))
        sensitivity["zeros_as_height"]=dict(summary=_summary(other),depths_um=[s["depth_um"] for s in other],
                                           valid=[s["valid"] for s in other],reasons=[s["reason"] for s in other])
    # 全中央80%作为取样敏感性；不替代固定3位置主指标，也不宣称是真实全槽。
    keep=np.flatnonzero((r["x"]>=r["x"][0]+.1*np.ptp(r["x"])) & (r["x"]<=r["x"][0]+.9*np.ptp(r["x"])))
    central=measure_strip(r["x"],r["Zclean"],real_missing,r["y0"],r["dy"],fit_baseline,opening_function,
                          column_range=(int(keep[0]),int(keep[-1])+1) if keep.size else (0,0))
    return dict(method="three_positions_strip_mean_profile_depth",parameters=dict(
                positions=positions,strip_width_um=strip_width_um,smooth_y=PROFILE_SMOOTH_Y,
                min_row_coverage=MIN_ROW_COVERAGE,baseline_degree=2,ref_in=REF_IN,ref_out=REF_OUT,
                center_search=CENTER_SEARCH,min_depth_um=MIN_DEPTH_UM,min_snr=MIN_SNR,
                registration="none; average in instrument Y coordinates",aggregation="arithmetic mean only if all three valid",
                profile_average="X-spacing weighted mean of real supported heights; no X resampling",
                depth_definition="baseline to minimum of mean profile, then three-position arithmetic mean",
                support_excludes="missing, imputed and repaired pixels",
                parameter_provenance="位置/平均剖面有文献依据；宽度、滤波、质量阈值为本项目操作参数"),
                references=REFERENCES,strips=strips,summary=_summary(strips),sensitivity=sensitivity,
                central_80pct=central)


def selftest(fit_baseline,opening_function):
    """检查平均顺序、噪声/基准、缺失支撑、连续深区、浅槽与汇总完整性。"""
    yy=np.arange(512,dtype=float)
    xx=np.arange(121)*.63
    groove=-np.maximum(0,np.minimum(1.5,(40-abs(yy-256))*.08))
    platform=5+.001*yy+2e-6*(yy-256)**2
    z=np.tile((platform+groove)[:,None],(1,len(xx)))
    mask=np.zeros_like(z,bool)
    r=dict(x=xx,Zclean=z,missing=mask,dy=.63,y0=256)
    result=analyse_profiles(r,fit_baseline,opening_function)
    assert result["summary"]["n_depth"]==3
    assert abs(result["summary"]["depth_mean_um"]-1.5)<.015
    for s in result["strips"]:
        assert s["width_valid"] and abs(s["width_um"]-80*.63)<.05*80*.63
        assert s["equivalent_depth_um"]<s["depth_um"]
        assert abs(s["area_um2"]-1.5*(40+21.25)*.63)<.05*1.5*(40+21.25)*.63
    rng=np.random.default_rng(123)
    noisy=dict(r,Zclean=z+rng.normal(0,.02,z.shape))
    assert abs(analyse_profiles(noisy,fit_baseline,opening_function)["summary"]["depth_mean_um"]-1.5)<.03
    uneven=dict(r,x=xx+.02*np.sin(np.arange(len(xx))))
    assert abs(analyse_profiles(uneven,fit_baseline,opening_function)["summary"]["depth_mean_um"]-1.5)<.015
    missing=mask.copy(); missing[254:259,:]=True
    assert analyse_profiles(dict(r,missing=missing),fit_baseline,opening_function)["summary"]["depth_mean_um"] is None
    partial=mask.copy(); partial[256,30]=True
    assert analyse_profiles(dict(r,missing=partial),fit_baseline,opening_function)["summary"]["n_depth"]==3
    repaired=analyse_profiles(dict(r,repaired=partial),fit_baseline,opening_function)
    assert repaired["strips"][0]["support_counts"][256]==20,"修复点也不得冒充真实支撑"
    # 两类最低点位置不同：先取最深再平均仍为2；先平均轮廓后测深明显更浅。
    zz=np.full_like(z,5.)
    for j in range(len(xx)):
        center=248 if j%2 else 264
        zz[:,j]-=2*np.exp(-.5*((yy-center)/2)**2)
    mixed=analyse_profiles(dict(r,Zclean=zz),fit_baseline,opening_function)
    assert mixed["summary"]["depth_mean_um"]<1.3,"不能退化为先逐列取最深再平均"
    shallow=z.copy(); shallow[:,20:41]=platform[:,None]
    incomplete=analyse_profiles(dict(r,Zclean=shallow),fit_baseline,opening_function)
    assert incomplete["summary"]["depth_mean_um"] is None and incomplete["summary"]["n_depth"]==2
    # 连续真实深区应保留其贡献，不能以不单调为理由抹除。
    deep=z.copy(); deep[247:266,50:71]-=.6
    deeper=analyse_profiles(dict(r,Zclean=deep),fit_baseline,opening_function)
    assert deeper["strips"][1]["depth_um"]>1.9
    assert jsonable(float("nan")) is None
    print("  论文法自检：已知槽深/开口、噪声、先平均后测深、真实支撑、浅槽不静默排除、连续深区保护通过")

# -*- coding: utf-8 -*-
"""沿槽长分层随机取样、独立宽深筛查和有界补测。原始候选全部保留。"""
from __future__ import annotations

import numpy as np

from profile_measurement import (measure_strip, _summary, REFERENCES,
                                 PROFILE_POSITIONS, STRIP_WIDTH_UM, PROFILE_SMOOTH_Y)

DEFAULT_COUNT = 9
DEFAULT_SEED = 20261029
DEFAULT_ATTEMPTS = 4
OUTLIER_SIGMA = 3.5
OUTLIER_RELATIVE_FLOOR = .20


def _sampling_plan(x, width, positions, count, seed, attempts):
    """在不重叠的分区内预先产生候选顺序，候选顺序不依赖测量结果。"""
    x = np.asarray(x, float)
    if x.size < 3 or not np.all(np.isfinite(x)) or np.any(np.diff(x) <= 0):
        raise ValueError("X坐标至少3个且有限、严格递增")
    if not np.isfinite(width) or width <= 0:
        raise ValueError("条带宽度须为有限正数")
    if count < 1 or attempts < 1 or seed < 0:
        raise ValueError("位置数、候选次数须为正整数，随机种子须非负")
    span = np.ptp(x)
    if positions is None:
        boundaries = np.linspace(.1, .9, count+1)
        fractions = None
    else:
        fractions = np.asarray(positions, float)
        if (fractions.size < 1 or np.any(~np.isfinite(fractions)) or
                np.any((fractions <= 0) | (fractions >= 1)) or np.any(np.diff(fractions) <= 0)):
            raise ValueError("固定位置须为严格递增的0–1小数")
        count = len(fractions)
        boundaries = np.r_[0., (fractions[:-1]+fractions[1:])/2, 1.]
    rng = np.random.Generator(np.random.PCG64(seed))
    plan = []
    for group in range(count):
        lo, hi = x[0]+boundaries[group]*span, x[0]+boundaries[group+1]*span
        centers = np.flatnonzero((x-width/2 >= lo-1e-8) & (x+width/2 <= hi+1e-8))
        centers = np.array([j for j in centers if np.count_nonzero(abs(x-x[j]) <= width/2+1e-8) >= 3])
        if not centers.size:
            raise ValueError(f"分区{group+1}无法容纳条带；请减少位置数或减小条带宽度")
        if fractions is None:
            order = rng.permutation(centers).tolist()
        else:
            center = int(np.argmin(abs(x-(x[0]+fractions[group]*span))))
            if center not in centers:
                raise ValueError("固定位置的条带跨越分区或扫描边界")
            order = [center] + rng.permutation(centers[centers != center]).tolist()
        # 限制重复候选的重叠，但相邻候选允许少量重叠；同一分区最多选一个/指标。
        chosen = []
        for center in order:
            if all(abs(x[center]-x[k]) >= width/3 for k in chosen):
                chosen.append(center)
            if len(chosen) >= attempts:
                break
        plan.append(dict(group=group, bounds_fraction=boundaries[group:group+2].tolist(),
                         centers=chosen))
    return plan


def _screen_and_select(strips, count, dy):
    """以每区首个有效值估计稳健尺度；保留连续偏离区，宽深分别选择。"""
    summary = dict(n_planned=count, n_attempted=len(strips))
    expected_zones = set(range(min(3, count)))
    for metric, valid_key, value_key, floor in (
            ("depth", "valid", "depth_um", .15),
            ("width", "width_valid", "width_um", 2*dy)):
        representatives = []
        for group in range(count):
            candidates = [s for s in strips if s["group"] == group and s[valid_key]
                          and s[value_key] is not None and np.isfinite(s[value_key])]
            if candidates:
                representatives.append(candidates[0])
        values = np.array([s[value_key] for s in representatives])
        median = float(np.median(values)) if values.size else None
        scale = float(1.4826*np.median(abs(values-median))) if values.size else None
        limit = max(OUTLIER_SIGMA*scale, OUTLIER_RELATIVE_FLOOR*abs(median), floor) if values.size else None
        # 小样本不做统计剔除；偏离只相对同一样品，不比较不同编号的趋势。
        odd_groups = {s["group"] for s in representatives
                      if len(values) >= 5 and abs(s[value_key]-median) > limit}
        continuous = {g for g in odd_groups if g-1 in odd_groups or g+1 in odd_groups}
        selected = []
        for s in strips:
            quality = bool(s[valid_key] and s[value_key] is not None and np.isfinite(s[value_key]))
            outlier = bool(quality and len(values) >= 5 and abs(s[value_key]-median) > limit)
            preserved = outlier and s["group"] in continuous
            s[metric+"_outlier_candidate"] = outlier
            s[metric+"_excluded"] = outlier and not preserved
            s[metric+"_selected"] = False
            if preserved:
                s["flags"].append(f"{metric}连续偏离区保留，需核对真实形貌")
            elif outlier:
                s["flags"].append(f"{metric}孤立偏差超阈值，排除汇总但保留候选")
        for group in range(count):
            candidates = [s for s in strips if s["group"] == group and s[valid_key]
                          and not s[metric+"_excluded"] and s[value_key] is not None
                          and np.isfinite(s[value_key])]
            if candidates:
                s = candidates[0]
                s[metric+"_selected"] = True
                selected.append(s)
        values_selected = np.array([s[value_key] for s in selected])
        zones = {min(min(3,count)-1, s["group"]*min(3,count)//count) for s in selected}
        minimum = min(count, max(3, int(np.ceil(2*count/3))))
        # 大面积缺失或大量孤立偏离不能靠不断补测变成有效结论。
        excessive = len(odd_groups-continuous) > max(1, int(np.floor(.25*count)))
        publish = len(selected) >= minimum and zones == expected_zones and not excessive
        summary.update({"n_"+metric: len(selected), metric+"_minimum_required": minimum,
                        metric+"_mean_um": float(np.mean(values_selected)) if publish else None,
                        metric+"_position_sd_um": float(np.std(values_selected, ddof=1)) if publish and len(selected)>1 else None,
                        metric+"_partial_mean_um": float(np.mean(values_selected)) if len(selected) else None,
                        metric+"_unfiltered_mean_um": float(np.mean(values)) if values.size else None,
                        metric+"_screen_median_um": median, metric+"_screen_sigma_um": scale,
                        metric+"_screen_limit_um": limit,
                        metric+"_excluded_attempts": sum(s[metric+"_excluded"] for s in strips),
                        metric+"_outlier_groups": len(odd_groups-continuous),
                        metric+"_replacements": sum(s["attempt"]>1 for s in selected),
                        metric+"_zones": sorted(zones),
                        metric+"_status": ("有效；连续偏离区保留" if continuous else "有效") if publish else
                        "孤立偏差区过多，需复核" if excessive else "有效位置或沿槽覆盖不足"})
    # 截面积与A/W只汇总同一剖面上宽深均入选、内部支撑有效的位置。
    paired = [s for s in strips if s["depth_selected"] and s["width_selected"] and s["area_um2"] is not None]
    enough = (len(paired) >= min(count, max(3, int(np.ceil(2*count/3)))))
    area_zones = {min(min(3,count)-1, s['group']*min(3,count)//count) for s in paired}
    enough = enough and area_zones == expected_zones
    enough = enough and summary["depth_mean_um"] is not None and summary["width_mean_um"] is not None
    summary["n_area"] = len(paired)
    summary["area_mean_um2"] = float(np.mean([s["area_um2"] for s in paired])) if enough else None
    summary["equivalent_depth_mean_um"] = float(np.mean([s["equivalent_depth_um"] for s in paired])) if enough else None
    return summary


def selftest(fit_baseline, opening_function):
    """验证缺陷补测、独立宽深、连续变化保护、覆盖和随机可复现。"""
    from copy import deepcopy
    x = np.arange(512)*.63
    plan = _sampling_plan(x,12.6,None,9,20261029,4)
    assert plan == _sampling_plan(x,12.6,None,9,20261029,4)
    assert plan != _sampling_plan(x,12.6,None,9,20261030,4)
    for a,b in zip(plan,plan[1:]):
        assert max(x[a['centers']])+6.3 <= min(x[b['centers']])-6.3+1e-8
    y = np.arange(512,dtype=float)
    groove = -np.maximum(0,np.minimum(1.5,(40-abs(y-256))*.08))
    z = np.tile((5+.001*y+groove)[:,None],(1,len(x)))
    result = analyse_profiles(dict(x=x,Zclean=z,missing=np.zeros_like(z,bool),dy=.63,y0=256),
                              fit_baseline,opening_function,max_attempts=1)
    assert result['summary']['n_depth']==9 and result['summary']['n_width']==9
    assert abs(result['summary']['depth_mean_um']-1.5)<.015
    assert abs(result['summary']['width_mean_um']-80*.63)<.05*80*.63
    # 凸起槽缘的解析剖面恰在中心±12像素穿过基准，验证新增槽壁方法。
    t=(y-256)/12
    raised=2*(t*t-1)*np.exp(-.5*((y-256)/18)**2)
    opening=opening_function(raised,256,.01)
    assert opening['valid'] and abs(opening['right']['pos']-opening['left']['pos']-24)<.3
    neighbor=2*(((y-216)/12)**2-1)*np.exp(-.5*((y-216)/18)**2)
    opening=opening_function(neighbor,256,.01)
    assert not opening['valid'] and opening.get('center_mismatch'), '旁边小凹陷不能替代目标槽口'
    base = [dict(group=g,attempt=a,valid=True,width_valid=True,depth_um=2.,width_um=20.,
                 area_um2=30.,equivalent_depth_um=1.5,flags=[]) for g in range(9) for a in (1,2)]
    # 首候选深度异常但宽度有效：只替换深度，宽度保留首候选。
    defect = deepcopy(base); defect[8]['depth_um']=10.
    summary = _screen_and_select(defect,9,.63)
    assert summary['depth_replacements']==1 and summary['width_replacements']==0
    assert summary['depth_mean_um']==2. and summary['depth_unfiltered_mean_um']>2.
    assert defect[8]['depth_excluded'] and defect[8]['width_selected'] and defect[9]['depth_selected']
    wide=deepcopy(base);wide[8]['width_um']=40.
    summary=_screen_and_select(wide,9,.63)
    assert summary['width_replacements']==1 and summary['depth_replacements']==0
    # 连续真实偏离区保留，不能按同一全局阈值删成平滑结果。
    continuous = deepcopy(base)
    for s in continuous:
        if s['group'] in (3,4): s['depth_um']=10.
    summary = _screen_and_select(continuous,9,.63)
    assert summary['n_depth']==9 and summary['depth_mean_um']>3.
    assert all(not s['depth_excluded'] for s in continuous)
    # 位置总数够但全部位于前中段，不能输出覆盖全槽的主均值。
    missing = deepcopy(base)
    for s in missing:
        if s['group']>=6: s['valid']=False
    summary = _screen_and_select(missing,9,.63)
    assert summary['n_depth']==6 and summary['depth_mean_um'] is None and summary['width_mean_um']==20.
    # 多个分散偏离区不能通过补测掩盖。
    excessive = deepcopy(base)
    for s in excessive:
        if s['group'] in (0,3,6) and s['attempt']==1: s['depth_um']=10.
    assert _screen_and_select(excessive,9,.63)['depth_mean_um'] is None
    fixed = _sampling_plan(x,12.6,(.2,.4,.6,.8),9,123,2)
    assert len(fixed)==4
    for kwargs in (dict(count=100), dict(width=1000), dict(positions=(.7,.2))):
        params=dict(x=x,width=12.6,positions=None,count=9,seed=1,attempts=4);params.update(kwargs)
        try: _sampling_plan(**params)
        except ValueError: pass
        else: raise AssertionError('非法取样计划必须拒绝')
    print('  分层随机自检：可复现、区间隔离、已知宽深、独立筛查/补测、连续偏离保护、覆盖不足及偏离过多拦截通过')


def analyse_profiles(r, fit_baseline, opening_function, strip_width_um=STRIP_WIDTH_UM,
                     positions=None, sample_count=DEFAULT_COUNT, random_seed=DEFAULT_SEED,
                     max_attempts=DEFAULT_ATTEMPTS):
    missing = r["missing"] | r.get("repaired", False)
    x = np.asarray(r["x"], float)
    plan = _sampling_plan(x, strip_width_um, positions, sample_count, random_seed, max_attempts)

    def sample(fraction, **kwargs):
        strip=measure_strip(x, kwargs.pop("heights", r["Zclean"]), kwargs.pop("missing", missing),
                             r["y0"], r["dy"], fit_baseline, opening_function,
                             fraction=fraction, width_um=kwargs.pop("width", strip_width_um), **kwargs)
        from groove_local import apply_geometry_quality
        return apply_geometry_quality(strip,r.get('source',{}).get('orientation',{}))

    strips = []
    for group in plan:
        for attempt, center in enumerate(group["centers"], 1):
            fraction = float((x[center]-x[0])/np.ptp(x))
            strip = sample(fraction)
            strip.update(group=group["group"], attempt=attempt,
                         bounds_fraction=group["bounds_fraction"],sampling_strategy='standard_strip')
            strips.append(strip)
            if not strip['valid'] or not strip['width_valid']:
                narrow=sample(fraction,width=strip_width_um/2)
                narrow.update(group=group['group'],attempt=attempt,bounds_fraction=group['bounds_fraction'],
                              sampling_strategy='half_width_quality_fallback')
                narrow['flags'].append('标准条带质量失败，固定半宽备用；按质量顺序选择，不按测量大小选择')
                strips.append(narrow)
    summary = _screen_and_select(strips, len(plan), r["dy"])
    local=r.get('source',{}).get('orientation',{}).get('mapping_kind')=='local_normals'
    for metric,key in (('depth','valid'),('width','width_valid')):
        summary[metric+'_quality_failed_attempts']=sum(not s[key] for s in strips)
        summary[metric+'_confidence']='待复核（局部法向估计）' if local else '质量通过'
        if summary[metric+'_mean_um'] is None:
            summary[metric+'_confidence']='不可信作整槽均值；仅提供局部参考' if summary[metric+'_partial_mean_um'] is not None else '不可测；候选和原因保留'
        if local:
            summary[metric+'_status']+='；局部法向，待复核'
        narrower=sum(s[metric+'_selected'] and s.get('sampling_strategy')=='half_width_quality_fallback' for s in strips)
        summary[metric+'_half_width_selected']=narrower
        if narrower:
            summary[metric+'_status']+='；含半宽备用，需复核条带敏感性'
            if summary[metric+'_mean_um'] is not None:
                summary[metric+'_confidence']='待复核（含半宽备用）' if not local else '待复核（局部法向及半宽备用）'
        for s in strips:
            s[metric+'_confidence']=('质量失败，未纳入汇总' if not s[key] else
                '孤立偏差排除，原候选保留' if s[metric+'_excluded'] else
                summary[metric+'_confidence'] if s[metric+'_selected'] else '备用候选，未入选')
    # 敏感性固定采用主结果入选的深度位置；未入选区用预先计划的首位置，不重新抽样。
    anchors = []
    for group in range(len(plan)):
        candidates = [s for s in strips if s['group']==group]
        anchors.append(next((s for s in candidates if s['depth_selected']), candidates[0]))
    fractions = [s['fraction'] for s in anchors]
    selected = [s['depth_selected'] for s in anchors]
    sensitivity = {}
    variants = (("half_width", dict(width=strip_width_um/2)), ("double_width",dict(width=strip_width_um*2)),
                ("linear_baseline",dict(degree=1)), ("outer_baseline",dict(ref_in=90,ref_out=180)),
                ("smooth_1",dict(smooth_y=1)), ("smooth_5",dict(smooth_y=5)))
    if "zero_mask" in r:
        restored = r["Zclean"].copy()
        restored=r.get('zero_restored_height',restored)
        if 'zero_restored_height' not in r:
            restored[r['zero_mask']]=0.
        variants += (("zeros_as_height",dict(heights=restored,missing=r.get('zero_restored_missing',r["missing"] & ~r["zero_mask"]) | r.get("repaired",False))),)
    for key, kwargs in variants:
        other = [sample(f,**({'width':anchors[i]['requested_width_um']} | kwargs)) for i,f in enumerate(fractions)]
        # 参数敏感性不重新统计筛选或补测，固定主结果深度入选分区。
        complete = all(other[i]["valid"] for i, keep in enumerate(selected) if keep) and any(selected)
        vals = [s["depth_um"] for s, keep in zip(other, selected) if keep and s["valid"]]
        comparable = complete and summary["depth_mean_um"] is not None
        sensitivity[key] = dict(summary=dict(depth_mean_um=float(np.mean(vals)) if comparable else None,
                                            n_depth=len(vals)),
                                depths_um=[s["depth_um"] for s in other], valid=[s["valid"] for s in other],
                                reasons=[s["reason"] for s in other])
    # 原固定三位置仅作方法对照，不决定主结果是否留空。
    fixed = [sample(f) for f in PROFILE_POSITIONS]
    keep = np.flatnonzero((x >= x[0]+.1*np.ptp(x)) & (x <= x[0]+.9*np.ptp(x)))
    central = measure_strip(x,r["Zclean"],missing,r["y0"],r["dy"],fit_baseline,opening_function,
                            column_range=(int(keep[0]),int(keep[-1])+1))
    from groove_local import apply_geometry_quality
    central=apply_geometry_quality(central,r.get('source',{}).get('orientation',{}))
    return dict(method="stratified_random_strip_profiles_with_audited_replacements",
                parameters=dict(positions=[float((x[p["centers"][0]]-x[0])/np.ptp(x)) for p in plan],
                                sampling="stratified_random" if positions is None else "fixed_within_strata_replacements",
                                sample_count=len(plan), random_seed=random_seed, rng="PCG64", max_attempts=max_attempts,
                                quality_fallback='standard strip first; fixed half-width only on quality failure at the same preplanned position; independent metric selection',
                                strip_width_um=strip_width_um, smooth_y=PROFILE_SMOOTH_Y,
                                min_row_coverage=.8, baseline_degree=2, ref_in=70, ref_out=165,
                                outlier_sigma=OUTLIER_SIGMA, outlier_relative_floor=OUTLIER_RELATIVE_FLOOR,
                                outlier_absolute_floor_depth_um=.15, outlier_absolute_floor_width_um=2*r["dy"],
                                aggregation="first quality/non-outlier candidate per stratum independently for depth and width; >=2/3 strata and all three longitudinal zones",
                                continuous_deviations="adjacent anomalous strata retained and flagged",
                                parameter_provenance="项目操作参数，未经过实物标定；统计排除不证明是仪器错误"),
                references=REFERENCES, plan=plan, strips=strips, summary=summary,
                sensitivity=sensitivity, fixed_reference=dict(strips=fixed,summary=_summary(fixed)),
                central_80pct=central)

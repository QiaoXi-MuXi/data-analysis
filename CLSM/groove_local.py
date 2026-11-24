# -*- coding: utf-8 -*-
"""直线不适用时跟踪槽壁中心，建立有质量掩码的局部法向坐标。"""
from __future__ import annotations

import numpy as np


def _trend(u, v, query, neighbors=11):
    """固定空间窗口稳健二次回归，不逐列对齐槽底最低点。"""
    result, slopes = [], []
    for q in np.atleast_1d(query):
        idx = np.argsort(abs(u-q))[:min(neighbors, len(u))]
        t = u[idx]-q
        radius = max(np.max(abs(t)), 1e-8)
        weights = np.exp(-2*(t/radius)**2)
        design = np.column_stack((np.ones(t.size), t, t*t))
        robust = np.ones(t.size)
        for _ in range(12):
            w = np.sqrt(weights*robust)
            coeff = np.linalg.lstsq(design*w[:, None], v[idx]*w, rcond=None)[0]
            residual = v[idx]-design@coeff
            scale = max(1.4826*np.median(abs(residual-np.median(residual))), .05)
            robust = np.minimum(1., 1.5*scale/np.maximum(abs(residual), 1e-10))
        result.append(coeff[0]); slopes.append(coeff[1])
    return np.array(result), np.array(slopes)


def _tracking_outliers(u,v,dn):
    """留一预测防止尖点把紧邻正常点一起标成异常；只排除孤立突跳。"""
    errors=np.zeros(len(u))
    for i in range(1,len(u)-1):
        other=np.arange(len(u))!=i
        predicted,_=_trend(u[other],v[other],[u[i]],9)
        errors[i]=v[i]-predicted[0]
    scale=1.4826*np.median(abs(errors-np.median(errors)))
    odd=abs(errors)>max(3.5*scale,3*dn)
    return odd & ~np.r_[False,odd[:-1]] & ~np.r_[odd[1:],False]


def local_grid(z, missing, dx, dy, evidence, measurement):
    """以合格开口中点作几何跟踪，直接从原始高度一次采样。"""
    from groove_orientation import _rectification_grid, resample_height
    from profile_measurement import measure_strip
    if measurement is None:
        raise ValueError('局部法向跟踪需要可靠槽壁定位函数')
    baseline, opening = measurement
    grid = _rectification_grid(z, dx, dy, evidence)
    coarse, mask = resample_height(z, missing, grid)
    ds, dn = grid['analysis_step_xy_um']
    xs = np.arange(coarse.shape[1])*ds
    records, points = [], []
    # 固定候选不依赖测量数值；失败点保留原因，不因数值大小选择方向。
    for group, fraction in enumerate(np.linspace(.04, .96, 41)):
        s = measure_strip(xs, coarse, mask, grid['cross_center_row'], dn, baseline, opening,
                          fraction=float(fraction), width_um=6.3)
        record = dict(group=group, along_um=s['x_um'], valid=bool(s['width_valid']),
                      reason=s['width_reason'], width_um=s['width_um'],
                      localization_um=s.get('width_localization_uncertainty_um'))
        if s['width_valid']:
            center = ((s['left_open_px']+s['right_open_px'])/2-grid['cross_center_row'])*dn
            record['normal_um'] = float(center)
            points.append((s['x_um'], center, group))
        records.append(record)
    if len(points) < 12:
        raise ValueError('局部槽壁跟踪少于12/41个可靠中点，不能确定局部法向')
    points = np.array(points)
    u, v = points[:, 0], points[:, 1]
    # 仅排除孤立中点突跳；连续变化交由局部曲线与方向稳定性检查。
    isolated = _tracking_outliers(u,v,dn)
    for point, excluded in zip(points, isolated):
        r = records[int(point[2])]
        r['tracking_excluded'] = bool(excluded)
        if excluded:
            r['reason'] = '槽壁中点孤立突跳；仅排除几何跟踪，不修改原始高度'
    keep = ~isolated
    u, v = u[keep], v[keep]
    if len(u) < 12 or np.ptp(u) < .65*np.ptp(xs):
        raise ValueError('可靠局部中点覆盖不足，保留输入但不能建立法向测量坐标')
    fitted, _ = _trend(u, v, u)
    fit_error = abs(v-fitted)
    center, slope = _trend(u, v, xs)
    # 按中心线弧长重建等距列，宽仍为每条截线的物理法向距离。
    arc = np.r_[0., np.cumsum(np.hypot(np.diff(xs), np.diff(center)))]
    count = int(np.floor(arc[-1]/ds))+1
    along_arc = np.arange(count)*ds
    coarse_x = np.interp(along_arc, arc, xs)
    center, slope = _trend(u, v, coarse_x)
    _, narrow_slope = _trend(u, v, coarse_x, 7)
    angle_difference = abs(np.degrees(np.arctan(slope)-np.arctan(narrow_slope)))
    nearest = np.min(abs(coarse_x[:, None]-u), axis=1)
    nearest_error = fit_error[np.argmin(abs(coarse_x[:, None]-u), axis=1)]
    reasons = []
    for j, value in enumerate(coarse_x):
        why = []
        if value < u[0] or value > u[-1] or nearest[j] > .045*np.ptp(xs):
            why.append('槽壁跟踪缺口或端部无支撑')
        if nearest_error[j] > 3*dn:
            why.append('局部中点拟合偏差超过3像素')
        if angle_difference[j] > 3.:
            why.append('两种固定窗口局部方向差超过3度')
        for point, excluded in zip(points, isolated):
            if excluded and abs(value-point[0]) <= 6.3:
                why.append('孤立槽壁突跳影响区')
        reasons.append('；'.join(why))
    along, normal = np.array(evidence['along_vector_xy']), np.array(evidence['normal_vector_xy'])
    # coarse grid的中心行在原始物理XY中的位置。
    a = np.array(grid['analysis_index_to_original_row_col'])
    r0 = a[0, 1]*grid['cross_center_row']+a[0, 2]
    c0 = a[1, 1]*grid['cross_center_row']+a[1, 2]
    centers = np.array([c0*dx, r0*dy])+coarse_x[:, None]*along+center[:, None]*normal
    tangents = (along+slope[:, None]*normal)/np.sqrt(1+slope*slope)[:, None]
    normals = (normal-slope[:, None]*along)/np.sqrt(1+slope*slope)[:, None]
    # 曲率过高时远处法向可能相交；相应行失效，不能补成可信平台。
    curvature = np.gradient(np.arctan(slope), ds)
    offsets = (np.arange(coarse.shape[0])-grid['cross_center_row'])*dn
    nonfold = abs(offsets[:, None]*curvature[None, :]) < .65
    # 邻近映射格子的Jacobian必须同向且至少保留25%的面积。
    rr = (centers[None, :, 1]+offsets[:, None]*normals[None, :, 1])/dy
    cc = (centers[None, :, 0]+offsets[:, None]*normals[None, :, 0])/dx
    drs, drn = np.gradient(rr, axis=1), np.gradient(rr, axis=0)
    dcs, dcn = np.gradient(cc, axis=1), np.gradient(cc, axis=0)
    jac = dcs*drn-drs*dcn
    sign = np.sign(np.linalg.det(np.stack((along, normal), axis=1)))
    nonfold &= jac*sign > .25*ds*dn/(dx*dy)
    row_bounds = []
    for j in range(count):
        ok = np.flatnonzero(nonfold[:, j])
        if not len(ok) or np.any(np.diff(ok) != 1):
            row_bounds.append([0, -1])
            reasons[j] += '；法向映射折叠或无连续有效行'
        else:
            row_bounds.append([int(ok[0]), int(ok[-1])])
    column_valid = np.array([not reason for reason in reasons])
    return dict(mapping_kind='local_normals', analysis_shape_yx=[coarse.shape[0], count],
        analysis_step_xy_um=[ds, dn], cross_center_row=grid['cross_center_row'],
        local_centers_original_row_col=np.column_stack((centers[:, 1]/dy, centers[:, 0]/dx)).tolist(),
        local_normals_original_row_col_per_um=np.column_stack((normals[:, 1]/dy, normals[:, 0]/dx)).tolist(),
        local_tangents_xy=tangents.tolist(), geometry_column_valid=column_valid.tolist(),
        geometry_column_reasons=reasons, geometry_valid_row_bounds=row_bounds,
        tracking_records=records, tracking_fit_residual_um=fit_error.tolist(),
        local_angle_difference_deg=angle_difference.tolist(), local_curvature_per_um=curvature.tolist(),
        local_normal_policy='41 predetermined 6.3um strips; robust local quadratic 11 neighbors; 7-neighbor direction check <=3deg; midpoint residual <=3px; gap <=4.5% span; positive Jacobian',
        interpolation='one bilinear sample from original input; all positive-weight source pixels valid; no extrapolation',
        support_definition='local normal resampling supported by original pixels; not independent raw pixels',
        cropping='original coarse rectangle defines domain; edge gaps, unstable angles and folded platform rows remain invalid',
        along_interval_from_image_center_um=[0., float(along_arc[-1])])


def apply_geometry_quality(strip, orientation):
    """几何失效不因条带均值掩盖；保留候选值，但禁止进入宽深汇总。"""
    if orientation.get('mapping_kind') != 'local_normals':
        return strip
    columns = np.array(strip['columns'], int)
    valid = np.array(orientation['geometry_column_valid'], bool)
    bad = columns[~valid[columns]]
    flags = strip.setdefault('flags', [])
    strip['geometry_strategy'] = 'local_normals'
    strip['geometry_valid'] = not bool(bad.size)
    strip['geometry_bad_columns'] = bad.tolist()
    if bad.size:
        why = sorted({orientation['geometry_column_reasons'][j] for j in bad})
        reason = '局部法向不可信：'+'；'.join(why)
        strip['valid'] = strip['width_valid'] = False
        strip['reason'] = strip['width_reason'] = reason
        flags.append(reason)
    else:
        flags.append('局部法向策略；方向为估计值，需复核跟踪与参数敏感性')
    return strip


def selftest(baseline, opening):
    """解析弯槽、局部坐标真值、缺失及方向失效传播回归。"""
    from groove_orientation import orient_input, resample_height, original_coordinates
    from profile_sampling import analyse_profiles
    cases=[]
    u=np.linspace(-150,150,41);v=.001*u*u
    assert not _tracking_outliers(u,v,.625).any(),'连续曲线不能作为缺陷剔除'
    defect=v.copy();defect[20]+=12.
    excluded=_tracking_outliers(u,defect,.625)
    assert np.flatnonzero(excluded).tolist()==[20],'孤立尖点只能排除本点，不能污染邻点'
    for angle in (0,30,90):
        for amplitude in (12.,25.):
            step=.625
            xx,yy=np.meshgrid(np.arange(640)*step,np.arange(640)*step)
            rad=np.radians(angle)
            u=(xx-xx.mean())*np.cos(rad)+(yy-yy.mean())*np.sin(rad)
            v=-(xx-xx.mean())*np.sin(rad)+(yy-yy.mean())*np.cos(rad)
            center=amplitude*(u/150)**2
            slope=2*amplitude*u/150**2
            # 独立求抛物中心线的最近点，构造精确欧氏法向距离场作真值。
            nearest=u.copy()
            a=amplitude/150**2
            for _ in range(15):
                h=a*nearest*nearest-v
                grad=nearest-u+h*2*a*nearest
                hessian=1+(2*a*nearest)**2+2*a*h
                nearest-=np.clip(grad/np.maximum(hessian,.1),-40,40)
            distance=np.sign(v-a*nearest**2)*np.hypot(u-nearest,v-a*nearest**2)
            z=7+.001*xx+.002*yy-np.maximum(0,np.minimum(2.,(20-abs(distance))*.2))
            raw=z.copy();mask=np.zeros(z.shape,bool)
            x,out,dy,missing,source=orient_input(np.arange(640)*step,z,step,mask,
                dict(format='txt',dy_source='解析真值'),dy_override=step,measurement=(baseline,opening))
            o=source['orientation']
            assert o.get('mapping_kind')=='local_normals'
            r=dict(x=x,Zclean=out,missing=missing,dy=dy,y0=o['cross_center_row'],source=source)
            summary=analyse_profiles(r,baseline,opening)['summary']
            w,d=summary['width_mean_um'],summary['depth_mean_um']
            assert w is not None and abs(w-40)<1.5,(angle,amplitude,w)
            assert d is not None and abs(d-2)<.04,(angle,amplitude,d)
            assert np.array_equal(raw,z)
            plane=3+.2*xx-.1*yy
            pp,mm=resample_height(plane,np.zeros_like(mask),o)
            jj,ii=np.meshgrid(np.arange(pp.shape[1]),np.arange(pp.shape[0]))
            rr,cc=original_coordinates(o,jj,ii)
            truth=3+.2*cc*step-.1*rr*step
            assert np.max(abs(pp[~mm]-truth[~mm]))<1e-10
            hole=np.zeros_like(mask);hole[310:325,310:325]=True
            _,holemask=resample_height(plane,hole,o)
            assert holemask.sum()>mm.sum()
            j=next(j for j,ok in enumerate(o['geometry_column_valid']) if not ok)
            strip=dict(columns=[j],valid=True,width_valid=True,depth_um=2.,width_um=40.,flags=[])
            apply_geometry_quality(strip,o)
            assert not strip['valid'] and not strip['width_valid']
            cases.append(dict(angle=angle,amplitude_um=amplitude,width_um=w,depth_um=d))
    print('  局部法向自检：6种解析曲槽、40µm宽/2µm深、原始数据不变、物理平面坐标真值、缺失及方向失效传播通过')
    return cases

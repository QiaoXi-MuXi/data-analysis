# -*- coding: utf-8 -*-
"""识别直槽中心线，在物理坐标中校正方向；保留原始数据及缺失判据。"""
from __future__ import annotations

import numpy as np

MIN_LINE_RATIO = 6.0
MAX_CENTER_RESIDUAL_PX = 3.0
NATIVE_ANGLE_TOLERANCE_DEG = .05


def _line_fit(u, v, pixel_size=0.):
    """分区中心的中位斜率初值，拒绝支撑不足。"""
    slopes = [(v[j]-v[i])/(u[j]-u[i]) for i in range(len(u)) for j in range(i+1,len(u))]
    slope = float(np.median(slopes))
    offset = float(np.median(v-slope*u))
    error = v-(offset+slope*u)
    scale = 1.4826*np.median(abs(error-np.median(error)))
    keep = abs(error) <= max(3.5*scale, .5*pixel_size, 1e-8)
    if keep.sum() < 6:
        raise ValueError("槽中心线有效分区不足，不能可靠校正方向")
    slope, offset = np.polyfit(u[keep],v[keep],1)
    return float(slope), float(offset), keep


def _unit_axis(vector):
    vector = np.asarray(vector,float)
    dominant = int(np.argmax(abs(vector)))
    if vector[dominant] < 0:
        vector = -vector
    return vector/np.linalg.norm(vector)


def detect_axis(z, missing, dx, dy, measurement=None, allow_local=False):
    """去平面只为识别槽线；不将去平面值送入宽深测量。"""
    sy, sx = max(1,z.shape[0]//256), max(1,z.shape[1]//256)
    sampled = np.asarray(z[::sy,::sx],float)
    yy,xx = np.meshgrid(np.arange(sampled.shape[0])*dy*sy,
                        np.arange(sampled.shape[1])*dx*sx,indexing='ij')
    xx -= (z.shape[1]-1)*dx/2
    yy -= (z.shape[0]-1)*dy/2
    span = np.array([(z.shape[1]-1)*dx,(z.shape[0]-1)*dy])
    good = (~missing[::sy,::sx] & np.isfinite(sampled) &
            (abs(xx)<.45*span[0]) & (abs(yy)<.45*span[1]))
    if min(sampled.shape)<30 or good.sum()<.5*sampled.size:
        raise ValueError("有效高度或视场不足，无法可靠识别直槽方向")
    points = np.column_stack((xx[good],yy[good]))
    design = np.column_stack((np.ones(good.sum()), points/span))
    values = sampled[good]
    keep = np.ones(values.size,bool)
    for _ in range(5):
        coeff = np.linalg.lstsq(design[keep],values[keep],rcond=None)[0]
        residual = values-design@coeff
        mid = np.median(residual[keep])
        noise = float(1.4826*np.median(abs(residual[keep]-mid)))
        next_keep = abs(residual-mid)<=max(3*noise,np.ptp(values)*1e-6,1e-12)
        if next_keep.sum()<.3*len(values) or np.array_equal(keep,next_keep):
            break
        keep=next_keep
    depression = np.maximum(0,-residual)
    depth = float(np.percentile(depression,99))
    if depth<=max(5*noise,1e-10):
        raise ValueError("未找到超过背景噪声的连续槽线（平面或干扰过强）")
    chosen = depression>max(3*noise,.20*depth)
    p=points[chosen]
    w=np.minimum(depression[chosen],depth)
    if len(w)<60:
        raise ValueError("槽线真实支撑不足")
    center=np.average(p,axis=0,weights=w)
    q=p-center
    cov=(q.T*w)@q/w.sum()
    eig,vec=np.linalg.eigh(cov)
    ratio=float(eig[-1]/max(eig[0],1e-12))
    if ratio<MIN_LINE_RATIO:
        raise ValueError("未找到唯一细长直槽（可能为交叉槽、多槽、坑或强干扰），拒绝强行方向校正")
    along=_unit_axis(vec[:,-1])
    # 分区凹陷重心比最低点更少受双谷切换的影响，不把槽底逐列平移对齐。
    for _ in range(3):
        normal=np.array([-along[1],along[0]])
        u,v=p@along,p@normal
        bounds=np.linspace(*np.percentile(u,[2,98]),10)
        centers=[]
        for a,b in zip(bounds[:-1],bounds[1:]):
            band=(u>=a)&(u<b)
            if band.sum()>=max(6,.15*len(w)/9):
                centers.append([np.average(u[band],weights=w[band]),float(np.mean(np.percentile(v[band],[5,95])))])
        if len(centers)<7:
            raise ValueError("连续槽线支撑不足7/9分区，不能可靠校正方向")
        centers=np.array(centers)
        slope,offset,inliers=_line_fit(centers[:,0],centers[:,1],min(dx,dy))
        along=_unit_axis(along+slope*normal)
    normal=np.array([-along[1],along[0]])
    u,v=p@along,p@normal
    centers=[]
    bounds=np.linspace(*np.percentile(u,[2,98]),10)
    for a,b in zip(bounds[:-1],bounds[1:]):
        band=(u>=a)&(u<b)
        if band.sum()>=max(6,.15*len(w)/9):
            centers.append([np.average(u[band],weights=w[band]),float(np.mean(np.percentile(v[band],[5,95])))])
    centers=np.array(centers)
    slope,offset,inliers=_line_fit(centers[:,0],centers[:,1],min(dx,dy))
    wall_support=None
    if measurement is not None:
        along,normal,offset,centers,wall_support=_refine_walls(z,missing,dx,dy,along,offset,measurement)
        slope,offset,inliers=_line_fit(centers[:,0],centers[:,1],min(dx,dy))
    errors=centers[:,1]-(offset+slope*centers[:,0])
    cross_pixel=float(np.hypot(normal[0]*dx,normal[1]*dy))
    bad=abs(errors)>MAX_CENTER_RESIDUAL_PX*cross_pixel
    straight_failures=[]
    if bad.sum()>2 or any(bad[i] and bad[i+1] for i in range(len(bad)-1)):
        straight_failures.append(f"两壁中点不符合直线模型：最大残差{max(abs(errors))/cross_pixel:.2f}px，"
                                 f"{int(bad.sum())}个中心超3px且过多或相邻")
    if inliers.sum()<6:
        raise ValueError("直槽中心线一致性不足，拒绝方向校正")
    half=len(centers)//2
    first=np.polyfit(centers[:half,0],centers[:half,1],1)[0]
    last=np.polyfit(centers[-half:,0],centers[-half:,1],1)[0]
    half_difference=float(abs(np.degrees(np.arctan(first)-np.arctan(last))))
    if half_difference>5.0:
        straight_failures.append('槽线前后方向差超过5°')
    if straight_failures and not allow_local:
        raise ValueError('；'.join(straight_failures)+'；不能用统一角度测量')
    selected_axis='horizontal' if abs(along[0])>=abs(along[1]) else 'vertical'
    if selected_axis=='vertical':
        normal=-normal
        offset=-offset
        centers[:,1]*=-1
        if wall_support:
            for item in wall_support['accepted']:
                item['normal_um']=-item['normal_um']
    angle=float(np.degrees(np.arctan2(along[1],along[0])))
    deviation=angle-(90 if selected_axis=='vertical' else 0)
    evidence=dict(method='robust-plane depression covariance and nine-stratum centroid line',
        angle_deg=angle,angle_to_nearest_axis_deg=deviation,
        along_vector_xy=along.tolist(),normal_vector_xy=normal.tolist(),
        center_normal_offset_um=float(offset),noise_estimate=noise,
        line_eigenvalue_ratio=ratio,minimum_line_ratio=MIN_LINE_RATIO,
        supported_strata=len(centers),wall_midpoint_support=wall_support,centerline_strata_uv_um=centers.tolist(),
        centerline_fit_residual_um=errors.tolist(),maximum_center_residual_px=MAX_CENTER_RESIDUAL_PX,
        half_direction_difference_deg=half_difference,maximum_half_difference_deg=5.,
        direction_review_flag=('前后估角差超过2°；局部槽形变化，统一直槽方向仍需复核' if half_difference>2 else ''),
        native_angle_tolerance_deg=NATIVE_ANGLE_TOLERANCE_DEG,straight_failures=straight_failures)
    return selected_axis,evidence


def _refine_walls(z,missing,dx,dy,along,offset,measurement):
    """用原有开口拟合通过的两壁中点细化方向，槽底缺陷不参与角度回归。"""
    from profile_measurement import measure_strip
    baseline,opening=measurement
    for _ in range(3):
        normal=np.array([-along[1],along[0]])
        evidence=dict(along_vector_xy=along,normal_vector_xy=normal,center_normal_offset_um=offset)
        grid=_rectification_grid(z,dx,dy,evidence)
        image,mask=resample_height(z,missing,grid)
        along_step,cross_step=grid['analysis_step_xy_um']
        xs=np.arange(image.shape[1])*along_step
        centers=[]
        support=[]
        attempts=[]
        for group,fraction in enumerate(np.linspace(.12,.88,9)):
            for delta in (0.,.015,-.015,.03):
                s=measure_strip(xs,image,mask,grid['cross_center_row'],cross_step,baseline,opening,
                                fraction=float(fraction+delta),width_um=12.6)
                attempts.append(dict(stratum=group+1,fraction=float(fraction+delta),
                                     width_valid=s['width_valid'],reason=s['width_reason'],
                                     width_um=s['width_um']))
                if s['width_valid']:
                    break
            if s['width_valid']:
                u=grid['along_interval_from_image_center_um'][0]+s['x_um']
                v=offset+((s['left_open_px']+s['right_open_px'])/2-grid['cross_center_row'])*cross_step
                centers.append([u,v])
                support.append(dict(stratum=group+1,along_um=float(u),normal_um=float(v),
                                    width_um=s['width_um'],localization_um=s.get('width_localization_uncertainty_um')))
        if len(centers)<6 or not ({0,1,2}<={min(2,(s['stratum']-1)//3) for s in support}):
            raise ValueError("可靠槽壁中点不足6/9分区或前中后覆盖不足，不能确认校正角度")
        centers=np.array(centers)
        slope,new_offset,inliers=_line_fit(centers[:,0],centers[:,1],min(dx,dy))
        if abs(slope)<1e-5:
            offset=new_offset
            break
        old_along,old_normal=along,normal
        along=_unit_axis(old_along+slope*old_normal)
        normal=np.array([-along[1],along[0]])
        physical=centers[:,0,None]*old_along+centers[:,1,None]*old_normal
        centers=np.column_stack((physical@along,physical@normal))
        offset=float(np.median(centers[:,1]))
    normal=np.array([-along[1],along[0]])
    for item,point in zip(support,centers):
        item['along_um'],item['normal_um']=map(float,point)
    return along,normal,offset,centers,dict(accepted=support,attempts=attempts,
        method='first quality-valid opening midpoint at each of 9 preplanned strata; at most 4 attempts; 12.6 um strips')


def original_coordinates(orientation, xx, yy):
    """分析索引对应原始行列；局部法向映射不能用单个仿射矩阵替代。"""
    if orientation.get('mapping_kind')=='local_normals':
        centers=np.asarray(orientation['local_centers_original_row_col'])
        normals=np.asarray(orientation['local_normals_original_row_col_per_um'])
        idx=np.asarray(xx,int)
        offset=(np.asarray(yy)-orientation['cross_center_row'])*orientation['analysis_step_xy_um'][1]
        return centers[idx,0]+offset*normals[idx,0],centers[idx,1]+offset*normals[idx,1]
    matrix=np.asarray(orientation['analysis_index_to_original_row_col'],float)
    return matrix[0,0]*xx+matrix[0,1]*yy+matrix[0,2],matrix[1,0]*xx+matrix[1,1]*yy+matrix[1,2]


def resample_height(z, missing, orientation):
    """正权重源点均须有效，不跨缺失区域补齐，不在原始视场外外推。"""
    z=np.asarray(z,float)
    missing=np.asarray(missing,bool)|~np.isfinite(z)
    rows,cols=orientation['analysis_shape_yx']
    yy,xx=np.meshgrid(np.arange(rows),np.arange(cols),indexing='ij')
    r,c=original_coordinates(orientation,xx,yy)
    inside=(r>=-1e-8)&(r<=z.shape[0]-1+1e-8)&(c>=-1e-8)&(c<=z.shape[1]-1+1e-8)
    r=np.clip(r,0,z.shape[0]-1);c=np.clip(c,0,z.shape[1]-1)
    r0=np.floor(r).astype(int);c0=np.floor(c).astype(int)
    r1=np.minimum(r0+1,z.shape[0]-1);c1=np.minimum(c0+1,z.shape[1]-1)
    fr,fc=r-r0,c-c0
    result=np.zeros(r.shape)
    valid=inside.copy()
    if orientation.get('mapping_kind')=='local_normals':
        bounds=np.asarray(orientation['geometry_valid_row_bounds'])
        valid &= (yy>=bounds[:,0]) & (yy<=bounds[:,1])
        valid &= np.asarray(orientation['geometry_column_valid'])[None,:]
    for rr,cc,weight in ((r0,c0,(1-fr)*(1-fc)),(r0,c1,(1-fr)*fc),
                         (r1,c0,fr*(1-fc)),(r1,c1,fr*fc)):
        contributes=weight>1e-10
        valid &= ~contributes|~missing[rr,cc]
        result+=weight*np.where(missing[rr,cc],0,z[rr,cc])
    result[~valid]=np.nan
    return result,~valid


def _rectification_grid(z,dx,dy,evidence):
    along=np.array(evidence['along_vector_xy'])
    normal=np.array(evidence['normal_vector_xy'])
    maximum=np.array([(z.shape[1]-1)*dx,(z.shape[0]-1)*dy])
    origin=maximum/2+normal*evidence['center_normal_offset_um']
    along_step=float(np.hypot(along[0]*dx,along[1]*dy))
    cross_step=float(np.hypot(normal[0]*dx,normal[1]*dy))
    # 从最长中心线段逐步内缩，优先保留沿槽覆盖，再检验两侧平台视场。
    lower,upper=-float(np.linalg.norm(maximum)),float(np.linalg.norm(maximum))
    for i in range(2):
        if abs(along[i])>1e-10:
            limits=sorted((-origin[i]/along[i],(maximum[i]-origin[i])/along[i]))
            lower=max(lower,limits[0]);upper=min(upper,limits[1])
    midpoint=(lower+upper)/2
    halfspan=(upper-lower)/2-along_step
    for fraction in np.linspace(1.,.25,76):
        lo,hi=midpoint-halfspan*fraction,midpoint+halfspan*fraction
        negative_radius=positive_radius=209
        for u in (lo,hi):
            position=origin+along*u
            for i in range(2):
                if abs(normal[i])>1e-10:
                    positive=(maximum[i]-position[i])/normal[i] if normal[i]>0 else -position[i]/normal[i]
                    negative=position[i]/normal[i] if normal[i]>0 else (position[i]-maximum[i])/normal[i]
                    positive_radius=min(positive_radius,int(np.floor(positive/cross_step))-1)
                    negative_radius=min(negative_radius,int(np.floor(negative/cross_step))-1)
        if min(negative_radius,positive_radius)>=94 and negative_radius+positive_radius+1>=346:
            lower,upper=lo,hi
            break
    else:
        raise ValueError("校正后两侧平台视场不足，无法保证既有基准区；请扩大扫描范围")
    columns=int(np.floor((upper-lower)/along_step))+1
    if columns<30:
        raise ValueError("校正后沿槽可用视场不足，不能分区测量")
    start=origin+along*lower-normal*negative_radius*cross_step
    matrix=[[along[1]*along_step/dy,normal[1]*cross_step/dy,start[1]/dy],
            [along[0]*along_step/dx,normal[0]*cross_step/dx,start[0]/dx]]
    return dict(analysis_shape_yx=[negative_radius+positive_radius+1,columns],
        analysis_index_to_original_row_col=matrix,analysis_step_xy_um=[along_step,cross_step],
        along_interval_from_image_center_um=[lower,lower+(columns-1)*along_step],
        cross_center_row=negative_radius,source_origin_xy_um=start.tolist(),
        interpolation='bilinear; every positive-weight source pixel must be finite and unmasked; no extrapolation',
        support_definition='resampled node supported by original valid pixels; not an independent original pixel',
        cropping='longest centerline interval on 1% geometric search, >=346 normal rows and >=94 each side; no padded heights')


def orient_input(x,z,dy,missing,source,axis='auto',dy_override=None,measurement=None):
    """统一返回沿直槽X、法向Y；原生横纵槽保留原矩阵或转置。"""
    x,z,missing=np.asarray(x,float),np.asarray(z,float),np.asarray(missing,bool)
    if z.ndim!=2 or z.shape!=missing.shape or z.shape[1]!=x.size:
        raise ValueError("方向处理的高度、坐标或缺失掩码尺寸不一致")
    if x.size<2 or not np.isfinite(x).all() or np.any(np.diff(x)<=0):
        raise ValueError("原始X坐标必须有限且严格递增")
    source=dict(source)
    original_assumed=source['format']=='txt' and dy_override is None
    if dy_override is not None:
        dy=float(dy_override)
        source['dy_source']='命令行指定原始Y步距'
    if not np.isfinite(dy) or dy<=0:
        raise ValueError("原始Y步距必须为有限正数")
    dx=float((x[-1]-x[0])/(len(x)-1))
    if axis not in ('auto','horizontal','vertical'):
        raise ValueError("槽方向只支持auto、horizontal、vertical")
    selected,evidence=detect_axis(z,missing,dx,dy,measurement,allow_local=True)
    if axis!='auto' and selected!=axis:
        raise ValueError("指定横纵方向与高度图槽线不符；请使用auto或核对输入")
    transposed=selected=='vertical'
    local=bool(evidence.get('straight_failures'))
    rectified=local or abs(evidence['angle_to_nearest_axis_deg'])>NATIVE_ANGLE_TOLERANCE_DEG
    orientation=dict(requested=axis,selected=selected,transposed=transposed,rectified=rectified,
        angle_deg=evidence['angle_deg'],original_shape_yx=list(z.shape),
        original_x_start_um=float(x[0]),original_x_step_um=dx,original_y_step_um=float(dy),
        original_x_median_step_um=float(np.median(np.diff(x))),
        original_y_source=source['dy_source'],original_y_assumed=original_assumed,
        analysis_x_axis='groove_tangent' if rectified else ('original_y' if transposed else 'original_x'),
        analysis_y_axis='groove_normal' if rectified else ('original_x_minus_start' if transposed else 'original_y'),
        original_row_col='affine in analysis_index_to_original_row_col' if rectified else
                         ('analysis_col,analysis_row' if transposed else 'analysis_row,analysis_col'),evidence=evidence)
    if transposed or rectified:
        grid_error=float(np.max(abs((x-x[0])-np.arange(x.size)*dx)))
        if grid_error>.05*dx:
            raise ValueError("原始X坐标不够等距，不能可靠校正方向；请导出均匀物理坐标")
        orientation['cross_grid_max_error_um']=grid_error
    orientation['strategy']='local_normals' if local else 'global_straight'
    if rectified:
        if local:
            from groove_local import local_grid
            orientation.update(local_grid(z,missing,dx,dy,evidence,measurement))
            evidence['direction_review_flag']='局部法向跟踪；全局直线检查失败，检查方向对照及局部质量标记'
        else:
            orientation.update(_rectification_grid(z,dx,dy,evidence))
        z,missing=resample_height(z,missing,orientation)
        along_step,cross_step=orientation['analysis_step_xy_um']
        x=np.arange(z.shape[1])*along_step
        dy=cross_step
        source['dy_source']='物理XY方向校正后的法向步距'
    elif transposed:
        x=np.arange(z.shape[0])*dy
        z,missing=z.T.copy(),missing.T.copy()
        dy=dx
        source['dy_source']='原始X坐标全图平均步距（纵槽转置后的横槽步距）'
    if rectified or transposed:
        for j in range(z.shape[1]):
            good=np.flatnonzero(~missing[:,j]&np.isfinite(z[:,j]))
            if good.size and missing[:,j].any():
                z[missing[:,j],j]=np.interp(np.flatnonzero(missing[:,j]),good,z[good,j])
    source['orientation']=orientation
    return x,z,dy,missing,source


def orientation_note(source,english=False):
    o=source.get('orientation',{})
    if not o:
        return ''
    if o.get('mapping_kind')=='local_normals':
        return ('Unwrapped local coordinates: X=centerline arc length, Y=local normal; review quality flags'
                if english else '局部展开：X为中心线弧长，Y为局部法向；异常区标记，需复核')
    if o.get('rectified'):
        return (f"Straight groove {o['angle_deg']:.2f} deg; rectified X=tangent, Y=normal; bilinear resampling"
                if english else f"直槽方向{o['angle_deg']:.2f}°；已校正：X沿槽、Y法向；双线性重采样")
    if english:
        return ('Vertical groove; analysis X=original Y, analysis Y=original X - X0' if o['transposed']
                else 'Horizontal groove; analysis X/Y=original X/Y')
    return '纵槽；分析X=原始Y，分析Y=原始X-X0' if o['transposed'] else '横槽；分析X/Y对应原始X/Y'


def plot_orientation(result,path):
    """同时显示原始高度及实际法向截线、校正后的分析视场，便于人工复核。"""
    import hashlib
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.sans-serif']=['Microsoft YaHei','SimHei','DejaVu Sans']
    plt.rcParams['axes.unicode_minus']=False
    raw=result['raw_surface']
    o=result['source']['orientation']
    original=np.where(raw['missing'],np.nan,raw['Z'])
    corrected=np.where(result['missing'],np.nan,result['Z'])
    lo,hi=np.nanpercentile(original,[1,99])
    fig,axes=plt.subplots(1,2,figsize=(12,6.6))
    extent=[0,float(np.ptp(raw['x'])),0,(original.shape[0]-1)*raw['dy']]
    axes[0].imshow(original,origin='lower',extent=extent,vmin=lo,vmax=hi,cmap='viridis',aspect='equal')
    axes[0].set(title='原始高度与实际截线',xlabel='原始 X-X0（µm）',ylabel='原始 Y（µm）')
    x=result['x']
    cross=np.array([max(0,result['y0']-100),min(corrected.shape[0]-1,result['y0']+100)])
    for s in result['paper']['strips']:
        if not (s['width_selected'] or s['depth_selected']):
            continue
        j=s['center_column']
        if o['rectified']:
            rr,cc=original_coordinates(o,j,cross)
        elif o['transposed']:
            rr=np.full(2,j);cc=cross
        else:
            rr=cross;cc=np.full(2,j)
        axes[0].plot(cc*o['original_x_step_um'],rr*o['original_y_step_um'],color='white',lw=.8,alpha=.85)
    if o.get('mapping_kind')=='local_normals':
        centers=np.asarray(o['local_centers_original_row_col'])
        good=np.asarray(o['geometry_column_valid'],bool)
        axes[0].plot(centers[:,1]*o['original_x_step_um'],centers[:,0]*o['original_y_step_um'],color='cyan',lw=.8,label='估计槽线')
        axes[0].scatter(centers[~good,1]*o['original_x_step_um'],centers[~good,0]*o['original_y_step_um'],s=3,color='red',label='方向不可信区')
        axes[0].legend(fontsize=8)
    im=axes[1].imshow(corrected,origin='lower',extent=[0,float(np.ptp(x)),0,(corrected.shape[0]-1)*result['dy']],
                      vmin=lo,vmax=hi,cmap='viridis',aspect='equal')
    axes[1].set(title='校正后的分析高度（两图同色标）' if o['rectified'] else '分析高度（无需角度重采样）',
                xlabel='沿槽 X（µm）',ylabel='法向 Y（µm）')
    axes[1].axhline(result['y0']*result['dy'],color='white',lw=.7,ls='--')
    fig.colorbar(im,ax=axes.ravel().tolist(),fraction=.025,pad=.03,label='高度 Z（µm）')
    fig.suptitle(result['path'].stem+' | '+orientation_note(result['source']),fontsize=11)
    note='仅在原始视场内取局部双线性支撑，不外推、不将缺失源像素填成有效点。原始文件未修改。'
    flag=o['evidence'].get('direction_review_flag','')
    if flag:
        note+='\n'+flag
    fig.text(.06,.035,note,fontsize=9)
    fig.subplots_adjust(left=.07,right=.85,top=.88,bottom=.18,wspace=.25)
    fig.savefig(path,dpi=300)
    plt.close(fig)
    o['review_plot']=path.name
    o['review_plot_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()


def selftest(baseline,opening):
    """解析斜槽真值、各向异性像素、原生轴回归和无效支撑保护。"""
    from profile_sampling import analyse_profiles
    cases=[]
    def synthetic(angle,dx=.625,dy=.625,ny=512,nx=512,defect=False):
        xx,yy=np.meshgrid(np.arange(nx)*dx,np.arange(ny)*dy)
        rad=np.radians(angle)
        u=(xx-xx.mean())*np.cos(rad)+(yy-yy.mean())*np.sin(rad)
        v=-(xx-xx.mean())*np.sin(rad)+(yy-yy.mean())*np.cos(rad)
        z=7+.001*xx+.002*yy-np.maximum(0,np.minimum(2.,(20-abs(v))*.2))
        if defect:
            z-=8*np.exp(-((u-20)/4)**2-(v/2)**2)
        return np.arange(nx)*dx,z,dy,np.zeros(z.shape,bool),dict(format='txt',dy_source='合成真值')
    for angle in (-60,-45,-30,-15,-5,0,5,15,30,45,60,85,90,105,135):
        raw=synthetic(angle)
        x,z,dy,mask,source=orient_input(*raw,dy_override=.625,measurement=(baseline,opening))
        y0=int(np.argmin(np.median(z,axis=1)))
        p=analyse_profiles(dict(x=x,Zclean=z,missing=mask,dy=dy,y0=y0),baseline,opening)
        s=p['summary']
        w,d=s['width_mean_um'],s['depth_mean_um']
        estimated=source['orientation']['angle_deg']
        assert abs((estimated-angle+90)%180-90)<.3,(angle,estimated)
        assert w is not None and abs(w-40)<.8,(angle,w)
        assert d is not None and abs(d-2)<.03,(angle,d)
        if angle in (0,90):
            assert not source['orientation']['rectified']
            assert np.array_equal(z,raw[1].T if angle==90 else raw[1])
        cases.append(dict(angle_true_deg=angle,angle_measured_deg=estimated,width_um=w,depth_um=d))
    for angle in (30,60):
        raw=synthetic(angle,.45,.8,512,768,True)
        x,z,dy,mask,source=orient_input(*raw,dy_override=.8,measurement=(baseline,opening))
        p=analyse_profiles(dict(x=x,Zclean=z,missing=mask,dy=dy,y0=source['orientation']['cross_center_row']),baseline,opening)
        assert abs(p['summary']['width_mean_um']-40)<1.2
        assert abs((source['orientation']['angle_deg']-angle+90)%180-90)<.5
    raw=synthetic(30)
    _,_,_,_,source=orient_input(*raw,measurement=(baseline,opening))
    o=source['orientation']
    yy,xx=np.meshgrid(np.arange(512),np.arange(512),indexing='ij')
    plane=3+.2*xx-.1*yy
    out,mask=resample_height(plane,np.zeros_like(plane,bool),o)
    r,c=np.meshgrid(np.arange(out.shape[0]),np.arange(out.shape[1]),indexing='ij')
    a=np.array(o['analysis_index_to_original_row_col'])
    truth=3+.2*(a[1,0]*c+a[1,1]*r+a[1,2])-.1*(a[0,0]*c+a[0,1]*r+a[0,2])
    assert not mask.any() and np.max(abs(out-truth))<1e-10
    hole=np.zeros_like(plane,bool)
    hole[250:260,250:260]=True
    _,mask=resample_height(plane,hole,o)
    assert mask.sum()>50,'有缺失源点的插值不能伪装成真实支撑'
    for kind in ('flat','cross','curve','all_missing'):
        x,z,dy,mask,s=synthetic(0)
        yy,xx=np.meshgrid(np.arange(512)*dy,np.arange(512)*dy,indexing='ij')
        if kind=='flat':
            z=np.ones_like(z)*7
        if kind=='cross':
            z-=np.maximum(0,np.minimum(2,(20-abs(xx-xx.mean()))*.2))
        if kind=='curve':
            v=yy-yy.mean()-30*((xx-xx.mean())/(xx.max()/2))**2
            z=7-np.maximum(0,np.minimum(2,(20-abs(v))*.2))
        if kind=='all_missing':
            mask[:]=True
        try:
            detect_axis(z,mask,dy,dy,measurement=(baseline,opening))
        except ValueError:
            pass
        else:
            raise AssertionError(kind+'必须拒绝')
    print('  方向校正自检：15个已知角度、不同XY步距/深坑、原生横纵回归、仿射真值/缺失保护及交叉/曲槽拒绝通过')
    return cases

# -*- coding: utf-8 -*-
"""MetroPro DAT 标定、去平面、周期分离及独立高通候选。"""
from pathlib import Path
import struct
import hashlib
import numpy as np

def read_dat(path):
    """使用头部标定换算为 nm；保留文件行序与无效点，不做翻转。"""
    path = Path(path)
    b = path.read_bytes()
    if len(b)<256:
        raise ValueError('DAT头部被截断')
    def u(offset, fmt):
        return struct.unpack_from(">" + fmt, b, offset)[0]
    magic, fmt, header = u(0, "I"), u(4, "h"), u(6, "i")
    if fmt not in (1, 2, 3) or magic != 0x881B036E + fmt:
        raise ValueError(f"非支持的 MetroPro DAT: {path.name}")
    ib, w, h, pb = u(60, "i"), u(68, "h"), u(70, "h"), u(72, "i")
    res = {0: 4096, 1: 32768, 2: 131072}.get(u(218, "h"))
    if not res or min(w, h) <= 0 or pb != w*h*4 or ib < 0:
        raise ValueError("矩阵尺寸、相位字节数或分辨率无效")
    if header < 256 or header + ib + pb != len(b):
        raise ValueError("头部/数据区长度与文件长度不一致")
    sf, wl, ob, dx = [u(o, "f") for o in (164, 168, 176, 184)]
    if not all(np.isfinite([sf, wl, ob, dx])) or min(sf, wl, ob, dx) <= 0:
        raise ValueError("文件内标定值无效，不允许猜测高度或横向标定")
    step = sf*wl*ob/res*1e9
    raw = np.frombuffer(b, dtype=">i4", offset=header+ib, count=w*h).reshape(h, w)
    z = raw.astype(np.float64)*step
    z[raw >= 2147483640] = np.nan
    meta = dict(file=path.name, sha256=hashlib.sha256(b).hexdigest(),
                phase_sha256=hashlib.sha256(raw.tobytes()).hexdigest(),
                header_format=fmt, header_bytes=header, intensity_bytes=ib,
                phase_bytes=pb, width=w, height=h, dx_um=dx*1e6,
                wavelength_nm=wl*1e9, intf_scale=sf, obliquity=ob,
                resolution=res, height_step_nm=step, valid_fraction=float(np.isfinite(z).mean()))
    return z, meta


def remove_plane(z):
    y, x = np.indices(z.shape, dtype=float)
    x = (x-(z.shape[1]-1)/2)/z.shape[1]
    y = (y-(z.shape[0]-1)/2)/z.shape[0]
    m = np.isfinite(z)
    if m.sum() < 16:
        raise ValueError("有效点不足")
    a = np.column_stack((np.ones(m.sum()), x[m], y[m]))
    coeff = np.linalg.lstsq(a, z[m], rcond=None)[0]
    plane = coeff[0]+coeff[1]*x+coeff[2]*y
    return z-plane, plane


def metrics(z):
    v = z[np.isfinite(z)]
    v = v-v.mean()
    return dict(Sa_nm=float(np.mean(abs(v))), Sq_nm=float(np.sqrt(np.mean(v*v))),
                Sz_nm=float(np.ptp(v)))


def fit_period(z, bounds=(1.0, 5.0, 3.5, 8.0)):
    """在共享谱峰附近用加窗谱精细搜索，频率单位为每视场周期数。"""
    h, w = z.shape
    # 降采样只用于低频定位；系数在全分辨率有效点上重新拟合。
    small = np.nan_to_num(z[::4, ::4])
    yy, xx = np.indices(small.shape)
    xx, yy = xx*4/w, yy*4/h
    win = np.outer(np.hanning(small.shape[0]), np.hanning(small.shape[1]))
    v = small*win
    def score(kx, ky):
        ex = np.exp(-2j*np.pi*kx*xx[0])
        ey = np.exp(-2j*np.pi*ky*yy[:, 0])
        return float(abs(ey@v@ex)**2)
    lo_x, hi_x, lo_y, hi_y = bounds
    best = max(((score(kx, ky), kx, ky)
                for kx in np.arange(lo_x, hi_x+0.01, .5)
                for ky in np.arange(lo_y, hi_y+0.01, .5)))
    for step in (.25, .125, .0625, .03125):
        _, cx, cy = best
        choices = [(score(kx, ky), kx, ky)
                   for kx in (cx-step, cx, cx+step)
                   for ky in (cy-step, cy, cy+step)
                   if lo_x <= kx <= hi_x and lo_y <= ky <= hi_y]
        best = max(choices)
    _, kx, ky = best
    y, x = np.indices(z.shape, dtype=float)
    phase = 2*np.pi*(kx*x/w+ky*y/h)
    m = np.isfinite(z)
    a = np.column_stack((np.ones(m.sum()), (x[m]-w/2)/w, (y[m]-h/2)/h,
                         np.cos(phase[m]), np.sin(phase[m])))
    coeff = np.linalg.lstsq(a, z[m], rcond=None)[0]
    # 平面为干扰项，只减去拟合的正余弦；之后再按相同口径去平面。
    wave = coeff[3]*np.cos(phase)+coeff[4]*np.sin(phase)
    clean, _ = remove_plane(z-wave)
    removed = z-clean
    # 空间交错块留出：仅检查周期结构能否预测其他空间位置，不判定噪声来源。
    train = ((x[m].astype(int)//80+y[m].astype(int)//60)%2 == 0)
    gains = []
    for tr in (train, ~train):
        te = ~tr
        c1 = np.linalg.lstsq(a[tr], z[m][tr], rcond=None)[0]
        c0 = np.linalg.lstsq(a[tr, :3], z[m][tr], rcond=None)[0]
        mse0 = np.mean((z[m][te]-a[te, :3]@c0)**2)
        mse1 = np.mean((z[m][te]-a[te]@c1)**2)
        gains.append(float(1-mse1/mse0))
    return clean, removed, dict(kx=float(kx), ky=float(ky),
                               amplitude_nm=float(np.hypot(coeff[3], coeff[4])),
                               cv_gain_min=min(gains), cv_gain_mean=float(np.mean(gains)),
                               search_at_boundary=bool(kx in (lo_x, hi_x) or ky in (lo_y, hi_y)))


def review_flag(fit):
    """诊断提示不是自动验收，不以粗糙度是否下降作为有效性标准。"""
    reasons = []
    if fit['cv_gain_min'] <= 0:
        reasons.append("fixed_frequency_spatial_check_failed")
    if fit['search_at_boundary']:
        reasons.append("frequency_at_search_boundary")
    return ';'.join(reasons) or 'candidate_requires_measurement_validation'

def read_intensity(path):
    """MetroPro 手册 12-4：大端 uint16，0xffff 是无效光强点。"""
    b = Path(path).read_bytes()
    def u(offset, fmt):
        return struct.unpack_from('>'+fmt, b, offset)[0]
    header, width, height = u(6,'I'), u(52,'H'), u(54,'H')
    buckets, expected_range, nbytes = u(56,'H'), u(58,'H'), u(60,'I')
    if min(width, height, buckets) <= 0 or nbytes != width*height*buckets*2:
        raise ValueError('光强区缺失或长度不符合头部定义，不允许生成推测光强图')
    raw = np.frombuffer(b, dtype='>u2', offset=header, count=width*height*buckets).reshape(buckets,height,width)
    valid = raw != 65535
    if np.any(raw[valid] > expected_range):
        raise ValueError('光强数值超过文件头 ac_range，需要检查字节序/数据格式')
    frames = raw.astype(float)
    frames[~valid] = np.nan
    # 单帧直接使用；多帧取有效帧均值并在元数据标明，不偷偷选帧。
    n = np.isfinite(frames).sum(axis=0)
    intensity = np.divide(np.nansum(frames, axis=0), n,
                          out=np.full((height,width),np.nan), where=n>0)
    return intensity, dict(width=width, height=height, buckets=buckets,
                           ac_range=expected_range, byte_order='big-endian uint16',
                           invalid_value=65535, invalid_pixels=int((~np.isfinite(intensity)).sum()),
                           ac_org_x=u(48,'H'), ac_org_y=u(50,'H'),
                           cn_org_x=u(64,'H'), cn_org_y=u(66,'H'),
                           frame_policy='single frame' if buckets==1 else 'valid-frame arithmetic mean')

def gaussian_lowpass(z,dx,cutoff):
    """各向同性高斯、50% 振幅截止；半采样镜像边界，保留原掩膜。"""
    valid=np.isfinite(z)
    def smooth(a):
        extended=np.concatenate((a,a[::-1]),axis=0)
        extended=np.concatenate((extended,extended[:,::-1]),axis=1)
        fy=np.fft.fftfreq(extended.shape[0],d=dx)
        fx=np.fft.rfftfreq(extended.shape[1],d=dx)
        transfer=np.exp(-np.log(2)*cutoff**2*(fy[:,None]**2+fx[None,:]**2))
        out=np.fft.irfft2(np.fft.rfft2(extended)*transfer,s=extended.shape)
        return out[:a.shape[0],:a.shape[1]]
    low=smooth(np.where(valid,z,0.))
    if not valid.all():
        low/=smooth(valid.astype(float))
    return low


def spline_factor(n,lam):
    """I+λD₂ᵀD₂ 五对角 Cholesky；端部二阶差分惩罚自然停止。"""
    diagonal=np.full(n,1+6*lam);diagonal[[0,-1]]=1+lam;diagonal[[1,-2]]=1+5*lam
    first=np.full(n-1,-4*lam);first[[0,-1]]=-2*lam
    main=np.empty(n);off1=np.zeros(n);off2=np.zeros(n)
    for i in range(n):
        if i>=2:off2[i]=lam/main[i-2]
        if i>=1:off1[i]=(first[i-1]-off2[i]*off1[i-1])/main[i-1]
        main[i]=np.sqrt(diagonal[i]-off1[i]**2-off2[i]**2)
    return main,off1,off2


def spline_axis(a,axis,lam):
    source=np.moveaxis(a,axis,0)
    main,off1,off2=spline_factor(source.shape[0],lam)
    tmp=np.empty_like(source);result=np.empty_like(source)
    for i in range(source.shape[0]):
        v=source[i].copy()
        if i>=1:v-=off1[i]*tmp[i-1]
        if i>=2:v-=off2[i]*tmp[i-2]
        tmp[i]=v/main[i]
    for i in range(source.shape[0]-1,-1,-1):
        v=tmp[i].copy()
        if i+1<len(tmp):v-=off1[i+1]*result[i+1]
        if i+2<len(tmp):v-=off2[i+2]*result[i+2]
        result[i]=v/main[i]
    return np.moveaxis(result,0,axis)


def cubic_lowpass(z,dx,cutoff):
    """行列可分离三次平滑样条；不是原生张力 Gauss Spline 算法。"""
    omega=2*np.pi*dx/cutoff
    lam=1/(4*np.sin(omega/2)**2)**2
    return spline_axis(spline_axis(np.nan_to_num(z),1,lam),0,lam)


def highpass(z,dx,cutoff,method):
    low=gaussian_lowpass(z,dx,cutoff) if method=='gaussian' else cubic_lowpass(z,dx,cutoff)
    out=z-low
    out-=np.nanmean(out)
    return out


# -*- coding: utf-8 -*-
"""分别导出二维分区剖面与三维位置剖面；不把离散剖面插值成连续表面。"""
from __future__ import annotations

import hashlib
import json
import textwrap
from pathlib import Path

import numpy as np

from profile_measurement import jsonable
from groove_orientation import orientation_note


def plot_profiles(results, output_root):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    output_root = Path(output_root)
    for folder in ('2D', '3D'):
        (output_root/folder).mkdir(parents=True, exist_ok=True)
    records = []
    for result in results:
        paper = result['paper']
        chosen = [s for s in paper['strips'] if s['depth_selected'] or s['width_selected']]
        n = paper['summary']['n_planned']
        dy = result['dy']
        y = np.arange(result['Z'].shape[0])*dy
        start = max(0, (result['y0']-100)*dy)
        end = min(y[-1], (result['y0']+100)*dy)
        view = (y >= start) & (y <= end)
        values = [np.asarray(s['smoothed_residual_um'])[view & np.asarray(s['row_valid'])]
                  for s in chosen if 'smoothed_residual_um' in s]
        finite = np.concatenate(values) if values else np.array([-1., 1.])
        finite = finite[np.isfinite(finite)]
        if not finite.size:
            finite=np.array([-1.,1.])
        low, high = min(float(finite.min()),0.), max(float(finite.max()),0.)
        margin = max((high-low)*.12,.1)
        low, high = low-margin, high+margin
        cols = min(3,n)
        rows = int(np.ceil(n/cols))
        fig, axes = plt.subplots(rows,cols,figsize=(max(9.4,4.7*cols),3.5*rows+.5),squeeze=False,
                                 sharex=True,sharey=True)
        for group, ax in enumerate(axes.flat):
            if group >= n:
                ax.set_visible(False)
                continue
            selected = [s for s in chosen if s['group']==group]
            ax.axhline(0,color='k',ls=':',lw=.8)
            if not selected:
                candidates=[s for s in paper['strips'] if s['group']==group]
                reason=next((s.get('reason') or s.get('width_reason') for s in candidates if s.get('reason') or s.get('width_reason')),'质量不通过')
                ax.text(.5,.5,'未纳入汇总\n'+'\n'.join(textwrap.wrap(reason,width=19)),ha='center',va='center',fontsize=8,color='#ad4e28',transform=ax.transAxes)
            labels = []
            for s in selected:
                d = np.where(s['row_valid'],s['smoothed_residual_um'],np.nan)
                role = '宽深' if s['depth_selected'] and s['width_selected'] else '槽深' if s['depth_selected'] else '槽宽'
                color = 'C0' if s['depth_selected'] else 'C1'
                ax.plot(y,d,color=color,lw=1.2,label=f"{role}入选 X={s['x_um']:.2f} µm")
                if s['width_selected']:
                    left,right = s['left_open_px']*dy,s['right_open_px']*dy
                    ax.scatter([left,right],[0,0],color='C1',s=16,zorder=4)
                    ax.annotate('',(left,0),(right,0),arrowprops=dict(arrowstyle='<->',color='C1',lw=1))
                    labels.append(f"W={s['width_um']:.3f} µm")
                if s['depth_selected']:
                    bottom = s['bottom_y_px']*dy
                    depth = s['depth_um']
                    ax.scatter([bottom],[-depth],color='C3',s=16,zorder=4)
                    ax.annotate('',(bottom,-depth),(bottom,0),arrowprops=dict(arrowstyle='<->',color='C3',lw=1))
                    labels.append(f"D={depth:.3f} µm")
            if labels:
                ax.text(.02,.03,'；'.join(labels),fontsize=8.5,transform=ax.transAxes,
                        bbox=dict(facecolor='white',edgecolor='none',alpha=.8))
                ax.legend(loc='upper right',fontsize=7.5)
            confidence=('；局部方向待复核' if result['source']['orientation'].get('mapping_kind')=='local_normals' else '')
            ax.set(title=f'分区 {group+1}'+confidence,xlim=(start,end),ylim=(low,high),
                   xlabel='法向剖面位置 Y（µm）',ylabel='相对基准高度（µm）')
            ax.grid(alpha=.18)
        cross_source = ('方向校正后的法向' if result.get('source',{}).get('orientation',{}).get('rectified') else
                        '原始X坐标' if result.get('source',{}).get('orientation',{}).get('transposed')
                        else result.get('source',{}).get('dy_source','已指定'))
        dy_note = f"分析Y步距{dy:.4g}µm"+('（按X假定）' if result['dy_assumed'] else '（'+cross_source+'）')
        if result.get('source',{}).get('format') == 'lsm':
            dy_note += '；'+result['source']['height_status']
        dy_note += '\n'+orientation_note(result.get('source',{}))
        if paper['summary']['depth_mean_um'] is None or paper['summary']['width_mean_um'] is None:
            dy_note+='\n有效区段参考：覆盖或异常比例未达整槽汇总要求；详见Excel质量页'
        elif any('复核' in paper['summary'][metric+'_status'] for metric in ('depth','width')):
            dy_note+='\n存在待复核项：方向估计或半宽备用；详见Excel质量页'
        if result.get('source',{}).get('orientation',{}).get('transposed') and result['source']['orientation']['original_y_assumed']:
            dy_note += '；沿槽分析X步距按原始X假定'
        fig.suptitle(f"{result['path'].stem} | 二维入选剖面 | 点为基准面开口交点/槽底；箭头为W/D\n{dy_note}",fontsize=11)
        fig.tight_layout(rect=(0,0,1,.87 if dy_note.count('\n')>=2 else .91))
        png2 = output_root/'2D'/f"{result['path'].stem}_profile.png"
        fig.savefig(png2,dpi=300)
        plt.close(fig)

        fig = plt.figure(figsize=(13,8.5))
        ax = fig.add_subplot(111,projection='3d')
        for s in chosen:
            color = f"C{s['group']%10}"
            good = view & np.asarray(s['row_valid'])
            d = np.where(good,s['smoothed_residual_um'],np.nan)
            role = '宽深' if s['depth_selected'] and s['width_selected'] else '深' if s['depth_selected'] else '宽'
            xx = np.full(y.shape,s['x_um'])
            ax.plot(xx,y,d,color=color,lw=1.2,ls='-' if s['depth_selected'] else '--',
                    label=f"区{s['group']+1} {role} X={s['x_um']:.1f}")
            if s['width_selected']:
                left,right = s['left_open_px']*dy,s['right_open_px']*dy
                ax.plot([s['x_um']]*2,[left,right],[0,0],color=color,lw=1,marker='o',ms=3)
            if s['depth_selected']:
                bottom = s['bottom_y_px']*dy
                ax.plot([s['x_um']]*2,[bottom,bottom],[0,-s['depth_um']],color=color,ls=':',lw=1)
        x = result['x']
        ax.set(xlabel='沿槽位置 X（µm）',ylabel='法向剖面位置 Y（µm）',zlabel='相对基准高度（µm）',
               xlim=(x[0],x[-1]),ylim=(start,end),zlim=(low,high))
        z_exaggeration = max(8., .18*max(np.ptp(x),end-start)/(high-low))
        ax.set_box_aspect((np.ptp(x),end-start,(high-low)*z_exaggeration))
        from matplotlib.ticker import MaxNLocator
        for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
            axis.set_major_locator(MaxNLocator(4))
        ax.view_init(elev=26,azim=-57)
        if chosen:
            ax.legend(loc='upper left',bbox_to_anchor=(1.01,1),fontsize=8,frameon=False)
        fig.suptitle(f"{result['path'].stem} | 三维入选剖面与实际X位置",fontsize=13)
        note = f'各曲线减去自身局部基准；未在剖面之间拟合表面。竖向显示夸张{z_exaggeration:.1f}倍，刻度为真实µm。'+dy_note
        fig.text(.05,.025,'\n'.join(textwrap.wrap(note,width=78)),fontsize=9)
        fig.subplots_adjust(left=.04,right=.79,bottom=.16,top=.93)
        png3 = output_root/'3D'/f"{result['path'].stem}_profile.png"
        fig.savefig(png3,dpi=300)
        plt.close(fig)
        records.append(dict(file=result['path'].name,dy_um=dy,dy_assumed=result['dy_assumed'],source=result.get('source',{}),
                            y_range_um=[start,end],height_range_um=[low,high],z_display_exaggeration=float(z_exaggeration),
                            profiles=[{key:s[key] for key in ('group','attempt','fraction','x_um','depth_selected',
                                      'width_selected','depth_um','width_um')} for s in chosen],
                            png2=str(png2.relative_to(output_root)),png3=str(png3.relative_to(output_root)),
                            png2_sha256=hashlib.sha256(png2.read_bytes()).hexdigest(),
                            png3_sha256=hashlib.sha256(png3.read_bytes()).hexdigest()))
        print(f"  剖面图 {result['path'].stem}: 2D + 3D")
    manifest = dict(source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    height_definition='strip mean profile minus individual local baseline; real supported rows only',
                    dpi=300,files=records)
    (output_root/'profile_plot_manifest.json').write_text(json.dumps(jsonable(manifest),ensure_ascii=False,
                                                        indent=2,allow_nan=False),encoding='utf-8')
    return manifest

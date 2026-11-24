# -*- coding: utf-8 -*-
"""BMP 识别与 DAT 重建共用入口、持久参数及分流输出。"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
import ra_config as config
import plot_labels

import zygo_dat as dat
import zygo_render

FILTERS = config.FILTERS








def format_sheet(ws, header_row, widths):
    ws.freeze_panes = f'C{header_row+1}'
    ws.sheet_view.showGridLines = False
    for c, width in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(1, c).column_letter].width = width
    for cell in ws[header_row]:
        cell.fill = PatternFill('solid', fgColor='DCE6F1')
        cell.font = Font(bold=True)
        cell.alignment = Alignment(vertical='center', wrap_text=True)
    ws.row_dimensions[header_row].height = 34
    for row in ws.iter_rows(min_row=header_row+1):
        for cell in row:
            cell.alignment = Alignment(vertical='center')
            if isinstance(cell.value, float):
                cell.number_format = '0.000000'
    ws.auto_filter.ref = f'A{header_row}:{ws.cell(ws.max_row,ws.max_column).coordinate}'




def read_bmp(files, api):
    recs = []
    reference_unit = None
    for path in files:
        source_digest = sha(path)
        with Image.open(path) as image:
            if image.size != (api.IMG_W, api.IMG_H):
                print(f'[跳过] {path.name}: 截图尺寸不匹配')
                continue
        values = []
        for row in [api.ROW_RA, api.ROW_PV, api.ROW_RMS]:
            text, confidence = api.read_number(path, row)
            if row == api.ROW_RA and confidence and (max(c[1] for c in confidence)>40 or min(c[2] for c in confidence)<10):
                print(f'[低置信] {path.name}: 建议与截图核对Ra')
            values.append(float(text))
        unit, distance = api.read_unit(path, api.ROW_RA)
        if reference_unit is None:reference_unit=unit
        elif unit!=reference_unit:print(f'[警告] {path.name}单位{unit}与首张{reference_unit}不一致，请核对')
        if sha(path) != source_digest:
            raise ValueError(f'读取期间截图发生变化：{path}')
        recs.append(dict(name=path.name, path=path, ra=values[0], pv=values[1], rms=values[2], unit=unit, sha256=source_digest))
    if files and not recs:
        raise ValueError('截图均未成功识别')
    return recs


def process_dat(path, setting):
    raw, meta = dat.read_dat(path)
    intensity, imeta = dat.read_intensity(path)
    if min(raw.shape) < 16:
        raise ValueError(f'{path.name}: 高度矩阵尺寸不足')
    z0, _ = dat.remove_plane(raw)
    z = z0.copy()
    removed = np.where(np.isfinite(z), 0., np.nan)
    fit = None
    noise, high, cutoff = setting
    if noise == '启用':
        z, removed, fit = dat.fit_period(z)
    period_stats = dat.metrics(z)
    if FILTERS[high]:
        if cutoff <= 2*meta['dx_um']:
            raise ValueError(f'{path.name}: 截止波长必须大于两倍像素间距')
        z = dat.highpass(z, meta['dx_um'], cutoff, FILTERS[high])
    if not np.array_equal(np.isfinite(z), np.isfinite(raw)):
        raise AssertionError('处理改变了原有效点掩膜')
    notes = []
    if fit:
        notes.append(dat.review_flag(fit))
    if high != '关闭':
        notes.append('independent_highpass_candidate_not_native_GaussSpline')
        if cutoff > min(raw.shape)*meta['dx_um']:
            notes.append('cutoff_exceeds_short_field_boundary_sensitive')
    if imeta['ac_range'] <= 0:
        raise ValueError('光强范围无效')
    intensity = intensity*255/imeta['ac_range']
    rec = dict(name=path.name, path=path, ra=dat.metrics(z)['Sa_nm'], pv=dat.metrics(z)['Sz_nm'],
               rms=dat.metrics(z)['Sq_nm'], unit='nm', meta=meta, intensity_meta=imeta,
               setting=list(setting), baseline=dat.metrics(z0), after_period=period_stats,
               fit=fit, review=';'.join(notes) or 'plane_removed_only')
    return rec, dict(raw_height_nm=raw, plane_removed_nm=z0, removed_periodic_nm=removed,
                     processed_height_nm=z, valid_mask=np.isfinite(raw)), intensity


def write_dat_results(wb, records, means, source):
    if 'DAT分析' in wb.sheetnames:
        del wb['DAT分析']
    ws = wb.create_sheet('DAT分析')
    ws.append(['DAT面粗糙度：Sa/Sq/Sz以nm计；原界面使用Ra/rms/PV标签，数值由本次处理矩阵重算。'])
    ws.append(['配置文件' if Path(source).suffix.lower()=='.xlsx' else '输入目录', str(source), '处理时间', datetime.now().isoformat(timespec='seconds')])
    ws.append(['整体Sa平均 (nm)', float(np.mean([r['ra'] for r in records]))])
    ws.append(['区域Sa平均 (nm)', *means])
    ws.append(['代表图按本次最终Sa选择；中间行剖面未平均。关闭去噪仍执行去平面。'])
    ws.append(['文件','区域','Sa (nm)','Sz (nm)','Sq (nm)','DAT去噪（已应用）','高通（已应用）',
               '截止 (µm)','去平面Sa (nm)','去周期后Sa (nm)','周期振幅 (nm)','周期 (µm)',
               'kx (周/视场)','ky (周/视场)','空间检查最小改善','有效点比例','代表图',
               '审查提示','源文件SHA256','源文件','配置行','输出名','四联图（已应用）','独立图（已应用）'])
    for r in records:
        fit = r['fit']
        period = 1/math.hypot(fit['kx']/(r['meta']['width']*r['meta']['dx_um']), fit['ky']/(r['meta']['height']*r['meta']['dx_um'])) if fit else None
        ws.append([r['name'],r['batch'],r['ra'],r['pv'],r['rms'],r['setting'][0],r['setting'][1],
                   r['setting'][2] if r['setting'][1]!='关闭' else None,r['baseline']['Sa_nm'],
                   r['after_period']['Sa_nm'],fit['amplitude_nm'] if fit else None,period,
                   fit['kx'] if fit else None,fit['ky'] if fit else None,fit['cv_gain_min'] if fit else None,
                   r['meta']['valid_fraction'],','.join(sorted(r['flags'])),r['review'],r['meta']['sha256'],str(r['path']),
                   r.get('config_row'),r.get('output_stem',r['path'].stem),r.get('combined',True),r.get('single',True)])
        fill='C6EFCE' if 'min' in r['flags'] else 'FCE4D6' if 'medoid' in r['flags'] else None
        if fill:
            for cell in ws[ws.max_row]:cell.fill=PatternFill('solid',fgColor=fill)
    format_sheet(ws,6,[24,18,18,18,18,23,23,18,22,22,22,20,20,20,25,18,22,65,70,90,14,26,22,22])


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_record(r):
    return {k:(str(v) if isinstance(v,Path) else sorted(v) if isinstance(v,set) else v) for k,v in r.items()}


def commit_outputs(root, staged, category):
    """仅清理上一份清单拥有且未被用户修改的文件，不遍历删除其他PNG。"""
    root.mkdir(parents=True, exist_ok=True)
    manifest=root/'manifest.json'
    previous=json.loads(manifest.read_text(encoding='utf8')) if manifest.exists() else {}
    current={p.relative_to(staged).as_posix():sha(p) for p in staged.rglob('*') if p.is_file() and p.name!='manifest.json'}
    # 先检查所有路径，再写入/清理；损坏清单不能造成一半更新。
    for relative in set(current) | set(previous.get('outputs',{})):
        if not (root/relative).resolve().is_relative_to(root.resolve()):
            raise ValueError('输出清单包含越界路径')
    for relative, digest in previous.get('outputs',{}).items():
        target=(root/relative).resolve()
        if not target.is_relative_to(root.resolve()):
            raise ValueError('旧清单包含越界路径')
        if relative not in current and target.is_file():
            if sha(target)==digest:
                target.unlink()
            else:
                print(f'[保留] 已被修改的旧结果：{target}')
    for relative in current:
        target=root/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.is_file() and sha(target)!=previous.get('outputs',{}).get(relative):
            backup=root.parents[1]/'backups'/('outputs_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))/category/relative
            backup.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(target,backup)
        os.replace(staged/relative,target)
    data=json.loads((staged/'manifest.json').read_text(encoding='utf8'))
    data['outputs']=current
    temp=root/'manifest.json.tmp'
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
    os.replace(temp,manifest)


def prepare_groups(records, api, batch=None):
    """显式区域优先，空值用原文件名前缀；同类内按表格顺序分组。"""
    groups={}
    for index,record in enumerate(records):
        match=api.REGION_NAME_RE.match(record['path'].stem)
        name=record.get('region') or (match.group(1) if match else f'未命名{index//(batch or api.DEFAULT_BATCH)+1}')
        groups.setdefault(name,[]).append(record)
    return [items[:batch] if batch else items for items in groups.values()]


def calculate_table(records, api, batch=None):
    groups=prepare_groups(records,api,batch)
    table=api.build_table(groups)
    header,values,detail,groups,means,overall=table
    labels=[]
    for group in groups:
        r=group[0];match=api.REGION_NAME_RE.match(r['path'].stem)
        label=r.get('region') or (match.group(1) if match else str(len(labels)+1))
        labels.append(label)
        for r in group:r['batch']=label
    header[1:]=[f'区域{label}' for label in labels]
    for row,r in zip(detail[1:],[r for g in groups for r in g]):row[1]=r['batch']
    return table


def validate_plot_names(records, api):
    names=set()
    for r in records:
        name=api.tagged_stem(r['output_stem'],r['flags']).casefold()
        if name in names:raise ValueError(f'代表图后缀导致输出名重复：{name}，请修改配置中的输出名')
        names.add(name)


def directory_plans(source, api, batch, types):
    """保留--dir的临时扫描入口；默认批处理走配置表。"""
    images=api.collect_files(source) if types!='dat' else []
    heights=sorted((p for p in source.iterdir() if p.is_file() and p.suffix.lower()=='.dat'),key=lambda p:[int(v) if v.isdigit() else v.casefold() for v in re.split(r'(\d+)',p.name)]) if types!='bmp' else []
    plans=[]
    for kind,files in [('bmp',images),('dat',heights)]:
        for path in api.select_files(files,batch):
            plans.append(dict(path=path,kind=kind,region='',output_stem=path.stem,setting=['关闭','关闭',80.],combined=kind=='dat',single=True,config_row=None,label_style=plot_labels.settings(enabled=kind=='bmp')))
    return plans


def run(api):
    parser=argparse.ArgumentParser(description='按独立Excel配置批量处理Zygo BMP/DAT')
    parser.add_argument('--config',type=Path,help='配置Excel，默认脚本同目录ra_config.xlsx')
    setup=parser.add_mutually_exclusive_group()
    setup.add_argument('--init-config',action='store_true',help='首次创建配置并迁移旧DAT设置，不覆盖已有配置')
    setup.add_argument('--import-config',action='store_true',help='追加导入目录中新文件，BMP默认去噪关、DAT默认开，已有行保留')
    parser.add_argument('--prune-stale',action='store_true',help='导入配置时移除不在扫描目录中的旧文件行')
    parser.add_argument('--dir',type=Path,help='临时扫描目录，使用默认处理参数；与--config互斥')
    parser.add_argument('--out',type=Path)
    parser.add_argument('--batch',type=int)
    parser.add_argument('--no-plot',action='store_true')
    parser.add_argument('--types',choices=['all','bmp','dat'],default='all')
    args=parser.parse_args()
    if args.batch is not None and args.batch<1:parser.error('--batch必须为正整数')
    if args.dir and args.config and not (args.init_config or args.import_config):parser.error('--dir与--config不能同时使用')
    here=Path(api.__file__).resolve().parent
    config_path=(args.config or here/'ra_config.xlsx').resolve()
    destination=(args.out or here).resolve()
    book=destination/'ra_results.xlsx'
    try:
        if args.import_config:
            imported=config.import_files(config_path,args.dir or here/api.INPUT_DIR,args.prune_stale)
            print(f'已导入配置：{config_path}，新增{imported["added"]}行，移除过期行{imported.get("removed",0)}行，共{imported["total"]}行。')
            print('新文件默认：BMP去噪关闭，DAT去噪启用，高通关闭；已有行设置保留。')
            if imported['backup']:print(f'原配置备份：{imported["backup"]}')
            return 0
        if args.init_config:
            source=(args.dir or here/api.INPUT_DIR).resolve()
            rows=config.initial_rows(source,None if args.dir else here/'ra_results.xlsx',config_path.parent)
            config.create_config(config_path,rows)
            print(f'已新建配置：{config_path}（{len(rows)}行）；修改保存后运行run.bat。')
            return 0
        config_digest=None
        if args.dir:
            source=args.dir.resolve()
            if not source.is_dir():raise ValueError(f'输入目录不存在：{source}')
            plans=directory_plans(source,api,args.batch,args.types)
        else:
            config_digest=sha(config_path) if config_path.exists() else None
            plans=config.load_config(config_path)
            source=config_path
        plans=[p for p in plans if args.types=='all' or p['kind']==args.types]
        # --batch在配置模式下按实际分组和表格顺序截取，不改变配置文件。
        if args.batch and not args.dir:
            selected=[]
            for kind in ['bmp','dat']:
                rows=[dict(p,name=p['path'].name) for p in plans if p['kind']==kind]
                selected.extend(p for group in prepare_groups(rows,api,args.batch) for p in group)
            plans=selected
        if not plans:raise ValueError('没有选中的数据行')
        outputs=set()
        for p in plans:
            key=(p['kind'],p['output_stem'].casefold())
            if key in outputs:raise ValueError('输出名重复，请在配置表指定不同输出名')
            outputs.add(key)
        bmp_plans={config.file_key(p['path']):p for p in plans if p['kind']=='bmp'}
        dat_plans=[p for p in plans if p['kind']=='dat']
        if book==config_path:raise ValueError('配置文件与结果文件不能相同')
        if any(p['path']==book for p in plans):raise ValueError('结果文件不能作为输入')
        book_digest=sha(book) if book.exists() else None
        wb=load_workbook(book) if book.exists() else Workbook()
        if not book.exists():del wb[wb.sheetnames[0]]
        if book.exists():
            with book.open('r+b'):pass
        # 配置迁移完成后结果表只保留结果，正常运行不向配置工作簿写入。
        if 'DAT参数' in wb.sheetnames:del wb['DAT参数']
        api.ensure_bat_encoding()
        records=read_bmp([p['path'] for p in bmp_plans.values()],api)
        for r in records:r.update({k:v for k,v in bmp_plans[config.file_key(r['path'])].items() if k!='path'})
        if records:
            header,values,detail,groups,means,overall=calculate_table(records,api)
            validate_plot_names(records,api)
            api.write_xlsx(None,header,values,detail,[r['flags'] for g in groups for r in g],workbook=wb)
            print(f'BMP: {len(records)}张，{len(groups)}区域，整体Ra={overall:.6f} nm')
        destination.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.ra-tmp-',dir=destination) as temporary:
            tmp=Path(temporary);bmp_stage=tmp/'bmp';dat_stage=tmp/'dat'
            for folder in [bmp_stage,bmp_stage/'min',bmp_stage/'medoid',dat_stage/'combined',dat_stage/'single']:
                folder.mkdir(parents=True,exist_ok=True)
            for kind in ['combined','single']:
                for tag in ['min','medoid','_pending']:(dat_stage/kind/tag).mkdir()
            if not args.no_plot:
                for r in records:
                    if not r['single']:continue
                    name=api.tagged_stem(r['output_stem'],r['flags'])+'.png'
                    api.export_plot(r['path'],bmp_stage/name,f'Ra={r["ra"]:.3f} {r["unit"]}',r['label_style'])
                    for tag in r['flags']:shutil.copyfile(bmp_stage/name,bmp_stage/tag/name)
            dat_records=[];assets=None
            for plan in dat_plans:
                path=plan['path'];setting=plan['setting'];stem=plan['output_stem']
                r,arrays,intensity=process_dat(path,setting)
                r.update({k:v for k,v in plan.items() if k!='path'});dat_records.append(r)
                data_dir=dat_stage/'combined'/'data';data_dir.mkdir(parents=True,exist_ok=True)
                np.savez_compressed(data_dir/(stem+'.npz'),**arrays)
                if not args.no_plot and (plan['combined'] or plan['single']):
                    if assets is None:assets=zygo_render.load_assets()
                    images,display=zygo_render.render(arrays['processed_height_nm'],intensity,r['meta']['dx_um'],dat.metrics(arrays['processed_height_nm']),assets,setting[2] if setting[1]!='关闭' else None,label_style=plan['label_style'])
                    for kind,key in [('combined','four_panel'),('single','oblique_plot')]:
                        if plan[kind]:images[key].save(dat_stage/kind/'_pending'/f'{len(dat_records)}.png')
                    r['display']=display
                line=arrays['processed_height_nm'][arrays['processed_height_nm'].shape[0]//2]
                np.savetxt(data_dir/(stem+'_profile.csv'),np.column_stack((np.arange(len(line))*r['meta']['dx_um'],line)),delimiter=',',header='x_um,height_nm',comments='',fmt='%.12g')
                print(f'DAT {path.name}: Sa={r["ra"]:.6f} nm，去噪={setting[0]}，高通={setting[1]}',flush=True)
            if dat_records:
                _,_,_,groups,means,_=calculate_table(dat_records,api)
                validate_plot_names(dat_records,api)
                write_dat_results(wb,dat_records,means,source)
                if not args.no_plot:
                    for index,r in enumerate(dat_records,1):
                        name=api.tagged_stem(r['output_stem'],r['flags'])+'.png'
                        for kind in ['combined','single']:
                            if not r[kind]:continue
                            old=dat_stage/kind/'_pending'/f'{index}.png'
                            old.rename(dat_stage/kind/name)
                            for tag in r['flags']:shutil.copyfile(dat_stage/kind/name,dat_stage/kind/tag/name)
            workbook_tmp=tmp/'ra_results.xlsx';wb.save(workbook_tmp)
            for r in dat_records+records:
                expected=r['meta']['sha256'] if 'meta' in r else r['sha256']
                if sha(r['path'])!=expected:raise ValueError(f'处理期间源数据发生变更：{r["path"]}')
            if config_digest is not None and sha(config_path)!=config_digest:
                raise ValueError('计算期间配置表发生变更，请保存后重跑')
            if (sha(book) if book.exists() else None)!=book_digest:
                raise ValueError('计算期间结果Excel被修改，请保存并关闭后重跑')
            os.replace(workbook_tmp,book)
            for kind,recs,stage in [('bmp',records,bmp_stage),('dat',dat_records,dat_stage)]:
                if not recs:continue
                report=dict(category=kind,input_directory=str(source),config_file=None if args.dir else str(config_path),
                            config_sha256=config_digest,generated_at=datetime.now().isoformat(timespec='seconds'),
                            native_renderer=False if kind=='dat' else None,records=[json_record(r) for r in recs])
                (stage/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
                if args.no_plot:
                    target=destination/'out'/kind;target.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(stage/'manifest.json',target/'analysis_no_plot.json')
                    if kind=='dat':
                        for p in (stage/'combined'/'data').iterdir():
                            q=target/'combined'/'data_no_plot'/p.name;q.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,q)
                else:commit_outputs(destination/'out'/kind,stage,kind)
            for kind in ['combined','single']:
                for tag in ['min','medoid']:(destination/'out'/'dat'/kind/tag).mkdir(parents=True,exist_ok=True)
        print(f'配置：{config_path}' if not args.dir else f'临时扫描：{source}')
        print(f'已输出：{book}')
        if args.no_plot:print('本次未更新PNG；绘图清单仍对应上次生成的图片。')
        return 0
    except PermissionError as error:
        # 替换文件时目标路径在filename2中，读取或预检查时在filename中。
        paths=[Path(name).resolve() for name in (error.filename,error.filename2) if name]
        if book in paths:
            print(f'[错误] 无法读写结果表：{book}',file=sys.stderr)
            print('[提示] 文件可能正被 Excel / WPS 占用。请保存并关闭其中的 ra_results.xlsx 后重新运行。',file=sys.stderr)
            print('[提示] 若已关闭，请检查该文件是否只读，以及输出目录是否有写入权限。',file=sys.stderr)
        elif config_path in paths:
            print(f'[错误] 无法写入配置表：{config_path}',file=sys.stderr)
            print('[提示] 文件可能正被 Excel / WPS 占用。请保存并关闭其中的 ra_config.xlsx 后重新运行。',file=sys.stderr)
            print('[提示] 若已关闭，请检查该文件是否只读，以及配置目录是否有写入权限。',file=sys.stderr)
        else:
            print(f'[错误] {error}',file=sys.stderr)
        return 4
    except (ValueError,OSError,KeyError) as error:
        print(f'[错误] {error}',file=sys.stderr)
        return 4

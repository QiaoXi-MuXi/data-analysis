# -*- coding: utf-8 -*-
"""独立的Excel输入配置；正常运行只读，首次初始化才写入。"""
from __future__ import annotations

import math
import os
import re
import hashlib
import tempfile
from copy import copy
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
import plot_labels

HEADERS = ['文件', '启用(0/1)', '区域', '输出名', 'DAT去噪(0/1)', '高通方式', '截止波长(µm)', '四联图(0/1)', '独立图(0/1)', '数值标注(0/1)', '标注字号(px)', '标注颜色', '标注位置', '描边宽度(px)', '描边颜色']
FILTERS = {'关闭': None, '自然样条候选': 'cubic', '高斯候选': 'gaussian'}
IMAGE_EXTENSIONS = {'.bmp', '.png', '.jpg', '.jpeg'}


def natural_key(name):
    """文件名自然排序：2-1 排在 10-1 前，数字段按整数比较。"""
    text = str(name)
    key = []
    for part in re.split(r'(\d+)', text):
        if part.isdigit():
            key.append((0, int(part), len(part)))
        else:
            key.append((1, part.casefold()))
    return (key, text.casefold())
POSITION_RULE = '"' + ','.join(plot_labels.POSITIONS) + '"'


def normalized(value):
    text=re.sub(r'[(（][^)）]*[)）]', '', str(value))
    return re.sub(r'[\s_]', '', text.strip().casefold())


ALIASES = {
    'file': ['文件','file','源文件'], 'enabled': ['启用','enabled','enable'],
    'region': ['区域','分组','region','group'], 'stem': ['输出名','输出名称','stem'],
    'noise': ['DAT去噪','去噪','denoise'], 'high': ['高通方式','高通','highpass'],
    'cutoff': ['截止波长','cutoff','cutoffum'], 'combined': ['四联图','联合图','combined'],
    'single': ['独立图','单图','single'],
    'label_enabled': ['数值标注', '标注开关', 'label_enabled'],
    'label_size': ['标注字号', '字号', 'label_size'],
    'label_color': ['标注颜色', '字体颜色', 'label_color'],
    'label_position': ['标注位置', 'label_position'],
    'label_stroke': ['描边宽度', 'label_stroke'],
    'label_stroke_color': ['描边颜色', 'label_stroke_color'],
}
LOOKUP = {normalized(alias): key for key, aliases in ALIASES.items() for alias in aliases}


def flag(value, default, where):
    if value is None or str(value).strip() == '':
        return default
    if value in (0, 1):
        return bool(value)
    text = str(value).strip().casefold()
    if text in ('1','1.0','启用','是','on','true','yes','y'):return True
    if text in ('0','0.0','关闭','否','off','false','no','n'):return False
    raise ValueError(f'{where}: 开关请填写0或1')


def file_key(path):
    return os.path.normcase(str(Path(path).resolve()))


def safe_stem(value, where):
    text = str(value).strip()
    reserved = {'CON','PRN','AUX','NUL'} | {f'{prefix}{i}' for prefix in ['COM','LPT'] for i in range(1,10)}
    if not text or text in ('.','..') or re.search(r'[<>:"/\\|?*\x00-\x1f]',text) or text.endswith(('.', ' ')) or text.split('.')[0].upper() in reserved:
        raise ValueError(f'{where}: 输出名不是合法的Windows文件名（不要带路径）')
    return text


def load_config(path):
    """按首表的表头名称读取全部配置行；停用行不检查源文件是否存在。"""
    path = Path(path).resolve()
    if not path.is_file():raise ValueError(f'找不到配置表：{path}，可先运行 --init-config')
    lock = path.with_name('~$' + path.name)
    if lock.exists():
        print(f'提示：{path.name} 正在 Excel 中打开；本次读取磁盘上已保存的版本，'
              'Excel 里未保存的修改不会生效，建议保存关闭后重跑。')
    wb = load_workbook(path, read_only=True, data_only=False)
    try:
        ws = wb.worksheets[0]
        raw_rows = ws.iter_rows(values_only=True)
        header = next(raw_rows, ())
        columns = {}
        for column, name in enumerate(header):
            key = LOOKUP.get(normalized(name)) if name is not None else None
            if key:
                if key in columns:raise ValueError(f'配置表存在重复列：{name}')
                columns[key] = column
        if 'file' not in columns:raise ValueError('配置表第一行必须包含“文件”列')
        plans=[];seen=set();outputs=set()
        for number, row in enumerate(raw_rows, 2):
            def get(key):
                column=columns.get(key)
                return row[column] if column is not None and column<len(row) else None
            value=get('file')
            if value is None or str(value).strip()=='':continue
            where=f'{ws.title}第{number}行'
            if not flag(get('enabled'), True, where+'启用'):continue
            # 禁止把公式当成文件名或参数；无需依赖Excel缓存计算结果。
            if any(isinstance(get(key),str) and get(key).startswith('=') for key in columns):
                raise ValueError(f'{where}: 配置使用实际值，不支持公式')
            source=Path(str(value).strip())
            if not source.is_absolute():
                direct=path.parent/source
                source=direct if direct.is_file() or len(source.parts)>1 else path.parent/'data'/source
            source=source.resolve()
            if not source.is_file():raise ValueError(f'{where}: 文件不存在：{source}')
            kind='dat' if source.suffix.lower()=='.dat' else 'bmp' if source.suffix.lower() in IMAGE_EXTENSIONS else None
            if kind is None:raise ValueError(f'{where}: 不支持的文件类型：{source.name}')
            if file_key(source) in seen:raise ValueError(f'{where}: 源文件重复：{source}')
            seen.add(file_key(source))
            stem=safe_stem(get('stem') or source.stem,where)
            if (kind,stem.casefold()) in outputs:raise ValueError(f'{where}: 同类型输出名重复：{stem}，请修改“输出名”')
            outputs.add((kind,stem.casefold()))
            region=str(get('region')).strip() if get('region') is not None else ''
            high=get('high') or '关闭'
            if high not in FILTERS:raise ValueError(f'{where}: 高通方式只能为{list(FILTERS)}')
            cutoff=get('cutoff')
            cutoff=80. if cutoff is None else cutoff
            if isinstance(cutoff,bool):raise ValueError(f'{where}: 截止波长必须为正数')
            try:cutoff=float(cutoff)
            except (TypeError,ValueError):raise ValueError(f'{where}: 截止波长必须为正数') from None
            if not math.isfinite(cutoff) or cutoff<=0:
                raise ValueError(f'{where}: 截止波长必须为正数')
            try:
                label_style = plot_labels.settings({key: get(key) for key in ('label_enabled', *plot_labels.DEFAULTS)}, enabled=kind == 'bmp')
            except ValueError as exc:
                raise ValueError(f'{where}: {exc}') from None
            plans.append(dict(label_style=label_style, path=source,kind=kind,region=region,output_stem=stem,
                              setting=['启用' if flag(get('noise'),kind=='dat',where+'DAT去噪') else '关闭',high,float(cutoff)],
                              combined=flag(get('combined'),True,where+'四联图'),
                              single=flag(get('single'),True,where+'独立图'),config_row=number))
        if not plans:raise ValueError('配置表没有启用的数据行')
        return plans
    finally:wb.close()


def relative_path(path, base):
    try:return os.path.relpath(Path(path).resolve(),Path(base).resolve())
    except ValueError:return str(Path(path).resolve())


def initial_rows(data, result_book, base):
    """一次性迁移旧参数并登记当前data；正常计算从不读取结果中的参数。"""
    default=['启用','关闭',80.]
    previous={}
    if result_book and Path(result_book).is_file():
        wb=load_workbook(result_book,read_only=True,data_only=False)
        try:
            if 'DAT参数' in wb.sheetnames:
                for row in wb['DAT参数'].iter_rows(min_row=5,max_col=5,values_only=True):
                    name,noise,high,cutoff,source=row
                    # 新文件按类型设置默认值，旧版“默认”行不覆盖新规则。
                    if name!='默认（新文件）' and source:
                        previous[file_key(source)]=(Path(source),[noise,high,cutoff])
        finally:wb.close()
    files=sorted((p for p in Path(data).iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS|{'.dat'}),
                 key=lambda p:natural_key(p.name)) if Path(data).is_dir() else []
    paths={file_key(p):p for p in files}
    paths.update({key:p for key,(p,_) in previous.items() if key not in paths})
    rows=[]
    for key,p in paths.items():
        noise,high,cutoff=previous[key][1] if key in previous else default
        is_dat=p.suffix.lower()=='.dat'
        match=re.match(r'^(.+)-(\d+)$',p.stem)
        rows.append([relative_path(p,base),int(p.is_file()),match.group(1) if match else '', '',
                     int(flag(noise,True,'旧DAT去噪')) if is_dat else 0,high if is_dat else '关闭',
                     cutoff if is_dat else 80.,int(is_dat),1,int(not is_dat),44,'#FFFFFF','左上',3,'#000000'])
    return rows


def sync_validations(ws, columns):
    """旧配置升级：只为标注相关列补齐/更新输入限制，不改动任何单元格数据。"""
    changed = False
    specs = [('label_position', dict(type='list', formula1=POSITION_RULE), '标注位置请使用下拉列表中的值'),
             ('label_size', dict(type='whole', operator='between', formula1='8', formula2='96'), '请填写 8–96 的整数'),
             ('label_stroke', dict(type='whole', operator='between', formula1='0', formula2='8'), '请填写 0–8 的整数')]
    for key, rule, error in specs:
        if key not in columns:
            continue
        letter = get_column_letter(columns[key])
        reference = f'{letter}2:{letter}1048576'
        existing = next((rule_item for rule_item in ws.data_validations.dataValidation
                         if f'{letter}2:' in str(rule_item.sqref)), None)
        if existing is None:
            validation = DataValidation(allow_blank=True, showErrorMessage=True, errorStyle='stop',
                                        error=error, **rule)
            ws.add_data_validation(validation)
            validation.add(reference)
            changed = True
            continue
        # 已有同列规则时只纠正不一致的属性，保留用户原有的适用范围。
        for name, value in rule.items():
            if str(getattr(existing, name) or '') != str(value):
                setattr(existing, name, value)
                changed = True
        if existing.errorStyle != 'stop' or existing.showErrorMessage is not True:
            existing.errorStyle = 'stop'
            existing.showErrorMessage = True
            changed = True
        if existing.error != error:
            existing.error = error
            changed = True
    return changed


def import_files(path, data, prune_stale=False):
    """将目录中新文件追加到配置；可清理不在当前扫描目录中的旧配置行。"""
    path=Path(path).resolve();data=Path(data).resolve()
    if not data.is_dir():raise ValueError(f'导入目录不存在：{data}')
    candidates=initial_rows(data,None,path.parent)
    if not candidates:raise ValueError(f'导入目录没有支持的BMP/图片或DAT文件：{data}')
    if not path.exists():
        create_config(path,candidates)
        return dict(added=len(candidates),total=len(candidates),backup=None)
    original=path.read_bytes()
    digest=hashlib.sha256(original).digest()
    # 先检查占用；不自动关闭Excel，也不丢弃未保存的编辑。
    with path.open('r+b'):pass
    wb=load_workbook(path,data_only=False)
    try:
        ws=wb.worksheets[0];columns={}
        for cell in ws[1]:
            key=LOOKUP.get(normalized(cell.value)) if cell.value is not None else None
            if key:
                if key in columns:raise ValueError(f'配置表存在重复列：{cell.value}')
                columns[key]=cell.column
        keys=[LOOKUP[normalized(name)] for name in HEADERS]
        missing=[name for name,key in zip(HEADERS[:9],keys[:9]) if key not in columns]
        if missing:raise ValueError(f'导入配置缺少列：{", ".join(missing)}')
        # 为旧表补充可选标注列并同步下拉校验；不重建工作簿，不改变既有行的开关与格式。
        upgraded = False
        for title, key in zip(HEADERS[9:], keys[9:]):
            if key not in columns:
                column = ws.max_column + 1
                cell = ws.cell(1, column, title)
                cell._style = copy(ws.cell(1, 1)._style)
                columns[key] = column
                ws.column_dimensions[cell.column_letter].width = 20
                upgraded = True
        if sync_validations(ws, columns):
            upgraded = True
        candidate_keys={file_key(path.parent/row[0]) for row in candidates}
        seen=set();outputs=set();total=0;stale_rows=[]
        for number in range(2,ws.max_row+1):
            value=ws.cell(number,columns['file']).value
            if value is None or str(value).strip()=='':continue
            if str(value).startswith('='):raise ValueError(f'配置第{number}行：文件列不支持公式')
            source=Path(str(value).strip())
            if not source.is_absolute():
                direct=path.parent/source
                source=direct if direct.is_file() or len(source.parts)>1 else path.parent/'data'/source
            key=file_key(source);seen.add(key);total+=1
            if prune_stale and key not in candidate_keys:
                stale_rows.append(number)
                continue
            kind='dat' if source.suffix.lower()=='.dat' else 'bmp'
            stem=ws.cell(number,columns['stem']).value or source.stem
            outputs.add((kind,str(stem).casefold()))
        added=0
        for row in candidates:
            source=(path.parent/row[0]).resolve()
            key=file_key(source)
            if key in seen:continue
            kind='dat' if source.suffix.lower()=='.dat' else 'bmp'
            stem=source.stem;suffix=2
            while (kind,stem.casefold()) in outputs:
                stem=f'{source.stem}_{suffix}';suffix+=1
            if stem!=source.stem:row[3]=stem
            number=ws.max_row+1
            for name,value in zip(keys,row):
                cell=ws.cell(number,columns[name],value)
                cell.font=Font(color='0000FF')
                cell.alignment=Alignment(vertical='center')
                if number>2:
                    previous=ws.cell(number-1,columns[name])
                    cell._style=copy(previous._style)
            ws.cell(number,columns['cutoff']).number_format='0.000'
            seen.add(key);outputs.add((kind,stem.casefold()));added+=1
        if stale_rows:
            for number in reversed(stale_rows):ws.delete_rows(number,1)
            total-=len(stale_rows)
        if not added and not stale_rows and not upgraded:return dict(added=0,removed=0,total=total,backup=None)
        ws.auto_filter.ref=ws.dimensions
        with tempfile.TemporaryDirectory(prefix='.ra-tmp-',dir=path.parent) as folder:
            temporary=Path(folder)/path.name
            wb.save(temporary)
            if hashlib.sha256(path.read_bytes()).digest()!=digest:
                raise ValueError('配置在导入期间发生变化，已停止写入，请保存关闭后重试')
            backup=path.parent/'backups'/('config_import_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))/path.name
            backup.parent.mkdir(parents=True,exist_ok=False)
            backup.write_bytes(original)
            # 文件被Excel占用时替换会失败，保留原表与备份。
            os.replace(temporary,path)
        return dict(added=added,removed=len(stale_rows),total=total+added,backup=str(backup))
    finally:wb.close()


def create_config(path, rows):
    """只新建，不覆盖已有配置；run的正常分析路径不调用此函数。"""
    path=Path(path)
    if path.exists():raise ValueError(f'配置表已经存在，不覆盖：{path}')
    wb=Workbook();ws=wb.active;ws.title='配置';ws.append(HEADERS)
    for row in rows:ws.append(row)
    ws.freeze_panes='B2';ws.sheet_view.showGridLines=False
    widths=[65,14,18,26,18,22,21,17,17,19,19,20,18,19,20]
    for column,width in enumerate(widths,1):ws.column_dimensions[ws.cell(1,column).column_letter].width=width
    for cell in ws[1]:
        cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='1F4E78')
        cell.alignment=Alignment(wrap_text=True,vertical='center')
    ws.row_dimensions[1].height=32
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.font=Font(color='0000FF');cell.alignment=Alignment(vertical='center')
        row[6].number_format='0.000'
    ws.auto_filter.ref=ws.dimensions
    for formula,columns in [('"0,1"',['B','E','H','I','J']),('"关闭,自然样条候选,高斯候选"',['F'])]:
        rule=DataValidation(type='list',formula1=formula,allow_blank=True)
        rule.showErrorMessage=True;rule.errorStyle='stop';rule.error='请使用下拉列表中的值'
        ws.add_data_validation(rule)
        for column in columns:rule.add(f'{column}2:{column}1048576')
    rule=DataValidation(type='decimal',operator='greaterThan',formula1=0,allow_blank=True)
    rule.showErrorMessage=True;ws.add_data_validation(rule);rule.add('G2:G1048576')
    for column, lower, upper in [('K', 8, 96), ('N', 0, 8)]:
        rule=DataValidation(type='whole',operator='between',formula1=lower,formula2=upper,allow_blank=True)
        rule.showErrorMessage=True;rule.error=f'请填写 {lower}–{upper} 的整数'
        ws.add_data_validation(rule);rule.add(f'{column}2:{column}1048576')
    rule=DataValidation(type='list',formula1=POSITION_RULE,allow_blank=True)
    rule.showErrorMessage=True;ws.add_data_validation(rule);rule.add('M2:M1048576')
    help_sheet=wb.create_sheet('说明')
    notes=[
        ['操作','修改配置表 → 保存 → 双击run.bat；结果只写入ra_results.xlsx和out，运行不改配置。'],
        ['文件','可填绝对路径，或相对本配置文件的路径；只填文件名时优先同目录，再查data/。'],
        ['启用(0/1)','1处理、0跳过；停用行即使文件不存在也不会报错。空值按1。'],
        ['区域','自定义分组；留空按原文件名前缀分组，无法匹配的每5个一组。BMP与DAT各自统计。'],
        ['输出名','留空使用原文件名（不含扩展名）；不同目录同名文件可用此列区分。不要填写路径。'],
        ['DAT去噪(0/1)','新导入DAT默认1，BMP/图片默认0；1启用周期分离候选、0关闭。空值按文件类型；已填0/1优先。'],
        ['高通方式','关闭、自然样条候选、高斯候选；仅对DAT生效。空值关闭。'],
        ['截止波长(µm)','正数，空值80；仅高通开启时参与计算。高通候选不等同原生Gauss Spline。'],
        ['四联图(0/1)','DAT完整四联图开关；BMP无四联图。空值按1。'],
        ['独立图(0/1)','BMP裁图或DAT独立Oblique图开关；空值按1。'],
        ['数值标注(0/1)','1添加数值、0关闭附加文字。旧表缺列/空值：BMP启用、DAT关闭，保持旧效果。DAT启用时标注处理后面的Sa，不改变原有图框/刻度。'],
        ['标注样式','字号8–96输出像素，默认44；字宽超出左右留白时自动收敛，不裁字。颜色使用#RRGGBB，默认#FFFFFF；位置可选'+' / '.join(plot_labels.POSITIONS)+'，默认左上；填上中、顶部居中等等价写法会自动识别。'],
        ['描边','宽度0–8像素，默认3；0无描边。描边颜色默认#000000。字号过大会占用更多绘图面积。'],
        ['统计','关闭出图仍参与数值和min/medoid选择；代表图不出图时该类别没有此样本副本。'],
        ['编辑规则','仅首表控制计算；表头可重排，空行跳过，可增加备注列；请填写实际值，不使用公式。'],
        ['输入范围','只处理配置表启用的行，顺序沿用表格；data中新增文件需自行加行或另建配置。'],
        ['科学限制','去噪是待复测验证的候选，绘图不是原生MetroPro渲染，不能保证逐像素一致。'],
    ]
    for row in notes:help_sheet.append(row)
    help_sheet.column_dimensions['A'].width=24;help_sheet.column_dimensions['B'].width=110
    for row in help_sheet:
        for cell in row:cell.alignment=Alignment(wrap_text=True,vertical='center')
        help_sheet.row_dimensions[row[0].row].height=34
    path.parent.mkdir(parents=True,exist_ok=True)
    # 独占创建，避免检查与保存之间覆盖另一个进程新建的配置。
    with path.open('xb') as handle:wb.save(handle)

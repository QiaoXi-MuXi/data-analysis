# -*- coding: utf-8 -*-
"""科研绘图模板目录、工作副本与桌面运行适配，复用现有绘图引擎。"""
from __future__ import annotations

from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import uuid

import csv as _csv

from documents import (resolve_sci_pic_style, sci_pic_style_rows, sci_pic_style_values,
                       write_sci_pic_style_rows)
from model import (Command, PlotRequest, PlotResult, SciPicRequest, SourceRef,
                   SCI_PIC_PROJECT, as_path, sci_pic_command)
from runs import PreparedRun, save_record


def catalog(root):
    folder = Path(root) / 'sci-pic'
    manifest = json.loads((folder / 'templates.json').read_text(encoding='utf-8'))
    if manifest.get('version') != 1 or not isinstance(manifest.get('templates'), list):
        raise ValueError('绘图模板目录格式不正确。')
    entries, identifiers = [], set()
    for entry in manifest['templates']:
        if not isinstance(entry, dict) or not all(isinstance(entry.get(k), str) and entry[k].strip()
                for k in ('id', 'title', 'workbook', 'preview', 'plot_type', 'description', 'example')):
            raise ValueError('绘图模板条目缺少必要信息。')
        if entry['id'] in identifiers:
            raise ValueError('绘图模板标识重复。')
        identifiers.add(entry['id'])
        item = dict(entry)
        for key, directory in (('workbook', 'templates'), ('preview', 'previews')):
            path = (folder / entry[key]).resolve()
            if not path.is_relative_to((folder / directory).resolve()) or not path.is_file():
                raise ValueError(f'模板文件缺失或路径不正确：{entry[key]}')
            item[key] = path
        entries.append(item)
    if not entries:
        raise ValueError('尚未配置绘图模板。')
    return entries


def engine(root):
    path = Path(root) / 'sci-pic' / 'plot_sci_pic.py'
    spec = importlib.util.spec_from_file_location('_workbench_sci_pic', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def working_copy(root, entry, *, target_dir=None):
    """创建唯一工作副本，保留模板的全部单元格、格式和公式。"""
    root = Path(root).resolve()
    if target_dir is None:
        folder = root / 'sci-pic' / 'out' / 'workbooks'
    else:
        folder = as_path(target_dir, root)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (Path(entry['workbook']).stem + '_' + datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:6] + '.xlsx')
    with target.open('xb') as stream:
        stream.write(Path(entry['workbook']).read_bytes())
    return target


def style_rows():
    """Canonical field names shown in the GUI; units are pt/dpi, never px."""
    return list(sci_pic_style_rows())


def add_style_rows(workbook, values, *, root=None):
    """Add or update rows in the work copy's isolated 全局设置 sheet."""
    return write_sci_pic_style_rows(workbook, values, module_root=root)


def inspect_style(workbook):
    settings, locations = sci_pic_style_values(workbook)
    return {'settings': settings, 'locations': locations, 'fields': style_rows()}


def validate_style(root, workbook=None, overrides=None, *, strict=False, output=None,
                   excel_settings=None, locations=None):
    return resolve_sci_pic_style(root, workbook=workbook, overrides=overrides,
                                 strict=strict, output=output,
                                 excel_settings=excel_settings, locations=locations)


def create_from_document(root, entry, document, *, source_project='Ra', mapping=None,
                         target_dir=None, style_values=None):
    """Build an explicit category/value/group work copy from an analysis result.

    Approved initial shapes are a Nano-style summary table or a Raman comparison
    category/value table. Raw spectral series are never silently converted.
    """
    from openpyxl import Workbook
    raw_rows = getattr(document, 'rows', None) or []
    mapping = dict(mapping or {})
    category_key = mapping.get('category', '')
    value_key = mapping.get('value', '')
    group_key = mapping.get('group', '')
    if not category_key or not value_key:
        raise ValueError('必须明确指定类别列和数值列。')
    records, ignored, errors = [], 0, []
    for number, row in enumerate(raw_rows, 1):
        if not isinstance(row, dict) or not row.get('enabled', True):
            ignored += 1
            continue
        missing = [key for key in (category_key, value_key) if row.get(key) in (None, '')]
        if missing:
            errors.append({'row': number, 'message': '缺少：' + '、'.join(missing)})
            continue
        records.append((str(row.get(category_key)).strip(), row.get(value_key),
                        '' if not group_key else str(row.get(group_key) or '').strip()))
    if not records:
        raise ValueError('映射后没有可绘制的数据行。')
    wb = Workbook()
    data = wb.active
    data.title = '数据'
    for column, title in enumerate(('类别', '数值', '备注说明'), 1):
        data.cell(row=4, column=column, value=title)
    for index, (category, value, group) in enumerate(records, 5):
        data.cell(row=index, column=1, value=category)
        data.cell(row=index, column=2, value=value)
        data.cell(row=index, column=3, value=group)
    config = wb.create_sheet('设置')
    for column, title in enumerate(('参数', '填写值', '说明'), 1):
        config.cell(row=4, column=column, value=title)
    legend = {item['key']: item.get('default', '') for item in [
        {'key': '图名', 'default': Path(document.path).stem if getattr(document, 'path', None) else '分析结果'},
        {'key': '图标题', 'default': '分析结果'}, {'key': '横轴标题', 'default': '类别'},
        {'key': '纵轴标题', 'default': '数值'}, {'key': '纵轴单位', 'default': ''},
        {'key': '配色', 'default': 'academic'}, {'key': '图片宽度_cm', 'default': '16'},
        {'key': '图片高度_cm', 'default': '9'}, {'key': '分辨率_dpi', 'default': '600'},
        {'key': '小数位数', 'default': '2'}, {'key': '显示数值', 'default': '是'},
        {'key': '标签旋转_度', 'default': '0'}, {'key': '图类型', 'default': entry['plot_type']},
        {'key': '显示倍数', 'default': '否'}, {'key': '倍数小数位数', 'default': '2'}]}
    for index, key in enumerate(legend, 5):
        config.cell(row=index, column=1, value=key)
        config.cell(row=index, column=2, value=legend[key])
    workbook = working_copy(root, entry, target_dir=target_dir)
    wb.save(workbook)
    wb.close()
    add_style_rows(workbook, style_values or {}, root=root)
    source_path = Path(document.path).resolve() if getattr(document, 'path', None) else None
    manifest = {'version': 1, 'source_project': source_project,
                'source_file': str(source_path) if source_path else '',
                'source_sha256': hashlib.sha256(source_path.read_bytes()).hexdigest() if source_path and source_path.is_file() else '',
                'source_mtime': datetime.fromtimestamp(source_path.stat().st_mtime).isoformat(timespec='seconds') if source_path and source_path.is_file() else '',
                'mapping': mapping, 'rows_written': len(records), 'rows_ignored': ignored,
                'row_errors': errors, 'template_id': entry['id'],
                'workbook': str(workbook)}
    manifest_path = workbook.with_suffix('.manifest.json')
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {'workbook': workbook, 'manifest': manifest, 'manifest_path': manifest_path,
            'row_count': len(records), 'ignored': ignored, 'errors': errors}


class SourceMappingError(ValueError):
    """Source result cannot be mapped to a SCI-Pic template."""


def _coerce_number(value):
    if value is None or value == '':
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(',', '')
    if text.endswith('%'):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None


def read_source_table(path):
    """Read an analysis result into columns/rows; Qt-free, read-only.

    Supports .xlsx, .csv and normalizes .json lists/dicts of objects. Raw Raman
    spectrum text files are rejected explicitly instead of being forced onto a
    bar/line template.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == '.txt':
        raise SourceMappingError('原始光谱为波数—强度序列，不能直接映射为柱状/折线模板；请使用 Raman 比较汇总结果。')
    if not path.is_file():
        raise SourceMappingError(f'源文件不存在：{path.name}')
    rows, errors = [], []
    if suffix == '.xlsx':
        from openpyxl import load_workbook
        book = load_workbook(path, read_only=True, data_only=True)
        sheet = book['数据'] if '数据' in book.sheetnames else book.worksheets[0]
        iterator = sheet.iter_rows(values_only=True)
        try:
            header = [str(cell).strip() if cell is not None else '' for cell in next(iterator)]
        except StopIteration:
            header = []
        columns = [name for name in header if name]
        offset = len(header) - len(columns)
        for number, raw in enumerate(iterator, 2):
            record = {columns[index]: raw[index] for index in range(len(columns))}
            if not any(value not in (None, '') for value in record.values()):
                continue
            rows.append(record)
        book.close()
    elif suffix == '.csv':
        with path.open('r', encoding='utf-8-sig', newline='') as stream:
            reader = _csv.DictReader(stream)
            columns = list(reader.fieldnames or [])
            for record in reader:
                if any(value not in (None, '') for value in record.values()):
                    rows.append(dict(record))
    elif suffix == '.json':
        payload = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(payload, dict):
            if all(isinstance(item, dict) for item in payload.values()):
                rows = [{'category': key, **value} for key, value in payload.items()]
            elif all(isinstance(item, (str, int, float)) for item in payload.values()):
                rows = [{'category': key, 'value': value} for key, value in payload.items()]
            else:
                raise SourceMappingError('JSON 结果结构无法映射为类别—数值表。')
        elif isinstance(payload, list):
            if not all(isinstance(item, dict) for item in payload):
                raise SourceMappingError('JSON 结果必须是对象列表才能映射为类别—数值表。')
            rows = [dict(item) for item in payload]
        else:
            raise SourceMappingError('JSON 结果结构无法映射为类别—数值表。')
        columns = sorted({key for row in rows for key in row})
    else:
        raise SourceMappingError(f'不支持的源文件类型：{suffix or "无扩展名"}')
    return {'columns': columns, 'rows': rows, 'row_count': len(rows),
            'ignored': len(errors), 'errors': errors}


def propose_mapping(path, source_project=''):
    """Detect candidate category/value/group columns; never guess a mapping.

    Returns a proposal the GUI shows to the user; actual mapping is only applied
    after explicit confirmation.
    """
    table = read_source_table(path)
    columns = table['columns']
    if not columns:
        raise SourceMappingError('源结果中没有可识别的列。')
    numeric, text, group_candidates = [], [], []
    for column in columns:
        sample = [row.get(column) for row in table['rows'][:500]]
        numbers = [_coerce_number(value) for value in sample]
        numbers = [item for item in numbers if item is not None]
        if numbers and len(numbers) >= max(1, len(sample) // 2):
            numeric.append(column)
        else:
            text.append(column)
            distinct = {str(value).strip() for value in sample if value not in (None, '')}
            if 2 <= len(distinct) <= 8:
                group_candidates.append(column)
    category_candidates = text
    grouped = bool(group_candidates)
    supported = bool(category_candidates) and bool(numeric)
    if not supported:
        reason = '缺少可作为类别的文本列或可作为数值的列；请先在分析结果中确认类别—数值关系。'
    else:
        reason = '可选择类别列、数值列' + ('及分组列' if grouped else '；该结果为单系列，不适用双组对比模板。')
    return {'source_path': str(Path(path).resolve()), 'source_project': source_project,
            'columns': columns, 'rows': table['row_count'], 'ignored': table['ignored'],
            'errors': table['errors'], 'category_candidates': category_candidates,
            'value_candidates': numeric, 'group_candidates': group_candidates,
            'supported': supported, 'reason': reason,
            'detected_shape': 'grouped' if grouped else 'single-series'}


def _fingerprint(path):
    entry = Path(path)
    if not entry.is_file():
        return {'sha256': '', 'mtime': '', 'size': 0}
    return {'sha256': hashlib.sha256(entry.read_bytes()).hexdigest(),
            'mtime': datetime.fromtimestamp(entry.stat().st_mtime).isoformat(timespec='seconds'),
            'size': entry.stat().st_size}


def create_work_copy_from_mapping(root, entry, source_path, source_project, source_run_id,
                                  mapping, target_dir=None, *, style_values=None,
                                  style_strict=False):
    """Write a work copy after the user explicitly confirms the column mapping."""
    root = Path(root).resolve()
    source_path = Path(source_path).resolve()
    proposal = propose_mapping(source_path, source_project)
    mapping = dict(mapping or {})
    category_key = mapping.get('category', '')
    value_key = mapping.get('value', '')
    group_key = mapping.get('group', '')
    if category_key not in proposal['category_candidates']:
        raise SourceMappingError(f'类别列 {category_key!r} 不可用；可选：' + '、'.join(proposal['category_candidates']))
    if value_key not in proposal['value_candidates']:
        raise SourceMappingError(f'数值列 {value_key!r} 不可用；可选：' + '、'.join(proposal['value_candidates']))
    if group_key and group_key not in proposal['group_candidates']:
        raise SourceMappingError(f'分组列 {group_key!r} 不可用；可选：' + '、'.join(proposal['group_candidates']))
    records, ignored, errors = [], 0, []
    for number, row in enumerate(proposal['rows'] and read_source_table(source_path)['rows'], 1):
        category, raw_value, group = row.get(category_key), row.get(value_key), row.get(group_key) if group_key else ''
        if category in (None, '') and raw_value in (None, ''):
            ignored += 1
            continue
        value = _coerce_number(raw_value)
        if category in (None, '') or value is None:
            errors.append({'row': number, 'message': '类别为空或数值不可解析'})
            continue
        label = str(category).strip() if not group_key else f"{category}-{str(group or '').strip()}"
        records.append((label, value, str(group or '').strip()))
    if not records:
        raise SourceMappingError('映射后没有可绘制的数据行；请检查所选列。')
    # 双组模板需要恰好两组；分组列缺失时由用户选择单系列模板。
    if entry['plot_type'] == '双组柱状' and len({group for _, _, group in records}) != 2:
        raise SourceMappingError('双组对比模板需要两个分组；当前分组数量不是 2，请改选柱状折线模板或核对分组列。')
    from openpyxl import Workbook
    wb = Workbook()
    data = wb.active
    data.title = '数据'
    for column, title in enumerate(('类别', '数值', '备注说明'), 1):
        data.cell(row=4, column=column, value=title)
    for index, (category, value, group) in enumerate(records, 5):
        data.cell(row=index, column=1, value=category)
        data.cell(row=index, column=2, value=value)
        data.cell(row=index, column=3, value=group)
    config = wb.create_sheet('设置')
    for column, title in enumerate(('参数', '填写值', '说明'), 1):
        config.cell(row=4, column=column, value=title)
    legend = {item['key']: item['default'] for item in [
        {'key': '图名', 'default': Path(source_path).stem},
        {'key': '图标题', 'default': '分析结果'}, {'key': '横轴标题', 'default': '类别'},
        {'key': '纵轴标题', 'default': '数值'}, {'key': '纵轴单位', 'default': ''},
        {'key': '配色', 'default': 'academic'}, {'key': '图片宽度_cm', 'default': '16'},
        {'key': '图片高度_cm', 'default': '9'}, {'key': '分辨率_dpi', 'default': '600'},
        {'key': '小数位数', 'default': '2'}, {'key': '显示数值', 'default': '是'},
        {'key': '标签旋转_度', 'default': '0'}, {'key': '图类型', 'default': entry['plot_type']},
        {'key': '显示倍数', 'default': '否'}, {'key': '倍数小数位数', 'default': '2'}]}
    for index, key in enumerate(legend, 5):
        config.cell(row=index, column=1, value=key)
        config.cell(row=index, column=2, value=legend[key])
    workbook = working_copy(root, entry, target_dir=target_dir)
    wb.save(workbook)
    wb.close()
    add_style_rows(workbook, style_values or {}, root=root)
    fingerprint = _fingerprint(source_path)
    manifest = {'version': 2, 'source_project': source_project, 'source_run_id': source_run_id,
                'source_file': str(source_path), 'source_sha256': fingerprint['sha256'],
                'source_mtime': fingerprint['mtime'], 'source_size': fingerprint['size'],
                'mapping': mapping, 'rows_written': len(records), 'rows_ignored': ignored,
                'row_errors': errors, 'template_id': entry['id'], 'template_title': entry['title'],
                'workbook': str(workbook), 'resolved_config': ''}
    manifest_path = workbook.with_suffix('.manifest.json')
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {'workbook': workbook, 'manifest': manifest, 'manifest_path': manifest_path,
            'row_count': len(records), 'ignored': ignored, 'errors': errors}


def verify_source(manifest_path):
    """Check the recorded source file still exists and matches hash/mtime."""
    manifest_path = Path(manifest_path)
    if not manifest_path.is_file():
        return {'status': 'missing-manifest', 'message': '缺少运行清单，无法核对源文件。'}
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    source = Path(str(manifest.get('source_file', '')))
    if not source.is_file():
        return {'status': 'source-missing', 'message': f'源文件已不存在：{source.name}', 'manifest': manifest}
    fingerprint = _fingerprint(source)
    if fingerprint['sha256'] and fingerprint['sha256'] != manifest.get('source_sha256'):
        return {'status': 'source-changed', 'message': '源文件内容已变化，请核对后再继续。', 'manifest': manifest}
    return {'status': 'ok', 'message': '源文件与清单记录一致。', 'manifest': manifest}


def _overlay_path(root, folder, style_config, overrides):
    """Merge GUI overrides into an isolated style config without touching inputs."""
    if not overrides:
        return str(style_config) if style_config else ''
    merged, locations = {}, {}
    if style_config:
        settings, locations = sci_pic_style_values(style_config)
        merged.update(settings)
    merged.update(overrides)
    path = folder / 'style_config.json'
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return str(path)


def prepare_plot(root, entry, source, output, *, style_config=None, style_overrides=None,
                 style_strict=False, resolved_path=None, create_resolved=None,
                 source_project='', source_run_id='', manifest_path=''):
    """Prepare an isolated SCI-Pic run; old calls keep their original behavior."""
    root = Path(root).resolve()
    source, output = as_path(source, root), as_path(output, root)
    if output.exists() and not output.is_dir():
        raise ValueError('输出位置必须是文件夹。')
    protected = [(root / 'sci-pic' / name).resolve() for name in ('templates', 'previews')]
    if any(output == p or output.is_relative_to(p) for p in protected):
        raise ValueError('输出不能写入模板或样式预览文件夹。')
    raw, points, config = engine(root).load_input(source)
    if config['图类型'] != entry['plot_type']:
        raise ValueError('工作簿的图类型与所选样式不同，请选择对应模板。')
    identity = datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:8]
    folder = output / 'runs' / identity
    folder.mkdir(parents=True, exist_ok=False)
    snapshot = folder / 'input.xlsx'
    with snapshot.open('xb') as stream:
        stream.write(raw)
    effective_style = _overlay_path(root, folder, style_config, style_overrides)
    resolved = Path(resolved_path).resolve() if resolved_path else folder / 'resolved_config.json'
    if create_resolved is None:
        create_resolved = bool(effective_style or style_strict)
    wants_style = bool(effective_style or style_strict or create_resolved)
    arguments = ['--excel', str(snapshot), '--out', str(folder / 'results')]
    if effective_style:
        arguments.extend(['--style-config', str(as_path(effective_style, root))])
    if style_strict:
        arguments.append('--style-strict')
    if wants_style:
        arguments.extend(['--write-resolved', str(resolved)])
    request = SciPicRequest(SCI_PIC_PROJECT.key, entry['id'], str(source),
                            str(folder / 'results'), style_config=effective_style,
                            style_overrides=dict(style_overrides or {}),
                            style_strict=bool(style_strict),
                            write_resolved=str(resolved) if wants_style else '')
    command = sci_pic_command(root, request, results=(folder / 'results',), resolved=resolved)
    resolved_payload = None
    if wants_style:
        merged = dict(config)
        merged_locations = dict(config.get('_setting_locations') or {})
        if effective_style:
            overlay, overlay_locations = sci_pic_style_values(effective_style)
            merged.update(overlay)
            merged_locations.update(overlay_locations)
        resolved_payload = validate_style(
            root, excel_settings=merged, overrides=style_overrides or {},
            strict=style_strict, output=resolved, locations=merged_locations)
    record = {'version': 2, 'id': identity, 'module': 'sci-pic',
        'started': datetime.now().isoformat(timespec='seconds'), 'status': 'prepared',
        'message': '工作簿快照已保存', 'input_count': 1,
        'inputs': [{'path': str(source), 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}],
        'template_id': entry['id'], 'template_title': entry['title'], 'settings': config,
        'point_count': len(points), 'snapshot': str(snapshot),
        'source_project': source_project, 'source_run_id': source_run_id,
        'manifest_path': manifest_path,
        'style_config': effective_style, 'style_strict': bool(style_strict),
        'resolved_config': str(resolved) if wants_style else '',
        'resolved_preview': resolved_payload,
        'command': {'program': str(root / 'python' / 'python.exe'), 'script': str(command.script), 'arguments': list(command.arguments)},
        'result_roots': [str(p) for p in command.results], 'files': [], 'output_parent': str(output)}
    prepared = PreparedRun(command, record, folder / 'run.json', root / 'out' / 'gui' / 'history' / 'sci-pic' / (identity + '.json'))
    save_record(prepared)
    return prepared


def _read_manifest_mapping(manifest_path):
    if not manifest_path:
        return None
    path = Path(manifest_path)
    if not path.is_file():
        return None
    try:
        return dict(json.loads(path.read_text(encoding='utf-8')).get('mapping') or {})
    except (OSError, json.JSONDecodeError):
        return None


def build_plot_request(record, root=None):
    """Build the serializable PlotRequest contract from a prepared run record."""
    refs = tuple(SourceRef(path=str(item.get('path', '')), sha256=str(item.get('sha256', '')),
                          size=int(item.get('size', 0) or 0)) for item in record.get('inputs', []))
    manifest_path = record.get('manifest_path', '')
    return PlotRequest(
        source_project=str(record.get('source_project', '')),
        source_run_id=str(record.get('source_run_id', '')),
        source_refs=refs,
        template_id=str(record.get('template_id', '')),
        work_copy=str(record.get('snapshot', '')),
        output_dir=str(record.get('output_parent', '')),
        style_config=str(record.get('style_config', '')),
        style_overrides=dict(record.get('style_overrides') or {}),
        style_strict=bool(record.get('style_strict', False)),
        resolved_config=str(record.get('resolved_config', '')),
        mapping=_read_manifest_mapping(manifest_path))


def collect_plot_result(record):
    """Collect the serializable PlotResult contract from a finished run record."""
    return PlotResult(
        status=str(record.get('status', '')),
        outputs=tuple(str(item) for item in record.get('files', [])),
        log_path=str(record.get('log_path', '')),
        resolved_config=str(record.get('resolved_config', '')),
        manifest=str(record.get('manifest_path', '')),
        message=str(record.get('message', '')),
        errors=tuple(str(item) for item in record.get('errors', [])))

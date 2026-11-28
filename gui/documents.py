# -*- coding: utf-8 -*-
"""桌面项目配置、兼容导入和受保护的本地保存；不改变科学计算模块。"""
from __future__ import annotations

from copy import deepcopy
import csv
from dataclasses import dataclass, field
from datetime import date, datetime
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import uuid


@dataclass(frozen=True)
class Column:
    key: str
    title: str
    kind: str = 'text'
    choices: tuple[str, ...] = ()


SCHEMAS = {
    'Ra': (
        Column('file', '源文件'), Column('enabled', '参与分析', 'enabled'),
        Column('region', '区域 / 分组'), Column('stem', '输出名称'),
        Column('noise', 'DAT 去噪（候选）', 'bool'),
        Column('high', '高通方式', 'choice', ('关闭', '自然样条候选', '高斯候选')),
        Column('cutoff', '截止波长 / µm', 'positive'),
        Column('combined', '生成四联图', 'bool'), Column('single', '生成独立图', 'bool'),
        Column('label_enabled', '附加数值标注', 'bool'),
        Column('label_size', '标注字号 / px', 'font_size'),
        Column('label_color', '标注颜色', 'color'),
        Column('label_position', '标注位置', 'choice', ('左上', '上方中间', '右上', '左下', '下方中间', '右下')),
        Column('label_stroke', '描边宽度 / px', 'stroke'),
        Column('label_stroke_color', '描边颜色', 'color'),
    ),
    'Raman': (
        Column('file', '光谱文件'), Column('enabled', '参与分析', 'enabled'),
        Column('label', '图例名称'), Column('normalize', '强度归一化', 'bool'),
        Column('peaks', '标注峰位', 'bool'), Column('height', '峰高阈值', 'number'),
        Column('offset', '纵向偏移', 'number'), Column('xmin', '区间下限 / cm⁻¹', 'number'),
        Column('xmax', '区间上限 / cm⁻¹', 'number'),
    ),
    'CLSM': (), 'ER': (),
}
RA_HEADERS = ['文件', '启用(0/1)', '区域', '输出名', 'DAT去噪(0/1)', '高通方式', '截止波长(µm)', '四联图(0/1)', '独立图(0/1)', '数值标注(0/1)', '标注字号(px)', '标注颜色', '标注位置', '描边宽度(px)', '描边颜色']
RA_KEYS = ['file', 'enabled', 'region', 'stem', 'noise', 'high', 'cutoff', 'combined', 'single', 'label_enabled', 'label_size', 'label_color', 'label_position', 'label_stroke', 'label_stroke_color']
RAMAN_HEADERS = ['文件', '图例名称', '归一化(0/1)', '标注峰位(0/1)', '峰高阈值', '纵向偏移', '区间下限', '区间上限']
RAMAN_KEYS = ['file', 'label', 'normalize', 'peaks', 'height', 'offset', 'xmin', 'xmax']
COMPARE_HEADERS = ['文件', '图例名称', '启用(0/1)', '峰高阈值', '标注峰位(0/1)']
COMPARE_KEYS = ['file', 'label', 'enabled', 'height', 'peaks']
COMPARE_PARAMETERS = {
    '基准文件': 'reference', '基准名称': 'reference_label',
    '基准峰高阈值': 'reference_height', '区间下限': 'xmin',
    '区间上限': 'xmax', '匹配容差': 'tolerance',
    '归一化(0/1)': 'normalize', '峰位校正': 'shift',
    '激光线排除半宽': 'zero_guard',
}
ALIASES = {
    'Ra': {'file': ['文件', 'file', '源文件'], 'enabled': ['启用', 'enabled', 'enable', '参与分析'],
           'region': ['区域', '分组', 'region', 'group', '区域/分组'], 'stem': ['输出名', '输出名称', 'stem'],
           'noise': ['DAT去噪', '去噪', 'denoise', 'noise'], 'high': ['高通方式', '高通', 'highpass', 'high'],
           'cutoff': ['截止波长', 'cutoff', 'cutoffum', '截止波长/µm'], 'combined': ['四联图', '联合图', 'combined', '生成四联图'],
           'single': ['独立图', '单图', 'single', '生成独立图'],
           'label_enabled': ['数值标注', '标注开关', 'label_enabled', '附加数值标注'],
           'label_size': ['标注字号', '字号', 'label_size', '标注字号/px'],
           'label_color': ['标注颜色', '字体颜色', 'label_color'],
           'label_position': ['标注位置', 'label_position'],
           'label_stroke': ['描边宽度', 'label_stroke', '描边宽度/px'],
           'label_stroke_color': ['描边颜色', 'label_stroke_color']},
    'Raman': {'file': ['文件', '文件名', 'file', '光谱文件'], 'enabled': ['启用', 'enabled', '参与分析'],
              'label': ['图例', '图例名称', '名称', 'label'], 'normalize': ['归一化', 'normalize', '强度归一化'],
              'peaks': ['标注峰位', '峰位', 'peaks'], 'height': ['峰高阈值', '峰高', '阈值', 'height'],
              'offset': ['纵向偏移', '偏移', 'offset'], 'xmin': ['区间下限', '起点', 'xmin', '区间下限/cm⁻¹'],
              'xmax': ['区间上限', '终点', 'xmax', '区间上限/cm⁻¹']},
}
PATH_OPTIONS = {'input', 'output', 'config', 'template', 'xlsx', 'compare_reference',
                'mode_config'}


def normalize(value):
    return re.sub(r'[\s_]', '', re.sub(r'[(（][^)）]*[)）]', '', str(value or '')).casefold())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).is_file() else None


def relative(path, base):
    try:
        return os.path.relpath(Path(path).resolve(), Path(base).resolve())
    except ValueError:
        return str(Path(path).resolve())


def flag(value, default=False):
    if value is None or str(value).strip() == '':
        return default
    if value is True or value == 1 or str(value).strip().casefold() in ('1', '1.0', 'true', 'yes', 'y', '是', '启用', 'on'):
        return True
    if value is False or value == 0 or str(value).strip().casefold() in ('0', '0.0', 'false', 'no', 'n', '否', '关闭', 'off'):
        return False
    raise ValueError('开关请使用默认、启用或关闭')


def parse_value(column, value):
    if value is None or (isinstance(value, str) and value.strip() == ''):
        return None
    if column.kind in ('enabled', 'bool'):
        return flag(value)
    if column.kind == 'color':
        text = str(value).strip()
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', text):
            raise ValueError(f'{column.title}请填写 #RRGGBB，例如 #FFFFFF')
        return text.upper()
    if column.kind in ('font_size', 'stroke'):
        lower, upper = (8, 96) if column.kind == 'font_size' else (0, 8)
        try:
            number = float(value)
        except (ValueError, TypeError):
            raise ValueError(f'{column.title}需要 {lower}–{upper} 的整数') from None
        if isinstance(value, bool) or not math.isfinite(number) or not number.is_integer() or not lower <= number <= upper:
            raise ValueError(f'{column.title}需要 {lower}–{upper} 的整数')
        return int(number)
    if column.kind in ('number', 'positive'):
        if isinstance(value, bool):
            raise ValueError(f'{column.title}需要数字')
        try:
            number = float(value)
        except (ValueError, TypeError):
            raise ValueError(f'{column.title}需要数字，留空表示沿用原默认值') from None
        if not math.isfinite(number) or (column.kind == 'positive' and number <= 0):
            raise ValueError(f'{column.title}需要有限{"正" if column.kind == "positive" else ""}数')
        return number
    text = str(value).strip()
    if column.kind == 'choice' and text not in column.choices:
        raise ValueError(f'{column.title}请选择：' + '、'.join(column.choices))
    return text


def json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def compare_mode(options):
    return options.get('mode') == 'compare'


def _compare_config_api():
    import importlib.util
    script = Path(__file__).resolve().parent.parent / 'Raman' / 'compare_config.py'
    if not script.is_file():
        raise ValueError(f'基准对比模块不存在：{script}')
    name = '_gui_raman_compare_config'
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, script)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


def _compare_parameters(options, reference):
    return {
        '基准文件': reference,
        '基准名称': options.get('reference_label', ''),
        '基准峰高阈值': options.get('reference_height', ''),
        '区间下限': options.get('xmin', ''),
        '区间上限': options.get('xmax', ''),
        '匹配容差': options.get('compare_tolerance', '10') or '10',
        '归一化(0/1)': '1' if options.get('compare_normalize', True) else '0',
        '峰位校正': options.get('shift', '0') or '0',
        '激光线排除半宽': options.get('zero_guard', '50') or '50',
    }


def compare_reference_path(doc, options=None):
    options = options or doc.options
    text = str(options.get('compare_reference') or '').strip()
    if not text:
        raise ValueError('请选择基准光谱 TXT 文件')
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = Path(doc.base) / path
    if not path.exists() and path.suffix.casefold() != '.txt':
        path = Path(str(path) + '.txt')
    return path.resolve()


def _compare_options_from_workbook(wb, path):
    if '参数' not in wb.sheetnames:
        raise ValueError('基准对比配置缺少“参数”工作表')
    ws = wb['参数']
    rows = list(ws.iter_rows(values_only=True))
    lookup = {normalize(name): key for name, key in COMPARE_PARAMETERS.items()}
    params = {}
    for row in rows[1:]:
        if row is None or not any(value not in (None, '') for value in row):
            continue
        name = str(row[0] or '').strip()
        if name.startswith('#'):
            continue
        key = lookup.get(normalize(name))
        if key is None:
            raise ValueError(f'对比配置包含未知参数：{name}')
        if key in params:
            raise ValueError(f'对比配置参数重复：{name}')
        params[key] = '' if len(row) < 2 or row[1] is None else str(row[1]).strip()
    reference = params.get('reference', '')
    reference_value = ''
    if reference:
        candidate = Path(reference).expanduser()
        if not candidate.is_absolute():
            candidate = path.parent / candidate
        if not candidate.exists() and candidate.suffix.casefold() != '.txt':
            candidate = Path(str(candidate) + '.txt')
        reference_value = str(candidate.resolve())
    options = {
        'mode': 'compare',
        'config': str(path),
        'compare_reference': reference_value,
        'reference_label': params.get('reference_label', ''),
        'reference_height': params.get('reference_height', ''),
        'xmin': params.get('xmin', ''),
        'xmax': params.get('xmax', ''),
        'compare_tolerance': params.get('tolerance', '10') or '10',
        'compare_normalize': flag(params.get('normalize'), True),
        'shift': params.get('shift', '0') or '0',
        'zero_guard': params.get('zero_guard', '50') or '50',
    }
    return options, params


def validate_options(module, options):
    if 'no_plot' in options and not isinstance(options['no_plot'], bool):
        raise ValueError('“只计算不出图”需要布尔开关')
    for key in PATH_OPTIONS:
        if key in options and not isinstance(options[key], str):
            raise ValueError(f'{key} 路径需要文字')
    modes = ('config', 'directory', 'compare', 'montage', 'batch_display') if module == 'Raman' else (
        'config', 'directory') if module == 'Ra' else ('default',)
    choices = {'mode': modes,
               'types': ('all', 'bmp', 'dat'), 'input_format': ('auto', 'txt', 'lsm'),
               'lsm_mode': ('require-calibration', 'metadata-pending')}
    for key, allowed in choices.items():
        if key in options and options[key] not in allowed:
            raise ValueError(f'{key} 参数不支持：{options[key]}')
    numeric = {'Ra': {'batch': ('每组数量', 'integer')},
               'Raman': {'xmin': ('区间下限', 'number'), 'xmax': ('区间上限', 'number'),
                         'height': ('峰高阈值', 'number'), 'shift': ('峰位校正', 'number'),
                         'reference_height': ('基准峰高阈值', 'number'),
                         'compare_tolerance': ('匹配容差', 'number'),
                         'zero_guard': ('激光线排除半宽', 'number')},
               'CLSM': {'count': ('分区数', 'integer'), 'strip': ('条带宽度', 'positive'), 'seed': ('随机种子', 'seed'),
                        'attempts': ('候选次数', 'integer'), 'dy': ('Y步距', 'positive'), 'scale': ('高度比例', 'scale'), 'offset': ('高度偏移', 'number')}, 'ER': {}}
    for key, (title, kind) in numeric[module].items():
        value = options.get(key)
        if value in (None, ''):
            continue
        parsed = parse_value(Column(key, title, 'positive' if kind in ('integer', 'positive') else 'number'), value)
        if kind in ('integer', 'seed') and (not parsed.is_integer() or (parsed < 0 if kind == 'seed' else parsed <= 0)):
            raise ValueError(title + '需要' + ('非负整数' if kind == 'seed' else '正整数'))
        if kind == 'scale' and parsed == 0:
            raise ValueError('高度比例不能为零')
    if module == 'Raman':
        lower, upper = options.get('xmin', ''), options.get('xmax', '')
        if bool(lower) != bool(upper) or (lower and float(lower) >= float(upper)):
            raise ValueError('运行设置中的区间需同时填写上下限，且下限小于上限')
        if compare_mode(options):
            if not isinstance(options.get('compare_normalize', True), bool):
                raise ValueError('对比归一化需要布尔开关')
            tolerance = options.get('compare_tolerance', '')
            if str(tolerance).strip() and float(tolerance) <= 0:
                raise ValueError('匹配容差必须大于 0')
            guard = options.get('zero_guard', '')
            if str(guard).strip() and float(guard) < 0:
                raise ValueError('激光线排除半宽不能小于 0')
            height = options.get('reference_height', '')
            if str(height).strip() and float(height) < 0:
                raise ValueError('基准峰高阈值不能小于 0')


_STYLE_MODULES = {}


def sci_pic_style_module(root):
    """Load P2's standalone style integration without touching plotting internals."""
    path = Path(root).resolve() / 'sci-pic' / 'style_integration.py'
    key = str(path)
    if key not in _STYLE_MODULES:
        spec = importlib.util.spec_from_file_location('_workbench_style_integration', path)
        if spec is None or spec.loader is None:
            raise ValueError('无法加载样式解析模块。')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _STYLE_MODULES[key] = module
    return _STYLE_MODULES[key]


def sci_pic_style_rows():
    module = sci_pic_style_module(Path(__file__).resolve().parents[1])
    return list(module.STYLE_FIELDS)


def sci_pic_style_values(path):
    module = sci_pic_style_module(Path(__file__).resolve().parents[1])
    settings, locations = module.load_style_config(path)
    return settings, locations


def resolve_sci_pic_style(root, workbook=None, overrides=None, *, strict=False,
                          locations=None, output=None, excel_settings=None):
    module = sci_pic_style_module(root)
    if excel_settings is None and workbook is not None:
        excel_settings, discovered = module.load_style_config(workbook)
        if locations is None:
            locations = discovered
    style, sources, units = module.resolve_project_style(
        'sci-pic', excel_settings=excel_settings, config_path=None,
        explicit_overrides=overrides or {}, strict=strict, location=locations or {})
    payload = module.resolved_config_payload(style, sources, units=units, project='sci-pic')
    if output is not None:
        module.write_resolved_config(output, style, sources, units=units, project='sci-pic')
    return payload


def write_sci_pic_style_rows(path, values, *, module_root=None):
    """Write canonical style rows into a work copy's 全局设置 sheet only."""
    from openpyxl import load_workbook
    path = Path(path)
    fields = sci_pic_style_rows() if module_root is None else sci_pic_style_module(module_root).STYLE_FIELDS
    wb = load_workbook(path)
    try:
        ws = wb['全局设置'] if '全局设置' in wb.sheetnames else wb.create_sheet('全局设置')
        if ws.max_row == 1 and all(cell.value in (None, '') for cell in ws[1]):
            for column, title in enumerate(('参数', '填写值', '说明'), 1):
                ws.cell(row=1, column=column, value=title)
        elif ws.cell(row=1, column=1).value in (None, ''):
            ws.insert_rows(1)
            for column, title in enumerate(('参数', '填写值', '说明'), 1):
                ws.cell(row=1, column=column, value=title)
        existing = {}
        for row in range(2, ws.max_row + 1):
            key = ws.cell(row=row, column=1).value
            if key is not None and str(key).strip():
                existing[str(key).strip()] = row
        supplied = {str(key).strip(): value for key, value in (values or {}).items()}
        for field in fields:
            row = existing.get(field)
            if row is None:
                row = ws.max_row + 1
                existing[field] = row
            ws.cell(row=row, column=1, value=field)
            ws.cell(row=row, column=2, value='' if supplied.get(field) is None else supplied[field])
            if ws.cell(row=row, column=3).value in (None, ''):
                ws.cell(row=row, column=3, value='留空继承；SCI-Pic 字号/线宽单位为 pt，dpi 为分辨率')
        wb.save(path)
    finally:
        wb.close()
    return path


def atomic_write(path, data, expected=None, *, backup=True):
    """比较读取时摘要，先备份再替换；失败不修改已有文件。expected=None 表示只允许新建。"""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    original = path.read_bytes() if path.exists() else None
    current = hashlib.sha256(original).hexdigest() if original is not None else None
    if current != expected:
        raise ValueError('文件已被外部修改或目标已存在。请重新加载、比较后再保存，或使用另存为。')
    if original is not None:
        with path.open('r+b'):
            pass
    temporary = None
    backup_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.' + path.stem + '-', suffix='.tmp', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if digest(path) != expected:
            raise ValueError('保存期间文件发生变化，已停止替换。编辑内容仍保留在窗口中。')
        if original is not None and backup:
            folder = path.parent / '.backups'
            folder.mkdir(exist_ok=True)
            backup_path = folder / (path.stem + '_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + path.suffix)
            with backup_path.open('xb') as stream:
                stream.write(original)
        if original is None:
            # Windows rename 在目标存在时失败，首次创建也不会暴露半写文件。
            if os.name == 'nt':
                os.rename(temporary, path)
                temporary = None
            else:
                os.link(temporary, path)
        else:
            os.replace(temporary, path)
            temporary = None
        return digest(path), backup_path
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass
class Document:
    module: str
    base: Path
    rows: list[dict] = field(default_factory=list)
    options: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    path: Path | None = None
    fingerprint: str | None = None

    def payload(self, destination):
        destination = Path(destination).resolve()
        options = deepcopy(self.options)
        for key in PATH_OPTIONS:
            if options.get(key):
                options[key] = relative(options[key], destination.parent)
        return {'schema_version': 1, 'kind': 'data-analysis-project', 'module': self.module,
                'base': relative(self.base, destination.parent), 'options': options,
                'samples': deepcopy(self.rows), 'metadata': deepcopy(self.metadata)}

    def save(self, destination=None):
        path = Path(destination or self.path).resolve()
        if path.suffix.casefold() != '.json':
            raise ValueError('项目文件请使用 .json 扩展名')
        validate(self)
        validate_options(self.module, self.options)
        expected = self.fingerprint if self.path == path else None
        raw = json.dumps(self.payload(path), ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8') + b'\n'
        fingerprint, backup = atomic_write(path, raw, expected)
        self.path, self.fingerprint = path, fingerprint
        return backup


def load_json(path, module):
    path = Path(path).resolve()
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('项目配置超过 16 MB，请检查所选文件')
    raw = path.read_bytes()
    value = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(value, dict) or value.get('schema_version') != 1 or value.get('kind') != 'data-analysis-project':
        raise ValueError('不支持的项目格式或版本，不会修改原文件')
    if value.get('module') != module:
        raise ValueError(f'该配置属于 {value.get("module")}，请在对应项目打开')
    rows, options, metadata = value.get('samples', []), value.get('options', {}), value.get('metadata', {})
    if not isinstance(rows, list) or len(rows) > 20000 or any(not isinstance(r, dict) for r in rows):
        raise ValueError('样本列表格式无效或超过 20000 行')
    if not isinstance(options, dict) or any(not isinstance(v, (str, bool)) for v in options.values()):
        raise ValueError('项目参数应为文字或开关')
    validate_options(module, options)
    if not isinstance(metadata, dict) or not isinstance(value.get('base', '.'), str):
        raise ValueError('项目来源信息格式无效')
    options = deepcopy(options)
    for key in PATH_OPTIONS:
        if options.get(key):
            options[key] = str((path.parent / options[key]).resolve())
    return Document(module, (path.parent / value.get('base', '.')).resolve(), deepcopy(rows), options,
                    metadata, path, hashlib.sha256(raw).hexdigest())


def read_exchange(path, module, base=None):
    """导入全部业务行，停用与无效行均保留以便修正；绝不回写来源文件。"""
    path = Path(path).resolve()
    if path.suffix.casefold() == '.json':
        return load_json(path, module)
    if module not in ALIASES:
        raise ValueError('本模块请打开 JSON 项目文件')
    lookup = {normalize(alias): key for key, aliases in ALIASES[module].items() for alias in aliases}
    compare_options = None
    if path.suffix.casefold() == '.xlsx':
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=False)
        try:
            ws = wb.worksheets[0]
            if (ws.max_row or 0) > 20001 or (ws.max_column or 0) > 256:
                raise ValueError('配置表超过 20000 行或 256 列，请检查所选文件')
            raw = [[json_value(cell.value) for cell in row] for row in ws.iter_rows()]
            metadata = {'imported_from': path.name, 'source_digest': digest(path), 'sheets': wb.sheetnames,
                        'source_sheet': ws.title}
            if module == 'Raman' and '参数' in wb.sheetnames:
                compare_options, compare_params = _compare_options_from_workbook(wb, path)
                metadata['compare_parameters'] = compare_params
        finally:
            wb.close()
    elif path.suffix.casefold() == '.csv':
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('CSV 超过 16 MB')
        with path.open(encoding='utf-8-sig', newline='') as stream:
            raw = list(csv.reader(stream, strict=True))
        metadata = {'imported_from': path.name, 'source_digest': digest(path)}
    else:
        raise ValueError('请选择 JSON、XLSX 或 UTF-8 CSV 文件')
    if not raw:
        raise ValueError('配置没有表头')
    header_row = next((i for i, row in enumerate(raw) if any(v not in ('', None) for v in row)), None) if module == 'Raman' else 0
    if header_row is None:
        raise ValueError('配置没有表头')
    header = raw[header_row]
    columns = {}
    for index, name in enumerate(header):
        key = lookup.get(normalize(name))
        if key:
            if key in columns:
                raise ValueError(f'存在重复配置列：{name}')
            columns[key] = index
    if 'file' not in columns:
        raise ValueError('未找到“文件”列，请检查表头')
    rows, ignored = [], []
    schema = {c.key: c for c in SCHEMAS[module]}
    for index, values in enumerate(raw[header_row + 1:], header_row + 2):
        first = values[0] if values else None
        file_col = columns['file']
        name = values[file_col] if file_col < len(values) else None
        if not name or (module == 'Raman' and str(first or '').startswith('#')):
            if any(v not in ('', None) for v in values):
                ignored.append({'source_row': index, 'values': values})
            continue
        row = {c.key: None for c in SCHEMAS[module]}
        row['enabled'] = True
        for key, col in columns.items():
            value = values[col] if col < len(values) else None
            if module == 'Raman' and key in ('normalize', 'peaks') and value not in (None, '') and not str(value).startswith('='):
                word = str(value).strip().casefold()
                value = word in ('1', '1.0', 'y', 'yes', 'true', '是') if key == 'normalize' else word not in ('0', '0.0', 'n', 'no', 'false', '否')
            try:
                row[key] = parse_value(schema[key], value)
            except ValueError:
                row[key] = value
        row['_source_row'] = index
        row['_extra_columns'] = [{'column': i+1, 'title': str(header[i]), 'value': value} for i, value in enumerate(values)
                                 if i not in columns.values() and i < len(header)]
        rows.append(row)
        if len(rows) > 20000:
            raise ValueError('配置超过 20000 行')
    metadata['unmapped_rows'] = ignored
    metadata['source_headers'] = header
    return Document(module, Path(base or path.parent).resolve(), rows,
                    compare_options or {}, metadata)


def resolve_source(doc, row, options=None):
    text = str(row.get('file') or '').strip()
    if not text:
        raise ValueError('源文件不能为空')
    source = Path(text)
    if doc.module == 'Ra':
        if not source.is_absolute():
            direct = doc.base / source
            source = direct if direct.is_file() or len(source.parts) > 1 else doc.base / 'data' / source
    else:
        source = source if source.is_absolute() else Path((options or doc.options).get('input') or doc.base / 'data') / source
        if not source.exists() and source.suffix.casefold() != '.txt':
            source = Path(str(source) + '.txt')
    return source.resolve()


@dataclass(frozen=True)
class Problem:
    row: int | None
    key: str | None
    message: str
    blocks_save: bool = True

    @property
    def description(self):
        return (f'第 {self.row} 行：' if self.row is not None else '') + self.message


def inspect_document(doc, *, check_files=False, options=None):
    """一次收集各行问题；保存与运行共享规则，缺失文件仅阻止运行。"""
    options = options or doc.options
    mode = options.get('mode', 'config')
    schema = SCHEMAS.get(doc.module)
    if schema is None:
        return [], [Problem(None, None, '未知的分析模块')]
    active, files, outputs, normalized, lower, upper = [], set(), set(), set(), None, None
    problems = []
    if len(doc.rows) > 20000:
        return [], [Problem(None, None, '最多支持 20000 行配置')]
    for index, row in enumerate(doc.rows, 1):
        field = 'file'
        try:
            values = {}
            for column in schema:
                field = column.key
                values[field] = parse_value(column, row.get(field))
                if isinstance(values[field], str) and values[field].startswith('='):
                    raise ValueError('配置需要实际值，不支持公式；来源表保持不变')
            field = 'file'
            if not str(values.get('file') or '').strip() and schema:
                raise ValueError('源文件不能为空；不需要的行请移除')
            if not flag(values.get('enabled'), True):
                continue
            source = resolve_source(doc, values, options)
            key = os.path.normcase(str(source))
            if key in files:
                raise ValueError('启用的源文件重复')
            files.add(key)
            if doc.module == 'Ra':
                kind = 'dat' if source.suffix.casefold() == '.dat' else 'bmp' if source.suffix.casefold() in {'.bmp', '.png', '.jpg', '.jpeg'} else None
                if kind is None:
                    raise ValueError('仅支持 DAT、BMP、PNG、JPG 等输入')
                field = 'stem'
                stem = str(values.get('stem') or source.stem)
                reserved = {'CON', 'PRN', 'AUX', 'NUL'} | {f'{p}{i}' for p in ('COM', 'LPT') for i in range(1, 10)}
                if not stem or stem in ('.', '..') or re.search(r'[<>:"/\\|?*\x00-\x1f]', stem) or stem.endswith(('.', ' ')) or stem.split('.')[0].upper() in reserved:
                    raise ValueError('输出名称不是合法的 Windows 文件名')
                output = (kind, stem.casefold())
            else:
                if source.suffix.casefold() != '.txt':
                    raise ValueError('Raman 输入需要 TXT 光谱')
                output = ('txt', re.sub(r'[<>:"/\\|?*]', '_', source.stem).casefold())
                if mode != 'compare':
                    normalized.add(flag(values.get('normalize'), False))
                    if lower is None and values.get('xmin') is not None:
                        lower = values['xmin']
                    if upper is None and values.get('xmax') is not None:
                        upper = values['xmax']
            if output in outputs:
                field = 'stem' if doc.module == 'Ra' else 'file'
                raise ValueError('启用样本的输出文件名重复')
            outputs.add(output)
            if check_files and not source.is_file():
                problems.append(Problem(index, 'file', f'源文件不存在：{source}', False))
            active.append((values, source))
        except (ValueError, TypeError, OSError) as exc:
            problems.append(Problem(index, field, str(exc)))
    if normalized and len(normalized) > 1:
        problems.append(Problem(None, 'normalize', 'Raman 启用行的归一化方式必须一致'))
    if mode != 'compare' and ((lower is None) != (upper is None) or (lower is not None and lower >= upper)):
        problems.append(Problem(None, 'xmin', 'Raman 共享区间需同时有上下限，且下限小于上限'))
    if doc.module == 'Raman' and mode == 'compare':
        try:
            reference = compare_reference_path(doc, options)
            if reference.suffix.casefold() != '.txt':
                problems.append(Problem(None, 'compare_reference', '基准光谱需要 TXT 文件', False))
            elif check_files and not reference.is_file():
                problems.append(Problem(None, 'compare_reference',
                                        f'基准文件不存在：{reference}', False))
        except ValueError as exc:
            problems.append(Problem(None, 'compare_reference', str(exc), False))
    if schema and not active and not problems:
        problems.append(Problem(None, 'enabled', '请至少启用一个样本', False))
    return active, problems


def readiness(doc):
    _, problems = inspect_document(doc, check_files=True, options=doc.options)
    try:
        validate_options(doc.module, doc.options)
    except (ValueError, TypeError) as exc:
        problems.insert(0, Problem(None, None, '运行设置：' + str(exc)))
    return problems


def validate(doc, *, for_run=False, options=None):
    active, problems = inspect_document(doc, check_files=for_run, options=options)
    for problem in problems:
        if for_run or problem.blocks_save:
            raise ValueError(problem.description)
    return active


def write_snapshot_xlsx(doc, options, path):
    import openpyxl
    active = validate(doc, for_run=True, options=options)
    if doc.module == 'Raman' and compare_mode(options):
        validate_options(doc.module, options)
        api = _compare_config_api()
        reference = compare_reference_path(doc, options)
        samples = [api.CompareSample(
            str(source), str(values.get('label') or ''), True,
            values.get('height'), flag(values.get('peaks'), True))
            for values, source in active]
        api.write_compare_workbook(path, samples,
                                   _compare_parameters(options, str(reference)),
                                   overwrite=True)
        sources = []
        for candidate in [reference, *[source for _values, source in active]]:
            if candidate not in sources:
                sources.append(candidate)
        return sources
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '配置'
    keys = RA_KEYS if doc.module == 'Ra' else RAMAN_KEYS
    ws.append(RA_HEADERS if doc.module == 'Ra' else RAMAN_HEADERS)
    for values, source in active:
        values = dict(values, file=str(source))
        ws.append([int(v) if isinstance(v, bool) else v for v in (values.get(k) for k in keys)])
    wb.save(path)
    wb.close()
    return [source for _, source in active]


def export_csv(doc, destination):
    validate(doc)
    keys = [c.key for c in SCHEMAS[doc.module]]
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\r\n')
    writer.writerow(keys)
    for row in doc.rows:
        values = dict(row, file=str(resolve_source(doc, row)))
        writer.writerow([int(v) if isinstance(v, bool) else v for v in (values.get(k) for k in keys)])
    path = Path(destination)
    if path.suffix.casefold() != '.csv':
        raise ValueError('样本表请使用 .csv 扩展名')
    # 交换文件只新建；不会覆盖原 Excel 或用户已有文件。
    atomic_write(path, b'\xef\xbb\xbf' + stream.getvalue().encode('utf-8'))


def export_xlsx(doc, destination):
    """导出全部样本（含停用行），仅新建，不覆盖来源表；不是运行快照。"""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.worksheet.datavalidation import DataValidation
    validate(doc)
    path = Path(destination)
    if path.suffix.casefold() != '.xlsx':
        raise ValueError('Excel 配置请使用 .xlsx 扩展名')
    if doc.module == 'Raman' and compare_mode(doc.options):
        validate_options(doc.module, doc.options)
        api = _compare_config_api()
        samples = []
        for row in doc.rows:
            source = resolve_source(doc, row)
            samples.append(api.CompareSample(
                str(source), str(row.get('label') or ''), flag(row.get('enabled'), True),
                parse_value(Column('height', '峰高阈值', 'number'), row.get('height')),
                flag(row.get('peaks'), True)))
        try:
            reference = str(compare_reference_path(doc, doc.options))
        except ValueError:
            reference = str(doc.options.get('compare_reference') or '')
        api.write_compare_workbook(path, samples,
                                   _compare_parameters(doc.options, reference))
        return
    keys = RA_KEYS if doc.module == 'Ra' else ['enabled', *RAMAN_KEYS]
    headers = RA_HEADERS if doc.module == 'Ra' else ['启用(0/1)', *RAMAN_HEADERS]
    wb = openpyxl.Workbook()
    try:
        ws = wb.active
        ws.title = '配置'
        ws.append(headers)
        for row in doc.rows:
            values = dict(row, file=str(resolve_source(doc, row)))
            ws.append([int(v) if isinstance(v, bool) else v for v in (values.get(k) for k in keys)])
        ws.freeze_panes = 'B2'
        ws.auto_filter.ref = ws.dimensions
        ws.sheet_view.showGridLines = False
        for index, key in enumerate(keys, 1):
            cell = ws.cell(1, index)
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='315B50')
            cell.alignment = Alignment(wrap_text=True, vertical='center')
            ws.column_dimensions[cell.column_letter].width = 64 if key == 'file' else 22
            column = next(c for c in SCHEMAS[doc.module] if c.key == key)
            rule = None
            if column.kind in ('bool', 'enabled', 'choice'):
                choices = column.choices if column.kind == 'choice' else ('0', '1')
                rule = DataValidation(type='list', formula1='"' + ','.join(choices) + '"', allow_blank=True)
            elif column.kind in ('font_size', 'stroke'):
                lower, upper = (8, 96) if column.kind == 'font_size' else (0, 8)
                rule = DataValidation(type='whole', operator='between', formula1=lower, formula2=upper, allow_blank=True)
            if rule:
                rule.showErrorMessage = True
                rule.error = '请填写有效值，或清空以使用默认值'
                ws.add_data_validation(rule)
                rule.add(f'{cell.column_letter}2:{cell.column_letter}1048576')
        ws.row_dimensions[1].height = 34
        help_sheet = wb.create_sheet('说明')
        help_sheet.append(['用途', '样本参数交换表，包含停用行；完整运行设置和备注请保存在JSON项目中。源文件使用绝对路径。'])
        if doc.module == 'Ra':
            help_sheet.append(['数值标注(0/1)', '1启用、0关闭；空白保持旧默认：BMP/图片启用，DAT关闭。'])
            help_sheet.append(['标注数值', 'BMP使用截图Ra及原单位；DAT使用本次处理后面的Sa，四联图和独立图共享同一标注。'])
            help_sheet.append(['样式', '字号8–96输出像素，默认44；颜色#RRGGBB，默认#FFFFFF；位置默认左上。描边0–8像素，默认3、黑色。'])
        help_sheet.column_dimensions['A'].width = 24
        help_sheet.column_dimensions['B'].width = 110
        for row in help_sheet:
            row[1].alignment = Alignment(wrap_text=True, vertical='center')
            help_sheet.row_dimensions[row[0].row].height = 40
        stream = io.BytesIO()
        wb.save(stream)
        atomic_write(path, stream.getvalue())
    finally:
        wb.close()


def append_sources(doc, paths):
    seen = {os.path.normcase(str(resolve_source(doc, row))) for row in doc.rows if row.get('file')}
    added = []
    for path in paths:
        path = Path(path).resolve()
        key = os.path.normcase(str(path))
        if key in seen:
            continue
        row = {c.key: None for c in SCHEMAS[doc.module]}
        row.update(file=relative(path, doc.base) if doc.module == 'Ra' else str(path), enabled=True)
        if doc.module == 'Ra':
            row.update(noise=path.suffix.casefold() == '.dat', high='关闭', cutoff=80.0,
                       combined=path.suffix.casefold() == '.dat', single=True)
        else:
            row.update(normalize=True, peaks=True)
        added.append(row)
        seen.add(key)
    return added

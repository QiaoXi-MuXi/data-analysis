# -*- coding: utf-8 -*-
"""科研绘图模板目录和桌面运行适配，复用现有绘图引擎。"""
from __future__ import annotations

from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import uuid

from model import Command, as_path
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


def working_copy(root, entry):
    """创建唯一工作副本，保留模板的全部单元格、格式和公式。"""
    folder = Path(root) / 'sci-pic' / 'out' / 'workbooks'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (Path(entry['workbook']).stem + '_' + datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:6] + '.xlsx')
    with target.open('xb') as stream:
        stream.write(Path(entry['workbook']).read_bytes())
    return target


def prepare_plot(root, entry, source, output):
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
    command = Command(root / 'sci-pic' / 'plot_sci_pic.py',
        ('--excel', str(snapshot), '--out', str(folder / 'results')),
        root / 'sci-pic', (folder / 'results',), '每次生成独立结果，原始工作簿只读。')
    record = {'version': 1, 'id': identity, 'module': 'sci-pic',
        'started': datetime.now().isoformat(timespec='seconds'), 'status': 'prepared',
        'message': '工作簿快照已保存', 'input_count': 1,
        'inputs': [{'path': str(source), 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}],
        'template_id': entry['id'], 'template_title': entry['title'], 'settings': config,
        'point_count': len(points), 'snapshot': str(snapshot),
        'command': {'program': str(root / 'python' / 'python.exe'), 'script': str(command.script), 'arguments': list(command.arguments)},
        'result_roots': [str(p) for p in command.results], 'files': [], 'output_parent': str(output)}
    prepared = PreparedRun(command, record, folder / 'run.json', root / 'out' / 'gui' / 'history' / 'sci-pic' / (identity + '.json'))
    save_record(prepared)
    return prepared

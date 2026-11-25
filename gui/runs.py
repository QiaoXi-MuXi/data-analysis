# -*- coding: utf-8 -*-
"""每次桌面运行冻结配置并使用独立目录，结果不混入历史文件。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import uuid

from documents import Document, atomic_write, digest, validate, write_snapshot_xlsx
from model import as_path, build_command, list_inputs, Command


@dataclass
class PreparedRun:
    command: Command
    record: dict
    path: Path
    index: Path


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def save_record(prepared):
    raw = json.dumps(prepared.record, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8') + b'\n'
    atomic_write(prepared.path, raw, digest(prepared.path), backup=False)
    summary = {key: prepared.record.get(key) for key in ('id', 'module', 'started', 'finished', 'status', 'message', 'input_count')}
    summary['record_path'] = str(prepared.path)
    atomic_write(prepared.index, json.dumps(summary, ensure_ascii=False, indent=2).encode('utf-8'), digest(prepared.index), backup=False)


def prepare_run(root, project, options, document=None):
    """调用方传入独立副本；此函数允许在工作线程执行。"""
    root = Path(root).resolve()
    options = deepcopy(options)
    frozen = deepcopy(document) if document is not None else None
    configured = (project.key == 'Raman' and options['mode'] in ('config', 'compare')) or (
        project.key == 'Ra' and options['mode'] == 'config')
    temporary = None
    state = root / 'out' / 'gui'
    if configured:
        if frozen is None:
            raise ValueError('尚未加载项目配置，请先导入或添加样本')
        frozen.options = deepcopy(options)
        validate(frozen, for_run=True, options=options)
    try:
        if configured:
            staging = state / 'preflight'
            staging.mkdir(parents=True, exist_ok=True)
            temporary = staging / (uuid.uuid4().hex + '.xlsx')
            sources = write_snapshot_xlsx(frozen, options, temporary)
            options['config'] = str(temporary)
        else:
            sources, truncated = list_inputs(root, project, options, limit=100000)
            if truncated:
                raise ValueError('输入超过 100000 份，请缩小本批次范围后运行')
            if not sources:
                raise ValueError('当前目录没有可处理的输入')
        # 使用原有适配器校验用户选定的父目录及全局参数，再创建独立运行位置。
        build_command(root, project, options)
        output = as_path(options['output'], root)
        parent = output / 'out' / 'runs' if project.key in ('Ra', 'ER') and output == root / project.key else output / 'runs'
        identity = datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:8]
        folder = parent / identity
        folder.mkdir(parents=True, exist_ok=False)
        effective = deepcopy(options)
        effective['output'] = str(folder / 'results')
        if project.key == 'CLSM':
            effective['output'] = str(folder / 'out')
            effective['xlsx'] = str(folder / 'out' / 'results' / Path(options['xlsx']).name)
        if configured:
            config = folder / 'config.xlsx'
            with config.open('xb') as stream:
                stream.write(temporary.read_bytes())
            effective['config'] = str(config)
        command = build_command(root, project, effective)
        snapshot = frozen if configured else Document(project.key, root / project.key)
        snapshot.path, snapshot.fingerprint = None, None
        snapshot.options = effective
        snapshot.save(folder / 'project.json')
        source_records = []
        for source in sources:
            before = source.stat()
            fingerprint = file_hash(source)
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError(f'准备期间输入发生变化，请重试：{source.name}')
            source_records.append({'path': str(source), 'size': after.st_size, 'sha256': fingerprint})
        record = {'version': 1, 'id': identity, 'module': project.key, 'started': datetime.now().isoformat(timespec='seconds'),
                  'status': 'prepared', 'message': '配置及输入摘要已冻结', 'input_count': len(sources),
                  'inputs': source_records, 'command': {'program': str(root / 'python' / 'python.exe'), 'script': str(command.script),
                  'arguments': list(command.arguments)}, 'result_roots': [str(p) for p in command.results],
                  'files': [], 'project': str(folder / 'project.json'), 'output_parent': str(output)}
        prepared = PreparedRun(command, record, folder / 'run.json', state / 'history' / project.key / (identity + '.json'))
        save_record(prepared)
        return prepared
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def finish_run(prepared, code, message, log_path):
    files = set()
    for root in prepared.command.results:
        candidates = (root,) if root.is_file() else root.rglob('*') if root.is_dir() else ()
        for path in candidates:
            if path.is_file() and not path.name.startswith('~$'):
                files.add(str(path.resolve()))
    prepared.record.update(status='success' if code == 0 else 'partial' if code == 2 and prepared.record['module'] == 'Raman' and files else 'failed',
                           finished=datetime.now().isoformat(timespec='seconds'), exit_code=code, message=message,
                           log_path=str(log_path), files=sorted(files))
    changed = []
    for source in prepared.record.get('inputs', []):
        try:
            if file_hash(source['path']) != source['sha256']:
                changed.append(source['path'])
        except OSError:
            changed.append(source['path'])
    prepared.record['changed_inputs'] = changed
    if changed and prepared.record['status'] in ('success', 'partial'):
        prepared.record.update(status='review', message='输入在准备后发生变化，结果需重新核对；请查看 changed_inputs。')
    save_record(prepared)
    return prepared.record


def history(root, module):
    folder = Path(root) / 'out' / 'gui' / 'history' / module
    result = []
    for path in sorted(folder.glob('*.json'), reverse=True)[:200]:
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(record, dict) and record.get('module') == module and isinstance(record.get('record_path'), str):
                result.append(record)
        except (OSError, ValueError):
            continue
    return result

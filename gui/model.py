# -*- coding: utf-8 -*-
"""界面与既有命令行之间的适配，不导入或修改科学计算模块。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math


@dataclass(frozen=True)
class Project:
    key: str
    title: str
    subtitle: str
    script: str
    extensions: tuple[str, ...]
    note: str


PROJECTS = (
    Project('Ra', '表面粗糙度', 'ZYGO  /  BMP · DAT', 'extract_ra.py',
            ('.bmp', '.png', '.jpg', '.jpeg', '.dat'),
            '按界面配置的启用行处理；原始数据只读。DAT 去噪和高通为待验证的候选处理。'),
    Project('Raman', '拉曼光谱', 'RAMAN  /  SPECTRAL ANALYSIS', 'plot_raman.py', ('.txt',),
            '堆叠模式使用界面中的顺序和逐行参数；单谱模式使用运行设置。归一化不是绝对强度标定。'),
    Project('CLSM', '槽宽与槽深', 'CLSM  /  SURFACE METROLOGY', 'extract_groove.py', ('.txt', '.lsm'),
            '槽宽浅壁边界仍需人工核验。LSM 默认要求外部高度标定；待核验试算不可作为正式定量结论。'),
    Project('ER', '发票验收单', 'REIMBURSEMENT  /  LOCAL OCR', 'fill_reimbursement.py',
            ('.pdf', '.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff', '.webp'),
            '票据在本机识别，不上传。生成后请核对 OCR 品名及金额；模板与原始票据只读。'),
)


@dataclass(frozen=True)
class Command:
    script: Path
    arguments: tuple[str, ...]
    working_directory: Path
    results: tuple[Path, ...]
    impact: str


def default_options(root: Path, project: Project) -> dict:
    folder = root / project.key
    return {
        'input': str(folder / 'data'),
        'output': str(folder / 'out' / 'figures' if project.key == 'CLSM'
                      else folder / 'out' if project.key == 'Raman' else folder),
        'config': str(folder / ('ra_config.xlsx' if project.key == 'Ra' else 'stack.xlsx')),
        'template': '', 'mode': 'config' if project.key in ('Ra', 'Raman') else 'default',
        'types': 'all', 'no_plot': False, 'batch': '',
        'xmin': '', 'xmax': '', 'height': '5', 'shift': '0',
        'input_format': 'auto', 'lsm_mode': 'require-calibration', 'scale': '', 'offset': '0',
        'dy': '', 'strip': '12.6', 'count': '9', 'seed': '20261029', 'attempts': '4',
        'xlsx': str(folder / 'out' / 'results' / 'groove_paper_results.xlsx'),
    }


def as_path(value: str, root: Path) -> Path:
    if not str(value).strip():
        raise ValueError('路径不能为空。')
    path = Path(value).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def number(value, name, *, integer=False, positive=False, nonzero=False):
    try:
        parsed = int(str(value)) if integer else float(str(value))
    except (ValueError, TypeError):
        raise ValueError(f'{name}必须是{"整数" if integer else "数字"}。') from None
    if not math.isfinite(parsed) or (positive and parsed <= 0) or (nonzero and parsed == 0):
        raise ValueError(f'{name}必须是有限{"正数" if positive else "非零数" if nonzero else "数值"}。')
    return parsed


def build_command(root: Path, project: Project, options: dict) -> Command:
    root = root.resolve()
    folder = root / project.key
    args = []
    output = as_path(options['output'], root)
    source = as_path(options['input'], root)
    if output.exists() and not output.is_dir():
        raise ValueError('结果目录指向了文件，请选择目录。')
    # 阻止把结果写回输入树；配置模式Ra的外部源路径由原程序再检查。
    if source == output or output.is_relative_to(source):
        raise ValueError('结果目录不能是输入目录或其子目录，请选择独立结果目录。')
    if source.is_relative_to(output) and output != folder:
        raise ValueError('结果目录不能包含输入目录，请选择独立结果目录。')

    def require_source():
        if not source.is_dir():
            raise ValueError(f'输入目录不存在：{source}')

    def config_file():
        path = as_path(options['config'], root)
        if not path.is_file() or path.suffix.casefold() != '.xlsx':
            raise ValueError('请选择存在的 XLSX 配置表，并保存关闭 Excel。')
        return path

    def add_numeric(flag, key, name, **kwargs):
        if str(options.get(key, '')).strip():
            value = number(options[key], name, **kwargs)
            args.extend((flag, str(value)))
            return value
        return None

    if project.key == 'Ra':
        if options['mode'] == 'config':
            args += ['--config', str(config_file())]
        else:
            require_source()
            args += ['--dir', str(source)]
        args += ['--out', str(output), '--types', options['types']]
        add_numeric('--batch', 'batch', '每组数量', integer=True, positive=True)
        results = (output / 'ra_results.xlsx', output / 'out')
        impact = f'更新 {output / "ra_results.xlsx"} 及本次图像；配置表只读，既有同名结果可能被替换。'
    elif project.key == 'Raman':
        require_source()
        args += ['--src', str(source), '--out', str(output)]
        if options['mode'] == 'config':
            args += ['--stack', str(config_file())]
        xmin = add_numeric('--xmin', 'xmin', '区间下限')
        xmax = add_numeric('--xmax', 'xmax', '区间上限')
        if (xmin is None) != (xmax is None):
            raise ValueError('波数区间上下限请同时填写，或同时留空。')
        if options['mode'] != 'config' and xmin is None:
            raise ValueError('单谱模式请填写波数区间上下限。堆叠模式可留空并沿用配置。')
        if xmin is not None and xmin >= xmax:
            raise ValueError('区间下限必须小于上限。')
        add_numeric('--height', 'height', '峰高阈值')
        add_numeric('--shift', 'shift', '峰位校正')
        results = (output,)
        impact = f'图像及峰位表写入 {output}；同名文件会被替换，其他旧文件保留。'
    elif project.key == 'CLSM':
        require_source()
        xlsx = as_path(options['xlsx'], root)
        if xlsx.suffix.casefold() != '.xlsx':
            raise ValueError('结果工作簿必须使用 .xlsx 扩展名。')
        if xlsx == source or xlsx.is_relative_to(source):
            raise ValueError('结果工作簿不能写入原始输入目录。')
        args += ['--dir', str(source), '--xlsx', str(xlsx), '--input-format', options['input_format'],
                 '--lsm-height-mode', options['lsm_mode']]
        # 保留默认CLI输出布局；GUI不传--clean-out，避免切换项目时清理既有结果。
        if output != folder / 'out' / 'figures':
            args += ['--out', str(output)]
        for flag, key, name, kwargs in (
            ('--dy', 'dy', 'Y步距', {'positive': True}),
            ('--strip-width-um', 'strip', '条带宽度', {'positive': True}),
            ('--profile-count', 'count', '分区数', {'integer': True, 'positive': True}),
            ('--random-seed', 'seed', '随机种子', {'integer': True}),
            ('--max-attempts', 'attempts', '候选次数', {'integer': True, 'positive': True}),
            ('--lsm-height-scale-um', 'scale', '高度比例', {'nonzero': True}),
        ):
            value = add_numeric(flag, key, name, **kwargs)
            if key == 'seed' and value is not None and value < 0:
                raise ValueError('随机种子不能小于0。')
        offset = number(options['offset'] or '0', '高度偏移')
        if offset and not str(options['scale']).strip():
            raise ValueError('LSM高度偏移必须与外部标定比例配套填写。')
        args += ['--lsm-height-offset-um', str(offset)]
        results = (xlsx, output)
        impact = f'更新 {xlsx} 和 {output} 中的同名结果；GUI保留旧目录内容，不执行整目录清理。'
    else:
        require_source()
        args += ['--data', str(source), '--output-dir', str(output), '--no-pause']
        if options.get('template', '').strip():
            template = as_path(options['template'], root)
            if not template.is_file():
                raise ValueError('所选模板不存在。')
            args += ['--template', str(template)]
        results = (output,)
        impact = f'在 {output} 新建带时间戳的验收单；追溯记录写入其out目录，模板与旧结果保留。'
    if project.key in ('Ra', 'CLSM') and options['no_plot']:
        args.append('--no-plot')
    script = folder / project.script
    if not script.is_file():
        raise ValueError(f'分析脚本不存在：{script}')
    return Command(script, tuple(args), folder, tuple(results), impact)


def list_inputs(root: Path, project: Project, options: dict, limit=2000):
    """配置Ra按实际启用行列出；其他页面只读发现，最终筛选仍由分析程序负责。"""
    if project.key == 'Ra' and options['mode'] == 'config':
        import importlib.util
        spec = importlib.util.spec_from_file_location('_gui_ra_config', root / 'Ra' / 'ra_config.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        rows = module.load_config(as_path(options['config'], root))
        return [Path(row['path']) for row in rows[:limit]], len(rows) > limit
    source = as_path(options['input'], root)
    if not source.is_dir():
        return [], False
    def valid(p):
        return p.is_file() and p.suffix.casefold() in project.extensions and not p.name.startswith('~$')
    if project.key == 'CLSM':
        # 复用正式发现规则，避免同名TXT/LSM在GUI中被双计。
        import importlib.util
        import sys
        directory = str(root / 'CLSM')
        if directory not in sys.path:
            sys.path.insert(0, directory)
        from clsm_inputs import discover_inputs
        paths, _ = discover_inputs(source, options['input_format'])
        return paths[:limit], len(paths) > limit
    paths = []
    iterator = source.rglob('*') if project.key == 'ER' else source.iterdir()
    for path in iterator:
        if valid(path):
            paths.append(path)
            if len(paths) > limit:
                return sorted(paths[:limit]), True
    return sorted(paths), False

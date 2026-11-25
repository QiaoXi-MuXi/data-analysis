# -*- coding: utf-8 -*-
"""只读检查共享运行时、模块入口、源码语法及BAT格式，不执行数据处理。"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
MODULES = {
    'Ra': ('extract_ra.py', 'ra_config.py', 'ra_workflow.py', 'plot_labels.py', 'zygo_dat.py', 'zygo_render.py', 'zygo_style_assets.npz'),
    'Raman': ('plot_raman.py', '_fill_stack.py', '_make_stack_xlsx.py',
              'compare_config.py', 'raman_compare.py', 'compare_report.py',
              'compare_runner.py', '_make_compare_xlsx.py',
              'raman_montage.py', '_make_montage_xlsx.py',
              'batch_display_config.py', 'batch_display_data.py',
              'batch_display_plots.py', 'batch_display_runner.py',
              '_make_batch_display_xlsx.py'),
    'CLSM': ('extract_groove.py', 'clsm_inputs.py', 'profile_measurement.py', 'profile_sampling.py', 'profile_plots.py', 'surface_plots.py', 'inspect_zen_lsm.py'),
    'ER': ('fill_reimbursement.py',),
    'sci-pic': ('plot_sci_pic.py', 'build_previews.py', 'templates.json', 'templates/sci_pic.xlsx',
                'templates/removal_rate.xlsx', 'previews/roughness.png', 'previews/removal_rate.png'),
    'Nanoindentation Analysis': ('nano_analysis.py', 'nano_core.py', 'nano_config.py',
                                 'nano_plots.py', '_fill_config.py', 'config.json',
                                 'config.xlsx', 'run.bat', 'fill_config.bat'),
}
DEPENDENCIES = ('numpy', 'matplotlib', 'PIL', 'openpyxl', 'xlrd', 'win32api',
                'rapidocr_onnxruntime', 'onnxruntime', 'pypdfium2')


def main() -> int:
    errors = []

    def report(ok: bool, message: str) -> None:
        print(f'[{"OK" if ok else "FAIL"}] {message}')
        if not ok:
            errors.append(message)

    expected = ROOT / 'python' / 'python.exe'
    actual = Path(sys.executable).resolve()
    print(f'项目：{ROOT}\n解释器：{actual}\nPython：{sys.version.split()[0]}')
    report(actual == expected.resolve(), '使用项目共享便携解释器')
    report(sys.version_info >= (3, 12), 'Python 版本至少3.12')
    runtime = (ROOT / 'python').resolve()
    for name in DEPENDENCIES:
        try:
            module = importlib.import_module(name)
            location = Path(module.__file__).resolve()
            report(location.is_relative_to(runtime), f'依赖 {name}：{location}')
        except Exception as exc:
            report(False, f'依赖 {name} 导入失败：{exc}')

    for name, required in MODULES.items():
        folder = ROOT / name
        missing = [p for p in ('README.md', 'run.bat', *required) if not (folder / p).is_file()]
        report(not missing, f'{name} 入口与必要文件' + (f'，缺少：{", ".join(missing)}' if missing else ''))
        duplicates = [p.name for p in folder.glob('run*.bat') if p.name.casefold() != 'run.bat']
        report(not duplicates, f'{name} 仅保留一个分析启动入口 run.bat' + (f'，重复入口：{", ".join(duplicates)}' if duplicates else ''))
        sources = sorted(folder.glob('*.py'))
        for path in sources:
            try:
                ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
            except (SyntaxError, UnicodeError, OSError) as exc:
                report(False, f'{name}/{path.name} 语法或编码：{exc}')
        report(bool(sources), f'{name} 已检查 {len(sources)} 份 Python 源码')
        for path in sorted(folder.glob('*.bat')):
            raw = path.read_bytes()
            try:
                text = raw.decode('gbk')
                valid = not raw.startswith(b'\xef\xbb\xbf') and '\n' not in text.replace('\r\n', '') and '\r' not in text.replace('\r\n', '')
                report(valid, f'{name}/{path.name}：GBK兼容、CRLF换行、无BOM')
            except UnicodeError as exc:
                report(False, f'{name}/{path.name} 编码：{exc}')

    # GUI依赖为可选项；基础检查只校验新增源码，GUI导入由run_gui.bat --check检查。
    gui_sources = sorted((ROOT / 'gui').glob('*.py'))
    for path in gui_sources:
        try:
            ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
            report(True, f'GUI/{path.name} 语法')
        except (SyntaxError, UnicodeError, OSError) as exc:
            report(False, f'GUI/{path.name} 语法或编码：{exc}')

    for path in (ROOT / 'Ra' / 'ra_config.xlsx', ROOT / 'Raman' / 'stack.xlsx'):
        if not path.is_file():
            print(f'[INFO] 未发现配置 {path.relative_to(ROOT)}，请按模块说明初始化或选择其它运行模式')
    print(f'\n检查结束：{len(errors)} 项错误。未处理原始数据，未改写配置或清理输出。')
    print('本检查不验证测量准确性、OCR品名或完整业务流程。')
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())

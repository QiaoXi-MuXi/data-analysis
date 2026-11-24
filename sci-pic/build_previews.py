# -*- coding: utf-8 -*-
"""使用真实模板和同一绘图引擎更新样式示例，原工作簿只读。"""
from pathlib import Path
import hashlib
import json
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plot_sci_pic import load_input, draw


def main():
    root = Path(__file__).resolve().parent
    entries = json.loads((root / 'templates.json').read_text(encoding='utf-8'))['templates']
    records = []
    for entry in entries:
        source = (root / entry['workbook']).resolve()
        target = (root / entry['preview']).resolve()
        if not source.is_relative_to((root / 'templates').resolve()) or not target.is_relative_to((root / 'previews').resolve()):
            raise ValueError('模板与预览路径必须位于相应子文件夹。')
        raw, points, config = load_input(source)
        with tempfile.TemporaryDirectory(prefix='sci-pic-preview-') as temporary:
            _, warnings = draw(points, config, Path(temporary))
            if warnings:
                raise ValueError('样式预览存在绘图警告：' + '；'.join(warnings))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((Path(temporary) / (config['图名'] + '.png')).read_bytes())
        records.append({'id': entry['id'], 'workbook': entry['workbook'], 'preview': entry['preview'],
                        'workbook_sha256': hashlib.sha256(raw).hexdigest(),
                        'script_sha256': hashlib.sha256((root / 'plot_sci_pic.py').read_bytes()).hexdigest()})
        print(f'已生成样式预览：{target}')
    (root / 'previews' / 'manifest.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()

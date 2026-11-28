# -*- coding: utf-8 -*-
"""Raman 处理模式的控件—options—命令—run.json 映射回归。

只验证适配层把界面字段翻成最终命令行时的对应关系，不启动窗口、
不渲染图像，也不读取真实样品数据。
"""
import json
from copy import deepcopy
import os
from pathlib import Path
import sys
import tempfile
import unittest

GUI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GUI))
from documents import Document
from model import PROJECTS, default_options, build_command
from runs import prepare_run, finish_run


def write_txt(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(f'{index + 100}\t{value}' for index, value in enumerate(values)),
                    encoding='utf-8')


class RamanModeMappingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='raman-modes-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / 'Raman'
        (self.folder / 'data').mkdir(parents=True)
        (self.folder / 'out').mkdir()
        (self.folder / 'plot_raman.py').write_text('# 测试适配器', encoding='utf-8')
        (self.folder / 'stack.xlsx').write_bytes(b'stack')
        (self.folder / 'compare.xlsx').write_bytes(b'compare')
        write_txt(self.folder / 'data' / 'a.txt', [5] * 40)
        write_txt(self.folder / 'data' / 'b.txt', [6] * 40)
        self.project = next(p for p in PROJECTS if p.key == 'Raman')
        self.doc = Document('Raman', self.folder, [
            dict(file='a.txt', label='样品 A', enabled=True, height=5, peaks=True,
                 normalize=None, offset=None, xmin=None, xmax=None),
            dict(file='b.txt', label='样品 B', enabled=True, height=None, peaks=False,
                 normalize=None, offset=None, xmin=None, xmax=None),
        ], {'mode': 'config', 'input': str(self.folder / 'data'),
            'output': str(self.folder / 'out'),
            'config': str(self.folder / 'stack.xlsx'),
            'compare_reference': str(self.folder / 'data' / 'a.txt'),
            'reference_label': '基准', 'reference_height': '',
            'compare_tolerance': '10', 'compare_normalize': True,
            'zero_guard': '50', 'xmin': '', 'xmax': '', 'height': '5', 'shift': '0'})

    def options(self, mode, **changes):
        options = default_options(self.root, self.project)
        options.update(deepcopy(self.doc.options))
        options['mode'] = mode
        options['config'] = str(self.folder / ('stack.xlsx' if mode == 'config'
                                               else 'compare.xlsx'))
        options.update(changes)
        return options

    def command(self, mode, **changes):
        return build_command(self.root, self.project, self.options(mode, **changes))

    def flags(self, command, flag):
        arguments = list(command.arguments)
        return [arguments[index + 1] for index, item in enumerate(arguments[:-1])
                if item == flag]

    def test_config_mode_uses_stack_and_ignores_peak_shift(self):
        command = self.command('config', shift='3', compare_reference='',
                               compare_tolerance='99')
        self.assertIn('--stack', command.arguments)
        for absent in ('--compare', '--auto-window', '--montage', '--batch-display',
                       '--shift', '--compare'):
            self.assertNotIn(absent, command.arguments)
        self.assertEqual(self.flags(command, '--height'), ['5.0'])

    def test_directory_mode_without_window_requests_auto_window(self):
        command = self.command('directory', xmin='', xmax='', shift='2', height='')
        self.assertIn('--auto-window', command.arguments)
        for absent in ('--xmin', '--xmax', '--stack', '--compare', '--height'):
            self.assertNotIn(absent, command.arguments)
        self.assertEqual(self.flags(command, '--shift'), ['2.0'])

    def test_directory_mode_with_window_keeps_explicit_range(self):
        command = self.command('directory', xmin='120', xmax='900')
        self.assertNotIn('--auto-window', command.arguments)
        self.assertEqual(self.flags(command, '--xmin'), ['120.0'])
        self.assertEqual(self.flags(command, '--xmax'), ['900.0'])

    def test_one_sided_window_is_rejected_in_every_mode(self):
        for mode in ('config', 'directory', 'compare', 'montage', 'batch_display'):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, '同时'):
                self.command(mode, xmin='120', xmax='')

    def test_compare_mode_uses_reference_and_omits_sample_height(self):
        command = self.command('compare', xmin='', xmax='', height='9', shift='1')
        self.assertIn('--compare', command.arguments)
        for absent in ('--stack', '--auto-window', '--height'):
            self.assertNotIn(absent, command.arguments)
        self.assertEqual(self.flags(command, '--shift'), ['1.0'])
        self.assertEqual(self.flags(command, '--src'), [str(self.folder / 'data')])

    def test_montage_mode_auto_matches_without_config(self):
        command = self.command('montage', xmin='', xmax='', mode_config='', shift='4',
                               height='7', zero_guard='0')
        self.assertIn('--montage', command.arguments)
        self.assertNotIn('--auto-window', command.arguments)
        for absent in ('--stack', '--compare', '--batch-display'):
            self.assertNotIn(absent, command.arguments)
        self.assertEqual(self.flags(command, '--height'), ['7.0'])
        self.assertEqual(self.flags(command, '--shift'), ['4.0'])
        self.assertEqual(self.flags(command, '--zero-guard'), ['0.0'])
        # 自动配对时不带任何表名：--montage 后面紧跟另一个参数
        arguments = list(command.arguments)
        following = arguments[arguments.index('--montage') + 1]
        self.assertTrue(following.startswith('--'), following)

    def test_montage_mode_passes_selected_table(self):
        table = self.folder / 'montage.xlsx'
        table.write_bytes(b'montage')
        command = self.command('montage', xmin='', xmax='', mode_config=str(table))
        arguments = list(command.arguments)
        self.assertEqual(arguments[arguments.index('--montage') + 1], str(table))
        with self.assertRaisesRegex(ValueError, '配置表'):
            self.command('montage', mode_config=str(self.folder / 'missing.xlsx'))

    def test_batch_display_mode_omits_peak_controls(self):
        command = self.command('batch_display', xmin='100', xmax='900', height='9',
                               shift='3', zero_guard='50', mode_config='')
        self.assertIn('--batch-display', command.arguments)
        for absent in ('--stack', '--compare', '--montage', '--auto-window',
                       '--height', '--shift', '--zero-guard'):
            self.assertNotIn(absent, command.arguments)
        self.assertEqual(self.flags(command, '--xmin'), ['100.0'])
        self.assertEqual(self.flags(command, '--xmax'), ['900.0'])

    def test_zero_guard_is_sent_only_where_the_engine_uses_it(self):
        sent_for = ('config', 'directory', 'montage')
        skipped_for = ('compare', 'batch_display')
        for mode in sent_for:
            with self.subTest(mode=mode):
                self.assertIn('--zero-guard', self.command(mode, zero_guard='40').arguments)
        for mode in skipped_for:
            with self.subTest(mode=mode):
                self.assertNotIn('--zero-guard', self.command(mode, zero_guard='40').arguments)

    def test_run_json_keeps_the_same_arguments_as_the_command(self):
        for mode in ('directory', 'compare', 'montage', 'batch_display'):
            with self.subTest(mode=mode):
                options = self.options(mode, xmin='', xmax='', shift='0')
                prepared = prepare_run(self.root, self.project, options,
                                       self.doc if mode == 'compare' else None)
                record = json.loads(prepared.path.read_text(encoding='utf-8'))
                self.assertEqual(record['module'], 'Raman')
                self.assertEqual(record['command']['arguments'],
                                 list(prepared.command.arguments))
                project = json.loads((prepared.path.parent / 'project.json')
                                     .read_text(encoding='utf-8'))
                self.assertEqual(project['options']['mode'], mode)
                self.assertEqual(project['options']['xmin'], '')
                if mode == 'directory':
                    self.assertIn('--auto-window', record['command']['arguments'])
                elif mode == 'compare':
                    self.assertIn('--compare', record['command']['arguments'])
                elif mode == 'montage':
                    self.assertIn('--montage', record['command']['arguments'])
                else:
                    self.assertIn('--batch-display', record['command']['arguments'])
                finish_run(prepared, 0, '完成', self.root / 'log.txt')


if __name__ == '__main__':
    unittest.main()

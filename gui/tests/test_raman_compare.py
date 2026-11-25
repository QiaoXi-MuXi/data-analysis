# -*- coding: utf-8 -*-
"""GUI 基准对比模式、Excel 配置和运行快照回归。"""
from copy import deepcopy
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
GUI = Path(__file__).resolve().parents[1]
DATAROOT = GUI.parent
sys.path.insert(0, str(GUI))
import openpyxl
from PySide6.QtWidgets import QApplication
from documents import (Document, read_exchange, validate, validate_options,
                        write_snapshot_xlsx, export_xlsx)
from model import PROJECTS, default_options, build_command, list_inputs
from runs import prepare_run, finish_run
from app import Window
from editor import ConfigEditor


def write_txt(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(f'{index + 100}\t{value}' for index, value in enumerate(values)),
                    encoding='utf-8')


class RamanCompareGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='raman-compare-gui-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / 'Raman'
        (self.folder / 'data').mkdir(parents=True)
        (self.folder / 'out').mkdir()
        (self.folder / 'plot_raman.py').write_text('# 测试适配器', encoding='utf-8')
        self.project = next(p for p in PROJECTS if p.key == 'Raman')
        self.reference = self.folder / 'data' / 'base.txt'
        self.sample_a = self.folder / 'data' / 'a.txt'
        self.sample_b = self.folder / 'data' / 'b.txt'
        write_txt(self.reference, [5] * 100)
        write_txt(self.sample_a, [6] * 100)
        write_txt(self.sample_b, [7] * 100)
        self.doc = Document('Raman', self.folder, [
            dict(file='base.txt', label='基准样品', enabled=False, height=None, peaks=True,
                 normalize=None, offset=None, xmin=None, xmax=None),
            dict(file='a.txt', label='样品 A', enabled=True, height=5, peaks=True,
                 normalize=None, offset=None, xmin=None, xmax=None),
            dict(file='b.txt', label='样品 B', enabled=True, height=None, peaks=False,
                 normalize=None, offset=None, xmin=None, xmax=None),
        ], {
            'mode': 'compare', 'input': str(self.folder / 'data'),
            'output': str(self.folder / 'out'), 'config': str(self.folder / 'compare.xlsx'),
            'compare_reference': str(self.reference), 'reference_label': '基准',
            'reference_height': '', 'compare_tolerance': '7', 'compare_normalize': True,
            'zero_guard': '50', 'shift': '0', 'xmin': '', 'xmax': '',
        })

    def test_input_scan_uses_natural_file_order(self):
        scan = self.folder / 'scan'
        scan.mkdir()
        for name in ('10-1.txt', '2-1.txt', '1-10.txt', '1-2.txt', '1-1.txt'):
            write_txt(scan / name, [1, 2])
        paths, _ = list_inputs(self.root, self.project, {'input': str(scan)})
        self.assertEqual([p.name for p in paths],
                         ['1-1.txt', '1-2.txt', '1-10.txt', '2-1.txt', '10-1.txt'])

        er_folder = self.root / 'ER'
        er_data = er_folder / 'data'
        er_data.mkdir(parents=True)
        for name in ('10-1.png', '2-1.png', '1-10.png', '1-2.png'):
            (er_data / name).write_bytes(b'')
        er = next(p for p in PROJECTS if p.key == 'ER')
        paths, _ = list_inputs(self.root, er, {'input': str(er_data)})
        self.assertEqual([p.name for p in paths],
                         ['1-2.png', '1-10.png', '2-1.png', '10-1.png'])

        editor = ConfigEditor(deepcopy(self.doc))
        editor.add_paths([str(scan / (name + '.txt'))
                          for name in ('10-1', '2-1', '1-10', '1-2', '1-1')])
        self.assertEqual([Path(row['file']).name for row in editor.document.rows[-5:]],
                         ['1-1.txt', '1-2.txt', '1-10.txt', '2-1.txt', '10-1.txt'])

    def test_snapshot_is_native_compare_workbook_and_freezes_reference(self):
        snapshot = self.folder / 'out' / 'snapshot.xlsx'
        sources = write_snapshot_xlsx(self.doc, self.doc.options, snapshot)
        self.assertEqual(len(sources), 3)
        wb = openpyxl.load_workbook(snapshot, data_only=True)
        try:
            self.assertEqual(wb.sheetnames[:2], ['样品', '参数'])
            rows = list(wb['样品'].iter_rows(values_only=True))
            self.assertEqual(rows[0], ('文件', '图例名称', '启用(0/1)', '峰高阈值', '标注峰位(0/1)'))
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[1][1], '样品 A')
            self.assertEqual(rows[2][1], '样品 B')
            params = dict((row[0], row[1]) for row in
                          wb['参数'].iter_rows(min_row=2, values_only=True))
            self.assertEqual(params['匹配容差'], '7')
            self.assertTrue(Path(params['基准文件']).is_absolute())
            self.assertEqual(params['归一化(0/1)'], '1')
        finally:
            wb.close()
        imported = read_exchange(snapshot, 'Raman')
        self.assertEqual(imported.options['mode'], 'compare')
        self.assertEqual(imported.options['compare_reference'], str(self.reference))
        self.assertEqual(imported.options['compare_tolerance'], '7')
        self.assertEqual([row['label'] for row in imported.rows], ['样品 A', '样品 B'])

    def test_export_and_build_command_use_compare_not_stack(self):

        target = self.folder / 'out' / 'export.xlsx'
        export_xlsx(self.doc, target)
        imported = read_exchange(target, 'Raman')
        self.assertEqual(imported.options['mode'], 'compare')
        self.assertEqual(len(imported.rows), 3)

        options = deepcopy(self.doc.options)
        options['config'] = str(target)
        command = build_command(self.root, self.project, options)
        self.assertIn('--compare', command.arguments)
        self.assertNotIn('--stack', command.arguments)
        self.assertNotIn('--height', command.arguments)
        self.assertEqual(command.results, (self.folder / 'out',))

        options['xmin'] = '120'
        with self.assertRaisesRegex(ValueError, '同时'):
            build_command(self.root, self.project, options)

    def test_validation_requires_reference_only_for_run(self):
        options = deepcopy(self.doc.options)
        options['compare_reference'] = ''
        doc = deepcopy(self.doc)
        doc.options = options
        validate(doc)  # 允许先保存项目，运行前再拦截
        with self.assertRaisesRegex(ValueError, '基准光谱'):
            validate(doc, for_run=True)
        options['compare_tolerance'] = '0'
        with self.assertRaisesRegex(ValueError, '大于 0'):
            validate_options('Raman', options)

    def test_prepare_run_records_reference_and_changed_reference_marks_review(self):
        prepared = prepare_run(self.root, self.project, deepcopy(self.doc.options), self.doc)
        self.assertIn('--compare', prepared.command.arguments)
        self.assertEqual(prepared.record['input_count'], 3)
        paths = [item['path'] for item in prepared.record['inputs']]
        self.assertIn(str(self.reference), paths)
        self.assertTrue((prepared.path.parent / 'config.xlsx').is_file())
        self.reference.write_text('changed', encoding='utf-8')
        record = finish_run(prepared, 0, '完成', self.root / 'log.txt')
        self.assertEqual(record['status'], 'review')
        self.assertIn(str(self.reference), record['changed_inputs'])

    def test_editor_uses_compare_columns(self):
        editor = ConfigEditor(deepcopy(self.doc))
        try:
            self.assertEqual(editor.model.columns,
                             ['enabled', 'file', 'label', 'height', 'peaks', '_status'])
            self.assertEqual(editor.model.rowCount(), 3)
        finally:
            editor.deleteLater()

    def test_window_exposes_compare_mode_settings(self):
        window = Window(self.root)
        self.addCleanup(lambda: window.pool.waitForDone(10000))
        page = window.pages['Raman']
        self.assertGreaterEqual(page.fields['mode'].count(), 3)
        self.assertEqual(page.fields['mode'].findData('compare'), 2)
        for key in ('compare_reference', 'reference_height', 'compare_tolerance',
                    'compare_normalize', 'zero_guard'):
            self.assertIn(key, page.fields)
        page.fields['mode'].setCurrentIndex(page.fields['mode'].findData('compare'))
        self.assertTrue(page.fields['compare_reference'].isEnabled())
        self.assertFalse(page.fields['height'].isEnabled())


if __name__ == '__main__':
    unittest.main()

# -*- coding: utf-8 -*-
"""标注配置的交换、批量撤销和真实像素回归；不写正式输入与输出。"""
from copy import deepcopy
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'gui'))
sys.path.insert(0, str(ROOT / 'Ra'))

import numpy as np
from PIL import Image, ImageDraw
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QDialog
import openpyxl
from documents import Document, RA_HEADERS, SCHEMAS, read_exchange, export_csv, export_xlsx, write_snapshot_xlsx, parse_value
from editor import ConfigEditor
from ra_batch import RaBatchDialog, ColorField, target_rows, modified_rows, LABEL_KEYS
import plot_labels
import ra_config
import extract_ra
import zygo_render
import zygo_dat


class LabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.assets = zygo_render.load_assets()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='data-analysis-label-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'sample.bmp'
        self.source.write_bytes(b'source')
        self.style = dict(label_enabled=True, label_size=30, label_color='#12ABCD',
                          label_position='右下', label_stroke=0, label_stroke_color='#012345')
        self.doc = Document('Ra', self.root, [dict(file=str(self.source), enabled=True, **self.style),
                                             dict(file='missing.dat', enabled=False, label_enabled=False)])

    def test_old_excel_defaults_and_new_snapshot_match_engine(self):
        old = self.root / 'old.xlsx'
        ra_config.create_config(old, [[str(self.source), 1, '', '', 0, '关闭', 80, 0, 1]])
        plans = ra_config.load_config(old)
        self.assertEqual(plans[0]['label_style'], plot_labels.settings())
        snap = self.root / 'snapshot.xlsx'
        write_snapshot_xlsx(self.doc, {}, snap)
        plans = ra_config.load_config(snap)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]['label_style'], self.style)

    def test_xlsx_export_keeps_disabled_rows_and_does_not_overwrite(self):
        path = self.root / 'exchange.xlsx'
        export_xlsx(self.doc, path)
        loaded = read_exchange(path, 'Ra')
        self.assertEqual(len(loaded.rows), 2)
        self.assertFalse(loaded.rows[1]['enabled'])
        self.assertEqual({k: loaded.rows[0][k] for k in LABEL_KEYS}, self.style)
        self.assertEqual(ra_config.load_config(path)[0]['label_style'], self.style)
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            export_xlsx(self.doc, path)
        self.assertEqual(path.read_bytes(), before)
        wb = openpyxl.load_workbook(path)
        try:
            self.assertEqual([c.value for c in wb.active[1]], RA_HEADERS)
            self.assertGreater(len(wb.active.data_validations.dataValidation), 4)
        finally:
            wb.close()

    def test_csv_and_json_keep_annotation_settings(self):
        export_csv(self.doc, self.root / 'samples.csv')
        loaded = read_exchange(self.root / 'samples.csv', 'Ra')
        self.assertEqual({k: loaded.rows[0][k] for k in LABEL_KEYS}, self.style)
        self.doc.save(self.root / 'project.json')
        loaded = read_exchange(self.root / 'project.json', 'Ra')
        self.assertEqual(loaded.rows, self.doc.rows)

    def test_portable_cli_imports_label_module_outside_script_directory(self):
        completed = subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', str(ROOT/'Ra/extract_ra.py'), '--help'],
                                   cwd=self.root, capture_output=True, encoding='utf-8', errors='replace')
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('--config', completed.stdout)

    def test_excel_new_headers_accept_reorder_and_english_aliases(self):
        path = self.root / 'reordered.xlsx'
        wb = openpyxl.Workbook()
        wb.active.append(['label_color', '文件', 'label_enabled', 'label_size', 'label_stroke', 'label_position'])
        wb.active.append(['#ab12cd', str(self.source), 1, 28, 0, '右上'])
        wb.save(path)
        wb.close()
        gui = read_exchange(path, 'Ra')
        engine = ra_config.load_config(path)[0]['label_style']
        self.assertEqual(engine['label_color'], '#AB12CD')
        self.assertEqual(gui.rows[0]['label_size'], engine['label_size'])

    def test_gui_and_engine_reject_invalid_style_consistently(self):
        schema = {c.key: c for c in SCHEMAS['Ra']}
        for key, values in {'label_size': [0, 7, 97, 14.5, True, 'NaN', '=12'],
                            'label_stroke': [-1, 9, 2.5, False, 'inf'],
                            'label_color': ['red', '#FFF', '#GGGGGG', '#12345678'],
                            'label_position': ['center'], 'label_enabled': ['maybe']}.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ValueError):
                        parse_value(schema[key], value)
                    with self.assertRaises(ValueError):
                        plot_labels.settings({key: value})
        self.assertEqual(parse_value(schema['label_size'], 8), 8)
        self.assertEqual(plot_labels.settings({'label_stroke': 0})['label_stroke'], 0)

    def test_suffix_scope_is_case_insensitive_and_keeps_unselected_data(self):
        rows = [dict(file='A.BMP'), dict(file='b.dat'), dict(file='c.PNG', enabled=False), dict(file='e.bmp')]
        self.assertEqual(target_rows(rows, '.bmp'), [0, 3])
        self.assertEqual(target_rows(rows, '.dat'), [1])
        self.assertEqual(target_rows(rows, 'images'), [0, 2, 3])
        self.assertEqual(target_rows(rows, 'selected', [1, 3]), [1, 3])
        changed = modified_rows(rows, [0, 3], {'label_color': '#abcdef'})
        self.assertEqual(changed[1:3], rows[1:3])
        self.assertEqual(changed[0]['label_color'], '#ABCDEF')
        self.assertNotIn('label_color', rows[0])
        with self.assertRaises(ValueError):
            modified_rows(rows, [0], {'label_color': 'bad'})

    def test_native_dialog_apply_is_one_undo_and_cancel_keeps_rows(self):
        before = deepcopy(self.doc.rows)
        editor = ConfigEditor(self.doc)
        self.addCleanup(editor.close)
        def apply(dialog):
            dialog.scope.setCurrentIndex(dialog.scope.findData('.bmp'))
            dialog.preset('standard')
            dialog.apply_changes()
            return QDialog.DialogCode.Accepted
        with patch.object(RaBatchDialog, 'exec', new=apply):
            editor.quick_config()
        self.assertEqual(editor.model.stack.count(), 1)
        self.assertEqual(self.doc.rows[0]['label_size'], 32)
        self.assertEqual(self.doc.rows[1], before[1])
        editor.model.stack.undo()
        self.assertEqual(self.doc.rows, before)
        editor.model.stack.redo()
        self.assertEqual(self.doc.rows[0]['label_size'], 32)
        after = deepcopy(self.doc.rows)
        with patch.object(RaBatchDialog, 'exec', return_value=QDialog.DialogCode.Rejected):
            editor.quick_config()
        self.assertEqual(self.doc.rows, after)

    def test_empty_scope_invalid_input_and_off_preset(self):
        dialog = RaBatchDialog(self.doc.rows)
        self.addCleanup(dialog.close)
        dialog.preset('standard')
        dialog.preset('off')
        self.assertEqual(dialog.changes(), {'label_enabled': False})
        dialog.scope.setCurrentIndex(dialog.scope.findData('selected'))
        self.assertFalse(dialog.apply_button.isEnabled())
        dialog.scope.setCurrentIndex(dialog.scope.findData('.bmp'))
        dialog.checks['label_color'].setChecked(True)
        dialog.inputs['label_color'].setText('bad')
        self.assertFalse(dialog.apply_button.isEnabled())
        dialog.apply_changes()
        self.assertIsNone(dialog.result_rows)
        dialog.preset('defaults')
        self.assertTrue(dialog.apply_button.isEnabled())
        dialog.apply_changes()
        self.assertIsNone(dialog.result_rows[0]['label_enabled'])

    def test_color_picker_and_inspector_commit_with_undo(self):
        editor = ConfigEditor(self.doc)
        self.addCleanup(editor.close)
        field = editor.inputs['label_color']
        self.assertIsInstance(field, ColorField)
        with patch('ra_batch.QColorDialog.getColor', return_value=QColor('#FF0033')):
            field.choose()
        self.assertEqual(self.doc.rows[0]['label_color'], '#FF0033')
        editor.model.stack.undo()
        self.assertEqual(self.doc.rows[0]['label_color'], '#12ABCD')
        with patch('ra_batch.QColorDialog.getColor', return_value=QColor()):
            field.choose()
        self.assertEqual(self.doc.rows[0]['label_color'], '#12ABCD')

    def test_native_style_preview_retains_fill_inside_outline(self):
        dialog = RaBatchDialog(self.doc.rows)
        self.addCleanup(dialog.close)
        dialog.preset('standard')
        dialog.inputs['label_color'].setText('#FF0000')
        dialog.preview.resize(602, 150)
        image = dialog.preview.grab().toImage()
        red = sum(image.pixelColor(x, y).red() > 180 and image.pixelColor(x, y).green() < 60
                  for y in range(image.height()) for x in range(image.width()))
        self.assertGreater(red, 50)

    def test_old_config_import_adds_optional_columns_and_preserves_user_cells(self):
        path = self.root / 'old_import.xlsx'
        ra_config.create_config(path, [[str(self.source), 1, '分组', '自定义', 0, '关闭', 80, 0, 1]])
        wb = openpyxl.load_workbook(path)
        wb.active.delete_cols(10, 6)
        wb.active.cell(1, 10, '备注')
        wb.active.cell(2, 10, '用户说明')
        wb.create_sheet('用户页').append(['原样保留'])
        wb.save(path)
        wb.close()
        before = path.read_bytes()
        result = ra_config.import_files(path, self.root)
        self.assertEqual(Path(result['backup']).read_bytes(), before)
        wb = openpyxl.load_workbook(path)
        try:
            self.assertEqual(wb.active.cell(2, 3).value, '分组')
            self.assertEqual(wb.active.cell(2, 10).value, '用户说明')
            self.assertEqual(wb['用户页']['A1'].value, '原样保留')
        finally:
            wb.close()
        self.assertTrue(ra_config.load_config(path)[0]['label_style']['label_enabled'])
        self.assertIsNone(ra_config.import_files(path, self.root)['backup'])

    def test_bmp_old_default_pixels_and_off_unmodified_crop(self):
        source = self.root / 'real.bmp'
        Image.new('RGB', (extract_ra.IMG_W, extract_ra.IMG_H), '#274035').save(source)
        before = source.read_bytes()
        expected = Image.new('RGB', (602, 325), '#274035')
        ImageDraw.Draw(expected).text((10, 4), 'Ra=12.345 nm', font=extract_ra._label_font(44),
                                     fill=(255, 255, 255), stroke_width=3, stroke_fill=(0, 0, 0))
        out = self.root / 'on.png'
        extract_ra.export_plot(source, out, 'Ra=12.345 nm')
        with Image.open(out) as actual:
            self.assertEqual(actual.tobytes(), expected.tobytes())
        extract_ra.export_plot(source, self.root / 'off.png', 'Ra=12.345 nm', {'label_enabled': False})
        with Image.open(self.root / 'off.png') as actual:
            self.assertEqual(actual.tobytes(), Image.new('RGB', actual.size, '#274035').tobytes())
        self.assertEqual(source.read_bytes(), before)

    def test_color_position_and_font_size_change_expected_pixels(self):
        small, large = Image.new('RGB', (602, 325)), Image.new('RGB', (602, 325))
        style = dict(label_color='#FF0000', label_stroke=0, label_position='右下')
        plot_labels.annotate(small, 'Ra=1.000 nm', dict(style, label_size=20))
        plot_labels.annotate(large, 'Ra=1.000 nm', dict(style, label_size=40))
        a, b = np.asarray(small), np.asarray(large)
        self.assertGreater(np.count_nonzero(b), np.count_nonzero(a))
        self.assertTrue(np.all(b[:, :, 1:] == 0))
        yy, xx = np.nonzero(a[:, :, 0])
        self.assertGreater(xx.min(), 300)
        self.assertGreater(yy.min(), 270)
        self.assertLessEqual(xx.max(), 592)
        self.assertLessEqual(yy.max(), 315)

    def test_dat_annotation_shared_by_single_and_combined_without_metric_changes(self):
        yy, xx = np.mgrid[:24, :32]
        z = np.sin(xx/4)*15 + np.cos(yy/5)*8
        before = z.copy()
        stats = zygo_dat.metrics(z)
        images, metadata = zygo_render.render(z, np.full(z.shape, 100.), 0.5, stats, self.assets)
        on, annotated = zygo_render.render(z, np.full(z.shape, 100.), 0.5, stats, self.assets,
                                           label_style=dict(label_enabled=True, label_size=24, label_color='#FF0000'))
        np.testing.assert_array_equal(z, before)
        self.assertEqual(stats, zygo_dat.metrics(z))
        self.assertEqual(metadata['profile'], annotated['profile'])
        self.assertNotEqual(images['oblique_plot'].tobytes(), on['oblique_plot'].tobytes())
        self.assertEqual(on['four_panel'].crop((484, 50, 1086, 375)).tobytes(), on['oblique_plot'].tobytes())
        self.assertEqual(on['surface_map'].tobytes(), images['surface_map'].tobytes())
        explicit_off, _ = zygo_render.render(z, np.full(z.shape, 100.), 0.5, stats, self.assets,
                                             label_style={'label_enabled': False, 'label_size': 80})
        self.assertEqual(images['four_panel'].tobytes(), explicit_off['four_panel'].tobytes())


    def test_center_positions_and_large_font_stay_inside_plot(self):
        width, height = 602, 325
        for position, expected in [('上方中间', '上方中间'), ('下方中间', '下方中间'),
                                   ('上中', '上方中间'), ('顶部居中', '上方中间')]:
            self.assertEqual(plot_labels.settings({'label_position': position})['label_position'], expected)
        for position in ('上方中间', '下方中间'):
            for size in (8, 44, 96):
                with self.subTest(position=position, size=size):
                    image = Image.new('RGB', (width, height))
                    plot_labels.annotate(image, 'Sa=123.456 nm',
                                         {'label_position': position, 'label_size': size})
                    ink = np.asarray(image)[:, :, 0] > 0
                    yy, xx = np.nonzero(ink)
                    self.assertGreaterEqual(xx.min(), 0)
                    self.assertLessEqual(xx.max(), width - 1)
                    self.assertGreaterEqual(yy.min(), 0)
                    self.assertLessEqual(yy.max(), height - 1)
                    self.assertLessEqual(xx.max() - xx.min() + 1, width - 2 * 10)
                    self.assertLessEqual(abs((xx.min() + xx.max()) / 2 - (width - 1) / 2), 1.5)
                    if position == '上方中间':
                        self.assertLessEqual(yy.min(), 60)
                    else:
                        self.assertGreaterEqual(yy.max(), 260)


if __name__ == '__main__':
    unittest.main()

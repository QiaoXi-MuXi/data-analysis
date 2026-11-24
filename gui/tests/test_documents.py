# -*- coding: utf-8 -*-
"""配置持久化和原生编辑器的关键回归；夹具均在独立临时目录。"""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from documents import (Document, RA_HEADERS, RAMAN_HEADERS, atomic_write, digest, read_exchange,
                       load_json, validate, validate_options, write_snapshot_xlsx, export_csv, append_sources)
from PySide6.QtWidgets import QApplication
from editor import ConfigEditor
import openpyxl


class ConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='data-analysis-config-')
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        (self.root / 'data').mkdir()
        (self.root / 'data' / 'sample.dat').write_bytes(b'sample')
        self.row = dict(file='data/sample.dat', enabled=True, region=None, stem=None, noise=None, high=None, cutoff=None, combined=None, single=None)
        self.doc = Document('Ra', self.root, [deepcopy(self.row)])

    def book(self, headers, rows, name='config.xlsx'):
        path = self.root / name
        wb = openpyxl.Workbook()
        wb.active.append(headers)
        for row in rows:
            wb.active.append(row)
        wb.create_sheet('额外说明').append(['保留原表', '=1+2'])
        wb.save(path)
        wb.close()
        return path

    def test_json_roundtrip_keeps_defaults_and_types(self):
        target = self.root / 'projects' / 'project.json'
        self.doc.options = {'input': str(self.root / 'data'), 'no_plot': True, 'mode': 'config'}
        self.doc.save(target)
        loaded = load_json(target, 'Ra')
        self.assertEqual(loaded.rows, self.doc.rows)
        self.assertEqual(loaded.base, self.root)
        self.assertEqual(loaded.options, self.doc.options)

    def test_project_can_be_relocated(self):
        self.doc.options = {'input': str(self.root / 'data')}
        self.doc.save(self.root / 'project.json')
        moved = self.root / 'moved'
        moved.mkdir()
        shutil.copyfile(self.root / 'project.json', moved / 'project.json')
        loaded = load_json(moved / 'project.json', 'Ra')
        self.assertEqual(loaded.base, moved)
        self.assertEqual(loaded.options['input'], str(moved / 'data'))

    def test_external_modification_is_not_overwritten(self):
        target = self.root / 'project.json'
        self.doc.save(target)
        target.write_text('{"external":true}', encoding='utf-8')
        before = target.read_bytes()
        with self.assertRaisesRegex(ValueError, '外部修改'):
            self.doc.save()
        self.assertEqual(target.read_bytes(), before)

    def test_update_creates_exact_backup(self):
        target = self.root / 'project.json'
        self.doc.save(target)
        original = target.read_bytes()
        self.doc.rows[0]['region'] = '测试分组'
        backup = self.doc.save()
        self.assertEqual(backup.read_bytes(), original)

    def test_save_as_does_not_overwrite_existing_file(self):
        target = self.root / 'existing.json'
        target.write_text('existing', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.doc.save(target)
        self.assertEqual(target.read_text(), 'existing')

    def test_xlsx_import_keeps_disabled_missing_rows_and_source(self):
        path = self.book(RA_HEADERS, [['missing.dat', 0, None, None, None, None, None, None, None]])
        before = path.read_bytes()
        loaded = read_exchange(path, 'Ra')
        self.assertEqual(len(loaded.rows), 1)
        self.assertFalse(loaded.rows[0]['enabled'])
        validate(loaded)
        loaded.save(self.root / 'imported.json')
        self.assertEqual(path.read_bytes(), before)

    def test_xlsx_reordered_aliases_and_extra_columns(self):
        path = self.book(['输出名', '文件', '备注', '备注'], [['改名', 'data/sample.dat', 'A', 'B']])
        loaded = read_exchange(path, 'Ra')
        self.assertEqual(loaded.rows[0]['stem'], '改名')
        self.assertEqual([x['value'] for x in loaded.rows[0]['_extra_columns']], ['A', 'B'])

    def test_duplicate_recognized_columns_rejected(self):
        path = self.book(['文件', 'file'], [['a.dat', 'b.dat']])
        with self.assertRaisesRegex(ValueError, '重复'):
            read_exchange(path, 'Ra')

    def test_formula_preserved_as_invalid_draft(self):
        path = self.book(['文件', '截止波长'], [['data/sample.dat', '=1+2']])
        loaded = read_exchange(path, 'Ra')
        self.assertEqual(loaded.rows[0]['cutoff'], '=1+2')
        with self.assertRaises(ValueError):
            loaded.save(self.root / 'bad.json')

    def test_missing_file_can_be_saved_but_cannot_run(self):
        self.doc.rows[0]['file'] = 'missing.dat'
        self.doc.save(self.root / 'missing.json')
        with self.assertRaisesRegex(ValueError, '不存在'):
            validate(self.doc, for_run=True)

    def test_no_enabled_rows_can_be_saved_but_cannot_run(self):
        self.doc.rows[0]['enabled'] = False
        self.doc.save(self.root / 'disabled.json')
        with self.assertRaisesRegex(ValueError, '至少启用'):
            validate(self.doc, for_run=True)

    def test_invalid_numeric_draft_blocks_save(self):
        for value in (-1, True, float('inf'), 'bad'):
            self.doc.rows[0]['cutoff'] = value
            with self.assertRaises(ValueError):
                self.doc.save(self.root / 'bad.json')

    def test_duplicate_sources_and_output_names_rejected(self):
        self.doc.rows.append(deepcopy(self.row))
        with self.assertRaisesRegex(ValueError, '源文件重复'):
            validate(self.doc)
        self.doc.rows[1]['file'] = 'another.dat'
        self.doc.rows[0]['stem'] = self.doc.rows[1]['stem'] = 'same'
        with self.assertRaisesRegex(ValueError, '输出文件名重复'):
            validate(self.doc)

    def test_windows_reserved_output_name_rejected(self):
        self.doc.rows[0]['stem'] = 'CON.txt'
        with self.assertRaisesRegex(ValueError, 'Windows'):
            validate(self.doc)

    def test_snapshot_resolves_original_relative_paths(self):
        target = self.root / 'snapshot.xlsx'
        paths = write_snapshot_xlsx(self.doc, {}, target)
        self.assertEqual(paths, [self.root / 'data' / 'sample.dat'])
        wb = openpyxl.load_workbook(target)
        self.assertEqual(wb.active.cell(2, 1).value, str(paths[0]))
        self.assertIsNone(wb.active.cell(2, 5).value)
        wb.close()

    def test_csv_roundtrip_preserves_chinese_quotes_and_defaults(self):
        self.doc.rows[0]['region'] = '中文, "分组"\n第二行'
        target = self.root / 'exchange.csv'
        export_csv(self.doc, target)
        loaded = read_exchange(target, 'Ra')
        self.assertEqual(loaded.rows[0]['region'], self.doc.rows[0]['region'])
        self.assertIsNone(loaded.rows[0]['noise'])
        self.assertTrue(loaded.rows[0]['enabled'])
        self.assertEqual(validate(loaded, for_run=True)[0][1], self.root / 'data' / 'sample.dat')

    def test_append_does_not_prune_missing_rows(self):
        self.doc.rows[0]['file'] = 'missing.dat'
        self.doc.rows[0]['enabled'] = False
        rows = append_sources(self.doc, [self.root / 'data' / 'sample.dat'])
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(self.doc.rows), 1)
        self.assertEqual(self.doc.rows[0]['file'], 'missing.dat')

    def test_raman_import_matches_legacy_boolean_rules(self):
        path = self.book(RAMAN_HEADERS, [['a.txt', '', 'on', '关闭', None, None, None, None]], 'raman.xlsx')
        loaded = read_exchange(path, 'Raman')
        self.assertFalse(loaded.rows[0]['normalize'])
        self.assertTrue(loaded.rows[0]['peaks'])

    def test_raman_normalization_constraint(self):
        path = self.book(RAMAN_HEADERS, [['a.txt', None, 1, 1, None, None, None, None], ['b.txt', None, 0, 1, None, None, None, None]], 'raman.xlsx')
        with self.assertRaisesRegex(ValueError, '归一化'):
            validate(read_exchange(path, 'Raman'))

    def test_raman_shared_window_preserves_first_values(self):
        path = self.book(RAMAN_HEADERS, [['a.txt', None, 1, 1, None, None, 100, None], ['b.txt', None, 1, 1, None, None, None, 1000]], 'raman.xlsx')
        loaded = read_exchange(path, 'Raman')
        self.assertEqual(len(validate(loaded)), 2)
        loaded.rows[1]['xmax'] = 50
        with self.assertRaises(ValueError):
            validate(loaded)

    def test_wrong_module_and_future_version_are_rejected(self):
        path = self.root / 'project.json'
        self.doc.save(path)
        with self.assertRaisesRegex(ValueError, '属于'):
            load_json(path, 'Raman')
        value = json.loads(path.read_text(encoding='utf-8'))
        value['schema_version'] = 999
        path.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '版本'):
            load_json(path, 'Ra')

    def test_global_options_are_validated(self):
        for options in ({'batch': '1.5'}, {'batch': '-1'}, {'no_plot': 'false'}, {'input': True}, {'mode': 'unknown'}):
            with self.assertRaises(ValueError):
                validate_options('Ra', options)
        with self.assertRaises(ValueError):
            validate_options('CLSM', {'scale': '0'})

    def test_native_editor_undo_redo_and_invalid_draft(self):
        editor = ConfigEditor(self.doc)
        editor.model.set_value(0, 'cutoff', '-1')
        self.assertEqual(editor.document.rows[0]['cutoff'], '-1')
        self.assertTrue(editor.dirty())
        with self.assertRaises(ValueError):
            editor.document.save(self.root / 'invalid.json')
        editor.model.stack.undo()
        self.assertIsNone(editor.document.rows[0]['cutoff'])
        editor.model.stack.redo()
        self.assertEqual(editor.document.rows[0]['cutoff'], '-1')
        editor.close()

    def test_native_editor_and_inspector_share_changes(self):
        editor = ConfigEditor(self.doc)
        editor.model.set_value(0, 'region', '新分组')
        self.assertEqual(editor.inputs['region'].text(), '新分组')
        editor.inputs['cutoff'].setText('120')
        editor.commit('cutoff')
        self.assertEqual(editor.document.rows[0]['cutoff'], 120)
        editor.close()


if __name__ == '__main__':
    unittest.main()

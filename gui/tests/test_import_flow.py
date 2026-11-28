# -*- coding: utf-8 -*-
"""导入样本的替换/追加、目录筛选与跳过反馈。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
GUI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GUI))

from PySide6.QtWidgets import QApplication

from documents import Document, append_sources
from editor import ConfigEditor as Editor


class ImportFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='import-flow-'))
        self.input = self.root / 'input'
        self.input.mkdir()
        for name in ('spec_1.txt', 'spec_2.txt', 'spec_10.txt'):
            (self.input / name).write_text('x')
        (self.input / 'note.png').write_text('x')
        nested = self.input / 'nested'
        nested.mkdir()
        (nested / 'spec_3.txt').write_text('x')
        self.document = Document('Raman', self.root)
        self.document.options = {'input': str(self.input)}
        self.editor = Editor(self.document)

    def tearDown(self):
        self.editor.deleteLater()

    def test_replace_import_drops_the_previous_rows(self):
        old = append_sources(self.document, [self.input / 'spec_1.txt'])
        self.document.rows = old
        added = self.editor.import_paths([self.input / 'spec_2.txt', self.input / 'spec_10.txt'], replace=True)
        self.assertEqual(added, 2)
        names = [Path(row['file']).name for row in self.document.rows]
        self.assertEqual(names, ['spec_2.txt', 'spec_10.txt'])
        self.assertNotIn('spec_1.txt', names)

    def test_append_import_keeps_old_rows_and_dedups(self):
        self.document.rows = append_sources(self.document, [self.input / 'spec_1.txt'])
        added = self.editor.import_paths([self.input / 'spec_1.txt', self.input / 'spec_2.txt'], replace=False)
        self.assertEqual(added, 1)
        self.assertEqual(len(self.document.rows), 2)

    def test_folder_scan_is_single_level_and_extension_filtered(self):
        found = [path.name for path in self.editor.scan_folder(self.input)]
        self.assertEqual(found, ['spec_1.txt', 'spec_2.txt', 'spec_10.txt'])

    def test_unsupported_files_are_skipped_with_a_count(self):
        added = self.editor.import_paths([self.input / 'note.png', self.input / 'spec_1.txt'], replace=True)
        self.assertEqual(added, 1)
        self.assertIn('跳过 1 个不支持的文件', self.editor.message.text())

    def test_nothing_importable_reports_instead_of_clearing_rows(self):
        self.document.rows = append_sources(self.document, [self.input / 'spec_1.txt'])
        added = self.editor.import_paths([self.input / 'note.png'], replace=True)
        self.assertEqual(added, 0)
        self.assertEqual(len(self.document.rows), 1)

    def test_dialog_starts_in_the_input_directory(self):
        self.assertEqual(self.editor.start_dir(), str(self.input))

    def test_import_button_exposes_all_sources(self):
        from PySide6.QtWidgets import QToolButton
        buttons = self.editor.findChildren(QToolButton)
        import_button = next(b for b in buttons if b.text().startswith('导入样本'))
        labels = [action.text() for action in import_button.menu().actions() if not action.isSeparator()]
        self.assertEqual(labels, ['选择文件…（可多选）', '选择文件夹…（按扩展名筛选）',
                                  '从输入目录导入…', '导入项目 / 配置（JSON / XLSX / CSV）…'])
        self.assertTrue(self.editor.table.acceptDrops())


if __name__ == '__main__':
    unittest.main()


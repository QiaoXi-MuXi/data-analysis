# -*- coding: utf-8 -*-
"""SCI-Pic page: template thumbnails, descriptions and Chinese style labels."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'gui'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from app import Window, SCI_STYLE_LABELS, SciPicPage


class SciPicPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='scipic-preview-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = ROOT / 'sci-pic'
        (self.root / 'sci-pic').mkdir()
        for name in ('templates.json',):
            (self.root / 'sci-pic' / name).write_text(
                (source / name).read_text(encoding='utf-8'), encoding='utf-8')
        for name in ('templates', 'previews'):
            shutil.copytree(source / name, self.root / 'sci-pic' / name)
        self.window = Window(self.root)
        self.addCleanup(self.stop)
        self.page = self.window.scipic_page

    def stop(self):
        self.window.pool.waitForDone(10000)
        self.window.deleteLater()

    def test_template_catalog_fills_combo_and_preview(self):
        self.assertEqual(self.page.template.count(), 2)
        self.assertEqual(self.page.template.currentText(), '柱状折线图')
        self.assertEqual(self.page.preview_title.text(), '柱状折线图')
        self.assertIn('柱状折线', self.page.preview_type.text())
        self.assertIn('roughness.png', self.page.preview_type.text())
        self.assertTrue(self.page.preview_desc.text().strip())
        self.assertTrue(self.page.preview_example.text().startswith('示例：'))
        pixmap = self.page.preview_image.pixmap()
        self.assertFalse(pixmap.isNull())
        self.assertGreater(pixmap.width(), 100)

    def test_switching_template_updates_thumbnail_and_text(self):
        first = self.page.preview_image.pixmap().cacheKey()
        self.page.template.setCurrentIndex(1)
        self.assertEqual(self.page.template.currentText(), '双组对比柱状图')
        self.assertEqual(self.page.preview_title.text(), '双组对比柱状图')
        self.assertIn('removal_rate.png', self.page.preview_type.text())
        second = self.page.preview_image.pixmap().cacheKey()
        self.assertNotEqual(first, second)

    def test_style_table_uses_chinese_labels_but_keeps_raw_keys(self):
        self.assertEqual(self.page.style_table.rowCount(), len(SCI_STYLE_LABELS))
        name = self.page.style_table.item(0, 0)
        self.assertEqual(name.text(), '样式预设')
        self.assertEqual(name.data(Qt.ItemDataRole.UserRole), 'style_preset')
        self.assertEqual(name.toolTip(), 'style_preset')
        self.assertIn('pt', self.page.style_table.item(2, 0).text())
        self.page.style_table.item(0, 1).setText('paper')
        self.assertEqual(list(self.page.values()), ['style_preset'])
        self.assertEqual(self.page.values()['style_preset'], 'paper')

    def test_page_is_scrollable_and_tabs_reach_all_four_groups(self):
        self.assertIsInstance(self.page, SciPicPage)
        self.assertIsNotNone(self.page.body)
        self.assertEqual(self.page.tabs.count(), 3)
        self.assertEqual([self.page.tabs.tabText(i) for i in range(3)],
                         ['01  输入与示意图', '02  映射与样式', '03  运行与结果'])
        self.assertTrue(self.page.body.widgetResizable())
        self.assertIsNotNone(self.page.body.widget())

    def test_workbook_open_buttons_follow_the_file(self):
        page = self.page
        page.workbook.setText('')
        self.assertFalse(page.open_workbook_button.isEnabled())
        self.assertFalse(page.reveal_workbook_button.isEnabled())
        missing = self.root / 'sci-pic' / 'out' / 'workbooks' / 'copy.xlsx'
        page.workbook.setText(str(missing))
        self.assertFalse(page.open_workbook_button.isEnabled())
        self.assertTrue(page.reveal_workbook_button.isEnabled())
        missing.parent.mkdir(parents=True, exist_ok=True)
        ready = self.root / 'sci-pic' / 'out' / 'workbooks' / 'copy-2.xlsx'
        ready.write_bytes(b'')
        page.workbook.setText(str(ready))
        self.assertTrue(page.open_workbook_button.isEnabled())
        self.assertEqual(page.open_workbook_button.toolTip(), str(ready))
        self.assertIn('双击', page.workbook.toolTip())

    def test_new_copy_opens_the_file_and_enables_the_button(self):
        import app as app_module
        book = self.root / 'sci-pic' / 'out' / 'workbooks' / 'run-01.xlsx'
        book.parent.mkdir(parents=True, exist_ok=True)
        book.write_bytes(b'')
        opened = []
        with mock.patch.object(app_module.sci_pic_services, 'working_copy', return_value=book), \
                mock.patch.object(app_module, 'open_path', side_effect=opened.append):
            self.page.create_copy()
        self.assertEqual(opened, [book])
        self.assertEqual(self.page.workbook.text(), str(book))
        self.assertTrue(self.page.open_workbook_button.isEnabled())
        self.assertIn('run-01.xlsx', self.page.status.text())
        self.assertEqual(self.page.status.toolTip(), str(book))

    def test_recent_copies_merge_history_and_output_folder(self):
        page = self.page
        out = self.root / 'sci-pic' / 'out'
        out.mkdir(parents=True, exist_ok=True)
        older = out / 'older.xlsx'
        newer = out / 'newer.xlsx'
        for path, stamp in ((older, 1600000000), (newer, 1600000060)):
            path.write_bytes(b'')
            os.utime(path, (stamp, stamp))
        page.output.setText(str(out))
        self.assertEqual([p.name for p in page.recent_paths()], ['newer.xlsx', 'older.xlsx'])
        page.state['recent_workbooks'] = [str(older)]
        names = [p.name for p in page.recent_paths()]
        self.assertEqual(names, ['older.xlsx', 'newer.xlsx'])
        self.assertEqual(len(names), len(set(names)))

    def test_recent_list_keeps_eight_entries(self):
        page = self.page
        out = self.root / 'sci-pic' / 'out'
        out.mkdir(parents=True, exist_ok=True)
        for index in range(10):
            path = out / f'copy-{index}.xlsx'
            path.write_bytes(b'')
            os.utime(path, (1600000000 + index, 1600000000 + index))
        page.output.setText(str(out))
        self.assertEqual(len(page.recent_paths()), 8)
        self.assertEqual(page.recent_paths()[0].name, 'copy-9.xlsx')

    def test_using_a_recent_copy_updates_field_and_history(self):
        page = self.page
        out = self.root / 'sci-pic' / 'out'
        out.mkdir(parents=True, exist_ok=True)
        book = out / 'pick.xlsx'
        book.write_bytes(b'')
        page.state['recent_workbooks'] = [str(self.root / 'old.xlsx')]
        with mock.patch.object(page, 'load_style'):
            page.use_recent(book)
        self.assertEqual(page.workbook.text(), str(book))
        self.assertEqual(page.state['recent_workbooks'][0], str(book))
        self.assertIn(str(book), page.state_values()['recent_workbooks'])

    def test_workbook_field_points_at_where_copies_are_created(self):
        self.assertIn('新建工作副本', self.page.workbook.placeholderText())
        self.assertIn('workbooks', self.page.workbook.placeholderText())
        self.assertEqual(self.page.workbook.text(), '')

    def test_navigation_reaches_scipic_page(self):
        self.window.navigate_to_key('sci-pic')
        self.assertIs(self.window.stack.currentWidget(), self.page)


if __name__ == '__main__':
    unittest.main()

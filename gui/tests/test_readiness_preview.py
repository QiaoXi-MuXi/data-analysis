# -*- coding: utf-8 -*-
"""输入检查与异步预览边界；使用独立临时目录和合成PDF。"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PIL import Image
from documents import Document, inspect_document, readiness, validate
from editor import ConfigEditor
from pdf_preview import render_pdf, MAX_BYTES
from app import Preview, Window


class DeferredOwner:
    def __init__(self):
        self.jobs = []

    def submit(self, function, callback):
        self.jobs.append((function, callback))

    def complete(self, index=0):
        function, callback = self.jobs.pop(index)
        try:
            value = function()
        except Exception as exc:
            callback(None, str(exc))
        else:
            callback(value, None)


class ReadinessPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='data-analysis-preview-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.doc = Document('Ra', self.root, [dict(file='missing.bmp', enabled=True)])

    def pdf(self, name='多页.pdf'):
        path = self.root / name
        with Image.new('RGB', (240, 180), '#ff0000') as first, Image.new('RGB', (300, 200), '#0000ff') as second:
            first.save(path, save_all=True, append_images=[second], format='PDF')
        return path

    def test_all_row_problems_collected_and_defaults_unchanged(self):
        self.doc.rows += [dict(file='other.dat', enabled=True, cutoff='bad'), dict(file='停用.bmp', enabled=False)]
        before = deepcopy(self.doc.rows)
        problems = readiness(self.doc)
        self.assertEqual([p.row for p in problems], [1, 2])
        self.assertFalse(problems[0].blocks_save)
        self.assertTrue(problems[1].blocks_save)
        self.assertEqual(problems[1].key, 'cutoff')
        self.assertEqual(self.doc.rows, before)

    def test_missing_files_do_not_block_saving_and_disabled_rows_skipped(self):
        self.doc.save(self.root / 'project.json')
        with self.assertRaisesRegex(ValueError, '源文件不存在'):
            validate(self.doc, for_run=True)
        self.doc.rows[0]['enabled'] = False
        self.assertEqual(readiness(self.doc)[0].message, '请至少启用一个样本')
        self.assertFalse(readiness(self.doc)[0].blocks_save)

    def test_cross_row_raman_window_is_checked_together(self):
        doc = Document('Raman', self.root, [dict(file='a.txt', xmin=100), dict(file='b.txt', xmax=1000)])
        self.assertFalse(inspect_document(doc)[1])
        doc.rows[1]['normalize'] = True
        self.assertIn('归一化', inspect_document(doc)[1][0].message)

    def test_repeated_source_and_output_diagnostics_match_save_validation(self):
        self.doc.rows += [dict(file='missing.bmp', enabled=True)]
        problems = readiness(self.doc)
        self.assertEqual(problems[1].row, 2)
        self.assertIn('重复', problems[1].message)
        with self.assertRaisesRegex(ValueError, '第 2 行.*重复'):
            validate(self.doc)

    def test_native_problem_click_clears_filters_and_locates_row(self):
        self.doc.rows.insert(0, dict(file='disabled.bmp', enabled=False))
        editor = ConfigEditor(self.doc)
        editor.search.setText('没有匹配')
        editor.check_inputs()
        self.assertEqual(editor.problems_list.count(), 1)
        editor.focus_problem(editor.problems_list.item(0))
        self.assertEqual(editor.table.currentIndex().row(), 1)
        self.assertEqual(editor.search.text(), '')
        status = editor.model.index(1, editor.model.columnCount()-1)
        self.assertEqual(status.data(), '文件缺失')
        self.assertFalse(editor.model.flags(status) & Qt.ItemFlag.ItemIsEditable)
        editor.close()

    def test_reselect_source_preserves_parameters_and_is_undoable(self):
        target = self.root / 'new.bmp'
        target.write_bytes(b'fixture')
        self.doc.rows[0]['region'] = '分组A'
        editor = ConfigEditor(self.doc)
        with patch('editor.QFileDialog.getOpenFileName', return_value=(str(target), '')):
            editor.choose_source()
        editor.check_inputs()
        self.assertFalse(editor.problems)
        self.assertEqual(editor.document.rows[0]['region'], '分组A')
        editor.model.stack.undo()
        self.assertEqual(editor.document.rows[0]['file'], 'missing.bmp')
        editor.close()

    def test_invalid_bmp_dat_parameter_can_be_repaired_in_inspector(self):
        self.doc.rows[0]['cutoff'] = '非法原值'
        editor = ConfigEditor(self.doc)
        self.assertTrue(editor.inputs['cutoff'].isEnabled())
        editor.inputs['cutoff'].setText('')
        editor.commit('cutoff')
        self.assertIsNone(editor.document.rows[0]['cutoff'])
        self.assertFalse(editor.inputs['cutoff'].isEnabled())
        validate(editor.document)
        editor.close()

    def test_old_input_check_cannot_replace_new_report(self):
        owner = DeferredOwner()
        editor = ConfigEditor(self.doc, submit=owner.submit)
        editor.check_inputs()
        editor.model.set_value(0, 'enabled', False)
        editor.check_inputs()
        owner.complete(1)
        owner.complete(0)
        self.assertEqual(editor.problems[0].message, '请至少启用一个样本')
        editor.close()

    def test_global_option_errors_are_visible(self):
        self.doc.options['batch'] = '-2'
        problem = readiness(self.doc)[0]
        self.assertIsNone(problem.row)
        self.assertIn('运行设置', problem.message)

    def test_pdf_pages_have_independent_images_and_no_file_changes(self):
        path = self.pdf()
        original = path.read_bytes()
        first, second = render_pdf(path), render_pdf(path, 2)
        self.assertEqual(first.count, 2)
        self.assertEqual(second.page, 2)
        self.assertGreater(first.image.pixelColor(100,100).red(), 220)
        self.assertGreater(second.image.pixelColor(100,100).blue(), 220)
        self.assertEqual(path.read_bytes(), original)
        path.unlink()  # Windows应可删除：没有未释放的文件句柄。
        self.assertGreater(first.image.pixelColor(100,100).red(), 220)

    def test_pdf_bounds_corrupt_files_and_size_limit(self):
        path = self.pdf()
        for page in (0, 3):
            with self.assertRaisesRegex(ValueError, '页码'):
                render_pdf(path, page)
        corrupt = self.root / 'broken.pdf'
        corrupt.write_bytes(b'not a pdf')
        with self.assertRaisesRegex(ValueError, 'PDF无法预览'):
            render_pdf(corrupt)
        oversized = self.root / 'large.pdf'
        with oversized.open('wb') as stream:
            stream.truncate(MAX_BYTES + 1)
        with self.assertRaisesRegex(ValueError, '64MB'):
            render_pdf(oversized)

    def test_parallel_pdf_requests_are_serialized_safely(self):
        path = self.pdf()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda page: render_pdf(path, page), [1,2,1,2]))
        self.assertEqual([result.page for result in results], [1,2,1,2])

    def test_pdf_render_resolution_is_bounded(self):
        path = self.root / 'wide.pdf'
        with Image.new('RGB', (3000, 100), '#00ff00') as image:
            image.save(path, format='PDF')
        frame = render_pdf(path)
        self.assertLessEqual(max(frame.image.width(), frame.image.height()), 1800)

    def test_pdf_preview_can_recover_after_failed_file(self):
        owner = DeferredOwner()
        preview = Preview(owner)
        broken = self.root / 'broken.pdf'
        broken.write_bytes(b'not pdf')
        preview.show_file(broken)
        owner.complete()
        self.assertIn('无法预览', preview.empty.text())
        preview.show_file(self.pdf())
        owner.complete()
        self.assertIs(preview.stack.currentWidget(), preview.image)
        self.assertTrue(preview.pdf_page.isEnabled())
        preview.close()

    def test_pdf_navigation_and_stale_page_completion(self):
        owner = DeferredOwner()
        preview = Preview(owner)
        preview.show_file(self.pdf())
        owner.complete()
        self.assertEqual(preview.pdf_page.maximum(), 2)
        self.assertFalse(preview.previous_page.isEnabled())
        self.assertTrue(preview.next_page.isEnabled())
        preview.pdf_page.setValue(2)
        owner.complete()
        self.assertIn('第2/2页', preview.caption.text())
        self.assertGreater(preview.image.original.toImage().pixelColor(100,100).blue(), 220)
        preview.pdf_page.setValue(1)
        preview.reset('新运行没有结果')
        owner.complete()
        self.assertIs(preview.stack.currentWidget(), preview.empty)
        self.assertEqual(preview.empty.text(), '新运行没有结果')
        preview.close()

    def test_old_sheet_or_text_cannot_refill_after_reset(self):
        owner = DeferredOwner()
        preview = Preview(owner)
        path = self.root / 'old.txt'
        path.write_text('old value', encoding='utf-8')
        preview.show_file(path)
        preview.reset('已切换运行')
        owner.complete()
        self.assertIs(preview.stack.currentWidget(), preview.empty)
        self.assertEqual(preview.empty.text(), '已切换运行')
        preview.close()

    def test_csv_preview_keeps_quoted_multiline_cells(self):
        owner = DeferredOwner()
        preview = Preview(owner)
        path = self.root / 'multiline.csv'
        path.write_text('文件,说明\na.txt,"第一行\n第二行"\n', encoding='utf-8-sig')
        preview.show_file(path)
        owner.complete()
        self.assertEqual(preview.table.rowCount(), 2)
        self.assertEqual(preview.table.item(1,1).text(), '第一行\n第二行')
        preview.close()

    def test_long_csv_field_shows_error_instead_of_raising_from_callback(self):
        owner = DeferredOwner()
        preview = Preview(owner)
        path = self.root / 'long.csv'
        path.write_text('name,value\nsample,' + 'x'*150000, encoding='utf-8')
        preview.show_file(path)
        owner.complete()
        self.assertIs(preview.stack.currentWidget(), preview.empty)
        self.assertIn('CSV字段过长', preview.empty.text())
        preview.close()

    def test_er_input_preview_is_read_only_and_uses_selected_path(self):
        # 仅在独立空工作区构建窗口，不调用分析引擎。
        path = self.pdf()
        folder = self.root / 'ER/data'
        folder.mkdir(parents=True)
        target = folder / path.name
        target.write_bytes(path.read_bytes())
        window = Window(self.root)
        self.addCleanup(lambda: window.pool.waitForDone(10000))
        page = window.pages['ER']
        page.files.setRowCount(1)
        from PySide6.QtWidgets import QTableWidgetItem
        item = QTableWidgetItem(target.name)
        item.setData(Qt.ItemDataRole.UserRole, str(target))
        page.files.setItem(0,0,item)
        page.select_input(0,0,-1,-1)
        self.assertEqual(page.input_preview.path, target)
        self.assertFalse(page.files.editTriggers())
        for other in window.pages.values():
            if other.editor:
                other.editor.validation_timer.stop()
        window.pool.waitForDone(10000)
        self.application.processEvents()
        window.close()


if __name__ == '__main__':
    unittest.main()

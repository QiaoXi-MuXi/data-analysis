# -*- coding: utf-8 -*-
"""P4: GUI style configuration parsing and SCI-Pic run dispatch regression."""
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
GUI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GUI))
ROOT = GUI.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication
import openpyxl

import documents
import sci_pic
from app import Window


class StyleGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        # 沙箱建在系统临时目录，用例结束自动删除；项目根目录不再留 _*_test 残件。
        self.temp = tempfile.TemporaryDirectory(prefix='style-p4-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def fixture_root(self):
        (self.root / 'sci-pic' / 'templates').mkdir(parents=True, exist_ok=True)
        (self.root / 'sci-pic' / 'previews').mkdir(parents=True, exist_ok=True)
        (self.root / 'sci-pic' / 'out').mkdir(parents=True, exist_ok=True)
        (self.root / 'sci-pic' / 'previews' / 'sci_pic.png').write_bytes(b'\x89PNG\r\n\x1a\n')
        (self.root / 'sci-pic' / 'style_integration.py').write_bytes(
            (ROOT / 'sci-pic' / 'style_integration.py').read_bytes())
        (self.root / 'sci-pic' / 'plot_sci_pic.py').write_bytes(
            (ROOT / 'sci-pic' / 'plot_sci_pic.py').read_bytes())
        (self.root / 'plot_style').mkdir(exist_ok=True)
        for source in (ROOT / 'plot_style').glob('*.py'):
            (self.root / 'plot_style' / source.name).write_bytes(source.read_bytes())
        (self.root / 'sci-pic' / 'templates.json').write_text(json.dumps({
            'version': 1,
            'templates': [{'id': 'sci_pic', 'title': 'SCI-Pic', 'workbook': 'templates/sci_pic.xlsx',
                           'preview': 'previews/sci_pic.png', 'plot_type': '柱状折线',
                           'description': '柱状折线', 'example': '示例'}]
        }, ensure_ascii=False), encoding='utf-8')
        self.workbook(self.root / 'sci-pic' / 'templates' / 'sci_pic.xlsx', style=False)
        return self.root

    def workbook(self, path, style=True):
        wb = openpyxl.Workbook()
        data = wb.active
        data.title = '数据'
        for c, title in enumerate(('类别', '数值', '备注说明'), 1):
            data.cell(row=4, column=c, value=title)
        for row, (label, value) in enumerate((('A', 1), ('B', 2)), 5):
            data.cell(row=row, column=1, value=label)
            data.cell(row=row, column=2, value=value)
        config = wb.create_sheet('设置')
        for c, title in enumerate(('参数', '填写值', '说明'), 1):
            config.cell(row=4, column=c, value=title)
        for row, (key, value) in enumerate([
                ('图名', '样式测试'), ('图标题', '样式测试'), ('横轴标题', '类别'),
                ('纵轴标题', '数值'), ('纵轴单位', ''), ('配色', 'academic'),
                ('图片宽度_cm', 16), ('图片高度_cm', 9), ('分辨率_dpi', 600),
                ('小数位数', 2), ('显示数值', '是'), ('标签旋转_度', 0),
                ('图类型', '柱状折线')], 5):
            config.cell(row=row, column=1, value=key)
            config.cell(row=row, column=2, value=value)
        if style:
            sheet = wb.create_sheet('全局设置')
            sheet.append(['参数', '填写值', '说明'])
            sheet.append(['figure_title_font_size_pt', 12, 'pt'])
            sheet.append(['line_width_pt', 2.4, 'pt'])
        wb.save(path)
        wb.close()
        return path

    def test_navigation_adds_same_page_for_sidebar_and_home(self):
        window = Window(self.fixture_root())
        labels = [window.navigation.item(i).text() for i in range(window.navigation.count())]
        self.assertIn('sci-pic    SCI-Pic 学术绘图', labels)
        self.assertEqual(window.stack.count(), 7)
        window.navigation.setCurrentRow(5)
        self.assertIs(window.stack.currentWidget(), window.scipic_page)
        window.state_path = self.root / 'out' / 'gui' / 'state.json'
        window.save_state()
        saved = json.loads(window.state_path.read_text(encoding='utf-8'))
        self.assertIn('sci-pic', saved)
        self.assertEqual(saved['version'], 3)

    def test_style_config_parse_strict_and_resolved_config(self):
        root = self.fixture_root()
        path = self.workbook(root / 'sci-pic' / 'out' / 'style.xlsx')
        source = root / 'sci-pic' / 'out' / 'resolved.json'
        payload = sci_pic.validate_style(root, workbook=path, output=source)
        saved = json.loads(source.read_text(encoding='utf-8'))
        self.assertEqual(saved['project'], 'sci-pic')
        self.assertEqual(saved['units']['font_sizes'], 'pt')
        self.assertEqual(saved['values']['figure_title_font_size_pt']['value'], 12)
        self.assertEqual(saved['values']['figure_title_font_size_pt']['source'], 'excel')
        self.assertEqual(saved['values']['dpi']['source'], 'project')
        wb = openpyxl.load_workbook(path)
        try:
            self.assertNotIn('设置!A18', str(wb['设置']['A18'].value))
            self.assertEqual(wb['全局设置']['A2'].value, 'figure_title_font_size_pt')
        finally:
            wb.close()

    def test_unknown_style_key_is_warning_then_strict_error(self):
        root = self.fixture_root()
        path = self.workbook(root / 'bad.xlsx')
        wb = openpyxl.load_workbook(path)
        try:
            wb['全局设置'].append(['未定义参数', '1', ''])
            wb.save(path)
        finally:
            wb.close()
        with self.assertWarns(UserWarning) as caught:
            sci_pic.validate_style(root, workbook=path)
        self.assertIn('未定义参数', str(caught.warning))
        with self.assertRaisesRegex(ValueError, '未定义参数'):
            sci_pic.validate_style(root, workbook=path, strict=True)

    def test_work_copy_add_style_rows_and_leave_template_untouched(self):
        root = self.fixture_root()
        template = self.workbook(root / 'sci-pic' / 'templates' / 'sci_pic.xlsx', style=False)
        entry = sci_pic.catalog(root)[0]
        before = template.read_bytes()
        work = sci_pic.working_copy(root, entry, target_dir=root / 'sci-pic' / 'out')
        sci_pic.add_style_rows(work, {'figure_title_font_size_pt': 11}, root=root)
        wb = openpyxl.load_workbook(work)
        try:
            sheet = wb['全局设置']
            values = {sheet.cell(row=i, column=1).value: sheet.cell(row=i, column=2).value
                      for i in range(1, sheet.max_row + 1)}
            self.assertEqual(values['figure_title_font_size_pt'], 11)
            self.assertEqual(wb['设置']['A5'].value, '图名')
        finally:
            wb.close()
        self.assertEqual(template.read_bytes(), before)

    def test_prepare_plot_dispatch_uses_style_flags_and_history(self):
        root = self.fixture_root()
        path = self.workbook(root / 'sci-pic' / 'out' / 'style.xlsx')
        entry = sci_pic.catalog(root)[0]
        prepared = sci_pic.prepare_plot(root, entry, path, root / 'sci-pic' / 'out',
                                        style_config=str(path),
                                        style_overrides={'figure_title_font_size_pt': 14})
        args = prepared.command.arguments
        self.assertIn('--style-config', args)
        self.assertIn('--style-strict' if False else '--write-resolved', args)
        resolved = Path(args[args.index('--write-resolved') + 1])
        self.assertTrue(resolved.is_file())
        payload = json.loads(resolved.read_text(encoding='utf-8'))
        self.assertEqual(payload['values']['figure_title_font_size_pt']['value'], 14)
        self.assertEqual(payload['values']['figure_title_font_size_pt']['source'], 'cli')
        self.assertEqual(prepared.record['template_id'], 'sci_pic')
        self.assertTrue((root / 'out' / 'gui' / 'history' / 'sci-pic' / (prepared.record['id'] + '.json')).is_file())

    def test_old_call_keeps_legacy_argument_shape(self):
        root = self.fixture_root()
        path = self.workbook(root / 'sci-pic' / 'out' / 'style.xlsx')
        entry = sci_pic.catalog(root)[0]
        prepared = sci_pic.prepare_plot(root, entry, path, root / 'sci-pic' / 'out')
        self.assertEqual(list(prepared.command.arguments),
                         ['--excel', prepared.command.arguments[1], '--out', prepared.command.arguments[3]])

    def test_gui_page_has_no_autorun_and_all_style_fields(self):
        root = self.fixture_root()
        window = Window(root)
        page = window.scipic_page
        self.assertEqual(page.style_table.rowCount(), len(sci_pic.style_rows()))
        self.assertEqual(page.style_table.horizontalHeaderItem(1).text(), '填写值')
        self.assertTrue(page.run_button.isEnabled())
        self.assertFalse(hasattr(page, 'auto_run_timer'))
        self.assertFalse(page.task)
        self.assertEqual(len(page.entries), 1 if (root / 'sci-pic' / 'templates.json').is_file() else 0)


if __name__ == '__main__':
    unittest.main()

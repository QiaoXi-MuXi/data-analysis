# -*- coding: utf-8 -*-
"""工作区结构一致性：四段式布局、任务摘要与运行计时的确定性部分。

窗口在 offscreen 平台构造，只检查结构、文案和状态，不启动分析进程，
不读写任何真实数据。
"""
import os
from datetime import datetime, timedelta
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
GUI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GUI))
from PySide6.QtWidgets import QApplication  # noqa: E402
from app import Window  # noqa: E402


class WorkspaceLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workspace-layout-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.window = Window(self.root)
        self.addCleanup(self.stop)

    def stop(self):
        # 不调用 close()：模式切换会改配置表路径并标记未保存，关闭会弹出模态确认框。
        self.window.pool.waitForDone(10000)
        self.window.deleteLater()

    def test_every_module_keeps_the_same_four_part_workspace(self):
        from PySide6.QtWidgets import QGroupBox
        for key, page in self.window.pages.items():
            with self.subTest(module=key):
                if key == 'sci-pic':
                    titles = [box.title() for box in page.findChildren(QGroupBox)]
                    for expected in ('01  输入与配置', '02  关键设置', '03  可选样式', '04  运行与结果'):
                        self.assertTrue(any(title.startswith(expected) for title in titles),
                                        f'{expected}: {titles}')
                    self.assertEqual(page.run_button.text(), '开始绘图')
                    continue
                titles = [page.tabs.tabText(index) for index in range(page.tabs.count())]
                self.assertIn('运行设置', titles)
                self.assertIn('02  运行记录', titles)
                self.assertIn('03  分析结果', titles)
                first = titles[0]
                expected = {'Ra', 'Raman'}
                self.assertIn(first, ('01  数据与配置') if key in expected else ('01  输入文件'))
                self.assertEqual(page.run_button.text(), '开始分析')
                self.assertTrue(page.task_caption.text().startswith('本次任务：'))
                self.assertFalse(page.progress.isVisible())

    def test_captions_name_the_mode_and_the_risk_that_matters(self):
        raman = self.window.pages['Raman']
        combo = raman.fields['mode']
        for mode, expected in (('config', '堆叠对比'), ('directory', '单谱扫描'),
                               ('compare', '基准对比'), ('montage', '拼图'),
                               ('batch_display', '分层批次展示')):
            with self.subTest(mode=mode):
                combo.setCurrentIndex(combo.findData(mode))
                self.assertIn(expected, raman.task_caption.text())
        clsm = self.window.pages['CLSM']
        self.assertIn('不清理 out', clsm.task_caption.text())
        self.assertIn('标定', clsm.task_caption.text())
        er = self.window.pages['ER']
        self.assertIn('人工复核', er.task_caption.text())

    def test_elapsed_clock_reports_real_seconds(self):
        page = self.window.pages['Ra']
        page.run_started = datetime.now() - timedelta(seconds=7)
        page.tick_clock()
        self.assertEqual(page.timing_label.text(), '已运行 7 秒')
        page.run_started = None
        page.tick_clock()
        self.assertEqual(page.timing_label.text(), '已运行 7 秒')

    def test_caption_includes_input_total_once_scanned(self):
        raman = self.window.pages['Raman']
        raman.input_total = 3
        raman.refresh_caption()
        self.assertIn('3 份输入', raman.task_caption.text())
        raman.input_total = None
        raman.refresh_caption()
        self.assertNotIn('份输入', raman.task_caption.text())

    def test_sample_table_keeps_readable_column_headers(self):
        page = self.window.pages['Raman']
        self.assertEqual(page.editor.table.horizontalHeader().minimumSectionSize(), 96)

    def test_theme_draws_a_keyboard_focus_ring(self):
        from theme import STYLE
        self.assertIn('QPushButton:focus', STYLE)
        self.assertIn('QPushButton#primary:focus', STYLE)

    def test_ctrl_r_reaches_the_current_page_run_button(self):
        from PySide6.QtGui import QKeySequence
        page = self.window.pages['Raman']
        self.assertEqual(self.window.run_shortcut.key().toString(), QKeySequence('Ctrl+R').toString())
        calls = []
        page.run_button.clicked.connect(lambda: calls.append('run'))
        self.window.stack.setCurrentWidget(page)
        self.window.run_shortcut.activated.emit()
        self.assertEqual(calls, ['run'])

    def test_window_can_shrink_to_common_scaled_screens(self):
        # 1366×768 在 125% 缩放下只有约 1000×600 的逻辑空间，窗口最小尺寸必须容得下。
        self.window.resize(1000, 600)
        self.assertEqual((self.window.width(), self.window.height()), (1000, 600))
        self.assertLessEqual(self.window.minimumWidth(), 1000)
        self.assertLessEqual(self.window.minimumHeight(), 620)
        self.assertGreaterEqual(self.window.minimumWidth(), 900)

    def test_narrow_window_scrolls_instead_of_hiding_the_inspector(self):
        # 真正的像素级行为由 shot_real.py 在真实窗口下截图验证；这里只锁定实现方式，
        # 避免 offscreen 下多次 show() 触发 Qt 崩溃（0xC0000409）。
        page = self.window.pages['Raman']
        body = page.body_scroll
        self.assertTrue(body.widgetResizable())
        self.assertEqual(body.horizontalScrollBarPolicy().name, 'ScrollBarAsNeeded')
        self.assertFalse(body.widget().childrenCollapsible())

    def test_raman_advanced_settings_start_collapsed(self):
        from PySide6.QtWidgets import QToolButton
        raman = self.window.pages['Raman']
        toggles = raman.findChildren(QToolButton, 'advanced_toggle')
        self.assertEqual(len(toggles), 1)
        self.assertFalse(toggles[0].isChecked())
        body = raman.fields['zero_guard'].parentWidget()
        self.assertIsNotNone(body)
        self.assertTrue(body.isHidden())
        toggles[0].setChecked(True)
        self.assertFalse(body.isHidden())
        toggles[0].setChecked(False)
        self.assertTrue(body.isHidden())
        clsm = self.window.pages['CLSM']
        clsm_toggles = clsm.findChildren(QToolButton, 'advanced_toggle')
        self.assertEqual(len(clsm_toggles), 1)
        self.assertFalse(clsm_toggles[0].isChecked())
        self.assertTrue(clsm.fields['scale'].parentWidget().isHidden())


if __name__ == '__main__':
    unittest.main()

# -*- coding: utf-8 -*-
"""滚轮不再误改下拉框 / 数值框；下拉框带独立箭头，长得像下拉而不是输入框。"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'gui'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt, QPoint, QPointF  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QScrollArea, QSpinBox, QVBoxLayout, QWidget  # noqa: E402

from app import WheelGuard  # noqa: E402
from theme import STYLE  # noqa: E402


def wheel(angle=-120, position=(5, 5)):
    point = QPointF(*position)
    return QWheelEvent(point, point, QPoint(0, 0), QPoint(0, angle),
                       Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                       Qt.ScrollPhase.NoScrollPhase, False)


class WheelGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(6)
        for index in range(24):
            layout.addWidget(QLabel(f'占位 {index}'))
        self.combo = QComboBox()
        self.combo.addItems(['启用', '关闭', '默认'])
        layout.addWidget(self.combo)
        self.spin = QSpinBox()
        self.spin.setRange(0, 100)
        self.spin.setValue(5)
        layout.addWidget(self.spin)
        for index in range(24):
            layout.addWidget(QLabel(f'结尾 {index}'))
        self.area = QScrollArea()
        self.area.setWidget(content)
        self.area.resize(320, 240)
        self.guard = WheelGuard(self.area)
        self.application.installEventFilter(self.guard)
        self.addCleanup(self.teardown)

    def teardown(self):
        self.application.removeEventFilter(self.guard)
        self.area.hide()
        self.area.deleteLater()

    def test_wheel_over_combo_changes_nothing_and_scrolls_the_page(self):
        self.area.show()
        self.application.processEvents()
        self.assertEqual(self.combo.currentIndex(), 0)
        self.assertTrue(QApplication.sendEvent(self.combo, wheel(-120)))
        self.assertEqual(self.combo.currentIndex(), 0)
        self.assertGreater(self.area.verticalScrollBar().value(), 0)

    def test_wheel_over_spinbox_keeps_the_value(self):
        self.assertTrue(QApplication.sendEvent(self.spin, wheel(120)))
        self.assertEqual(self.spin.value(), 5)

    def test_combo_looks_like_a_dropdown(self):
        self.assertIn('QComboBox::down-arrow', STYLE)
        self.assertIn('chevron-down.svg', STYLE.replace('\\', '/'))
        self.assertIn('QComboBox::drop-down', STYLE)


if __name__ == '__main__':
    unittest.main()

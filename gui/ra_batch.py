# -*- coding: utf-8 -*-
"""Ra 原生批量配置：明确目标范围，勾选字段后一次应用。"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QFont, QBrush
from PySide6.QtWidgets import (QWidget, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QComboBox, QCheckBox, QLineEdit, QPushButton, QColorDialog,
    QDialogButtonBox, QSpinBox, QGroupBox, QScrollArea)

from documents import SCHEMAS, parse_value

LABEL_KEYS = ('label_enabled', 'label_size', 'label_color', 'label_position', 'label_stroke', 'label_stroke_color')
IMAGE_SUFFIXES = {'.bmp', '.png', '.jpg', '.jpeg'}


def target_rows(rows, scope, selected=()):
    """筛选配置行，不读取/改名/移动源文件；扩展名大小写无关。"""
    selection = set(selected)
    result = []
    for index, row in enumerate(rows):
        suffix = Path(str(row.get('file') or '')).suffix.casefold()
        if (scope == 'all' or (scope == 'selected' and index in selection)
                or (scope == 'images' and suffix in IMAGE_SUFFIXES) or suffix == scope):
            result.append(index)
    return result


def modified_rows(rows, indexes, changes):
    """先完整校验，再拷贝应用；取消和无效输入不会产生半份修改。"""
    schema = {c.key: c for c in SCHEMAS['Ra']}
    parsed = {key: parse_value(schema[key], value) for key, value in changes.items()}
    result = deepcopy(rows)
    for index in indexes:
        result[index].update(parsed)
    return result


class ColorField(QWidget):
    editingFinished = Signal()
    valueChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.line = QLineEdit()
        self.line.setPlaceholderText('默认 / #RRGGBB')
        self.line.editingFinished.connect(self.editingFinished)
        self.line.textChanged.connect(self.refresh)
        self.line.textChanged.connect(self.valueChanged)
        layout.addWidget(self.line, 1)
        self.button = QPushButton('选色')
        self.button.setToolTip('打开颜色选择器；清空色号表示默认')
        self.button.clicked.connect(self.choose)
        layout.addWidget(self.button)
        self.setFocusProxy(self.line)

    def text(self):
        return self.line.text()

    def setText(self, value):
        self.line.setText(value)
        self.refresh()

    def refresh(self):
        color = QColor(self.text())
        self.button.setStyleSheet(f'border:2px solid {color.name()};' if color.isValid() else '')

    def choose(self):
        initial = QColor(self.text())
        color = QColorDialog.getColor(initial if initial.isValid() else QColor('#FFFFFF'), self, '选择标注颜色')
        if color.isValid():
            self.setText(color.name().upper())
            self.editingFinished.emit()


class LabelPreview(QWidget):
    """仅用示意值展示样式，不把示意值保存到配置或输出。"""
    def __init__(self):
        super().__init__()
        self.style = {}
        self.sample_text = 'Ra=12.345 nm'
        self.setMinimumHeight(140)
        self.setMaximumHeight(150)
        self.setToolTip('示意文字和背景；实际数值由本次分析产生，字号按输出像素计。')

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor('#18251F'))
        painter.setPen(QPen(QColor('#30443A'), 1))
        for x in range(0, self.width(), 28):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), 28):
            painter.drawLine(0, y, self.width(), y)
        style = self.style
        if style.get('label_enabled') is False:
            painter.setPen(QColor('#B7C6BD'))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, '附加标注已关闭')
            return
        # 602×325 是实际独立图尺寸，等比显示字号与边距。
        scale = min(self.width()/602, self.height()/325)
        painter.translate((self.width()-602*scale)/2, (self.height()-325*scale)/2)
        painter.scale(scale, scale)
        font = QFont('Microsoft YaHei')
        font.setPixelSize(style.get('label_size') or 44)
        font.setBold(True)
        path = QPainterPath()
        path.addText(0, 0, font, self.sample_text)
        box = path.boundingRect()
        position = style.get('label_position') or '左上'
        if position.startswith('右'):
            x = 602-10-box.right()
        elif '中' in position:
            x = (602-box.width())/2-box.left()
        else:
            x = 10-box.left()
        y = 325-10-box.bottom() if position.endswith('下') else 10-box.top()
        path.translate(x, y)
        stroke = style.get('label_stroke')
        stroke = 3 if stroke is None else stroke
        if stroke:
            painter.strokePath(path, QPen(QColor(style.get('label_stroke_color') or '#000000'), 2*stroke))
        painter.fillPath(path, QBrush(QColor(style.get('label_color') or '#FFFFFF')))


class RaBatchDialog(QDialog):
    def __init__(self, rows, selected=(), parent=None):
        super().__init__(parent)
        self.rows = deepcopy(rows)
        self.selection = tuple(selected)
        self.result_rows = None
        self.applied_count = 0
        self.inputs, self.checks = {}, {}
        self.setWindowTitle('Ra · 一键配置')
        self.resize(700, 740)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        heading = QLabel('按后缀统一配置')
        heading.setObjectName('sectionTitle')
        layout.addWidget(heading)
        note = QLabel('选择范围，勾选要修改的参数后应用。包含停用行；搜索筛选不影响后缀范围。')
        note.setWordWrap(True)
        note.setObjectName('muted')
        layout.addWidget(note)
        target = QHBoxLayout()
        target.addWidget(QLabel('应用范围'))
        self.scope = QComboBox()
        for title, value in [(f'仅所选行（{len(selected)}）', 'selected'), ('全部 .bmp', '.bmp'), ('全部 .dat', '.dat'),
                             ('全部图片（BMP / PNG / JPG）', 'images'), ('全部样本', 'all')]:
            self.scope.addItem(title, value)
        self.scope.setCurrentIndex(0 if len(selected) > 1 else 1)
        self.scope.currentIndexChanged.connect(self.refresh)
        target.addWidget(self.scope, 1)
        self.count = QLabel()
        target.addWidget(self.count)
        layout.addLayout(target)
        presets = QHBoxLayout()
        for title, preset in [('常用标注样式', 'standard'), ('关闭附加标注', 'off'), ('恢复标注默认', 'defaults')]:
            button = QPushButton(title)
            button.clicked.connect(lambda _, value=preset: self.preset(value))
            presets.addWidget(button)
        layout.addLayout(presets)
        group = QGroupBox('只修改勾选项')
        grid = QGridLayout(group)
        columns = [c for c in SCHEMAS['Ra'] if c.key in LABEL_KEYS]
        columns += [c for c in SCHEMAS['Ra'] if c.key not in (*LABEL_KEYS, 'file', 'stem')]
        for index, column in enumerate(columns):
            check = QCheckBox(column.title)
            check.toggled.connect(self.refresh)
            self.checks[column.key] = check
            grid.addWidget(check, index, 0)
            if column.kind in ('enabled', 'bool', 'choice') or column.key == 'label_stroke':
                field = QComboBox()
                field.addItem('默认', None)
                choices = [(str(i), i) for i in range(9)] if column.key == 'label_stroke' else (
                    [(v, v) for v in column.choices] if column.kind == 'choice' else [('启用', True), ('关闭', False)])
                for title, value in choices:
                    field.addItem(title, value)
                field.currentIndexChanged.connect(self.refresh)
            elif column.kind == 'font_size':
                field = QSpinBox()
                field.setRange(7, 96)
                field.setSpecialValueText('默认（44 px）')
                field.setValue(7)
                field.valueChanged.connect(self.refresh)
            elif column.kind == 'color':
                field = ColorField()
                field.valueChanged.connect(self.refresh)
            else:
                field = QLineEdit()
                field.setPlaceholderText('留空使用默认值')
                field.textChanged.connect(self.refresh)
            field.setObjectName('bulk_' + column.key)
            self.inputs[column.key] = field
            grid.addWidget(field, index, 1)
        grid.setColumnStretch(1, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(group)
        scroll.setMinimumHeight(220)
        layout.addWidget(scroll, 1)
        self.preview = LabelPreview()
        layout.addWidget(self.preview)
        caption = QLabel('样式示意；BMP 标注截图 Ra，DAT 标注处理后面的 Sa，四联图同步。仅在“样本配置”模式运行时使用本表。')
        caption.setWordWrap(True)
        caption.setObjectName('muted')
        layout.addWidget(caption)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet('color:#AA5D43;')
        layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = self.buttons.button(QDialogButtonBox.StandardButton.Apply)
        self.apply_button.clicked.connect(self.apply_changes)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        layout.addWidget(self.buttons)
        self.refresh()

    def value(self, key):
        field = self.inputs[key]
        if isinstance(field, QComboBox):
            return field.currentData()
        if isinstance(field, QSpinBox):
            return None if field.value() == 7 else field.value()
        return field.text()

    def preset(self, name):
        values = dict(label_enabled=True, label_size=32, label_color='#FFFFFF', label_position='左上',
                      label_stroke=2, label_stroke_color='#000000') if name == 'standard' else (
            {'label_enabled': False} if name == 'off' else {key: None for key in LABEL_KEYS})
        for key in LABEL_KEYS:
            self.checks[key].setChecked(key in values)
        for key, value in values.items():
            self.checks[key].setChecked(True)
            field = self.inputs[key]
            if isinstance(field, QComboBox):
                field.setCurrentIndex(field.findData(value))
            elif isinstance(field, QSpinBox):
                field.setValue(7 if value is None else value)
            else:
                field.setText('' if value is None else str(value))
        # 预设只作用于标注，不勾选计算参数。
        self.refresh()

    def changes(self):
        schema = {c.key: c for c in SCHEMAS['Ra']}
        return {key: parse_value(schema[key], self.value(key)) for key, check in self.checks.items() if check.isChecked()}

    def refresh(self):
        if not hasattr(self, 'buttons'):
            return
        indexes = target_rows(self.rows, self.scope.currentData(), self.selection)
        self.count.setText(f'{len(indexes)} 行')
        try:
            changes = self.changes()
            message = ''
        except ValueError as exc:
            changes, message = {}, str(exc)
        for key, field in self.inputs.items():
            field.setEnabled(self.checks[key].isChecked())
        self.error.setText(message)
        self.apply_button.setText(f'应用到 {len(indexes)} 行')
        self.apply_button.setEnabled(bool(indexes and changes and not message))
        example = dict(self.rows[indexes[0]]) if indexes else {}
        example.update(changes)
        schema = {c.key: c for c in SCHEMAS['Ra']}
        for key in LABEL_KEYS:
            try:
                example[key] = parse_value(schema[key], example.get(key))
            except ValueError:
                example[key] = None
        if example.get('label_enabled') is None:
            example['label_enabled'] = Path(str(example.get('file') or '')).suffix.casefold() in IMAGE_SUFFIXES
        self.preview.style = example
        self.preview.sample_text = 'Sa=12.345 nm' if Path(str(example.get('file') or '')).suffix.casefold() == '.dat' else 'Ra=12.345 nm'
        self.preview.update()

    def apply_changes(self):
        indexes = target_rows(self.rows, self.scope.currentData(), self.selection)
        try:
            changes = self.changes()
            if not indexes or not changes:
                return
            self.result_rows = modified_rows(self.rows, indexes, changes)
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.applied_count = len(indexes)
        self.accept()

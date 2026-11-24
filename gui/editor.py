# -*- coding: utf-8 -*-
"""原生样本表与参数面板，共用一份文档及撤销记录。"""
from __future__ import annotations

from copy import deepcopy
import csv
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal, Slot, QTimer
from PySide6.QtGui import QColor, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QComboBox, QTableView, QHeaderView, QAbstractItemView, QSplitter,
    QScrollArea, QFrame, QFormLayout, QFileDialog, QMessageBox, QMenu,
    QStyledItemDelegate, QCheckBox, QInputDialog, QListWidget, QListWidgetItem)

from documents import SCHEMAS, Document, Problem, readiness, flag, parse_value, read_exchange, resolve_source, append_sources, export_csv, export_xlsx
from ra_batch import ColorField, RaBatchDialog


def text(value, name=None):
    widget = QLabel(value)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    return widget


def action(value, callback, name=None):
    widget = QPushButton(value)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if name:
        widget.setObjectName(name)
    widget.clicked.connect(callback)
    return widget


class EditRows(QUndoCommand):
    def __init__(self, model, rows, title):
        super().__init__(title)
        self.model, self.before, self.after = model, deepcopy(model.document.rows), deepcopy(rows)

    def undo(self):
        self.model.apply(self.before)

    def redo(self):
        self.model.apply(self.after)


class SampleModel(QAbstractTableModel):
    changed = Signal()
    invalid = Signal(str)

    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.document = document
        self.stack = QUndoStack(self)
        self.stack.setUndoLimit(60)
        self.columns = ['enabled', 'file', 'stem', 'high'] if document.module == 'Ra' else ['enabled', 'file', 'label', 'normalize', 'height']
        self.columns.append('_status')
        self.problems = {}
        self.checking = True
        self.schema = {c.key: c for c in SCHEMAS[document.module]}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.document.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            if orientation == Qt.Orientation.Vertical:
                return f'{section+1:02}'
            return '检查状态' if self.columns[section] == '_status' else self.schema[self.columns[section]].title

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.document.rows[index.row()]
        key = self.columns[index.column()]
        value = row.get(key)
        problems = self.problems.get(index.row()+1, [])
        if key == '_status':
            if role == Qt.ItemDataRole.ToolTipRole:
                return '输入检查中，运行前还会复核' if self.checking else '\n'.join(p.message for p in problems) or '本行参数与文件检查通过'
            if role == Qt.ItemDataRole.DisplayRole:
                if self.checking:
                    return '待检查'
                if problems:
                    return '需修正' if any(p.blocks_save for p in problems) else '文件缺失'
                try:
                    return '可用' if flag(row.get('enabled'), True) else '已停用'
                except ValueError:
                    return '需修正'
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor('#aa5d43' if problems else '#758c71')
            return None
        if role == Qt.ItemDataRole.ToolTipRole and problems:
            return '\n'.join(p.message for p in problems)
        if role == Qt.ItemDataRole.CheckStateRole and key == 'enabled':
            try:
                return Qt.CheckState.Checked if flag(value, True) else Qt.CheckState.Unchecked
            except ValueError:
                return Qt.CheckState.PartiallyChecked
        if role == Qt.ItemDataRole.EditRole:
            return value
        if role == Qt.ItemDataRole.DisplayRole:
            if key == 'enabled':
                return ''
            if self.schema[key].kind == 'bool':
                return '默认' if value is None else '启用' if value is True else '关闭' if value is False else str(value)
            if key == 'file':
                return Path(str(value or '')).name
            return '默认' if value is None else str(value)
        if role == Qt.ItemDataRole.ToolTipRole:
            if key == 'file':
                try:
                    path = resolve_source(self.document, row)
                    return str(path) + ('' if path.is_file() else '\n文件缺失，可修改路径或停用此行')
                except (ValueError, OSError) as exc:
                    return str(exc)
            return self.schema[key].title + '：' + (str(value) if value is not None else '沿用原模块默认值')
        if role == Qt.ItemDataRole.ForegroundRole:
            try:
                parse_value(self.schema[key], value)
                if not flag(row.get('enabled'), True):
                    return QColor('#929e96')
                if key == 'file' and not resolve_source(self.document, row).is_file():
                    return QColor('#a96a38')
            except (ValueError, OSError):
                return QColor('#aa5d43')
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = super().flags(index)
        if self.columns[index.column()] == '_status':
            return flags
        return flags | (Qt.ItemFlag.ItemIsUserCheckable if self.columns[index.column()] == 'enabled' else Qt.ItemFlag.ItemIsEditable)

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid():
            return False
        if role == Qt.ItemDataRole.CheckStateRole:
            value = value == Qt.CheckState.Checked or value == Qt.CheckState.Checked.value
        return self.set_value(index.row(), self.columns[index.column()], value)

    def set_value(self, row, key, value):
        error = None
        try:
            parsed = parse_value(self.schema[key], value)
        except ValueError as exc:
            # 保留无效草稿供修正；保存和运行由文档校验拦截，不能悄悄使用旧值。
            parsed, error = value, str(exc)
        if self.document.rows[row].get(key) == parsed:
            if error:
                self.invalid.emit(error)
            return True
        rows = deepcopy(self.document.rows)
        rows[row][key] = parsed
        self.stack.push(EditRows(self, rows, '修改' + self.schema[key].title))
        if error:
            self.invalid.emit(error)
        return True

    def apply(self, rows):
        self.beginResetModel()
        self.document.rows = deepcopy(rows)
        self.endResetModel()
        self.changed.emit()

    def set_problems(self, problems, *, checking=False):
        self.problems = {}
        self.checking = checking
        for problem in problems:
            if problem.row is not None:
                self.problems.setdefault(problem.row, []).append(problem)
        if self.rowCount():
            self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount()-1, self.columnCount()-1))


class TypedDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        column = index.model().schema[index.model().columns[index.column()]]
        if column.kind in ('choice', 'bool'):
            editor = QComboBox(parent)
            editor.addItem('默认', None)
            for title, value in ([(x, x) for x in column.choices] if column.kind == 'choice' else [('启用', True), ('关闭', False)]):
                editor.addItem(title, value)
            return editor
        return QLineEdit(parent)

    def setEditorData(self, editor, index):
        value = index.data(Qt.ItemDataRole.EditRole)
        if isinstance(editor, QComboBox):
            editor.setCurrentIndex(max(0, editor.findData(value)))
        else:
            # 文件名列显示短名称，编辑时保留完整原路径。
            editor.setText('' if value is None else str(value))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentData() if isinstance(editor, QComboBox) else editor.text())


class ConfigEditor(QWidget):
    changed = Signal()
    imported = Signal(object)
    save_requested = Signal()
    save_as_requested = Signal()

    def __init__(self, document, parent=None, *, submit=None):
        super().__init__(parent)
        self.document = document
        self.pending_import = False
        self.selected_row = 0
        self.inputs = {}
        self.updating = False
        self.submit = submit
        self.revision = 0
        self.problems = []
        self.validation_timer = QTimer(self)
        self.validation_timer.setSingleShot(True)
        self.validation_timer.setInterval(350)
        self.validation_timer.timeout.connect(self.check_inputs)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(13)
        source = QHBoxLayout()
        self.source_label = text('', 'dataset')
        source.addWidget(self.source_label, 1)
        self.counter = text('', 'muted')
        source.addWidget(self.counter)
        layout.addLayout(source)
        check = QHBoxLayout()
        self.readiness_label = text('正在检查样本…', 'muted')
        check.addWidget(self.readiness_label, 1)
        self.problems_button = action('查看问题', self.toggle_problems)
        self.problems_button.setEnabled(False)
        check.addWidget(self.problems_button)
        check.addWidget(action('重新检查', self.check_inputs))
        layout.addLayout(check)
        self.problems_list = QListWidget()
        self.problems_list.setObjectName('problems')
        self.problems_list.setMaximumHeight(106)
        self.problems_list.itemClicked.connect(self.focus_problem)
        self.problems_list.hide()
        layout.addWidget(self.problems_list)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(14)
        card = QFrame()
        card.setObjectName('dataCard')
        center = QVBoxLayout(card)
        center.setContentsMargins(16, 16, 16, 12)
        top = QHBoxLayout()
        title = QVBoxLayout()
        title.addWidget(text('样本与配置', 'sectionTitle'))
        hint = text('选择样本，在右侧调整参数；双击表格可直接编辑', 'muted')
        hint.setWordWrap(True)
        title.addWidget(hint)
        top.addLayout(title, 1)
        if document.module == 'Ra':
            top.addWidget(action('一键配置', self.quick_config, 'primarySmall'))
        top.addWidget(action('添加文件', self.add_files))
        top.addWidget(action('导入配置', self.import_file))
        export = QPushButton('导出 / 另存为')
        menu = QMenu(export)
        menu.addAction('样本参数 CSV…', self.export_table)
        if document.module == 'Ra':
            menu.addAction('Excel 配置（含标注设置）…', self.export_excel)
        menu.addAction('项目另存为 JSON…', self.save_as_requested.emit)
        export.setMenu(menu)
        top.addWidget(export)
        center.addLayout(top)
        tools = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索文件、输出名或分组')
        self.search.textChanged.connect(self.filter_rows)
        tools.addWidget(self.search, 1)
        self.only_enabled = QCheckBox('仅看启用')
        self.only_enabled.toggled.connect(self.filter_rows)
        tools.addWidget(self.only_enabled)
        center.addLayout(tools)
        self.table = QTableView()
        self.table.setObjectName('samples')
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(49)
        self.table.verticalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItemDelegate(TypedDelegate(self.table))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed | QAbstractItemView.EditTrigger.SelectedClicked)
        center.addWidget(self.table, 1)
        bottom = QHBoxLayout()
        self.undo_button = action('撤销', lambda: self.model.stack.undo())
        self.redo_button = action('重做', lambda: self.model.stack.redo())
        for widget in (self.undo_button, self.redo_button, action('批量设置', self.batch_edit), action('上移', lambda: self.move(-1)), action('下移', lambda: self.move(1)), action('移除行', self.remove_rows)):
            bottom.addWidget(widget)
        bottom.addStretch()
        center.addLayout(bottom)
        self.message = text('原始输入只读；导入不会改写来源 Excel。', 'muted')
        self.message.setWordWrap(True)
        center.addWidget(self.message)
        split.addWidget(card)
        inspector_scroll = QScrollArea()
        self.inspector_scroll = inspector_scroll
        inspector_scroll.setWidgetResizable(True)
        inspector_scroll.setMinimumWidth(250)
        inspector_scroll.setMaximumWidth(355)
        inspector = QFrame()
        inspector.setObjectName('inspector')
        self.inspector_layout = QVBoxLayout(inspector)
        self.inspector_layout.setContentsMargins(20, 20, 20, 18)
        self.inspector_layout.setSpacing(14)
        self.inspector_layout.addWidget(text('SAMPLE INSPECTOR', 'eyebrow'))
        self.filename = text('选择一个样本', 'sectionTitle')
        self.filename.setWordWrap(True)
        self.inspector_layout.addWidget(self.filename)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setVerticalSpacing(12)
        for column in SCHEMAS[document.module]:
            if column.kind in ('enabled', 'bool', 'choice'):
                widget = QComboBox()
                widget.addItem('默认（沿用原模块规则）', None)
                for title, value in ([(x, x) for x in column.choices] if column.kind == 'choice' else [('启用', True), ('关闭', False)]):
                    widget.addItem(title, value)
                widget.currentIndexChanged.connect(lambda _, key=column.key: self.commit(key))
            elif column.kind == 'color':
                widget = ColorField()
                widget.editingFinished.connect(lambda key=column.key: self.commit(key))
            else:
                widget = QLineEdit()
                widget.setPlaceholderText('留空沿用原默认值')
                widget.editingFinished.connect(lambda key=column.key: self.commit(key))
            if column.key == 'label_enabled':
                widget.setItemText(0, '默认（图片启用 / DAT 关闭）')
            if column.key == 'label_size':
                widget.setPlaceholderText('8–96；默认 44 px')
            if column.key == 'label_stroke':
                widget.setPlaceholderText('0–8；默认 3 px，0 无描边')
            if column.key == 'label_position':
                widget.setItemText(0, '默认（左上）')
            widget.setObjectName('sample_' + column.key)
            self.inputs[column.key] = widget
            if column.key == 'file':
                field = QWidget()
                field_layout = QHBoxLayout(field)
                field_layout.setContentsMargins(0, 0, 0, 0)
                field_layout.addWidget(widget, 1)
                self.browse_button = action('选择…', self.choose_source)
                field_layout.addWidget(self.browse_button)
                form.addRow(column.title, field)
            else:
                form.addRow(column.title, widget)
        self.inspector_layout.addLayout(form)
        note = text('空白保留原模块默认值。标注只改变出图，BMP显示截图Ra，DAT显示处理后面的Sa；DAT四联图与独立图同步。候选去噪、高通及归一化的适用性需独立核验。'
                    if document.module == 'Ra' else '空白保留原模块默认值。候选去噪、高通及归一化的适用性需独立核验。', 'methodNote')
        note.setWordWrap(True)
        self.inspector_layout.addWidget(note)
        self.inspector_layout.addStretch()
        inspector_scroll.setWidget(inspector)
        split.addWidget(inspector_scroll)
        split.setStretchFactor(0, 1)
        split.setSizes([740, 280])
        layout.addWidget(split, 1)
        self.install_model()

    def install_model(self):
        old = getattr(self, 'model', None)
        self.model = SampleModel(self.document, self)
        self.table.setModel(self.model)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 65)
        for col in range(2, self.model.columnCount()):
            self.table.setColumnWidth(col, 145 if col == 2 else 125)
        self.table.selectionModel().currentRowChanged.connect(self.selected)
        self.model.changed.connect(self.updated)
        self.model.invalid.connect(self.show_error)
        self.model.stack.cleanChanged.connect(self.clean_changed)
        self.model.stack.canUndoChanged.connect(self.undo_button.setEnabled)
        self.model.stack.canRedoChanged.connect(self.redo_button.setEnabled)
        self.undo_button.setEnabled(False)
        self.redo_button.setEnabled(False)
        if old:
            old.deleteLater()
        self.updated()

    @Slot(bool)
    def clean_changed(self, clean):
        self.changed.emit()

    def dirty(self):
        return self.pending_import or not self.model.stack.isClean()

    def mark_saved(self):
        self.pending_import = False
        self.model.stack.setClean()
        self.updated()

    def show_error(self, message):
        self.message.setText(message)
        self.message.setStyleSheet('color:#a75a3d;')

    def updated(self):
        self.message.setStyleSheet('')
        self.message.setText('修改尚未保存到项目。' if self.dirty() else '原始输入只读；导入不会改写来源 Excel。')
        self.source_label.setText(self.document.path.name if self.document.path else self.document.metadata.get('imported_from', '新项目配置'))
        self.source_label.setToolTip(str(self.document.path or self.document.base) + ('\n当前为导入快照；Excel 修改后需重新导入。' if not self.document.path else ''))
        enabled = 0
        for row in self.document.rows:
            try:
                enabled += flag(row.get('enabled'), True)
            except ValueError:
                pass
        self.counter.setText(f'{len(self.document.rows)} 份样本  /  {enabled} 份启用')
        self.selected_row = min(self.selected_row, max(0, len(self.document.rows)-1))
        self.filter_rows()
        if self.document.rows:
            self.table.setCurrentIndex(self.model.index(self.selected_row, 1))
        self.fill_inspector()
        self.schedule_validation()
        self.changed.emit()

    def schedule_validation(self):
        self.revision += 1
        self.model.set_problems([], checking=True)
        self.readiness_label.setText('配置已变化，正在检查…')
        self.problems_button.setEnabled(False)
        self.validation_timer.start()

    def check_inputs(self):
        self.validation_timer.stop()
        self.revision += 1
        token = self.revision
        document = deepcopy(self.document)
        self.model.set_problems([], checking=True)
        self.readiness_label.setText('正在检查参数与源文件…')
        def done(problems, error):
            if token != self.revision:
                return
            self.problems = problems if not error else [Problem(None, None, '检查未完成：' + error)]
            self.model.set_problems(self.problems)
            self.problems_list.clear()
            for problem in self.problems[:100]:
                item = QListWidgetItem(problem.description)
                item.setData(Qt.ItemDataRole.UserRole, problem)
                item.setToolTip(problem.description)
                self.problems_list.addItem(item)
            count = len(self.problems)
            self.readiness_label.setText(f'发现 {count} 项问题；' + ('显示前100项，修正后重新检查' if count > 100 else '点击条目定位样本') if count else '输入检查通过；运行前将再次复核')
            self.readiness_label.setStyleSheet('color:#a66b3e;' if count else 'color:#527947;')
            self.problems_button.setText(f'查看问题 ({count})' if count else '暂无问题')
            self.problems_button.setEnabled(bool(count))
            self.problems_list.setVisible(bool(count))
        if self.submit:
            self.submit(lambda: readiness(document), done)
        else:
            try:
                done(readiness(document), None)
            except (ValueError, TypeError, OSError) as exc:
                done(None, str(exc))

    def toggle_problems(self):
        self.problems_list.setVisible(not self.problems_list.isVisible())

    def focus_problem(self, item):
        problem = item.data(Qt.ItemDataRole.UserRole)
        if problem.row is None:
            self.show_error(problem.message)
            return
        self.search.clear()
        self.only_enabled.setChecked(False)
        self.selected_row = problem.row - 1
        index = self.model.index(self.selected_row, 1)
        self.table.setCurrentIndex(index)
        self.table.scrollTo(index)
        field = self.inputs.get(problem.key)
        if field and field.isEnabled():
            self.inspector_scroll.ensureWidgetVisible(field)
            field.setFocus()
        self.show_error(problem.description)

    def choose_source(self):
        if not self.document.rows:
            return
        pattern = '表面数据 (*.dat *.bmp *.png *.jpg *.jpeg)' if self.document.module == 'Ra' else '光谱 (*.txt)'
        path, _ = QFileDialog.getOpenFileName(self, '重新选择源文件（保留本行参数）', str(self.document.base), pattern)
        if path:
            self.model.set_value(self.selected_row, 'file', str(Path(path).resolve()))

    def filter_rows(self):
        query = self.search.text().strip().casefold()
        for index, row in enumerate(self.document.rows):
            try:
                enabled = flag(row.get('enabled'), True)
            except ValueError:
                enabled = True
            matched = not query or any(query in str(row.get(key, '')).casefold() for key in ('file', 'stem', 'label', 'region'))
            self.table.setRowHidden(index, not matched or (self.only_enabled.isChecked() and not enabled))

    def selected(self, current, previous):
        if current.isValid():
            self.selected_row = current.row()
            self.fill_inspector()

    def fill_inspector(self):
        self.updating = True
        row = self.document.rows[self.selected_row] if self.document.rows else {}
        self.browse_button.setEnabled(bool(row))
        self.filename.setText(Path(str(row.get('file') or '选择一个样本')).name)
        for key, widget in self.inputs.items():
            value = row.get(key)
            widget.setEnabled(bool(row))
            if isinstance(widget, QComboBox):
                index = widget.findData(value)
                if index < 0:
                    widget.addItem('待修正：' + str(value), value)
                    index = widget.count()-1
                widget.setCurrentIndex(index)
            else:
                widget.setText('' if value is None else str(value))
            if self.document.module == 'Ra' and key in ('noise', 'high', 'cutoff', 'combined'):
                applicable = bool(row) and Path(str(row.get('file') or '')).suffix.casefold() == '.dat'
                invalid = False
                try:
                    parse_value(self.model.schema[key], value)
                    invalid = isinstance(value, str) and value.startswith('=')
                except ValueError:
                    invalid = True
                # 旧表中的非法DAT参数也会被原引擎拦截，必须允许在图像行修正。
                widget.setEnabled(bool(row) and (applicable or invalid))
                widget.setToolTip('原值格式无效，请修正或清空；此参数对图像不生效。' if invalid and not applicable else
                                  '此参数仅对 DAT 生效；图像行中的原值保留。' if not applicable else '')
        self.updating = False

    def commit(self, key):
        if self.updating or not self.document.rows:
            return
        widget = self.inputs[key]
        value = widget.currentData() if isinstance(widget, QComboBox) else widget.text()
        self.model.set_value(self.selected_row, key, value)

    def add_files(self):
        pattern = '表面数据 (*.dat *.bmp *.png *.jpg *.jpeg)' if self.document.module == 'Ra' else '光谱 (*.txt)'
        paths, _ = QFileDialog.getOpenFileNames(self, '添加样本（保留已有参数）', str(self.document.base), pattern)
        if paths:
            self.add_paths(paths)

    def add_paths(self, paths):
        added = append_sources(self.document, paths)
        if added:
            self.selected_row = len(self.document.rows)
            self.model.stack.push(EditRows(self.model, self.document.rows + added, '追加样本'))
        else:
            self.message.setText('没有新文件；已有样本及其参数保持不变。')
        return len(added)

    def remove_rows(self):
        indexes = {i.row() for i in self.table.selectionModel().selectedRows()}
        if not indexes:
            return
        if QMessageBox.question(self, '移除配置行', f'从本项目移除 {len(indexes)} 行配置？原始文件不会删除，可撤销。',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.model.stack.push(EditRows(self.model, [row for i, row in enumerate(self.document.rows) if i not in indexes], '移除配置行'))

    def quick_config(self):
        selected = [index.row() for index in self.table.selectionModel().selectedRows()]
        dialog = RaBatchDialog(self.document.rows, selected, self)
        if dialog.exec() == RaBatchDialog.DialogCode.Accepted and dialog.result_rows != self.document.rows:
            self.model.stack.push(EditRows(self.model, dialog.result_rows, f'一键配置 {dialog.applied_count} 行'))
            self.message.setText(f'已应用到 {dialog.applied_count} 行，支持撤销；保存项目后生效于下次运行。')
        dialog.deleteLater()

    def batch_edit(self):
        if self.document.module == 'Ra':
            self.quick_config()
            return
        indexes = {i.row() for i in self.table.selectionModel().selectedRows()}
        if not indexes:
            self.show_error('请先选择一行或多行；按 Ctrl / Shift 可多选。')
            return
        columns = [c for c in SCHEMAS[self.document.module] if c.key not in ('file', 'stem', 'label')]
        title, accepted = QInputDialog.getItem(self, '批量设置', f'修改所选 {len(indexes)} 行的参数：', [c.title for c in columns], 0, False)
        if not accepted:
            return
        column = next(c for c in columns if c.title == title)
        if column.kind in ('enabled', 'bool', 'choice'):
            choices = ['默认', *column.choices] if column.kind == 'choice' else ['默认', '启用', '关闭']
            value, accepted = QInputDialog.getItem(self, '批量设置 · ' + title, '应用值：', choices, 0, False)
            value = None if value == '默认' else value
        else:
            value, accepted = QInputDialog.getText(self, '批量设置 · ' + title, '应用值（留空沿用默认）：')
        if accepted:
            try:
                parsed = parse_value(column, value)
                rows = deepcopy(self.document.rows)
                for index in indexes:
                    rows[index][column.key] = parsed
                self.model.stack.push(EditRows(self.model, rows, '批量修改' + title))
            except ValueError as exc:
                self.show_error(str(exc))

    def move(self, offset):
        indexes = self.table.selectionModel().selectedRows()
        if len(indexes) != 1:
            self.show_error('调整顺序时请选择一行。')
            return
        index = indexes[0].row()
        other = index + offset
        if 0 <= other < len(self.document.rows):
            rows = deepcopy(self.document.rows)
            rows[index], rows[other] = rows[other], rows[index]
            self.selected_row = other
            self.model.stack.push(EditRows(self.model, rows, '调整样本顺序'))

    def import_file(self):
        path, _ = QFileDialog.getOpenFileName(self, '打开项目 / 导入样本配置', str(self.document.base), '配置 (*.json *.xlsx *.csv)')
        if not path:
            return
        try:
            doc = read_exchange(path, self.document.module)
            if hasattr(self, 'can_replace'):
                if not self.can_replace():
                    return
            elif self.dirty():
                if QMessageBox.question(self, '替换当前配置', '当前有未保存的样本修改。导入将替换列表，原始文件不会改变。是否继续？',
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                    return
            if not doc.options:
                doc.options = deepcopy(self.document.options)
            self.document = doc
            self.pending_import = doc.path is None
            self.selected_row = 0
            self.install_model()
            self.imported.emit(doc)
        except Exception as exc:
            self.show_error('导入失败：' + str(exc))

    def export_table(self):
        path, _ = QFileDialog.getSaveFileName(self, '导出样本参数（新文件）', str(self.document.base / 'samples_export.csv'), 'CSV (*.csv)')
        if path:
            try:
                export_csv(self.document, path)
                self.message.setText('已导出 CSV；完整项目请保存为 JSON。')
            except (OSError, ValueError) as exc:
                self.show_error('导出失败：' + str(exc))


    def export_excel(self):
        path, _ = QFileDialog.getSaveFileName(self, '导出 Excel 配置（新文件）', str(self.document.base / 'ra_config_export.xlsx'), 'Excel (*.xlsx)')
        if path:
            try:
                export_xlsx(self.document, path)
                self.message.setText('已导出 Excel 配置（含停用行及标注列）；修改保存后可重新导入 GUI，或通过 --config 交给命令行运行。')
            except (OSError, ValueError) as exc:
                self.show_error('导出失败：' + str(exc))

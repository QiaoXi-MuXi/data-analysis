# -*- coding: utf-8 -*-
"""Data Analysis 本地桌面工作台。"""
from __future__ import annotations

import argparse
import codecs
import csv
import io
from itertools import islice
from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import traceback
import uuid

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True

from PySide6.QtCore import (QObject, QRunnable, QThreadPool, QProcess, QProcessEnvironment,
                            Qt, Signal, Slot, QTimer, QUrl, QSize)
from PySide6.QtGui import QDesktopServices, QImageReader, QPixmap, QFont, QFontDatabase, QShortcut, QKeySequence, QIcon
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QFrame, QLabel,
    QPushButton, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLineEdit,
    QComboBox, QCheckBox, QFileDialog, QMessageBox, QListWidget, QListWidgetItem,
    QStackedWidget, QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QPlainTextEdit, QSplitter, QScrollArea, QProgressBar, QAbstractItemView, QGridLayout, QSpinBox, QAbstractSpinBox)

from model import PROJECTS, default_options, build_command, list_inputs, as_path, Command, natural_key
from theme import STYLE
from documents import Document, read_exchange, load_json, flag, resolve_source, digest
from editor import ConfigEditor
from runs import prepare_run, finish_run, history, save_record
from pdf_preview import render_pdf


def setup_appearance(application):
    application.setApplicationName('Data Analysis')
    application.setApplicationDisplayName('数据分析工作台')
    application.setWindowIcon(QIcon(str(HERE / 'assets' / 'app.ico')))
    application.setStyle('Fusion')
    # Windows离屏验证没有系统字体数据库；显式加载已有字体，不安装字体或改系统设置。
    if 'Microsoft YaHei UI' not in QFontDatabase.families():
        fonts = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts'
        for name in ('msyh.ttc', 'msyhl.ttc', 'georgia.ttf', 'consola.ttf'):
            path = fonts / name
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
    application.setFont(QFont('Microsoft YaHei UI', 10))
    application.setStyleSheet(STYLE)


def label(text, name=None, wrap=False):
    item = QLabel(text)
    if name:
        item.setObjectName(name)
    item.setWordWrap(wrap)
    return item


def button(text, callback, primary=False):
    item = QPushButton(text)
    item.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        item.setObjectName('primary')
    item.clicked.connect(callback)
    return item


def open_path(path):
    path = Path(path)
    if path.exists():
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
    else:
        QMessageBox.information(None, '路径尚不存在', f'尚未生成或创建：\n{path}')


class WorkSignals(QObject):
    done = Signal(str, object, object)


class Work(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.token = uuid.uuid4().hex
        self.signals = WorkSignals()

    def run(self):
        try:
            self.signals.done.emit(self.token, self.function(), None)
        except Exception as exc:
            self.signals.done.emit(self.token, None, str(exc))


class ProcessTask(QObject):
    output = Signal(str)
    finished = Signal(int, str)

    def __init__(self, root, command, parent=None):
        super().__init__(parent)
        self.command = command
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.setWorkingDirectory(str(command.working_directory))
        env = QProcessEnvironment.systemEnvironment()
        for key, value in {'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1',
                           'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1',
                           'PYTHONUNBUFFERED': '1', 'DATA_ANALYSIS_NO_PAUSE': '1'}.items():
            env.insert(key, value)
        for key in ('PYTHONHOME', 'PYTHONPATH'):
            env.remove(key)
        self.process.setProcessEnvironment(env)
        self.program = str(root / 'python' / 'python.exe')
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self.completed = False
        self.log_path = root / 'out' / 'gui' / 'logs' / (datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:6] + '.log')
        self.stream = None
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.on_finished)
        self.process.errorOccurred.connect(self.on_error)

    def start(self):
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.stream = self.log_path.open('x', encoding='utf-8')
        except OSError as exc:
            self.finish(-1, f'无法创建运行日志：{exc}')
            return
        arguments = ['-B', '-u', '-X', 'utf8', str(self.command.script), *self.command.arguments]
        self.emit_text('运行参数：' + json.dumps(arguments[4:], ensure_ascii=False) + '\n')
        self.process.start(self.program, arguments)
        self.process.closeWriteChannel()

    def emit_text(self, text):
        if text:
            if self.stream:
                try:
                    self.stream.write(text)
                    self.stream.flush()
                except OSError:
                    # 日志盘写入失败不终止正在计算/写入的业务进程。
                    self.stream.close()
                    self.stream = None
                    self.output.emit('\n[提示] 日志写入失败，后续信息仅显示在界面。\n')
            self.output.emit(text)

    def read_output(self):
        self.emit_text(self.decoder.decode(bytes(self.process.readAllStandardOutput())))

    def on_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.finish(-1, '启动失败：' + self.process.errorString())

    def on_finished(self, code, status):
        self.read_output()
        self.emit_text(self.decoder.decode(b'', final=True))
        crashed = status == QProcess.ExitStatus.CrashExit
        message = ('进程异常退出，请查看日志。' if crashed else '完成' if code == 0 else
                   '部分图像未生成，请查看日志。' if code == 2 and self.command.script.name == 'plot_raman.py' else
                   f'未完成，退出码 {code}。请查看日志及已生成的部分结果。')
        self.finish(code if not crashed else -1, message)

    def finish(self, code, message):
        if self.completed:
            return
        self.completed = True
        self.emit_text('\n' + message + '\n')
        if self.stream:
            self.stream.close()
            self.stream = None
        self.finished.emit(code, message)


class ImageView(QLabel):
    def __init__(self):
        super().__init__()
        self.original = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(200, 180)
        self.setText('选择图像后在此预览')
        self.setStyleSheet('background: #f1f5f7; color: #79909f; border-radius: 5px;')

    def load(self, path):
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        size = reader.size()
        if not size.isValid():
            raise ValueError('无法读取这张图像。')
        reader.setScaledSize(size.scaled(QSize(2400, 1600), Qt.AspectRatioMode.KeepAspectRatio))
        image = reader.read()
        if image.isNull():
            raise ValueError(reader.errorString())
        self.original = QPixmap.fromImage(image)
        self.fit()

    def fit(self):
        if not self.original.isNull():
            self.setPixmap(self.original.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def set_image(self, image):
        self.original = QPixmap.fromImage(image)
        self.fit()

    def resizeEvent(self, event):
        self.fit()
        super().resizeEvent(event)


def read_sheet(path, sheet=None):
    import openpyxl
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True, keep_links=False)
    try:
        selected = sheet if sheet in workbook.sheetnames else workbook.sheetnames[0]
        ws = workbook[selected]
        rows = list(ws.iter_rows(max_row=min(ws.max_row or 1, 200), max_col=min(ws.max_column or 1, 40), values_only=True))
        return workbook.sheetnames, selected, rows, ws.max_row or 0
    finally:
        workbook.close()


class Preview(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.path = None
        self.generation = 0
        box = QVBoxLayout(self)
        box.setContentsMargins(12, 12, 12, 12)
        top = QHBoxLayout()
        self.caption = label('选择文件查看内容', 'muted')
        self.caption.setTextFormat(Qt.TextFormat.PlainText)
        self.sheets = QComboBox()
        self.sheets.setVisible(False)
        self.sheets.currentTextChanged.connect(self.change_sheet)
        top.addWidget(self.caption, 1)
        top.addWidget(self.sheets)
        top.addWidget(button('外部打开', lambda: open_path(self.path) if self.path else None))
        box.addLayout(top)
        self.pdf_controls = QWidget()
        pdf_bar = QHBoxLayout(self.pdf_controls)
        pdf_bar.setContentsMargins(0, 0, 0, 0)
        self.previous_page = button('上一页', lambda: self.pdf_page.setValue(self.pdf_page.value()-1))
        self.next_page = button('下一页', lambda: self.pdf_page.setValue(self.pdf_page.value()+1))
        self.pdf_page = QSpinBox()
        self.pdf_page.setRange(1, 1)
        self.pdf_page.setPrefix('第 ')
        self.pdf_page.setSuffix(' 页')
        self.pdf_page.setKeyboardTracking(False)
        self.pdf_page.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.pdf_page.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pdf_page.setFixedWidth(84)
        self.pdf_page.setAccessibleName('PDF页码')
        self.pdf_page.valueChanged.connect(self.load_pdf)
        self.pdf_count = label('', 'muted')
        pdf_bar.addWidget(self.previous_page)
        pdf_bar.addWidget(self.pdf_page)
        pdf_bar.addWidget(self.pdf_count)
        pdf_bar.addWidget(self.next_page)
        pdf_bar.addStretch()
        box.addWidget(self.pdf_controls)
        self.pdf_controls.hide()
        self.stack = QStackedWidget()
        self.empty = label('选择文件\n图片、PDF、Excel、CSV 和追溯记录可直接预览', 'muted')
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image = ImageView()
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        for widget in (self.empty, self.image, self.table, self.text):
            self.stack.addWidget(widget)
        box.addWidget(self.stack, 1)

    def reset(self, message='选择文件查看内容'):
        self.generation += 1
        self.path = None
        self.caption.setText('选择文件查看内容')
        self.caption.setToolTip('')
        self.sheets.hide()
        self.pdf_controls.hide()
        self.image.original = QPixmap()
        self.image.clear()
        self.stack.setCurrentWidget(self.empty)
        self.empty.setText(message)

    def show_file(self, path):
        self.reset('正在读取…')
        self.path = Path(path)
        self.generation += 1
        self.caption.setText(self.path.name)
        self.caption.setToolTip(str(self.path))
        self.sheets.setVisible(False)
        self.stack.setCurrentWidget(self.empty)
        self.empty.setText('正在读取…')
        extension = self.path.suffix.casefold()
        if extension in ('.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tif', '.tiff'):
            try:
                self.image.load(self.path)
                self.stack.setCurrentWidget(self.image)
            except Exception as exc:
                self.empty.setText(str(exc))
        elif extension == '.xlsx':
            self.load_sheet(None)
        elif extension == '.pdf':
            self.pdf_page.blockSignals(True)
            self.pdf_page.setRange(1, 1)
            self.pdf_page.setValue(1)
            self.pdf_page.blockSignals(False)
            self.pdf_controls.show()
            self.load_pdf(1)
        elif extension in ('.csv', '.json', '.txt', '.log', '.md'):
            token, path = self.generation, self.path
            def read():
                with path.open('r', encoding='utf-8-sig', errors='replace') as stream:
                    return stream.read(262144)
            def done(value, error):
                if token != self.generation:
                    return
                if error:
                    self.empty.setText(error)
                elif extension == '.csv':
                    try:
                        self.fill_table(list(islice(csv.reader(io.StringIO(value, newline='')), 200)))
                        self.caption.setText(path.name + ' · 最多200行，长文件截取前256KB')
                    except csv.Error:
                        self.empty.setText('CSV字段过长或格式无法解析，请使用外部打开。')
                else:
                    self.text.setPlainText(value)
                    self.caption.setText(path.name + ' · 最多256KB')
                    self.stack.setCurrentWidget(self.text)
            self.owner.submit(read, done)
        else:
            self.empty.setText('此格式请使用“外部打开”查看。')

    def load_pdf(self, number):
        if self.path is None or self.path.suffix.casefold() != '.pdf':
            return
        self.generation += 1
        token, path = self.generation, self.path
        self.previous_page.setEnabled(False)
        self.next_page.setEnabled(False)
        self.pdf_page.setEnabled(False)
        self.pdf_count.setText('正在读取…')
        self.stack.setCurrentWidget(self.empty)
        self.empty.setText('正在渲染PDF页面…')
        def done(frame, error):
            if token != self.generation:
                return
            if error:
                self.pdf_count.setText('预览不可用')
                self.empty.setText(error)
                return
            self.pdf_page.blockSignals(True)
            self.pdf_page.setRange(1, frame.count)
            self.pdf_page.setValue(frame.page)
            self.pdf_page.blockSignals(False)
            self.pdf_page.setEnabled(True)
            self.previous_page.setEnabled(frame.page > 1)
            self.next_page.setEnabled(frame.page < frame.count)
            self.pdf_count.setText(f'共 {frame.count} 页 · 只读缩放预览')
            self.caption.setText(f'{path.name} · 第{frame.page}/{frame.count}页')
            self.image.set_image(frame.image)
            self.stack.setCurrentWidget(self.image)
        self.owner.submit(lambda: render_pdf(path, number), done)

    def change_sheet(self, name):
        if name and self.path:
            self.load_sheet(name)

    def load_sheet(self, sheet):
        self.generation += 1
        token, path = self.generation, self.path
        def done(value, error):
            if token != self.generation:
                return
            if error:
                self.empty.setText('读取失败，请关闭 Excel 后重试：\n' + error)
                self.stack.setCurrentWidget(self.empty)
                return
            names, selected, rows, total = value
            self.sheets.blockSignals(True)
            self.sheets.clear()
            self.sheets.addItems(names)
            self.sheets.setCurrentText(selected)
            self.sheets.blockSignals(False)
            self.sheets.setVisible(True)
            self.caption.setText(f'{path.name} · 前{len(rows)}/{total}行，最多40列 · 只读缓存值')
            self.fill_table(rows)
        self.owner.submit(lambda: read_sheet(path, sheet), done)

    def fill_table(self, rows):
        columns = min(max((len(row) for row in rows), default=0), 40)
        self.table.clear()
        self.table.setRowCount(len(rows))
        self.table.setColumnCount(columns)
        for r, row in enumerate(rows):
            for c, value in enumerate(row[:columns]):
                self.table.setItem(r, c, QTableWidgetItem('' if value is None else str(value)))
        self.table.horizontalHeader().setDefaultSectionSize(145)
        self.stack.setCurrentWidget(self.table)


class ProjectPage(QWidget):
    status_changed = Signal(str, str)

    def __init__(self, window, project, saved):
        super().__init__()
        self.window, self.project, self.root = window, project, window.root
        self.fields = {}
        self.task = None
        self.task_kind = 'analysis'
        self.preparing = False
        self.recording = False
        self.current_run = None
        self.current_record = None
        self.editor = None
        self.project_path = self.root / 'out' / 'gui' / 'projects' / (project.key + '.json')
        self.document = Document(project.key, self.root / project.key)
        self.result_roots = []
        self.scan_generation = 0
        self.result_generation = 0
        self.setObjectName('workspace')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 18)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.addWidget(label(project.subtitle, 'eyebrow'))
        titles.addWidget(label(project.title, 'title'))
        head.addLayout(titles, 1)
        self.status = label('就绪', 'status')
        head.addWidget(self.status, 0, Qt.AlignmentFlag.AlignVCenter)
        self.save_button = button('保存项目', self.save_project)
        head.addWidget(button('打开项目', self.open_project))
        head.addWidget(self.save_button)
        head.addWidget(button('使用说明', lambda: open_path(self.root / project.key / 'README.md')))
        layout.addLayout(head)
        layout.addWidget(label(project.note, 'note', True))

        split = QSplitter(Qt.Orientation.Horizontal)
        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        self.controls = QWidget()
        control_layout = QVBoxLayout(self.controls)
        control_layout.setContentsMargins(0, 0, 9, 0)
        controls_scroll.setWidget(self.controls)
        controls_scroll.setMinimumWidth(310)
        io, form = self.group('01  输入与输出')
        self.path_field(form, 'input', '输入目录', directory=True)
        if project.key in ('Ra', 'Raman'):
            modes = [('使用界面中的项目配置', 'config'), ('扫描目录 / 单独处理', 'directory')]
            if project.key == 'Raman':
                modes.append(('基准对比分析', 'compare'))
            self.combo(form, 'mode', '处理模式', modes)
            form.addRow(label('在“数据与配置”直接编辑，或导入 JSON / Excel / CSV。', 'muted', True))
            self.mode_hint = label('', 'muted', True)
            form.addRow(self.mode_hint)
            form.addRow(button('从输入目录追加样本', self.import_config))
        if project.key == 'ER':
            self.path_field(form, 'template', '模板（留空自动识别）', file=True)
        self.path_field(form, 'output', '结果父目录（每次新建独立运行）', directory=True)
        if project.key == 'CLSM':
            self.path_field(form, 'xlsx', '结果工作簿（使用文件名）', save=True)
            form.addRow(label('GUI 将工作簿保存到本次运行目录；此处仅使用文件名。', 'muted', True))
        control_layout.addWidget(io)
        params, pf = self.group('02  分析参数')
        if project.key == 'Ra':
            self.combo(pf, 'types', '数据类型', [('全部', 'all'), ('仅BMP / 图片', 'bmp'), ('仅DAT', 'dat')])
            self.line(pf, 'batch', '每组数量', '留空处理全部启用行')
            self.check(pf, 'no_plot', '只计算，不生成图像')
        elif project.key == 'Raman':
            self.line(pf, 'xmin', '区间下限 / cm⁻¹', '单谱必填；堆叠 / 对比可留空')
            self.line(pf, 'xmax', '区间上限 / cm⁻¹', '单谱必填；堆叠 / 对比可留空')
            self.line(pf, 'height', '峰高阈值', '5；基准对比使用逐样品阈值')
            self.line(pf, 'shift', '峰位校正 / cm⁻¹', '仅单谱和基准对比生效')
            self.path_field(pf, 'compare_reference', '基准光谱 TXT', file=True,
                            filters='TXT 光谱 (*.txt)')
            self.line(pf, 'reference_height', '基准峰高阈值', '留空自动计算')
            self.line(pf, 'compare_tolerance', '匹配容差 / cm⁻¹', '10')
            self.check(pf, 'compare_normalize', '对比图逐谱归一化')
            self.line(pf, 'zero_guard', '激光线排除半宽 / cm⁻¹', '50；0 表示关闭')
        elif project.key == 'CLSM':
            self.combo(pf, 'input_format', '输入格式', [('自动 / 同名TXT优先', 'auto'), ('仅TXT', 'txt'), ('仅LSM', 'lsm')])
            self.line(pf, 'count', '取样分区数')
            self.line(pf, 'strip', '条带宽度 / µm')
            self.line(pf, 'seed', '随机种子')
            self.line(pf, 'attempts', '每区最多候选数')
            self.line(pf, 'dy', 'Y步距 / µm', '留空沿用输入规则')
            self.check(pf, 'no_plot', '只计算，不生成图像')
        else:
            pf.addRow(label('保留完整票据名称、模板样式和合并单元格。\n生成当天日期；品名与金额请在结果中复核。', 'muted', True))
        control_layout.addWidget(params)
        if project.key == 'CLSM':
            calibrate, cf = self.group('LSM 高度标定')
            self.combo(cf, 'lsm_mode', '标定策略', [('要求外部标定', 'require-calibration'), ('待核验试算', 'metadata-pending')])
            self.line(cf, 'scale', '有符号比例 / µm', '未确认请留空')
            self.line(cf, 'offset', '高度偏移 / µm')
            control_layout.addWidget(calibrate)
        control_layout.addStretch()

        self.tabs = QTabWidget()
        files_box = QWidget()
        fl = QVBoxLayout(files_box)
        top = QHBoxLayout()
        self.file_count = label('点击刷新读取输入', 'muted')
        top.addWidget(self.file_count, 1)
        top.addWidget(button('刷新输入', self.refresh_inputs))
        fl.addLayout(top)
        self.files = QTableWidget(0, 3)
        self.files.setHorizontalHeaderLabels(['文件', '类型', '位置'])
        self.files.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.files.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.files.setAlternatingRowColors(True)
        self.files.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.files.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.input_preview = None
        if project.key == 'ER':
            input_split = QSplitter(Qt.Orientation.Horizontal)
            self.input_preview = Preview(window)
            self.input_preview.reset('选择输入票据查看图片或PDF；原始文件只读')
            self.files.setMinimumWidth(180)
            input_split.addWidget(self.files)
            input_split.addWidget(self.input_preview)
            input_split.setSizes([280, 520])
            fl.addWidget(input_split, 1)
            self.files.currentCellChanged.connect(self.select_input)
        else:
            fl.addWidget(self.files)
        fl.addWidget(label('列表用于核对输入，最终处理范围由模式、Excel启用行及正式分析程序决定。', 'muted', True))
        if project.key in ('Ra', 'Raman'):
            try:
                remembered = (saved or {}).get('_project_file')
                source = as_path(remembered, self.root) if remembered else self.project_path if self.project_path.exists() else as_path((saved or {}).get('config') or default_options(self.root, project)['config'], self.root)
                if source.is_file():
                    self.document = read_exchange(source, project.key)
                elif remembered:
                    raise ValueError(f'上次打开的项目不存在：{source}；请重新选择项目')
            except Exception as exc:
                self.append_startup_error = str(exc)
            self.editor = ConfigEditor(self.document, submit=window.submit)
            self.editor.can_replace = self.confirm_replace
            self.editor.changed.connect(self.update_dirty)
            self.editor.imported.connect(self.project_imported)
            self.editor.save_as_requested.connect(lambda: self.save_project(save_as=True))
            self.tabs.addTab(self.editor, '01  数据与配置')
            self.tabs.addTab(controls_scroll, '运行设置')
            files_box.setParent(self)
            files_box.hide()
        else:
            self.tabs.addTab(files_box, '01  输入文件')
            self.tabs.addTab(controls_scroll, '运行设置')
            try:
                source = as_path((saved or {}).get('_project_file'), self.root) if (saved or {}).get('_project_file') else self.project_path
                if source.exists():
                    self.document = load_json(source, project.key)
            except (OSError, ValueError) as exc:
                self.append_startup_error = str(exc)
        self.history_list = QListWidget()
        self.history_list.setObjectName('history')
        self.history_list.currentItemChanged.connect(self.select_history)
        history_box = QWidget()
        hl = QVBoxLayout(history_box)
        hl.addWidget(label('每次运行保留配置、输入摘要、日志及独立结果；最多列出最近200次。', 'muted', True))
        hl.addWidget(self.history_list, 1)
        self.tabs.addTab(history_box, '02  运行记录')
        result_box = QWidget()
        rl = QVBoxLayout(result_box)
        rt = QHBoxLayout()
        self.result_caption = label('选择一次运行查看其实际生成的文件', 'muted')
        rt.addWidget(self.result_caption, 1)
        rt.addWidget(button('刷新结果', self.refresh_results))
        rl.addLayout(rt)
        results_split = QSplitter(Qt.Orientation.Horizontal)
        self.results = QListWidget()
        self.results.setObjectName('results')
        self.results.setMinimumWidth(140)
        self.results.currentItemChanged.connect(self.select_result)
        self.preview = Preview(window)
        results_split.addWidget(self.results)
        results_split.addWidget(self.preview)
        results_split.setSizes([180, 500])
        rl.addWidget(results_split, 1)
        self.result_tab = self.tabs.addTab(result_box, '03  分析结果')
        self.tabs.currentChanged.connect(self.tab_changed)
        split.addWidget(self.tabs)
        split.setSizes([360, 740])
        split.setStretchFactor(1, 1)
        layout.addWidget(split, 1)

        action = QHBoxLayout()
        action.addWidget(button('打开项目目录', lambda: open_path(self.root / project.key)))
        action.addWidget(button('打开结果目录', self.open_output))
        self.log_toggle = button('展开运行日志', self.toggle_log)
        action.addWidget(self.log_toggle)
        action.addStretch()
        self.save_state_label = label('配置已就绪', 'muted')
        action.addWidget(self.save_state_label)
        self.run_button = button('开始分析', self.start_analysis, True)
        action.addWidget(self.run_button)
        layout.addLayout(action)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        logs_head = QHBoxLayout()
        logs_head.addWidget(label('运行日志', 'eyebrow'))
        logs_head.addStretch()
        logs_head.addWidget(button('打开日志文件', self.open_log))
        self.logs_container = QWidget()
        logs_layout = QVBoxLayout(self.logs_container)
        logs_layout.setContentsMargins(0, 0, 0, 0)
        logs_layout.addLayout(logs_head)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.document().setMaximumBlockCount(4000)
        self.log.setFixedHeight(125)
        self.log.setPlaceholderText('就绪。分析过程在后台运行，切换项目不会中断任务。')
        logs_layout.addWidget(self.log)
        layout.addWidget(self.logs_container)
        self.logs_container.hide()

        values = default_options(self.root, project)
        if isinstance(saved, dict):
            values.update({k: v for k, v in saved.items() if k in values and isinstance(v, (str, bool))})
        values.update({k: v for k, v in self.document.options.items() if k in values and isinstance(v, (str, bool))})
        for key, widget in self.fields.items():
            value = values[key]
            if isinstance(widget, QComboBox):
                index = widget.findData(value)
                widget.setCurrentIndex(max(index, 0))
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            else:
                widget.setText(str(value))
        if 'mode' in self.fields:
            self.fields['mode'].currentIndexChanged.connect(self.mode_changed)
            self.mode_changed()
        self.document.options = self.values()
        self.saved_options = deepcopy(self.values())
        for widget in self.fields.values():
            signal = widget.currentIndexChanged if isinstance(widget, QComboBox) else widget.toggled if isinstance(widget, QCheckBox) else widget.textChanged
            signal.connect(self.update_dirty)
        self.update_dirty()
        if getattr(self, 'append_startup_error', None):
            self.append_log('项目配置未能打开，来源文件保持不变：' + self.append_startup_error + '\n')
            self.logs_container.show()
        self.refresh_history()

    def group(self, title):
        group = QGroupBox(title)
        form = QFormLayout(group)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setVerticalSpacing(9)
        return group, form

    def line(self, form, key, title, placeholder=''):
        widget = QLineEdit()
        widget.setPlaceholderText(placeholder)
        widget.setObjectName(key)
        self.fields[key] = widget
        form.addRow(title, widget)
        return widget

    def path_field(self, form, key, title, *, directory=False, file=False, save=False,
                   filters='Excel (*.xlsx)'):
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        widget = QLineEdit()
        widget.setObjectName(key)
        widget.textChanged.connect(widget.setToolTip)
        self.fields[key] = widget
        def choose():
            current = widget.text() or str(self.root / self.project.key)
            if directory:
                selected = QFileDialog.getExistingDirectory(self, title, current)
            elif save:
                selected, _ = QFileDialog.getSaveFileName(self, title, current, filters)
            else:
                selected, _ = QFileDialog.getOpenFileName(self, title, current, filters)
            if selected:
                widget.setText(selected)
        row.addWidget(widget, 1)
        choose_button = button('选择', choose)
        choose_button.setMaximumWidth(56)
        row.addWidget(choose_button)
        form.addRow(title, box)

    def combo(self, form, key, title, choices):
        widget = QComboBox()
        widget.setObjectName(key)
        for text, value in choices:
            widget.addItem(text, value)
        self.fields[key] = widget
        form.addRow(title, widget)

    def check(self, form, key, title):
        widget = QCheckBox(title)
        self.fields[key] = widget
        form.addRow(widget)

    def values(self):
        values = default_options(self.root, self.project)
        for key, widget in self.fields.items():
            values[key] = widget.currentData() if isinstance(widget, QComboBox) else widget.isChecked() if isinstance(widget, QCheckBox) else widget.text().strip()
        return values

    def mode_changed(self):
        mode = self.fields['mode'].currentData()
        configured = mode == 'config'
        managed = mode in ('config', 'compare')
        if self.project.key == 'Ra':
            self.mode_hint.setText('分析使用界面启用行；输入目录用于追加新样本。' if configured else
                                   '目录扫描不读取项目表格，DAT去噪和高通默认关闭。')
        elif mode == 'compare':
            self.mode_hint.setText('一个基准谱对多个启用样品；逐样品峰阈值，归一化、容差和区间为全局设置。')
        elif configured:
            self.mode_hint.setText('堆叠只做视觉对照；归一化、顺序和逐行参数由界面配置。')
        else:
            self.mode_hint.setText('单谱模式请填写波数上下限；扫描目录不读取项目表格。')
        if self.editor:
            self.editor.document.options = self.values()
            self.editor.sync_mode()
            self.editor.setEnabled(managed and not self.preparing and
                                   (not self.task or self.task.completed))
        if self.project.key == 'Raman':
            compare = mode == 'compare'
            self.fields['shift'].setEnabled(mode != 'config')
            self.fields['height'].setEnabled(not compare)
            for key in ('compare_reference', 'reference_height', 'compare_tolerance',
                        'compare_normalize', 'zero_guard'):
                self.fields[key].setEnabled(compare)
            reference = self.fields['compare_reference']
            reference.setToolTip('选择基准光谱 TXT；匹配只表示峰位差在容差内，不代表物相鉴定。'
                                 if compare else '仅在基准对比模式使用')

    def import_config(self):
        if self.preparing or (self.task and not self.task.completed) or not self.editor:
            return
        try:
            source = as_path(self.values()['input'], self.root)
            if not source.is_dir():
                raise ValueError('请先选择存在的输入目录。')
            matches = [p for p in source.iterdir() if p.is_file() and p.suffix.casefold() in self.project.extensions]
            paths = (sorted(matches, key=lambda p: natural_key(p.name))
                     if self.project.key == 'Raman' else sorted(matches))
            self.editor.document.options = self.values()
            added = self.editor.add_paths(paths)
            self.tabs.setCurrentWidget(self.editor)
            self.window.statusBar().showMessage(f'追加 {added} 份新样本；原有参数及缺失行保留，请检查后保存项目。', 12000)
        except (ValueError, OSError) as exc:
            QMessageBox.information(self, '无法追加样本', str(exc))

    def tab_changed(self, index):
        if self.tabs.widget(index) is self.history_list.parentWidget():
            self.refresh_history()

    def dirty(self):
        return (self.editor is not None and self.editor.dirty()) or (hasattr(self, 'saved_options') and self.values() != self.saved_options)

    def update_dirty(self, *unused):
        if not hasattr(self, 'save_state_label') or not hasattr(self, 'saved_options'):
            return
        dirty = self.dirty()
        self.save_state_label.setText('有未保存修改' if dirty else '无未保存修改')
        self.save_state_label.setStyleSheet('color:#a56c35;' if dirty else '')
        self.save_button.setText('保存项目 *' if dirty else '保存项目')
        if self.editor:
            values = self.values()
            if self.editor.document.options != values:
                self.editor.document.options = values
                self.editor.schedule_validation()

    def confirm_replace(self):
        if not self.dirty():
            return True
        answer = QMessageBox.question(self, '打开另一份配置', '当前项目有未保存修改。是否先保存？',
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        return self.save_project() if answer == QMessageBox.StandardButton.Save else answer == QMessageBox.StandardButton.Discard

    def apply_options(self, values):
        for key, widget in self.fields.items():
            if key not in values:
                continue
            value = values[key]
            if isinstance(widget, QComboBox):
                index = widget.findData(value)
                if index >= 0:
                    widget.setCurrentIndex(index)
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            else:
                widget.setText(str(value))

    def project_imported(self, document):
        self.document = document
        self.apply_options(document.options)
        self.document.options = self.values()
        self.saved_options = deepcopy(self.values())
        self.update_dirty()

    def open_project(self):
        if self.preparing or (self.task and not self.task.completed):
            return
        if self.editor:
            self.editor.import_file()
            return
        path, _ = QFileDialog.getOpenFileName(self, '打开项目参数', str(self.project_path.parent), '项目 (*.json)')
        if path:
            try:
                document = load_json(path, self.project.key)
                if self.confirm_replace():
                    self.project_imported(document)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, '无法打开项目', str(exc))

    def save_project(self, checked=False, *, save_as=False):
        self.save_button.setFocus()
        document = self.editor.document if self.editor else self.document
        destination = document.path or self.project_path
        if save_as:
            selected, _ = QFileDialog.getSaveFileName(self, '项目另存为（选择新文件名）', str(destination), '项目 (*.json)')
            if not selected:
                return False
            destination = Path(selected).resolve()
        try:
            document.options = self.values()
            backup = document.save(destination)
            self.document = document
            self.saved_options = deepcopy(self.values())
            if self.editor:
                self.editor.mark_saved()
            self.update_dirty()
            self.window.save_state()
            self.window.statusBar().showMessage('已保存：' + str(destination) + (f'；备份：{backup}' if backup else ''), 12000)
            return True
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, '项目未保存', str(exc) + '\n编辑内容仍保留，可使用“另存为”。')
            return False

    def toggle_log(self):
        visible = not self.logs_container.isVisible()
        self.logs_container.setVisible(visible)
        self.log_toggle.setText('收起运行日志' if visible else '展开运行日志')

    def refresh_history(self):
        selected = self.current_run.record['id'] if self.current_run else None
        self.history_list.blockSignals(True)
        self.history_list.clear()
        labels = {'success': '已完成', 'partial': '部分成功', 'failed': '失败', 'review': '输入变化，需复核', 'running': '未记录结束', 'prepared': '已准备'}
        for entry in history(self.root, self.project.key):
            status = '运行中' if selected == entry.get('id') and self.task and not self.task.completed else labels.get(entry.get('status'), '待检查')
            item = QListWidgetItem(f'{entry.get("started", "")}    {status}\n{entry.get("input_count", 0)} 份输入  ·  {entry.get("id", "")}')
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setToolTip(str(entry.get('record_path', '')))
            self.history_list.addItem(item)
        self.history_list.blockSignals(False)

    def select_history(self, item, previous):
        if not item:
            return
        try:
            entry = item.data(Qt.ItemDataRole.UserRole)
            record = json.loads(Path(entry['record_path']).read_text(encoding='utf-8'))
            if record.get('module') != self.project.key:
                raise ValueError('记录所属项目不匹配')
            self.current_record = record
            self.result_roots = tuple(Path(p) for p in record.get('result_roots', []))
            self.refresh_results()
            self.tabs.setCurrentIndex(self.result_tab)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.append_log('无法打开运行记录：' + str(exc) + '\n')

    def refresh_inputs(self):
        self.scan_generation += 1
        generation = self.scan_generation
        self.file_count.setText('正在读取…')
        values = self.values()
        def done(result, error):
            if generation != self.scan_generation:
                return
            self.files.setRowCount(0)
            if self.input_preview:
                self.input_preview.reset('选择输入票据查看内容')
            if error:
                self.file_count.setText('读取失败，请检查路径或配置')
                self.file_count.setToolTip(error)
                self.append_log('输入检查：' + error + '\n')
                return
            paths, truncated = result
            self.file_count.setText(f'{len(paths)} 份输入' + ('（仅显示前2000份）' if truncated else ''))
            self.file_count.setToolTip('')
            self.files.setRowCount(len(paths))
            for i, path in enumerate(paths):
                for col, text in enumerate((path.name, path.suffix.upper().lstrip('.'), str(path.parent))):
                    self.files.setItem(i, col, QTableWidgetItem(text))
                self.files.item(i, 0).setData(Qt.ItemDataRole.UserRole, str(path))
        if self.editor and values['mode'] == 'config':
            frozen = deepcopy(self.editor.document)
            frozen.options = deepcopy(values)
            def configured_inputs():
                paths = [resolve_source(frozen, row, values) for row in frozen.rows if flag(row.get('enabled'), True)]
                return paths[:2000], len(paths) > 2000
            self.window.submit(configured_inputs, done)
        else:
            self.window.submit(lambda: list_inputs(self.root, self.project, values), done)

    def select_input(self, row, column, previous_row, previous_column):
        if self.input_preview and row >= 0:
            item = self.files.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole):
                self.input_preview.show_file(item.data(Qt.ItemDataRole.UserRole))

    def refresh_results(self):
        self.result_generation += 1
        generation = self.result_generation
        values = self.values()
        try:
            output = as_path(values['output'], self.root)
        except ValueError:
            return
        record = deepcopy(self.current_record)
        roots = tuple(self.result_roots)
        self.results.clear()
        self.preview.reset('选择运行记录查看结果' if record is None else '选择本次生成的文件查看内容')
        status_name = {'success': '已完成', 'partial': '部分成功', 'failed': '失败', 'review': '需复核', 'running': '运行中', 'prepared': '已准备'}
        self.result_caption.setText('尚未选择运行；旧结果仍保留在原目录' if record is None else
                                    f'{record.get("id", "")}  ·  {status_name.get(record.get("status"), "待检查")}  ·  仅本次产物')
        def find():
            found = set()
            if record and record.get('status') not in ('running', 'prepared'):
                # 已结束的任务只使用产物清单；目录里后来增加的文件不会混入。
                candidates = (Path(p) for p in record.get('files', []))
                return [p for p in candidates if p.is_file() and p.suffix.casefold() in ('.png', '.jpg', '.jpeg', '.xlsx', '.csv', '.json', '.pdf', '.log')][:2000]
            for path in roots if record else ():
                candidates = (path,) if path.is_file() else path.rglob('*') if path.is_dir() else ()
                for item in candidates:
                    if item.is_file() and not item.name.startswith('~$') and item.suffix.casefold() in ('.png', '.jpg', '.jpeg', '.xlsx', '.csv', '.json'):
                        # ER默认输出目录为模块根目录，只展示生成验收单和out，避免把输入票据列为结果。
                        if self.project.key == 'ER' and output == self.root / 'ER' and not item.is_relative_to(output / 'out') and not item.name.startswith('物品验收单_'):
                            continue
                        found.add(item)
                        if len(found) >= 2000:
                            break
            return sorted(found, key=lambda p: (p.suffix != '.xlsx', str(p).casefold()))[:2000]
        def done(paths, error):
            if generation != self.result_generation:
                return
            if error:
                self.append_log('结果扫描：' + error + '\n')
                return
            for path in paths:
                try:
                    display = str(path.relative_to(output))
                except ValueError:
                    display = path.name
                item = QListWidgetItem(display)
                item.setData(Qt.ItemDataRole.UserRole, str(path))
                item.setToolTip(str(path))
                self.results.addItem(item)
            if paths:
                self.results.setCurrentRow(0)
        self.window.submit(find, done)

    def select_result(self, item, previous):
        if item:
            self.preview.show_file(item.data(Qt.ItemDataRole.UserRole))

    def open_output(self):
        try:
            destination = Path(self.current_record['project']).parent if self.current_record else as_path(self.values()['output'], self.root)
            open_path(destination)
        except ValueError as exc:
            QMessageBox.information(self, '结果路径', str(exc))

    def append_log(self, text):
        cursor = self.log.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        self.log.setTextCursor(cursor)
        self.log.ensureCursorVisible()

    def start_analysis(self):
        if self.preparing or self.recording or (self.task and not self.task.completed):
            return
        self.run_button.setFocus()
        if self.dirty():
            if QMessageBox.question(self, '保存后运行', '当前有未保存修改。保存项目并按当前配置开始？',
                    QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Save) != QMessageBox.StandardButton.Save or not self.save_project():
                return
        self.preparing = True
        self.run_button.setEnabled(False)
        self.controls.setEnabled(False)
        if self.editor:
            self.editor.setEnabled(False)
        self.status.setText('准备运行')
        self.progress.setRange(0, 0)
        self.progress.show()
        values = deepcopy(self.values())
        document = deepcopy(self.editor.document if self.editor else self.document)
        def ready(prepared, error):
            self.preparing = False
            if error:
                self.controls.setEnabled(True)
                if self.editor:
                    self.editor.setEnabled(True)
                self.mode_changed() if 'mode' in self.fields else None
                self.run_button.setEnabled(True)
                self.progress.hide()
                self.status.setText('请检查配置')
                QMessageBox.warning(self, '无法开始分析', error)
                return
            try:
                self.window.check_conflicts(self.project.key, prepared.command)
                self.current_run = prepared
                self.current_record = prepared.record
                prepared.record['status'] = 'running'
                save_record(prepared)
                self.launch(prepared.command)
                self.refresh_history()
            except (OSError, ValueError) as exc:
                self.controls.setEnabled(True)
                if self.editor:
                    self.editor.setEnabled(True)
                self.run_button.setEnabled(True)
                self.progress.hide()
                self.status.setText('启动失败')
                QMessageBox.warning(self, '无法开始分析', str(exc))
        self.task_kind = 'analysis'
        self.window.submit(lambda: prepare_run(self.root, self.project, values, document), ready)

    def launch(self, command):
        self.window.save_state()
        self.result_roots = command.results
        self.log.clear()
        self.controls.setEnabled(False)
        if self.editor:
            self.editor.setEnabled(False)
        self.run_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.status.setText('运行中')
        self.status_changed.emit(self.project.key, '运行中')
        if self.task:
            self.task.deleteLater()
        self.task = ProcessTask(self.root, command, self)
        self.task.output.connect(self.append_log)
        self.task.finished.connect(self.analysis_finished)
        self.task.start()

    def analysis_finished(self, code, message):
        self.controls.setEnabled(True)
        if self.editor:
            self.editor.setEnabled(True)
        if 'mode' in self.fields:
            self.mode_changed()
        self.run_button.setEnabled(True)
        self.progress.setVisible(False)
        self.status.setText('已完成' if code == 0 else '部分成功' if code == 2 and self.project.key == 'Raman' else '需检查日志')
        self.status_changed.emit(self.project.key, '已完成' if code == 0 else '需检查')
        if code != 0:
            self.logs_container.show()
            self.log_toggle.setText('收起运行日志')
        if self.current_run:
            self.recording = True
            self.run_button.setEnabled(False)
            prepared = self.current_run
            log_path = self.task.log_path
            def recorded(record, error):
                self.recording = False
                self.run_button.setEnabled(True)
                if error:
                    self.append_log('运行记录未能完整保存：' + error + '\n')
                    self.logs_container.show()
                else:
                    self.current_record = record
                    if record.get('status') == 'review':
                        self.status.setText('输入变化，需复核')
                        self.append_log(record.get('message', '') + '\n')
                        self.logs_container.show()
                self.refresh_history()
                self.refresh_results()
                self.tabs.setCurrentIndex(self.result_tab)
            self.window.submit(lambda: finish_run(prepared, code, message, log_path), recorded)
        else:
            self.refresh_results()

    def open_log(self):
        if self.current_record and self.current_record.get('log_path'):
            open_path(Path(self.current_record['log_path']))
        elif self.task:
            open_path(self.task.log_path)
        else:
            QMessageBox.information(self, '运行日志', '开始分析后会生成独立日志文件。')


class Window(QMainWindow):
    def __init__(self, root):
        super().__init__()
        self.root = root.resolve()
        self.setWindowTitle('Data Analysis · 实验数据工作台')
        available = QApplication.primaryScreen().availableGeometry()
        self.setMinimumSize(1050, 680)
        self.resize(min(1480, max(1050, available.width()-64)), min(960, max(680, available.height()-80)))
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.workers = {}
        self.state_path = self.root / 'out' / 'gui' / 'state.json'
        self.state = {}
        self.state_error = None
        try:
            if self.state_path.is_file():
                self.state = json.loads(self.state_path.read_text(encoding='utf-8'))
                if not isinstance(self.state, dict):
                    raise ValueError('界面状态必须是对象')
        except (OSError, ValueError) as exc:
            self.state_error = str(exc)
            self.state = {}
        central = QWidget()
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName('sidebar')
        sidebar.setFixedWidth(222)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(0, 28, 0, 20)
        brand = label('Data\nAnalysis.', 'brand')
        brand.setContentsMargins(25, 0, 0, 8)
        side.addWidget(brand)
        small = label('实验数据工作台  /  DESKTOP', 'muted')
        small.setContentsMargins(26, 0, 0, 20)
        side.addWidget(small)
        self.navigation = QListWidget()
        self.navigation.setObjectName('navigation')
        self.navigation.addItem('工作台概览')
        for project in PROJECTS:
            self.navigation.addItem(f'{project.key}    {project.title}')
        self.navigation.addItem('环境与设置')
        side.addWidget(self.navigation, 1)
        footer = label('LOCAL WORKSPACE\n本地处理 · 原始数据只读', 'muted')
        footer.setContentsMargins(24, 0, 12, 0)
        side.addWidget(footer)
        row.addWidget(sidebar)
        self.stack = QStackedWidget()
        row.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.stack.addWidget(self.make_home())
        projects_state = self.state.get('projects', {})
        if not isinstance(projects_state, dict):
            projects_state = {}
        self.pages = {}
        project_files = self.state.get('project_files', {})
        if not isinstance(project_files, dict):
            project_files = {}
        for project in PROJECTS:
            page_state = deepcopy(projects_state.get(project.key, {}))
            if not isinstance(page_state, dict):
                page_state = {}
            if isinstance(project_files.get(project.key), str):
                page_state['_project_file'] = project_files[project.key]
            page = ProjectPage(self, project, page_state)
            page.status_changed.connect(self.project_status)
            self.pages[project.key] = page
            self.stack.addWidget(page)
        self.stack.addWidget(self.make_environment())
        self.navigation.currentRowChanged.connect(self.navigate)
        self.navigation.setCurrentRow(0)
        self.save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self.save_shortcut.activated.connect(self.save_current_project)

    def save_current_project(self):
        current = self.stack.currentWidget()
        if isinstance(current, ProjectPage):
            current.save_project()

    def submit(self, function, callback):
        work = Work(function)
        self.workers[work.token] = (work, callback)
        work.signals.done.connect(self.work_done, Qt.ConnectionType.QueuedConnection)
        self.pool.start(work)

    @Slot(str, object, object)
    def work_done(self, token, result, error):
        entry = self.workers.pop(token, None)
        if entry:
            try:
                entry[1](result, error)
            except Exception as exc:
                self.statusBar().showMessage('预览更新失败：' + str(exc), 15000)
                traceback.print_exc()

    def navigate(self, index):
        if index < 0:
            return
        self.stack.setCurrentIndex(index)
        if 1 <= index <= len(PROJECTS):
            page = self.pages[PROJECTS[index - 1].key]
            page.refresh_inputs()
            page.refresh_results()

    def make_home(self):
        widget = QWidget()
        widget.setObjectName('workspace')
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(42, 38, 42, 30)
        layout.addWidget(label('RESEARCH WORKSPACE   /   01', 'eyebrow'))
        layout.addSpacing(10)
        layout.addWidget(label('从原始数据，到可核对的结果。', 'title'))
        layout.addWidget(label('选择一个项目开始。输入、配置、分析与结果，在同一个工作台完成。', 'muted', True))
        layout.addSpacing(20)
        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(20)
        for index, project in enumerate(PROJECTS, 1):
            card = QGroupBox()
            card.setObjectName('homeCard')
            card.setMinimumHeight(208)
            content = QVBoxLayout(card)
            content.setContentsMargins(23, 19, 23, 21)
            content.addWidget(label(f'{index:02}   /   {project.subtitle}', 'eyebrow'))
            content.addWidget(label(project.title, 'cardTitle'))
            content.addWidget(label(project.note, 'muted', True), 1)
            enter = QHBoxLayout()
            enter.addStretch()
            enter.addWidget(button('进入项目', lambda checked=False, n=index: self.navigation.setCurrentRow(n)))
            content.addLayout(enter)
            grid.addWidget(card, (index-1)//2, (index-1)%2)
        layout.addLayout(grid)
        layout.addStretch()
        layout.addWidget(label('开始之前', 'eyebrow'))
        layout.addWidget(label('配置直接在窗口中编辑并保存。每次 GUI 分析使用独立目录，保留输入摘要、配置快照与运行日志。\n原始数据只读；计算结果用于报告或报销前仍需核对。', 'note', True))
        return widget

    def make_environment(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(35, 30, 35, 25)
        layout.addWidget(label('WORKSPACE SETTINGS', 'eyebrow'))
        layout.addWidget(label('环境与设置', 'title'))
        layout.addWidget(label('本地运行，无需启动服务或登录账户。基础检查不会运行分析，也不会清理结果。', 'note', True))
        group = QGroupBox('当前工作区')
        form = QFormLayout(group)
        for title, text in [('项目目录', str(self.root)), ('共享解释器', str(self.root / 'python' / 'python.exe')),
                            ('界面状态', str(self.state_path)), ('结果策略', 'GUI 每次独立运行目录及配置快照；原 BAT 默认行为保留')]:
            value = label(text, 'muted', True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(title, value)
        layout.addWidget(group)
        actions = QHBoxLayout()
        self.check_button = button('运行环境检查', self.check_environment, True)
        actions.addWidget(self.check_button)
        actions.addWidget(button('打开日志目录', lambda: open_path(self.root / 'out' / 'gui' / 'logs')))
        actions.addWidget(button('项目说明', lambda: open_path(self.root / 'README.md')))
        actions.addStretch()
        layout.addLayout(actions)
        self.check_log = QPlainTextEdit()
        self.check_log.setReadOnly(True)
        self.check_log.setPlaceholderText('点击“运行环境检查”，核对便携解释器、依赖、源码语法与启动器格式。')
        layout.addWidget(self.check_log, 1)
        self.check_task = None
        return widget

    def check_environment(self):
        if self.check_task and not self.check_task.completed:
            return
        self.check_button.setEnabled(False)
        self.check_log.clear()
        command = Command(self.root / 'check_projects.py', (), self.root, (), '')
        if self.check_task:
            self.check_task.deleteLater()
        self.check_task = ProcessTask(self.root, command, self)
        def output(text):
            cursor = self.check_log.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            cursor.insertText(text)
            self.check_log.setTextCursor(cursor)
        self.check_task.output.connect(output)
        self.check_task.finished.connect(lambda code, message: self.check_button.setEnabled(True))
        self.check_task.start()

    def project_status(self, key, status):
        for index, project in enumerate(PROJECTS, 1):
            if project.key == key:
                self.navigation.item(index).setText(f'{key}  {project.title}\n{status}')

    def check_conflicts(self, key, command):
        for other, page in self.pages.items():
            if other == key or not page.task or page.task.completed:
                continue
            for new in command.results:
                for old in page.task.command.results:
                    if new == old or new.is_relative_to(old) or old.is_relative_to(new):
                        raise ValueError(f'结果位置与正在运行的{other}任务重叠，请等待该任务完成或选择独立目录。')

    def save_state(self):
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            if self.state_error and self.state_path.exists():
                # 损坏的旧状态不覆盖，保存到新文件供下次人工检查。
                destination = self.state_path.with_name('state_recovered.json')
            else:
                destination = self.state_path
            temporary = destination.with_suffix('.' + uuid.uuid4().hex[:8] + '.tmp')
            temporary.write_text(json.dumps({'version': 2, 'projects': {key: page.values() for key, page in self.pages.items()},
                'project_files': {key: str((page.editor.document if page.editor else page.document).path) for key, page in self.pages.items()
                                  if (page.editor.document if page.editor else page.document).path}}, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(destination)
        except OSError as exc:
            self.statusBar().showMessage('界面设置未保存：' + str(exc), 15000)

    def closeEvent(self, event):
        running = [key for key, page in self.pages.items() if page.preparing or page.recording or (page.task and not page.task.completed)]
        if self.check_task and not self.check_task.completed:
            running.append('环境检查')
        if running:
            QMessageBox.information(self, '任务正在运行', '请等待以下任务完成后关闭窗口：\n' + '、'.join(running) + '\n可继续切换项目查看状态。')
            event.ignore()
            return
        if self.workers:
            self.statusBar().showMessage('正在结束只读预览，请稍候…')
            event.ignore()
            QTimer.singleShot(200, self.close)
            return
        dirty = [page for page in self.pages.values() if page.dirty()]
        if dirty:
            answer = QMessageBox.question(self, '保存项目修改', '以下项目有未保存修改：' + '、'.join(p.project.key for p in dirty) + '\n是否保存后关闭？',
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save)
            if answer == QMessageBox.StandardButton.Cancel or (answer == QMessageBox.StandardButton.Save and not all(p.save_project() for p in dirty)):
                event.ignore()
                return
        self.save_state()
        event.accept()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='只检查GUI依赖，不显示窗口')
    args = parser.parse_args()
    if args.check:
        import PySide6
        print(f'GUI依赖正常：PySide6 {PySide6.__version__}\n解释器：{sys.executable}')
        return 0
    if sys.platform == 'win32':
        # 使用稳定任务栏标识与统一图标，不更改注册表或系统关联。
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('DataAnalysis.Workbench')
    application = QApplication(sys.argv[:1])
    setup_appearance(application)
    root = HERE.parent
    # 界面状态只有一份；同一工作区只允许一个GUI，避免重复写入同一结果。
    from PySide6.QtCore import QLockFile
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    import hashlib
    state_dir = root / 'out' / 'gui'
    state_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(state_dir / 'workspace.lock'))
    lock.setStaleLockTime(0)
    name = 'DataAnalysis-' + hashlib.sha256(str(root.resolve()).casefold().encode('utf-8')).hexdigest()[:24]
    if not lock.tryLock(0):
        client = QLocalSocket()
        client.connectToServer(name)
        if client.waitForConnected(500):
            client.write(b'activate')
            client.waitForBytesWritten(500)
            client.disconnectFromServer()
            return 0
        QMessageBox.information(None, '工作台已打开', '该工作区的GUI已在运行，请切换到已打开的窗口。')
        return 0
    window = Window(root)
    server = QLocalServer(application)
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    def activate_existing():
        while server.hasPendingConnections():
            connection = server.nextPendingConnection()
            if window.isMinimized():
                window.showNormal()
            window.show()
            window.raise_()
            window.activateWindow()
            connection.disconnectFromServer()
            connection.deleteLater()
    server.newConnection.connect(activate_existing)
    if not server.listen(name):
        window.statusBar().showMessage('窗口已启动；重复启动时请手动切回此窗口。', 15000)
    window.show()
    result = application.exec()
    server.close()
    lock.unlock()
    return result


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        error = traceback.format_exc()
        directory = HERE.parent / 'out' / 'gui'
        try:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'startup_error.log').write_text(error, encoding='utf-8')
        except OSError:
            pass
        application = QApplication.instance() or QApplication([])
        QMessageBox.critical(None, '工作台启动失败', '启动失败，请检查 out/gui/startup_error.log。\n' + error[-1800:])
        raise SystemExit(1)

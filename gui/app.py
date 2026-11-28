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
                            Qt, Signal, Slot, QTimer, QUrl, QSize, QEvent, QPointF)
from PySide6.QtGui import (QDesktopServices, QImageReader, QPixmap, QFont, QFontDatabase, QShortcut,
                           QKeySequence, QIcon, QWheelEvent)
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QFrame, QLabel,
    QPushButton, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLineEdit,
    QComboBox, QCheckBox, QFileDialog, QMessageBox, QListWidget, QListWidgetItem,
    QStackedWidget, QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QPlainTextEdit, QSplitter, QScrollArea, QProgressBar, QAbstractItemView, QGridLayout, QSpinBox, QAbstractSpinBox,
    QToolButton, QMenu, QAbstractScrollArea)

from model import PROJECTS, default_options, build_command, list_inputs, as_path, Command, natural_key
import sci_pic as sci_pic_services
from theme import STYLE, WARNING
from documents import Document, read_exchange, load_json, flag, resolve_source, digest
from editor import ConfigEditor
from runs import prepare_run, finish_run, history, save_record
from pdf_preview import render_pdf


class WheelGuard(QObject):
    """滚轮掠过下拉框、数值框时不再偷换值：事件改交给最近的滚动区，页面照常滚动。

    Qt 默认给 QComboBox / QSpinBox 的 focusPolicy 是 WheelFocus，纯滚动就会把焦点抢过去并改值，
    在检查器这种纵向排满输入框的区域最容易误触。
    """
    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.Wheel or not isinstance(watched, (QComboBox, QAbstractSpinBox)):
            return False
        area = watched.parentWidget()
        while area is not None and not isinstance(area, QAbstractScrollArea):
            area = area.parentWidget()
        if area is None:
            event.ignore()
            return True
        viewport = area.viewport()
        position = QPointF(viewport.mapFrom(watched, event.position().toPoint()))
        forwarded = QWheelEvent(position, event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                                event.buttons(), event.modifiers(), event.phase(), event.inverted())
        QApplication.sendEvent(viewport, forwarded)
        return True


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
    guard = WheelGuard(application)
    application.installEventFilter(guard)
    application.wheel_guard = guard


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
        self.setStyleSheet('background: #F2F2F5; color: #8A8A92; border-radius: 10px;')

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
        self.input_total = None
        self.setObjectName('workspace')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 16)
        head = QHBoxLayout()
        head.setSpacing(8)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label(project.subtitle, 'eyebrow'))
        titles.addWidget(label(project.title, 'title'))
        head.addLayout(titles, 1)
        self.status = label('就绪', 'status')
        head.addWidget(self.status, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addSpacing(8)
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
        controls_scroll.setMinimumWidth(300)
        io, form = self.group('01  输入与输出')
        self.io_form = form
        self.path_field(form, 'input', '输入目录', directory=True)
        if project.key in ('Ra', 'Raman'):
            modes = ([('使用界面中的项目配置', 'config'), ('扫描目录 / 单独处理', 'directory')]
                     if project.key == 'Ra' else
                     [('堆叠对比（表格配置）', 'config'), ('单谱（扫描目录）', 'directory'),
                      ('基准对比分析', 'compare'), ('拼图（实物图 + 各位置谱）', 'montage'),
                      ('分层批次展示', 'batch_display')])
            self.combo(form, 'mode', '处理模式', modes)
            if project.key == 'Raman':
                self.config_field = self.path_field(form, 'config', '堆叠/对比配置表', file=True)
                form.addRow(label('单谱/拼图/批次展示不需要该表；留空项由分析程序自动发现。', 'muted', True))
            form.addRow(label('在“数据与配置”直接编辑，或导入 JSON / Excel / CSV。', 'muted', True))
            self.mode_hint = label('', 'muted', True)
            form.addRow(self.mode_hint)
            form.addRow(button('从输入目录导入样本（可选替换/追加）', self.import_config))
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
            self.raman_form = pf
            self.line(pf, 'xmin', '区间下限 / cm⁻¹', '留空自动取数据波数范围')
            self.line(pf, 'xmax', '区间上限 / cm⁻¹', '留空自动取数据波数范围')
            self.line(pf, 'height', '峰高阈值', '5；基准对比使用逐样品阈值')
            self.line(pf, 'shift', '峰位校正 / cm⁻¹', '仅单谱、拼图和基准对比生效')
            self.mode_config_box = self.path_field(pf, 'mode_config',
                                                   '拼图/批次配置表（留空自动发现）', file=True)
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
        if project.key == 'Raman':
            advanced, af = self.advanced_group('高级设置：基准对比与激光线排除（点击展开）')
            self.raman_advanced = af
            self.path_field(af, 'compare_reference', '基准光谱 TXT', file=True,
                            filters='TXT 光谱 (*.txt)')
            self.line(af, 'reference_height', '基准峰高阈值', '留空自动计算')
            self.line(af, 'compare_tolerance', '匹配容差 / cm⁻¹', '10')
            self.check(af, 'compare_normalize', '对比图逐谱归一化')
            self.line(af, 'zero_guard', '激光线排除半宽 / cm⁻¹', '50；0 表示关闭')
            control_layout.addWidget(advanced)
        if project.key == 'CLSM':
            calibrate, cf = self.advanced_group('高级设置：LSM 高度标定（未确认前保持默认）')
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
        files_header = self.files.horizontalHeader()
        files_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        files_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        files_header.resizeSection(1, 90)
        files_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.input_preview = None
        if project.key == 'ER':
            input_split = QSplitter(Qt.Orientation.Horizontal)
            self.input_preview = Preview(window)
            self.input_preview.reset('选择输入票据查看图片或PDF；原始文件只读')
            self.files.setMinimumWidth(180)
            input_split.addWidget(self.files)
            input_split.addWidget(self.input_preview)
            input_split.setSizes([380, 440])
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
        rt.addWidget(button('创建 SCI-Pic 副本', self.send_result_to_scipic))
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
        split.setChildrenCollapsible(False)
        # 窄窗口（1366×768 在 125% 缩放下约 1093×614）放不下时横向滚动，而不是把检查器裁掉。
        body = QScrollArea()
        body.setObjectName('pageBody')
        body.setWidgetResizable(True)
        body.setFrameShape(QFrame.Shape.NoFrame)
        body.setWidget(split)
        self.body_scroll = body
        layout.addWidget(body, 1)

        action = QHBoxLayout()
        action.addWidget(button('打开项目目录', lambda: open_path(self.root / project.key)))
        action.addWidget(button('打开结果目录', self.open_output))
        self.log_toggle = button('展开运行日志', self.toggle_log)
        action.addWidget(self.log_toggle)
        action.addStretch()
        self.task_caption = label('', 'muted')
        action.addWidget(self.task_caption)
        self.timing_label = label('', 'muted')
        action.addWidget(self.timing_label)
        self.save_state_label = label('配置已就绪', 'muted')
        action.addWidget(self.save_state_label)
        self.run_button = button('开始分析', self.start_analysis, True)
        action.addWidget(self.run_button)
        layout.addLayout(action)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.clock = QTimer(self)
        self.clock.setInterval(1000)
        self.clock.timeout.connect(self.tick_clock)
        self.run_started = None
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

    def advanced_group(self, title):
        """可折叠的低频参数区：默认收起，避免常用任务被一屏无关字段淹没。"""
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        toggle = QToolButton()
        toggle.setObjectName('advanced_toggle')
        toggle.setText(title)
        toggle.setCheckable(True)
        toggle.setChecked(False)
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toggle.setArrowType(Qt.ArrowType.RightArrow)
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        body = QGroupBox()
        form = QFormLayout(body)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setVerticalSpacing(9)
        body.setVisible(False)

        def sync(checked):
            body.setVisible(checked)
            toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)

        toggle.toggled.connect(sync)
        outer.addWidget(toggle)
        outer.addWidget(body)
        return container, form

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
        return box

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

    def task_caption_text(self):
        """一行说明本次要跑什么：模式、关键输入限制和输出位置，不展开方法学。"""
        try:
            values = self.values()
        except Exception:
            return ''

        def name(key):
            value = str(values.get(key, '') or '').strip()
            return Path(value).name or value or '未选择'

        if self.project.key == 'Raman':
            mode = values.get('mode', 'config')
            label = {'config': '堆叠对比', 'directory': '单谱扫描', 'compare': '基准对比',
                     'montage': '拼图', 'batch_display': '分层批次展示'}.get(mode, mode)
            table = ''
            if mode in ('config', 'compare'):
                table = f' · {name("config")}'
            elif mode in ('montage', 'batch_display') and str(values.get('mode_config', '')).strip():
                table = f' · {name("mode_config")}'
            elif mode == 'directory':
                table = ' · 区间自动'
            return f'本次任务：{label} · 输出 {name("output")}{table}'
        if self.project.key == 'CLSM':
            fmt = {'auto': 'TXT/LSM 自动', 'txt': '仅 TXT', 'lsm': '仅 LSM'}.get(values.get('input_format'), '输入格式待定')
            calib = '要求外部标定' if values.get('lsm_mode') == 'require-calibration' else '待核验试算'
            return f'本次任务：槽宽/槽深 · {fmt} · {calib} · 不清理 out'
        if self.project.key == 'Ra':
            mode = '界面启用行' if values.get('mode') == 'config' else '扫描目录'
            return f'本次任务：Ra 粗糙度 · {mode} · 输出 {name("output")}'
        if self.project.key == 'ER':
            return f'本次任务：验收单 · 输出 {name("output")}（每次新建，不覆盖）· 品名与金额需人工复核'
        return f'本次任务：SCI-Pic · 输出 {name("output")}'

    def refresh_caption(self):
        if hasattr(self, 'task_caption'):
            text = self.task_caption_text()
            if getattr(self, 'input_total', None):
                text += f' · {self.input_total} 份输入'
            self.task_caption.setText(text)
            self.task_caption.setToolTip(text)

    def tick_clock(self):
        if self.run_started is None:
            return
        elapsed = int((datetime.now() - self.run_started).total_seconds())
        self.timing_label.setText(f'已运行 {elapsed} 秒')

    def mode_changed(self):
        mode = self.fields['mode'].currentData()
        configured = mode == 'config'
        managed = mode in ('config', 'compare')
        if self.project.key == 'Ra':
            self.mode_hint.setText('分析使用界面启用行；输入目录用于追加新样本。' if configured else
                                   '目录扫描不读取项目表格，DAT去噪和高通默认关闭。')
        elif mode == 'compare':
            self.mode_hint.setText('一个基准谱对多个启用样品；逐样品峰阈值，归一化、容差和区间为全局设置。')
        elif mode == 'montage':
            self.mode_hint.setText('每个样品一张图：上方实物照片，下方该样品各位置的谱；'
                                   '不填配置表时按 <组名>-1-1 图片与 <组名>-<序号> TXT 自动配对。')
        elif mode == 'batch_display':
            self.mode_hint.setText('多个基准 × 两组 × 多点位的分层展示：总览 → 单点 → 重复 → 对照 → 指标，'
                                   '生成可离线打开的 index.html；区间可选，峰高阈值不参与。')
        elif configured:
            self.mode_hint.setText('堆叠只做视觉对照；归一化、顺序和逐行参数由界面配置。')
        else:
            self.mode_hint.setText('单谱模式区间留空则自动使用输入数据的波数范围；扫描目录不读取项目表格。')
        if self.editor:
            self.editor.document.options = self.values()
            self.editor.sync_mode()
            self.editor.setEnabled(managed and not self.preparing and
                                   (not self.task or self.task.completed))
        if self.project.key == 'Raman':
            compare = mode == 'compare'
            needs_table = mode in ('config', 'compare')
            needs_auto_config = mode in ('montage', 'batch_display')
            self.fields['shift'].setEnabled(mode in ('directory', 'compare', 'montage'))
            self.fields['height'].setEnabled(mode in ('config', 'directory', 'montage'))
            self.fields['zero_guard'].setEnabled(mode in ('config', 'directory', 'montage'))
            for key in ('compare_reference', 'reference_height', 'compare_tolerance',
                        'compare_normalize'):
                self.fields[key].setEnabled(compare)
            self.set_row_visible(self.io_form, self.config_field, needs_table)
            self.set_row_visible(self.raman_form, self.mode_config_box, needs_auto_config)
            self.sync_mode_table(mode)
            reference = self.fields['compare_reference']
            reference.setToolTip('选择基准光谱 TXT；匹配只表示峰位差在容差内，不代表物相鉴定。'
                                 if compare else '仅在基准对比模式使用')
        self.refresh_caption()

    def sync_mode_table(self, mode):
        """堆叠/基准对比各用一张表；当前仍是另一模式的默认表时自动切到该模式的表。"""
        widget = self.fields['config']
        current = Path(widget.text().strip())
        target = self.root / self.project.key / ('stack.xlsx' if mode == 'config' else 'compare.xlsx')
        if mode not in ('config', 'compare') or current.name not in ('stack.xlsx', 'compare.xlsx'):
            return
        if target.is_file() and current.resolve() != target.resolve():
            widget.setText(str(target))

    def set_row_visible(self, form, widget, visible):
        row, _role = form.getWidgetPosition(widget)
        if row >= 0:
            form.setRowVisible(row, bool(visible))

    def import_config(self):
        if self.preparing or (self.task and not self.task.completed) or not self.editor:
            return
        self.editor.import_from_input()

    def tab_changed(self, index):
        if self.tabs.widget(index) is self.history_list.parentWidget():
            self.refresh_history()

    def dirty(self):
        return (self.editor is not None and self.editor.dirty()) or (hasattr(self, 'saved_options') and self.values() != self.saved_options)

    def update_dirty(self, *unused):
        if not hasattr(self, 'save_state_label') or not hasattr(self, 'saved_options'):
            return
        dirty = self.dirty()
        # 顶部状态点 + 底部一句话；不靠星号标记，保存键在未保存时自己变成强调色。
        self.save_state_label.setText('● 未保存修改' if dirty else '无未保存修改')
        self.save_state_label.setStyleSheet(f'color:{WARNING};' if dirty else '')
        self.save_button.setText('保存项目')
        self.save_button.setProperty('dirty', dirty)
        self.save_button.style().unpolish(self.save_button)
        self.save_button.style().polish(self.save_button)
        self.refresh_caption()
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
            self.input_total = len(paths)
            self.file_count.setText(f'{len(paths)} 份输入' + ('（仅显示前2000份）' if truncated else ''))
            self.refresh_caption()
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
            self.result_caption.setText(self.result_caption.text() + f'  ·  {len(paths)} 个产物')
        self.window.submit(find, done)

    def select_result(self, item, previous):
        if item:
            self.preview.show_file(item.data(Qt.ItemDataRole.UserRole))

    def send_result_to_scipic(self):
        item = self.results.currentItem()
        if not item:
            QMessageBox.information(self, 'SCI-Pic', '请先选择一个结果文件。')
            return
        raw_path = item.data(Qt.ItemDataRole.UserRole) or item.text()
        path = Path(raw_path)
        if not path.is_file():
            QMessageBox.warning(self, 'SCI-Pic', '所选结果文件当前不可用，请刷新结果后重试。')
            return
        record = self.current_record or {}
        self.window.navigate_to_key('sci-pic')
        self.window.scipic_page.accept_source(path, self.project.key, record.get('id', ''))

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
        self.run_started = datetime.now()
        self.timing_label.setText('已运行 0 秒')
        self.clock.start()
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
        self.clock.stop()
        if self.run_started is not None:
            elapsed = int((datetime.now() - self.run_started).total_seconds())
            self.timing_label.setText(f'本次耗时 {elapsed} 秒')
            self.run_started = None
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


SCI_STYLE_LABELS = {
    'style_preset': ('样式预设', '留空继承；如 paper / presentation'),
    'font': ('字体', '留空继承；中文字体名'),
    'figure_title_font_size_pt': ('图标题字号 pt', '图上方标题'),
    'axis_title_font_size_pt': ('轴标题字号 pt', '横轴/纵轴标题'),
    'tick_font_size_pt': ('刻度字号 pt', '坐标刻度数字'),
    'legend_font_size_pt': ('图例字号 pt', '图例文字'),
    'value_label_font_size_pt': ('数值标注字号 pt', '柱顶数值'),
    'peak_label_font_size_pt': ('峰值标注字号 pt', '峰位标注'),
    'min_peak_label_font_size_pt': ('最小标注字号 pt', '自动缩放下限'),
    'line_width_pt': ('线宽 pt', '折线与边框'),
    'axis_line_width_pt': ('轴线宽 pt', '坐标轴线'),
    'legend_location': ('图例位置', '留空继承；如 upper center'),
    'legend_text_color': ('图例文字颜色', 'HEX 或颜色名'),
    'series_colors': ('系列颜色', '逗号分隔的 HEX'),
    'palette': ('配色方案', '如 academic / energy'),
    'background_color': ('背景颜色', 'HEX 或颜色名'),
    'grid_enabled': ('显示网格', '是 / 否'),
    'grid_color': ('网格颜色', 'HEX 或颜色名'),
    'dpi': ('分辨率 dpi', '位图输出精度'),
    'output_formats': ('输出格式', '逗号分隔：png, pdf, svg'),
}


class SciPicPage(QWidget):
    """SCI-Pic workbench page; no run starts until the user submits a request."""
    def __init__(self, window, state=None):
        super().__init__()
        self.window = window
        self.root = window.root
        self.state = deepcopy(state) if isinstance(state, dict) else {}
        self.entries = []
        self.entry_cache = {}
        self.current_prepared = None
        self.current_record = None
        self.task = None
        self.preparing = False
        self.log_path = None
        self.editor = None
        self.proposal = None
        self.pending_source = None
        self.pending_run_id = ''
        self.output_root = self.root / 'sci-pic' / 'out'
        self.setObjectName('workspace')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        body = QScrollArea()
        body.setObjectName('pageBody')
        body.setWidgetResizable(True)
        body.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setObjectName('workspace')
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(30, 24, 30, 24)
        body.setWidget(inner)
        outer.addWidget(body)
        self.body = body
        layout.addWidget(label('SCI-PIC  /  STYLE-AWARE FIGURES', 'eyebrow'))
        layout.addWidget(label('SCI-Pic 学术绘图', 'title'))
        layout.addWidget(label('工作副本与可选样式在同一页面完成；留空继承，不自动运行。', 'note', True))
        self.tabs = QTabWidget()
        input_page = QWidget()
        input_layout = QVBoxLayout(input_page)
        input_layout.setContentsMargins(0, 6, 0, 0)
        self.tabs.addTab(input_page, '01  输入与示意图')
        box = QGroupBox('01  输入与配置：模板、示意图与工作副本')
        outer = QVBoxLayout(box)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.template = QComboBox()
        form.addRow('绘图模板', self.template)
        self.workbook = QLineEdit()
        self.workbook.setReadOnly(True)
        self.workbook.setPlaceholderText('尚未创建工作副本；点“新建工作副本”生成到 sci-pic/out/workbooks/')
        self.workbook.setToolTip('双击用系统默认程序（Excel / WPS）打开当前工作副本')
        self.workbook.installEventFilter(self)
        workbook_row = QWidget()
        workbook_layout = QHBoxLayout(workbook_row)
        workbook_layout.setContentsMargins(0, 0, 0, 0)
        workbook_layout.setSpacing(8)
        workbook_layout.addWidget(self.workbook, 1)
        self.open_workbook_button = button('打开', self.open_workbook)
        self.open_workbook_button.setMinimumWidth(64)
        self.open_workbook_button.setEnabled(False)
        workbook_layout.addWidget(self.open_workbook_button)
        self.reveal_workbook_button = button('文件夹', self.reveal_workbook)
        self.reveal_workbook_button.setMinimumWidth(64)
        self.reveal_workbook_button.setEnabled(False)
        self.reveal_workbook_button.setToolTip('打开工作副本所在的文件夹')
        workbook_layout.addWidget(self.reveal_workbook_button)
        self.workbook.textChanged.connect(self.sync_workbook_state)
        form.addRow('工作簿副本', workbook_row)
        self.output = QLineEdit(str(self.state.get('output', self.output_root)))
        form.addRow('输出目录', self.output)
        self.mapping = QLineEdit(','.join(sorted((self.state.get('mapping') or {}).keys())))
        form.addRow('映射（类别,数值[,分组]）', self.mapping)
        actions = QHBoxLayout()
        actions.addWidget(button('新建工作副本', self.create_copy))
        actions.addWidget(button('选择工作副本', self.choose_workbook))
        self.recent_button = button('最近副本', self.show_recent)
        self.recent_button.setToolTip('最近使用的工作副本，按时间倒序，最多 8 份')
        actions.addWidget(self.recent_button)
        actions.addWidget(button('打开输出目录', self.open_output))
        actions.addStretch()
        form.addRow(actions)
        outer.addLayout(form)
        card = QFrame()
        card.setObjectName('previewCard')
        card.setMaximumHeight(230)
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(14)
        self.preview_image = QLabel('模板示意图加载中…')
        self.preview_image.setObjectName('previewImage')
        self.preview_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_image.setFixedSize(176, 176)
        self.preview_image.setWordWrap(True)
        self.preview_image.setToolTip('缩略示意图：显示模板的版式与配色；坐标、图例文字请点“查看大图”。')
        card_layout.addWidget(self.preview_image, 0, Qt.AlignmentFlag.AlignTop)
        details = QVBoxLayout()
        details.setSpacing(5)
        details.addWidget(label('模板示意图', 'eyebrow'))
        self.preview_title = label('', 'dataset')
        details.addWidget(self.preview_title)
        self.preview_type = label('', 'muted', True)
        self.preview_type.setWordWrap(True)
        details.addWidget(self.preview_type)
        self.preview_desc = label('', 'methodNote', True)
        self.preview_desc.setWordWrap(True)
        details.addWidget(self.preview_desc)
        self.preview_example = label('', 'muted', True)
        self.preview_example.setWordWrap(True)
        details.addWidget(self.preview_example)
        details.addStretch()
        card_actions = QHBoxLayout()
        card_actions.addStretch()
        card_actions.addWidget(button('查看大图', self.open_preview))
        details.addLayout(card_actions)
        card_layout.addLayout(details, 1)
        outer.addWidget(card)
        input_layout.addWidget(box)
        input_layout.addStretch()
        mapping_box = QGroupBox('02  关键设置：从分析结果创建工作副本（先确认列映射）')
        mapping_form = QFormLayout(mapping_box)
        self.map_category = QComboBox()
        self.map_value = QComboBox()
        self.map_group = QComboBox()
        self.map_group.addItem('（无分组）', '')
        mapping_form.addRow('类别列', self.map_category)
        mapping_form.addRow('数值列', self.map_value)
        mapping_form.addRow('分组列（可选）', self.map_group)
        self.map_info = label('尚未选择源结果；原始 Raman 光谱不能直接映射为柱/折模板。', 'note', True)
        mapping_form.addRow(self.map_info)
        mapping_actions = QHBoxLayout()
        mapping_actions.addWidget(button('确认映射并生成副本', self.confirm_mapping))
        mapping_actions.addStretch()
        mapping_form.addRow(mapping_actions)
        self.tabs.setTabToolTip(0, '选模板、看示意图、创建工作副本')
        style_box = QGroupBox('03  可选样式（SCI-Pic 字号/线宽 pt；分辨率 dpi）')
        style_page = QWidget()
        style_page_layout = QVBoxLayout(style_page)
        style_page_layout.setContentsMargins(0, 6, 0, 0)
        style_page_layout.addWidget(mapping_box)
        style_page_layout.addWidget(style_box)
        self.tabs.addTab(style_page, '02  映射与样式')
        style_layout = QVBoxLayout(style_box)
        self.style_table = QTableWidget(0, 3)
        self.style_table.setHorizontalHeaderLabels(['参数', '填写值', '说明'])
        self.style_table.verticalHeader().setVisible(False)
        self.style_table.setMinimumHeight(160)
        self.style_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        style_layout.addWidget(self.style_table)
        self.strict = QCheckBox('未知样式键报错')
        style_actions = QHBoxLayout()
        style_actions.addWidget(self.strict)
        style_actions.addWidget(button('从副本读取', self.load_style))
        style_actions.addWidget(button('预览解析结果', self.preview_style))
        style_actions.addWidget(button('打开解析结果', self.open_resolved))
        style_actions.addStretch()
        style_layout.addLayout(style_actions)
        style_page_layout.addStretch()
        run_box = QGroupBox('04  运行与结果')
        run_layout = QVBoxLayout(run_box)
        controls = QHBoxLayout()
        self.run_button = button('开始绘图', self.run, True)
        controls.addWidget(self.run_button)
        controls.addWidget(button('打开运行日志', self.open_log))
        controls.addStretch()
        run_layout.addLayout(controls)
        self.status = label('尚未运行；界面不会自动开始绘图。', 'muted')
        run_layout.addWidget(self.status)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(150)
        run_layout.addWidget(self.log)
        self.results = QListWidget()
        self.results.itemDoubleClicked.connect(self.open_result)
        self.history_list = QListWidget()
        self.history_list.itemSelectionChanged.connect(self.select_history)
        run_layout.addWidget(label('历史运行（点击回看；可回到来源项目）', 'eyebrow'))
        self.history_list.setFixedHeight(120)
        run_layout.addWidget(self.history_list)
        run_layout.addWidget(label('结果文件（双击打开）', 'eyebrow'))
        self.results.setFixedHeight(120)
        run_layout.addWidget(self.results)
        self.back_source = button('回到来源项目', self.open_source_project)
        controls.addWidget(self.back_source)
        run_page = QWidget()
        run_page_layout = QVBoxLayout(run_page)
        run_page_layout.setContentsMargins(0, 6, 0, 0)
        run_page_layout.addWidget(run_box)
        self.tabs.addTab(run_page, '03  运行与结果')
        layout.addWidget(self.tabs, 1)
        try:
            self.entries = sci_pic_services.catalog(self.root)
            for entry in self.entries:
                self.template.addItem(entry['title'], entry['id'])
            self.template.currentIndexChanged.connect(self.refresh_preview)
        except (OSError, ValueError) as exc:
            self.status.setText('模板目录不可用：' + str(exc))
        stored = self.state.get('style', {}) if isinstance(self.state.get('style', {}), dict) else {}
        for field in sci_pic_services.style_rows():
            title, help_text = SCI_STYLE_LABELS.get(field, (field, '留空继承'))
            row = self.style_table.rowCount()
            self.style_table.insertRow(row)
            name = QTableWidgetItem(title)
            name.setData(Qt.ItemDataRole.UserRole, field)
            name.setToolTip(field)
            self.style_table.setItem(row, 0, name)
            self.style_table.setItem(row, 1, QTableWidgetItem(str(stored.get(field, ''))))
            help_item = QTableWidgetItem(help_text)
            help_item.setToolTip(field)
            self.style_table.setItem(row, 2, help_item)
        identifier = self.state.get('template_id', '')
        index = self.template.findData(identifier)
        if index >= 0:
            self.template.setCurrentIndex(index)
        if isinstance(self.state.get('workbook'), str) and Path(self.state['workbook']).is_file():
            self.workbook.setText(self.state['workbook'])
        self.output.setText(str(self.state.get('output', self.output_root)))
        self.refresh_preview()

    def refresh_preview(self, *_):
        """Show the selected template's thumbnail, type and description."""
        identifier = self.template.currentData()
        entry = next((item for item in self.entries if item['id'] == identifier), None)
        if entry is None:
            self.preview_image.setPixmap(QPixmap())
            self.preview_image.setText('暂无模板示意图\n（模板目录不可用）')
            self.preview_title.setText('')
            self.preview_type.setText('')
            self.preview_desc.setText('')
            self.preview_example.setText('')
            return
        pixmap = self.entry_cache.get(entry['id'])
        if pixmap is None:
            pixmap = QPixmap(str(entry['preview']))
            self.entry_cache[entry['id']] = pixmap
        if pixmap.isNull():
            self.preview_image.setPixmap(QPixmap())
            self.preview_image.setText('示意图文件无法读取：' + Path(entry['preview']).name)
        else:
            self.preview_image.setPixmap(pixmap.scaled(
                QSize(176, 176), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        self.preview_image.setText('')
        self.preview_title.setText(entry['title'])
        self.preview_type.setText('类型：%s　·　图 %s' % (entry['plot_type'], Path(entry['preview']).name))
        self.preview_desc.setText(entry['description'])
        self.preview_example.setText('示例：' + entry['example'])

    def open_preview(self):
        try:
            open_path(Path(self.entry()['preview']))
        except ValueError as exc:
            QMessageBox.information(self, '模板示意图', str(exc))

    def entry(self):
        identifier = self.template.currentData()
        for item in self.entries:
            if item['id'] == identifier:
                return item
        raise ValueError('请选择一个绘图模板。')

    def values(self):
        values = {}
        for row in range(self.style_table.rowCount()):
            text = self.style_table.item(row, 1).text().strip()
            if text:
                name = self.style_table.item(row, 0)
                values[name.data(Qt.ItemDataRole.UserRole) or name.text()] = text
        return values

    def create_copy(self):
        try:
            path = sci_pic_services.working_copy(self.root, self.entry(), target_dir=self.output.text().strip())
            if self.values():
                sci_pic_services.add_style_rows(path, self.values(), root=self.root)
            self.workbook.setText(str(path))
            self.remember_workbook(path)
            self.status.setText('已创建工作副本：' + path.name)
            self.status.setToolTip(str(path))
            # 新建副本的目的就是马上填数据；按 README 约定直接交给 Excel / WPS 打开。
            open_path(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, '工作副本', str(exc))

    def sync_workbook_state(self):
        """有工作副本且文件存在时，“打开”“文件夹”才可点。"""
        text = self.workbook.text().strip()
        path = Path(text) if text else None
        exists = bool(path) and path.is_file()
        self.open_workbook_button.setEnabled(exists)
        self.reveal_workbook_button.setEnabled(bool(path))
        self.open_workbook_button.setToolTip(str(path) if exists else '还没有可打开的工作副本')
        self.reveal_workbook_button.setToolTip(
            str(path.parent) if path else '打开工作副本所在的文件夹')
        if text:
            # 只读框默认把光标停在末尾，长路径只能看到文件名尾部；回到行首才能从根目录读起。
            self.workbook.setCursorPosition(0)

    def open_workbook(self):
        text = self.workbook.text().strip()
        if not text:
            QMessageBox.information(self, '工作副本', '还没有工作副本。先点“新建工作副本”，或用“选择工作副本”指定一份。')
            return
        path = Path(text)
        if not path.is_file():
            QMessageBox.information(self, '工作副本', f'找不到文件：{path}\n可以重新“新建工作副本”，或用“选择工作副本”重新指定。')
            return
        open_path(path)

    def reveal_workbook(self):
        text = self.workbook.text().strip()
        folder = Path(text).parent if text else self.root / 'sci-pic' / 'out' / 'workbooks'
        if folder.is_dir():
            open_path(folder)
        else:
            QMessageBox.information(self, '工作副本', f'文件夹还不存在：{folder}')

    def remember_workbook(self, path):
        """把副本记进本次会话的最近列表（去重、最多 8 条），随 state 一起持久化。"""
        text = str(path)
        items = [str(p) for p in (self.state.get('recent_workbooks') or []) if str(p).strip()]
        self.state['recent_workbooks'] = [text] + [p for p in items if p != text][:7]

    def recent_paths(self):
        """当前副本 → 历史列表 → 输出目录/工作副本目录里按时间倒序的 .xlsx，去重后最多 8 条。"""
        seen = []
        current = self.workbook.text().strip()
        if current and Path(current).is_file():
            seen.append(current)
        for item in self.state.get('recent_workbooks') or []:
            text = str(item).strip()
            if text and text not in seen and Path(text).is_file():
                seen.append(text)
        folders = []
        try:
            folders.append(as_path(self.output.text().strip() or str(self.output_root), self.root))
        except (OSError, ValueError):
            pass
        folders.append(self.output_root / 'workbooks')
        found = []
        for folder in folders:
            if not folder.is_dir():
                continue
            try:
                for path in folder.glob('*.xlsx'):
                    if path.is_file():
                        found.append((path.stat().st_mtime, path))
            except OSError:
                continue
        for _, path in sorted(found, key=lambda pair: pair[0], reverse=True):
            text = str(path)
            if text not in seen:
                seen.append(text)
        return [Path(p) for p in seen][:8]

    def use_recent(self, path):
        self.workbook.setText(str(path))
        self.remember_workbook(path)
        self.load_style()

    def show_recent(self):
        menu = QMenu(self)
        paths = self.recent_paths()
        if not paths:
            empty = menu.addAction('还没有历史副本')
            empty.setEnabled(False)
        else:
            for path in paths:
                try:
                    stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime('%m-%d %H:%M')
                except OSError:
                    stamp = ''
                action = menu.addAction(f'{path.name}    {stamp}' if stamp else path.name)
                action.setToolTip(str(path))
                action.triggered.connect(lambda _checked=False, target=path: self.use_recent(target))
            menu.addSeparator()
            menu.addAction('打开工作副本目录').triggered.connect(self.reveal_workbook)
        menu.exec(self.recent_button.mapToGlobal(self.recent_button.rect().bottomLeft()))

    def eventFilter(self, watched, event):
        if watched is self.workbook and event.type() == QEvent.Type.MouseButtonDblClick:
            self.open_workbook()
            return True
        return super().eventFilter(watched, event)

    def choose_workbook(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择 SCI-Pic 工作副本', str(self.root / 'sci-pic' / 'out'), 'Excel 工作簿 (*.xlsx *.xlsm)')
        if path:
            self.workbook.setText(path)
            self.remember_workbook(path)
            self.load_style()

    def load_style(self):
        path = self.workbook.text().strip()
        if not path or not Path(path).is_file():
            QMessageBox.information(self, '样式配置', '请先创建或选择工作副本。')
            return
        try:
            settings, locations = sci_pic_services.inspect_style(path)['settings'], sci_pic_services.inspect_style(path)['locations']
            for row in range(self.style_table.rowCount()):
                name = self.style_table.item(row, 0)
                field = name.data(Qt.ItemDataRole.UserRole) or name.text()
                self.style_table.item(row, 1).setText('' if settings.get(field) in (None, '') else str(settings[field]))
            self.status.setText('已读取 %d 个样式单元。' % len(locations))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, '样式配置', str(exc))

    def preview_style(self):
        try:
            payload = sci_pic_services.validate_style(self.root, workbook=self.workbook.text().strip() or None,
                                                       overrides=self.values(), strict=self.strict.isChecked())
            self.status.setText('解析成功：%d 个字段；schema %s' % (len(payload['values']), payload['schema_version']))
            self.append_log(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
        except Exception as exc:
            self.status.setText('样式解析失败')
            QMessageBox.warning(self, '样式解析', str(exc))

    def open_output(self):
        open_path(as_path(self.output.text().strip() or str(self.output_root), self.root))

    def open_resolved(self):
        path = (self.current_record or {}).get('resolved_config', '')
        if path and Path(path).is_file():
            open_path(Path(path))
        else:
            QMessageBox.information(self, '解析结果', '本次运行尚未生成 resolved_config.json。')

    def open_log(self):
        if self.log_path and Path(self.log_path).is_file():
            open_path(Path(self.log_path))
        else:
            QMessageBox.information(self, '运行日志', '开始运行后会生成独立日志。')

    def open_result(self, item):
        path = Path(item.text())
        if path.exists():
            open_path(path)

    def append_log(self, text):
        cursor = self.log.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        self.log.setTextCursor(cursor)

    def refresh_results(self):
        self.results.clear()
        for path in (self.current_record or {}).get('files', []):
            if Path(path).is_file():
                self.results.addItem(path)

    def accept_source(self, path, source_project='', source_run_id=''):
        """Called from another module's result list; mapping proposal runs off the Qt thread."""
        self.pending_source = str(path)
        self.pending_run_id = source_run_id
        self._source_project = source_project

        def worker():
            return sci_pic_services.propose_mapping(path, source_project)

        def done(proposal, error):
            if error:
                self.map_info.setText('无法识别源结果：' + error)
                return
            self.proposal = proposal
            self._fill_mapping(proposal)

        self.window.submit(worker, done)

    def _fill_mapping(self, proposal):
        self.map_category.clear()
        self.map_value.clear()
        self.map_group.clear()
        self.map_group.addItem('（无分组）', '')
        for col in proposal['category_candidates']:
            self.map_category.addItem(col, col)
        for col in proposal['value_candidates']:
            self.map_value.addItem(col, col)
        for col in proposal['group_candidates']:
            self.map_group.addItem(col, col)
        if not proposal['supported']:
            self.map_info.setText(proposal['reason'])
            return
        self.map_info.setText(
            f"可映射 {proposal['rows']} 行；忽略 {proposal['ignored']} 行；"
            f"错误 {len(proposal['errors'])} 行。{proposal['reason']}")

    def confirm_mapping(self):
        if not self.proposal or not self.pending_source:
            QMessageBox.information(self, '列映射', '请先从分析结果选择要制图的源文件。')
            return
        mapping = {'category': self.map_category.currentData(),
                   'value': self.map_value.currentData(),
                   'group': self.map_group.currentData() or ''}
        entry = self.entry()

        def worker():
            return sci_pic_services.create_work_copy_from_mapping(
                self.root, entry, self.pending_source,
                getattr(self, '_source_project', ''), self.pending_run_id, mapping,
                target_dir=self.output.text().strip() or None, style_values=self.values())

        def done(result, error):
            self.preparing = False
            if error:
                self.map_info.setText('工作副本生成失败：' + error)
                QMessageBox.warning(self, '工作副本', error)
                return
            self.workbook.setText(str(result['workbook']))
            self.map_info.setText(f"已生成副本：{result['row_count']} 行；忽略 {result['ignored']} 行。")
            self.current_record = {'manifest_path': str(result['manifest_path'])}

        self.preparing = True
        self.window.submit(worker, done)

    def refresh_history(self):
        self.history_list.clear()
        for record in history(self.root, 'sci-pic'):
            title = f"{record.get('started', '')} · {record.get('template_title', '')} · {record.get('status', '')}"
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, record)
            self.history_list.addItem(item)

    def select_history(self):
        item = self.history_list.currentItem()
        if not item:
            return
        record = item.data(Qt.ItemDataRole.UserRole)
        self.current_record = record
        self.refresh_results()
        if record.get('source_project'):
            self.map_info.setText(
                f"来源项目：{record['source_project']}；运行记录：{record.get('source_run_id', '') or '—'}")

    def open_source_project(self):
        record = self.current_record or {}
        project = record.get('source_project')
        if project:
            self.window.navigate_to_key(project)
        else:
            QMessageBox.information(self, '来源项目', '本次记录没有可回溯的来源项目。')

    def run(self):
        if self.task and not self.task.completed:
            return
        if self.preparing:
            return
        try:
            source = self.workbook.text().strip()
            if not source or not Path(source).is_file():
                raise ValueError('请先创建或选择工作副本。')
            manifest_path = (self.current_record or {}).get('manifest_path', '')
            source_project = getattr(self, '_source_project', '')
            source_run_id = self.pending_run_id

            def ready(prepared, error):
                self.preparing = False
                if error:
                    self.status.setText('准备失败')
                    QMessageBox.warning(self, '无法开始绘图', error)
                    return
                self.current_prepared, self.current_record = prepared, prepared.record
                prepared.record['status'] = 'running'
                save_record(prepared)
                self.launch(prepared.command)
            self.preparing = True
            self.window.submit(lambda: sci_pic_services.prepare_plot(
                self.root, self.entry(), source, self.output.text().strip() or str(self.output_root),
                style_config=source, style_overrides=self.values(), style_strict=self.strict.isChecked(),
                source_project=source_project, source_run_id=source_run_id, manifest_path=manifest_path), ready)
        except (OSError, ValueError) as exc:
            self.preparing = False
            self.status.setText('启动失败')
            QMessageBox.warning(self, '无法开始绘图', str(exc))

    def launch(self, command):
        self.window.save_state()
        self.log.clear()
        self.status.setText('运行中')
        if self.task:
            self.task.deleteLater()
        self.task = ProcessTask(self.root, command, self)
        self.task.output.connect(self.append_log)
        def finished(code, message):
            self.log_path = self.task.log_path
            self.status.setText('已完成' if code == 0 else '需检查日志')
            self.append_log(message + '\n')
            if self.current_prepared:
                def recorded(record, error):
                    if not error:
                        self.current_record = record
                        self.refresh_results()
                        self.refresh_history()
                    else:
                        self.append_log('运行记录未能完整保存：' + error + '\n')
                self.window.submit(lambda: finish_run(self.current_prepared, code, message, self.log_path), recorded)
        self.task.finished.connect(finished)
        self.task.start()

    def state_values(self):
        return {'template_id': self.template.currentData(), 'workbook': self.workbook.text().strip(),
                'output': self.output.text().strip(), 'style': self.values(),
                'recent_workbooks': [str(p) for p in (self.state.get('recent_workbooks') or [])],
                'style_strict': self.strict.isChecked()}

    def dirty(self):
        return False


class Window(QMainWindow):
    def __init__(self, root):
        super().__init__()
        self.root = root.resolve()
        self.setWindowTitle('Data Analysis · 实验数据工作台')
        available = QApplication.primaryScreen().availableGeometry()
        self.setMinimumSize(940, 560)
        # 常见 1366×768 在 125% 缩放下只有 1093×614 的逻辑空间，初始尺寸不能按 1050×680 硬顶。
        self.resize(min(1480, max(940, available.width() - 64)),
                    min(960, max(560, available.height() - 90)))
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
        sidebar.setFixedWidth(232)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(0, 26, 0, 18)
        brand = label('Data Analysis.', 'brand')
        brand.setContentsMargins(24, 0, 0, 2)
        side.addWidget(brand)
        small = label('EXPERIMENT WORKBENCH', 'muted')
        small.setContentsMargins(26, 0, 0, 18)
        side.addWidget(small)
        self.navigation = QListWidget()
        self.navigation.setObjectName('navigation')
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.navigation.addItem('工作台概览')
        for project in PROJECTS:
            self.navigation.addItem(f'{project.key}    {project.title}')
        self.navigation.addItem('sci-pic    SCI-Pic 学术绘图')
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
        scipic_state = self.state.get('sci-pic', {})
        self.scipic_page = SciPicPage(self, scipic_state if isinstance(scipic_state, dict) else {})
        self.pages['sci-pic'] = self.scipic_page
        self.stack.addWidget(self.scipic_page)
        self.stack.addWidget(self.make_environment())
        self.navigation.currentRowChanged.connect(self.navigate)
        self.navigation.setCurrentRow(0)
        self.save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self.save_shortcut.activated.connect(self.save_current_project)
        self.run_shortcut = QShortcut(QKeySequence('Ctrl+R'), self)
        self.run_shortcut.activated.connect(self.start_current_run)

    def start_current_run(self):
        """Ctrl+R：把焦点留在键盘上也能启动当前页面的分析。"""
        current = self.stack.currentWidget()
        if isinstance(current, ProjectPage):
            if current.run_button.isEnabled():
                current.run_button.click()
        elif isinstance(current, SciPicPage):
            if current.run_button.isEnabled():
                current.run_button.click()

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
        elif index == len(PROJECTS) + 1:
            self.scipic_page.refresh_results()

    def navigate_to_key(self, key):
        """Key-based navigation used by cross-module back-to-source links."""
        if key == 'sci-pic':
            self.navigation.setCurrentRow(len(PROJECTS) + 1)
            return
        for offset, project in enumerate(PROJECTS, 1):
            if project.key == key:
                self.navigation.setCurrentRow(offset)
                return

    def make_home(self):
        page = QScrollArea()
        page.setObjectName('pageBody')
        page.setWidgetResizable(True)
        page.setFrameShape(QFrame.Shape.NoFrame)
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
        # 卡片最小高度不会被网格自动带进行高，显式给每行定高，避免圆角边框互相压住。
        for row in range((len(PROJECTS) + 1) // 2):
            grid.setRowMinimumHeight(row, 208)
        card = QGroupBox()
        card.setObjectName('homeCard')
        card.setMinimumHeight(208)
        content = QVBoxLayout(card)
        content.setContentsMargins(23, 19, 23, 21)
        content.addWidget(label('05   /   SCI-PIC  /  STYLE-AWARE FIGURES', 'eyebrow'))
        content.addWidget(label('SCI-Pic 学术绘图', 'cardTitle'))
        content.addWidget(label('模板工作副本与可选样式配置；未填写样式时保持旧版外观。', 'muted', True), 1)
        enter = QHBoxLayout()
        enter.addStretch()
        enter.addWidget(button('进入项目', lambda checked=False, n=len(PROJECTS)+1: self.navigation.setCurrentRow(n)))
        content.addLayout(enter)
        grid.addWidget(card, len(PROJECTS)//2, len(PROJECTS)%2)
        layout.addLayout(grid)
        layout.addStretch()
        layout.addWidget(label('开始之前', 'eyebrow'))
        layout.addWidget(label('配置直接在窗口中编辑并保存。每次 GUI 分析使用独立目录，保留输入摘要、配置快照与运行日志。\n原始数据只读；计算结果用于报告或报销前仍需核对。', 'note', True))
        page.setWidget(widget)
        return page

    def make_environment(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(28, 24, 28, 20)
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
        layout.addWidget(label('检查输出', 'eyebrow'))
        self.check_log = QPlainTextEdit()
        self.check_log.setReadOnly(True)
        self.check_log.setMaximumHeight(220)
        self.check_log.setPlaceholderText('点击“运行环境检查”，核对便携解释器、依赖、源码语法与启动器格式。')
        layout.addWidget(self.check_log)
        layout.addStretch()
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
            if other == key or not getattr(page, 'task', None) or page.task.completed:
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
            state = {'version': 3,
                'projects': {key: page.values() for key, page in self.pages.items() if key != 'sci-pic'},
                'sci-pic': self.scipic_page.state_values(),
                'project_files': {key: str((page.editor.document if page.editor else page.document).path) for key, page in self.pages.items()
                                  if key != 'sci-pic' and (page.editor.document if page.editor else page.document).path}}
            temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(destination)
        except OSError as exc:
            self.statusBar().showMessage('界面设置未保存：' + str(exc), 15000)

    def closeEvent(self, event):
        running = [key for key, page in self.pages.items() if getattr(page, 'preparing', False)
                   or getattr(page, 'recording', False) or (getattr(page, 'task', None) and not page.task.completed)]
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
        dirty = [page for page in self.pages.values() if getattr(page, 'dirty', lambda: False)()]
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


# -*- coding: utf-8 -*-
"""原生桌面工作台：深色导航、白色数据区和浅绿色参数面板。"""
STYLE = '''
QWidget { color: #263b32; font-family: "Microsoft YaHei UI"; font-size: 13px; }
QMainWindow, QWidget#workspace { background: #f5f7f3; }
QFrame#sidebar { background: #172d31; border: 0; }
QFrame#sidebar QLabel { color: #9bb2aa; background: transparent; }
QFrame#sidebar QLabel#brand { font-family: Georgia; font-size: 29px; font-weight: bold; color: #f0f3e9; }
QFrame#sidebar QLabel#muted { font-size: 11px; color: #91a9a0; }
QListWidget#navigation { background: transparent; border: 0; outline: none; color: #a9c0b5; }
QListWidget#navigation::item { padding: 17px 12px; margin: 4px 12px; border-radius: 7px; border-left: 3px solid transparent; }
QListWidget#navigation::item:selected { background: #304e46; color: #f2f6ec; border-left: 3px solid #d9a06a; }
QListWidget#navigation::item:hover:!selected { background: #233d3c; color: white; }
QLabel#eyebrow { color: #7d9181; font-family: Consolas, "Microsoft YaHei UI"; font-size: 10px; letter-spacing: 1px; }
QLabel#title { font-size: 28px; font-weight: 600; color: #263f31; padding: 4px 0; }
QLabel#cardTitle { font-size: 22px; font-weight: 600; padding: 6px 0; color: #2b4c38; }
QLabel#sectionTitle { font-size: 15px; font-weight: 600; color: #34533d; }
QLabel#dataset { font-size: 14px; font-weight: 600; color: #4c6848; padding: 2px 0; }
QLabel#muted { color: #7b8e77; font-size: 12px; background: transparent; }
QLabel#note { background: #eaf0e5; color: #627b57; padding: 12px 15px; border-radius: 7px; font-size: 12px; }
QLabel#methodNote { background: #e3ebdc; color: #748a64; padding: 11px; border-radius: 6px; font-size: 11px; }
QLabel#status { color: #527947; background: #e8f0e1; padding: 7px 13px; border-radius: 6px; font-size: 12px; }
QFrame#dataCard { background: white; border: 1px solid #dfe6d9; border-radius: 10px; }
QFrame#inspector { background: #edf2e8; border: 1px solid #d9e3d0; border-radius: 10px; }
QFrame#inspector QLabel { background: transparent; color: #657e57; font-size: 12px; }
QFrame#inspector QLabel#sectionTitle { color: #3f5f34; font-size: 15px; }
QFrame#inspector QLabel#eyebrow { color: #879a78; font-size: 10px; }
QFrame#inspector QLabel#methodNote { background: #e1ead8; color: #6f855f; font-size: 11px; }
QGroupBox { background: white; border: 1px solid #dfe6d9; border-radius: 9px; margin-top: 13px; padding: 21px 16px 16px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 16px; padding: 0 7px; color: #536f46; }
QGroupBox#homeCard { margin-top: 0; padding: 0; border: 1px solid #dce5d5; border-radius: 11px; }
QLineEdit, QComboBox, QSpinBox { background: #fff; color: #3c5736; border: 1px solid #d5e0cd; border-radius: 5px; padding: 7px 9px; min-height: 20px; selection-background-color: #dcebd4; selection-color: #29472c; }
QLineEdit:focus, QComboBox:focus { border: 1px solid #5b9276; background: #fcfef9; }
QLineEdit:disabled, QComboBox:disabled { background: #edf1e8; color: #9aab8c; border-color: #dfe6d9; }
QComboBox::drop-down { border: 0; width: 22px; }
QComboBox QAbstractItemView { background: white; color: #38582d; selection-background-color: #e5efdc; selection-color: #33582a; border: 1px solid #cbdac0; padding: 4px; }
QPushButton { background: #fff; color: #536e48; border: 1px solid #d7e0ce; border-radius: 5px; padding: 8px 12px; min-height: 20px; }
QPushButton:hover { background: #edf4e6; border-color: #a8c195; color: #365f36; }
QPushButton:pressed { background: #e1ebd7; }
QPushButton:disabled { background: #eff3e9; color: #a9b69e; border-color: #e1e7da; }
QPushButton#primary { background: #2b6b59; color: #fff; border: 1px solid #2b6b59; padding: 11px 24px; font-weight: 600; }
QPushButton#primary:hover { background: #215744; border-color: #215744; }
QPushButton#primary:disabled { background: #9eb8a2; border-color: #9eb8a2; color: #eff4e8; }
QTabWidget::pane { border: 0; background: transparent; }
QTabBar::tab { color: #8a9b80; background: transparent; border-bottom: 2px solid #e0e7da; padding: 13px 18px; margin-right: 2px; }
QTabBar::tab:selected { color: #316a4b; border-bottom: 2px solid #387b56; font-weight: 600; }
QTabBar::tab:hover:!selected { color: #526f43; background: #eef3e7; }
QTableView, QTableWidget, QListWidget#results, QListWidget#history { background: #fff; border: 0; border-radius: 6px; gridline-color: #edf1e8; color: #4d6842; selection-background-color: #eaf2e3; selection-color: #304e2e; outline: none; alternate-background-color: #fbfcf8; }
QTableView::item { padding: 7px 5px; border-bottom: 1px solid #edf1e7; }
QTableView::item:selected { background: #eaf2e4; color: #3a6235; }
QHeaderView::section { background: #f4f7ef; color: #7e9271; border: 0; border-bottom: 1px solid #e5ecdf; padding: 11px 7px; font-size: 12px; }
QTableView QLineEdit { padding: 3px 5px; border: 1px solid #7ba36a; border-radius: 3px; background: white; }
QTableView QComboBox { padding: 1px 3px; min-height: 12px; }
QListWidget#history::item { padding: 21px; border: 1px solid #e2e9da; margin-bottom: 10px; border-radius: 8px; }
QListWidget#history::item:selected { background: #eaf2e4; color: #315a30; }
QListWidget#results::item { padding: 11px 8px; border-bottom: 1px solid #eef2e8; }
QListWidget#problems { background: #fcf5eb; border: 1px solid #eadbc4; border-radius: 6px; color: #8e623c; }
QListWidget#problems::item { padding: 7px 10px; }
QListWidget#problems::item:selected { background: #f0e2cc; color: #77502c; }
QPlainTextEdit { background: #1b3235; color: #d3e3d6; border: 0; border-radius: 7px; font-family: Consolas, "Microsoft YaHei UI"; font-size: 12px; padding: 10px; }
QScrollArea { background: transparent; border: 0; }
QSplitter::handle { background: transparent; width: 12px; }
QSplitter::handle:hover { background: #dce6d3; border-radius: 3px; }
QProgressBar { border: 0; background: #e3ebdb; max-height: 3px; }
QProgressBar::chunk { background: #65976e; }
QScrollBar:vertical { background: transparent; width: 9px; margin: 2px; }
QScrollBar::handle:vertical { background: #c9d6c1; min-height: 35px; border-radius: 3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QCheckBox { color: #708664; spacing: 8px; }
QMenu { background: white; color: #46653b; border: 1px solid #d6e2cb; padding: 5px; }
QMenu::item { padding: 9px 21px; border-radius: 4px; }
QMenu::item:selected { background: #e6f0dc; }
QStatusBar { background: #f5f7f3; color: #7f9273; font-size: 11px; border-top: 1px solid #e7ecdf; }
QStatusBar::item { border: 0; }
QToolTip { background: #263f33; color: #eef4e7; border: 0; padding: 7px 9px; }
'''

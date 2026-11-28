# -*- coding: utf-8 -*-
"""原生桌面工作台外观。

设计语言取自 Atrium 设置中心：中性灰底、白色卡片、单一强调色，
字号阶梯（页标题 / 行标题 / 说明 / 脚注），同心圆角，胶囊控件，
检查器是一条细分隔线而不是一张卡片。这里只放颜色、字号、圆角和控件样式，
布局数值留在 app.py / editor.py。
"""
from pathlib import Path

# --- 色彩 ---------------------------------------------------------------
BG = '#F5F5F7'            # 工作区底色
SURFACE = '#FFFFFF'       # 卡片、侧边栏
SUNKEN = '#EFEFF2'        # 分段控件轨道、按下底板
BORDER = '#E1E1E6'        # 1 DIP 描边
BORDER_SOFT = '#EDEDF0'   # 表内分隔线
INK = '#111317'           # 主文字
INK2 = '#5F5F66'          # 次文字
INK3 = '#8A8A92'          # 弱文字、脚注
ACCENT = '#0A84FF'        # 唯一强调色
ACCENT_HOVER = '#0077E6'
ACCENT_TINT = '#EAF3FF'   # 选中行、状态标签
DANGER = '#E0352B'        # 警示
WARNING = '#F0A020'       # 未保存提示

# --- 字号 ---------------------------------------------------------------
FONT_TITLE = 28           # 页标题
FONT_CARD = 19            # 卡片标题
FONT_SECTION = 14         # 分组标题、行标题
FONT_BODY = 13            # 正文
FONT_VALUE = 15           # 检查器里的样本名
FONT_NOTE = 11.5          # 说明、脚注、表格头

ASSETS = Path(__file__).resolve().parent / 'assets'


def asset(name):
    """QSS 里的 image: url(...) 需要正斜杠和引号。"""
    return str(ASSETS / name).replace('\\', '/')


STYLE = '''
QWidget {{
    color: {INK};
    font-family: "Microsoft YaHei UI";
    font-size: {FONT_BODY}px;
}}
QMainWindow, QWidget#workspace, QDialog {{ background: {BG}; }}

/* ---------- 侧边栏 ---------- */
QFrame#sidebar {{
    background: {SURFACE};
    border: 0;
    border-right: 1px solid {BORDER};
}}
QFrame#sidebar QLabel {{ color: {INK3}; background: transparent; }}
QFrame#sidebar QLabel#brand {{
    font-size: 19px; font-weight: 600; color: {INK}; letter-spacing: 0.2px;
}}
QFrame#sidebar QLabel#muted {{ font-size: {FONT_NOTE}px; color: {INK3}; }}
QListWidget#navigation {{
    background: transparent; border: 0; outline: none;
    color: {INK2}; font-size: {FONT_BODY}px;
}}
QListWidget#navigation::item {{
    padding: 9px 14px; margin: 1px 10px; border-radius: 19px;
}}
QListWidget#navigation::item:selected {{
    background: {ACCENT}; color: #ffffff; font-weight: 600;
}}
QListWidget#navigation::item:hover:!selected {{
    background: #F1F1F4; color: {INK};
}}

/* ---------- 文字层级 ---------- */
QLabel#eyebrow {{
    color: {INK3}; font-size: {FONT_NOTE}px; letter-spacing: 1.4px;
}}
QLabel#title {{ font-size: {FONT_TITLE}px; font-weight: 600; color: {INK}; padding: 2px 0; }}
QLabel#cardTitle {{ font-size: {FONT_CARD}px; font-weight: 600; color: {INK}; padding: 4px 0; }}
QLabel#sectionTitle {{ font-size: {FONT_SECTION}px; font-weight: 600; color: {INK}; }}
QLabel#dataset {{ font-size: {FONT_VALUE}px; font-weight: 600; color: {INK}; }}
QLabel#muted {{ color: {INK3}; font-size: 12px; background: transparent; }}
QLabel#note {{
    background: transparent; color: {INK2};
    padding: 2px 0; font-size: {FONT_BODY}px;
}}
QLabel#methodNote {{
    background: #F7F7F9; color: {INK2};
    padding: 10px 12px; border-radius: 8px; font-size: {FONT_NOTE}px;
}}
QLabel#status {{
    color: {ACCENT}; background: {ACCENT_TINT};
    padding: 4px 11px; border-radius: 11px;
    font-size: {FONT_NOTE}px; font-weight: 600;
}}

/* ---------- 卡片 ---------- */
QFrame#dataCard {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 18px; }}
QFrame#previewCard {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#previewCard QLabel {{ background: transparent; }}
QFrame#previewCard QLabel#methodNote {{ background: #F7F7F9; }}
QLabel#previewImage {{
    background: #F7F7F9; border: 1px solid {BORDER_SOFT};
    border-radius: 8px; color: {INK3}; font-size: {FONT_NOTE}px;
}}

/* 检查器：一条细线，不是卡片 */
QFrame#divider {{ background: {BORDER_SOFT}; border: 0; min-height: 1px; max-height: 1px; }}
QFrame#inspector {{
    background: transparent; border: 0;
    border-left: 1px solid {BORDER};
}}
QFrame#inspector QLabel {{ background: transparent; color: {INK2}; font-size: 12px; }}
QFrame#inspector QLabel#sectionTitle {{ color: {INK}; font-size: {FONT_SECTION}px; }}
QFrame#inspector QLabel#eyebrow {{ color: {INK3}; font-size: {FONT_NOTE}px; }}
QFrame#inspector QLabel#methodNote {{
    background: #F7F7F9; color: {INK2}; font-size: {FONT_NOTE}px;
}}

/* ---------- 分组框：标题放在卡片内，不压在边框上 ---------- */
QGroupBox {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 18px;
    margin: 0; padding: 42px 18px 16px;
    font-weight: 600; color: {INK};
}}
QGroupBox::title {{
    subcontrol-origin: padding; subcontrol-position: top left;
    left: 18px; top: 14px; padding: 0;
    color: {INK}; background: transparent; font-size: {FONT_SECTION}px;
}}
QGroupBox#homeCard {{ margin: 0; padding: 0; border: 1px solid {BORDER}; border-radius: 18px; }}

/* ---------- 输入 ---------- */
QLineEdit, QComboBox, QSpinBox {{
    background: {SURFACE}; color: {INK};
    border: 1px solid {BORDER}; border-radius: 8px;
    padding: 6px 10px; min-height: 18px;
    selection-background-color: {ACCENT}; selection-color: #ffffff;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border: 1px solid {ACCENT}; background: {SURFACE};
}}
QLineEdit:disabled, QComboBox:disabled {{
    background: #F7F7F9; color: {INK3}; border-color: {BORDER_SOFT};
}}
QComboBox {{ padding-right: 30px; }}
QComboBox::drop-down {{
    subcontrol-origin: padding; subcontrol-position: center right;
    width: 26px; border: 0; background: transparent;
}}
QComboBox::down-arrow {{ image: url("{CHEVRON}"); width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE}; color: {INK};
    border: 1px solid {BORDER}; border-radius: 10px; padding: 4px;
    selection-background-color: {ACCENT_TINT}; selection-color: {INK};
    outline: 0;
}}

/* ---------- 按钮 ---------- */
QPushButton, QToolButton {{
    background: {SURFACE}; color: {INK2};
    border: 1px solid {BORDER}; border-radius: 15px;
    padding: 6px 14px; min-height: 18px; font-size: 12.5px;
}}
QPushButton:hover, QToolButton:hover {{
    background: #F7F7F9; color: {INK}; border-color: #D5D5DB;
}}
QPushButton:pressed, QToolButton:pressed {{ background: {SUNKEN}; }}
QPushButton:disabled, QToolButton:disabled {{
    background: #F7F7F9; color: #B0B0B6; border-color: {BORDER_SOFT};
}}
QPushButton:focus, QToolButton:focus {{ border: 2px solid {ACCENT}; padding: 5px 13px; }}
QToolButton::menu-indicator {{ image: none; }}
QPushButton#primary {{
    background: {ACCENT}; color: #ffffff;
    border: 1px solid {ACCENT}; padding: 8px 20px; font-weight: 600;
}}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#primary:pressed {{ background: #0063C8; border-color: #0063C8; }}
QPushButton#primary:focus {{ border: 2px solid #0059B3; padding: 7px 19px; }}
QPushButton#primary:disabled {{
    background: #CFE3FF; border-color: #CFE3FF; color: #ffffff;
}}
QPushButton#primarySmall {{
    background: {ACCENT_TINT}; color: {ACCENT};
    border: 1px solid #CFE3FF; padding: 5px 12px; font-weight: 600;
}}
QPushButton#primarySmall:hover {{ background: #DCEDFF; border-color: #B9D9FF; }}
QPushButton[dirty="true"] {{
    background: {ACCENT}; color: #ffffff;
    border: 1px solid {ACCENT}; font-weight: 600;
}}
QPushButton[dirty="true"]:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton[dirty="true"]:focus {{ border: 2px solid #0059B3; padding: 5px 13px; }}

/* ---------- 标签页：胶囊分段 ---------- */
QTabWidget::pane {{ border: 0; background: transparent; }}
QTabBar::tab {{
    color: {INK3}; background: #F0F0F3; border: 0;
    border-radius: 15px; padding: 7px 16px; margin: 0 6px 10px 0;
    font-size: 12.5px;
}}
QTabBar::tab:selected {{ background: {ACCENT}; color: #ffffff; font-weight: 600; }}
QTabBar::tab:hover:!selected {{ background: #E7E7EB; color: {INK2}; }}

/* ---------- 表格与列表 ---------- */
QTableView, QTableWidget, QListWidget#results, QListWidget#history {{
    background: {SURFACE}; border: 0; border-radius: 0;
    gridline-color: {BORDER_SOFT}; color: {INK2};
    selection-background-color: {ACCENT_TINT}; selection-color: {INK};
    alternate-background-color: #FAFAFC; outline: none;
}}
QTableView::item, QTableWidget::item {{
    padding: 7px 6px; border-bottom: 1px solid {BORDER_SOFT};
}}
QTableView::item:hover, QTableWidget::item:hover {{ background: #F5F5F8; }}
QTableView::item:selected, QTableWidget::item:selected {{
    background: {ACCENT_TINT}; color: {INK};
}}
QHeaderView::section {{
    background: {SURFACE}; color: {INK3};
    border: 0; border-bottom: 1px solid {BORDER};
    padding: 9px 6px; font-size: {FONT_NOTE}px; font-weight: 600;
}}
QTableCornerButton::section {{ background: {SURFACE}; border: 0; border-bottom: 1px solid {BORDER}; }}
QTableView QLineEdit {{ padding: 4px 6px; border: 1px solid {ACCENT}; border-radius: 6px; background: {SURFACE}; }}
QTableView QComboBox {{ padding: 2px 20px 2px 4px; min-height: 14px; }}

QListWidget#history::item {{
    padding: 18px; border: 1px solid {BORDER};
    margin-bottom: 8px; border-radius: 12px; background: {SURFACE};
}}
QListWidget#history::item:selected {{ background: {ACCENT_TINT}; color: {INK}; border-color: #CFE3FF; }}
QListWidget#results::item {{ padding: 9px 10px; border-bottom: 1px solid {BORDER_SOFT}; }}
QListWidget#results::item:selected {{ background: {ACCENT_TINT}; color: {INK}; }}
QListWidget#problems {{
    background: #FFF7F5; border: 1px solid #F8DCD6; border-radius: 10px; color: {DANGER};
}}
QListWidget#problems::item {{ padding: 6px 10px; }}
QListWidget#problems::item:selected {{ background: #FDEAE6; color: #B3261E; }}

/* ---------- 日志 ---------- */
QPlainTextEdit {{
    background: #1C1C1E; color: #E6E6EA; border: 0; border-radius: 12px;
    font-family: Consolas, "Microsoft YaHei UI"; font-size: 12px;
    padding: 12px; selection-background-color: {ACCENT};
}}

/* ---------- 容器与滚动 ---------- */
QScrollArea {{ background: transparent; border: 0; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:hover {{ background: {BORDER_SOFT}; border-radius: 2px; }}
QProgressBar {{ border: 0; background: {SUNKEN}; max-height: 3px; border-radius: 1px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 1px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: #C7C7CC; min-height: 36px;
    border-radius: 3px; margin: 2px 3px;
}}
QScrollBar::handle:vertical:hover {{ background: #A8A8AE; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{
    background: #C7C7CC; min-width: 36px;
    border-radius: 3px; margin: 3px 2px;
}}
QScrollBar::handle:horizontal:hover {{ background: #A8A8AE; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

/* ---------- 复选框 ---------- */
QCheckBox {{ color: {INK2}; spacing: 8px; font-size: 12.5px; }}
QCheckBox::indicator {{
    width: 18px; height: 18px;
    border: 1px solid #C9C9CF; border-radius: 5px;
    background: {SURFACE};
}}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{
    background: {ACCENT}; border-color: {ACCENT};
    image: url("{CHECK}");
}}
QCheckBox::indicator:checked:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QCheckBox::indicator:disabled {{ background: #F2F2F5; border-color: {BORDER_SOFT}; }}
QCheckBox::indicator:checked:disabled {{ background: #CFE3FF; border-color: #CFE3FF; }}
QCheckBox:disabled {{ color: {INK3}; }}

/* ---------- 菜单、提示、状态栏 ---------- */
QMenu {{
    background: {SURFACE}; color: {INK};
    border: 1px solid {BORDER}; border-radius: 10px; padding: 5px;
}}
QMenu::item {{ padding: 7px 16px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT_TINT}; color: {INK}; }}
QMenu::separator {{ height: 1px; background: {BORDER_SOFT}; margin: 4px 8px; }}
QStatusBar {{
    background: {BG}; color: {INK3}; font-size: {FONT_NOTE}px;
    border-top: 1px solid {BORDER};
}}
QStatusBar::item {{ border: 0; }}
QToolTip {{
    background: #1C1C1E; color: #F2F2F5;
    border: 0; border-radius: 8px; padding: 6px 9px; font-size: 12px;
}}
QLabel#methodNote:disabled {{ color: {INK3}; }}
'''.format(
    CHECK=asset('check.svg'),
    CHEVRON=asset('chevron-down.svg'),
    INK=INK, INK2=INK2, INK3=INK3, BG=BG, SURFACE=SURFACE, SUNKEN=SUNKEN,
    BORDER=BORDER, BORDER_SOFT=BORDER_SOFT, ACCENT=ACCENT,
    ACCENT_HOVER=ACCENT_HOVER, ACCENT_TINT=ACCENT_TINT,
    DANGER=DANGER, FONT_BODY=FONT_BODY, FONT_NOTE=FONT_NOTE,
    FONT_SECTION=FONT_SECTION, FONT_VALUE=FONT_VALUE, FONT_CARD=FONT_CARD,
    FONT_TITLE=FONT_TITLE,
)

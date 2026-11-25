# -*- coding: utf-8 -*-
"""Raman 基准对比模式的独立 Excel 配置。

工作簿第一张表固定为“样品”，另含“参数”和“说明”。只有这个模式读取
compare.xlsx；既有 stack.xlsx 的列和语义保持不变。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import re
import tempfile


DEFAULT_TOLERANCE = 10.0
DEFAULT_NORMALIZE = True
DEFAULT_SHIFT = 0.0
DEFAULT_ZERO_GUARD = 50.0

SAMPLE_HEADERS = ["文件", "图例名称", "启用(0/1)", "峰高阈值", "标注峰位(0/1)"]
PARAMETER_HEADERS = ["参数", "值", "说明"]

PARAMETER_SPECS = (
    ("基准文件", "", "必填：一个两列 TXT，相对于本工作簿或使用绝对路径"),
    ("基准名称", "", "留空使用基准文件名"),
    ("基准峰高阈值", "", "原始 counts；留空按共同窗口自动计算"),
    ("区间下限", "", "与区间上限同时填写或同时留空，单位 cm-1"),
    ("区间上限", "", "留空时使用所有谱的共同交集"),
    ("匹配容差", str(int(DEFAULT_TOLERANCE)), "有限正数，单位 cm-1；边界视为匹配"),
    ("归一化(0/1)", "1", "比较图和残差图逐谱缩放到共同窗口 0-1，不改变原始 H 检测"),
    ("峰位校正", "0", "同时加到基准与全部样品，单位 cm-1"),
    ("激光线排除半宽", str(int(DEFAULT_ZERO_GUARD)), "以校正后零位为中心，0 表示关闭"),
)

SAMPLE_ALIASES = {
    "file": ("文件", "文件名", "光谱文件", "file"),
    "label": ("图例名称", "图例", "名称", "label"),
    "enabled": ("启用", "启用(0/1)", "参与分析", "enabled"),
    "height": ("峰高阈值", "峰高", "阈值", "height"),
    "peaks": ("标注峰位", "峰位", "标注峰位(0/1)", "peaks"),
}
PARAMETER_ALIASES = {
    "reference": ("基准文件", "基准谱", "参考文件", "reference"),
    "reference_label": ("基准名称", "参考名称", "reference_label", "reference_label"),
    "reference_height": ("基准峰高阈值", "基准阈值", "reference_height"),
    "xmin": ("区间下限", "起点", "xmin"),
    "xmax": ("区间上限", "终点", "xmax"),
    "tolerance": ("匹配容差", "峰位容差", "tolerance"),
    "normalize": ("归一化", "归一化(0/1)", "normalize"),
    "shift": ("峰位校正", "校正量", "shift"),
    "zero_guard": ("激光线排除半宽", "零位排除半宽", "zero_guard"),
}


class CompareConfigError(ValueError):
    """配置或路径不满足对比模式要求。"""


@dataclass
class CompareSample:
    file: str
    label: str = ""
    enabled: bool = True
    height: float | None = None
    peaks: bool = True
    source_row: int | None = None

    @property
    def display_label(self):
        return label_text(self.label, Path(self.file).stem if self.file else "")


@dataclass
class CompareConfig:
    path: Path
    samples: list[CompareSample] = field(default_factory=list)
    reference: str = ""
    reference_label: str = ""
    reference_height: float | None = None
    xmin: float | None = None
    xmax: float | None = None
    tolerance: float = DEFAULT_TOLERANCE
    normalize: bool = DEFAULT_NORMALIZE
    shift: float = DEFAULT_SHIFT
    zero_guard: float = DEFAULT_ZERO_GUARD

    @property
    def active_samples(self):
        return [sample for sample in self.samples if sample.enabled]

    @property
    def reference_display_label(self):
        return label_text(self.reference_label, Path(self.reference).stem if self.reference else "")

    def reference_path(self):
        text = str(self.reference or "").strip()
        if not text:
            raise CompareConfigError("参数表的“基准文件”不能为空")
        path = Path(text)
        if not path.is_absolute():
            path = self.path.parent / path
        if not path.exists() and path.suffix.casefold() != ".txt":
            path = Path(str(path) + ".txt")
        return path.resolve()


def label_text(label, fallback=""):
    """CSV 用单行文字；竖线只在绘图图例里换行。"""
    value = str(label or "").strip()
    if not value:
        value = str(fallback or "").strip()
    return value.replace("|", " / ")


def normalize_header(value):
    text = "" if value is None else str(value)
    text = re.sub(r"[(（][^)）]*[)）]", "", text)
    text = text.replace(" ", "").replace("\u3000", "").strip().casefold()
    return text


def parse_bool(value, default, title):
    if value is None or str(value).strip() == "":
        return default
    word = str(value).strip().casefold()
    if word in ("1", "1.0", "y", "yes", "true", "是", "启用", "on"):
        return True
    if word in ("0", "0.0", "n", "no", "false", "否", "关闭", "off"):
        return False
    raise CompareConfigError(f"{title}请填写 0/1、启用或关闭")


def parse_number(value, title, *, positive=False, nonnegative=False):
    if value is None or str(value).strip() == "":
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        raise CompareConfigError(f"{title}需要数字") from None
    if not math.isfinite(number):
        raise CompareConfigError(f"{title}需要有限数字")
    if positive and number <= 0:
        raise CompareConfigError(f"{title}必须大于 0")
    if nonnegative and number < 0:
        raise CompareConfigError(f"{title}不能小于 0")
    return float(number)


def warn_if_excel_open(path):
    folder, name = os.path.split(os.path.abspath(path))
    lock = os.path.join(folder, "~$" + name)
    if os.path.exists(lock):
        print("提示：%s 正在 Excel 中打开；本次读取磁盘上已保存的版本，"
              "Excel 里未保存的修改不会生效。建议保存并关闭后重跑。" % name)


def _sheet_after_header(ws, max_rows=20001, max_cols=256):
    """返回表头和非空行；调用方负责关闭工作簿。"""
    rows = list(ws.iter_rows(values_only=True))
    if len(rows) > max_rows:
        raise CompareConfigError("配置表超过 20000 行，请检查所选文件")
    header = None
    body = []
    for raw in rows:
        cells = ["" if cell is None else str(cell).strip() for cell in raw]
        if not any(cells):
            continue
        if header is None:
            if len(cells) > max_cols:
                raise CompareConfigError("配置表超过 256 列，请检查所选文件")
            header = cells
            continue
        if cells[0].startswith("#"):
            continue
        body.append((cells, None))
    if header is None:
        raise CompareConfigError("配置没有表头")
    return header, body


def _sample_columns(header):
    lookup = {}
    for key, aliases in SAMPLE_ALIASES.items():
        for alias in aliases:
            lookup[normalize_header(alias)] = key
    columns = {}
    for index, name in enumerate(header):
        key = lookup.get(normalize_header(name))
        if key:
            if key in columns:
                raise CompareConfigError(f"存在重复样品列：{name}")
            columns[key] = index
    if "file" not in columns:
        raise CompareConfigError("样品表未找到“文件”列")
    return columns


def _parameter_columns(header):
    first = normalize_header(header[0]) if header else ""
    second = normalize_header(header[1]) if len(header) > 1 else ""
    if first not in ("参数", "parameter", "key") or second not in ("值", "value"):
        raise CompareConfigError("参数表前两列应为“参数、值”")
    return 0, 1


def _parameter_key(value):
    normalized = normalize_header(value)
    for key, aliases in PARAMETER_ALIASES.items():
        for alias in aliases:
            if normalized == normalize_header(alias):
                return key
    return None


def load_compare_config(path):
    """读取并完整校验 compare.xlsx；任何未知参数均不静默忽略。"""
    path = Path(path).resolve()
    if not path.is_file() or path.suffix.casefold() != ".xlsx":
        raise CompareConfigError("请选择存在的 compare.xlsx 配置表")
    warn_if_excel_open(path)
    try:
        import openpyxl
    except ImportError:
        raise CompareConfigError("读取 compare.xlsx 需要 openpyxl") from None
    wb = openpyxl.load_workbook(path, data_only=False, read_only=True)
    try:
        if "参数" not in wb.sheetnames:
            raise CompareConfigError("compare.xlsx 缺少“参数”工作表")
        sample_ws = wb["样品"] if "样品" in wb.sheetnames else wb.worksheets[0]
        header, body = _sheet_after_header(sample_ws)
        columns = _sample_columns(header)
        samples = []
        for cells, _ in body:
            if any(str(cell).startswith("=") for cell in cells if cell not in (None, "")):
                raise CompareConfigError("样品表包含公式；请填写实际值")
            file_col = columns["file"]
            name = cells[file_col] if file_col < len(cells) else ""
            if not name:
                if any(cells):
                    raise CompareConfigError(f"样品表第 {len(samples) + 2} 行有内容但文件为空")
                continue
            get = lambda key: cells[columns[key]] if key in columns and columns[key] < len(cells) else ""
            try:
                enabled = parse_bool(get("enabled"), True, "启用")
                height = parse_number(get("height"), "峰高阈值")
                peaks = parse_bool(get("peaks"), True, "标注峰位")
            except CompareConfigError as exc:
                raise CompareConfigError(f"样品 {name}：{exc}") from None
            samples.append(CompareSample(name, get("label"), enabled, height, peaks,
                                         len(samples) + 2))
        if not samples:
            raise CompareConfigError("样品表没有可用数据行")

        param_header, param_body = _sheet_after_header(wb["参数"])
        pkey, pvalue = _parameter_columns(param_header)
        params = {}
        for cells, _ in param_body:
            if any(str(cell).startswith("=") for cell in cells if cell not in (None, "")):
                raise CompareConfigError("参数表包含公式；请填写实际值")
            key = _parameter_key(cells[pkey] if pkey < len(cells) else "")
            if key is None:
                name = cells[pkey] if pkey < len(cells) else ""
                raise CompareConfigError(f"未知参数：{name}")
            if key in params:
                raise CompareConfigError(f"参数重复：{cells[pkey]}")
            params[key] = cells[pvalue] if pvalue < len(cells) else ""
    finally:
        wb.close()

    reference = str(params.get("reference") or "").strip()
    if not reference:
        raise CompareConfigError("参数表的“基准文件”不能为空")
    xmin = parse_number(params.get("xmin"), "区间下限")
    xmax = parse_number(params.get("xmax"), "区间上限")
    if (xmin is None) != (xmax is None):
        raise CompareConfigError("区间下限和区间上限必须同时填写或同时留空")
    if xmin is not None and xmin >= xmax:
        raise CompareConfigError("区间下限必须小于区间上限")
    tolerance = parse_number(params.get("tolerance"), "匹配容差", positive=True)
    zero_guard = parse_number(params.get("zero_guard"), "激光线排除半宽", nonnegative=True)
    shift = parse_number(params.get("shift"), "峰位校正")
    normalize = parse_bool(params.get("normalize"), DEFAULT_NORMALIZE, "归一化")
    reference_height = parse_number(params.get("reference_height"), "基准峰高阈值")
    return CompareConfig(
        path=path,
        samples=samples,
        reference=reference,
        reference_label=str(params.get("reference_label") or "").strip(),
        reference_height=reference_height,
        xmin=xmin,
        xmax=xmax,
        tolerance=DEFAULT_TOLERANCE if tolerance is None else tolerance,
        normalize=normalize,
        shift=DEFAULT_SHIFT if shift is None else shift,
        zero_guard=DEFAULT_ZERO_GUARD if zero_guard is None else zero_guard,
    )


def _atomic_save(workbook, path):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix="." + path.stem + "-", suffix=".xlsx",
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            workbook.save(stream)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_compare_workbook(path, samples=None, params=None, *, overwrite=False):
    """生成 compare.xlsx；默认拒绝覆盖用户已有工作簿。"""
    try:
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.worksheet.datavalidation import DataValidation
    except ImportError:
        raise CompareConfigError("生成 compare.xlsx 需要 openpyxl") from None
    path = Path(path).resolve()
    if path.suffix.casefold() != ".xlsx":
        raise CompareConfigError("对比配置请使用 .xlsx 扩展名")
    if path.exists() and not overwrite:
        raise CompareConfigError(f"目标已存在，未覆盖：{path}")
    if path.exists() and not os.access(path, os.W_OK):
        raise CompareConfigError(f"目标没有写入权限：{path}")
    wb = openpyxl.Workbook()
    try:
        ws = wb.active
        ws.title = "样品"
        ws.append(SAMPLE_HEADERS)
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="315B50")
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for sample in samples or []:
            if isinstance(sample, CompareSample):
                row = [sample.file, sample.label, int(sample.enabled), sample.height,
                       int(sample.peaks)]
            else:
                row = list(sample)
            ws.append(row)
        widths = {"A": 34, "B": 24, "C": 12, "D": 14, "E": 16}
        for column, width in widths.items():
            ws.column_dimensions[column].width = width
        ws.freeze_panes = "B2"
        ws.auto_filter.ref = ws.dimensions
        ws.sheet_view.showGridLines = False

        enabled_rule = DataValidation(type="list", formula1='"0,1"', allow_blank=True)
        peaks_rule = DataValidation(type="list", formula1='"0,1"', allow_blank=True)
        for rule, column in ((enabled_rule, "C"), (peaks_rule, "E")):
            ws.add_data_validation(rule)
            rule.add(f"{column}2:{column}1048576")

        ps = wb.create_sheet("参数")
        ps.append(PARAMETER_HEADERS)
        for cell in ps[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="315B50")
            cell.alignment = Alignment(horizontal="center", vertical="center")
        values = dict(params or {})
        for key, default, note in PARAMETER_SPECS:
            internal = _parameter_key(key)
            value = values.get(key, values.get(internal, default)) if values else default
            ps.append([key, "" if value is None else value, note])
        ps.column_dimensions["A"].width = 22
        ps.column_dimensions["B"].width = 45
        ps.column_dimensions["C"].width = 72
        ps.freeze_panes = "A2"
        ps.sheet_view.showGridLines = False

        info = wb.create_sheet("说明")
        lines = [
            ("Raman 基准对比分析", True),
            ("", False),
            ("用法：run.bat --compare .\\compare.xlsx，或在桌面工作台选择“基准对比”。", False),
            ("一次运行使用一个基准谱和任意多个样品谱；基准文件可在参数表中填相对或绝对路径。", False),
            ("样品表“文件”相对 --src（默认 data），可省略 .txt；样品峰高阈值留空时自动计算。", False),
            ("匹配只表示峰位差不超过容差，不代表物相鉴定；相关系数也不是鉴定概率。", False),
            ("不做自动峰位对齐；峰位校正会同时作用于基准和全部样品。", False),
            ("输出为 compare_summary.csv、compare_peaks.csv、compare_reference_peaks.csv、"
             "逐样品 compare_<文件名>.png、compare_shift.png 和 compare_run.json。", False),
            ("缺少对应峰位或 H 的单元格留空，不写 0；全部峰参与计算，绘图标注可单独减少。", False),
            ("", False),
            ("参数说明", True),
        ]
        for text, bold in lines:
            info.append([text])
            if bold:
                info.cell(info.max_row, 1).font = Font(bold=True, size=12)
        info.column_dimensions["A"].width = 110
        _atomic_save(wb, path)
        return path
    finally:
        wb.close()

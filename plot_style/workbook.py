# -*- coding: utf-8 -*-
"""Additive-only openpyxl workbook upgrade helpers.

This helper appends missing rows to an existing setting sheet.  It never
deletes, rewrites or reformats existing cells, never moves columns, and keeps
all user sheets and values intact.  It does not rewrite workbook cells.
"""
from __future__ import annotations

from copy import copy
import os
from pathlib import Path
from typing import Any, Optional, Union

from openpyxl import Workbook, load_workbook

from .core import STYLE_FIELDS, STYLE_SCHEMA_VERSION

PathOrWorkbook = Union[str, os.PathLike, Workbook]
_SHEET_NAMES = ("设置", "设置表", "配置", "样式设置", "Settings", "PlotStyle")
_HEADER_CANDIDATES = ("参数", "字段", "parameter", "field", "name", "名称", "style field")
_VALUE_CANDIDATES = ("值", "设定值", "value", "setting")
_NOTE_Candidates = ("说明", "备注", "description", "note")
_SUGGESTIONS = {
    "style_preset": "paper",
    "font": "",
    "figure_title_font_size_pt": 8,
    "axis_title_font_size_pt": 8,
    "tick_font_size_pt": 7,
    "legend_font_size_pt": 7,
    "value_label_font_size_pt": 8,
    "peak_label_font_size_pt": 10,
    "min_peak_label_font_size_pt": 8,
    "line_width_pt": 0.75,
    "axis_line_width_pt": 0.7,
    "legend_location": "auto",
    "legend_text_color": "#222222",
    "series_colors": "",
    "palette": "",
    "background_color": "#FFFFFF",
    "grid_enabled": "否",
    "grid_color": "#D9D9D9",
    "dpi": 600,
    "output_formats": "png;pdf;svg",
}
_NOTES = {
    "min_peak_label_font_size_pt": "标注字号下限，有效范围 6-24 pt；不得设为 5。",
    "dpi": "输出分辨率，有效范围 150-1200 dpi；Raman 200 dpi 为项目级兼容设置。",
    "series_colors": "多个颜色用英文或中文分号分隔；留空继承当前 palette。",
    "grid_enabled": "是/否、1/0 或 true/false；留空继承项目默认。",
}


def _pick_setting_sheet(wb: Workbook, template_rows: Optional[list]) -> tuple:
    if template_rows:
        for row in template_rows:
            for sheet_name in _SHEET_NAMES:
                if sheet_name in wb.sheetnames:
                    return wb[sheet_name], list(row)
        return wb.create_sheet("设置表"), list(template_rows[0])
    for sheet_name in _SHEET_NAMES:
        if sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]
            headers = [str(cell.value).strip().lower() if cell.value is not None else ""
                       for cell in sheet[1]]
            if any(candidate in headers for candidate in _HEADER_CANDIDATES):
                return sheet, headers
            return sheet, headers
    return wb.create_sheet("设置表"), ["参数", "值", "单位", "说明"]

def _columns(headers: list) -> tuple:
    def find(candidates: tuple) -> Optional[int]:
        for index, header in enumerate(headers, start=1):
            text = str(header or "").strip().lower()
            if text in candidates:
                return index
        return None
    name_col = find(_HEADER_CANDIDATES) or 1
    value_col = find(_VALUE_CANDIDATES) or min(name_col + 1, max(len(headers) + 1, 2))
    note_col = find(_NOTE_Candidates) or max(value_col + 1, len(headers) + 1)
    return name_col, value_col, note_col

def _existing_fields(sheet, name_col: int) -> set:
    existing = set()
    for row in range(2, sheet.max_row + 1):
        value = sheet.cell(row=row, column=name_col).value
        if value is None:
            continue
        text = str(value).strip().lower()
        existing.add(text)
        existing.add(text.replace(" ", "_"))
        if text.endswith("_pt"):
            existing.add(text[:-3])
    return existing

def _safe_output(input_path: Optional[Path], output_path: Union[str, os.PathLike, None],
                 allow_overwrite: bool) -> Path:
    if output_path is not None:
        target = Path(output_path)
        if input_path is not None and target.resolve() == input_path.resolve() and not allow_overwrite:
            raise ValueError("output_path 显式指向输入文件；如需原地覆盖请传入 allow_overwrite=True")
        return target
    if input_path is None:
        raise ValueError("传入 Workbook 对象时必须提供 output_path")
    stem, suffix = input_path.stem, input_path.suffix or ".xlsx"
    candidate = input_path.with_name("%s.upgraded%s" % (stem, suffix))
    counter = 2
    while candidate.exists():
        candidate = input_path.with_name("%s.upgraded%d%s" % (stem, counter, suffix))
        counter += 1
    return candidate

def upgrade_workbook(path_or_workbook: PathOrWorkbook,
                     output_path: Union[str, os.PathLike, None] = None,
                     template_rows: Optional[list] = None,
                     *, allow_overwrite: bool = False) -> dict:
    """Append missing P1 style rows to a setting sheet, preserving the workbook.

    ``path_or_workbook`` may be a workbook path or an openpyxl Workbook.  By
    default a new ``*.upgraded*.xlsx`` path is chosen; a path input is never
    overwritten unless ``output_path`` explicitly names it and
    ``allow_overwrite=True``.  Existing cells, styles, column order, user
    values and additional sheets are untouched; only missing rows are appended
    so repeated calls are idempotent.  This function does not rewrite workbook
    cells or rebuild the workbook.
    """
    input_path = Path(path_or_workbook) if isinstance(path_or_workbook, (str, os.PathLike)) else None
    wb = load_workbook(input_path, data_only=False) if input_path is not None else path_or_workbook
    if not isinstance(wb, Workbook):
        raise TypeError("path_or_workbook 必须是路径或 openpyxl Workbook")
    target = _safe_output(input_path, output_path, allow_overwrite)
    sheet, headers = _pick_setting_sheet(wb, template_rows)
    name_col, value_col, note_col = _columns(headers)
    existing = _existing_fields(sheet, name_col)
    appended = []
    for field in STYLE_FIELDS:
        if field in existing:
            continue
        row = sheet.max_row + 1
        name_cell = sheet.cell(row=row, column=name_col)
        name_cell.value = field
        if name_col <= len(headers) and headers[name_col - 1: name_col]:
            name_cell._style = copy(sheet.cell(row=sheet.max_row - 1, column=name_col)._style) if sheet.max_row > 1 else name_cell._style
        value_cell = sheet.cell(row=row, column=value_col)
        value_cell.value = _SUGGESTIONS[field]
        note_cell = sheet.cell(row=row, column=note_col)
        note_cell.value = _NOTES.get(field, "P1 样式字段；留空继承项目默认。")
        appended.append(field)
    target.parent.mkdir(parents=True, exist_ok=True)
    wb.save(target)
    return {
        "output_path": str(target),
        "sheet": sheet.title,
        "appended": tuple(appended),
        "schema_version": STYLE_SCHEMA_VERSION,
    }

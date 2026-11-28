# -*- coding: utf-8 -*-
"""Resolve shared plot styles from layered settings without project imports."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import warnings
from typing import Any, Optional, Tuple

from .core import (
    FigureStyle, PAPER_STYLE, STYLE_FIELDS, StyleInputError,
    normalize_settings, parse_bool, parse_color, parse_color_list,
    parse_enum, parse_number, parse_output_formats,
)

ALLOWED_SOURCES = ("schema", "default", "project", "excel", "row", "cli")
_PT_FIELDS = {name for name in STYLE_FIELDS if name.endswith("_pt")}
_COLOR_FIELDS = {"legend_text_color", "background_color", "grid_color"}
_ENUM_FIELDS = {"style_preset", "legend_location"}


def _as_mapping(value: Any, label: str) -> dict:
    if value is None:
        return {}
    if isinstance(value, FigureStyle):
        return value.to_dict()
    if not isinstance(value, Mapping):
        raise StyleInputError("%s 必须是映射或 FigureStyle，收到 %r" % (label, type(value).__name__))
    return dict(value)

def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return not value
    return False

def _loc(location: Any, original: str, field: str) -> Optional[str]:
    if location is None:
        return None
    if isinstance(location, Mapping):
        text = location.get(original) or location.get(field) or location.get("default")
    else:
        text = str(location)
    return str(text) if text else None

def _index_loc(location: Optional[str], index: int) -> Optional[str]:
    return "%s 第 %d 项" % (location, index) if location else "第 %d 项" % index

def _convert(field: str, value: Any, original: str,
             location: Optional[str]) -> Any:
    parameter = original or field
    if field in _PT_FIELDS:
        lower, upper = (6.0, 24.0) if field == "min_peak_label_font_size_pt" else (0.1, 72.0)
        return parse_number(value, parameter, location, min_value=lower,
                            max_value=upper, units=("pt",))
    if field == "dpi":
        return parse_number(value, parameter, location, min_value=150,
                            max_value=1200, integer=True, units=("dpi",))
    if field in _COLOR_FIELDS:
        return parse_color(value, parameter, location)
    if field == "series_colors":
        if isinstance(value, str):
            return parse_color_list(value, parameter, location)
        if isinstance(value, Sequence):
            if not value:
                return None
            return tuple(parse_color(item, parameter, _index_loc(location, index))
                         for index, item in enumerate(value, start=1))
        raise StyleInputError("%s 必须是分号分隔字符串或颜色序列" % (
            "参数 %r 的值 %r" % (parameter, value) + ("，位置 %s" % location if location else "")))
    if field == "output_formats":
        if isinstance(value, str):
            return parse_output_formats(value, parameter, location)
        if isinstance(value, Sequence):
            if not value:
                return None
            parsed = tuple(parse_enum(item, ("png", "pdf", "svg"), parameter,
                                      _index_loc(location, index))
                           for index, item in enumerate(value, start=1))
            if len(set(parsed)) != len(parsed):
                raise StyleInputError("%s 包含重复格式" % (
                    "参数 %r 的值 %r" % (parameter, value) + ("，位置 %s" % location if location else "")))
            return parsed
        raise StyleInputError("%s 必须是格式字符串或序列" % (
            "参数 %r 的值 %r" % (parameter, value) + ("，位置 %s" % location if location else "")))
    if field == "grid_enabled":
        return parse_bool(value, parameter, location)
    if field in _ENUM_FIELDS:
        allowed = ("paper",) if field == "style_preset" else (
            "auto", "best", "upper right", "upper left", "outside right")
        return parse_enum(value, allowed, parameter, location)
    if field in ("font", "palette"):
        text = str(value).strip()
        return text or None
    raise StyleInputError("未知样式字段 %r（原始参数 %r）" % (field, parameter))

def _apply_layer(values: dict, sources: dict, mapping: Mapping, source: str,
                 location: Any, unknown_warnings: list) -> None:
    normalized, warnings_list = normalize_settings(mapping, location=location)
    for warning in warnings_list:
        unknown_warnings.append("[%s] %s" % (source, warning))
    for field, (original, value) in normalized.items():
        if _is_blank(value):
            continue
        converted = _convert(field, value, original, _loc(location, original, field))
        values[field] = converted
        sources[field] = source

def resolve_style(excel_settings: Any = None, project_defaults: Any = None,
                  explicit_overrides: Any = None, source_name: str = "excel",
                  *, strict: bool = False, location: Any = None,
                  row_settings: Any = None) -> Tuple[FigureStyle, dict]:
    """Resolve a FigureStyle and per-field source map.

    Precedence: dataclass schema defaults < project_defaults < excel_settings
    < optional row_settings < explicit_overrides (CLI).  Blank/None values
    inherit from the lower layer and are not recorded as overrides.
    Unknown keys are ignored with a UserWarning by default; with strict=True
    they raise StyleInputError listing every unknown parameter.
    ``location`` may be a string such as "设置表!C4" or a mapping from raw
    parameter name (or canonical field) to a cell address.
    """
    if source_name not in ("excel", "row"):
        raise StyleInputError("source_name 的值 %r 无效；允许 excel/row" % (source_name,))
    values = PAPER_STYLE.to_dict()
    sources = {field: "schema" for field in STYLE_FIELDS}
    unknown_warnings: list = []

    _apply_layer(values, sources, _as_mapping(project_defaults, "project_defaults"),
                 "project", location, unknown_warnings)

    excel = _as_mapping(excel_settings, "excel_settings")
    nested_rows = excel.pop("row_settings", None) if "row_settings" in excel else None
    if nested_rows is None and "row" in excel:
        nested_rows = excel.pop("row")
    _apply_layer(values, sources, excel, source_name, location, unknown_warnings)
    if row_settings is not None or nested_rows is not None:
        _apply_layer(values, sources, _as_mapping(nested_rows if nested_rows is not None else row_settings,
                                                  "row_settings"),
                     "row", location, unknown_warnings)
    _apply_layer(values, sources, _as_mapping(explicit_overrides, "explicit_overrides"),
                 "cli", location, unknown_warnings)

    if unknown_warnings:
        message = "；".join(unknown_warnings)
        if strict:
            raise StyleInputError(message)
        warnings.warn(message, UserWarning, stacklevel=2)
    if set(sources.values()) - set(ALLOWED_SOURCES):
        raise StyleInputError("内部错误：存在非法来源标记")
    return FigureStyle(**values), sources

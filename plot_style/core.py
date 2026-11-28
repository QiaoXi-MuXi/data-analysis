# -*- coding: utf-8 -*-
"""Framework-free style data model and parsers for shared plot settings."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
import math
import re
import unicodedata
from typing import Any, Optional, Tuple

try:
    import numpy as np
except ImportError:
    np = None

STYLE_SCHEMA_VERSION = 1
PAPER_FONT_DEFAULT = None
PAPER_PALETTE_DEFAULT = None

STYLE_FIELDS = (
    "style_preset", "font", "figure_title_font_size_pt", "axis_title_font_size_pt",
    "tick_font_size_pt", "legend_font_size_pt", "value_label_font_size_pt",
    "peak_label_font_size_pt", "min_peak_label_font_size_pt", "line_width_pt",
    "axis_line_width_pt", "legend_location", "legend_text_color", "series_colors",
    "palette", "background_color", "grid_enabled", "grid_color", "dpi",
    "output_formats",
)

FIELD_ALIASES = {
    "样式预设": "style_preset", "style_preset": "style_preset",
    "字体": "font", "font": "font",
    "图标题字号_pt": "figure_title_font_size_pt",
    "图标题字号pt": "figure_title_font_size_pt",
    "图标题字号": "figure_title_font_size_pt",
    "figure_title_font_size_pt": "figure_title_font_size_pt",
    "figure_title_font_size": "figure_title_font_size_pt",
    "轴标题字号_pt": "axis_title_font_size_pt",
    "轴标题字号pt": "axis_title_font_size_pt",
    "轴标题字号": "axis_title_font_size_pt",
    "axis_title_font_size_pt": "axis_title_font_size_pt",
    "axis_title_font_size": "axis_title_font_size_pt",
    "刻度字号_pt": "tick_font_size_pt",
    "刻度字号pt": "tick_font_size_pt",
    "刻度字号": "tick_font_size_pt",
    "tick_font_size_pt": "tick_font_size_pt",
    "tick_font_size": "tick_font_size_pt",
    "图例字号_pt": "legend_font_size_pt",
    "图例字号pt": "legend_font_size_pt",
    "图例字号": "legend_font_size_pt",
    "legend_font_size_pt": "legend_font_size_pt",
    "legend_font_size": "legend_font_size_pt",
    "数值标注字号_pt": "value_label_font_size_pt",
    "数值标注字号pt": "value_label_font_size_pt",
    "数值标注字号": "value_label_font_size_pt",
    "value_label_font_size_pt": "value_label_font_size_pt",
    "value_label_font_size": "value_label_font_size_pt",
    "峰值标注字号_pt": "peak_label_font_size_pt",
    "峰值标注字号pt": "peak_label_font_size_pt",
    "峰值标注字号": "peak_label_font_size_pt",
    "peak_label_font_size_pt": "peak_label_font_size_pt",
    "peak_label_font_size": "peak_label_font_size_pt",
    "最小标注字号_pt": "min_peak_label_font_size_pt",
    "最小标注字号pt": "min_peak_label_font_size_pt",
    "最小标注字号": "min_peak_label_font_size_pt",
    "min_peak_label_font_size_pt": "min_peak_label_font_size_pt",
    "min_peak_label_font_size": "min_peak_label_font_size_pt",
    "线宽_pt": "line_width_pt", "线宽pt": "line_width_pt", "线宽": "line_width_pt",
    "line_width_pt": "line_width_pt", "line_width": "line_width_pt",
    "轴线宽_pt": "axis_line_width_pt", "轴线宽pt": "axis_line_width_pt",
    "轴线宽": "axis_line_width_pt", "axis_line_width_pt": "axis_line_width_pt",
    "axis_line_width": "axis_line_width_pt",
    "图例位置": "legend_location", "legend_location": "legend_location",
    "图例文字颜色": "legend_text_color", "legend_text_color": "legend_text_color",
    "系列颜色": "series_colors", "series_colors": "series_colors",
    "配色": "palette", "palette": "palette",
    "背景颜色": "background_color", "background_color": "background_color",
    "网格线": "grid_enabled", "grid_enabled": "grid_enabled",
    "网格颜色": "grid_color", "grid_color": "grid_color",
    "分辨率_dpi": "dpi", "分辨率dpi": "dpi", "分辨率": "dpi", "dpi": "dpi",
    "输出格式": "output_formats", "output_formats": "output_formats",
}

HEX_COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}\Z")
HINT_RE = re.compile(r"[（(][^（）()]*?[）)]\Z")
STYLE_PRESETS = ("paper",)
LEGEND_LOCATIONS = ("auto", "best", "upper right", "upper left", "outside right")
OUTPUT_FORMATS = ("png", "pdf", "svg")

_DEFAULT_CONTEXT = {
    "style_preset": "paper", "font": PAPER_FONT_DEFAULT,
    "figure_title_font_size_pt": 8.0, "axis_title_font_size_pt": 8.0,
    "tick_font_size_pt": 7.0, "legend_font_size_pt": 7.0,
    "value_label_font_size_pt": 8.0, "peak_label_font_size_pt": 10.0,
    "min_peak_label_font_size_pt": 8.0, "line_width_pt": 0.75,
    "axis_line_width_pt": 0.7, "legend_location": "auto",
    "legend_text_color": "#222222", "series_colors": None,
    "palette": PAPER_PALETTE_DEFAULT, "background_color": "#FFFFFF",
    "grid_enabled": False, "grid_color": "#D9D9D9", "dpi": 600,
    "output_formats": None,
}


class StyleInputError(ValueError):
    """Raised when a style value cannot be parsed unambiguously."""

@dataclass(frozen=True)
class FigureStyle:
    """Immutable resolved plot style with explicit units in field names.

    Values ending in _pt are typographic points, dpi is dots per inch, and any
    future image-size field must end in _cm.  font, palette, series_colors and
    output_formats may be None to mean "inherit the project default".
    """

    style_preset: str = "paper"
    font: Optional[str] = PAPER_FONT_DEFAULT
    figure_title_font_size_pt: float = 8.0
    axis_title_font_size_pt: float = 8.0
    tick_font_size_pt: float = 7.0
    legend_font_size_pt: float = 7.0
    value_label_font_size_pt: float = 8.0
    peak_label_font_size_pt: float = 10.0
    min_peak_label_font_size_pt: float = 8.0
    line_width_pt: float = 0.75
    axis_line_width_pt: float = 0.7
    legend_location: str = "auto"
    legend_text_color: str = "#222222"
    series_colors: Optional[Tuple[str, ...]] = None
    palette: Optional[str] = PAPER_PALETTE_DEFAULT
    background_color: str = "#FFFFFF"
    grid_enabled: bool = False
    grid_color: str = "#D9D9D9"
    dpi: int = 600
    output_formats: Optional[Tuple[str, ...]] = None

    def __post_init__(self) -> None:
        if self.style_preset not in STYLE_PRESETS:
            raise StyleInputError("参数 style_preset 的值 %r 无效；允许值：%s" % (self.style_preset, list(STYLE_PRESETS)))
        if self.legend_location not in LEGEND_LOCATIONS:
            raise StyleInputError("参数 legend_location 的值 %r 无效；允许值：%s" % (self.legend_location, list(LEGEND_LOCATIONS)))
        if not 6 <= self.min_peak_label_font_size_pt <= 24:
            raise StyleInputError("参数 min_peak_label_font_size_pt 的值 %r 超出 6-24 pt" % (self.min_peak_label_font_size_pt,))
        if not 150 <= self.dpi <= 1200:
            raise StyleInputError("参数 dpi 的值 %r 超出 150-1200" % (self.dpi,))
        for name, value in self.to_dict().items():
            if name.endswith("_color") and value is not None and not HEX_COLOR_RE.fullmatch(str(value)):
                raise StyleInputError("参数 %s 的值 %r 无效；必须是 #RRGGBB" % (name, value))
            if name.endswith("_pt"):
                invalid = (isinstance(value, bool) or not isinstance(value, (int, float))
                           or not math.isfinite(float(value)) or value <= 0)
                if invalid:
                    raise StyleInputError("参数 %s 的值 %r 无效；必须是正数" % (name, value))

    def to_dict(self) -> dict:
        """Return a shallow, serializable snapshot of all stable fields."""
        return {field.name: getattr(self, field.name) for field in fields(self)}

    def __repr__(self) -> str:
        parts = ", ".join("%s=%r" % (key, value) for key, value in self.to_dict().items())
        return "FigureStyle(%s)" % parts

PAPER_STYLE = FigureStyle()

def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())

def _describe(value: Any, parameter: str, location: Optional[str]) -> str:
    suffix = "，位置 %s" % location if location else ""
    return "参数 %r 的值 %r%s" % (parameter, value, suffix)

def normalize_header(value: Any) -> str:
    """Normalize an Excel header to a canonical English style field name.

    Surrounding/full-width spaces are removed, a trailing parenthesized unit
    hint such as （pt） is stripped, ASCII text is lower-cased, and Chinese
    aliases are mapped.  Unknown headers are returned normalized but unchanged.
    """
    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("\u3000", " ").strip()
    text = HINT_RE.sub("", text).strip()
    return FIELD_ALIASES.get(text.lower(), text.lower())

def _strip_unit(value: Any, allowed_units: Tuple[str, ...], parameter: str,
                location: Optional[str]) -> str:
    text = str(value).strip().replace("\u3000", " ").replace("\xa0", " ")
    match = re.fullmatch(r"([+-]?[0-9]+(?:\.[0-9]*)?|[+-]?\.[0-9]+)\s*([A-Za-zμμ%°]+)?", text)
    if not match:
        raise StyleInputError("%s 不是有效数值" % _describe(value, parameter, location))
    number_text, unit = match.group(1), match.group(2)
    if unit and unit.lower() not in {item.lower() for item in allowed_units}:
        raise StyleInputError("%s 的单位 %r 不受支持" % (_describe(value, parameter, location), unit))
    return number_text

def parse_bool(value: Any, parameter: str = "boolean", location: Optional[str] = None) -> bool:
    """Parse 是/否, 1/0, true/false; blank or ambiguous values raise."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if np is not None and isinstance(value, np.integer) and int(value) in (0, 1):
        return bool(int(value))
    text = str(value).strip().replace("\u3000", " ").lower()
    if text in ("是", "真", "1", "true", "yes", "y", "t"):
        return True
    if text in ("否", "假", "0", "false", "no", "n", "f"):
        return False
    raise StyleInputError("%s 不是布尔值；允许 是/否、1/0、true/false" % _describe(value, parameter, location))

def parse_number(value: Any, parameter: str = "number", location: Optional[str] = None,
                 *, min_value: Optional[float] = None, max_value: Optional[float] = None,
                 integer: bool = False, units: Tuple[str, ...] = ()) -> Any:
    """Parse a finite number, optionally with one of ``units``, and range-check it."""
    if isinstance(value, bool):
        raise StyleInputError("%s 不是数值" % _describe(value, parameter, location))
    numeric_types = (int, float)
    if np is not None:
        numeric_types = (int, float, np.integer, np.floating)
    if isinstance(value, numeric_types):
        number = float(value)
    elif units:
        number = float(_strip_unit(value, units, parameter, location))
    else:
        text = str(value).strip().replace("\u3000", " ").replace("\xa0", " ")
        try:
            number = float(text)
        except (TypeError, ValueError, OverflowError) as exc:
            raise StyleInputError("%s 不是有效数值" % _describe(value, parameter, location)) from exc
    if not math.isfinite(number):
        raise StyleInputError("%s 不是有限数值" % _describe(value, parameter, location))
    if min_value is not None and number < min_value:
        raise StyleInputError("%s 小于最小值 %s" % (_describe(value, parameter, location), min_value))
    if max_value is not None and number > max_value:
        raise StyleInputError("%s 大于最大值 %s" % (_describe(value, parameter, location), max_value))
    if integer:
        if abs(number - round(number)) > 1e-9:
            raise StyleInputError("%s 必须是整数" % _describe(value, parameter, location))
        return int(round(number))
    return number

def parse_color(value: Any, parameter: str = "color", location: Optional[str] = None) -> str:
    """Accept exactly #RRGGBB and normalize letters to upper case."""
    if not isinstance(value, str):
        raise StyleInputError("%s 必须是 #RRGGBB 颜色" % _describe(value, parameter, location))
    text = value.strip().replace("\u3000", " ").replace("\xa0", " ")
    if not HEX_COLOR_RE.fullmatch(text):
        raise StyleInputError("%s 不是 #RRGGBB；命名色或多余文字不可接受" % _describe(value, parameter, location))
    return text.upper()

def parse_color_list(value: Any, parameter: str = "series_colors",
                     location: Optional[str] = None) -> Optional[Tuple[str, ...]]:
    """Parse a ;/； separated #RRGGBB list; empty cell means inherit palette.

    Duplicate colors are allowed because series consume the list cyclically.
    """
    if _is_blank(value):
        return None
    if not isinstance(value, str):
        raise StyleInputError("%s 必须是分号分隔的 #RRGGBB 列表" % _describe(value, parameter, location))
    segments = re.split(r"[;；]", value.strip())
    for index, segment in enumerate(segments, start=1):
        if not segment.strip():
            raise StyleInputError("%s 第 %d 段为空；不允许空颜色段" % (_describe(value, parameter, location), index))
    return tuple(parse_color(segment, parameter,
                             ("%s 第 %d 段" % (location, index)) if location else "第 %d 段" % index)
                 for index, segment in enumerate(segments, start=1))

def parse_enum(value: Any, allowed: Tuple[str, ...], parameter: str = "enum",
               location: Optional[str] = None) -> str:
    """Parse an enumerated value case-insensitively after Unicode normalization."""
    text = unicodedata.normalize("NFKC", str(value)).strip().replace("\u3000", " ").lower()
    text = re.sub(r"\s+", " ", text)
    for option in allowed:
        if text == option.lower():
            return option
    raise StyleInputError("%s 不在允许值 %s 内" % (_describe(value, parameter, location), list(allowed)))

def parse_legend_location(value: Any, parameter: str = "legend_location",
                          location: Optional[str] = None) -> str:
    """Parse the shared legend-position enum."""
    return parse_enum(value, LEGEND_LOCATIONS, parameter, location)

def parse_style_preset(value: Any, parameter: str = "style_preset",
                       location: Optional[str] = None) -> str:
    """Parse the style preset enum; schema 1 supports only paper."""
    return parse_enum(value, STYLE_PRESETS, parameter, location)

def parse_output_formats(value: Any, parameter: str = "output_formats",
                         location: Optional[str] = None) -> Optional[Tuple[str, ...]]:
    """Parse png/pdf/svg values separated by comma, semicolon or whitespace."""
    if _is_blank(value):
        return None
    parts = [part for part in re.split(r"[,，;；\s]+", str(value).strip()) if part]
    formats = tuple(parse_enum(part, OUTPUT_FORMATS, parameter, location) for part in parts)
    if not formats or len(set(formats)) != len(formats):
        raise StyleInputError("%s 必须列出不重复的 png/pdf/svg" % _describe(value, parameter, location))
    return formats

def _legacy_header(value: str) -> str:
    """Collapse a legacy unit suffix for backward-compatible key lookup."""
    return re.sub(r"_?(pt|dpi|cm)$", "", value.strip().lower())

def _warning_location(location: Any, original: str, field: str) -> str:
    if not location:
        return ""
    if isinstance(location, Mapping):
        text = location.get(original) or location.get(field)
    else:
        text = str(location)
    return "，位置 %s" % text if text else ""


def normalize_settings(mapping: Mapping, location: Any = None) -> Tuple[dict, list]:
    """Normalize a settings mapping to ``{field: (original_name, value)}``.

    Unknown keys are returned in the warnings list with their original name,
    value and optional cell location; strict callers can turn those warnings
    into errors.  Later duplicate aliases win, matching row order.
    """
    normalized: dict = {}
    warnings: list = []
    for original, value in mapping.items():
        field = normalize_header(original)
        if field not in STYLE_FIELDS:
            field = next((candidate for candidate in STYLE_FIELDS
                          if _legacy_header(candidate) == _legacy_header(field)), None)
        if field is None:
            where = _warning_location(location, str(original), normalize_header(original))
            warnings.append("未知样式参数 %r，值 %r%s 已忽略" % (str(original), value, where))
            continue
        normalized[field] = (str(original), value)
    return normalized, warnings

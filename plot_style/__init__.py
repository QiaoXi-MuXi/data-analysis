# -*- coding: utf-8 -*-
"""Shared plot style package (P1), with no project-specific integrations."""
from .core import (
    FIELD_ALIASES, HEX_COLOR_RE, PAPER_STYLE, STYLE_FIELDS, STYLE_SCHEMA_VERSION,
    FigureStyle, StyleInputError, normalize_header, normalize_settings, parse_bool,
    parse_color, parse_color_list, parse_enum, parse_legend_location, parse_number,
    parse_output_formats, parse_style_preset,
)
from .resolve import resolve_style
from .context import style_context
from .workbook import upgrade_workbook

__all__ = [
    "STYLE_SCHEMA_VERSION", "STYLE_FIELDS", "FIELD_ALIASES", "HEX_COLOR_RE",
    "FigureStyle", "PAPER_STYLE", "StyleInputError",
    "resolve_style", "style_context", "upgrade_workbook",
    "normalize_header", "normalize_settings", "parse_bool", "parse_color",
    "parse_color_list", "parse_enum", "parse_legend_location", "parse_number",
    "parse_output_formats", "parse_style_preset",
]

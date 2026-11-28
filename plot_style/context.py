# -*- coding: utf-8 -*-
"""Matplotlib rcParams context manager for the shared paper style."""
from __future__ import annotations

from contextlib import contextmanager
import warnings
from typing import Any, Iterator, Optional

from matplotlib import font_manager, rcParams

from .core import FigureStyle

BASE_SANS_SERIF = ["Arial", "DejaVu Sans", "Microsoft YaHei", "SimHei", "Noto Sans CJK SC"]
CJK_FALLBACKS = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC"]
_RC_KEYS = (
    "font.family", "font.sans-serif", "font.size",
    "axes.titlesize", "axes.labelsize", "axes.linewidth",
    "axes.facecolor", "axes.edgecolor", "axes.labelcolor",
    "axes.unicode_minus", "xtick.labelsize", "ytick.labelsize",
    "xtick.color", "ytick.color", "xtick.major.width", "ytick.major.width",
    "legend.fontsize", "legend.labelcolor", "lines.linewidth",
    "figure.facecolor", "figure.titlesize", "mathtext.fontset",
    "pdf.fonttype", "svg.fonttype", "savefig.dpi", "savefig.facecolor",
    "grid.color", "grid.linewidth", "axes.grid",
)


def _font_sans_serif(style: FigureStyle, font_fallback: Optional[str]) -> list:
    requested = font_fallback if font_fallback else style.font
    available = {font.name for font in font_manager.fontManager.ttflist}
    ordered = []
    if requested:
        ordered.append(str(requested))
    ordered.extend(BASE_SANS_SERIF)
    ordered.extend(item for item in CJK_FALLBACKS if item not in ordered)
    result = []
    for name in ordered:
        if name not in result:
            result.append(name)
    missing = []
    for name in ordered:
        if name not in available and name not in missing:
            missing.append(name)
    if missing:
        warnings.warn("字体未安装，已保留在 font.sans-serif 候选列表中：%s" % ", ".join(missing),
                      UserWarning, stacklevel=3)
    return result or ["DejaVu Sans"]

def _legend_location_key(location: str) -> Optional[str]:
    mapping = {
        "auto": "best",
        "best": "best",
        "upper right": "upper right",
        "upper left": "upper left",
        "outside right": "upper left",
    }
    return mapping.get(location)

@contextmanager
def style_context(style: FigureStyle, font_fallback: Optional[str] = None) -> Iterator[dict]:
    """Temporarily apply ``style`` to matplotlib.rcParams.

    The context yields a dict of effective rcParams and restores every key,
    including keys introduced inside the context, on exit.  Missing fonts
    produce warnings but never raise.  Callers wanting outside-figure legends
    for ``outside right`` must apply that placement explicitly; rcParams only
    supports standard Matplotlib locations.
    """
    if not isinstance(style, FigureStyle):
        raise TypeError("style 必须是 FigureStyle")
    previous = {key: rcParams[key] for key in _RC_KEYS if key in rcParams}
    existing_keys = set(rcParams.keys())
    settings: dict = {}
    try:
        font_list = _font_sans_serif(style, font_fallback)
        settings.update({
            "font.family": "sans-serif",
            "font.sans-serif": font_list,
            "font.size": style.tick_font_size_pt,
            "axes.titlesize": style.figure_title_font_size_pt,
            "axes.labelsize": style.axis_title_font_size_pt,
            "axes.linewidth": style.axis_line_width_pt,
            "axes.facecolor": style.background_color,
            "axes.edgecolor": "#222222",
            "axes.labelcolor": "#222222",
            "axes.unicode_minus": False,
            "xtick.labelsize": style.tick_font_size_pt,
            "ytick.labelsize": style.tick_font_size_pt,
            "xtick.color": "#222222",
            "ytick.color": "#222222",
            "xtick.major.width": style.axis_line_width_pt,
            "ytick.major.width": style.axis_line_width_pt,
            "legend.fontsize": style.legend_font_size_pt,
            "legend.labelcolor": style.legend_text_color,
            "lines.linewidth": style.line_width_pt,
            "figure.facecolor": style.background_color,
            "figure.titlesize": style.figure_title_font_size_pt,
            "mathtext.fontset": "dejavusans",
            "pdf.fonttype": 42,
            "svg.fonttype": "none",
            "savefig.dpi": style.dpi,
            "savefig.facecolor": style.background_color,
            "axes.grid": bool(style.grid_enabled),
            "grid.color": style.grid_color,
            "grid.linewidth": min(style.axis_line_width_pt, 0.6),
        })
        legend_key = _legend_location_key(style.legend_location)
        if legend_key:
            settings["legend.loc"] = legend_key
        for key, value in settings.items():
            rcParams[key] = value
        yield {key: rcParams[key] for key in settings}
    finally:
        for key in list(rcParams.keys()):
            if key not in existing_keys and key not in previous:
                try:
                    del rcParams[key]
                except (KeyError, ValueError):
                    pass
        for key, value in previous.items():
            rcParams[key] = value

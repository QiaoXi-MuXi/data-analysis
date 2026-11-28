# -*- coding: utf-8 -*-
"""Additive P2 style integration for SCI-Pic and Nano.

This module is intentionally standalone.  It only reads configuration and uses
the shared plot_style engine; algorithms and existing plotting modules are not
imported or modified.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from plot_style import (  # noqa: E402
    STYLE_FIELDS, STYLE_SCHEMA_VERSION, FigureStyle, normalize_header,
    resolve_style, upgrade_workbook,
)

# SCI-Pic appends optional rows only; legacy fields and their meanings stay put.
SCIPIC_OPTIONAL_ROWS = (
    ("样式预设", "paper", "paper；旧配色名不属于样式预设"),
    ("字体", "", "留空继承项目默认；英文数字优先 Arial，中文自动回退"),
    ("图标题字号_pt", "", "6-24 pt；留空继承项目默认"),
    ("轴标题字号_pt", "", "6-24 pt；留空继承项目默认"),
    ("刻度字号_pt", "", "6-24 pt；留空继承项目默认"),
    ("图例字号_pt", "", "6-24 pt；留空继承项目默认"),
    ("数值标注字号_pt", "", "6-24 pt；柱顶数值与倍数标签"),
    ("线宽_pt", "", "0.1-72 pt；主数据线宽"),
    ("轴线宽_pt", "", "0.1-72 pt；左、下坐标轴线宽"),
    ("图例位置", "", "auto/best/upper right/upper left/outside right；留空继承"),
    ("图例文字颜色", "", "#RRGGBB；只改图例文字，不改系列颜色"),
    ("系列颜色", "", "分号分隔 #RRGGBB；顺序为柱系列、折线系列；留空继承配色"),
    ("背景颜色", "", "#RRGGBB；留空继承白底"),
    ("网格线", "", "是/否；留空继承项目默认"),
    ("网格颜色", "", "#RRGGBB；仅网格开启时生效"),
    ("输出格式", "", "png/pdf/svg，分号分隔；留空使用项目默认"),
    ("峰值标注字号_pt", "", "Raman 兼容字段；SCI-Pic 不直接使用"),
    ("最小标注字号_pt", "", "Raman 兼容字段；SCI-Pic 不直接使用"),
)

# Curated rows for the two formal SCI-Pic templates (设置 sheet).
# Column B is intentionally left blank: writing a default would move the
# source of that field from "project default" to "excel" and change legacy
# template semantics.  Column C carries legal examples, the shared default,
# the unit and the field semantics required by P2B.1 / P5.3.
# 分辨率_dpi already exists in both formal templates, so it is not repeated;
# 峰值/最小标注字号 are Raman-compat fields that SCI-Pic does not read.
SCIPIC_TEMPLATE_ROWS = (
    ("样式预设",
     "合法值：paper；留空保持旧版外观。配色名（academic/npg/paired/energy）不属于样式预设。"),
    ("字体",
     "留空继承项目默认 Arial，中文自动回退 Microsoft YaHei；可填字体名，如 Arial、Times New Roman、Microsoft YaHei。"),
    ("图标题字号_pt",
     "6～24 pt；默认 8 pt；留空继承默认。字号单位为 pt（磅），不是 px。"),
    ("轴标题字号_pt",
     "6～24 pt；默认 8 pt；留空继承默认；单位 pt。"),
    ("刻度字号_pt",
     "6～24 pt；默认 7 pt；留空继承默认；单位 pt。"),
    ("图例字号_pt",
     "6～24 pt；默认 7 pt；留空继承默认；单位 pt。"),
    ("数值标注字号_pt",
     "6～24 pt；默认 8 pt；控制柱顶数值与倍数标签；单位 pt。注意：本表字号一律为 pt，仅 Ra 项目的标注字号使用 px。"),
    ("线宽_pt",
     "0.1～72 pt；默认 0.75 pt；主数据线宽；单位 pt。"),
    ("轴线宽_pt",
     "0.1～72 pt；默认 0.7 pt；左、下坐标轴线宽；单位 pt。"),
    ("图例位置",
     "合法值：auto/best/upper right/upper left/outside right；默认 auto；留空继承默认。"),
    ("图例文字颜色",
     "#RRGGBB，如 #222222；默认 #222222。只修改图例文字颜色，不改变系列（柱/折线）标记的颜色。"),
    ("系列颜色",
     "分号分隔 #RRGGBB，如 #33BBEE;#0077BB；按系列顺序循环：柱状折线先柱色后折线色，双组柱状先基准组后实验组；留空继承上方“配色”。"),
    ("背景颜色",
     "#RRGGBB，如 #FFFFFF；默认白底；留空继承默认。"),
    ("网格线",
     "是/否；留空继承项目默认（开启）。"),
    ("网格颜色",
     "#RRGGBB，如 #D9D9D9；默认 #D9D9D9；仅“网格线”为是时生效。"),
    ("输出格式",
     "png/pdf/svg，分号分隔，如 png;pdf;svg；默认 png;pdf;svg；留空沿用项目默认。"),
)


def upgrade_scipic_template(path, *, backup_dir=None):
    """Additively append missing public-style rows to a formal SCI-Pic template.

    Existing rows, header, fills, data validations and column widths are never
    rewritten or moved; only missing rows are appended, so repeated calls are
    idempotent.  When ``backup_dir`` is given, the original bytes are copied
    there before the in-place upgrade (the copy-to-new-file strategy of
    :func:`upgrade_workbook_copy` is preserved as a backup, not as a second
    live template).
    """
    from openpyxl import load_workbook
    from copy import copy

    source = Path(path)
    if not source.is_file():
        raise StyleResolutionError("模板文件不存在：" + str(source))
    existing_bytes = source.read_bytes()

    backup_path = None
    if backup_dir is not None:
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(existing_bytes).hexdigest()[:8]
        backup_path = backup_dir / ("%s.before.%s.xlsx" % (source.stem, digest))
        if not backup_path.exists():
            shutil.copy2(source, backup_path)

    wb = load_workbook(source, data_only=False)
    try:
        if "设置" not in wb.sheetnames:
            raise StyleResolutionError("模板缺少“设置”表：" + source.name)
        ws = wb["设置"]

        header_row = 1
        for row_index in range(1, min(ws.max_row or 1, 6) + 1):
            if str(ws.cell(row_index, 1).value or "").strip() == "参数":
                header_row = row_index
                break

        existing = set()
        donor_row = header_row
        notes_row = None
        for row in range(header_row + 1, (ws.max_row or header_row) + 1):
            if str(ws.cell(row, 1).value or "").strip() == "使用说明":
                notes_row = row
                break
        # Existing-name detection scans the whole sheet: previously appended
        # style rows live after the notes block and must still be recognized
        # so repeated upgrades stay idempotent.
        for row in range(header_row + 1, (ws.max_row or header_row) + 1):
            name = ws.cell(row, 1).value
            if name is None:
                continue
            text = str(name).strip()
            if text:
                existing.add(text)
        # The style donor is the last legacy parameter row before the notes
        # block; unstyled note rows must not supply its fill/font.
        scan_end = notes_row or (ws.max_row or header_row) + 1
        for row in range(header_row + 1, scan_end):
            name = ws.cell(row, 1).value
            if name is None:
                continue
            if ws.cell(row, 2).value not in (None, ""):
                donor_row = row

        appended = []
        for name, note in SCIPIC_TEMPLATE_ROWS:
            if name in existing:
                continue
            row = (ws.max_row or donor_row) + 1
            a = ws.cell(row, 1, value=name)
            b = ws.cell(row, 2, value=None)  # blank = inherit default
            c = ws.cell(row, 3, value=note)
            for target, col in ((a, 1), (b, 2), (c, 3)):
                donor = ws.cell(donor_row, col)
                target._style = copy(donor._style)
            appended.append(name)

        wb.save(source)
        return {
            "output_path": str(source),
            "sheet": ws.title,
            "appended": tuple(appended),
            "backed_up": str(backup_path) if backup_path else None,
            "sha256_before": hashlib.sha256(existing_bytes).hexdigest(),
        }
    finally:
        wb.close()


# Nano's existing anchors are preserved verbatim; the extra SCI-Pic sand
# anchor is not silently adopted.
NANO_ENERGY_RAMP = (
    "#08306B", "#1F4E9C", "#2F6DB5", "#5A9BD4",
    "#E3A857", "#D9772B", "#B93A25", "#8C1A1A",
)

LEGACY_UNIT_RULES = {
    "sci-pic": {"font_sizes": "pt", "line_and_axes": "pt", "dpi": "dpi"},
    "nano": {"font_sizes": "pt", "line_and_axes": "pt", "dpi": "dpi"},
}

_FIELD_UNITS = {name: ("pt" if name.endswith("_pt") else "dpi" if name == "dpi"
                       else "#RRGGBB" if name.endswith("_color") or name == "legend_text_color"
                       else "enum" if name in ("style_preset", "legend_location")
                       else "format-list" if name == "output_formats"
                       else "string" if name in ("font", "palette")
                       else "boolean" if name == "grid_enabled"
                       else "color-list" if name == "series_colors" else "value")
                for name in STYLE_FIELDS}


# Existing workbook keys are data/layout settings, not P2 style keys.  They
# must not produce spurious "unknown style key" warnings.
LEGACY_NON_STYLE_KEYS = frozenset({
    "图名", "图标题", "横轴标题", "纵轴标题", "纵轴单位", "图片宽度_cm",
    "图片高度_cm", "小数位数", "显示数值", "标签旋转_度", "图类型",
    "显示倍数", "倍数小数位数", "_setting_locations", "_style_context",
    "绘图dpi", "输出格式_旧", "能量色阶", "端点边距", "样品配色",
})
STYLE_ALIASES = frozenset({
    "样式预设", "字体", "图标题字号_pt", "轴标题字号_pt", "刻度字号_pt",
    "图例字号_pt", "数值标注字号_pt", "线宽_pt", "轴线宽_pt", "图例位置",
    "图例文字颜色", "系列颜色", "背景颜色", "网格线", "网格颜色",
    "输出格式", "峰值标注字号_pt", "最小标注字号_pt", "配色", "分辨率_dpi",
}) | frozenset(STYLE_FIELDS)


class StyleResolutionError(ValueError):
    """User-correctable P2 configuration error."""


def _style_settings(mapping):
    """Keep style aliases and genuine unknowns; ignore legacy non-style keys."""
    if not mapping:
        return {}
    result = {}
    for key, value in mapping.items():
        text = str(key).strip()
        if text in LEGACY_NON_STYLE_KEYS:
            continue
        if text in STYLE_ALIASES or normalize_header(text) in STYLE_FIELDS:
            result[key] = value
        else:
            # Genuine unknown keys must reach P1 so it can warn or fail strict.
            result[key] = value
    return result


def _read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise StyleResolutionError("JSON 样式文件必须是对象：" + path.name)
    return data


def _read_excel_global(path: Path) -> tuple[dict, dict]:
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True, read_only=True)
    try:
        sheet = wb["全局设置"] if "全局设置" in wb.sheetnames else wb.worksheets[-1]
        settings: dict = {}
        locations: dict = {}
        start_row = 1
        header = [str(cell.value).strip() for cell in sheet[1][:2]]
        if "参数" in header and "填写值" in header:
            start_row = 2
        for row in sheet.iter_rows(min_row=start_row, min_col=1, max_col=2):
            key = row[0].value
            if key is None or not str(key).strip():
                continue
            raw = str(key).strip()
            if raw in ("参数", "填写值", "使用说明"):
                continue
            settings[raw] = row[1].value if len(row) > 1 else None
            locations[raw] = "%s!%s" % (sheet.title, row[0].coordinate)
        return settings, locations
    finally:
        wb.close()


def load_style_config(path: Optional[Any]) -> tuple[dict, dict]:
    """Load an optional JSON overlay or Excel 全局设置 sheet.

    Excel row values and their cell addresses are preserved so P1 warnings and
    errors can name the sheet/cell/parameter.  A blank overlay returns empty
    mappings and therefore cannot change project defaults.
    """
    if path is None:
        return {}, {}
    path = Path(path)
    if not path.is_file():
        raise StyleResolutionError("样式配置文件不存在：" + str(path))
    if path.suffix.lower() == ".json":
        data = _read_json(path)
        return data, {key: "%s:%s" % (path.name, key) for key in data}
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        return _read_excel_global(path)
    raise StyleResolutionError("样式配置仅支持 .json 或 .xlsx：" + path.name)


def scipic_project_defaults(config: Optional[dict] = None) -> dict:
    """Project-level defaults built on the shared PAPER_STYLE values.

    Only SCI-Pic-specific choices (grid on, output formats, Arial first) stay
    here; font sizes / line widths come from the shared schema defaults and are
    not duplicated as a second SCI-Pic constant table.
    """
    from plot_style import PAPER_STYLE
    defaults = {
        "font": "Arial",
        "figure_title_font_size_pt": PAPER_STYLE.figure_title_font_size_pt,
        "axis_title_font_size_pt": PAPER_STYLE.axis_title_font_size_pt,
        "tick_font_size_pt": PAPER_STYLE.tick_font_size_pt,
        "legend_font_size_pt": PAPER_STYLE.legend_font_size_pt,
        "value_label_font_size_pt": PAPER_STYLE.value_label_font_size_pt,
        "peak_label_font_size_pt": PAPER_STYLE.peak_label_font_size_pt,
        "min_peak_label_font_size_pt": PAPER_STYLE.min_peak_label_font_size_pt,
        "line_width_pt": PAPER_STYLE.line_width_pt,
        "axis_line_width_pt": PAPER_STYLE.axis_line_width_pt,
        "legend_location": PAPER_STYLE.legend_location,
        "legend_text_color": PAPER_STYLE.legend_text_color,
        "background_color": PAPER_STYLE.background_color,
        "grid_enabled": True,
        "grid_color": PAPER_STYLE.grid_color,
        "dpi": PAPER_STYLE.dpi,
        "output_formats": ("png", "pdf", "svg"),
    }
    if config and config.get("分辨率_dpi") is not None:
        defaults["dpi"] = config["分辨率_dpi"]
    return defaults


def nano_project_defaults(cfg: Optional[dict] = None) -> dict:
    """Nano publication defaults from nano_plots.py, expressed in PT."""
    defaults = {
        "font": None,
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
        "background_color": "#FFFFFF",
        "grid_enabled": False,
        "grid_color": "#D9D9D9",
        "dpi": 600,
        "output_formats": ("png",),
    }
    draw_section = (cfg or {}).get("绘图", {}) if isinstance(cfg, dict) else {}
    if draw_section.get("格式"):
        formats = draw_section["格式"]
        if isinstance(formats, str):
            formats = [item.strip() for item in formats.replace("，", ";").split(";") if item.strip()]
        defaults["output_formats"] = tuple(str(item).strip().lower() for item in formats)
    return defaults


def _supported_project_keys(project: str) -> Iterable[str]:
    if project not in LEGACY_UNIT_RULES:
        return ()
    return STYLE_FIELDS


def resolve_project_style(project, excel_settings=None, config_path=None,
                          *, project_defaults=None, explicit_overrides=None,
                          row_settings=None, strict=False, location=None,
                          unit_rule=None):
    """Resolve a FigureStyle plus per-field source map for one supported project.

    The project argument is 'sci-pic' or 'nano'.  There is no Ra support here:
    passing another project raises instead of mixing px and pt.  Precedence
    and blank inheritance are delegated to plot_style.resolve_style.
    """
    if project not in LEGACY_UNIT_RULES:
        raise StyleResolutionError("P2 仅支持 sci-pic/nano；Ra 使用 px，不能在此混用")
    if project == "sci-pic" and project_defaults is None:
        project_defaults = scipic_project_defaults(excel_settings)
    elif project == "nano" and project_defaults is None:
        project_defaults = nano_project_defaults()
    overlay, overlay_locations = load_style_config(config_path)
    merged = _style_settings(excel_settings)
    merged.update(overlay)
    loc = dict(location or {})
    loc.update(overlay_locations)
    style, sources = resolve_style(
        merged,
        project_defaults=project_defaults,
        explicit_overrides=explicit_overrides,
        strict=strict,
        location=loc,
        row_settings=row_settings,
    )
    resolved_units = dict(LEGACY_UNIT_RULES[project])
    if unit_rule:
        resolved_units.update(unit_rule)
    return style, sources, resolved_units


def resolved_config_payload(style: FigureStyle, sources: dict,
                            *, units: Optional[dict] = None,
                            project: str = "") -> dict:
    """Build the value/unit/source document required in each fixture run."""
    return {
        "schema_version": STYLE_SCHEMA_VERSION,
        "project": project,
        "units": dict(units or {}),
        "values": {
            field: {
                "value": value,
                "unit": _FIELD_UNITS[field],
                "source": sources.get(field, "schema"),
            }
            for field, value in style.to_dict().items()
        },
    }


def write_resolved_config(path, style: FigureStyle, sources: dict,
                          *, units: Optional[dict] = None,
                          project: str = "") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(resolved_config_payload(style, sources, units=units, project=project),
                      ensure_ascii=False, indent=2)
    path.write_text(text + "\n", encoding="utf-8")
    return path


def upgrade_workbook_copy(source, output, *, template_rows=None):
    """Additive-only workbook upgrade into a new file; source stays untouched."""
    source, output = Path(source), Path(output)
    if source.resolve() == output.resolve():
        raise StyleResolutionError("增量升级必须写入新工作副本；不能覆盖源工作簿")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise StyleResolutionError("增量升级目标已存在：" + str(output))
    shutil.copy2(source, output)
    return upgrade_workbook(output, output_path=output, template_rows=template_rows,
                            allow_overwrite=True)

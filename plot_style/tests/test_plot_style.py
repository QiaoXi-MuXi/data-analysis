# -*- coding: utf-8 -*-
"""Unit tests for the P1 shared plot style package."""
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import sys
import tempfile
import unittest
import warnings

import matplotlib
matplotlib.use("Agg", force=True)
from matplotlib import rcParams
from openpyxl import Workbook, load_workbook

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PACKAGE_ROOT.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))
from plot_style import (
    PAPER_STYLE, STYLE_FIELDS, STYLE_SCHEMA_VERSION, FigureStyle, StyleInputError,
    normalize_header, parse_bool, parse_color, parse_color_list, parse_enum,
    parse_number, resolve_style, style_context, upgrade_workbook,
)


class DefaultsAndResolverTests(unittest.TestCase):
    def test_paper_defaults(self):
        style = PAPER_STYLE
        self.assertEqual(STYLE_SCHEMA_VERSION, 1)
        self.assertEqual(style.style_preset, "paper")
        self.assertIsNone(style.font)
        self.assertEqual(style.figure_title_font_size_pt, 8)
        self.assertEqual(style.axis_title_font_size_pt, 8)
        self.assertEqual(style.tick_font_size_pt, 7)
        self.assertEqual(style.legend_font_size_pt, 7)
        self.assertEqual(style.value_label_font_size_pt, 8)
        self.assertEqual(style.peak_label_font_size_pt, 10)
        self.assertEqual(style.min_peak_label_font_size_pt, 8)
        self.assertEqual(style.line_width_pt, 0.75)
        self.assertEqual(style.axis_line_width_pt, 0.7)
        self.assertEqual(style.legend_location, "auto")
        self.assertEqual(style.legend_text_color, "#222222")
        self.assertIsNone(style.series_colors)
        self.assertIsNone(style.palette)
        self.assertEqual(style.background_color, "#FFFFFF")
        self.assertFalse(style.grid_enabled)
        self.assertEqual(style.grid_color, "#D9D9D9")
        self.assertEqual(style.dpi, 600)
        self.assertIsNone(style.output_formats)

    def test_header_aliases_and_units(self):
        self.assertEqual(normalize_header("  图标题字号 （pt） "), "figure_title_font_size_pt")
        self.assertEqual(normalize_header("最小标注字号pt"), "min_peak_label_font_size_pt")
        self.assertEqual(normalize_header("DPI"), "dpi")
        value = parse_number(" 8 pt ", "图标题字号", min_value=1, max_value=10, units=("pt",))
        self.assertEqual(value, 8.0)

    def test_precedence_and_sources(self):
        excel = {"最小标注字号_pt": 9, "分辨率_dpi": 300, "网格线": "是"}
        defaults = {"min_peak_label_font_size_pt": 7, "dpi": 200, "legend_location": "upper left"}
        cli = {"min_peak_label_font_size_pt": "10pt"}
        style, sources = resolve_style(excel, project_defaults=defaults, explicit_overrides=cli)
        self.assertEqual(style.min_peak_label_font_size_pt, 10)
        self.assertEqual(sources["min_peak_label_font_size_pt"], "cli")
        self.assertEqual(style.dpi, 300)
        self.assertEqual(sources["dpi"], "excel")
        self.assertTrue(style.grid_enabled)
        self.assertEqual(sources["grid_enabled"], "excel")
        self.assertEqual(style.legend_location, "upper left")
        self.assertEqual(sources["legend_location"], "project")
        self.assertEqual(style.tick_font_size_pt, 7)
        self.assertEqual(sources["tick_font_size_pt"], "schema")
        row_style, row_sources = resolve_style(excel, row_settings={"dpi": 400})
        self.assertEqual(row_style.dpi, 400)
        self.assertEqual(row_sources["dpi"], "row")

    def test_blank_inheritance(self):
        settings = {"最小标注字号_pt": "  ", "dpi": None, "font": ""}
        defaults = {"min_peak_label_font_size_pt": 8, "dpi": 250, "font": "Arial"}
        style, sources = resolve_style(settings, project_defaults=defaults)
        self.assertEqual(style.min_peak_label_font_size_pt, 8)
        self.assertEqual(style.dpi, 250)
        self.assertEqual(style.font, "Arial")
        self.assertEqual(sources["min_peak_label_font_size_pt"], "project")
        self.assertEqual(sources["dpi"], "project")
        self.assertEqual(sources["font"], "project")
        off = resolve_style({"grid_enabled": "否"})[0]
        self.assertFalse(off.grid_enabled)

    def test_unknown_warning_and_strict(self):
        location = {"未知参数": "设置表!C4"}
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            style, sources = resolve_style({"未知参数": 1, "tick_font_size_pt": 8}, location=location)
        self.assertEqual(style.tick_font_size_pt, 8)
        self.assertTrue(any("未知参数" in str(item.message) and "1" in str(item.message)
                            and "设置表!C4" in str(item.message) for item in caught))
        self.assertEqual(sources["tick_font_size_pt"], "excel")
        with self.assertRaises(StyleInputError) as ctx:
            resolve_style({"未知参数": 1}, location=location, strict=True)
        self.assertIn("未知参数", str(ctx.exception))
        self.assertIn("设置表!C4", str(ctx.exception))

    def test_error_names_parameter_value_and_location(self):
        cases = [
            ({"背景颜色": "red"}, "背景颜色", "red"),
            ({"分辨率_dpi": 2000}, "分辨率_dpi", "2000"),
            ({"图例位置": "mid"}, "图例位置", "mid"),
            ({"网格线": "maybe"}, "网格线", "maybe"),
            ({"背景颜色": "#000000 extra"}, "背景颜色", "#000000 extra"),
        ]
        for settings, parameter, value in cases:
            with self.subTest(settings=settings):
                with self.assertRaises(StyleInputError) as ctx:
                    resolve_style(settings, location={"default": "设置表!C4"})
                message = str(ctx.exception)
                self.assertIn(parameter, message)
                self.assertIn(str(value), message)
                self.assertIn("设置表!C4", message)

    def test_to_dict_and_immutability(self):
        data = PAPER_STYLE.to_dict()
        self.assertEqual(len(data), len(STYLE_FIELDS))
        self.assertEqual(tuple(data), STYLE_FIELDS)
        with self.assertRaises(FrozenInstanceError):
            PAPER_STYLE.dpi = 300
        changed = replace(PAPER_STYLE, min_peak_label_font_size_pt=6)
        self.assertEqual(changed.min_peak_label_font_size_pt, 6)
        with self.assertRaises(StyleInputError):
            FigureStyle(min_peak_label_font_size_pt=5)

    def test_parser_details(self):
        self.assertTrue(parse_bool("是"))
        self.assertFalse(parse_bool("0"))
        self.assertEqual(parse_color("#abcdef"), "#ABCDEF")
        colors = parse_color_list("#101010;#FFFFFF")
        self.assertEqual(colors, ("#101010", "#FFFFFF"))
        self.assertIsNone(parse_color_list(None))
        self.assertEqual(parse_enum("UPPER RIGHT", ("upper right",)), "upper right")
        with self.assertRaises(StyleInputError):
            parse_color_list("#101010;")
        with self.assertRaises(StyleInputError):
            parse_number("nan", "dpi")


class ContextTests(unittest.TestCase):
    def test_rcparams_restored(self):
        monitored = list(rcParams.keys())
        before = {key: rcParams[key] for key in monitored}
        with style_context(PAPER_STYLE) as effective:
            self.assertEqual(effective["savefig.dpi"], 600)
            self.assertFalse(effective["axes.unicode_minus"])
            self.assertEqual(effective["pdf.fonttype"], 42)
            self.assertEqual(effective["svg.fonttype"], "none")
            self.assertNotEqual(rcParams["font.sans-serif"], before["font.sans-serif"])
        after = {key: rcParams[key] for key in monitored}
        self.assertEqual(after, before)
        self.assertEqual(set(rcParams.keys()), set(monitored))

    def test_font_fallback_warns_never_crashes(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with style_context(PAPER_STYLE, font_fallback="Definitely Missing Font") as effective:
                self.assertIn("DejaVu Sans", effective["font.sans-serif"])
        self.assertTrue(any("字体未安装" in str(item.message) for item in caught))


class WorkbookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="plot-style-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def make_workbook(self):
        wb = Workbook()
        sheet = wb.active
        sheet.title = "设置表"
        sheet.append(["参数", "值", "说明"])
        sheet.append(["tick_font_size_pt", 11, "user value"])
        user = wb.create_sheet("用户数据")
        user.append(["A", "B"])
        user.append([1, 2])
        path = self.root / "input.xlsx"
        wb.save(path)
        return wb, path

    def test_upgrade_preserves_and_is_idempotent(self):
        wb, path = self.make_workbook()
        info = upgrade_workbook(path)
        out = Path(info["output_path"])
        self.assertNotEqual(out.resolve(), path.resolve())
        expected = tuple(field for field in STYLE_FIELDS if field != "tick_font_size_pt")
        self.assertEqual(info["appended"], expected)
        upgraded = load_workbook(out, data_only=False)
        self.assertEqual(upgraded.sheetnames, ["设置表", "用户数据"])
        self.assertEqual(upgraded["设置表"]["B2"].value, 11)
        self.assertEqual(upgraded["设置表"]["C2"].value, "user value")
        self.assertEqual(upgraded["用户数据"]["B2"].value, 2)
        self.assertEqual(upgraded["设置表"]["A2"].value, "tick_font_size_pt")
        again = upgrade_workbook(out)
        self.assertEqual(again["appended"], ())
        self.assertEqual(again["output_path"], str(out.with_name("input.upgraded.upgraded.xlsx")))
        reloaded = load_workbook(again["output_path"], data_only=False)
        self.assertEqual(reloaded["设置表"]["B2"].value, 11)
        self.assertEqual(reloaded["设置表"].max_row, upgraded["设置表"].max_row)

    def test_workbook_object_needs_output_path(self):
        wb, path = self.make_workbook()
        with self.assertRaises(ValueError):
            upgrade_workbook(wb)
        out = self.root / "from_object.xlsx"
        info = upgrade_workbook(wb, output_path=out)
        self.assertEqual(Path(info["output_path"]), out)
        self.assertTrue(out.exists())

    def test_allow_overwrite_is_explicit(self):
        wb, path = self.make_workbook()
        with self.assertRaises(ValueError):
            upgrade_workbook(path, output_path=path)
        info = upgrade_workbook(path, output_path=path, allow_overwrite=True)
        self.assertEqual(Path(info["output_path"]).resolve(), path.resolve())
        self.assertEqual(load_workbook(path)["设置表"]["A2"].value, "tick_font_size_pt")


if __name__ == "__main__":
    unittest.main()

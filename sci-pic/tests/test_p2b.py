# -*- coding: utf-8 -*-
"""P2B: resolved config, recorded output size, legacy fixture compatibility."""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import matplotlib  # noqa: E402
matplotlib.use("Agg")

SCIPIC = Path(__file__).resolve().parents[1]
WORKSPACE = SCIPIC.parent
for item in (WORKSPACE, SCIPIC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

import plot_sci_pic  # noqa: E402


class P2BRegressionTests(unittest.TestCase):
    def test_legacy_run_writes_resolved_config_and_figure_size(self):
        root = Path(tempfile.mkdtemp(prefix="sci-pic-p2b-"))
        self.addCleanup(shutil.rmtree, root, True)
        out = root / "legacy"
        proc = subprocess.run(
            [sys.executable, str(SCIPIC / "plot_sci_pic.py"),
             "--excel", str(SCIPIC / "templates" / "sci_pic.xlsx"), "--out", str(out)],
            cwd=WORKSPACE, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        run_dir = next(out.glob("*"))
        resolved = json.loads((run_dir / "resolved_config.json").read_text(encoding="utf-8"))
        self.assertEqual(resolved["schema_version"], 1)
        self.assertEqual(resolved["project"], "sci-pic")
        for entry in resolved["values"].values():
            self.assertIn("value", entry)
            self.assertIn("unit", entry)
            self.assertIn("source", entry)
        info = json.loads((run_dir / "run_info.json").read_text(encoding="utf-8"))
        self.assertIn("figure_size", info)
        self.assertIn("png_pixels", info["figure_size"])
        self.assertEqual(info["figure_size"]["dpi"], 600)

    def test_engine_does_not_use_bbox_inches_tight(self):
        source = (SCIPIC / "plot_sci_pic.py").read_text(encoding="utf-8")
        self.assertNotIn("bbox_inches=", source)

    def test_baseline_fixtures_load_without_columns_added(self):
        folder = WORKSPACE / "_style_baseline_20261125" / "fixtures" / "SCI-Pic"
        if not folder.is_dir():
            self.skipTest("样式基线夹具只留在本机（见 LOCAL_ONLY.md），版本库内不携带")
        fixtures = sorted(glob.glob(str(folder / "*.xlsx")))
        self.assertGreaterEqual(len(fixtures), 5)
        for fixture in fixtures:
            _, points, config = plot_sci_pic.load_input(Path(fixture))
            self.assertTrue(points, fixture)
            self.assertIn(config["图类型"], ("柱状折线", "双组柱状"))

    def test_semicolon_color_list_resolves_two_groups(self):
        base = plot_sci_pic.load_input(SCIPIC / "templates" / "removal_rate.xlsx")[2]
        from style_integration import resolve_project_style
        style, sources, _ = resolve_project_style(
            "sci-pic", base, explicit_overrides={"series_colors": "#112233;#445566"})
        self.assertEqual(style.series_colors, ("#112233", "#445566"))
        self.assertEqual(sources["series_colors"], "cli")


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""拼图模式（实物图 + 位置谱）的发现、配置、渲染和 CLI 回归；全部用临时目录。"""
from pathlib import Path
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

RAMAN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAMAN))

from plot_raman import main as plot_main  # noqa: E402
from raman_montage import discover_groups, load_montage, run_montage  # noqa: E402
from _make_montage_xlsx import write_workbook  # noqa: E402


def write_spectrum(path, centers=(142.0, 566.0, 762.0), amplitude=500.0):
    """平坦基线 + 三条窄峰：H 正好是 500，阈值和峰数都可精确核对。"""
    x = np.arange(-100.0, 3800.0, 1.0)
    y = np.full_like(x, 460.0)
    for c in centers:
        y += amplitude * np.exp(-0.5 * ((x - c) / 4.0) ** 2)
    np.savetxt(path, np.column_stack([x, y]), fmt="%.4f")


def write_photo(path, size=(400, 250), color=(200, 205, 190)):
    Image.new("RGB", size, color).save(path)


def run_cli(src, out, extra=()):
    return plot_main(["--montage", "--src", str(src), "--out", str(out)] + list(extra))


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="raman_montage_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_source(self):
        """三组典型情况：1 与 10 只差一个字符、20 少了 20-1.txt。"""
        src = self.tmp / "src"
        src.mkdir()
        write_photo(src / "1-1-1.png")
        for n in (1, 2, 3):
            write_spectrum(src / ("1-%d.txt" % n))
        write_photo(src / "10-1-1.png")
        for n in (1, 2, 3):
            write_spectrum(src / ("10-%d.txt" % n), centers=(140.0, 560.0))
        write_photo(src / "20-1-1.png")
        for n in (2, 3):
            write_spectrum(src / ("20-%d.txt" % n), centers=(144.0, 570.0))
        # 已出好的单谱图，不能当实物图
        write_photo(src / "1-1.png", size=(2048, 1024))
        # 没有实物图的组
        write_spectrum(src / "30-1.txt")
        return src


class DiscoveryTests(TempCase):
    def test_groups_ignore_shifted_prefix_and_survive_missing_first_position(self):
        groups, notes = discover_groups(self.make_source())
        names = [g["name"] for g in groups]
        self.assertEqual(names, ["1", "10", "20"])
        by_name = {g["name"]: g for g in groups}
        self.assertEqual([r["label"] for r in by_name["1"]["rows"]],
                         ["1-1", "1-2", "1-3"])
        # 10 组不能把 1 组的谱抢走
        self.assertEqual([r["label"] for r in by_name["10"]["rows"]],
                         ["10-1", "10-2", "10-3"])
        # 20 组只有位置 2、3 的谱，仍然成组
        self.assertEqual([r["label"] for r in by_name["20"]["rows"]],
                         ["20-2", "20-3"])
        self.assertEqual(os.path.basename(by_name["20"]["photo"]), "20-1-1.png")
        # 单谱图 1-1.png 没被当成实物图
        self.assertEqual(os.path.basename(by_name["1"]["photo"]), "1-1-1.png")
        self.assertTrue(any("30" in n for n in notes))

    def test_rows_default_to_raw_counts_and_peak_labels(self):
        groups, _ = discover_groups(self.make_source())
        row = groups[0]["rows"][0]
        self.assertFalse(row["normalize"])
        self.assertTrue(row["peaks"])
        self.assertIsNone(row["height"])
        self.assertEqual(row["color"], "#e01b24")


class ConfigTests(TempCase):
    def config(self, src, rows):
        path = self.tmp / "montage.xlsx"
        write_workbook(path, rows, overwrite=True)
        return path

    def test_labels_colors_switch_and_window_are_read(self):
        src = self.make_source()
        path = self.config(src, [
            ["甲", "1-1-1.png", "1-1.txt", "位置 1", 1, 1, 50, "red", None, 200, 900],
            ["甲", None, "1-2.txt", None, 1, 0, None, "#00ff00", None, None, None],
        ])
        groups = load_montage(path, src)
        self.assertEqual(len(groups), 1)
        g = groups[0]
        self.assertEqual(g["name"], "甲")
        self.assertEqual([r["label"] for r in g["rows"]], ["位置 1", "1-2"])
        self.assertEqual(g["window"], (200.0, 900.0))
        self.assertTrue(g["rows"][0]["normalize"])
        self.assertFalse(g["rows"][1]["peaks"])
        self.assertEqual(g["rows"][0]["color"], "red")
        self.assertEqual(g["rows"][1]["color"], "#00ff00")
        self.assertEqual(g["rows"][0]["height"], 50.0)

    def test_group_name_falls_back_to_file_stem(self):
        src = self.make_source()
        path = self.config(src, [
            [None, "1-1-1.png", "1-1.txt", None, 0, 1, None, None, None, None, None],
            [None, None, "1-2.txt", None, 0, 1, None, None, None, None, None],
        ])
        groups = load_montage(path, src)
        self.assertEqual(groups[0]["name"], "1")
        self.assertEqual(len(groups[0]["rows"]), 2)

    def test_missing_photo_bad_color_and_conflicting_window_fail_loudly(self):
        src = self.make_source()
        with self.assertRaises(ValueError):
            load_montage(self.config(src, [
                ["甲", None, "1-1.txt", None, 0, 1, None, None, None, None, None]]), src)
        with self.assertRaises(ValueError):
            load_montage(self.config(src, [
                ["甲", "1-1-1.png", "1-1.txt", None, 0, 1, None, "notacolour",
                 None, None, None]]), src)
        with self.assertRaises(ValueError):
            load_montage(self.config(src, [
                ["甲", "1-1-1.png", "1-1.txt", None, 0, 1, None, None, None, 200, None],
                ["甲", None, "1-2.txt", None, 0, 1, None, None, None, 300, None]]), src)


class RenderTests(TempCase):
    def test_end_to_end_writes_one_png_per_group_and_peak_table(self):
        src = self.make_source()
        out = self.tmp / "out"
        self.assertEqual(run_cli(src, out), 0)
        self.assertEqual(sorted(p.name for p in out.glob("*.png")),
                         ["1.png", "10.png", "20.png"])
        with Image.open(out / "1.png") as im:
            self.assertEqual(im.size[0], 2048)
            self.assertGreater(im.size[1], 2048)   # 实物图 + 谱图，比单谱图高
        lines = (out / "montage_peaks.csv").read_text(
            encoding="utf-8-sig").strip().splitlines()
        self.assertEqual(lines[0], "group,file,peak_cm-1,I_max,H_counts")
        self.assertEqual(len(lines), 1 + 9 + 6 + 4)   # 3x3 + 3x2 + 2x2 个峰

    def test_window_keeps_only_peaks_inside(self):
        src = self.make_source()
        out = self.tmp / "win"
        self.assertEqual(run_cli(src, out, ("--xmin", "400", "--xmax", "900")), 0)
        text = (out / "montage_peaks.csv").read_text(encoding="utf-8-sig")
        self.assertNotIn("142.", text)
        self.assertIn("566.", text)
        self.assertIn("762.", text)

    def test_high_threshold_writes_no_peak_table(self):
        src = self.make_source()
        out = self.tmp / "high"
        self.assertEqual(run_cli(src, out, ("--height", "10000")), 0)
        self.assertTrue((out / "1.png").is_file())
        self.assertFalse((out / "montage_peaks.csv").exists())

    def test_previous_output_is_cleared(self):
        src = self.make_source()
        out = self.tmp / "out2"
        out.mkdir()
        (out / "stale.png").write_bytes(b"x")
        self.assertEqual(run_cli(src, out), 0)
        self.assertFalse((out / "stale.png").exists())

    def test_input_inside_output_is_refused_before_clearing(self):
        src = self.make_source()
        with self.assertRaises(ValueError):
            run_montage(None, src, src)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""分层批次展示的发现、配置、数据状态与 CLI 回归；全部使用临时目录。"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

RAMAN = Path(__file__).resolve().parents[1]
ROOT = RAMAN.parent
sys.path.insert(0, str(RAMAN))

from batch_display_config import (BatchConfig, CompareConfigError, discover,
                                   load_batch_config, write_batch_workbook)
from batch_display_data import common_window, display_mean, point_status
from raman_compare import SpectrumInput


def write_spectrum(path, centers=(500.0, 900.0), start=100.0, stop=1200.0, step=2.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    x = np.arange(start, stop + step / 2, step, dtype=float)
    y = np.ones_like(x) * 5.0
    for center in centers:
        y += 100.0 * np.exp(-0.5 * ((x - center) / 3.0) ** 2)
    np.savetxt(path, np.column_stack([x, y]), fmt="%.6f")
    return path


def make_tree(root, groups=("1100", "1210"), points=(1, 2), scans=(1, 2, 3),
              refs=5, photos=True, extras=()):
    for r in range(1, refs + 1):
        write_spectrum(root / ("0-%d.txt" % r))
    for g in groups:
        for p in points:
            for s in scans:
                write_spectrum(root / g / ("%d-%d.txt" % (p, s)))
            if photos:
                (root / g / ("%d-1-1.png" % p)).write_bytes(b"PNG")
        for name in extras:
            (root / g / name).write_bytes(b"x")
    return root


class DiscoveryTests(unittest.TestCase):
    def test_discovers_references_groups_and_scans(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tree(Path(tmp))
            info = discover(root, expected_points=2, scans_per_point=3)
            self.assertEqual(len(info["references"]), 5)
            self.assertEqual(info["groups"], ["1100", "1210"])
            self.assertEqual(len(info["scans"]), 12)
            self.assertEqual(info["warnings"], [])

    def test_missing_scan_becomes_warning_not_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tree(Path(tmp))
            (root / "1100" / "2-1.txt").unlink()
            info = discover(root, expected_points=2, scans_per_point=3)
            self.assertEqual(len(info["scans"]), 12)  # 11 真实 + 1 占位
            self.assertTrue(any("P02 S1" in w or "缺少扫描" in w
                                for w in info["warnings"]))

    def test_unexpected_files_are_warnings_not_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tree(Path(tmp), extras=("1-1.png", "notes.csv"))
            info = discover(root)
            paths = {s.file for s in info["scans"]}
            self.assertFalse(any("notes.csv" in p for p in paths))
            self.assertTrue(any("意外文件" in w for w in info["warnings"]))

    def test_wrong_root_txt_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tree(Path(tmp))
            (root / "random.txt").write_text("junk")
            info = discover(root)
            self.assertEqual(len(info["references"]), 5)

    def test_no_references_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "1-1.txt").write_text("junk")
            with self.assertRaises(CompareConfigError):
                discover(root)


class ConfigTests(unittest.TestCase):
    def test_workbook_round_trip_skips_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tree(Path(tmp))
            (root / "1100" / "2-1.txt").unlink()
            info = discover(root, expected_points=2, scans_per_point=3)
            target = Path(tmp) / "batch_display.xlsx"
            write_batch_workbook(target, src=root, scans=info["scans"],
                                 references=info["references"])
            config = load_batch_config(target, root)
            self.assertEqual(len(config.scans), 12)  # 11 真 + 1 占位，缺扫描仍可见
            self.assertEqual(config.pair_groups, ("1100", "1210"))
            self.assertEqual(config.expected_points, 0)  # 模板默认 0=按数据推断

    def test_formula_is_rejected(self):
        import openpyxl
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tree(Path(tmp))
            info = discover(root)
            target = Path(tmp) / "batch_display.xlsx"
            write_batch_workbook(target, src=root, scans=info["scans"],
                                 references=info["references"])
            wb = openpyxl.load_workbook(target)
            wb["参数"].cell(2, 2, "=1+1")
            wb.save(target)
            wb.close()
            with self.assertRaises(CompareConfigError):
                load_batch_config(target, root)


class DataTests(unittest.TestCase):
    def test_point_status_partial_and_complete(self):
        from batch_display_data import LoadedScan
        ok = LoadedScan(scan=None, spectrum=None, status="ok")
        missing = LoadedScan(scan=None, spectrum=None, status="missing")
        complete = point_status([ok, ok, ok])
        self.assertEqual(complete["status"], "complete")
        partial = point_status([ok, ok, missing])
        self.assertEqual(partial["status"], "partial")
        self.assertEqual(partial["valid_scans"], 2)

    def test_common_window_is_intersection(self):
        a = SpectrumInput(Path("a.txt"), "a", np.array([0.0, 10.0]), np.array([1.0, 1.0]))
        b = SpectrumInput(Path("b.txt"), "b", np.array([2.0, 8.0]), np.array([1.0, 1.0]))
        cfg = BatchConfig(path=None, src=Path("."), xmin=None, xmax=None)
        lo, hi = common_window(cfg, [a, b])
        self.assertEqual((lo, hi), (2.0, 8.0))

    def test_display_mean_uses_common_grid(self):
        x = np.arange(0.0, 10.0, 1.0)
        a = SpectrumInput(Path("a.txt"), "a", x, x + 1.0)
        b = SpectrumInput(Path("b.txt"), "b", x, x + 3.0)
        grid, mean, n = display_mean([a, b], 0.0, 9.0, 0.0)
        self.assertEqual(n, 2)
        self.assertEqual(len(grid), 10)
        self.assertTrue(np.allclose(mean, x + 2.0))


class CLITests(unittest.TestCase):
    def test_help_lists_batch_display(self):
        proc = subprocess.run([str(ROOT / "python" / "python.exe"), "-B", "-X", "utf8",
                               str(RAMAN / "plot_raman.py"), "--help"],
                              capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--batch-display", proc.stdout)

    def test_missing_src_fails_cleanly(self):
        env = dict(os.environ, DATA_ANALYSIS_NO_PAUSE="1")
        proc = subprocess.run([str(ROOT / "python" / "python.exe"), "-B", "-X", "utf8",
                               str(RAMAN / "plot_raman.py"), "--batch-display",
                               "--src", str(Path(tempfile.gettempdir()) / "no_such_dir_xyz"),
                               "--out", str(Path(tempfile.gettempdir()) / "bd_out_xyz")],
                              capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(proc.returncode, 1)


if __name__ == "__main__":
    unittest.main()

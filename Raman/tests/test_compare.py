# -*- coding: utf-8 -*-
"""基准对比模式的纯算法、配置和端到端 CLI 回归；全部使用临时目录。"""
import csv
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import numpy as np

RAMAN = Path(__file__).resolve().parents[1]
ROOT = RAMAN.parent
sys.path.insert(0, str(RAMAN))

from compare_config import (CompareSample, write_compare_workbook,
                            load_compare_config)
from raman_compare import (Peak, SpectrumInput, analysis_window, compare_sample,
                           detect_spectrum, diff_stats, load_spectrum,
                           match_peaks, pearson_r)


def spectrum_data(centers, *, amplitude=100.0, width=3.0, start=100.0,
                  stop=900.0, step=0.5, baseline=5.0, slope=0.0):
    x = np.arange(start, stop + step / 2, step, dtype=float)
    y = baseline + slope * (x - x.mean())
    for center in centers:
        y += amplitude * np.exp(-0.5 * ((x - center) / width) ** 2)
    return x, y


def write_spectrum(path, centers, **kwargs):
    x, y = spectrum_data(centers, **kwargs)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savetxt(path, np.column_stack([x, y]), fmt="%.6f")
    return path


def make_peak(identifier, center, height=100.0):
    return Peak(identifier, float(center), float(height), float(height), 0.0, 0)


class MatchingTests(unittest.TestCase):
    def test_anti_greedy_keeps_maximum_number_of_pairs(self):
        reference = [make_peak("R1", 400), make_peak("R2", 406)]
        sample = [make_peak("S1", 405), make_peak("S2", 411)]
        result = match_peaks(reference, sample, 6.0)
        self.assertEqual(result.pairs, [(0, 0), (1, 1)])
        self.assertEqual(result.missing, [])
        self.assertEqual(result.new, [])

    def test_one_to_many_and_tolerance_boundary(self):
        reference = [make_peak("R1", 400)]
        sample = [make_peak("S1", 405.0), make_peak("S2", 407.0)]
        result = match_peaks(reference, sample, 5.0)
        self.assertEqual(result.pairs, [(0, 0)])
        self.assertEqual(result.new, [1])
        self.assertEqual(match_peaks(reference, sample, 4.999).pairs, [])

    def test_empty_sets_and_full_mismatch(self):
        reference = [make_peak("R1", 300)]
        self.assertEqual(match_peaks(reference, [], 10).missing, [0])
        self.assertEqual(match_peaks([], [make_peak("S1", 300)], 10).new, [0])
        result = match_peaks(reference, [make_peak("S1", 700)], 10)
        self.assertEqual((result.pairs, result.missing, result.new), ([], [0], [0]))

    def test_diff_stats_match_hand_calculation(self):
        values = diff_stats([2.0, 0.0, 4.0])
        self.assertAlmostEqual(values["mean_delta_cm-1"], 2.0)
        self.assertAlmostEqual(values["mae_delta_cm-1"], 2.0)
        self.assertAlmostEqual(values["max_abs_delta_cm-1"], 4.0)
        self.assertAlmostEqual(values["rmse_delta_cm-1"], np.sqrt(20.0 / 3.0))
        empty = diff_stats([])
        self.assertTrue(all(value is None for value in empty.values()))

    def test_pearson_and_degenerate_vectors(self):
        self.assertAlmostEqual(pearson_r([1, 2, 3, 4], [2, 4, 6, 8]), 1.0)
        self.assertAlmostEqual(pearson_r([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)
        self.assertIsNone(pearson_r([1, 1, 1], [1, 2, 3]))
        self.assertIsNone(pearson_r([1], [2]))


class SpectrumTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="raman-compare-core-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_descending_is_reversed_and_duplicates_are_rejected(self):
        path = self.root / "descending.txt"
        x, y = spectrum_data([300, 500])
        np.savetxt(path, np.column_stack([x[::-1], y[::-1]]))
        loaded = load_spectrum(path)
        self.assertTrue(np.all(np.diff(loaded[0]) > 0))
        duplicate = self.root / "duplicate.txt"
        np.savetxt(duplicate, [[100, 1], [100, 2], [101, 3]])
        with self.assertRaisesRegex(ValueError, "重复"):
            load_spectrum(duplicate)

    def test_common_window_and_no_extrapolation(self):
        first = SpectrumInput(self.root / "a.txt", "A", np.arange(100, 501, 1), np.ones(401))
        second = SpectrumInput(self.root / "b.txt", "B", np.arange(200, 601, 1), np.ones(401))
        self.assertEqual(analysis_window([first, second]), (200.0, 500.0))
        with self.assertRaisesRegex(ValueError, "不覆盖"):
            analysis_window([first, second], 150, 400)
        with self.assertRaisesRegex(ValueError, "同时"):
            analysis_window([first, second], 150, None)

    def test_detection_keeps_more_than_label_limit(self):
        centers = [120 + 65 * index for index in range(12)]
        path = write_spectrum(self.root / "many.txt", centers, amplitude=50, width=2.3,
                              stop=950, baseline=0)
        x, y = load_spectrum(path)
        spectrum = SpectrumInput(path, "many", x, y, peaks=True)
        detection = detect_spectrum(spectrum, 100, 950, threshold=12, zero_guard=50,
                                    max_labels=None)
        self.assertEqual(len(detection.peaks), 12)
        self.assertTrue(all(abs(peak.center - center) < 0.2
                            for peak, center in zip(detection.peaks, centers)))

    def test_exact_metric_case(self):
        ref_path = write_spectrum(self.root / "ref.txt", [200, 400, 600, 800])
        a_path = write_spectrum(self.root / "a.txt", [202, 400, 604, 700])
        ref_xy, a_xy = load_spectrum(ref_path), load_spectrum(a_path)
        ref_spec = SpectrumInput(ref_path, "基准", *ref_xy)
        a_spec = SpectrumInput(a_path, "A", *a_xy)
        ref_det = detect_spectrum(ref_spec, 100, 900, threshold=20, zero_guard=50,
                                  max_labels=None)
        a_det = detect_spectrum(a_spec, 100, 900, threshold=20, zero_guard=50,
                                max_labels=None)
        result = compare_sample(ref_det, a_det, tolerance=5)
        self.assertEqual((result.status, result.matched, result.missing, result.new),
                         ("success", 3, 1, 1))
        self.assertAlmostEqual(result.match_rate, 0.75)
        self.assertAlmostEqual(result.metric["mean_delta_cm-1"], 2.0, places=1)
        self.assertAlmostEqual(result.metric["mae_delta_cm-1"], 2.0, places=1)
        self.assertAlmostEqual(result.metric["max_abs_delta_cm-1"], 4.0, places=1)
        self.assertLess(result.pearson_r, 1.0)

        identity = compare_sample(ref_det, ref_det, tolerance=5)
        self.assertEqual((identity.matched, identity.missing, identity.new), (4, 0, 0))
        self.assertAlmostEqual(identity.pearson_r, 1.0)


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="raman-compare-config-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.book = self.root / "compare.xlsx"

    def test_roundtrip_defaults_and_relative_reference(self):
        write_compare_workbook(self.book, [
            CompareSample("a.txt", "样品 A", True, None, True),
            CompareSample("b.txt", "样品 B", False, 12.5, False),
        ], {
            "基准文件": "reference/base.txt",
            "基准名称": "基准谱",
            "匹配容差": "6.5",
            "归一化(0/1)": "0",
            "峰位校正": "-2",
            "激光线排除半宽": "40",
        })
        config = load_compare_config(self.book)
        self.assertEqual(len(config.samples), 2)
        self.assertEqual([sample.enabled for sample in config.samples], [True, False])
        self.assertEqual(config.reference_path(), (self.root / "reference/base.txt").resolve())
        self.assertEqual(config.tolerance, 6.5)
        self.assertFalse(config.normalize)
        self.assertEqual(config.shift, -2)
        self.assertEqual(config.zero_guard, 40)

    def test_invalid_parameters_and_duplicates_are_rejected(self):
        write_compare_workbook(self.book, [CompareSample("a.txt", "A")],
                               {"基准文件": "reference/base.txt", "匹配容差": "0"})
        with self.assertRaisesRegex(ValueError, "大于 0"):
            load_compare_config(self.book)
        write_compare_workbook(self.book, [CompareSample("a.txt", "A")],
                               {"基准文件": "reference/base.txt"}, overwrite=True)
        import openpyxl
        wb = openpyxl.load_workbook(self.book)
        wb["参数"].append(["未知参数", "1", "不应被忽略"])
        wb.save(self.book)
        wb.close()
        with self.assertRaisesRegex(ValueError, "未知参数"):
            load_compare_config(self.book)

    def test_formula_cells_are_rejected_instead_of_becoming_defaults(self):
        write_compare_workbook(self.book, [CompareSample("a.txt", "A")],
                               {"基准文件": "reference/base.txt"}, overwrite=True)
        import openpyxl
        wb = openpyxl.load_workbook(self.book)
        wb["参数"]["B3"] = "=1+1"
        wb.save(self.book)
        wb.close()
        with self.assertRaisesRegex(ValueError, "公式"):
            load_compare_config(self.book)


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="raman-compare-cli-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.out = self.root / "out"
        self.data.mkdir()
        self.reference = write_spectrum(self.root / "reference" / "base.txt",
                                        [200, 400, 600, 800])
        self.sample_a = write_spectrum(self.data / "a.txt", [202, 400, 604, 700])
        self.sample_b = write_spectrum(self.data / "b.txt", [200, 400, 600, 800])
        self.config = self.root / "compare.xlsx"

    def make_book(self, samples=None, extra=None):
        params = {"基准文件": "reference/base.txt", "匹配容差": "5",
                  "归一化(0/1)": "1", "峰位校正": "0",
                  "激光线排除半宽": "50"}
        params.update(extra or {})
        write_compare_workbook(self.config, samples or [
            CompareSample("a.txt", "样品 A"), CompareSample("b.txt", "样品 B"),
        ], params, overwrite=True)

    def run_cli(self):
        return subprocess.run(
            [sys.executable, "-I", "-B", "-X", "utf8", str(RAMAN / "plot_raman.py"),
             "--compare", str(self.config), "--src", str(self.data),
             "--out", str(self.out)],
            cwd=str(RAMAN), capture_output=True, text=True, encoding="utf-8")

    def test_end_to_end_outputs_and_values(self):
        self.make_book()
        completed = self.run_cli()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertTrue((self.out / "compare_reference_peaks.csv").is_file())
        self.assertTrue((self.out / "compare_peaks.csv").is_file())
        self.assertTrue((self.out / "compare_summary.csv").is_file())
        self.assertTrue((self.out / "compare_run.json").is_file())
        self.assertTrue((self.out / "compare_a.png").is_file())
        self.assertTrue((self.out / "compare_b.png").is_file())
        self.assertTrue((self.out / "compare_shift.png").is_file())
        with (self.out / "compare_summary.csv").open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 2)
        row_a = next(row for row in rows if row["label"] == "样品 A")
        self.assertEqual((row_a["matched_peaks"], row_a["missing_peaks"],
                          row_a["new_peaks"]), ("3", "1", "1"))
        self.assertAlmostEqual(float(row_a["match_rate"]), 0.75)
        row_b = next(row for row in rows if row["label"] == "样品 B")
        self.assertEqual((row_b["matched_peaks"], row_b["missing_peaks"],
                          row_b["new_peaks"]), ("4", "0", "0"))
        with (self.out / "compare_peaks.csv").open(encoding="utf-8-sig", newline="") as fh:
            peak_rows = list(csv.DictReader(fh))
        missing = [row for row in peak_rows if row["status"] == "missing"]
        new = [row for row in peak_rows if row["status"] == "new"]
        self.assertEqual(len(missing), 1)
        self.assertEqual(len(new), 1)
        self.assertEqual(missing[0]["sample_peak_cm-1"], "")
        self.assertEqual(new[0]["reference_peak_cm-1"], "")
        self.assertNotEqual((self.out / "compare_a.png").stat().st_size, 0)

    def test_missing_sample_is_partial_and_cleanup_waits_for_preflight(self):
        self.make_book([CompareSample("a.txt", "A"), CompareSample("missing.txt", "Missing")])
        completed = self.run_cli()
        self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
        with (self.out / "compare_summary.csv").open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual([row["status"] for row in rows], ["success", "failed"])
        self.assertIn("不存在", rows[1]["message"])

        self.out.mkdir(exist_ok=True)
        sentinel = self.out / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        blocked = self.root / "out" / "blocked.xlsx"
        shutil.copyfile(self.config, blocked)
        config_inside = subprocess.run(
            [sys.executable, "-I", "-B", "-X", "utf8", str(RAMAN / "plot_raman.py"),
             "--compare", str(blocked), "--src", str(self.data), "--out", str(self.out)],
            cwd=str(RAMAN), capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(config_inside.returncode, 1)
        self.assertTrue(sentinel.is_file())

    def test_no_matched_peaks_skips_shift_plot_and_records_warning(self):
        write_spectrum(self.data / "new-only.txt", [700])
        self.make_book([CompareSample("new-only.txt", "仅新增峰")])
        completed = self.run_cli()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertFalse((self.out / "compare_shift.png").exists())
        import json
        manifest = json.loads((self.out / "compare_run.json").read_text(encoding="utf-8"))
        self.assertIn("没有匹配峰", "\n".join(manifest["warnings"]))
        self.assertNotIn("compare_shift.png", manifest["outputs"])
        with (self.out / "compare_summary.csv").open(encoding="utf-8-sig", newline="") as fh:
            row = next(csv.DictReader(fh))
        self.assertEqual((row["matched_peaks"], row["missing_peaks"],
                          row["new_peaks"]), ("0", "4", "1"))

    def test_xmin_override_and_single_side_error(self):
        self.make_book()
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-X", "utf8", str(RAMAN / "plot_raman.py"),
             "--compare", str(self.config), "--src", str(self.data),
             "--out", str(self.out), "--xmin", "250"],
            cwd=str(RAMAN), capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("--xmin", completed.stderr)
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-X", "utf8", str(RAMAN / "plot_raman.py"),
             "--compare", str(self.config), "--src", str(self.data),
             "--out", str(self.out), "--xmin", "250", "--xmax", "750"],
            cwd=str(RAMAN), capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        with (self.out / "compare_summary.csv").open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertLessEqual(abs(float(rows[0]["xmin_cm-1"]) - 250), 1)
        self.assertLessEqual(abs(float(rows[0]["xmax_cm-1"]) - 750), 1)


if __name__ == "__main__":
    unittest.main()

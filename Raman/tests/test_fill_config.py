# -*- coding: utf-8 -*-
"""配置表填充（_fill_stack.py）的回归：stack 旧行为不变、montage 保留手改值。

全部用临时目录，不碰 Raman 目录里真实的 data/ 和 xlsx。
"""
from pathlib import Path
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image
import openpyxl

RAMAN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAMAN))

import _fill_stack  # noqa: E402
from _make_montage_xlsx import write_workbook  # noqa: E402
from plot_raman import main as plot_main  # noqa: E402


def write_spectrum(path, centers=(142.0, 566.0)):
    x = np.arange(-100.0, 3800.0, 1.0)
    y = np.full_like(x, 460.0)
    for c in centers:
        y += 500.0 * np.exp(-0.5 * ((x - c) / 4.0) ** 2)
    np.savetxt(path, np.column_stack([x, y]), fmt="%.4f")


def write_photo(path, size=(400, 250)):
    Image.new("RGB", size, (200, 205, 190)).save(path)


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="raman_fill_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_source(self):
        src = self.tmp / "src"
        src.mkdir()
        for group, positions in (("1", (1, 2, 3)), ("20", (2, 3))):
            write_photo(src / ("%s-1-1.png" % group))
            for n in positions:
                write_spectrum(src / ("%s-%d.txt" % (group, n)))
        return src

    def read_rows(self, book):
        wb = openpyxl.load_workbook(book)
        try:
            ws = wb.worksheets[0]
            head = [c.value for c in ws[1]]
            return [dict(zip(head, [c.value for c in row]))
                    for row in ws.iter_rows(min_row=2)]
        finally:
            wb.close()


class StackTests(TempCase):
    def test_stack_fill_keeps_order_and_autofills_height(self):
        data = self.tmp / "data"
        data.mkdir()
        for name in ("1-1.txt", "1-2.txt", "10-1.txt"):
            write_spectrum(data / name)
        book = self.tmp / "stack.xlsx"
        code = _fill_stack.fill_stack(str(book), str(data))
        self.assertEqual(code, 0)
        rows = self.read_rows(book)
        self.assertEqual([r["文件"] for r in rows], ["1-1.txt", "1-2.txt", "10-1.txt"])
        self.assertTrue(all(r["峰高阈值"] for r in rows))
        self.assertTrue(all(str(r["归一化(0/1)"]) == "1" for r in rows))

    def test_stack_keep_height_preserves_hand_value(self):
        data = self.tmp / "data"
        data.mkdir()
        write_spectrum(data / "1-1.txt")
        book = self.tmp / "stack.xlsx"
        _fill_stack.fill_stack(str(book), str(data))
        wb = openpyxl.load_workbook(book)
        wb.worksheets[0].cell(2, 5).value = 99
        wb.save(book)
        wb.close()
        _fill_stack.fill_stack(str(book), str(data), keep_height=True)
        self.assertEqual(self.read_rows(book)[0]["峰高阈值"], 99)


class MontageFillTests(TempCase):
    def test_fill_empty_template_expands_to_one_row_per_spectrum(self):
        src = self.make_source()
        book = self.tmp / "montage.xlsx"
        write_workbook(book, [])
        self.assertEqual(_fill_stack.fill_montage(str(book), str(src)), 0)
        rows = self.read_rows(book)
        self.assertEqual([(r["组名"], r["文件"]) for r in rows],
                         [("1", "1-1.txt"), ("1", "1-2.txt"), ("1", "1-3.txt"),
                          ("20", "20-2.txt"), ("20", "20-3.txt")])
        self.assertTrue(all(r["实物图"] for r in rows))
        self.assertEqual(rows[0]["颜色"], "#e01b24")
        self.assertEqual(rows[3]["颜色"], "#e01b24")  # 每组从第一个颜色重新排

    def test_refill_keeps_hand_edits_and_group_normalize(self):
        src = self.make_source()
        book = self.tmp / "montage.xlsx"
        write_workbook(book, [])
        _fill_stack.fill_montage(str(book), str(src))
        wb = openpyxl.load_workbook(book)
        ws = wb.worksheets[0]
        head = [c.value for c in ws[1]]
        col = {n: i + 1 for i, n in enumerate(head)}
        ws.cell(2, col["图例名称"]).value = "位置 A"
        ws.cell(3, col["颜色"]).value = "#123456"
        for row in range(2, ws.max_row + 1):  # 组 1 三行统一改成 1
            if ws.cell(row, col["组名"]).value == "1":
                ws.cell(row, col["归一化(0/1)"]).value = 1
        wb.save(book)
        wb.close()

        self.assertEqual(_fill_stack.fill_montage(str(book), str(src)), 0)
        rows = self.read_rows(book)
        self.assertEqual(rows[0]["图例名称"], "位置 A")
        self.assertEqual(rows[1]["颜色"], "#123456")
        self.assertEqual(str(rows[2]["归一化(0/1)"]), "1")
        self.assertEqual(str(rows[0]["归一化(0/1)"]), "1")  # 同组跟着走
        self.assertEqual(str(rows[3]["归一化(0/1)"]), "0")  # 组 20 不受影响

    def test_new_row_inherits_group_normalize_and_missing_rows_drop(self):
        src = self.make_source()
        book = self.tmp / "montage.xlsx"
        write_workbook(book, [
            ["1", "1-1.png", "1-1.txt", "一", 1, 1, None, None, None, None, None],
            ["1", "", "1-2.txt", "二", 1, 1, None, None, None, None, None],
        ])
        # 组 1 新增 1-3，组 20 整组从表里消失（表里本来也没有）
        self.assertEqual(_fill_stack.fill_montage(str(book), str(src)), 0)
        rows = self.read_rows(book)
        self.assertEqual(len(rows), 5)
        new_row = [r for r in rows if r["文件"] == "1-3.txt"][0]
        self.assertEqual(str(new_row["归一化(0/1)"]), "1")
        self.assertEqual(new_row["实物图"], "1-1-1.png")

        # 把 20 组从表里补进去后再删源目录文件，重跑应删掉那一行
        _fill_stack.fill_montage(str(book), str(src))
        (src / "20-2.txt").unlink()
        _fill_stack.fill_montage(str(book), str(src))
        files = [r["文件"] for r in self.read_rows(book)]
        self.assertNotIn("20-2.txt", files)
        self.assertIn("1-3.txt", files)


class NestedStackTests(TempCase):
    """顶层 0 系列 + 一级子目录两个数据集：同名的 1-1.txt 不能撞行。"""

    def make_nested(self):
        root = self.tmp / "src"
        root.mkdir()
        write_spectrum(root / "0-1.txt")
        write_spectrum(root / "0-2.txt")
        for d in ("a", "b"):
            (root / d).mkdir()
            write_spectrum(root / d / "1-1.txt")
        return root

    def test_nested_rows_get_folder_prefix_and_label(self):
        root = self.make_nested()
        book = self.tmp / "stack.xlsx"
        self.assertEqual(_fill_stack.fill_stack(str(book), str(root)), 0)
        rows = self.read_rows(book)
        self.assertEqual([r["文件"] for r in rows],
                         ["0-1.txt", "0-2.txt",
                          os.path.join("a", "1-1.txt"), os.path.join("b", "1-1.txt")])
        self.assertIsNone(rows[0]["图例名称"])
        self.assertEqual(rows[2]["图例名称"], "a 1-1")
        self.assertTrue(rows[0]["峰高阈值"])

    def test_exclude_drops_matching_rows(self):
        root = self.make_nested()
        book = self.tmp / "stack.xlsx"
        self.assertEqual(_fill_stack.fill_stack(
            str(book), str(root), exclude=("0-2.txt",)), 0)
        files = [r["文件"] for r in self.read_rows(book)]
        self.assertNotIn("0-2.txt", files)
        self.assertEqual(len(files), 3)

    def test_single_figures_keep_folder_prefix(self):
        root = self.make_nested()
        book = self.tmp / "stack.xlsx"
        _fill_stack.fill_stack(str(book), str(root))
        out = self.tmp / "out"
        self.assertEqual(plot_main(["--stack", str(book), "--src", str(root),
                                    "--out", str(out)]), 0)
        self.assertTrue((out / "a-1-1.png").is_file())
        self.assertTrue((out / "b-1-1.png").is_file())
        self.assertTrue((out / "stack.png").is_file())


if __name__ == "__main__":
    unittest.main()

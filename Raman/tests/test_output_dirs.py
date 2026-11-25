# -*- coding: utf-8 -*-
"""三种 Raman 模式默认输出目录的回归用例。"""
from pathlib import Path
import unittest

from plot_raman import default_out_dir


class DefaultOutputTests(unittest.TestCase):
    def test_each_mode_gets_its_own_subfolder(self):
        here = Path(r"C:\Raman")
        self.assertEqual(Path(default_out_dir(here)).parts[-2:], ("out", "single"))
        self.assertEqual(Path(default_out_dir(here, stack="stack.xlsx")).parts[-2:],
                         ("out", "stack"))
        self.assertEqual(Path(default_out_dir(here, compare="compare.xlsx")).parts[-2:],
                         ("out", "compare"))
        self.assertEqual(Path(default_out_dir(here, montage="")).parts[-2:],
                         ("out", "montage"))
        self.assertEqual(Path(default_out_dir(here, montage="montage.xlsx",
                                              compare="c.xlsx")).parts[-1],
                         "montage")

    def test_explicit_compare_wins_over_stack_placeholder(self):
        here = Path(r"C:\Raman")
        self.assertEqual(Path(default_out_dir(here, compare="c.xlsx", stack=None)).parts[-1],
                         "compare")


if __name__ == "__main__":
    unittest.main()

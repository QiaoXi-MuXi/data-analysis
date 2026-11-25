# -*- coding: utf-8 -*-
"""文件名自然排序的回归用例。"""
import unittest

from plot_raman import natural_key


class NaturalOrderTests(unittest.TestCase):
    def test_numeric_chunks_compare_as_integers(self):
        names = ["10-1.txt", "2-1.txt", "1-10.txt", "1-2.txt", "1-1.txt"]
        self.assertEqual(sorted(names, key=natural_key),
                         ["1-1.txt", "1-2.txt", "1-10.txt", "2-1.txt", "10-1.txt"])

    def test_case_and_text_segments_are_deterministic(self):
        names = ["A-10.txt", "a-2.txt", "B-1.txt", "A-1.txt"]
        self.assertEqual(sorted(names, key=natural_key),
                         ["A-1.txt", "a-2.txt", "A-10.txt", "B-1.txt"])


if __name__ == "__main__":
    unittest.main()

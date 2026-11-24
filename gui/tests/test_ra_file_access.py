# -*- coding: utf-8 -*-
"""结果表权限提示回归；隔离输出，数值识别使用固定测试记录。"""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from copy import deepcopy
import ctypes
from ctypes import wintypes
import errno
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'Ra'))

from openpyxl import Workbook, load_workbook
import extract_ra
import ra_workflow


@contextmanager
def windows_read_lock(path):
    """允许读取，但拒绝写入和替换，模拟工作簿被打开时的共享限制。"""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        kernel.CloseHandle(handle)


class ResultAccessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ra-access-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'input'
        self.source.mkdir()
        self.image = self.source / '1-1.bmp'
        self.image.write_bytes(b'isolated mock screenshot')
        self.output = self.root / 'results'
        self.output.mkdir()
        self.book = self.output / 'ra_results.xlsx'
        wb = Workbook()
        wb.active.title = '用户保留页'
        wb.active['A1'] = '保留内容'
        wb.save(self.book)
        wb.close()
        self.before = self.book.read_bytes()
        self.record = dict(name=self.image.name, path=self.image, ra=1., pv=2.,
                           rms=1.2, unit='nm', sha256=ra_workflow.sha(self.image))
        self.start_patch(patch.object(extract_ra, 'ensure_bat_encoding'))
        self.reader = self.start_patch(patch.object(ra_workflow, 'read_bmp',
                                                    side_effect=lambda *a: [deepcopy(self.record)]))

    def start_patch(self, item):
        result = item.start()
        self.addCleanup(item.stop)
        return result

    def run_analysis(self):
        stderr, stdout = io.StringIO(), io.StringIO()
        args = ['extract_ra.py', '--dir', str(self.source), '--out', str(self.output), '--no-plot']
        with patch.object(sys, 'argv', args), redirect_stderr(stderr), redirect_stdout(stdout):
            code = ra_workflow.run(extract_ra)
        return code, stderr.getvalue(), stdout.getvalue()

    def assert_blocked(self, result):
        code, message, _ = result
        self.assertEqual(code, 4)
        self.assertIn('请保存并关闭', message)
        self.assertIn('Excel / WPS', message)
        self.assertIn(str(self.book), message)
        self.assertIn('只读', message)
        self.assertEqual(self.book.read_bytes(), self.before)
        self.assertFalse((self.output / 'out').exists())
        self.assertFalse(list(self.output.glob('.ra-tmp-*')))

    def test_result_read_permission_has_chinese_hint(self):
        original = ra_workflow.sha

        def blocked(path):
            if path == self.book:
                raise PermissionError(errno.EACCES, 'Permission denied', str(path))
            return original(path)

        with patch.object(ra_workflow, 'sha', side_effect=blocked):
            self.assert_blocked(self.run_analysis())
        self.reader.assert_not_called()

    def test_replace_destination_filename2_has_chinese_hint(self):
        def blocked(source, destination):
            raise PermissionError(errno.EACCES, 'Permission denied', str(source), None, str(destination))

        with patch.object(ra_workflow.os, 'replace', side_effect=blocked):
            self.assert_blocked(self.run_analysis())

    def test_input_permission_does_not_claim_result_is_locked(self):
        self.reader.side_effect = PermissionError(errno.EACCES, 'Permission denied', str(self.image))
        code, message, _ = self.run_analysis()
        self.assertEqual(code, 4)
        self.assertIn(self.image.name, message)
        self.assertNotIn('请保存并关闭', message)
        self.assertEqual(self.book.read_bytes(), self.before)

    def test_other_os_error_keeps_original_reason(self):
        self.reader.side_effect = OSError(errno.ENOSPC, 'disk full')
        code, message, _ = self.run_analysis()
        self.assertEqual(code, 4)
        self.assertIn('disk full', message)
        self.assertNotIn('请保存并关闭', message)

    def test_success_preserves_existing_user_sheet(self):
        code, message, _ = self.run_analysis()
        self.assertEqual((code, message), (0, ''))
        wb = load_workbook(self.book)
        try:
            self.assertEqual(wb['用户保留页']['A1'].value, '保留内容')
            self.assertIn('粗糙度分析', wb.sheetnames)
        finally:
            wb.close()

    @unittest.skipUnless(os.name == 'nt', '需要Windows共享锁')
    def test_windows_lock_before_analysis(self):
        with windows_read_lock(self.book):
            self.assert_blocked(self.run_analysis())
        self.reader.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', '需要Windows共享锁')
    def test_windows_lock_during_commit(self):
        original = ra_workflow.os.replace

        def locked_replace(source, destination):
            with windows_read_lock(self.book):
                original(source, destination)

        with patch.object(ra_workflow.os, 'replace', side_effect=locked_replace):
            self.assert_blocked(self.run_analysis())


if __name__ == '__main__':
    unittest.main()

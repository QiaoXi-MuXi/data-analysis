# -*- coding: utf-8 -*-
"""以隔离的便携目录验证实际EXE；不读取实验数据。"""
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(os.name == 'nt', '需要Windows EXE运行环境')
class LauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='launcher-tests-')
        cls.base = Path(cls.temporary.name)
        cls.addClassCleanup(cls.temporary.cleanup)
        source = cls.base / 'Stub.cs'
        # 固定本地桩只用于验证进程参数、隔离和退出码，不作为产品运行时。
        source.write_text('''using System; using System.IO;
class Stub { static int Main(string[] args) {
 Console.OutputEncoding = new System.Text.UTF8Encoding(false);
 bool check = Array.IndexOf(args, "--check") >= 0;
 string trace = Environment.GetEnvironmentVariable("LAUNCHER_TEST_TRACE");
 File.WriteAllLines(trace + (check ? ".check" : ".gui"), new string[] {
  Directory.GetCurrentDirectory(), String.Join("|", args),
  Environment.GetEnvironmentVariable("PYTHONHOME") ?? "",
  Environment.GetEnvironmentVariable("PYTHONPATH") ?? "",
  Environment.GetEnvironmentVariable("PYTHONNOUSERSITE") ?? "" });
 if (check && Environment.GetEnvironmentVariable("LAUNCHER_TEST_FAIL") == "1") {
  Console.Error.WriteLine("测试依赖缺失"); return 7; }
 Console.WriteLine("GUI依赖正常"); return 0;
} }''', encoding='utf-8')
        compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        if not compiler.exists():
            compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework/v4.0.30319/csc.exe'
        cls.stub = cls.base / 'stub.exe'
        result = subprocess.run([str(compiler), '/nologo', '/codepage:65001', '/out:' + str(cls.stub), str(source)],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        if result.returncode:
            raise RuntimeError(result.stdout.decode('utf-8', errors='replace'))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='中文 空格-', dir=self.base)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.exe = self.root / 'DataAnalysis.exe'
        shutil.copyfile(ROOT / 'DataAnalysis.exe', self.exe)
        self.env = dict(os.environ, LAUNCHER_TEST_TRACE=str(self.root / 'trace'),
                        PYTHONHOME='invalid-parent-python', PYTHONPATH='invalid-parent-path')
        self.env.pop('LAUNCHER_TEST_FAIL', None)

    def create_runtime(self):
        (self.root / 'python').mkdir()
        for name in ('python.exe', 'pythonw.exe'):
            shutil.copyfile(self.stub, self.root / 'python' / name)
        (self.root / 'gui').mkdir()
        (self.root / 'gui/app.py').write_text('# 测试占位，不会被执行\n', encoding='utf-8')

    def launch(self, *args):
        return subprocess.run([str(self.exe), *args], cwd=self.base, env=self.env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=40)

    def test_binary_is_windows_gui_subsystem(self):
        content = self.exe.read_bytes()
        pe = struct.unpack_from('<I', content, 0x3c)[0]
        self.assertEqual(content[pe:pe + 4], b'PE\0\0')
        self.assertEqual(struct.unpack_from('<H', content, pe + 24 + 68)[0], 2)

    def test_missing_python_returns_chinese_error_without_dialog_in_check_mode(self):
        result = self.launch('--check')
        self.assertEqual(result.returncode, 1)
        self.assertIn('缺少便携 Python', result.stderr.decode('utf-8'))
        self.assertTrue((self.root / 'out/gui/launcher_error.log').exists())

    def test_missing_app_returns_chinese_error(self):
        self.create_runtime()
        (self.root / 'gui/app.py').unlink()
        result = self.launch('--check')
        self.assertEqual(result.returncode, 1)
        self.assertIn('gui\\app.py', result.stderr.decode('utf-8'))

    def test_check_failure_preserves_details_and_does_not_start_gui(self):
        self.create_runtime()
        self.env['LAUNCHER_TEST_FAIL'] = '1'
        result = self.launch('--check')
        self.assertEqual(result.returncode, 1)
        self.assertIn('测试依赖缺失', result.stderr.decode('utf-8'))
        self.assertFalse((self.root / 'trace.gui').exists())
        self.assertIn('测试依赖缺失', (self.root / 'out/gui/startup_check.log').read_text(encoding='utf-8'))

    def test_relocated_check_isolated_environment_and_arguments(self):
        self.create_runtime()
        result = self.launch('--check')
        self.assertEqual(result.returncode, 0, result.stderr)
        trace = (self.root / 'trace.check').read_text(encoding='utf-8-sig').splitlines()
        self.assertEqual(trace[0], str(self.root))
        self.assertEqual(trace[1], '-I|-B|-X|utf8|' + str(self.root / 'gui/app.py') + '|--check')
        self.assertEqual(trace[2:], ['', '', '1'])
        self.assertFalse((self.root / 'trace.gui').exists())

    def test_normal_launch_hands_off_to_windowed_runtime(self):
        self.create_runtime()
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        trace = (self.root / 'trace.gui').read_text(encoding='utf-8-sig').splitlines()
        self.assertEqual(trace[0], str(self.root))
        self.assertEqual(trace[1], '-I|-B|-X|utf8|' + str(self.root / 'gui/app.py'))

    def test_unknown_argument_does_not_start_runtime(self):
        result = self.launch('--unknown')
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.root / 'trace.check').exists())


if __name__ == '__main__':
    unittest.main()

# -*- coding: utf-8 -*-
"""P4: SCI-Pic mapping detection, service contract, history and navigation regression."""
from pathlib import Path
import hashlib
import json
import os
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
GUI = Path(__file__).resolve().parents[1]
ROOT = GUI.parent
sys.path.insert(0, str(GUI))
sys.path.insert(0, str(ROOT))

import openpyxl  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import sci_pic  # noqa: E402
import runs  # noqa: E402
from app import Window  # noqa: E402
from model import PlotRequest, PlotResult, SourceRef  # noqa: E402


class ScipicP4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        # 沙箱建在系统临时目录，用例结束自动删除；项目根目录不再留 _*_test 残件。
        self.temp = tempfile.TemporaryDirectory(prefix='scipic-p4-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self._fixture()

    def _fixture(self):
        (self.root / 'sci-pic' / 'templates').mkdir(parents=True)
        (self.root / 'sci-pic' / 'previews').mkdir(parents=True)
        (self.root / 'sci-pic' / 'out').mkdir(parents=True)
        (self.root / 'plot_style').mkdir()
        for src in (ROOT / 'plot_style').glob('*.py'):
            (self.root / 'plot_style' / src.name).write_bytes(src.read_bytes())
        (self.root / 'sci-pic' / 'style_integration.py').write_bytes(
            (ROOT / 'sci-pic' / 'style_integration.py').read_bytes())
        (self.root / 'sci-pic' / 'plot_sci_pic.py').write_bytes(
            (ROOT / 'sci-pic' / 'plot_sci_pic.py').read_bytes())
        (self.root / 'sci-pic' / 'previews' / 'sci_pic.png').write_bytes(b'\x89PNG\r\n\x1a\n')
        (self.root / 'sci-pic' / 'templates.json').write_text(json.dumps({
            'version': 1,
            'templates': [
                {'id': 'sci_pic', 'title': '柱状折线', 'workbook': 'templates/sci_pic.xlsx',
                 'preview': 'previews/sci_pic.png', 'plot_type': '柱状折线',
                 'description': '示例', 'example': '示例'},
                {'id': 'paired', 'title': '双组对比', 'workbook': 'templates/sci_pic.xlsx',
                 'preview': 'previews/sci_pic.png', 'plot_type': '双组柱状',
                 'description': '示例', 'example': '示例'}]}, ensure_ascii=False), encoding='utf-8')
        wb = openpyxl.Workbook()
        wb.active.title = '数据'
        wb.create_sheet('设置')
        wb.save(self.root / 'sci-pic' / 'templates' / 'sci_pic.xlsx')  # noqa: PIE798

    def _csv_source(self, name='result.csv'):
        path = self.root / name
        path.write_text('类别,数值,分组\n甲,7,基准\n乙,9,基准\n丙,11,实验\n丁,13,实验\n',
                        encoding='utf-8')
        return path

    def test_catalog_discovery(self):
        entries = sci_pic.catalog(self.root)
        self.assertEqual({item['id'] for item in entries}, {'sci_pic', 'paired'})

    def test_propose_mapping_detects_candidates(self):
        source = self._csv_source()
        proposal = sci_pic.propose_mapping(source, 'Nanoindentation Analysis')
        self.assertTrue(proposal['supported'])
        self.assertIn('类别', proposal['category_candidates'])
        self.assertIn('数值', proposal['value_candidates'])
        self.assertIn('分组', proposal['group_candidates'])
        self.assertEqual(proposal['rows'], 4)

    def test_create_work_copy_valid_mapping_keeps_source_readonly(self):
        source = self._csv_source()
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        entry = next(item for item in sci_pic.catalog(self.root) if item['id'] == 'sci_pic')
        out = sci_pic.create_work_copy_from_mapping(
            self.root, entry, source, 'Nanoindentation Analysis', 'run-1',
            {'category': '类别', 'value': '数值'}, target_dir=self.root / 'out')
        manifest = json.loads(out['manifest_path'].read_text(encoding='utf-8'))
        self.assertEqual(manifest['source_project'], 'Nanoindentation Analysis')
        self.assertEqual(manifest['source_run_id'], 'run-1')
        self.assertTrue(manifest['source_sha256'])
        self.assertEqual(manifest['source_mtime'], manifest['source_mtime'])
        self.assertEqual(manifest['rows_written'], 4)
        self.assertEqual(manifest['template_id'], 'sci_pic')
        # 源文件哈希在副本生成后保持不变。
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)

    def test_invalid_mapping_is_rejected(self):
        source = self._csv_source()
        entry = next(item for item in sci_pic.catalog(self.root) if item['id'] == 'sci_pic')
        with self.assertRaises(sci_pic.SourceMappingError):
            sci_pic.create_work_copy_from_mapping(
                self.root, entry, source, 'Raman', 'run-x',
                {'category': '数值', 'value': '类别'})

    def test_raw_spectrum_text_is_not_mapped(self):
        source = self.root / 'spectrum.txt'
        source.write_text('520.0,1234\n520.5,1300\n521.0,1280\n', encoding='utf-8')
        with self.assertRaises(sci_pic.SourceMappingError):
            sci_pic.propose_mapping(source, 'Raman')

    def test_verify_source_ok_missing_and_changed(self):
        source = self._csv_source()
        entry = next(item for item in sci_pic.catalog(self.root) if item['id'] == 'sci_pic')
        out = sci_pic.create_work_copy_from_mapping(
            self.root, entry, source, 'Raman', 'run-2',
            {'category': '类别', 'value': '数值'})
        self.assertEqual(sci_pic.verify_source(out['manifest_path'])['status'], 'ok')
        source.write_text('类别,数值\n甲,99\n', encoding='utf-8')
        self.assertEqual(sci_pic.verify_source(out['manifest_path'])['status'], 'source-changed')
        source.unlink()
        self.assertEqual(sci_pic.verify_source(out['manifest_path'])['status'], 'source-missing')

    def test_request_result_contract_round_trip(self):
        request = PlotRequest('Raman', 'run-2',
                              (SourceRef('result.csv', 'h', '2026-11-25T00:00:00', 12),),
                              'sci_pic', 'copy.xlsx', 'out', mapping={'category': '类别'})
        restored = PlotRequest.from_dict(request.to_dict())
        self.assertEqual(restored.source_project, request.source_project)
        self.assertEqual(restored.mapping, request.mapping)
        self.assertEqual(restored.style_overrides, {})
        result = PlotResult('success', ('figure.png',), log_path='run.log',
                            manifest='copy.manifest.json', resolved_config='resolved.json')
        self.assertEqual(PlotResult.from_dict(result.to_dict()), result)

    def _prepared_workbook(self):
        source = self._csv_source()
        entry = next(item for item in sci_pic.catalog(self.root) if item['id'] == 'sci_pic')
        out = sci_pic.create_work_copy_from_mapping(
            self.root, entry, source, 'Raman', 'run-x',
            {'category': '类别', 'value': '数值'})
        return entry, Path(out['workbook'])

    def test_prepare_record_carries_source_identity(self):
        entry, workbook = self._prepared_workbook()
        prepared = sci_pic.prepare_plot(
            self.root, entry, workbook, self.root / 'out',
            source_project='Nanoindentation Analysis', source_run_id='run-9',
            manifest_path='copy.manifest.json')
        self.assertEqual(prepared.record['source_project'], 'Nanoindentation Analysis')
        self.assertEqual(prepared.record['source_run_id'], 'run-9')
        self.assertEqual(prepared.record['manifest_path'], 'copy.manifest.json')
        result = sci_pic.collect_plot_result(prepared.record)
        self.assertEqual(result.status, 'prepared')
        self.assertEqual(result.manifest, 'copy.manifest.json')

    def test_page_restores_state_without_autorun(self):
        window = Window(self.root)
        self.addCleanup(lambda: window.pool.waitForDone(10000))
        page = window.scipic_page
        self.assertFalse(page.task)
        self.assertFalse(page.preparing)
        self.assertFalse(hasattr(page, 'auto_run_timer'))
        state = page.state_values()
        self.assertIn('template_id', state)

    def test_navigation_reaches_same_scipic_page(self):
        window = Window(self.root)
        self.addCleanup(lambda: window.pool.waitForDone(10000))
        window.navigate_to_key('sci-pic')
        self.assertIs(window.stack.currentWidget(), window.scipic_page)
        window.navigate_to_key('Ra')
        self.assertIsNot(window.stack.currentWidget(), window.scipic_page)
        window.navigate_to_key('sci-pic')
        self.assertIs(window.stack.currentWidget(), window.scipic_page)

    def test_repeat_prepare_guard_blocks_second_submit(self):
        window = Window(self.root)
        self.addCleanup(lambda: window.pool.waitForDone(10000))
        page = window.scipic_page
        page.preparing = True
        page.run()
        self.assertIsNone(page.current_prepared)
        self.assertIsNone(page.current_record)

    def test_history_entry_carries_back_link(self):
        entry, workbook = self._prepared_workbook()
        prepared = sci_pic.prepare_plot(
            self.root, entry, workbook, self.root / 'out',
            source_project='Raman', source_run_id='run-history')
        records = runs.history(self.root, 'sci-pic')
        self.assertTrue(any(record['id'] == prepared.record['id'] for record in records))


if __name__ == '__main__':
    unittest.main()

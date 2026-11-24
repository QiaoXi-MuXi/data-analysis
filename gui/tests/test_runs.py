# -*- coding: utf-8 -*-
"""独立运行记录的回归；不执行科学计算，也不访问正式输出。"""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from documents import Document
from model import PROJECTS, default_options
from runs import prepare_run, finish_run, history


class RunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='data-analysis-run-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = PROJECTS[0]
        folder = self.root / 'Ra'
        (folder / 'data').mkdir(parents=True)
        (folder / 'extract_ra.py').write_text('# 测试适配器', encoding='utf-8')
        self.source = folder / 'data' / '样本.bmp'
        self.source.write_bytes(b'fixture')
        self.options = default_options(self.root, self.project)
        self.doc = Document('Ra', folder, [dict(file='data/样本.bmp', enabled=True)])

    def prepare(self):
        return prepare_run(self.root, self.project, self.options, self.doc)

    def test_runs_are_isolated_and_snapshots_frozen(self):
        first, second = self.prepare(), self.prepare()
        self.assertNotEqual(first.path.parent, second.path.parent)
        self.assertTrue((first.path.parent / 'project.json').is_file())
        self.assertTrue((first.path.parent / 'config.xlsx').is_file())
        self.assertEqual(first.record['input_count'], 1)
        self.assertEqual(len(history(self.root, 'Ra')), 2)
        self.assertFalse(list((self.root / 'out/gui/preflight').glob('*.xlsx')))

    def test_result_manifest_excludes_old_outputs(self):
        (self.root / 'Ra' / 'old.png').write_bytes(b'old')
        prepared = self.prepare()
        actual = prepared.command.results[0]
        actual.parent.mkdir(parents=True, exist_ok=True)
        actual.write_bytes(b'new')
        record = finish_run(prepared, 0, '完成', self.root / 'log.txt')
        self.assertEqual(record['status'], 'success')
        self.assertEqual(record['files'], [str(actual.resolve())])

    def test_changed_source_marks_results_for_review(self):
        prepared = self.prepare()
        self.source.write_bytes(b'changed')
        record = finish_run(prepared, 0, '完成', self.root / 'log.txt')
        self.assertEqual(record['status'], 'review')
        self.assertEqual(record['changed_inputs'], [str(self.source)])

    def test_failure_is_saved_in_history(self):
        prepared = self.prepare()
        record = finish_run(prepared, -1, '启动失败', self.root / 'log.txt')
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(history(self.root, 'Ra')[0]['status'], 'failed')

    def test_invalid_draft_does_not_create_run(self):
        self.doc.rows[0]['cutoff'] = '非法数字'
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse((self.root / 'Ra/out/runs').exists())


if __name__ == '__main__':
    unittest.main()

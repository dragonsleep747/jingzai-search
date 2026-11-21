import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

import indexer
import parsers
import searcher
from qt_frontend.backend import Store
from qt_frontend import import_service as imports
from qt_frontend.search_experience import rank_groups


class TestNativeImport(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='jingzai-native-import-')
        self.root = Path(self.folder.name)
        self.before = indexer.LIBRARY_DIR, parsers._OCR_CACHE_DIR
        self.store = Store(self.root)
        self.stop = threading.Event()

    def tearDown(self):
        for tls in (indexer._ro_tls, searcher._conn_tls):
            for conn in getattr(tls, 'conns', {}).values():
                conn.close()
            tls.conns = {}
        indexer.LIBRARY_DIR, parsers._OCR_CACHE_DIR = self.before
        self.folder.cleanup()

    def source(self, name='资料.txt', text='原生预检与复制入库。'):
        path = self.root / 'sources' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def plan(self, paths, link=False):
        return imports.analyze(self.store, [str(path) for path in paths], link, lambda _: None, self.stop)

    def execute(self, plans, progress=lambda _: None):
        return imports.execute(self.store, plans, progress, self.stop)

    def conflicting(self):
        first = self.source(text='旧版文件内容需要保留。')
        target, status, _ = indexer.import_file_to_library(str(first), self.store.db)
        self.assertEqual(status, 'ok')
        first.write_text('新版文件内容已经修订。', encoding='utf-8')
        plan = self.plan([first])[0]
        self.assertEqual(plan.kind, 'conflict')
        return first, Path(target), plan

    def test_preflight_deduplicates_paths_without_importing(self):
        source = self.source()
        plans = self.plan([source, source])
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].kind, 'new')
        self.assertEqual(self.store.statistics()['files'], 0)

    def test_copy_then_original_deletion_keeps_searchable_library_file(self):
        source = self.source()
        report = self.execute(self.plan([source]))
        source.unlink()
        self.assertEqual(report['ok'], 1)
        self.assertGreater(self.store.search('复制入库')['hits'], 0)
        self.assertTrue(Path(report['details'][0]['path']).is_file())

    def test_link_mode_preserves_original_path(self):
        source = self.source()
        report = self.execute(self.plan([source], True))
        self.assertEqual(report['ok'], 1)
        self.assertEqual(self.store.library()[0]['path'], str(source))
        self.assertFalse(any((self.root / 'library').rglob('*.txt')))

    def test_duplicate_is_unchecked_and_reported_as_skipped(self):
        source = self.source()
        self.execute(self.plan([source]))
        plans = self.plan([source])
        self.assertEqual(plans[0].kind, 'dup_skip')
        self.assertFalse(plans[0].checked)
        report = self.execute(plans)
        self.assertEqual(report['skipped'], 1)
        self.assertEqual(self.store.statistics()['files'], 1)

    def test_conflict_defaults_to_keep_both(self):
        source, target, plan = self.conflicting()
        self.assertEqual(plan.choice, 'keep_both')
        report = self.execute([plan])
        self.assertEqual(report['ok'], 1)
        self.assertEqual(self.store.statistics()['files'], 2)
        self.assertIn('旧版文件', target.read_text(encoding='utf-8'))

    def test_explicit_replace_keeps_old_file_backup(self):
        source, target, plan = self.conflicting()
        plan.choice = 'replace'
        report = self.execute([plan])
        self.assertEqual(report['ok'], 1)
        self.assertIn('新版文件', target.read_text(encoding='utf-8'))
        backups = list((self.root / 'backup' / 'replaced_files').rglob('*.txt'))
        self.assertEqual(len(backups), 1)
        self.assertIn('旧版文件', backups[0].read_text(encoding='utf-8'))

    def test_replace_parse_failure_restores_old_file_and_search(self):
        source, target, plan = self.conflicting()
        plan.choice = 'replace'
        original = indexer.index_single_file
        def parse(path, *args, **kwargs):
            if '新版文件' in Path(path).read_text(encoding='utf-8'):
                return 'error: simulated parser failure'
            return original(path, *args, **kwargs)
        with patch.object(indexer, 'index_single_file', side_effect=parse):
            result = self.execute([plan])
        self.assertEqual(result['failed'], 1)
        self.assertIn('旧版文件', target.read_text(encoding='utf-8'))
        self.assertGreater(self.store.search('旧版文件')['hits'], 0)

    def test_missing_snapshot_aborts_replace(self):
        source, target, plan = self.conflicting()
        plan.choice = 'replace'
        before = target.read_bytes()
        with patch.object(imports, 'snapshot_db', return_value=None), self.assertRaises(RuntimeError):
            self.execute([plan])
        self.assertEqual(target.read_bytes(), before)

    def test_source_changed_after_preflight_is_not_imported(self):
        source = self.source()
        plans = self.plan([source])
        source.write_text('预检之后被其他程序改动。', encoding='utf-8')
        report = self.execute(plans)
        self.assertEqual(report['failed'], 1)
        self.assertEqual(self.store.statistics()['files'], 0)

    def test_destination_changed_after_preflight_is_not_replaced(self):
        source, target, plan = self.conflicting()
        plan.choice = 'replace'
        target.write_text('其他程序刚刚写入的版本。', encoding='utf-8')
        report = self.execute([plan])
        self.assertEqual(report['failed'], 1)
        self.assertIn('其他程序', target.read_text(encoding='utf-8'))

    def test_archive_members_are_indexed_and_content_duplicates_skipped(self):
        source = self.source(text='压缩包重复内容。')
        self.execute(self.plan([source]))
        archive = self.root / 'members.zip'
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr('nested/new.txt', '压缩包独有内容。'.encode('utf-8'))
            zipped.writestr('same.txt', source.read_bytes())
        result = self.execute(self.plan([archive]))
        self.assertEqual((result['ok'], result['skipped']), (1, 1))
        self.assertGreater(self.store.search('压缩包独有')['hits'], 0)
        self.assertTrue(all(Path(row['path']).is_file() for row in result['details'] if row['state'] == '成功'))

    def test_cancel_keeps_completed_files_and_reports_unprocessed(self):
        first = self.source('a.txt', '首个入库任务。')
        second = self.source('b.txt', '第二个入库任务。')
        def progress(value):
            if value.get('current') == 1:
                self.stop.set()
        result = self.execute(self.plan([first, second]), progress)
        self.assertTrue(result['stopped'])
        self.assertEqual(result['ok'], 1)
        self.assertEqual(self.store.statistics()['files'], 1)
        self.assertEqual(result['plans'][1]['result'], '')

    def test_skipped_choice_can_be_changed_back(self):
        plan = self.plan([self.source()])[0]
        plan.choice = 'skip'
        self.assertIn('copy', plan.choices())


class TestSearchRanking(unittest.TestCase):
    def test_title_priority_hit_count_and_date_are_separate_sort_modes(self):
        first = {'path': 'a', 'name': '安全管理制度', 'hits': 2, 'matches': []}
        second = {'path': 'b', 'name': '综合汇编', 'hits': 100, 'matches': []}
        details = {'a': {'line_count': 100, 'effective_date': '2020-01-01'},
                   'b': {'line_count': 10000, 'effective_date': '2025-01-01'}}
        original = [second, first]
        self.assertEqual(rank_groups(original, details, '安全')[0]['path'], 'a')
        self.assertEqual(rank_groups(original, details, '安全', 'hits')[0]['path'], 'b')
        self.assertEqual(rank_groups(original, details, '安全', 'date')[0]['path'], 'b')
        self.assertEqual(original, [second, first])

import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from contextlib import closing

import core
import indexer
import parsers
import searcher
from qt_frontend.backend import Store, export_csv, preview_html


class TestQtBackend(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='jingzai-qt-test-')
        self.root = Path(self.temporary.name)
        self.library_before, self.cache_before = indexer.LIBRARY_DIR, parsers._OCR_CACHE_DIR
        self.store = Store(self.root)
        self.a = self.root / '安全管理.txt'
        self.b = self.root / '差旅报销.txt'
        self.a.write_text('第一章 总则\n第一条 安全员参加安全培训。\n第二条 每年组织应急演练。\n', encoding='utf-8')
        self.b.write_text('第一章 费用\n第一条 职工出差后办理报销。\n', encoding='utf-8')
        self.store.index_files([str(self.a), str(self.b)], lambda _: None, threading.Event())

    def tearDown(self):
        # 只读连接按线程缓存，释放后才能在 Windows 删除临时库。
        for tls in (indexer._ro_tls, searcher._conn_tls):
            for conn in list(getattr(tls, 'conns', {}).values()):
                conn.close()
            if tls:
                tls.conns = {}
        indexer.LIBRARY_DIR, parsers._OCR_CACHE_DIR = self.library_before, self.cache_before
        self.temporary.cleanup()

    def test_existing_config_is_never_rewritten(self):
        old = self.root / 'config.json'
        raw = '{"dark":true,"custom_synonyms":"安全员 = 专职安全管理员"}'.encode('utf-8')
        old.write_bytes(raw)
        store = Store(self.root)
        store.settings['dark'] = False
        store.save()
        self.assertEqual(old.read_bytes(), raw)
        self.assertTrue((self.root / 'config.qt.json').is_file())

    def test_search_matches_original_engine(self):
        for query in ('安全', '安全 培训', '安全|报销', '安全 -演练', '名称:安全'):
            expected = searcher.aggregate_results(searcher.search(self.store.db, query, only_active=True))
            actual = self.store.search(query, synonyms=False)['groups']
            self.assertEqual(actual, expected, query)

    def test_synonyms_expand_whole_words(self):
        exact = self.store.search('专职安全管理员', synonyms=False)
        expanded = self.store.search('专职安全管理员', synonyms=True)
        self.assertEqual(exact['hits'], 0)
        self.assertGreater(expanded['hits'], 0)

    def test_fuzzy_and_status_filters_match_engine(self):
        indexer.update_metadata(self.store.db, str(self.a), status='void')
        self.assertEqual(self.store.search('安全', active=True)['hits'], 0)
        self.assertGreater(self.store.search('安全', active=False)['hits'], 0)
        actual = self.store.search('安权', fuzzy=True, active=False, synonyms=False)['groups']
        expected = searcher.aggregate_results(searcher.search_fuzzy(self.store.db, '安权', only_active=False))
        self.assertEqual(actual, expected)

    def test_preview_uses_existing_bookmarks_and_escapes_document_html(self):
        indexer.add_bookmark(self.store.db, str(self.a), 2, '测试收藏')
        payload = self.store.preview(str(self.a), 2, '上下文', '安全')
        self.assertTrue(payload['bookmarked'])
        payload['rows'] = [(2, '<script>安全 & 培训</script>')]
        rendered = preview_html(payload)
        self.assertNotIn('<script>', rendered)
        self.assertIn('&lt;script&gt;', rendered)
        self.assertIn('&amp;', rendered)
        self.assertIn('background-color:', rendered)

    def test_csv_exports_all_matches(self):
        groups = self.store.search('安全', synonyms=False)['groups']
        output = self.root / 'export.csv'
        export_csv(output, groups)
        text = output.read_text(encoding='utf-8-sig')
        self.assertIn('安全管理.txt', text)
        self.assertIn('安全培训', text)

    def test_copy_import_handles_three_value_backend_result(self):
        source = self.root / '新资料.txt'
        source.write_text('全新的文件内容，校验复制入库。', encoding='utf-8')
        result = self.store.index_files([str(source)], lambda _: None, threading.Event(), copy=True)
        self.assertEqual(result['ok'], 1)
        self.assertEqual(result['failed'], 0)
        self.assertTrue(source.exists())
        self.assertTrue(any((self.root / 'library').rglob('新资料.txt')))

    def test_backup_is_readable_and_does_not_change_active_database(self):
        import sqlite3
        target = self.root / 'backup.db'
        before = self.store.statistics()['files']
        self.store.backup(target, lambda _: None, threading.Event())
        with closing(sqlite3.connect(target)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM documents').fetchone()[0], before)
        self.assertEqual(self.store.statistics()['files'], before)

    def test_copy_default_and_full_legacy_preferences_migrate(self):
        (self.root / 'config.json').write_text(json.dumps({'link_only': False, 'qa_topk': 19,
            'ollama_chat': 'my-installed-model', 'export_dir': 'D:/archives'}), encoding='utf-8')
        (self.root / 'config.qt.json').write_text('{"copy_import": false}', encoding='utf-8')
        migrated = Store(self.root)
        self.assertTrue(migrated.settings['copy_import'])
        self.assertEqual(migrated.settings['qa_topk'], 19)
        self.assertEqual(migrated.settings['export_dir'], 'D:/archives')

    def test_parallel_tool_settings_are_merged(self):
        self.store.save()
        helper = Store(self.root)
        self.store.settings['dark'] = True
        self.store.save()
        helper.settings['qa_topk'] = 17
        helper.save()
        self.store.settings['preview_font'] = 15
        self.store.save()
        config = json.loads(self.store.config_path.read_text(encoding='utf-8'))
        self.assertTrue(config['dark'])
        self.assertEqual(config['qa_topk'], 17)
        self.assertEqual(config['preview_font'], 15)

    def test_automatic_backup_retains_configured_count(self):
        self.store.settings['max_backups'] = 2
        for number in range(4):
            indexer.update_metadata(self.store.db, str(self.a), doc_no=str(number))
            self.store.automatic_backup(lambda _: None, threading.Event())
        backups = list((self.root / 'backup').glob('index_*.db'))
        self.assertEqual(len(backups), 2)

    def test_software_package_includes_entire_runtime_directory(self):
        import zipfile
        from qt_frontend.maintenance import package_complete
        source = self.root / '软件打包资料.txt'
        source.write_text('完整软件包不能遗漏动态库。', encoding='utf-8')
        self.store.index_files([str(source)], lambda _: None, threading.Event(), copy=True)
        software = self.root / 'software-test'
        (software / '_internal').mkdir(parents=True)
        (software / '_internal' / 'runtime.dll').write_bytes(b'fixture')
        (software / '镜仔搜索新版.exe').write_bytes(b'fixture')
        target = self.root / 'software.zip'
        package_complete(self.store, target, lambda _: None, threading.Event(), software=software)
        with zipfile.ZipFile(target) as archive:
            self.assertIn('software/_internal/runtime.dll', archive.namelist())
            self.assertIn('software/镜仔搜索新版.exe', archive.namelist())
            self.assertIn('config.qt.json', archive.namelist())

    def test_chapter_preview_has_exact_full_bounds(self):
        payload = self.store.preview(str(self.a), 1, '章节', chapter_end=3)
        self.assertEqual([row[0] for row in payload['rows']], [1, 2, 3])

    def test_category_correction_moves_library_file_and_can_undo(self):
        location = self.root / 'library' / indexer.OTHER_CATEGORY
        location.mkdir(parents=True)
        source = location / '分类撤销验收.txt'
        source.write_text('测试分类修正和撤销。', encoding='utf-8')
        indexer.index_single_file(str(source), self.store.db)
        target_category = next(category for category in indexer.CATEGORY_FOLDERS if category != indexer.OTHER_CATEGORY)
        records = indexer.apply_categories_ex(self.store.db, {str(source): target_category})
        self.assertEqual(len(records), 1)
        self.assertFalse(source.exists())
        self.assertTrue(Path(records[0]['new_path']).is_file())
        restored, skipped = indexer.revert_categories(self.store.db, records)
        self.assertEqual((restored, skipped), (1, 0))
        self.assertTrue(source.is_file())

    def test_reflow_preserves_all_source_line_anchors(self):
        payload = self.store.preview(str(self.a), 2, '上下文', '安全员')
        payload.update(rows=[(10, '这是较长段落的前半部分'), (11, '以及续接的后半部分。')], line=11, reflow=True,
                       synonym_terms=['专职安全管理员'])
        rendered = preview_html(payload)
        self.assertIn('name="L10"', rendered)
        self.assertIn('name="L11"', rendered)
        self.assertIn('font-weight:600', rendered)

    def test_reference_links_and_synonyms_are_distinguished(self):
        payload = self.store.preview(str(self.a), 2, '上下文', '安全员')
        payload.update(rows=[(2, '安全员 专职安全管理员 《安全管理》')], synonym_terms=['专职安全管理员'],
            references={'安全管理': {'path': str(self.a), 'line': 2, 'status': 'void'}})
        rendered = preview_html(payload)
        self.assertIn('#fff0b3', rendered)
        self.assertIn('#ffd19a', rendered)
        self.assertIn('jingzai-preview:', rendered)
        self.assertIn('#c13a28', rendered)

    def test_complete_package_restores_settings_and_database(self):
        from qt_frontend.maintenance import package_complete, switch_staging
        source = self.root / '打包资料.txt'
        source.write_text('未登记的新资料需要复制入库。', encoding='utf-8')
        self.store.index_files([str(source)], lambda _: None, threading.Event(), copy=True)
        archive = self.root / 'complete.zip'
        self.store.settings['qa_topk'] = 21
        package_complete(self.store, archive, lambda _: None, threading.Event())
        destination = self.root / 'destination'
        destination.mkdir()
        old = Store(destination)
        old.save()
        # 文件路径不需要存在于原 library 中，也不能误搬包内的软件到数据根。
        staged = indexer.restore_library(str(archive), app_dir=str(destination))
        backup = switch_staging(destination, staged['staging'])
        self.assertTrue((Path(backup) / 'index.db').is_file())
        self.assertEqual(Store(destination).settings['qa_topk'], 21)
        self.assertTrue(any((destination / 'library').rglob('打包资料.txt')))

    def test_restore_rejects_outside_staging(self):
        from qt_frontend.maintenance import validate_staging
        with self.assertRaises(ValueError):
            validate_staging(self.root, self.root.parent)

    def test_restore_rolls_back_previous_data_on_failure(self):
        from qt_frontend.maintenance import switch_staging
        stage = self.root / '_restore_staging_test'
        stage.mkdir()
        (stage / 'library').mkdir()
        self.store.backup(stage / 'index.db', lambda _: None, threading.Event())
        original = Path.rename
        def rename(path, target):
            if path == stage / 'library':
                raise PermissionError('simulated locked file')
            return original(path, target)
        with patch.object(Path, 'rename', rename), self.assertRaises(PermissionError):
            switch_staging(self.root, stage)
        self.assertEqual(self.store.statistics()['files'], 2)
        self.assertTrue((stage / 'index.db').is_file())

    def test_cancelled_package_does_not_overwrite_previous_backup(self):
        from qt_frontend.maintenance import package_complete
        source = self.root / '取消打包资料.txt'
        source.write_text('此份资料用于取消备份的验证。', encoding='utf-8')
        self.store.index_files([str(source)], lambda _: None, threading.Event(), copy=True)
        target = self.root / 'keep.zip'
        target.write_bytes(b'previous-backup')
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Exception):
            package_complete(self.store, target, lambda _: None, cancel)
        self.assertEqual(target.read_bytes(), b'previous-backup')

    def test_pdf_and_excel_open_with_native_location_arguments(self):
        from qt_frontend import document_open
        source = self.root / 'locate.pdf'
        source.write_bytes(b'fixture')
        with patch.object(document_open, 'get_line_text', return_value='[Page 7] 验收'), \
             patch.object(document_open, 'find_sumatra', return_value='SumatraPDF.exe'), \
             patch.object(document_open.subprocess, 'Popen') as launch:
            document_open.open_located(self.store.db, str(source), 7, '验收')
            arguments = launch.call_args.args[0]
            self.assertIn('-page', arguments)
            self.assertIn('7', arguments)
            self.assertIn('-search', arguments)
        source = self.root / 'locate.xlsx'
        source.write_bytes(b'fixture')
        with patch.object(document_open, 'get_line_text', return_value='fixture'), \
             patch.object(document_open, 'parse_excel_loc', return_value=('Sheet2', 'B12')), \
             patch.object(document_open, '_office', return_value=True) as office:
            document_open.open_located(self.store.db, str(source), 12, '验收')
            self.assertEqual(office.call_args.kwargs['sheet'], 'Sheet2')
            self.assertEqual(office.call_args.kwargs['cell'], 'B12')


if __name__ == '__main__':
    unittest.main()

# -*- coding: utf-8 -*-
"""应用级调度与范围管理回归：真实文件系统，无 GUI。

用临时目录做真实通用通道扫描、来源复用、重叠去重、移除范围与持久化。
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from discovery.scheduler import DiscoveryController  # noqa: E402
from discovery.model import SearchRequest  # noqa: E402


class TestDiscoveryController(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='jingzai-sched-')
        self.root = Path(self.tmp)
        self.controller = DiscoveryController(str(self.root / 'catalog.db'),
                                             str(self.root / 'discovery.json'))

    def tearDown(self):
        self.controller.shutdown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_files(self, base, names):
        base = Path(base)
        base.mkdir(parents=True, exist_ok=True)
        for name in names:
            (base / name).write_text('样本', encoding='utf-8')

    def test_directory_source_identity_is_stable_and_reuses(self):
        d = self.root / '资料'
        self._make_files(d, ['a.txt'])
        first = self.controller.add_directory(str(d))
        second = self.controller.add_directory(str(d))
        self.assertEqual(first, second)
        self.assertEqual(len([s for s in self.controller.service.list_sources()
                              if s['kind'] == 'directory']), 1)

    def test_generic_scan_searches_and_reports_unknown_content(self):
        d = self.root / '资料'
        self._make_files(d, ['安全生产法.txt', 'JGJ130.pdf'])
        sid = self.controller.add_directory(str(d))
        report = self.controller.start_scan(sid)
        self.assertEqual(report['state'], 'complete')
        page = self.controller.search(SearchRequest(query='安全', limit=20))
        self.assertEqual(len(page.results), 1)
        self.assertEqual(page.results[0]['name'], '安全生产法.txt')
        # 目录层只做名称匹配；不宣称正文命中。
        self.assertNotIn('正文', page.results[0])

    def test_overlapping_roots_dedupe_by_path_preferring_specific_source(self):
        parent = self.root / '整盘'
        child = parent / '子目录'
        self._make_files(parent, ['顶层.txt'])
        self._make_files(child, ['共享.txt'])
        broad = self.controller.add_directory(str(parent))
        narrow = self.controller.add_directory(str(child))
        self.controller.start_scan(broad)
        self.controller.start_scan(narrow)
        page = self.controller.search(SearchRequest(query='共享', limit=20), dedupe_paths=True)
        paths = [hit['path'] for hit in page.results]
        self.assertEqual(len(paths), 1)
        # 去重后保留根更具体的来源。
        self.assertTrue(paths[0].startswith(str(child)))

    def test_hardlink_distinct_paths_are_not_merged_by_dedupe(self):
        base = self.root / '硬链接'
        self._make_files(base, ['原.txt'])
        alias = self.root / '别名.txt'
        try:
            os.link(base / '原.txt', alias)
        except OSError:
            self.skipTest('当前文件系统不支持硬链接')
        sid = self.controller.add_directory(str(self.root))
        self.controller.start_scan(sid)
        page = self.controller.search(SearchRequest(query='', limit=100), dedupe_paths=True)
        paths = {hit['path'] for hit in page.results}
        self.assertIn(str(base / '原.txt'), paths)
        self.assertIn(str(alias), paths)

    def test_remove_range_deletes_index_but_not_files(self):
        d = self.root / '要移除'
        self._make_files(d, ['保留原文件.txt'])
        sid = self.controller.add_directory(str(d))
        self.controller.start_scan(sid)
        self.assertGreater(self.controller.catalog.count_entries(sid), 0)
        self.controller.remove_range(sid)
        self.assertEqual(self.controller.catalog.count_entries(sid), 0)
        self.assertTrue((d / '保留原文件.txt').is_file())

    def test_selected_persists_in_json(self):
        d = self.root / '持久'
        self._make_files(d, ['x.txt'])
        sid = self.controller.add_directory(str(d))
        self.controller.save()
        raw = json.loads((self.root / 'discovery.json').read_text(encoding='utf-8'))
        self.assertIn(sid, raw['selected'])

    def test_auto_scan_config_round_trips(self):
        self.controller._config['auto_scan'] = True
        self.controller.save()
        reloaded = DiscoveryController(str(self.root / 'catalog.db'),
                                       str(self.root / 'discovery.json'))
        self.assertTrue(reloaded._config.get('auto_scan'))
        reloaded.shutdown()

    def test_cancel_keeps_old_index_and_marks_partial(self):
        d = self.root / '取消'
        self._make_files(d, ['a.txt', 'b.txt'])
        sid = self.controller.add_directory(str(d))
        self.controller.start_scan(sid)
        before = self.controller.catalog.count_entries(sid)
        stop = __import__('threading').Event()
        stop.set()
        report = self.controller.start_scan(sid, stop=stop)
        self.assertEqual(report['state'], 'partial')
        # 取消不清旧索引
        self.assertGreaterEqual(self.controller.catalog.count_entries(sid), before)


if __name__ == '__main__':
    unittest.main()

# -*- coding: utf-8 -*-
"""目录发现：流式扫描（中文/特殊字符、取消、循环、硬链接）。"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discovery.catalog import Catalog  # noqa: E402
from discovery.model import SearchRequest, SourceInfo  # noqa: E402
from discovery.scan import scan_source  # noqa: E402


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='discan-')
        self.db = os.path.join(self.tmp, 'catalog.db')
        self.cat = Catalog(self.db)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _source(self, root):
        return SourceInfo(source_id='dir:t', kind='directory', root=root,
                          provider='generic')

    def _scan(self, root):
        return scan_source(self.cat, self._source(root))

    def test_chinese_space_special(self):
        root = os.path.join(self.tmp, '资料 目录')
        os.makedirs(root, exist_ok=True)
        names = ['安全生产法.txt', '带 空格.pdf', '带_下划线.docx', '100%.xls']
        for n in names:
            with open(os.path.join(root, n), 'w', encoding='utf-8') as f:
                f.write('x')
        rep = self._scan(root)
        self.assertEqual(rep.state, 'complete')
        self.assertEqual(self.cat.count_entries('dir:t'), len(names) + 1)
        for q in ['安全生产', '空格', '下划线', '100%']:
            page = self.cat.search(SearchRequest(query=q, limit=20))
            self.assertGreaterEqual(len(page.results), 1, q)

    def test_scan_pagination_and_ext(self):
        root = os.path.join(self.tmp, 'd')
        os.makedirs(root, exist_ok=True)
        for i in range(30):
            ext = 'pdf' if i % 2 else 'txt'
            with open(os.path.join(root, f'f{i:02d}.{ext}'), 'w') as f:
                f.write('x')
        self._scan(root)
        page = self.cat.search(SearchRequest(query='', ext=['pdf'], limit=100))
        self.assertEqual(len(page.results), 15)

    def test_cancel(self):
        root = os.path.join(self.tmp, 'd')
        os.makedirs(root, exist_ok=True)
        for i in range(500):
            with open(os.path.join(root, f'f{i}.txt'), 'w') as f:
                f.write('x')
        stop = threading.Event()
        seen = []

        def progress(p):
            seen.append(p)
            if len(seen) >= 3:
                stop.set()

        rep = scan_source(self.cat, self._source(root),
                          cancel=stop.is_set, progress=progress)
        self.assertTrue(rep.stopped)

    def test_junction_loop_no_hang(self):
        root = os.path.join(self.tmp, 'd')
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, 'a.txt'), 'w') as f:
            f.write('x')
        # 目录联接点循环：root\loop -> root（无管理员也能建 junction）
        loop = os.path.join(root, 'loop')
        created = False
        if os.name == 'nt':
            try:
                subprocess.run(['cmd', '/c', 'mklink', '/J', loop, root],
                               check=True, capture_output=True, timeout=20)
                created = os.path.isdir(loop)
            except Exception:
                created = False
        if not created:
            self.skipTest('cannot create junction on this system')
        try:
            rep = self._scan(root)
            self.assertEqual(rep.state, 'complete')
            page = self.cat.search(SearchRequest(query='loop', limit=20))
            # 联接点作为 link 记录，不递归
            kinds = {r['kind'] for r in page.results}
            self.assertIn('link', kinds)
            self.assertNotIn('dir', {r['kind'] for r in page.results
                                     if r['name'] == 'loop'})
        finally:
            if created:
                subprocess.run(['cmd', '/c', 'rmdir', loop], capture_output=True)

    def test_hardlink_two_paths(self):
        root = os.path.join(self.tmp, 'd')
        os.makedirs(root, exist_ok=True)
        a = os.path.join(root, 'a.txt')
        b = os.path.join(root, 'b.txt')
        with open(a, 'w') as f:
            f.write('x')
        try:
            os.link(a, b)
        except OSError:
            self.skipTest('hardlink unsupported here')
        self._scan(root)
        page = self.cat.search(SearchRequest(query='.txt', limit=20))
        self.assertEqual(len(page.results), 2)
        oids = {r['object_id'] for r in page.results}
        self.assertEqual(len(oids), 1)


if __name__ == '__main__':
    unittest.main()

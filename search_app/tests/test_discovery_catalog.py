# -*- coding: utf-8 -*-
"""目录发现：catalog.db 写入 / 字面查询 / 事件重放 / checkpoint。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discovery.catalog import Catalog  # noqa: E402
from discovery.model import ChangeEvent, SearchRequest  # noqa: E402


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='discat-')
        self.db = os.path.join(self.tmp, 'catalog.db')
        self.cat = Catalog(self.db)
        self.sid = 'dir:test'

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _put(self, path, name=None, kind='file', size=10, mtime_ns=1,
             ext='txt', dev=1, ino=None, generation=1):
        ino = ino if ino is not None else hash(path) & 0xFFFFFFFFFFFF
        import os as _os
        self.cat.upsert_entries([{
            'source_id': self.sid, 'parent_path': _os.path.dirname(path),
            'name': name or _os.path.basename(path), 'path': path,
            'match_name': (name or _os.path.basename(path)).casefold(),
            'match_path': _os.path.normcase(path), 'ext': ext, 'kind': kind,
            'size': size, 'mtime_ns': mtime_ns, 'volume_serial': dev,
            'file_id': ino, 'is_dir': 1 if kind == 'dir' else 0,
            'scan_generation': generation,
        }])

    def test_search_literal_percent(self):
        self._put(r'C:\资料\100%.txt', name='100%.txt')
        page = self.cat.search(SearchRequest(query='100%'))
        self.assertEqual(len(page.results), 1)

    def test_search_underscore(self):
        self._put(r'C:\资料\a_b.txt', name='a_b.txt')
        page = self.cat.search(SearchRequest(query='a_b'))
        self.assertEqual(len(page.results), 1)
        self.assertEqual(len(self.cat.search(
            SearchRequest(query='axb')).results), 0)

    def test_search_scope_path_vs_name(self):
        self._put(r'C:\资料\项目A\报告.txt', name='报告.txt')
        self.assertEqual(len(self.cat.search(
            SearchRequest(query='项目A', scope='name')).results), 0)
        self.assertEqual(len(self.cat.search(
            SearchRequest(query='项目A', scope='path')).results), 1)

    def test_ext_filter(self):
        self._put(r'C:\资料\a.txt', name='a.txt', ext='txt')
        self._put(r'C:\资料\b.pdf', name='b.pdf', ext='pdf')
        page = self.cat.search(SearchRequest(query='', ext=['pdf'], limit=20))
        self.assertEqual([r['ext'] for r in page.results], ['pdf'])

    def test_pagination_no_dup(self):
        for i in range(5):
            self._put(rf'C:\资料\文件{i}.txt', name=f'文件{i}.txt', ino=100 + i)
        seen, offset = set(), 0
        while True:
            page = self.cat.search(SearchRequest(query='文件', limit=2,
                                                 offset=offset, sort='path'))
            for r in page.results:
                self.assertNotIn(r['entry_id'], seen)
                seen.add(r['entry_id'])
            if not page.has_more:
                break
            offset += 2
        self.assertEqual(len(seen), 5)

    def test_root_boundary(self):
        self._put(r'D:\资料\a.txt', name='a.txt')
        self._put(r'D:\资料备份\b.txt', name='b.txt')
        page = self.cat.search(SearchRequest(query='', root=r'D:\资料', limit=20))
        paths = [r['path'] for r in page.results]
        self.assertIn(r'D:\资料\a.txt', paths)
        self.assertNotIn(r'D:\资料备份\b.txt', paths)

    def test_rename_file_keeps_content_version(self):
        self._put(r'D:\资料\a.txt', name='a.txt', ino=7)
        self.cat.rename_entry(self.sid, r'D:\资料\a.txt', r'D:\资料\b.txt')
        with self.cat._conn(read_only=True) as conn:
            row = conn.execute(
                'SELECT name, content_version FROM entries '
                'WHERE source_id=?', (self.sid,)).fetchone()
        self.assertEqual(row['name'], 'b.txt')
        self.assertEqual(row['content_version'], 0)  # 仅更名不升内容版本

    def test_modified_bumps_content_version(self):
        path = os.path.join(self.tmp, 'real.txt')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('hello')
        self.cat.upsert_path(self.sid, path)
        with open(path, 'w', encoding='utf-8') as f:
            f.write('world')
        self.cat.touch_modified(self.sid, path)
        with self.cat._conn(read_only=True) as conn:
            row = conn.execute(
                'SELECT content_version FROM entries WHERE source_id=?',
                (self.sid,)).fetchone()
        self.assertEqual(row['content_version'], 1)

    def test_directory_rename_moves_subtree(self):
        self._put(r'D:\资料\旧目录', name='旧目录', kind='dir', ino=1)
        self._put(r'D:\资料\旧目录\子\a.txt', name='a.txt', ino=2)
        self.cat.rename_entry(self.sid, r'D:\资料\旧目录', r'D:\资料\新目录',
                              is_dir=True)
        with self.cat._conn(read_only=True) as conn:
            rows = {r['path'] for r in conn.execute(
                'SELECT path FROM entries WHERE source_id=?', (self.sid,))}
        self.assertIn(r'D:\资料\新目录', rows)
        self.assertIn(r'D:\资料\新目录\子\a.txt', rows)

    def test_hardlinks_preserved(self):
        # 同一对象（file_id 相同）两条路径都要保留
        self._put(r'D:\a\link1.txt', name='link1.txt', ino=999)
        self._put(r'D:\b\link2.txt', name='link2.txt', ino=999)
        page = self.cat.search(SearchRequest(query='link', limit=20))
        self.assertEqual(len(page.results), 2)
        oids = {r['object_id'] for r in page.results}
        self.assertEqual(len(oids), 1)

    def test_checkpoint_roundtrip(self):
        self.cat.save_checkpoint(self.sid, 'usn', '12345', '42', 3)
        cp = self.cat.get_checkpoint(self.sid)
        self.assertEqual(cp['cursor'], '12345')
        self.assertEqual(cp['journal_id'], '42')

    def test_prune_stale_only_present(self):
        self._put(r'D:\资料\old.txt', name='old.txt', ino=1, generation=1)
        self._put(r'D:\资料\new.txt', name='new.txt', ino=2, generation=2)
        self.cat.prune_stale(self.sid, 2)
        with self.cat._conn(read_only=True) as conn:
            rows = {r['name'] for r in conn.execute(
                'SELECT name FROM entries WHERE source_id=?', (self.sid,))}
        self.assertEqual(rows, {'new.txt'})

    def test_event_replay_idempotent(self):
        path = os.path.join(self.tmp, 'ev.txt')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('x')
        ev = ChangeEvent(source_id=self.sid, change_type='created',
                         new_parent_path=self.tmp, new_name='ev.txt')
        self.cat.record_event(ev, apply_now=False)
        self.cat.record_event(ev, apply_now=False)  # 重复事件
        n = self.cat.apply_pending(self.sid)
        self.assertEqual(n, 2)
        page = self.cat.search(SearchRequest(query='ev', limit=10))
        self.assertEqual(len(page.results), 1)  # 重放不产生重复条目


if __name__ == '__main__':
    unittest.main()

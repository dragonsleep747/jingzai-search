# -*- coding: utf-8 -*-
"""镜仔搜索 · 索引器测试（stdlib unittest）

覆盖：目录建库（基本入库、索引库旁车文件不误入库）。
"""
import os
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from indexer import index_directory  # noqa: E402


class TestIndexDirectory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='jz_idx_')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_basic_index(self):
        root = tempfile.mkdtemp(dir=self.tmp)
        db = os.path.join(root, 'idx.db')
        with open(os.path.join(root, 'a.txt'), 'w', encoding='utf-8') as f:
            f.write('第一条 安全生产\n第二条 消防管理')
        with open(os.path.join(root, 'b.txt'), 'w', encoding='utf-8') as f:
            f.write('第三条 给排水')
        r = index_directory(root, db)
        self.assertEqual(r['total'], 2)
        self.assertEqual(r['processed'], 2)

    def test_db_sidecars_not_indexed(self):
        """WAL 模式下 -wal/-shm 旁车文件不得被当作文档入库"""
        root = tempfile.mkdtemp(dir=self.tmp)
        db = os.path.join(root, 'idx.db')
        for name in ('a.txt', 'b.txt'):
            with open(os.path.join(root, name), 'w', encoding='utf-8') as f:
                f.write('安全 生产 规范\n消防 验收 标准')
        index_directory(root, db)
        import sqlite3
        conn = sqlite3.connect(db)
        rows = conn.execute('SELECT path FROM documents').fetchall()
        conn.close()
        paths = [p for (p,) in rows]
        self.assertEqual(len(paths), 2)
        for p in paths:
            self.assertFalse(p.endswith('-wal') or p.endswith('-shm')
                             or p.endswith('-journal'), p)


if __name__ == '__main__':
    unittest.main(verbosity=2)

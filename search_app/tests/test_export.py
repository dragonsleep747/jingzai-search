# -*- coding: utf-8 -*-
"""export.py 行为锁测试（stdlib unittest）"""
import os
import sys
import unittest
import tempfile
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from export import write_digest_docx, build_search_hits


def _make_db():
    """构造含 line_count 的最小库，兼容 get_metadata"""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript('''
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY, path TEXT, name TEXT, status TEXT,
            doc_no TEXT, category TEXT, effective_date TEXT, ext TEXT,
            line_count INTEGER);
        CREATE TABLE lines (
            doc_id INTEGER, line_no INTEGER, line_text TEXT);
        CREATE TABLE chapters (
            doc_id INTEGER, title TEXT, start_line INTEGER,
            end_line INTEGER, level INTEGER);
    ''')
    conn.execute('INSERT INTO documents VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)',
                 ('a.txt', 'a', 'active', '建标〔2024〕3号', '安全',
                  '2024-01-01', '.txt', 7))
    lines = [
        '第一章 总则', '第一条 本制度适用于全公司。', '第二条 高空作业需系安全带。',
        '', '第二章 差旅', '第十条 报销需附发票。', '差旅费标准：',
    ] + [f'第{i}条 通用条款。' for i in range(11, 30)]
    for i, t in enumerate(lines, 1):
        conn.execute('INSERT INTO lines VALUES (1, ?, ?)', (i, t))
    conn.execute('INSERT INTO chapters VALUES '
                 '(1, "第一章 总则", 1, 3, 1), '
                 '(1, "第二条 高空作业需系安全带。", 3, 3, 2), '
                 '(1, "第二章 差旅", 5, 7, 1)')
    conn.commit()
    conn.close()
    return path


class TestWriteDigestDocx(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = _make_db()

    @classmethod
    def tearDownClass(cls):
        try:
            os.remove(cls.db)
        except OSError:
            pass

    def _write(self, **kw):
        fd, fn = tempfile.mkstemp(suffix='.docx')
        os.close(fd)
        sections = kw.get('sections', [('a.txt', [('第二条 高空作业', 3)])])
        ok, err = write_digest_docx(
            self.db, fn, kw.get('title', '常用制度摘编'),
            kw.get('subtitle', '测试'), sections,
            kw.get('highlight_terms', ()), kw.get('context_n', 0))
        return ok, err, fn

    def test_basic(self):
        ok, err, fn = self._write()
        self.assertTrue(ok, err)
        self.assertTrue(os.path.exists(fn) and os.path.getsize(fn) > 0)
        from docx import Document
        doc = Document(fn)
        text = '\n'.join(p.text for p in doc.paragraphs)
        self.assertIn('常用制度摘编', text)
        self.assertIn('高空作业需系安全带', text)
        self.assertIn('建标〔2024〕3号', text)
        os.remove(fn)

    def test_context_mode(self):
        ok, err, fn = self._write(
            sections=[('a.txt', [('命中', 3), ('命中', 20)])], context_n=1)
        self.assertTrue(ok, err)
        from docx import Document
        doc = Document(fn)
        text = '\n'.join(p.text for p in doc.paragraphs)
        self.assertIn('（略）', text)   # 两个不相邻区间之间有省略分隔
        os.remove(fn)

    def test_highlight(self):
        ok, err, fn = self._write(highlight_terms=('安全带',))
        self.assertTrue(ok, err)
        os.remove(fn)

    def test_missing_path_defaults(self):
        # 库中无此路径：get_metadata 返回默认值，构建不应抛错
        ok, err, fn = self._write(sections=[('ghost.txt', [('x', 1)])])
        self.assertTrue(ok, err)
        from docx import Document
        doc = Document(fn)
        text = '\n'.join(p.text for p in doc.paragraphs)
        self.assertIn('现行有效', text)   # status 默认 active → 现行有效
        os.remove(fn)


class TestBuildSearchHits(unittest.TestCase):
    def test_structure(self):
        groups = [{
            'name': 'a.txt', 'path': 'a.txt', 'status': 'active',
            'matches': [
                {'line_no': 3, 'line_text': '第二条 高空作业需系安全带。'},
                {'line_no': 6, 'line_text': '第十条 报销需附发票。'},
            ],
        }]
        ctx = {('a.txt', 3): {'prev': '第一条 本制度适用于全公司。',
                              'next': ''}}
        hits = build_search_hits(groups, ctx)
        self.assertEqual(len(hits), 1)
        h = hits[0]
        self.assertEqual(h['name'], 'a.txt')
        self.assertIn('第二条 高空作业', h['text'])
        self.assertIn('第一条 本制度', h['text'])   # prev 上下文带入
        self.assertGreater(h['score'], 0.5)

    def test_empty_matches_skipped(self):
        hits = build_search_hits(
            [{'name': 'x', 'path': 'x', 'status': 'active', 'matches': []}],
            {})
        self.assertEqual(hits, [])

    def test_max_chars_truncates(self):
        groups = [{
            'name': 'a', 'path': 'a', 'status': 'active',
            'matches': [{'line_no': i, 'line_text': '长' * 100} for i in range(9)],
        }]
        hits = build_search_hits(groups, {}, max_files=5, max_chars=400)
        # 每文件最多 4 行、按字符预算截断：必不会吃掉全部 9 行
        self.assertLessEqual(len(hits), 5)
        total = sum(len(h['text']) for h in hits)
        self.assertLess(total, 4 * 100 * 5)


if __name__ == '__main__':
    unittest.main()

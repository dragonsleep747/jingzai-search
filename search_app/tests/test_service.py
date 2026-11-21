# -*- coding: utf-8 -*-
"""service.py 行为锁测试（stdlib unittest）"""
import os
import sys
import unittest
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from service import (
    fmt_size, fmt_eta, fmt_mtime, valid_date, eff_sort_key,
    find_fuzzy_pos, is_table_row, parse_dropped_paths, line_path_from_tags,
    prepare_filter_cache, plan_row_cost, parse_excel_loc, snapshot_db,
    page_label, snippet, bar_pts, find_acrobat, find_sumatra,
)


class TestFmtSize(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(fmt_size(0), '0 B')
        self.assertEqual(fmt_size(1023), '1023 B')

    def test_kb_mb_gb(self):
        self.assertEqual(fmt_size(1024), '1.0 KB')
        self.assertEqual(fmt_size(1024 * 1024), '1.0 MB')
        self.assertEqual(fmt_size(int(1.5 * 1024 ** 3)), '1.5 GB')


class TestFmtEta(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(fmt_eta(5), '~5s')
        self.assertEqual(fmt_eta(123), '~02:03')

    def test_hours(self):
        self.assertEqual(fmt_eta(3660), '~1h01m')

    def test_invalid(self):
        self.assertEqual(fmt_eta(-1), '—')
        self.assertEqual(fmt_eta('abc'), '—')


class TestValidDate(unittest.TestCase):
    def test_valid(self):
        self.assertTrue(valid_date('2024-02-29'))  # 闰年
        self.assertTrue(valid_date('2023-01-01'))

    def test_invalid(self):
        self.assertFalse(valid_date('2021-02-30'))
        self.assertFalse(valid_date('2023-13-01'))
        self.assertFalse(valid_date('2023/01/01'))
        self.assertFalse(valid_date(''))


class TestEffSortKey(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(eff_sort_key('2024-01-01'), '2024-01-01')

    def test_range_uses_start(self):
        self.assertEqual(eff_sort_key('2024-01-01~2024-12-31'), '2024-01-01')

    def test_empty_first(self):
        self.assertEqual(eff_sort_key(''), '0000-00-00')
        self.assertEqual(eff_sort_key(None), '0000-00-00')


class TestFindFuzzyPos(unittest.TestCase):
    def test_substitution(self):
        # 错字：产→x，返回命中片段起始位置（非错字下标）
        self.assertEqual(find_fuzzy_pos('第一条：安全生产法', '安全生x法'), 4)

    def test_extra_char_in_kw(self):
        # 多打一个字：少打分支定位实心片段
        self.assertEqual(find_fuzzy_pos('第一条：安全生产法', '安全生产法x'), 4)

    def test_not_found(self):
        # 完全对不上且剩余实心 <3 字时返回 -1
        self.assertEqual(find_fuzzy_pos('安全生产法', 'xyz'), -1)


class TestIsTableRow(unittest.TestCase):
    def test_table(self):
        self.assertTrue(is_table_row('姓名 | 张三 | 男 | 30'))

    def test_not_table(self):
        self.assertFalse(is_table_row('普通一行文本'))
        self.assertFalse(is_table_row(''))
        self.assertFalse(is_table_row('A | B'))  # 只有 1 个分隔符


class TestParseDroppedPaths(unittest.TestCase):
    def test_plain(self):
        with tempfile.TemporaryDirectory() as d:
            a = os.path.join(d, 'a.txt')
            open(a, 'w').close()
            self.assertEqual(parse_dropped_paths(a), [a])

    def test_braced(self):
        with tempfile.TemporaryDirectory() as d:
            a = os.path.join(d, 'a b.txt')
            open(a, 'w').close()
            self.assertEqual(parse_dropped_paths('{%s}' % a), [a])

    def test_file_url(self):
        with tempfile.TemporaryDirectory() as d:
            a = os.path.join(d, 'a.txt')
            open(a, 'w').close()
            got = parse_dropped_paths('file:///' + a.replace('\\', '/'))
            self.assertEqual(len(got), 1)
            self.assertEqual(os.path.normcase(got[0]), os.path.normcase(a))

    def test_empty(self):
        self.assertEqual(parse_dropped_paths(''), [])


class TestLinePathFromTags(unittest.TestCase):
    def test_line_tag(self):
        self.assertEqual(
            line_path_from_tags(('line', 'D:/x/a.txt', 'kw')), 'D:/x/a.txt')

    def test_no_line(self):
        self.assertIsNone(line_path_from_tags(('kw',)))


class TestPrepareFilterCache(unittest.TestCase):
    def test_caches(self):
        groups = [{'name': '文档A', 'clusters': [
            {'lines': [{'line_text': 'Hello', 'cross_text': 'World'}]}]}]
        prepare_filter_cache(groups)
        self.assertEqual(groups[0]['_low_name'], '文档a')
        self.assertEqual(groups[0]['clusters'][0]['_low'],
                         [('hello', 'world')])

    def test_idempotent(self):
        groups = [{'name': 'X', 'clusters': []}]
        prepare_filter_cache(groups)
        n1 = len(groups[0])
        prepare_filter_cache(groups)
        self.assertEqual(len(groups[0]), n1)


class TestPlanRowCost(unittest.TestCase):
    def test_full(self):
        self.assertEqual(plan_row_cost({'visible': list(range(10))}), 5)

    def test_short(self):
        self.assertEqual(plan_row_cost({'visible': [1, 2]}), 3)


class TestParseExcelLoc(unittest.TestCase):
    def test_cell(self):
        self.assertEqual(parse_excel_loc('[Sheet1!B3] 内容'), ('Sheet1', 'B3'))

    def test_none(self):
        self.assertIsNone(parse_excel_loc('普通文本'))
        self.assertIsNone(parse_excel_loc(''))


class TestSnapshotDb(unittest.TestCase):
    def test_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, 'index.db')
            import sqlite3
            conn = sqlite3.connect(db)
            conn.execute('CREATE TABLE t(a)')
            conn.execute('INSERT INTO t VALUES (1)')
            conn.commit()
            conn.close()
            dest = snapshot_db(db, d, 't1')
            self.assertTrue(dest and os.path.exists(dest))
            conn = sqlite3.connect(dest)
            self.assertEqual(conn.execute('SELECT a FROM t').fetchone(), (1,))
            conn.close()

    def test_missing(self):
        self.assertIsNone(snapshot_db('/no/such/db.db', '/tmp', 't'))


class TestPageLabel(unittest.TestCase):
    def test_pdf_page(self):
        self.assertEqual(page_label('[Page 12] 内容'), 'P12')

    def test_slide(self):
        self.assertEqual(page_label('[Slide 3] 内容'), 'P3')

    def test_none(self):
        self.assertEqual(page_label('普通文本'), '')


class TestSnippet(unittest.TestCase):
    def test_hit_middle(self):
        out = snippet('这是一条安全生产法相关制度内容文本', '安全生产')
        self.assertIn('【安全生产】', out)

    def test_hit_at_start_no_ellipsis(self):
        out = snippet('安全生产法全文', '安全生产')
        self.assertFalse(out.startswith('…'))
        self.assertIn('【安全生产】', out)

    def test_hit_near_end_ellipsis(self):
        long_text = '前' * 300 + '安全生产' + '后' * 10
        out = snippet(long_text, '安全生产')
        self.assertIn('【安全生产】', out)
        self.assertIn('…', out)

    def test_no_hit(self):
        out = snippet('abcdefghij' * 30, 'zzz')
        self.assertEqual(out, ('abcdefghij' * 30)[:200])

    def test_fuzzy(self):
        out = snippet('第一条：安全生产法', '安全生x法', fuzzy=True)
        self.assertIn('【', out)   # 命中词位置被框出

    def test_empty(self):
        self.assertEqual(snippet('', 'kw'), '')


class TestBarPts(unittest.TestCase):
    def test_thin_bar(self):
        # r 收缩到 <0.5 → 直角矩形 8 个点
        pts = bar_pts(100, 1, 0, 10, 0.1)
        self.assertEqual(pts, [99, 0, 101, 0, 101, 10, 99, 10])

    def test_rounded(self):
        pts = bar_pts(100, 5, 0, 10, 2)
        self.assertEqual(len(pts), 24)
        self.assertEqual(pts[0], 100 - 5 + 2)   # x0 + r


class TestPdfViewers(unittest.TestCase):
    def test_find_acrobat_returns_str_or_none(self):
        self.assertIsInstance(find_acrobat(), (str, type(None)))

    def test_find_sumatra_returns_str_or_none(self):
        self.assertIsInstance(
            find_sumatra('D:/nonexist', 'D:/nonexist'), (str, type(None)))


if __name__ == '__main__':
    unittest.main()

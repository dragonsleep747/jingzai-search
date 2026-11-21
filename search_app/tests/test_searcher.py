# -*- coding: utf-8 -*-
"""镜仔搜索 · 检索引擎纯函数与轻量集成测试（stdlib unittest）

覆盖：全半角/写法归一化、布尔查询语法、高级元数据语法、模糊通配、
聚类与聚合排序、上下文/段落/章节定位、引用提取。
"""
import os
import sys
import sqlite3
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from searcher import (  # noqa: E402
    _to_halfwidth, _needs_norm, _norm_variants, _build_fts_query,
    _has_short_token, parse_advanced_query, _date_bound, _escape_like,
    _fuzzy_patterns, _parse_boolean, _build_clusters, aggregate_results,
    extract_page, cn_to_int, find_refs_in_text,
    get_context, get_line_text, get_paragraph,
    get_chapter_for_line, get_chapter_chain_str, list_chapters,
)


class TestNormalization(unittest.TestCase):
    def test_to_halfwidth(self):
        self.assertEqual(_to_halfwidth('ＧＢ５００１６'), 'GB50016')
        self.assertEqual(_to_halfwidth('ＡＢＣ　１２３'), 'ABC 123')

    def test_needs_norm(self):
        self.assertFalse(_needs_norm('安全生产责任制'))
        self.assertTrue(_needs_norm('GB50016'))
        self.assertTrue(_needs_norm('建标[2003]206号'))

    def test_norm_variants_pure_chinese(self):
        self.assertEqual(_norm_variants('安全生产责任制'), ['安全生产责任制'])

    def test_norm_variants_std_code_space(self):
        v = _norm_variants('GB50016')
        self.assertEqual(v[0], 'GB50016')
        self.assertIn('GB 50016', v)

    def test_norm_variants_fullwidth(self):
        v = _norm_variants('ＧＢ５００１６')
        self.assertIn('GB50016', v)
        self.assertIn('GB 50016', v)

    def test_norm_variants_bracket_swap(self):
        v = _norm_variants('建标[2003]206号')
        self.assertIn('建标【2003】206号', v)

    def test_norm_variants_cap(self):
        v = _norm_variants('GB50016-2014—2019')
        self.assertLessEqual(len(v), 12)
        self.assertEqual(len(set(v)), len(v))  # 去重保序


class TestFtsQuery(unittest.TestCase):
    def test_single_short(self):
        self.assertEqual(_build_fts_query('a'), '"a"')

    def test_and_terms(self):
        self.assertEqual(_build_fts_query('安全生产法 检查'),
                         '"安全生产法" "检查"')

    def test_or_syntax(self):
        self.assertEqual(_build_fts_query('a | b'), '"a" OR "b"')
        self.assertEqual(_build_fts_query('a OR b'), '"a" OR "b"')

    def test_not_syntax(self):
        self.assertEqual(_build_fts_query('安全员 -施工'), '"安全员" NOT "施工"')

    def test_plus_syntax(self):
        self.assertEqual(_build_fts_query('+安全生产法 检查'),
                         '"安全生产法" "检查"')

    def test_synonym_group(self):
        syn = {'安全员': ['专职安全管理员']}
        self.assertEqual(_build_fts_query('安全员', syn),
                         '("安全员" OR "专职安全管理员")')

    def test_std_code_group(self):
        q = _build_fts_query('GB50016')
        self.assertIn('"GB50016"', q)
        self.assertIn('"GB 50016"', q)
        self.assertTrue(q.startswith('(') and q.endswith(')'))

    def test_has_short_token(self):
        self.assertFalse(_has_short_token('安全生产法'))
        self.assertTrue(_has_short_token('a b'))
        self.assertTrue(_has_short_token('安全员 -施工'))


class TestAdvancedQuery(unittest.TestCase):
    def test_all_fields(self):
        free, f = parse_advanced_query(
            '安全生产 文号:GB50016 状态:废止|替代 日期:2022..2024 '
            '名称:差旅 分类:公司制度|人事')
        self.assertEqual(free, '安全生产')
        self.assertEqual(f['doc_no'], ['GB50016'])
        self.assertEqual(f['status'], ['void', 'replaced'])
        self.assertEqual(f['date'], '2022..2024')
        self.assertEqual(f['name'], ['差旅'])
        self.assertEqual(f['category'], ['公司制度', '人事'])

    def test_status_alias(self):
        _, f = parse_advanced_query('状态:现行有效')
        self.assertEqual(f['status'], ['active'])

    def test_category_comma_and_slash(self):
        _, f = parse_advanced_query('分类:公司制度，人事/财务')
        self.assertEqual(f['category'], ['公司制度', '人事', '财务'])

    def test_unknown_field_is_free_word(self):
        free, f = parse_advanced_query('foo:bar 安全')
        self.assertEqual(free, 'foo:bar 安全')
        self.assertEqual(f['doc_no'], [])

    def test_date_bound(self):
        self.assertEqual(_date_bound('2022'), ('2022-01-01', '2022-12-31'))
        self.assertEqual(_date_bound('2022-3-1'), ('2022-03-01', None))
        self.assertIsNone(_date_bound('abc'))
        self.assertIsNone(_date_bound(''))


class TestFuzzyPatterns(unittest.TestCase):
    def test_escape_like(self):
        self.assertEqual(_escape_like('50%_x'), '50\\%\\_x')

    def test_fuzzy_patterns(self):
        pats = _fuzzy_patterns('abc')
        self.assertIn('%abc%', pats)
        self.assertIn('%a_c%', pats)   # 中间错字
        self.assertIn('%_bc%', pats)   # 首字错
        self.assertIn('%ab_%', pats)   # 尾字错
        self.assertIn('%ac%', pats)    # 少打
        self.assertEqual(len(pats), len(set(pats)))


class TestBoolean(unittest.TestCase):
    def test_and(self):
        self.assertEqual(_parse_boolean('a b'),
                         {'and': ['a', 'b'], 'or': [], 'not': []})

    def test_or_two(self):
        self.assertEqual(_parse_boolean('a | b'),
                         {'and': [], 'or': [['a'], ['b']], 'not': []})

    def test_not_global(self):
        self.assertEqual(_parse_boolean('a -b'),
                         {'and': ['a'], 'or': [], 'not': ['b']})

    def test_mixed_or_and(self):
        self.assertEqual(_parse_boolean('a OR b c'),
                         {'and': ['b', 'c'], 'or': [['a']], 'not': []})

    def test_or_chain(self):
        self.assertEqual(_parse_boolean('a | b | c'),
                         {'and': [], 'or': [['a'], ['b'], ['c']], 'not': []})


class TestClusterAndAggregate(unittest.TestCase):
    def test_clusters_gap2(self):
        ms = [{'line_no': 1}, {'line_no': 2}, {'line_no': 5},
              {'line_no': 6}, {'line_no': 9}]
        cl = _build_clusters(ms, gap=2)
        self.assertEqual([c['line_start'] for c in cl], [1, 5, 9])
        self.assertEqual([c['line_end'] for c in cl], [2, 6, 9])

    def test_clusters_empty(self):
        self.assertEqual(_build_clusters([]), [])

    def test_aggregate_rank_and_clusters(self):
        rows = [
            {'path': 'a.txt', 'name': 'a', 'status': 'active',
             'line_no': 1, 'line_text': 'x', 'score': 1.0},
            {'path': 'a.txt', 'name': 'a', 'status': 'active',
             'line_no': 2, 'line_text': 'x', 'score': 1.0},
            {'path': 'b.txt', 'name': 'b', 'status': 'active',
             'line_no': 10, 'line_text': 'x', 'score': 0.8},
        ]
        groups = aggregate_results(rows)
        self.assertEqual(groups[0]['path'], 'a.txt')  # 精确命中权重高
        self.assertEqual(groups[0]['hits'], 2)
        self.assertEqual(groups[0]['fuzzy_hits'], 0)
        self.assertEqual(len(groups[0]['clusters']), 1)
        self.assertIn('density', groups[0])
        self.assertEqual(groups[1]['fuzzy_hits'], 1)


class TestPageAndRefs(unittest.TestCase):
    def test_extract_page(self):
        self.assertEqual(extract_page('[Page 12] 内容'), '12')
        self.assertEqual(extract_page('[Slide 3] 标题'), '3')
        self.assertIsNone(extract_page('普通行'))

    def test_cn_to_int(self):
        self.assertEqual(cn_to_int('十五'), 15)
        self.assertEqual(cn_to_int('一百二十'), 120)
        self.assertEqual(cn_to_int('０９'), 9)
        self.assertEqual(cn_to_int('10'), 10)
        self.assertIsNone(cn_to_int('abc'))

    def test_find_refs_single(self):
        self.assertEqual(find_refs_in_text('见《安全生产法》第三条'),
                         [('安全生产法', '第三条')])

    def test_find_refs_enum(self):
        self.assertEqual(find_refs_in_text('《建规》第二条、第三条'),
                         [('建规', '第二条'), ('建规', '第三条')])

    def test_find_refs_no_clause(self):
        self.assertEqual(find_refs_in_text('依据《安全生产法》执行'),
                         [('安全生产法', '')])


def _make_db():
    """构造最小 documents/lines/chapters 库，返回 (db_path, cleanup)"""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript('''
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY, path TEXT, name TEXT, status TEXT,
            doc_no TEXT, category TEXT, effective_date TEXT, ext TEXT);
        CREATE TABLE lines (
            doc_id INTEGER, line_no INTEGER, line_text TEXT);
        CREATE TABLE chapters (
            doc_id INTEGER, title TEXT, start_line INTEGER,
            end_line INTEGER, level INTEGER);
    ''')
    conn.execute('INSERT INTO documents VALUES (1, ?, ?, ?, ?, ?, ?, ?)',
                 ('a.txt', 'a', 'active', '', '', '', '.txt'))
    lines = [
        '第一章 总则', '第一条 目的', '为规范管理。',
        '', '第二章 差旅', '第一条 审批', '部门经理审批。',
        '差旅费标准如下：', '', '第十条 报销', '凭发票报销。',
    ]
    for i, t in enumerate(lines, 1):
        conn.execute('INSERT INTO lines VALUES (1, ?, ?)', (i, t))
    conn.execute('INSERT INTO chapters VALUES '
                 '(1, "第一章 总则", 1, 3, 1), '
                 '(1, "第一条 目的", 2, 3, 2), '
                 '(1, "第二章 差旅", 5, 10, 1)')
    conn.commit()
    conn.close()
    return path


class TestContextIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = _make_db()

    @classmethod
    def tearDownClass(cls):
        try:
            os.remove(cls.db)
        except OSError:
            pass

    def test_get_context_window(self):
        rows = get_context(self.db, 'a.txt', 7, before=1, after=2)
        nos = [r[0] for r in rows]
        self.assertEqual(nos, [6, 7, 8, 9])

    def test_get_line_text(self):
        self.assertEqual(get_line_text(self.db, 'a.txt', 7), '部门经理审批。')
        self.assertEqual(get_line_text(self.db, 'a.txt', 99), '')

    def test_get_paragraph_stops_at_blank(self):
        para = get_paragraph(self.db, 'a.txt', 6)
        self.assertEqual([n for n, _ in para], [5, 6, 7, 8])

    def test_get_chapter_chain(self):
        self.assertEqual(get_chapter_chain_str(self.db, 'a.txt', 7),
                         '第二章 差旅')
        self.assertEqual(get_chapter_chain_str(self.db, 'a.txt', 2),
                         '第一章 总则 > 第一条 目的')

    def test_list_chapters(self):
        titles = [c['title'] for c in list_chapters(self.db, 'a.txt')]
        self.assertEqual(titles, ['第一章 总则', '第一条 目的', '第二章 差旅'])


if __name__ == '__main__':
    unittest.main(verbosity=2)

# -*- coding: utf-8 -*-
"""镜仔搜索 · core 基础模块测试（stdlib unittest）

覆盖：配置读写（BOM/损坏回退/原子写）、数据目录定位、访问口令生成、
搜索框高亮词解析、规范引用文本。
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core  # noqa: E402
from core import (  # noqa: E402
    _get_app_dir, APP_DIR, _resource_path,
    load_config, save_config, _DB_MIN_SIZE, _resolve_data_root,
    _gen_token, kw_terms, build_citation,
)


class TestPaths(unittest.TestCase):
    def test_app_dir_is_core_dir(self):
        self.assertEqual(APP_DIR, os.path.dirname(os.path.abspath(core.__file__)))
        self.assertEqual(_get_app_dir(), APP_DIR)

    def test_resource_path(self):
        self.assertEqual(_resource_path('a.png'),
                         os.path.join(APP_DIR, 'a.png'))


class TestConfig(unittest.TestCase):
    def setUp(self):
        self._orig = core.CONFIG_PATH
        self.tmp = tempfile.mkdtemp(prefix='core_cfg_')
        core.CONFIG_PATH = os.path.join(self.tmp, 'config.json')

    def tearDown(self):
        core.CONFIG_PATH = self._orig
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_when_missing(self):
        cfg = load_config()
        self.assertEqual(cfg['root_dir'], '')
        self.assertEqual(cfg['ollama_base'], 'http://127.0.0.1:11434')
        self.assertEqual(cfg['qa_topk'], 8)

    def test_merge_defaults(self):
        with open(core.CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump({'root_dir': 'D:/x'}, f)
        cfg = load_config()
        self.assertEqual(cfg['root_dir'], 'D:/x')
        self.assertEqual(cfg['ollama_base'], 'http://127.0.0.1:11434')

    def test_bom_tolerant(self):
        with open(core.CONFIG_PATH, 'w', encoding='utf-8') as f:
            f.write('\ufeff' + json.dumps({'root_dir': 'BOM路径'}))
        cfg = load_config()
        self.assertEqual(cfg['root_dir'], 'BOM路径')

    def test_bad_config_falls_back_to_bak(self):
        with open(core.CONFIG_PATH, 'w', encoding='utf-8') as f:
            f.write('{invalid json')
        with open(core.CONFIG_PATH + '.bak', 'w', encoding='utf-8') as f:
            json.dump({'root_dir': '来自bak'}, f)
        cfg = load_config()
        self.assertEqual(cfg['root_dir'], '来自bak')
        self.assertTrue(os.path.exists(core.CONFIG_PATH + '.bad'))

    def test_save_roundtrip(self):
        save_config({'root_dir': 'D:/save', 'recent_keywords': ['消防']})
        cfg = load_config()
        self.assertEqual(cfg['root_dir'], 'D:/save')
        self.assertEqual(cfg['recent_keywords'], ['消防'])
        self.assertTrue(os.path.exists(core.CONFIG_PATH + '.bak'))


class TestResolveDataRoot(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='core_root_')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _mkdb(self, d, size=None):
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'index.db'), 'wb') as f:
            f.write(b'x' * (size if size is not None else _DB_MIN_SIZE))

    def test_app_dir_wins(self):
        app = os.path.join(self.root, 'app')
        self._mkdb(app)
        self.assertEqual(_resolve_data_root(app, {}), app)

    def test_last_db_path_priority(self):
        app = os.path.join(self.root, 'app')
        other = os.path.join(self.root, 'other')
        self._mkdb(app)
        self._mkdb(other)
        cfg = {'last_db_path': os.path.join(other, 'index.db')}
        self.assertEqual(_resolve_data_root(app, cfg), other)

    def test_parent_fallback(self):
        app = os.path.join(self.root, 'app')
        os.makedirs(app)
        self._mkdb(self.root)
        self.assertEqual(_resolve_data_root(app, {}), self.root)

    def test_no_db_returns_app_dir(self):
        app = os.path.join(self.root, 'app')
        os.makedirs(app)
        self.assertEqual(_resolve_data_root(app, {}), app)

    def test_small_db_ignored(self):
        app = os.path.join(self.root, 'app')
        self._mkdb(app, size=10)  # 小于 100KB 阈值，视为空库
        self.assertEqual(_resolve_data_root(app, {}), app)


class TestGenToken(unittest.TestCase):
    def test_length(self):
        self.assertEqual(len(_gen_token()), 6)
        self.assertEqual(len(_gen_token(12)), 12)

    def test_no_ambiguous_chars(self):
        for _ in range(50):
            t = _gen_token()
            for ch in '0O1lIi':
                self.assertNotIn(ch, t)

    def test_randomness(self):
        self.assertNotEqual(_gen_token(), _gen_token())


class TestKwTerms(unittest.TestCase):
    def test_and(self):
        self.assertEqual(kw_terms('消防 给排水'), ['消防', '给排水'])

    def test_or(self):
        self.assertEqual(kw_terms('消防|给排水'), ['消防', '给排水'])
        self.assertEqual(kw_terms('消防 OR 给排水'), ['消防', '给排水'])

    def test_not(self):
        self.assertEqual(kw_terms('消防 -给排水'), ['消防'])

    def test_plus(self):
        self.assertEqual(kw_terms('+消防 给排水'), ['消防', '给排水'])

    def test_meta_prefix_filtered(self):
        # 文号:/状态:/日期:/名称: 前缀是元数据过滤，不作为高亮词
        self.assertEqual(kw_terms('文号:GB50016 消防'), ['消防'])

    def test_empty(self):
        self.assertEqual(kw_terms(''), [])


class TestBuildCitation(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='core_cite_')
        self.db = os.path.join(self.root, 'idx.db')
        doc = os.path.join(self.root, '安全制度.txt')
        with open(doc, 'w', encoding='utf-8') as f:
            f.write('《第3号管理制度》\n第一章 总则\n'
                    '第一条 本制度适用于全公司。\n'
                    '第二条 高空作业需系安全带。\n')
        from indexer import index_directory, update_metadata
        index_directory(self.root, self.db)
        update_metadata(self.db, doc, doc_no='建标〔2024〕3号')
        self.doc = doc

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_citation(self):
        cite = build_citation(self.db, self.doc, 4)
        self.assertTrue(cite.startswith('《安全制度》'))
        self.assertIn('（建标〔2024〕3号）', cite)
        self.assertTrue(cite.endswith(': 第二条 高空作业需系安全带。'))


if __name__ == '__main__':
    unittest.main()

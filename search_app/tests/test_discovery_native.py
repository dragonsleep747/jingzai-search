# -*- coding: utf-8 -*-
"""完整原生 NTFS 建库的合成全量枚举验证。

不接触真实卷：把 usn 只读调用替换为合成记录，验证 NativeIndexBuilder 的
枚举循环、路径重建、有界内存、未知元数据、重解析点、未解析路径与 checkpoint。
"""
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from discovery.catalog import Catalog  # noqa: E402
from discovery.model import SourceInfo  # noqa: E402
from discovery.native_scan import NativeIndexBuilder  # noqa: E402
from discovery import usn as usn_module  # noqa: E402


def _journal(**overrides):
    data = {
        'usn_journal_id': 424242,
        'next_usn': 100000,
        'first_usn': 0,
        'lowest_valid_usn': 0,
        'max_usn': 100000,
        'maximum_size': 1 << 30,
        'allocation_delta': 1 << 20,
        'min_supported_major': 2,
        'max_supported_major': 3,
    }
    data.update(overrides)
    return data


def _records(root_ref=5, orphan=False):
    recs = [
        {'usn': 1, 'file_ref': root_ref, 'parent_ref': root_ref, 'name': '',
         'reason': 0, 'attributes': usn_module.FILE_ATTRIBUTE_DIRECTORY,
         'is_dir': True, 'major_version': 2},
        {'usn': 2, 'file_ref': 100, 'parent_ref': root_ref, 'name': '甲目录',
         'reason': 0, 'attributes': usn_module.FILE_ATTRIBUTE_DIRECTORY,
         'is_dir': True, 'major_version': 2},
        {'usn': 3, 'file_ref': 101, 'parent_ref': 100, 'name': '报告 2026.txt',
         'reason': 0, 'attributes': 0, 'is_dir': False, 'major_version': 2},
        {'usn': 4, 'file_ref': 102, 'parent_ref': 100, 'name': '图片.jpg',
         'reason': 0, 'attributes': 0, 'is_dir': False, 'major_version': 2},
        {'usn': 5, 'file_ref': 103, 'parent_ref': root_ref, 'name': '联接点',
         'reason': 0,
         'attributes': usn_module.FILE_ATTRIBUTE_DIRECTORY | usn_module.FILE_ATTRIBUTE_REPARSE_POINT,
         'is_dir': True, 'major_version': 2},
    ]
    if orphan:
        recs.append({'usn': 6, 'file_ref': 200, 'parent_ref': 999, 'name': '孤儿.txt',
                     'reason': 0, 'attributes': 0, 'is_dir': False, 'major_version': 2})
    return recs


class TestNativeIndexBuilder(unittest.TestCase):
    def setUp(self):
        self.root_patch=patch('discovery.native_scan._root_id',return_value=5)
        self.meta_patch=patch('discovery.native_scan._metadata',side_effect=lambda path,ref:
            SimpleNamespace(st_size=None,st_mtime_ns=None,st_nlink=1,st_ino=ref,st_dev=0x1234))
        self.root_patch.start()
        self.meta_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(self.meta_patch.stop)
        self.tmp = tempfile.mkdtemp(prefix='jingzai-native-')
        self.db = os.path.join(self.tmp, 'catalog.db')
        self.catalog = Catalog(self.db)
        self.source = SourceInfo(source_id='vol:syn', kind='volume', root='F:\\',
                                 volume_serial=0x1234, filesystem='NTFS', provider='usn')
        self.catalog.register_source(self.source)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_full_synthetic_enumeration_builds_complete_catalog(self):
        records = _records()
        with patch.dict('sys.modules'), patch.multiple(
                'discovery.native_scan.usn',
                open_volume_handle=lambda root: 1,
                close_handle=lambda handle: None,
                query_usn_journal=lambda handle: _journal(),
                enum_usn_chunk=_fake_enum(records),
                parse_usn_buffer=_fake_parse(records)):
            builder = NativeIndexBuilder(self.catalog, self.source)
            report = builder.build()
        self.assertEqual(report['state'], 'complete', report)
        self.assertEqual(report['found'], len(records))
        entries = self.catalog.list_sources()
        self.assertEqual(self.catalog.count_entries('vol:syn'), len(records))
        # size/mtime 未知值不伪造为 0
        from discovery.model import SearchRequest
        page = self.catalog.search(SearchRequest(query='报告', limit=20))
        self.assertEqual(len(page.results), 1)
        self.assertEqual(page.results[0]['name'], '报告 2026.txt')
        self.assertIsNone(page.results[0].get('size'))
        self.assertIsNone(page.results[0].get('mtime_ns'))
        # 重解析点记为 link
        link = self.catalog.search(SearchRequest(query='联接点', limit=20))
        self.assertEqual(link.results[0]['kind'], 'link')
        cp = self.catalog.get_checkpoint('vol:syn')
        self.assertEqual(cp['journal_id'], '424242')
        self.assertEqual(cp['cursor'], '100000')

    def test_unresolved_parent_marks_partial_and_keeps_cursor_consistent(self):
        records = _records(orphan=True)
        with patch.dict('sys.modules'), patch.multiple(
                'discovery.native_scan.usn',
                open_volume_handle=lambda root: 1,
                close_handle=lambda handle: None,
                query_usn_journal=lambda handle: _journal(),
                enum_usn_chunk=_fake_enum(records),
                parse_usn_buffer=_fake_parse(records)):
            builder = NativeIndexBuilder(self.catalog, self.source)
            report = builder.build()
        self.assertEqual(report['state'], 'partial')
        self.assertIn('unresolved', report['reason'])
        source = self.catalog.get_source('vol:syn')
        self.assertTrue(source['needs_rescan'])


def _fake_enum(records):
    state = {'calls': 0}
    def fake_enum(handle, start_ref, buffer_size):
        if state['calls'] == 0:
            state['calls'] += 1
            return (999).to_bytes(8, 'little') + b'x' * 8
        return b''
    return fake_enum


def _fake_parse(records):
    state = {'calls': 0}
    def fake_parse(buf, start_offset=0, max_records=100000):
        state['calls'] += 1
        if state['calls'] == 1:
            return records, {}
        return [], {}
    return fake_parse


if __name__ == '__main__':
    unittest.main()

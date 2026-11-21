# -*- coding: utf-8 -*-
"""目录发现：FRN→路径重建与 USN 记录→事件转换（合成记录）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discovery.sync import build_path_map, convert_usn_records_to_events  # noqa: E402
from discovery import usn  # noqa: E402


def rec(fr, pr, name, is_dir=False, reason=usn.USN_REASON_FILE_CREATE, usn_no=0):
    return {'file_ref': fr, 'parent_ref': pr, 'name': name, 'is_dir': is_dir,
            'reason': reason, 'usn': usn_no}


class TestBuildPathMap(unittest.TestCase):
    def test_tree(self):
        records = [
            rec(5, 5, '.', True),           # 根
            rec(10, 5, '资料', True),
            rec(11, 10, '报告.txt'),
            rec(12, 11, 'x', True),          # 无子项目录
        ]
        paths = build_path_map(records)
        self.assertEqual(paths[10], '资料')
        self.assertEqual(paths[11], os.path.join('资料', '报告.txt'))

    def test_unresolved_parent_missing(self):
        records = [rec(20, 999, '孤儿.txt')]
        paths = build_path_map(records)
        self.assertNotIn(20, paths)


class TestConvertUsn(unittest.TestCase):
    def test_create(self):
        events, stats = convert_usn_records_to_events(
            [rec(30, 5, 'new.txt', reason=usn.USN_REASON_FILE_CREATE)],
            {5: ''})
        self.assertEqual(events[0].change_type, 'created')
        self.assertEqual(events[0].new_parent_path, '')
        self.assertEqual(events[0].new_name, 'new.txt')

    def test_delete(self):
        events, _ = convert_usn_records_to_events(
            [rec(31, 5, 'gone.txt', reason=usn.USN_REASON_FILE_DELETE)],
            {31: os.path.join('d', 'gone.txt'), 5: ''})
        self.assertEqual(events[0].change_type, 'deleted')

    def test_rename_new_name(self):
        events, stats = convert_usn_records_to_events(
            [rec(32, 10, 'b.txt', reason=usn.USN_REASON_RENAME_NEW_NAME)],
            {32: os.path.join('d', 'a.txt'), 10: 'd'})
        self.assertEqual(events[0].change_type, 'renamed')
        self.assertEqual(events[0].old_name, 'a.txt')
        self.assertEqual(events[0].new_name, 'b.txt')
        self.assertEqual(stats['rename_old_skipped'], 0)

    def test_rename_old_skipped(self):
        events, stats = convert_usn_records_to_events(
            [rec(33, 10, 'a.txt', reason=usn.USN_REASON_RENAME_OLD_NAME)],
            {33: os.path.join('d', 'a.txt'), 10: 'd'})
        self.assertEqual(events, [])
        self.assertEqual(stats['rename_old_skipped'], 1)

    def test_modified_distinct_from_rename(self):
        # 内容修改产生 modified 且带 content_version；更名不产生 modified
        events, _ = convert_usn_records_to_events(
            [rec(34, 10, 'a.txt', reason=usn.USN_REASON_DATA_OVERWRITE)],
            {34: os.path.join('d', 'a.txt'), 10: 'd'})
        self.assertEqual(events[0].change_type, 'modified')
        self.assertEqual(events[0].content_version, 1)

    def test_child_after_new_dir_same_batch(self):
        # 同批先建目录，再建其子文件：frn_map 就地更新可解析
        events, _ = convert_usn_records_to_events(
            [rec(40, 5, '新目录', is_dir=True, reason=usn.USN_REASON_FILE_CREATE),
             rec(41, 40, '子.txt', reason=usn.USN_REASON_FILE_CREATE)],
            {5: ''})
        self.assertEqual(events[1].new_parent_path, '新目录')


if __name__ == '__main__':
    unittest.main()

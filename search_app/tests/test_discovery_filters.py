# -*- coding: utf-8 -*-
"""目录发现：排除规则与字面检索边界。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discovery.filters import (  # noqa: E402
    escape_like, parse_ext_filter, path_within, should_ignore_dir_name,
    should_ignore_file_name,
)


class TestEscapeLike(unittest.TestCase):
    def test_percent_underscore_backslash(self):
        self.assertEqual(escape_like('100%_x'), '100\\%\\_x')
        self.assertEqual(escape_like('C:\\a'), 'C:\\\\a')

    def test_chinese_unchanged(self):
        self.assertEqual(escape_like('安全生产'), '安全生产')


class TestParseExt(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(parse_ext_filter('.txt'), ['txt'])
        self.assertEqual(parse_ext_filter('TXT'), ['txt'])
        self.assertEqual(parse_ext_filter(['txt', 'pdf']), ['txt', 'pdf'])
        self.assertEqual(parse_ext_filter('txt,pdf；docx'), ['txt', 'pdf', 'docx'])
        self.assertEqual(parse_ext_filter(None), [])
        self.assertEqual(parse_ext_filter(''), [])


class TestPathWithin(unittest.TestCase):
    def test_boundary(self):
        self.assertTrue(path_within(r'D:\资料', r'D:\资料'))
        self.assertTrue(path_within(r'D:\资料', r'D:\资料\子目录\文件.txt'))
        self.assertFalse(path_within(r'D:\资料', r'D:\资料备份\文件.txt'))
        self.assertFalse(path_within(r'D:\资料', r'D:\资料2'))

    def test_case_insensitive_windows(self):
        if os.name == 'nt':
            self.assertTrue(path_within(r'C:\DATA', r'c:\data\file.txt'))


class TestIgnore(unittest.TestCase):
    def test_always_ignore(self):
        self.assertTrue(should_ignore_dir_name('$Recycle.Bin', False))
        self.assertTrue(should_ignore_dir_name('System Volume Information', False))

    def test_volume_root_only(self):
        self.assertTrue(should_ignore_dir_name('windows', True))
        self.assertFalse(should_ignore_dir_name('windows', False))

    def test_do_not_ignore_build_at_directory_layer(self):
        # dist/build 属于内容层排除，目录层不排除（用户可能要找里面的文件）
        self.assertFalse(should_ignore_dir_name('build', False))
        self.assertFalse(should_ignore_dir_name('dist', False))

    def test_ignore_files(self):
        self.assertTrue(should_ignore_file_name('pagefile.sys'))
        self.assertFalse(should_ignore_file_name('report.pdf'))


if __name__ == '__main__':
    unittest.main()

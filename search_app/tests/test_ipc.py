# -*- coding: utf-8 -*-
"""镜仔搜索 · 命令行 / jingzai:// 协议解析与单实例边界测试"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ipc import parse_protocol_url, read_command, MAX_KW_LEN  # noqa: E402


class TestProtocolUrl(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(parse_protocol_url('jingzai://search?kw=差旅'), '差旅')

    def test_url_decode(self):
        self.assertEqual(
            parse_protocol_url('jingzai://search?kw=%E5%AE%89%E5%85%A8'),
            '安全')

    def test_wrong_scheme(self):
        self.assertIsNone(parse_protocol_url('http://search?kw=差旅'))

    def test_no_kw(self):
        self.assertIsNone(parse_protocol_url('jingzai://search?other=1'))

    def test_scheme_case_insensitive(self):
        self.assertEqual(parse_protocol_url('JingZai://search?kw=abc'), 'abc')

    def test_garbage(self):
        self.assertIsNone(parse_protocol_url('not a url at all'))
        self.assertIsNone(parse_protocol_url(''))


class TestReadCommand(unittest.TestCase):
    def test_s_flag(self):
        self.assertEqual(read_command(['prog', '/s', '差旅']), '差旅')

    def test_search_eq(self):
        self.assertEqual(read_command(['prog', '--search=安全']), '安全')

    def test_protocol(self):
        self.assertEqual(
            read_command(['prog', 'jingzai://search?kw=检查']), '检查')

    def test_last_explicit_wins(self):
        # read_command 逐个扫描，后出现的显式参数覆盖前面的（与实现一致）
        self.assertEqual(read_command(['prog', '/s', 'A', '--search=B']), 'B')

    def test_no_arg(self):
        self.assertIsNone(read_command(['prog']))

    def test_long_kw_truncated(self):
        kw = '长' * (MAX_KW_LEN + 100)
        self.assertEqual(len(read_command(['prog', '/s', kw])), MAX_KW_LEN)


if __name__ == '__main__':
    unittest.main(verbosity=2)

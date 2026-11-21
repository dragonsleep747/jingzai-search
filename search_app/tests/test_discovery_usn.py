# -*- coding: utf-8 -*-
"""目录发现：USN 记录解析（V2/V3、截断、未知版本，合成字节）。"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discovery import usn  # noqa: E402


def _align8(n):
    return (n + 7) & ~7


def _v2_record(usn_no, file_ref, parent_ref, name, reason, attrs=0):
    name_b = name.encode('utf-16-le')
    fn_len = len(name_b)
    rec_len = _align8(60 + fn_len)
    buf = bytearray(rec_len)
    struct.pack_into('<IHHQQQQIIIIHH', buf, 0, rec_len, 2, 0, file_ref,
                     parent_ref, usn_no, 123456789, reason, 0, 0, attrs,
                     fn_len, 60)
    buf[60:60 + fn_len] = name_b
    return bytes(buf)


def _v3_record(usn_no, file_ref_hi, file_ref_lo, parent_ref, name, reason):
    name_b = name.encode('utf-16-le')
    fn_len = len(name_b)
    rec_len = _align8(76 + fn_len)
    buf = bytearray(rec_len)
    # 128 位 little-endian：低 64 位在前
    struct.pack_into('<IHHQQQQQQIIIIHH', buf, 0, rec_len, 3, 0,
                     file_ref_lo, file_ref_hi, 0, parent_ref,
                     usn_no, 123456789, reason, 0, 0, 0, fn_len, 76)
    buf[76:76 + fn_len] = name_b
    return bytes(buf)


class TestParseUsnBuffer(unittest.TestCase):
    def test_v2_single(self):
        buf = _v2_record(100, 200, 5, '报告.txt',
                         usn.USN_REASON_FILE_CREATE,
                         attrs=0x20)
        records, stats = usn.parse_usn_buffer(buf)
        self.assertEqual(len(records), 1)
        r = records[0]
        self.assertEqual(r['usn'], 100)
        self.assertEqual(r['file_ref'], 200)
        self.assertEqual(r['parent_ref'], 5)
        self.assertEqual(r['name'], '报告.txt')
        self.assertEqual(r['major_version'], 2)
        self.assertFalse(r['is_dir'])
        self.assertEqual(stats['truncated'], 0)

    def test_v3_single_128bit_ref(self):
        buf = _v3_record(500, 0x1234, 0x5678, 5, '资料',
                         usn.USN_REASON_FILE_CREATE)
        records, _ = usn.parse_usn_buffer(buf)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['major_version'], 3)
        self.assertEqual(records[0]['file_ref'],
                         (0x1234 << 64) | 0x5678)
        self.assertEqual(records[0]['name'], '资料')

    def test_multiple_records(self):
        buf = (_v2_record(1, 10, 5, 'a.txt', usn.USN_REASON_FILE_CREATE) +
               _v2_record(2, 11, 5, 'b.txt', usn.USN_REASON_FILE_CREATE))
        records, _ = usn.parse_usn_buffer(buf)
        self.assertEqual([r['name'] for r in records], ['a.txt', 'b.txt'])

    def test_truncated_tail(self):
        good = _v2_record(1, 10, 5, 'a.txt', usn.USN_REASON_FILE_CREATE)
        # 8 字节残缺头：RecordLength=100 但剩余字节不足 → 截断
        tail = struct.pack('<IHH', 100, 2, 0)
        buf = good + tail
        records, stats = usn.parse_usn_buffer(buf)
        self.assertEqual(len(records), 1)
        self.assertEqual(stats['truncated'], 1)

    def test_unknown_major_version(self):
        name_b = 'x.txt'.encode('utf-16-le')
        rec_len = _align8(60 + len(name_b))
        buf = bytearray(rec_len)
        struct.pack_into('<IHHQQQQIIIIHH', buf, 0, rec_len, 9, 0, 1, 1,
                         1, 1, 0, 0, 0, 0, len(name_b), 60)
        buf[60:60 + len(name_b)] = name_b
        records, stats = usn.parse_usn_buffer(bytes(buf))
        self.assertEqual(records, [])
        self.assertEqual(stats['unsupported'], 1)


if __name__ == '__main__':
    unittest.main()

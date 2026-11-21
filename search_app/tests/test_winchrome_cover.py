# -*- coding: utf-8 -*-
"""winchrome 遮罩预贴 API（set_cover_window / set_cover_rect）的纯状态单测。

只锁 API 的入参与登记行为，不建真窗口（真机行为由 _test_ui3.py 的
还原防黑帧段落覆盖）。"""
import unittest

import winchrome


class TestCoverState(unittest.TestCase):

    def setUp(self):
        self._saved = dict(winchrome._COVER)

    def tearDown(self):
        winchrome._COVER.clear()
        winchrome._COVER.update(self._saved)

    def test_rect_roundtrip(self):
        winchrome.set_cover_rect((10, 20, 800, 600))
        self.assertEqual(winchrome._COVER['rect'], (10, 20, 800, 600))

    def test_rect_ignores_none(self):
        winchrome._COVER['rect'] = None
        winchrome.set_cover_rect(None)
        self.assertIsNone(winchrome._COVER['rect'])

    def test_rect_coerces_to_ints(self):
        winchrome.set_cover_rect((1.9, 2.1, 3.0, 4.5))
        self.assertEqual(winchrome._COVER['rect'], (1, 2, 3, 4))

    def test_cover_window_ignores_zero(self):
        winchrome._COVER['hwnd'] = 777
        winchrome.set_cover_window(0)
        self.assertEqual(winchrome._COVER['hwnd'], 777)

    def test_cover_window_stores_hwnd(self):
        winchrome.set_cover_window(12345)
        self.assertEqual(winchrome._COVER['hwnd'], 12345)

    def test_armed_roundtrip(self):
        winchrome.set_cover_armed(True)
        self.assertTrue(winchrome._COVER['armed'])
        winchrome.set_cover_armed(False)
        self.assertFalse(winchrome._COVER['armed'])

    def test_cover_state_starts_disarmed(self):
        # 预贴必须以「轮询确认已最小化」为前提，初始必须未武装
        self.assertIsNotNone(winchrome._COVER.get('armed'))
        self.assertFalse(winchrome._COVER['armed'])

    def test_pre_show_constants_present(self):
        # SC_RESTORE 预贴依赖这些常量，防止后续重构误删
        self.assertEqual(winchrome._SC_RESTORE, 0xF120)
        self.assertTrue(winchrome._SWP_NOACTIVATE & 0x10)
        self.assertTrue(winchrome._SWP_SHOWWINDOW & 0x40)


if __name__ == '__main__':
    unittest.main()

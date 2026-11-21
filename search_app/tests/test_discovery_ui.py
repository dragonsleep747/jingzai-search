# -*- coding: utf-8 -*-
"""探索/智答 Qt 接入回归：真实目录扫描 + offscreen 窗口。

验证范围控件、目录检索路由、目录预览与文件动作、范围对话框、智答切换。
"""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

try:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from qt_frontend.backend import Store
    from qt_frontend.window import Window
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, '使用 .venv-qt 运行 Qt 界面测试')
class TestDiscoveryUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix='jingzai-discui-')
        self.store = Store(self.folder)
        self.store.settings['max_backups'] = 0
        self.store.settings['qt_startup_refresh'] = False
        self.window = Window(self.store)
        self.window.show()
        self.wait_idle()
        self.sample = Path(self.folder) / '样本'
        (self.sample / '子').mkdir(parents=True)
        (self.sample / '子' / '目标文件A.txt').write_text('x', encoding='utf-8')
        (self.sample / '子' / '无关B.pdf').write_text('%PDF-1.4', encoding='utf-8')
        self.window.discovery_scope.setCurrentIndex(0)  # 已选电脑位置
        source_id = self.window.discovery.add_directory(str(self.sample))
        self.window.discovery.start_scan(source_id)

    def wait_until(self, condition, timeout=8):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.application.processEvents()
            QTest.qWait(10)
        self.assertTrue(condition())

    def wait_idle(self):
        self.application.processEvents()
        self.wait_until(lambda: not self.window.jobs.active)

    def tearDown(self):
        self.wait_idle()
        self.window._force_exit = True
        self.window.close()
        self.application.processEvents()

    def test_explore_mode_has_mode_switch_and_scope(self):
        self.assertEqual(self.window.search_mode.count(), 2)
        self.assertEqual(self.window.search_mode.itemText(0), '探索')
        self.assertEqual(self.window.search_mode.itemText(1), '智答')
        self.assertEqual(self.window.discovery_scope.count(), 3)
        self.assertEqual(set(self.window.nav), {'search', 'library', 'bookmarks'})

    def test_directory_search_routes_to_discovery_results(self):
        self.window.use_query('目标文件')
        self.wait_until(lambda: self.window.discovery_results.topLevelItemCount() > 0)
        self.assertEqual(self.window.result_stack.currentIndex(), 2)
        self.assertEqual(self.window.discovery_results.topLevelItem(0).text(0), '目标文件A.txt')

    def test_directory_result_previews_file_info_and_copies_path(self):
        self.window.use_query('目标文件')
        self.wait_until(lambda: self.window.discovery_results.topLevelItemCount() > 0)
        item = self.window.discovery_results.topLevelItem(0)
        hit = item.data(0, Qt.ItemDataRole.UserRole)
        self.window.preview_discovery_file(hit)
        self.assertEqual(self.window.reader_stack.currentIndex(), 2)
        self.assertIn(str(self.sample), self.window.discovery_preview.info.toPlainText() or '')
        from PySide6.QtWidgets import QApplication
        self.window.copy_discovery_path(hit['path'])
        self.assertEqual(QApplication.clipboard().text(), hit['path'])

    def test_content_scope_preserves_legacy_library_search(self):
        source = Path(self.folder) / '正文资料.txt'
        source.write_text('第一条 安全培训要求。', encoding='utf-8')
        self.store.index_files([str(source)], lambda _: None, threading.Event())
        self.window.discovery_scope.setCurrentIndex(1)  # 原资料库
        self.window.use_query('安全培训')
        self.wait_until(lambda: bool(self.window.groups))
        self.assertEqual(self.window.groups[0]['path'], str(source))

    def test_mode_switch_to_qa_navigates_to_native_ai_page(self):
        self.window.search_mode.setCurrentIndex(1)
        self.wait_idle()
        self.assertEqual(self.window._current_page, 'ai')
        self.assertTrue(hasattr(self.window, 'ai_question'))
        self.window.search_mode.setCurrentIndex(0)
        self.assertEqual(self.window._current_page, 'search')

    def test_range_dialog_builds_and_lists_sources(self):
        self.window.open_range_manager()
        dialog = self.window._range_dialog
        self.assertTrue(dialog.isVisible())
        self.wait_until(lambda: dialog.tree.topLevelItemCount()>0)
        dialog.accept()


if __name__ == '__main__':
    unittest.main()

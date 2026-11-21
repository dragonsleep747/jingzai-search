"""Second-round journeys: continuity, retention, preview decoding and AI control."""
import os
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_ux_review as fixtures

if fixtures.fixtures.QT:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QTextCursor
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication


@unittest.skipUnless(fixtures.fixtures.QT, 'run with .venv-qt')
class TestDeepExperience(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.TestExperienceReview.setUpClass.__func__(cls)

    setUp = fixtures.TestExperienceReview.setUp
    tearDown = fixtures.TestExperienceReview.tearDown
    wait = fixtures.TestExperienceReview.wait
    idle = fixtures.TestExperienceReview.idle
    index = fixtures.TestExperienceReview.index
    search = fixtures.TestExperienceReview.search

    def test_returning_from_ai_keeps_results_and_filter_without_requery(self):
        self.index('甲资料.txt', '连续体验关键词\n甲正文')
        self.index('乙资料.txt', '连续体验关键词\n乙正文')
        tree = self.search('连续体验关键词')
        self.window.result_filter.setText('乙')
        root = tree.topLevelItem(0)
        root.setExpanded(False)
        generation = self.window._preview_generation
        self.window.search_mode.setCurrentIndex(1)
        with patch.object(self.store, 'search', wraps=self.store.search) as search:
            self.window.search_mode.setCurrentIndex(0)
            self.idle()
            search.assert_not_called()
        self.assertEqual(self.window.result_filter.text(), '乙')
        self.assertFalse(root.isExpanded())
        self.assertEqual(self.window._preview_generation, generation)

    def test_catalog_preferences_survive_reopening_the_window(self):
        from qt_frontend.backend import Store
        from qt_frontend.window import Window
        self.window.discovery_scope.setCurrentIndex(0)
        self.window.catalog_scope.setCurrentIndex(1)
        self.window.catalog_ext.setText('pdf,txt')
        self.window.catalog_root.setText(str(self.sample))
        self.window.catalog_sort.setCurrentIndex(2)
        self.idle()
        self.wait(lambda: not self.window.explore_preferences_timer.isActive())
        reopened = Window(Store(self.root))
        try:
            self.assertEqual(reopened.discovery_scope.currentIndex(), 0)
            self.assertEqual(reopened.catalog_scope.currentIndex(), 1)
            self.assertEqual(reopened.catalog_ext.text(), 'pdf,txt')
            self.assertEqual(reopened.catalog_root.text(), str(self.sample))
            self.assertEqual(reopened.catalog_sort.currentIndex(), 2)
        finally:
            reopened._force_exit = reopened._closing_requested = True
            reopened.close()
            self.wait(lambda: not reopened.jobs.active)
            reopened.jobs.pool.waitForDone(5000)
            reopened.deleteLater()

    def test_returning_from_ai_retains_all_loaded_directory_pages(self):
        for i in range(105):
            (self.sample / f'连续分页-{i:03}.txt').write_text('样本', encoding='utf-8')
        self.window.discovery.start_scan(self.sid)
        self.window.discovery_scope.setCurrentIndex(0)
        tree = self.search('连续分页')
        self.window.load_more_discovery()
        self.idle()
        self.assertEqual(tree.topLevelItemCount(), 105)
        self.window.search_mode.setCurrentIndex(1)
        self.window.search_mode.setCurrentIndex(0)
        self.idle()
        self.assertEqual(tree.topLevelItemCount(), 105)

    def test_utf8_preview_cutoff_does_not_turn_chinese_into_garbage(self):
        from qt_frontend.file_preview import load_preview
        path = self.sample / '长中文.txt'
        path.write_text('中文内容\n' * 100000, encoding='utf-8')
        payload = load_preview(str(path), threading.Event())
        self.assertTrue('中文内容' in payload['html'], 'UTF-8 Chinese text was decoded incorrectly')
        self.assertTrue('�' not in payload['html'], 'Truncation introduced replacement characters')
        self.assertTrue(payload['truncated'])

    def test_utf16_preview_cutoff_keeps_surrogate_pairs_valid(self):
        from qt_frontend.file_preview import load_preview
        path = self.sample / '长UTF16.txt'
        # The read boundary falls between the two halves of the emoji.
        path.write_bytes(b'\xff\xfe' + ('中中' + '🙂' * 300000).encode('utf-16-le'))
        payload = load_preview(str(path), threading.Event())
        self.assertTrue('中🙂' in payload['html'], 'UTF-16 preview was decoded incorrectly')
        self.assertTrue('�' not in payload['html'], 'Truncation split a surrogate pair')
        self.assertTrue(payload['truncated'])

    def test_finished_hidden_jobs_are_bounded_and_release_worker_closures(self):
        jobs = self.window.jobs
        visible = jobs.submit('保留的用户任务', lambda p, s: {'ok': 1})
        for _ in range(75):
            jobs.submit('输入即搜', lambda p, s: 'x' * 100000, visible=False)
        self.idle()
        self.assertLessEqual(sum(not job.visible for job in jobs.jobs.values()), 32)
        self.assertIn(visible, jobs.jobs)
        self.assertIsNone(jobs.jobs[visible].worker.fn)
        self.assertTrue(all(job.result is None for job in jobs.jobs.values() if not job.visible))

    def test_streaming_keeps_user_selection_and_scroll_position(self):
        entered, release = threading.Event(), threading.Event()
        def stream(*args, **kwargs):
            time.sleep(.08)
            yield '最初的回答\n' * 150
            entered.set()
            release.wait(4)
            yield '末尾继续生成\n' * 30
        self.window.search_mode.setCurrentIndex(1)
        self.window.ai_kind.setCurrentIndex(1)
        self.window.search_input.setText('流式阅读')
        with patch('ai_qa.chat_stream', side_effect=stream):
            self.window.ask_ai()
            self.wait(lambda: entered.is_set() and self.window.ai_answer.verticalScrollBar().maximum() > 0)
            editor = self.window.ai_answer
            editor.verticalScrollBar().setValue(0)
            cursor = editor.textCursor()
            cursor.setPosition(0)
            cursor.setPosition(5, QTextCursor.MoveMode.KeepAnchor)
            editor.setTextCursor(cursor)
            selected = editor.textCursor().selectedText()
            release.set()
            self.idle()
        self.assertEqual(editor.verticalScrollBar().value(), 0)
        self.assertEqual(editor.textCursor().selectedText(), selected)

    def test_retry_uses_original_question_and_sources_after_draft_changes(self):
        self.index('重试依据.txt', '重试正文证据')
        self.search('重试正文证据')
        self.window.search_mode.setCurrentIndex(1)
        self.window.search_input.setText('原来的问题')
        with patch('ai_qa.chat_stream', return_value=iter(['原回答'])):
            self.window.ask_ai()
            self.idle()
        self.window.search_input.setText('尚未发送的新草稿')
        self.window.groups = []
        self.assertTrue(hasattr(self.window, 'retry_ai'))
        with patch('ai_qa.chat_stream', return_value=iter(['重新回答'])) as stream:
            self.window.retry_ai()
            self.idle()
        self.assertIn('原来的问题', str(stream.call_args.args[2]))
        self.assertEqual(self.window.ai_sources.count(), 1)
        self.assertEqual(self.window.search_input.text(), '尚未发送的新草稿')

    def test_ai_copy_and_new_conversation_are_available_in_native_ui(self):
        self.window.search_mode.setCurrentIndex(1)
        self.assertTrue(hasattr(self.window, 'ai_copy'))
        self.window.ai_answer.setPlainText('复制本轮回答')
        clipboard = QApplication.clipboard()
        previous = clipboard.text()
        try:
            QTest.mouseClick(self.window.ai_copy, Qt.MouseButton.LeftButton)
            self.assertEqual(clipboard.text(), '复制本轮回答')
        finally:
            clipboard.setText(previous)
        self.window._ai_history = [{'role': 'user', 'content': '旧问题'}]
        QTest.mouseClick(self.window.ai_new, Qt.MouseButton.LeftButton)
        self.assertEqual(self.window._ai_history, [])
        self.assertEqual(self.window.ai_answer.toPlainText(), '')

    def test_filtered_directory_count_matches_visible_results(self):
        for i in range(5):
            (self.sample / f'筛选计数-{i}.txt').write_text(str(i), encoding='utf-8')
        self.window.discovery.start_scan(self.sid)
        self.window.discovery_scope.setCurrentIndex(0)
        self.search('筛选计数')
        self.window.result_filter.setText('筛选计数-2.txt')
        self.assertIn('1 / 5', self.window.result_heading.text())

    def test_retry_chat_replaces_the_previous_turn_without_duplicating_history(self):
        self.window.search_mode.setCurrentIndex(1)
        self.window.ai_kind.setCurrentIndex(1)
        self.window.search_input.setText('同一个问题')
        with patch('ai_qa.chat_stream', return_value=iter(['旧回答'])):
            self.window.ask_ai()
            self.idle()
        with patch('ai_qa.chat_stream', return_value=iter(['新回答'])):
            self.window.retry_ai()
            self.idle()
        self.assertEqual(len(self.window._ai_history), 2)
        self.assertEqual(self.window._ai_history[-1]['content'], '新回答')

    def test_library_changes_during_ai_invalidate_the_suspended_search(self):
        self.index('已有.txt', '变更体验关键词\n已有正文')
        self.search('变更体验关键词')
        self.window.search_mode.setCurrentIndex(1)
        self.window.jobs.submit('新资料入库', lambda p, s: self.index('新增.txt', '变更体验关键词\n新正文'), mutating=True)
        self.idle()
        self.window.search_mode.setCurrentIndex(0)
        self.idle()
        self.assertEqual(self.window.discovery_results.topLevelItemCount(), 2)

    def test_missing_sources_gives_document_guidance_instead_of_model_error(self):
        self.window.search_mode.setCurrentIndex(1)
        self.window.search_input.setText('资料在哪里')
        with patch('ai_qa.chat_stream') as stream:
            self.window.ask_ai()
            self.idle()
            stream.assert_not_called()
        self.assertIn('正文依据', self.window.ai_status.text())
        self.assertNotIn('检查本机模型', self.window.ai_answer.toPlainText())

    def test_a_different_query_after_pending_sort_does_not_keep_old_filter(self):
        self.index('旧文件.txt', '旧查询关键词')
        self.index('新文件.txt', '新查询关键词')
        self.search('旧查询关键词')
        self.window.result_filter.setText('旧文件')
        self.window.catalog_sort.setCurrentIndex(2)
        self.search('新查询关键词')
        self.assertEqual(self.window.result_filter.text(), '')
        self.assertFalse(self.window.discovery_results.topLevelItem(0).isHidden())

    def test_answer_source_opens_the_actual_matching_line(self):
        self.index('精确来源.txt', '\n'.join(['背景文字'] * 50 + ['唯一来源体验关键词']))
        self.search('唯一来源体验关键词')
        self.window.search_mode.setCurrentIndex(1)
        self.window.search_input.setText('哪里提到了它')
        with patch('ai_qa.chat_stream', return_value=iter(['已引用正文'])):
            self.window.ask_ai()
            self.idle()
        item = self.window.ai_sources.item(0)
        self.window._open_ai_source(item)
        self.idle()
        self.assertEqual(self.window.current['line'], 51)

    def test_retry_after_service_configuration_change_uses_new_endpoint(self):
        import ai_qa
        self.window.search_mode.setCurrentIndex(1)
        self.window.ai_kind.setCurrentIndex(1)
        self.window.search_input.setText('连接恢复体验')
        with patch('ai_qa.chat_stream', side_effect=ai_qa.OllamaUnavailable('connection refused')):
            self.window.ask_ai()
            self.idle()
        self.assertIn('模型服务暂不可用', self.window.ai_status.text())
        self.assertNotIn('OllamaUnavailable', self.window.ai_answer.toPlainText())
        self.store.settings['ollama_base'] = 'http://127.0.0.1:11435'
        with patch('ai_qa.chat_stream', return_value=iter(['已恢复'])) as stream:
            self.window.retry_ai()
            self.idle()
        self.assertEqual(stream.call_args.args[0], 'http://127.0.0.1:11435')
        self.assertEqual(self.window.ai_answer.toPlainText(), '已恢复')

    def test_cpu_compatibility_is_applied_on_retry_without_changing_sources(self):
        self.window.search_mode.setCurrentIndex(1)
        self.window.ai_kind.setCurrentIndex(1)
        self.window.search_input.setText('兼容模式体验')
        with patch('ai_qa.chat_stream', side_effect=RuntimeError('CUDA initialization failed')):
            self.window.ask_ai()
            self.idle()
        self.assertIn('兼容模式', self.window.ai_notice.text())
        self.assertEqual(self.window.ai_answer.toPlainText(), '')
        self.assertFalse(self.window.ai_copy.isEnabled())
        self.store.settings['qa_cpu_only'] = True
        with patch('ai_qa.chat_stream', return_value=iter(['CPU 回答'])) as stream:
            self.window.retry_ai()
            self.idle()
        self.assertEqual(stream.call_args.kwargs['options']['num_gpu'], 0)
        self.assertEqual(self.window.ai_answer.toPlainText(), 'CPU 回答')


if __name__ == '__main__':
    unittest.main()

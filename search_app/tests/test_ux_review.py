"""User-journey regressions found during the October desktop experience review."""
import os
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests import test_discovery_integration_review as fixtures

if fixtures.QT:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest


@unittest.skipUnless(fixtures.QT, 'run with .venv-qt')
class TestExperienceReview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.TestUiReview.setUpClass.__func__(cls)

    def setUp(self):
        fixtures.TestUiReview.setUp(self)

    def tearDown(self):
        self.idle()
        evidence = os.environ.get('JINGZAI_UX_EVIDENCE')
        if evidence:
            target = Path(evidence)
            target.mkdir(parents=True, exist_ok=True)
            self.window.grab().save(str(target / (self._testMethodName + '.png')))
        fixtures.TestUiReview.tearDown(self)

    wait = fixtures.TestUiReview.wait
    def idle(self):
        self.wait(lambda: not self.window.search_debounce.isActive())
        fixtures.TestUiReview.idle(self)

    def index(self, name, text):
        path = self.sample / name
        path.write_text(text, encoding='utf-8')
        self.store.index_files([str(path)], lambda _: None, threading.Event())
        return str(path)

    def search(self, query):
        self.window.use_query(query)
        self.idle()
        return self.window.discovery_results

    def test_combined_scope_has_a_working_content_sort(self):
        self.index('Z命名.txt', '体验关键词\n第二行 甲')
        self.index('A命名.txt', '体验关键词\n第二行 乙')
        tree = self.search('体验关键词')
        self.window.sort_order.setCurrentIndex(self.window.sort_order.findData('name'))
        self.idle()
        self.assertEqual([tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())],
                         ['A命名.txt', 'Z命名.txt'])
        menu = self.window.build_search_actions_menu()
        titles = [action.text() for action in menu.actions() if action.menu()]
        self.assertIn('正文结果排序', titles)

    def test_combined_scope_distribution_contains_actual_hits(self):
        self.index('分布.txt', '体验关键词\n另一段')
        self.search('体验关键词')
        self.window.chart_toggle.setChecked(True)
        self.assertEqual(len(self.window.distribution.groups), 1)
        self.assertTrue(self.window.distribution.isVisible())
        root = self.window.discovery_results.topLevelItem(0)
        root.setExpanded(False)
        self.window.jump_distribution(self.groups_path())
        self.assertTrue(root.isExpanded())

    def groups_path(self):
        return self.window.groups[0]['path']

    def test_combined_content_sort_changes_order_and_preserves_reading(self):
        self.index('A短文.txt', '体验排序关键词\n短文')
        self.index('Z长文.txt', '\n'.join(['体验排序关键词'] * 12))
        tree = self.search('体验排序关键词')
        self.window.sort_order.setCurrentIndex(self.window.sort_order.findData('name'))
        self.assertEqual(tree.topLevelItem(0).text(0), 'A短文.txt')
        generation = self.window._preview_generation
        self.window.sort_order.setCurrentIndex(self.window.sort_order.findData('hits'))
        self.assertEqual(tree.topLevelItem(0).text(0), 'Z长文.txt')
        self.assertEqual(self.window._preview_generation, generation)

    def test_directory_sort_keeps_the_result_filter_and_reader(self):
        for name in ('体验甲.txt', '体验乙.txt'):
            (self.sample / name).write_text('预览正文', encoding='utf-8')
        self.window.discovery.start_scan(self.sid)
        self.window.discovery_scope.setCurrentIndex(0)
        tree = self.search('体验')
        self.window.result_filter.setText('乙')
        tree.setCurrentItem(tree.topLevelItem(1))
        self.idle()
        generation = self.window._preview_generation
        self.window.catalog_sort.setCurrentIndex(2)
        self.idle()
        self.assertEqual(self.window.result_filter.text(), '乙')
        self.assertEqual(self.window._preview_generation, generation)
        self.assertEqual(sum(not tree.topLevelItem(i).isHidden() for i in range(tree.topLevelItemCount())), 1)

    def test_pagination_does_not_reopen_a_folded_content_group(self):
        self.index('命中文件.txt', '体验关键词\n第二行')
        for i in range(102):
            (self.sample / f'体验关键词-{i:03}.txt').write_text('其他内容', encoding='utf-8')
        self.window.discovery.start_scan(self.sid)
        tree = self.search('体验关键词')
        root = tree.topLevelItem(0)
        self.assertGreater(root.childCount(), 0)
        QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton,
                         pos=tree.visualItemRect(root).center())
        self.assertFalse(root.isExpanded())
        self.window.load_more_discovery()
        self.idle()
        self.assertFalse(tree.topLevelItem(0).isExpanded())

    def test_directory_loading_preserves_expanded_snippet_and_selected_line(self):
        from qt_frontend.result_snippets import EXPANDED_LINES
        path = self.index('长片段.txt', '体验长片段' * 100)
        tree = self.search('体验长片段')
        root = tree.topLevelItem(0)
        # Use separate clusters to represent the engine's nonadjacent matches.
        group = self.window.discovery_content_hits['groups'][0]
        group['clusters'] = [{'lines': [{'line_no': i, 'line_text': '体验长片段' * 100}]} for i in range(1, 13)]
        self.window.render_discovery_results()
        root = tree.topLevelItem(0)
        more = root.child(root.childCount() - 1)
        self.window.discovery_result_selected(more, None)
        self.assertEqual(root.childCount(), 11)
        child = root.child(0)
        child.setData(0, EXPANDED_LINES, 24)
        tree.setCurrentItem(child)
        self.idle()
        self.window.render_discovery_results()
        root = tree.topLevelItem(0)
        self.assertEqual(root.childCount(), 11)
        self.assertEqual(root.child(0).data(0, EXPANDED_LINES), 24)
        self.assertEqual(tree.currentItem().data(0, Qt.ItemDataRole.UserRole), (path, 1))

    def test_stopped_range_is_not_restarted_by_periodic_updates(self):
        self.window.open_range_manager()
        self.idle()
        dialog = self.window._range_dialog
        for i in range(dialog.tree.topLevelItemCount()):
            item = dialog.tree.topLevelItem(i)
            if item.data(0, Qt.ItemDataRole.UserRole) == self.sid:
                dialog.tree.setCurrentItem(item)
        self.window._discovery_active_ranges.add(self.sid)
        dialog.stop_selected()
        with patch.object(self.window, 'submit_discovery_task') as submit:
            self.window._discovery_tick()
            self.assertNotIn(self.sid, [call.args[0] for call in submit.call_args_list])
        self.assertTrue((self.sample).exists())

    def test_scope_is_visible_and_manageable_without_opening_filters(self):
        self.assertTrue(hasattr(self.window, 'range_button'))
        self.assertTrue(self.window.range_button.isVisible())
        self.assertIn('范围', self.window.range_button.text())
        self.assertIn('资料库', self.window.range_button.toolTip())
        self.assertFalse(self.window.options_scroll.isVisible())

    def test_ai_mode_hides_filters_and_restores_them_on_return(self):
        QTest.mouseClick(self.window.options_button, Qt.MouseButton.LeftButton)
        self.assertTrue(self.window.options_scroll.isVisible())
        self.window.search_mode.setCurrentIndex(1)
        self.assertFalse(self.window.options_scroll.isVisible())
        self.window.search_mode.setCurrentIndex(0)
        self.assertTrue(self.window.options_scroll.isVisible())

    def test_search_failure_remains_visible_after_coverage_refresh(self):
        self.window.discovery_scope.setCurrentIndex(0)
        with patch.object(self.window.discovery, 'search', side_effect=RuntimeError('体验失败样本')):
            self.search('体验')
        self.window.refresh_discovery_coverage()
        self.idle()
        self.assertIn('未完成', self.window.result_summary.text())
        self.assertIn('体验失败样本', self.window.result_summary.toolTip())

    def test_ai_stop_and_failure_have_persistent_status(self):
        self.window.search_mode.setCurrentIndex(1)
        self.window.ai_kind.setCurrentIndex(1)
        self.assertTrue(hasattr(self.window, 'ai_status'))
        self.assertFalse(self.window.ai_stop.isEnabled())
        self.window.search_input.setText('体验问题')
        with patch('ai_qa.chat_stream', side_effect=RuntimeError('连接失败样本')):
            self.window.start_search()
            self.idle()
        self.assertIn('未完成', self.window.ai_status.text())
        self.assertFalse(self.window.ai_stop.isEnabled())

    def test_native_acceptance_exits_even_when_redirected_console_is_unusable(self):
        from qt_frontend.acceptance import Acceptance
        output = self.root / 'console-failure'
        output.mkdir()
        timer = SimpleNamespace(stop=lambda: None)
        harness = SimpleNamespace(completed=False, periodic=timer, watchdog=timer, window=self.window,
                                  output=output, errors=[], cycles=[{}] * 20, checks=[], components={})
        with patch('sys.stdout') as stream, patch.object(self.window, 'close') as close_window, \
                patch.object(self.app, 'exit') as exit_app:
            stream.write.side_effect = OSError('invalid inherited console handle')
            Acceptance.finish(harness)
            self.assertTrue(self.window._force_exit)
            close_window.assert_called_once()
            exit_app.assert_called_once_with(0)
        self.assertTrue((output / 'result.json').is_file())


if __name__ == '__main__':
    unittest.main()

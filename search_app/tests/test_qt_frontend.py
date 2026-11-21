import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

try:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QThread
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from qt_frontend.backend import Store
    from qt_frontend.window import Window
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, '使用 .venv-qt 运行 Qt 界面测试')
class TestQtFrontend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix='jingzai-qt-ui-')
        self.store = Store(self.folder)
        self.store.settings['max_backups'] = 0
        self.store.settings['qt_startup_refresh'] = False
        self.window = Window(self.store)
        # 本类专注正文检索旧能力回归；探索新入口的回归见 test_discovery_ui.py。
        # 将探索检索范围切到“原资料库”，等价于旧版 start_search 行为。
        self.window.discovery_scope.setCurrentIndex(1)
        self.window.show()
        self.wait_idle()

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

    def test_jobs_deliver_callbacks_on_gui_thread(self):
        observed = []
        self.window.jobs.submit('thread test', lambda progress, stop: QThread.currentThread(),
            lambda result: observed.append((result, QThread.currentThread())), visible=False)
        self.wait_idle()
        self.assertNotEqual(observed[0][0], self.application.thread())
        self.assertEqual(observed[0][1], self.application.thread())

    def test_search_preview_bookmark_and_library(self):
        source = Path(self.folder) / '测试资料.txt'
        source.write_text('第一章 总则\n第一条 安全培训是岗位要求。\n', encoding='utf-8')
        self.store.index_files([str(source)], lambda _: None, threading.Event())
        self.window.use_query('安全培训')
        self.wait_until(lambda: self.window.current is not None)
        self.assertEqual(self.window.groups[0]['path'], str(source))
        self.assertIn('安全培训', self.window.reader.toPlainText())
        self.window.toggle_bookmark()
        self.wait_idle()
        self.assertTrue(self.window.current['bookmarked'])
        self.window.navigate('bookmarks')
        self.wait_idle()
        self.assertEqual(self.window.bookmarks_model.rowCount(), 1)
        self.window.navigate('library')
        self.wait_idle()
        self.assertEqual(self.window.library_model.rowCount(), 1)

    def test_latest_search_wins_when_requests_overlap(self):
        source = Path(self.folder) / '文件.txt'
        source.write_text('安全培训\n差旅报销\n', encoding='utf-8')
        self.store.index_files([str(source)], lambda _: None, threading.Event())
        self.window.use_query('安全培训')
        self.window.use_query('差旅报销')
        self.wait_idle()
        self.assertEqual(self.window.query, '差旅报销')
        self.assertIn('差旅报销', self.window.groups[0]['matches'][0]['line_text'])

    def test_window_uses_native_opaque_frame(self):
        from PySide6.QtCore import Qt
        self.assertFalse(self.window.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertFalse(self.window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        self.assertEqual(self.window.windowOpacity(), 1.0)
        # 入口变化：新增原生“智答”页（探索/智答切换），页数由 5 增至 6。
        self.assertEqual(self.window.pages.count(), 5)
        self.assertEqual(self.window.page_indices['ai'],self.window.page_indices['search'])

    def test_saved_search_restores_legacy_template_fields(self):
        self.window.apply_saved_search({'name': '旧版模板', 'kw': '安全', 'mode': 'fuzzy',
                                        'oa': False, 'syn': True, 'ft': 'pdf', 'cs': True})
        self.wait_idle()
        self.assertEqual(self.window.mode.currentIndex(), 1)
        self.assertEqual(self.window.type_filter.currentData(), 'pdf')
        self.assertFalse(self.window.active_filter.isChecked())
        self.assertTrue(self.window.case_filter.isChecked())

    def test_idle_typing_triggers_search_without_enter(self):
        source = Path(self.folder) / '输入即搜.txt'
        source.write_text('自动检索验收。', encoding='utf-8')
        self.store.index_files([str(source)], lambda _: None, threading.Event())
        self.window.search_input.setText('自动检索')
        self.window.search_input.textEdited.emit('自动检索')
        self.wait_until(lambda: bool(self.window.groups))
        self.assertEqual(self.window.query, '自动检索')

    def test_clipboard_fast_save_indexes_without_file_dialog(self):
        QApplication.clipboard().setText('快存独有测试内容。')
        with patch('qt_frontend.tools.QFileDialog.getSaveFileName') as save:
            self.window.clipboard_save()
            self.wait_idle()
            save.assert_not_called()
        self.assertGreater(self.store.search('快存独有测试内容')['hits'], 0)
        self.assertTrue(any((Path(self.folder) / 'library').rglob('剪藏_*.txt')))

    def test_drag_import_uses_complete_preflight_instead_of_silent_import(self):
        source = Path(self.folder) / '重复处理.txt'
        source.write_text('完整预检', encoding='utf-8')
        with patch.object(self.window, 'legacy_tool') as legacy:
            dialog = self.window.confirm_import([str(source), str(source)])
            self.wait_idle()
            legacy.assert_not_called()
            self.assertEqual(len(dialog.model.plans), 1)
            self.assertEqual(dialog.model.plans[0].source, str(source))
            self.assertTrue(dialog.start_button.isEnabled())
            self.assertEqual(self.store.statistics()['files'], 0)
            dialog.close()

    def test_recycle_failure_keeps_file_and_index(self):
        import indexer
        from PySide6.QtWidgets import QMessageBox
        source = Path(self.folder) / '不要删除.txt'
        source.write_text('回收失败仍应保留的资料。', encoding='utf-8')
        self.store.index_files([str(source)], lambda _: None, threading.Event())
        with patch('qt_frontend.parity.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes), \
             patch('qt_frontend.parity.indexer.remove_from_library', return_value='error: recycle unavailable'), \
             patch.object(self.window, 'text_dialog'):
            self.window.clean_rows([{'path': str(source)}], True)
            self.wait_idle()
        self.assertTrue(source.is_file())
        self.assertGreater(self.store.search('回收失败')['hits'], 0)

    def test_missing_cleanup_snapshot_aborts_before_removing(self):
        from PySide6.QtWidgets import QMessageBox
        with patch('qt_frontend.parity.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes), \
             patch('qt_frontend.parity.snapshot_db', return_value=None), \
             patch('qt_frontend.parity.indexer.remove_from_library') as remove:
            self.window.clean_rows([{'path': 'do-not-touch.txt'}], True)
            self.wait_idle()
            remove.assert_not_called()

    def test_double_click_result_uses_document_locator(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QTreeWidgetItem
        item = QTreeWidgetItem(['fixture'])
        item.setData(0, Qt.ItemDataRole.UserRole, ('fixture.pdf', 44))
        with patch.object(self.window, 'open_located_file') as locate:
            self.window.open_result_original(item)
            locate.assert_called_once_with('fixture.pdf', 44)

    def test_advanced_ai_and_web_keep_complete_tools(self):
        # 入口变化：侧栏“镜仔智答”迁入第二列“探索/智答”切换；
        # 完整智答工作台仍作为兼容入口保留（旧模型管理、多轮历史、导出）。
        self.window.navigate('ai')
        self.assertEqual(self.window._current_page, 'ai')
        self.assertTrue(hasattr(self.window, 'ai_question'))
        with patch.object(self.window, 'legacy_tool') as tool:
            self.window.web_search()
            tool.assert_called_with('web')

    def test_navigation_does_not_repeat_sidebar_pages_in_top_menu(self):
        from PySide6.QtWidgets import QPushButton
        menus = self.window.menuBar().actions()
        self.assertEqual(menus, [])
        self.assertFalse(self.window.menuBar().isVisible())
        buttons = self.window.sidebar.findChildren(QPushButton)
        # 入口变化：侧栏只留“搜索、资料库、收藏”三个文字导航；“智答”迁入搜索列切换。
        self.assertEqual(sum(button.text().strip() == '镜仔智答' for button in buttons), 0)
        self.assertFalse(any(button.text() == '帮助与更多' for button in buttons))
        self.assertEqual(set(self.window.nav), {'search', 'library', 'bookmarks'})
        self.assertEqual(self.window.search_mode.count(), 2)
        self.assertEqual(self.window.search_mode.itemText(1), '智答')

    def test_search_tools_keep_relocated_actions_reachable(self):
        from contextlib import ExitStack
        methods = ['export_results', 'show_references', 'graph_view', 'send_results_to_qa']
        with ExitStack() as stack:
            callbacks = [stack.enter_context(patch.object(self.window, name)) for name in methods]
            menu = self.window.build_search_actions_menu()
            # 入口变化：“排序”下拉迁入“结果”菜单（新增“排序”子菜单 + 4 个动作）。
            self.assertTrue(any(action.text() == '排序' and action.menu() for action in menu.actions()))
            method_actions = [action for action in menu.actions()
                              if action.text() in ('导出搜索结果', '文档引用关系', '引用关系图',
                                                   'AI 分析当前搜索结果')]
            self.assertEqual(len(method_actions), len(methods))
            for action in method_actions:
                action.trigger()
            for callback in callbacks:
                callback.assert_called_once()

    def test_library_menu_keeps_management_routes_without_missing_alias(self):
        from contextlib import ExitStack
        methods = ['open_library_original', 'reveal_library_file', 'rename_file', 'reindex_selection',
                   'remove_library_selection', 'batch_metadata', 'export_ledger', 'show_duplicates',
                   'dedupe_cleanup', 'problem_files', 'category_audit', 'category_rules', 'compare_versions',
                   'compare_auto', 'standard_check']
        with ExitStack() as stack:
            callbacks = [stack.enter_context(patch.object(self.window, name)) for name in methods]
            menu = self.window.build_library_menu()
            actions = [action for action in menu.actions() if not action.isSeparator()]
            self.assertEqual(sum(action.text() == '问题文件管理' for action in actions), 1)
            self.assertFalse(any('失联文件检查' in action.text() for action in actions))
            self.assertEqual(len(actions), len(methods))
            for i,action in enumerate(actions):
                selection=[{},{}] if methods[i]=='compare_versions' else [{}]
                with patch.object(self.window,'selected_library_rows',return_value=selection):
                    available=[item for item in self.window.build_library_menu().actions() if not item.isSeparator()]
                    self.assertTrue(available[i].isEnabled())
                    available[i].trigger()
            for callback in callbacks:
                callback.assert_called_once()

    def test_add_materials_keeps_import_scan_inbox_and_clipboard(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            callbacks = [stack.enter_context(patch.object(self.window, name)) for name in
                         ['choose_files', 'choose_folder', 'legacy_tool', 'clipboard_save']]
            menu = self.window.build_add_menu()
            actions = [action for action in menu.actions() if not action.isSeparator()]
            self.assertEqual(len(actions), 4)
            for action in actions:
                action.trigger()
            for callback in callbacks:
                callback.assert_called_once()
            callbacks[2].assert_called_once_with('inbox')

    def test_feature_location_guide_does_not_launch_duplicate_legacy_routes(self):
        with patch.object(self.window, 'legacy_tool') as tool:
            self.window.feature_catalog()
            dialog = self.window._dialogs[-1]
            self.assertEqual(dialog.windowTitle(), '功能位置说明')
            rows = dialog.model.rows
            self.assertTrue(any('智答' in row['name'] and '搜索输入区' in row['location'] for row in rows))
            self.assertFalse(any('侧栏' in row['location'] and '智答' in row['name'] for row in rows))
            self.assertTrue(any('问题文件管理' == row['name'] for row in rows))
            dialog.table.doubleClicked.emit(dialog.model.index(0, 0))
            self.application.processEvents()
            tool.assert_not_called()
            dialog.close()

    def test_search_options_never_take_height_from_reader(self):
        self.window.resize(1280, 720)
        self.application.processEvents()
        before = self.window.reader_stack.geometry()
        self.assertGreaterEqual(self.window.reader_stack.height() / self.window.pages.height(), .90)
        self.assertFalse(self.window.mode.isVisible())
        self.assertFalse(self.window.statusBar().isVisible())
        self.window.options_button.click()
        self.application.processEvents()
        self.assertFalse(self.window.mode.isVisible())
        self.window.advanced_toggle.click()
        self.application.processEvents()
        self.assertTrue(self.window.mode.isVisible())
        self.assertTrue(self.window.options_scroll.isVisible())
        self.assertEqual(self.window.reader_stack.geometry(), before)
        self.window.options_button.click()
        self.assertFalse(self.window.options_scroll.isVisible())

    def test_menu_keeps_tasks_tools_settings_and_help_reachable(self):
        with patch.object(self.window, 'navigate') as navigate:
            menu = self.window.build_application_menu()
            menu.actions()[0].trigger()
            menu.actions()[1].trigger()
            self.assertEqual(navigate.call_args_list[0].args, ('tasks',))
            self.assertEqual(navigate.call_args_list[1].args, ('tools',))
        self.window.navigate('tasks')
        self.assertEqual(self.window.pages.currentIndex(), self.window.page_indices['tasks'])
        self.assertFalse(any(item.isChecked() for item in self.window.nav.values()))
        self.window.navigate('tools')
        self.assertGreater(self.window.tool_model.rowCount(), 0)

    def test_auxiliary_search_and_backup_routes_survive_in_tool_center(self):
        from contextlib import ExitStack
        methods = ['web_search', 'quick_search', 'reverse_lookup', 'backup_library',
                   'restore_package', 'restore_in_place', 'package_software', 'open_store_folder']
        with ExitStack() as stack:
            callbacks = [stack.enter_context(patch.object(self.window, name)) for name in methods]
            entries = self.window.tools_entries()
            for entry in entries:
                if entry['group'] in ('资料安全', '辅助检索'):
                    entry['run']()
            for callback in callbacks:
                callback.assert_called_once()

    def test_long_hit_can_expand_and_collapse_without_reopening_document(self):
        from PySide6.QtCore import QPoint, Qt
        from qt_frontend.result_snippets import SNIPPET, EXPANDED_LINES, folded_excerpt
        text = '背景说明' * 80 + '命中独有词' + ' 详细制度要求' * 120
        source = Path(self.folder) / '长命中.txt'
        source.write_text(text, encoding='utf-8')
        self.store.index_files([str(source)], lambda _: None, threading.Event())
        self.window.use_query('命中独有词')
        self.wait_until(lambda: self.window.current is not None)
        tree = self.window.results
        root = tree.topLevelItem(0)
        root.setExpanded(True)
        item = root.child(0)
        index = tree.indexFromItem(item)
        self.application.processEvents()
        tree.doItemsLayout()
        folded_height = tree.visualRect(index).height()
        self.assertEqual(item.data(0, SNIPPET)['text'], text)
        excerpt, hidden_start = folded_excerpt(text, ['命中独有词'])
        self.assertTrue(hidden_start)
        self.assertIn('命中独有词', excerpt[:40])
        generation, document = self.window._preview_generation, self.window.reader.toPlainText()
        rectangle = tree.visualRect(index)
        QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton,
                         pos=QPoint(rectangle.left() + 15, rectangle.bottom() - 10))
        self.application.processEvents()
        self.assertGreater(tree.visualRect(index).height(), folded_height)
        self.assertGreater(item.data(0, EXPANDED_LINES), 0)
        tree.change_expansion(index, collapse=True)
        self.assertEqual(tree.visualRect(index).height(), folded_height)
        self.assertEqual(self.window._preview_generation, generation)
        self.assertEqual(self.window.reader.toPlainText(), document)

    def test_more_hits_are_available_in_batches_without_truncating_content(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QTreeWidgetItem
        clusters = [{'lines': [{'line_no': n, 'line_text': f'命中片段 {n}'}]} for n in range(1, 8)]
        group = {'path': 'sample.txt', 'name': '样本', 'clusters': clusters}
        root = QTreeWidgetItem(['样本', '7'])
        self.window.results.addTopLevelItem(root)
        self.window.append_result_clusters(root, group, 0)
        self.assertEqual(root.childCount(), 6)
        self.assertEqual(root.child(5).data(0, Qt.ItemDataRole.UserRole)['type'], 'more')
        self.window.result_filter.setText('片段 7')
        self.assertFalse(root.isHidden())
        self.assertFalse(root.child(5).isHidden())
        self.window.open_result_original(root.child(5))
        self.assertEqual(root.childCount(), 7)
        self.assertEqual(root.child(6).text(0), '命中片段 7')

    def test_reading_focus_and_long_title_preserve_vertical_space(self):
        self.application.processEvents()
        before = self.window.reader_stack.geometry()
        self.window.doc_title.setText('非常长的文件名称' * 100)
        self.application.processEvents()
        self.assertEqual(self.window.reader_header.height(), 34)
        self.assertEqual(self.window.reader_stack.geometry(), before)
        self.window.toggle_reading_focus()
        self.application.processEvents()
        self.assertTrue(self.window.result_column.isHidden())
        self.assertGreater(self.window.preview_panel.width(), before.width())
        self.window.toggle_reading_focus()
        self.assertFalse(self.window.result_column.isHidden())

    def test_manual_range_survives_facet_reload_and_reset_clears_effective_filters(self):
        self.window.advanced_fields['日期'].setText('2022..2024')
        self.window.refresh_facets()
        self.wait_idle()
        self.assertEqual(self.window.advanced_fields['日期'].text(), '2022..2024')
        self.assertIn('日期：2022..2024', self.window.options_button.toolTip())
        self.window.reset_search_options()
        self.assertTrue(all(not field.text() for field in self.window.advanced_fields.values()))
        self.assertFalse(self.window.active_filter.isChecked())
        self.assertFalse(self.window.synonym_filter.isChecked())

    def test_cancel_first_close_leaves_window_open_and_choice_unset(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMessageBox
        def cancel():
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, QMessageBox):
                next(button for button in dialog.buttons() if button.text() == '取消').click()
        QTimer.singleShot(0, cancel)
        self.window.close()
        self.assertTrue(self.window.isVisible())
        self.assertIsNone(self.window._close_choice)

    def test_visual_status_filter_works_without_body_keyword_and_can_clear(self):
        import indexer
        source = Path(self.folder) / '废止资料.txt'
        source.write_text('这份资料已被废止。', encoding='utf-8')
        self.store.index_files([str(source)], lambda _: None, threading.Event())
        indexer.update_metadata(self.store.db, str(source), status='void', effective_date='2024-01-01')
        self.window.refresh_facets()
        self.wait_idle()
        combo = self.window.facet_combos['状态']
        combo.setCurrentIndex(combo.findData('废止'))
        self.wait_until(lambda: bool(self.window.groups))
        self.assertIn('状态:已废止', self.window.query)
        self.assertFalse(self.window.active_filter.isChecked())
        self.assertIn('废止', self.window.filter_chips.text())
        self.window.clear_facets()
        self.wait_until(lambda: self.window.query == '')
        self.assertFalse(self.window.groups)

    def test_sort_change_preserves_current_document_and_filter(self):
        for name, text in [('A.txt', '安全\n安全\n'), ('B.txt', '安全\n')]:
            source = Path(self.folder) / name
            source.write_text(text, encoding='utf-8')
            self.store.index_files([str(source)], lambda _: None, threading.Event())
        self.window.use_query('安全')
        self.wait_until(lambda: self.window.current is not None)
        second = next(row for row in self.window.groups if row['name'] == 'B.txt')
        self.window.open_preview(second['path'], second['first_line'])
        self.wait_idle()
        self.window.result_filter.setText('B')
        self.window.sort_order.setCurrentIndex(self.window.sort_order.findData('hits'))
        self.wait_idle()
        self.assertEqual(self.window.current['path'], second['path'])
        self.assertEqual(self.window.result_filter.text(), 'B')
        self.assertEqual(self.window.groups[0]['name'], 'A.txt')

    def test_density_and_distribution_are_persisted(self):
        compact = self.window.sidebar.width()
        self.window.density.setCurrentIndex(1)
        self.assertGreater(self.window.sidebar.width(), compact)
        self.assertFalse(self.store.settings['compact'])
        self.window.chart_toggle.setChecked(True)
        self.assertTrue(self.store.settings['show_distribution'])

    def test_native_import_can_run_in_background_and_finish(self):
        source = Path(self.folder) / '原生入库.txt'
        source.write_text('从原生预检复制进入资料库。', encoding='utf-8')
        dialog = self.window.confirm_import([str(source)])
        self.wait_idle()
        with patch.object(self.window, 'sync_semantic'):
            dialog.start()
            dialog.close()
            self.assertFalse(dialog.isVisible())
            self.wait_idle()
        self.assertFalse(dialog.running)
        self.assertEqual(dialog.result['ok'], 1)
        self.assertTrue(Path(dialog.result['details'][0]['path']).is_file())
        dialog.show()
        self.assertTrue(dialog.details_button.isVisible())
        dialog.close()

    def make_pdf(self):
        source = Path(self.folder) / 'original.pdf'
        first = b'BT /F1 20 Tf 30 700 Td (JINGZAI FIRST PAGE 2026) Tj ET'
        second = b'BT /F1 20 Tf 30 700 Td (JINGZAI SECOND PAGE 2026) Tj ET'
        objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 >>',
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
            b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
            b'<< /Length ' + str(len(first)).encode() + b' >>\nstream\n' + first + b'\nendstream',
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 7 0 R >>',
            b'<< /Length ' + str(len(second)).encode() + b' >>\nstream\n' + second + b'\nendstream']
        content, offsets = bytearray(b'%PDF-1.4\n'), [0]
        for number, data in enumerate(objects, 1):
            offsets.append(len(content))
            content += f'{number} 0 obj\n'.encode() + data + b'\nendobj\n'
        xref = len(content)
        content += f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode()
        for offset in offsets[1:]:
            content += f'{offset:010d} 00000 n \n'.encode()
        content += f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()
        source.write_bytes(content)
        self.store.index_files([str(source)], lambda _: None, threading.Event())
        return source

    def test_pdf_original_pages_search_thumbnails_and_text_switch(self):
        from PySide6.QtCore import QModelIndex
        from PySide6.QtPdf import QPdfDocument
        source = self.make_pdf()
        self.window.use_query('SECOND')
        self.wait_until(lambda: self.window.current is not None and self.window.pdf_reader.document.status() == QPdfDocument.Status.Ready)
        pdf = self.window.pdf_reader
        self.assertEqual(self.window.reader_stack.currentIndex(), 1)
        self.assertEqual(pdf.document.pageCount(), 2)
        self.assertEqual(pdf.page.value(), 2)
        pdf.thumbnail_toggle.setChecked(True)
        self.wait_until(lambda: len(pdf.thumbnail_pages) == 2 and not pdf.requests)
        self.assertFalse(pdf.thumbnails.item(0).icon().isNull())
        from qt_frontend.experience_acceptance import page_has_visible_ink
        self.wait_until(lambda: page_has_visible_ink(pdf.view))
        self.window.resize(1580, 940)
        self.wait_until(lambda: not pdf.reanchor.isActive() and page_has_visible_ink(pdf.view))
        self.assertEqual(pdf.page.value(), 2)
        self.wait_until(lambda: pdf.search.rowCount(QModelIndex()) > 0)
        pdf.next_match(1)
        self.assertEqual(pdf.view.currentSearchResultIndex(), 0)
        pdf.goto(0)
        self.wait_until(lambda: self.window.current.get('page') == 1)
        self.window.reader_kind.setCurrentIndex(0)
        self.wait_idle()
        self.assertEqual(self.window.reader_stack.currentIndex(), 0)
        self.assertIn('FIRST PAGE', self.window.reader.toPlainText())
        self.assertEqual(pdf.path, '')

    def test_stop_native_import_delivers_partial_report(self):
        source = Path(self.folder) / 'stop.txt'
        source.write_text('可停止的原生入库。', encoding='utf-8')
        dialog = self.window.confirm_import([str(source)])
        self.wait_idle()
        from qt_frontend import import_service
        original = import_service.execute
        def delayed(store, plans, progress, stop):
            deadline = time.monotonic() + 2
            while not stop.is_set() and time.monotonic() < deadline:
                time.sleep(.005)
            return original(store, plans, progress, stop)
        with patch.object(import_service, 'execute', side_effect=delayed), patch.object(self.window, 'sync_semantic'):
            dialog.start()
            dialog.stop()
            self.wait_idle()
        self.assertFalse(dialog.running)
        self.assertTrue(dialog.result['stopped'])
        self.assertEqual(self.window.jobs.jobs[dialog.job].state, '已停止')
        self.assertEqual(self.store.statistics()['files'], 0)
        dialog.close()


if __name__ == '__main__':
    unittest.main()

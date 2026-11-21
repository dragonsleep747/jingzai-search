"""全窗口评审发现的用户操作与状态恢复回归。"""
from contextlib import nullcontext
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests import test_discovery_integration_review as fixtures
if fixtures.QT:
    from PySide6.QtCore import QTimer,Qt
    from PySide6.QtTest import QTest,QSignalSpy
    from PySide6.QtWidgets import (QApplication,QDialog,QDialogButtonBox,QLabel,QLineEdit,
        QMenu,QPlainTextEdit,QPushButton,QTabWidget,QCheckBox)


@unittest.skipUnless(fixtures.QT,'requires Qt')
class TestUiRefinements(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixtures.TestUiReview.setUpClass.__func__(cls)
    def setUp(self):
        fixtures.TestUiReview.setUp(self)
        self.window.sync_semantic=lambda *a:None
        self.store.settings['ollama_base']='http://127.0.0.1:1'
    def tearDown(self):fixtures.TestUiReview.tearDown(self)
    wait=fixtures.TestUiReview.wait
    def idle(self):
        self.wait(lambda:not self.window.search_debounce.isActive())
        fixtures.TestUiReview.idle(self)
    def index(self,name,text):
        path=self.sample/name
        path.write_text(text,encoding='utf-8')
        self.store.index_files([str(path)],lambda _:None,threading.Event())
        return str(path)
    def inspect_modal(self,open_dialog,inspect):
        errors=[]
        def visit():
            dialog=QApplication.activeModalWidget()
            try:
                self.assertIsInstance(dialog,QDialog)
                inspect(dialog)
            except Exception as exc:errors.append(exc)
            finally:
                if isinstance(dialog,QDialog):dialog.reject()
        QTimer.singleShot(120,visit)
        open_dialog()
        if errors:raise errors[0]

    def test_missing_evidence_is_not_copyable_as_an_answer(self):
        path=self.index('上次阅读.txt','第一条 阅读内容。')
        self.window.open_preview(path);self.idle()
        self.window.search_mode.setCurrentIndex(1)
        self.window.search_input.setText('本轮没有依据的问题')
        with patch('ai_qa.chat_stream') as stream:
            self.window.ask_ai();self.idle();stream.assert_not_called()
        self.assertEqual(self.window.ai_answer.toPlainText(),'')
        self.assertFalse(self.window.ai_copy.isEnabled())
        self.assertTrue(self.window.ai_notice.isVisible())
        self.assertIn('未作为本轮',self.window.reader_context.text())
        self.assertTrue(self.window.ai_library_recovery.isVisible())

    def test_partial_answer_stays_copyable_with_separate_failure_notice(self):
        def stream(*a,**kw):
            yield '已生成的部分内容'
            raise RuntimeError('连接中断')
        self.window.search_mode.setCurrentIndex(1)
        self.window.ai_kind.setCurrentIndex(1)
        self.window.search_input.setText('测试部分生成')
        with patch('ai_qa.chat_stream',side_effect=stream):self.window.ask_ai();self.idle()
        self.assertEqual(self.window.ai_answer.toPlainText(),'已生成的部分内容')
        self.assertTrue(self.window.ai_copy.isEnabled())
        self.assertIn('连接中断',self.window.ai_notice.text())
        self.assertIn('部分生成',self.window.ai_status.text())

    def test_completed_markdown_renders_tables_and_headings(self):
        self.window.search_mode.setCurrentIndex(1)
        self.window.ai_kind.setCurrentIndex(1)
        self.window.search_input.setText('排版')
        with patch('ai_qa.chat_stream',return_value=iter(['## 结论\n\n|项目|结果|\n|---|---|\n|核对|完成|'])):
            self.window.ask_ai();self.idle()
        self.assertIn('<table',self.window.ai_answer.toHtml())
        self.assertNotIn('##',self.window.ai_answer.toPlainText())
        self.assertEqual(len(self.window._ai_history),2)

    def test_multiline_prompt_preserves_newline_and_enter_submits(self):
        self.window.search_mode.setCurrentIndex(1)
        entry=self.window.ai_input
        entry.submitted.disconnect(self.window.start_search)
        spy=QSignalSpy(entry.submitted)
        entry.setPlainText('第一行')
        entry.moveCursor(entry.textCursor().MoveOperation.End)
        QTest.keyClick(entry,Qt.Key.Key_Return,Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.window.search_input.text(),'第一行\n')
        self.assertGreater(entry.height(),28)
        QTest.keyClick(entry,Qt.Key.Key_Return)
        self.assertEqual(spy.count(),1)

    def test_visible_filter_chips_can_remove_a_hidden_condition(self):
        self.window.advanced_fields['文号'].setText('测试文号')
        self.assertFalse(self.window.options_scroll.isVisible())
        self.assertTrue(self.window.filter_chips.isVisible())
        self.assertIn('测试文号',self.window.filter_chips.text())
        self.window.remove_facet('文号');self.idle()
        self.assertEqual(self.window.advanced_fields['文号'].text(),'')

    def test_results_context_uses_the_clicked_file_not_old_preview(self):
        first=self.index('旧预览.txt','旧资料')
        second=self.index('右键目标.txt','唯一右键正文')
        self.window.use_query('唯一右键正文');self.idle()
        self.window.open_preview(first);self.idle()
        tree=self.window.discovery_results
        item=tree.topLevelItem(0)
        captured=[]
        errors=[]
        def drive():
            menu=QApplication.activePopupWidget()
            try:
                self.assertIsInstance(menu,QMenu)
                labels=[action.text() for action in menu.actions()]
                self.assertIn('复制条款引用',labels)
                next(action for action in menu.actions() if action.text()=='复制路径').trigger()
                captured.append(QApplication.clipboard().text())
            except Exception as exc:errors.append(exc)
            finally:
                if menu:menu.close()
        previous=QApplication.clipboard().text()
        try:
            QTimer.singleShot(150,drive)
            self.window.discovery_context_menu(tree.visualItemRect(item).center())
            if errors:raise errors[0]
            self.assertEqual(captured,[second])
        finally:QApplication.clipboard().setText(previous)

    def test_import_complete_button_closes_without_reimporting(self):
        path=self.sample/'独立导入样本.txt'
        path.write_text('独立导入'+str(time.time_ns()),encoding='utf-8')
        dialog=self.window.confirm_import([str(path)]);self.idle()
        dialog.start();self.idle()
        self.assertEqual(dialog.result['ok'],1)
        self.assertEqual(dialog.start_button.text(),'完成')
        self.assertTrue(dialog.start_button.isEnabled())
        self.assertTrue(dialog.model.locked)
        finished=QSignalSpy(dialog.finished)
        dialog.start()
        self.assertEqual(finished.count(),1)
        self.assertEqual(self.store.statistics()['files'],1)

    def test_settings_cancel_preserves_values_and_has_groups(self):
        original=self.store.settings.get('dark',False)
        def inspect(dialog):
            tabs=dialog.findChild(QTabWidget)
            self.assertEqual([tabs.tabText(i) for i in range(tabs.count())],['外观与阅读','搜索与索引','智答','资料安全','启动与唤起','快捷键'])
            dialog.findChild(QCheckBox,'dark').setChecked(not original)
        self.inspect_modal(self.window.settings_dialog,inspect)
        self.assertEqual(self.store.settings.get('dark',False),original)

    def test_invalid_model_address_does_not_save_settings(self):
        original=self.store.settings.get('ollama_base')
        def inspect(dialog):
            dialog.findChild(QLineEdit,'ollama_base').setText('not-a-service')
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
            self.assertTrue(dialog.isVisible())
        self.inspect_modal(self.window.settings_dialog,inspect)
        self.assertEqual(self.store.settings.get('ollama_base'),original)

    def test_task_failure_shows_actionable_summary_before_traceback(self):
        job=SimpleNamespace(title='读取文件',state='失败',result=None,progress={},started=time.time(),key='done',
            error='Traceback (most recent call last):\nRuntimeError: 原文件已移动')
        dialog=self.window.task_report(job)
        labels=' '.join(w.text() for w in dialog.findChildren(QLabel))
        self.assertIn('原文件已移动',labels)
        self.assertNotIn('Traceback',labels)
        self.assertTrue(dialog.findChild(QPlainTextEdit).isHidden())
        dialog.close()

    def test_chat_expands_answer_and_explore_restores_reader(self):
        before=self.window.splitter.sizes()
        self.window.search_mode.setCurrentIndex(1)
        self.window.ai_kind.setCurrentIndex(1)
        self.assertTrue(self.window.preview_panel.isHidden())
        self.window.search_mode.setCurrentIndex(0);self.idle()
        self.assertFalse(self.window.preview_panel.isHidden())
        after=self.window.splitter.sizes()
        self.assertLessEqual(abs(before[0]-after[0]),3)

    def test_comparison_lists_changes_and_navigates_both_versions(self):
        first=self.index('旧版.txt','第一条 原要求\n第二条 共同内容')
        second=self.index('新版.txt','第一条 新要求\n第二条 共同内容\n第三条 新增要求')
        self.window.compare_pair({'path':first,'name':'旧版.txt'},{'path':second,'name':'新版.txt'});self.idle()
        dialog=next(w for w in QApplication.topLevelWidgets() if isinstance(w,QDialog) and w.isVisible() and w.windowTitle()=='版本对比')
        next(w for w in dialog.findChildren(QPushButton) if w.text()=='下一处差异').click()
        self.assertTrue(any('差异 1/' in w.text() for w in dialog.findChildren(QLabel)))
        self.assertTrue(any(w.text()=='同步滚动' and w.isChecked() for w in dialog.findChildren(QCheckBox)))
        dialog.close()

    def test_bookmark_undo_restores_note_and_original_time(self):
        import indexer
        path=self.index('收藏撤销.txt','第一条 收藏条款')
        indexer.add_bookmark(self.store.db,path,1,'条款标题')
        original=indexer.list_bookmarks(self.store.db)[0]
        indexer.set_bookmark_note(self.store.db,original['id'],'需要保留的多行\n备注')
        self.window.navigate('bookmarks');self.idle()
        self.window.bookmarks_table.selectRow(0)
        self.window.remove_bookmark();self.idle()
        self.assertEqual(indexer.list_bookmarks(self.store.db),[])
        self.window.undo_bookmark_removal();self.idle()
        restored=indexer.list_bookmarks(self.store.db)[0]
        self.assertEqual(restored['note'],'需要保留的多行\n备注')
        self.assertEqual(restored['created_at'],original['created_at'])

    def test_directory_format_filter_applies_to_file_name_results(self):
        from qt_frontend.experience_acceptance import make_pdf
        (self.sample/'格式样本.txt').write_text('普通文本',encoding='utf-8')
        pdf=self.sample/'格式样本.pdf'
        make_pdf(pdf)
        self.window.discovery.start_scan(self.sid)
        self.window.discovery_scope.setCurrentIndex(0)
        self.window.type_filter.setCurrentIndex(self.window.type_filter.findData('pdf'))
        self.window.use_query('格式样本');self.idle()
        self.assertEqual(self.window.discovery_results.topLevelItemCount(),1)
        hit=self.window.discovery_results.topLevelItem(0).data(0,Qt.ItemDataRole.UserRole)
        self.assertEqual(hit['path'],str(pdf))

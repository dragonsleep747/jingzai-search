"""Actual mouse/keyboard regressions for result width, folding and citations."""
import os
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
try:
    from PySide6.QtCore import QPoint,Qt,QUrl
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication,QTreeWidgetItem
    from qt_frontend.backend import preview_html
    from qt_frontend.result_snippets import SNIPPET
    from tests import test_discovery_integration_review as fixtures
    QT=True
except ImportError:
    QT=False


@unittest.skipUnless(QT,'run with .venv-qt')
class TestReadingInteractions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        fixtures.TestUiReview.setUp(self)

    def tearDown(self):
        fixtures.TestUiReview.tearDown(self)

    def wait(self,*args,**kwargs):
        return fixtures.TestUiReview.wait(self,*args,**kwargs)

    def idle(self):
        fixtures.TestUiReview.idle(self)

    def index(self,name,text):
        path=self.sample/name
        path.write_text(text,encoding='utf-8')
        self.store.index_files([str(path)],lambda _:None,threading.Event())
        return str(path)

    def search(self,text):
        self.window.use_query(text)
        self.idle()
        self.app.processEvents()
        tree=self.window.discovery_results
        tree.doItemsLayout()
        self.app.processEvents()
        return tree

    def title_point(self,tree,item):
        rectangle=tree.visualItemRect(item)
        return QPoint(rectangle.left()+35,rectangle.center().y())

    def anchor_point(self,contains=None):
        reader=self.window.reader
        for y in range(1,reader.viewport().height(),4):
            for x in range(1,reader.viewport().width(),4):
                point=QPoint(x,y)
                href=reader.anchorAt(point)
                if href.startswith('jingzai-preview:') and (contains is None or contains in href):
                    return point
        self.fail('No rendered citation anchor found')

    def test_snippet_uses_the_result_viewport_instead_of_half_a_column(self):
        self.index('宽度.txt','横向排版测试'*80)
        tree=self.search('横向')
        root=tree.topLevelItem(0)
        child=root.child(0)
        index=tree.indexFromItem(child)
        rectangle=tree.visualRect(index)
        self.assertFalse(tree.header().stretchLastSection())
        self.assertEqual(tree.columnWidth(1),56)
        self.assertTrue(child.isFirstColumnSpanned())
        self.assertGreater(rectangle.width(),tree.viewport().width()*.88)
        self.assertLess(abs(tree.snippet_delegate.row_width(index)-rectangle.width()),4)
        layout,*_=tree.snippet_delegate.layout_for(index,rectangle.width())
        self.assertGreater(layout.lineAt(0).naturalTextWidth(),(rectangle.width()-20)*.85)

    def test_dragging_result_column_reflows_the_same_text(self):
        self.index('宽度.txt','横向排版测试'*80)
        tree=self.search('横向')
        child=tree.topLevelItem(0).child(0)
        index=tree.indexFromItem(child)
        self.window.splitter.setSizes([300,900])
        self.app.processEvents();tree.doItemsLayout()
        narrow=tree.visualRect(index).width()
        layout,*_=tree.snippet_delegate.layout_for(index,narrow)
        before=layout.lineAt(0).textLength()
        self.window.splitter.setSizes([560,640])
        self.app.processEvents();tree.doItemsLayout()
        wide=tree.visualRect(index).width()
        layout,*_=tree.snippet_delegate.layout_for(index,wide)
        self.assertGreater(wide,narrow+100)
        self.assertGreater(layout.lineAt(0).textLength(),before)

    def test_single_title_click_collapses_and_expands_without_reloading_body(self):
        path=self.index('标题点击.txt','标题测试正文\n第二行内容')
        tree=self.search('标题测试')
        root=tree.topLevelItem(0)
        generation=self.window._preview_generation
        self.assertTrue(root.isExpanded())
        QTest.mouseClick(tree.viewport(),Qt.MouseButton.LeftButton,pos=self.title_point(tree,root))
        self.assertFalse(root.isExpanded())
        QTest.mouseClick(tree.viewport(),Qt.MouseButton.LeftButton,pos=self.title_point(tree,root))
        self.assertTrue(root.isExpanded())
        self.assertEqual(self.window._preview_generation,generation)
        self.assertEqual(self.window.current['path'],path)

    def test_clicking_another_file_header_selects_and_previews_it(self):
        self.index('A测试.txt','共同命中 甲')
        second=self.index('B测试.txt','共同命中 乙')
        tree=self.search('共同命中')
        root=tree.topLevelItem(1)
        QTest.mouseClick(tree.viewport(),Qt.MouseButton.LeftButton,pos=self.title_point(tree,root))
        self.idle()
        self.assertEqual(self.window.current['path'],second)
        self.assertTrue(root.isExpanded())

    def test_new_query_in_same_file_updates_body_highlight_and_location(self):
        self.index('同一文件.txt','第一条 初次词\n不相关内容\n第三条 新查询词')
        self.search('初次词')
        self.search('新查询词')
        self.assertEqual(self.window.current['query'],'新查询词')
        self.assertEqual(self.window.current['line'],3)

    def test_branch_arrow_changes_expansion_only_once(self):
        self.index('箭头.txt','箭头测试')
        tree=self.search('箭头测试')
        root=tree.topLevelItem(0)
        rectangle=tree.visualItemRect(root)
        QTest.mouseClick(tree.viewport(),Qt.MouseButton.LeftButton,
            pos=QPoint(max(1,rectangle.left()-6),rectangle.center().y()))
        self.assertFalse(root.isExpanded())

    def test_more_hits_replace_the_sentinel_without_duplication(self):
        tree=self.window.discovery_results
        self.window.result_stack.setCurrentWidget(tree)
        root=QTreeWidgetItem(['分批命中','12'])
        root.setData(0,Qt.ItemDataRole.UserRole,{'path':'test-only','kind':'content'})
        tree.addTopLevelItem(root)
        group={'path':'test-only','name':'test-only','clusters':[{'lines':[{'line_no':i,'line_text':f'分批命中 {i}'}]} for i in range(1,13)]}
        self.window.append_result_clusters(root,group,0)
        root.setExpanded(True)
        more=root.child(root.childCount()-1)
        tree.scrollToItem(more)
        self.app.processEvents()
        generation=self.window._preview_generation
        QTest.mouseClick(tree.viewport(),Qt.MouseButton.LeftButton,pos=tree.visualItemRect(more).center())
        self.assertEqual(root.childCount(),11)
        self.assertEqual(sum(tree.is_more(root.child(i)) for i in range(root.childCount())),1)
        self.assertEqual(self.window._preview_generation,generation)

    def test_click_current_document_title_reads_inside_app(self):
        path=self.index('当前管理办法.txt','关于《当前管理办法》的通知')
        self.window.open_preview(path)
        self.idle()
        with patch.object(self.window,'open_located_file') as open_file:
            QTest.mouseClick(self.window.reader.viewport(),Qt.MouseButton.LeftButton,pos=self.anchor_point())
            self.idle()
            open_file.assert_not_called()
            self.assertEqual(self.window.current['path'],path)

    def test_other_reference_click_navigates_to_its_clause_and_can_go_back(self):
        target=self.index('引用管理办法.txt','第一条 起始内容\n第二条 说明\n第三条 目标条款')
        source=self.index('引用说明.txt','请参照《引用管理办法》第三条执行。')
        self.window.open_preview(source)
        self.idle()
        QTest.mouseClick(self.window.reader.viewport(),Qt.MouseButton.LeftButton,pos=self.anchor_point())
        self.idle()
        self.assertEqual(self.window.current['path'],target)
        self.assertEqual(self.window.current['line'],3)
        self.window.go_back()
        self.idle()
        self.assertEqual(self.window.current['path'],source)

    def test_control_click_reference_opens_original_without_replacing_reader(self):
        target=self.index('Ctrl目标.txt','第一条 目标内容')
        source=self.index('Ctrl来源.txt','参见《Ctrl目标》第一条。')
        self.window.open_preview(source)
        self.idle()
        with patch.object(self.window,'open_located_file') as open_file:
            QTest.mouseClick(self.window.reader.viewport(),Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.ControlModifier,pos=self.anchor_point())
            open_file.assert_called_once_with(target,1)
        self.assertEqual(self.window.current['path'],source)

    def test_same_reference_can_target_different_clauses_in_one_paragraph(self):
        target=self.index('多条款目标.txt','第一条 起始\n第二条 中间\n第三条 结尾')
        source=self.index('多条款来源.txt','参见《多条款目标》第一条，以及《多条款目标》第三条。')
        payload=self.store.preview(source,1)
        self.assertEqual(payload['reference_locations']['多条款目标']['第一条']['line'],1)
        self.assertEqual(payload['reference_locations']['多条款目标']['第三条']['line'],3)
        rendered=preview_html(payload)
        self.assertIn('&amp;line=1',rendered)
        self.assertIn('&amp;line=3',rendered)
        self.window.open_preview(source)
        self.idle()
        QTest.mouseClick(self.window.reader.viewport(),Qt.MouseButton.LeftButton,pos=self.anchor_point('line=3'))
        self.idle()
        self.assertEqual((self.window.current['path'],self.window.current['line']),(target,3))

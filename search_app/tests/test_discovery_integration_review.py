"""Independent acceptance regressions for failures found in the DSH candidate."""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import closing

from discovery.scheduler import DiscoveryController
from discovery.model import SearchRequest, SourceInfo
from discovery.native_scan import NativeIndexBuilder
from discovery.links import hardlink_paths
from tests import test_discovery_native as fixtures


class TestControllerReview(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='jingzai-controller-review-')
        self.root = Path(self.temp.name)
        self.control = DiscoveryController(str(self.root/'catalog.db'),str(self.root/'discovery.json'))
        self.sample = self.root/'sample'
        self.sample.mkdir()
        (self.sample/'target.txt').write_text('original',encoding='utf-8')
        self.sid = self.control.add_directory(str(self.sample))

    def tearDown(self):
        self.control.shutdown()
        self.temp.cleanup()

    def test_unchecked_sources_and_empty_selection_do_not_search_all_catalog(self):
        self.control.start_scan(self.sid)
        self.assertTrue(self.control.search(SearchRequest('target')).results)
        self.control.select(self.sid,False)
        self.assertEqual(self.control.search(SearchRequest('target')).results,[])
        self.assertEqual(self.control.get_coverage()['sources'],[])
        self.assertTrue((self.sample/'target.txt').exists())

    def test_requested_sources_intersect_user_selection(self):
        self.control.start_scan(self.sid)
        self.assertEqual(self.control.search(SearchRequest('target',source_ids=['unselected'])).results,[])

    def test_async_scan_finishes_without_cross_thread_rlock_deadlock(self):
        result=[]
        worker=self.control.start_scan_async(self.sid,callback=result.append)
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0]['state'],'complete')
        self.assertFalse(self.control.is_running(self.sid))

    def test_direct_job_can_be_cancelled_and_duplicate_is_not_queued(self):
        started=threading.Event()
        def scan(sid,progress,stop,full_native=True):
            started.set()
            while not stop.is_set():
                time.sleep(.01)
            return {'state':'partial','stopped':True}
        with patch.object(self.control,'_scan',side_effect=scan):
            thread=self.control.start_scan_async(self.sid)
            self.assertTrue(started.wait(2))
            self.assertTrue(self.control.start_scan(self.sid)['busy'])
            self.control.cancel(self.sid)
            thread.join(2)
            self.assertFalse(thread.is_alive())

    def test_waiting_concurrency_slot_is_cancellable(self):
        result=[]
        self.control._semaphore.acquire()
        self.control._semaphore.acquire()
        try:
            worker=self.control.start_scan_async(self.sid,callback=result.append)
            self.control.cancel(self.sid)
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertTrue(result[0]['stopped'])
        finally:
            self.control._semaphore.release()
            self.control._semaphore.release()

    def test_remove_fences_running_scan_and_preserves_original(self):
        self.control.start_scan(self.sid)
        started=threading.Event()
        def scan(sid,progress,stop,full_native=True):
            started.set()
            while not stop.is_set():
                time.sleep(.01)
            return {'state':'partial','stopped':True}
        with patch.object(self.control,'_scan',side_effect=scan):
            worker=self.control.start_scan_async(self.sid)
            self.assertTrue(started.wait(2))
            self.control.remove_range(self.sid)
            worker.join(2)
        self.assertIsNone(self.control.catalog.get_source(self.sid))
        self.assertEqual(self.control.catalog.count_entries(self.sid),0)
        self.assertTrue((self.sample/'target.txt').exists())

    def test_persistent_watch_updates_after_initial_scan_returns(self):
        self.control.start_scan(self.sid)
        self.assertTrue(self.control._watchers[self.sid].is_running())
        added=self.sample/'later.txt'
        added.write_text('created after scan',encoding='utf-8')
        deadline=time.monotonic()+4
        while time.monotonic()<deadline and not self.control.search(SearchRequest('later')).results:
            time.sleep(.03)
        self.assertTrue(self.control.search(SearchRequest('later')).results)
        added.unlink()
        while time.monotonic()<deadline and self.control.search(SearchRequest('later')).results:
            time.sleep(.03)
        self.assertFalse(self.control.search(SearchRequest('later')).results)

    def test_reopening_marks_closed_time_gap_for_reconciliation(self):
        self.control.start_scan(self.sid)
        self.control.shutdown()
        (self.sample/'target.txt').rename(self.sample/'renamed.txt')
        self.control=DiscoveryController(str(self.root/'catalog.db'),str(self.root/'discovery.json'))
        self.control.resume_watchers()
        self.assertTrue(self.control.catalog.get_source(self.sid)['needs_rescan'])
        self.control.sync(self.sid)
        self.assertFalse(self.control.search(SearchRequest('target')).results)
        self.assertTrue(self.control.search(SearchRequest('renamed')).results)

    @unittest.skipUnless(os.name=='nt','Windows hardlink API')
    def test_real_native_hardlink_names_preserve_both_paths(self):
        original=self.sample/'target.txt'
        alias=self.sample/'alias.txt'
        os.link(original,alias)
        paths={os.path.normcase(p) for p in hardlink_paths(str(original))}
        self.assertIn(os.path.normcase(str(original)),paths)
        self.assertIn(os.path.normcase(str(alias)),paths)


class TestNativeReview(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='jingzai-native-review-')
        from discovery.catalog import Catalog
        self.catalog=Catalog(str(Path(self.temp.name)/'catalog.db'))
        self.source=SourceInfo('synthetic','volume','F:\\',volume_serial=0x1234,filesystem='NTFS')
        self.catalog.register_source(self.source)

    def tearDown(self):
        self.temp.cleanup()

    def run_records(self,records,**kwargs):
        journal=kwargs.pop('journal',fixtures._journal())
        with patch('discovery.native_scan._root_id',return_value=5), \
             patch('discovery.native_scan._metadata',side_effect=lambda path,ref:
                 SimpleNamespace(st_size=None,st_mtime_ns=None,st_nlink=1,st_ino=ref,st_dev=0x1234)), \
             patch.multiple('discovery.native_scan.usn',open_volume_handle=lambda root:1,
                 close_handle=lambda h:None,query_usn_journal=lambda h:journal,
                 enum_usn_chunk=fixtures._fake_enum(records),parse_usn_buffer=fixtures._fake_parse(records)):
            return NativeIndexBuilder(self.catalog,self.source).build(**kwargs)

    def test_actual_root_seed_works_when_enum_omits_root_record(self):
        report=self.run_records(fixtures._records()[1:])
        self.assertEqual(report['state'],'complete',report)
        roots=self.catalog.search(SearchRequest('',scope='path')).results
        self.assertIn('F:\\',{hit['path'] for hit in roots})
        self.assertNotIn('F:',{hit['path'] for hit in roots})

    def test_reparse_descendants_and_excluded_system_subtree_are_not_indexed(self):
        records=fixtures._records()
        records.extend([
            dict(records[2],file_ref=111,parent_ref=103,name='through-link.txt'),
            dict(records[1],file_ref=112,parent_ref=5,name='Windows'),
            dict(records[2],file_ref=113,parent_ref=112,name='system-file.txt')])
        report=self.run_records(records)
        self.assertEqual(report['state'],'complete',report)
        self.assertFalse(self.catalog.search(SearchRequest('through-link')).results)
        self.assertFalse(self.catalog.search(SearchRequest('system-file')).results)

    def test_v4_capability_does_not_falsely_reject_valid_v2_snapshot(self):
        report=self.run_records(fixtures._records(),journal=fixtures._journal(max_supported_major=4))
        self.assertEqual(report['state'],'complete',report)

    def test_cancel_during_writes_keeps_previous_generation_and_checkpoint(self):
        from discovery.scan import _entry_row
        old=Path(self.temp.name)/'previous.txt'
        old.write_text('keep',encoding='utf-8')
        self.catalog.upsert_entries([_entry_row(self.source.source_id,str(old),old.name,'file',old.stat(),False,0)])
        stop=threading.Event()
        original=self.catalog.upsert_entries
        def put(rows):
            result=original(rows)
            stop.set()
            return result
        records=fixtures._records()[:2]+[dict(fixtures._records()[2],file_ref=1000+i,name=f'file-{i}.txt') for i in range(1100)]
        with patch.object(self.catalog,'upsert_entries',side_effect=put):
            report=self.run_records(records,cancel=stop.is_set)
        self.assertTrue(report['stopped'])
        self.assertIsNone(self.catalog.get_checkpoint(self.source.source_id))
        self.assertTrue(self.catalog.search(SearchRequest('previous')).results)

    def test_atomic_publish_rolls_back_pruning_when_checkpoint_write_fails(self):
        from discovery.scan import _entry_row
        old=Path(self.temp.name)/'previous.txt'
        old.write_text('keep',encoding='utf-8')
        self.catalog.upsert_entries([_entry_row(self.source.source_id,str(old),old.name,'file',old.stat(),False,0)])
        with self.catalog._conn() as conn:
            conn.execute("CREATE TRIGGER fail_checkpoint BEFORE INSERT ON checkpoints BEGIN SELECT RAISE(ABORT,'injected'); END")
            conn.commit()
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.run_records(fixtures._records())
        self.assertTrue(self.catalog.search(SearchRequest('previous')).results)
        self.assertIsNone(self.catalog.get_checkpoint(self.source.source_id))

    def test_bounded_and_unknown_records_never_publish_checkpoint(self):
        report=self.run_records(fixtures._records(),limit_records=2)
        self.assertEqual(report['state'],'partial')
        self.assertIsNone(self.catalog.get_checkpoint(self.source.source_id))

    def test_omitted_ntfs_extend_parent_is_excluded_metadata_not_a_user_orphan(self):
        records=fixtures._records()+[dict(fixtures._records()[1],file_ref=27,parent_ref=11,name='$RmMetadata'),
            dict(fixtures._records()[2],file_ref=32,parent_ref=27,name='internal-log')]
        report=self.run_records(records)
        self.assertEqual(report['state'],'complete',report)
        self.assertEqual(report['excluded'],2)
        self.assertFalse(self.catalog.search(SearchRequest('internal-log')).results)

    def test_ordinary_missing_parent_can_be_resolved_by_its_real_file_identity(self):
        records=fixtures._records()+[dict(fixtures._records()[2],file_ref=222,parent_ref=201,name='recovered.txt')]
        with patch('discovery.native_scan.path_for_file_id',return_value='F:\\recovered-parent'):
            report=self.run_records(records)
        self.assertEqual(report['state'],'complete',report)
        hits=self.catalog.search(SearchRequest('recovered.txt')).results
        self.assertEqual(hits[0]['path'],'F:\\recovered-parent\\recovered.txt')

    def test_usn_read_can_request_v4_for_explicit_gap_detection(self):
        from discovery import usn
        requested=[]
        def ioctl(handle,code,request,output):
            requested.append(request.MaxMajorVersion)
            for i,value in enumerate((100).to_bytes(8,'little')):
                output[i]=value
            return 8
        with patch.object(usn,'_require_windows'),patch.object(usn,'_device_io_control',side_effect=ioctl):
            raw,cursor=usn.read_usn_journal_chunk(1,0,7,max_major_version=4)
        self.assertEqual(requested,[4])
        self.assertEqual(cursor,100)


try:
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from qt_frontend.backend import Store
    from qt_frontend.window import Window
    QT=True
except ImportError:
    QT=False


@unittest.skipUnless(QT,'run with .venv-qt')
class TestUiReview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='jingzai-ui-review-')
        self.root=Path(self.temp.name)
        self.store=Store(self.root)
        self.store.settings.update(max_backups=0,qt_startup_refresh=False)
        self.window=Window(self.store)
        self.window.show()
        self.wait(lambda:not self.window.jobs.active)
        self.sample=self.root/'sample'
        self.sample.mkdir()
        self.sid=self.window.discovery.add_directory(str(self.sample))

    def wait(self,condition,timeout=6):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            self.app.processEvents()
            if condition():
                return
            QTest.qWait(10)
        self.assertTrue(condition())

    def idle(self):
        self.wait(lambda:not self.window.jobs.active)

    def tearDown(self):
        self.idle()
        self.window._force_exit=True
        self.window._closing_requested=True
        self.window.close()
        self.wait(lambda:not self.window.isVisible())
        self.idle()
        self.window.jobs.pool.waitForDone(5000)
        self.window.deleteLater()
        from PySide6.QtCore import QCoreApplication,QEvent
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        # The legacy engines intentionally cache per-thread read connections.
        # Release this fixture's main-thread cache; worker caches die with the pool.
        import indexer,searcher
        for local in (indexer._ro_tls,searcher._conn_tls):
            connections=getattr(local,'conns',{})
            connection=connections.pop(os.path.abspath(self.store.db),None)
            if connection:
                connection.close()
        self.temp.cleanup()

    def test_both_scope_shows_content_only_matches_and_preserves_snippets(self):
        path=self.sample/'document.txt'
        path.write_text('第一条 独特正文关键字\n第二条 其他内容',encoding='utf-8')
        self.store.index_files([str(path)],lambda _:None,threading.Event())
        self.window.discovery.start_scan(self.sid)
        self.window.use_query('独特正文')
        self.idle()
        tree=self.window.discovery_results
        self.assertEqual(tree.topLevelItemCount(),1)
        item=tree.topLevelItem(0)
        self.assertEqual(item.data(0,Qt.ItemDataRole.UserRole)['kind'],'content')
        self.assertGreater(item.childCount(),0)
        tree.setCurrentItem(item)
        self.idle()
        self.assertEqual(self.window.current['path'],str(path))

    def test_directory_pagination_does_not_silently_drop_after_100(self):
        for i in range(205):
            (self.sample/f'page-target-{i:03d}.txt').write_text('sample',encoding='utf-8')
        self.window.discovery.start_scan(self.sid)
        self.window.discovery_scope.setCurrentIndex(0)
        self.window.use_query('page-target')
        self.idle()
        self.assertEqual(self.window.discovery_results.topLevelItemCount(),100)
        self.assertTrue(self.window.discovery_more.isVisible())
        self.window.load_more_discovery()
        self.idle()
        self.window.load_more_discovery()
        self.idle()
        tree=self.window.discovery_results
        self.assertEqual(tree.topLevelItemCount(),205)
        self.assertEqual(len({tree.topLevelItem(i).data(0,Qt.ItemDataRole.UserRole)['path'] for i in range(205)}),205)
        self.assertFalse(self.window.discovery_more.isVisible())

    def test_on_demand_text_preview_does_not_import_into_content_library(self):
        path=self.sample/'unindexed.txt'
        path.write_text('正文预览不能自动入库',encoding='utf-8')
        self.window.preview_discovery_file({'path':str(path),'name':path.name,'kind':'file','online':True})
        self.idle()
        self.assertIn('正文预览不能自动入库',self.window.discovery_preview.info.toPlainText())
        import sqlite3
        with closing(sqlite3.connect(self.store.db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM documents').fetchone()[0],0)

    def test_result_menu_can_be_reopened_without_destroying_sort_controls(self):
        from shiboken6 import isValid
        for _ in range(3):
            menu=self.window.build_search_actions_menu()
            menu.deleteLater()
            self.app.sendPostedEvents(None,0)
            self.app.processEvents()
        self.assertTrue(isValid(self.window.sort_order))
        self.assertTrue(isValid(self.window.catalog_sort))

    def test_ai_is_embedded_and_source_keeps_right_preview(self):
        path=self.sample/'source.txt'
        path.write_text('资料正文',encoding='utf-8')
        self.store.index_files([str(path)],lambda _:None,threading.Event())
        self.window.search_mode.setCurrentIndex(1)
        self.assertTrue(self.window.result_column.isVisible())
        self.assertEqual(self.window.pages.currentIndex(),self.window.page_indices['search'])
        from PySide6.QtWidgets import QListWidgetItem
        item=QListWidgetItem('source')
        item.setData(Qt.ItemDataRole.UserRole,{'path':str(path),'line_no':1})
        self.window._open_ai_source(item)
        self.idle()
        self.assertEqual(self.window.search_mode.currentIndex(),1)
        self.assertEqual(self.window.current['path'],str(path))

    def test_typing_question_does_not_send_until_enter(self):
        self.window.search_mode.setCurrentIndex(1)
        with patch.object(self.window,'ask_ai') as ask:
            self.window.search_input.setFocus()
            QTest.keyClicks(self.window.search_input,'question')
            QTest.qWait(450)
            self.app.processEvents()
            ask.assert_not_called()
            QTest.keyClick(self.window.search_input,Qt.Key.Key_Return)
            ask.assert_called_once()

    def test_name_path_and_extension_filters_reach_real_catalog(self):
        path=self.sample/'parent-keyword'
        path.mkdir()
        (path/'child.txt').write_text('body',encoding='utf-8')
        (path/'child.pdf').write_text('sample',encoding='utf-8')
        self.window.discovery.start_scan(self.sid)
        self.window.discovery_scope.setCurrentIndex(0)
        self.window.catalog_scope.setCurrentIndex(1)
        self.window.catalog_ext.setText('txt')
        self.window.use_query('parent-keyword')
        self.idle()
        self.assertEqual(self.window.discovery_results.topLevelItemCount(),1)
        self.assertEqual(self.window.discovery_results.topLevelItem(0).text(0),'child.txt')

    def test_clear_query_clears_ai_context_and_old_results(self):
        path=self.sample/'hit.txt'
        path.write_text('body',encoding='utf-8')
        self.window.discovery.start_scan(self.sid)
        self.window.use_query('hit')
        self.idle()
        self.window.use_query('')
        self.idle()
        self.assertEqual(self.window.groups,[])
        self.assertEqual(self.window.discovery_results.topLevelItemCount(),0)
        self.assertTrue(self.window.search_button.isEnabled())

    def test_range_stop_cancels_the_actual_jobs_scan(self):
        started=threading.Event()
        result=[]
        def scan(sid,progress,stop,full_native=True):
            started.set()
            while not stop.is_set():
                time.sleep(.01)
            result.append('cancelled')
            return {'state':'partial','stopped':True}
        self.window.open_range_manager()
        self.idle()
        dialog=self.window._range_dialog
        for i in range(dialog.tree.topLevelItemCount()):
            item=dialog.tree.topLevelItem(i)
            if item.data(0,Qt.ItemDataRole.UserRole)==self.sid:
                dialog.tree.setCurrentItem(item)
        with patch.object(self.window.discovery,'_scan',side_effect=scan):
            dialog.start_selected()
            self.wait(started.is_set)
            dialog.stop_selected()
            self.idle()
        self.assertEqual(result,['cancelled'])

    def test_switch_scope_rejects_late_directory_results(self):
        path=self.sample/'late-target.txt'
        path.write_text('body',encoding='utf-8')
        self.window.discovery.start_scan(self.sid)
        entered,release=threading.Event(),threading.Event()
        original=self.window.discovery.search
        def delayed(request,**kwargs):
            page=original(request,**kwargs)
            entered.set()
            release.wait(3)
            return page
        with patch.object(self.window.discovery,'search',side_effect=delayed):
            self.window.discovery_scope.setCurrentIndex(0)
            self.window.use_query('late-target')
            self.wait(entered.is_set)
            self.window.discovery_scope.setCurrentIndex(1)
            self.window.start_search()
            release.set()
            self.idle()
        self.assertEqual(self.window.result_stack.currentIndex(),0)
        self.assertEqual(self.window.discovery_results.topLevelItemCount(),0)

    def test_cancelled_answer_cannot_append_late_tokens_to_new_answer(self):
        entered,release=threading.Event(),threading.Event()
        def stream(base,model,messages,should_stop):
            if messages[-1]['content']=='old question':
                entered.set()
                release.wait(3)
                yield 'LATE-OLD'
            else:
                yield 'NEW-ANSWER'
        self.window.search_mode.setCurrentIndex(1)
        self.window.ai_kind.setCurrentIndex(1)
        with patch('ai_qa.chat_stream',side_effect=stream):
            self.window.search_input.setText('old question')
            self.window.ask_ai()
            self.wait(entered.is_set)
            self.window.stop_ai()
            self.window.search_input.setText('new question')
            self.window.ask_ai()
            release.set()
            self.idle()
        self.assertEqual(self.window.ai_answer.toPlainText(),'NEW-ANSWER')
        self.assertEqual(len(self.window._ai_history),2)

    def test_image_preview_retains_aspect_ratio_without_reading_on_resize(self):
        from PySide6.QtGui import QImage,QColor
        image=QImage(800,200,QImage.Format.Format_RGB32)
        image.fill(QColor('blue'))
        path=self.sample/'wide.png'
        self.assertTrue(image.save(str(path)))
        self.window.preview_discovery_file({'path':str(path),'name':path.name,'kind':'file','online':True})
        self.idle()
        self.wait(lambda:self.window.discovery_preview.image.pixmap() is not None)
        source=self.window.discovery_preview._pixmap
        self.window.resize(1150,750)
        self.app.processEvents()
        displayed=self.window.discovery_preview.image.pixmap()
        self.assertAlmostEqual(displayed.width()/displayed.height(),4,delta=.1)
        self.assertEqual(source.cacheKey(),self.window.discovery_preview._pixmap.cacheKey())

    def test_metadata_only_filter_still_works_in_default_combined_scope(self):
        path=self.sample/'obsolete.txt'
        path.write_text('archived text',encoding='utf-8')
        self.store.index_files([str(path)],lambda _:None,threading.Event())
        import indexer
        indexer.update_metadata(self.store.db,str(path),status='void')
        combo=self.window.facet_combos['状态']
        combo.setCurrentIndex(combo.findData('废止'))
        self.window.start_search()
        self.idle()
        self.assertEqual(self.window.query,'状态:已废止')
        self.assertEqual([group['path'] for group in self.window.groups],[str(path)])
        self.assertEqual(self.window.discovery_results.topLevelItemCount(),1)

    def test_directory_badge_counts_only_conditions_used_by_directory_query(self):
        self.window.discovery_scope.setCurrentIndex(0)
        self.assertIn('筛选 · 0',self.window.options_button.text())
        self.window.catalog_ext.setText('txt')
        self.window.catalog_root.setText(str(self.sample))
        self.assertIn('筛选 · 2',self.window.options_button.text())
        self.window.reset_search_options()
        self.assertEqual(self.window.catalog_ext.text(),'')
        self.assertEqual(self.window.catalog_root.text(),'')

    def test_directory_only_results_export_real_paths_without_content_groups(self):
        path=self.sample/'export-target.txt'
        path.write_text('body',encoding='utf-8')
        self.window.discovery.start_scan(self.sid)
        self.window.discovery_scope.setCurrentIndex(0)
        self.window.use_query('export-target')
        self.idle()
        target=self.root/'result.csv'
        with patch('qt_frontend.tools.QFileDialog.getSaveFileName',return_value=(str(target),'CSV 表格 (*.csv)')):
            self.window.export_results()
            self.idle()
        import csv
        with target.open(encoding='utf-8-sig',newline='') as stream:
            rows=list(csv.reader(stream))
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[1][1],str(path))

    def test_send_results_enters_native_answer_without_launching_legacy_window(self):
        self.window.groups=[{'path':'test-only','name':'sample','matches':[]}]
        with patch.object(self.window,'legacy_tool') as legacy:
            self.window.send_results_to_qa()
            legacy.assert_not_called()
        self.assertEqual(self.window.search_mode.currentIndex(),1)
        self.assertTrue(self.window.ai_use_results.isChecked())

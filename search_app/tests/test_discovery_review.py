"""交接复核回归：错误恢复、实际通知、游标不丢失与输出路径保护。"""
import os
from pathlib import Path
import sqlite3
import struct
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager, ExitStack
from unittest.mock import patch

from discovery import DiscoveryService, SearchRequest, SourceInfo, ChangeEvent
from discovery import usn, sync
from discovery.catalog import Catalog, normcase
from discovery.errors import Cancelled, ReconciliationRequired
from discovery.model import SourceCapabilities
from discovery.scan import scan_source
from discovery.watch import DiscoveryWatcher
from tests.test_discovery_usn import _v2_record


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='discovery-review-')
        self.base = Path(self.tmp.name)
        self.root = self.base / '样本'
        self.root.mkdir()
        self.svc = DiscoveryService(str(self.base / '目录#测试%.db'))
        self.cat = self.svc.catalog
        self.sid = self.svc.add_directory_source(str(self.root))
        self.info = SourceInfo(self.sid,'directory',str(self.root))

    def tearDown(self):
        self.tmp.cleanup()

    def file(self,name='a.txt',text='x'):
        path = self.root / name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(text,encoding='utf-8')
        self.cat.upsert_path(self.sid,str(path))
        return path

    def paths(self):
        return {r['path'] for r in self.cat.search(SearchRequest('',limit=1000)).results}

    def version(self,path):
        with self.cat._conn(read_only=True) as conn:
            return conn.execute('SELECT content_version FROM entries WHERE source_id=? AND match_path=?',
                                (self.sid,normcase(str(path)))).fetchone()[0]

    def test_handoff_hash_uri_and_fresh_read(self):
        self.assertEqual(len(self.svc.list_sources()),1)
        self.assertEqual(self.paths(),set())
        self.file('中文#%.txt')
        self.assertEqual(len(self.cat.search(SearchRequest('#%')).results),1)

    def test_denied_subtree_preserves_old_entries(self):
        path = self.file('拒绝/保留.txt')
        original = os.scandir
        def denied(directory):
            if Path(directory)==path.parent:
                raise PermissionError('simulated denied subtree')
            return original(directory)
        with patch('discovery.scan.os.scandir',side_effect=denied):
            report=scan_source(self.cat,self.info)
        self.assertEqual(report.state,'partial')
        self.assertIn(str(path),self.paths())
        self.assertTrue(self.cat.get_source(self.sid)['needs_rescan'])

    def test_offline_root_retains_names_with_offline_flag(self):
        path=self.file()
        self.info.root=str(self.base/'不存在')
        report=scan_source(self.cat,self.info)
        self.assertEqual(report.state,'offline')
        hits=self.cat.search(SearchRequest('a.txt')).results
        self.assertEqual(hits[0]['path'],str(path))
        self.assertFalse(hits[0]['online'])

    def test_flat_directory_flush_is_bounded_inside_iteration(self):
        for n in range(11): self.file(f'{n}.txt')
        batches=[]
        original=self.cat.upsert_entries
        def capture(rows):
            batches.append(len(rows))
            return original(rows)
        with patch.object(self.cat,'upsert_entries',side_effect=capture):
            scan_source(self.cat,self.info,batch_size=3)
        self.assertGreater(len(batches),1)
        self.assertLessEqual(max(batches),3)

    def test_catalog_and_sidecars_are_not_catalogued(self):
        catalog=Catalog(str(self.root/'catalog.db'))
        source=SourceInfo('self','directory',str(self.root))
        catalog.register_source(source)
        scan_source(catalog,source)
        names={r['name'] for r in catalog.search(SearchRequest('',limit=50)).results}
        self.assertNotIn('catalog.db',names)
        self.assertFalse(any(name.startswith('catalog.db-') for name in names))

    def test_rescan_invalidates_content_if_file_changed(self):
        path=self.file()
        scan_source(self.cat,self.info)
        before=self.version(path)
        path.write_text('longer content',encoding='utf-8')
        scan_source(self.cat,self.info)
        self.assertGreater(self.version(path),before)

    def test_directory_rename_includes_direct_and_deep_children(self):
        direct=self.file('旧/直接.txt'); deep=self.file('旧/子/深层.txt')
        self.cat.upsert_path(self.sid,str(direct.parent))
        self.cat.rename_entry(self.sid,str(direct.parent),str(self.root/'新'),is_dir=True)
        paths=self.paths()
        self.assertIn(str(self.root/'新/直接.txt'),paths)
        self.assertIn(str(self.root/'新/子/深层.txt'),paths)
        self.assertNotIn(str(direct),paths)

    def test_delete_directory_removes_catalog_subtree(self):
        path=self.file('删/子.txt')
        self.cat.upsert_path(self.sid,str(path.parent))
        self.cat.record_event(ChangeEvent(self.sid,change_type='deleted',
            old_parent_path=str(self.root),old_name='删'))
        self.assertNotIn(str(path),self.paths())
        self.assertTrue(path.exists())  # 只删目录记录，不删真实文件。

    def test_file_replace_and_extension_are_updated(self):
        old=self.file('old.txt'); target=self.file('new.md','target')
        os.replace(old,target)
        self.cat.rename_entry(self.sid,str(old),str(target))
        hits=self.cat.search(SearchRequest('new.md')).results
        self.assertEqual(len(hits),1)
        self.assertEqual(hits[0]['ext'],'md')

    def test_native_batch_replay_does_not_double_invalidate(self):
        path=self.file()
        event=ChangeEvent(self.sid,seq=10,change_type='modified',old_parent_path=str(self.root),old_name=path.name)
        self.cat.apply_usn_batch(self.sid,'usn','j','20',[event])
        before=self.version(path)
        self.assertEqual(self.cat.apply_usn_batch(self.sid,'usn','j','20',[event]),0)
        self.assertEqual(self.version(path),before)

    def test_native_failure_rolls_back_events_and_checkpoint(self):
        path=self.file()
        self.cat.save_checkpoint(self.sid,'usn','10','j')
        events=[ChangeEvent(self.sid,seq=11,change_type='modified',old_parent_path=str(self.root),old_name=path.name),
                ChangeEvent(self.sid,seq=12,change_type='created',new_parent_path=str(self.root),new_name='不存在.txt')]
        with self.assertRaises(ReconciliationRequired):
            self.cat.apply_usn_batch(self.sid,'usn','j','20',events)
        self.assertEqual(self.cat.get_checkpoint(self.sid)['cursor'],'10')
        self.assertEqual(self.version(path),0)
        with self.cat._conn(read_only=True) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)

    def test_distinct_128bit_identities_are_not_truncated(self):
        row={'source_id':self.sid,'parent_path':str(self.root),'name':'large.txt',
             'path':str(self.root/'large.txt'),'match_name':'large.txt','match_path':normcase(str(self.root/'large.txt')),
             'ext':'txt','kind':'file','file_id':(0x123456<<64)+7,'volume_serial':1}
        self.cat.upsert_entries([row])
        other=dict(row,name='other.txt',path=str(self.root/'other.txt'),match_name='other.txt',
                   match_path=normcase(str(self.root/'other.txt')),file_id=(0x654321<<64)+7)
        self.cat.upsert_entries([other])
        self.assertEqual(len(self.cat.frn_path_map(self.sid)),2)
        self.assertIn(row['file_id'],self.cat.frn_path_map(self.sid))

    def test_case_sensitive_and_unicode_casefold_search(self):
        self.file('AbC.txt');self.file('abc.txt');self.file('Straße.txt')
        self.assertEqual(len(self.cat.search(SearchRequest('AbC',case_sensitive=True)).results),1)
        self.assertEqual(len(self.cat.search(SearchRequest('STRASSE')).results),1)

    def test_drive_root_has_single_separator(self):
        self.cat.upsert_entries([dict(source_id=self.sid,path='C:\\x.txt',name='x.txt',parent_path='C:\\',
            match_path=normcase('C:\\x.txt'),match_name='x.txt',kind='file',ext='txt')])
        self.assertEqual(len(self.cat.search(SearchRequest('',root='C:\\')).results),1)

    def test_source_registration_is_stable_and_reason_clears(self):
        self.assertEqual(self.svc.add_directory_source(str(self.root)),self.sid)
        self.cat.set_source_state(self.sid,'partial',rescan_reason='old')
        self.cat.set_source_state(self.sid,'complete',rescan_reason=None)
        self.assertIsNone(self.cat.get_source(self.sid)['rescan_reason'])

    def test_query_cancellation(self):
        with self.assertRaises(Cancelled):
            self.cat.search(SearchRequest('',cancel=lambda:True))

    def test_unrelated_database_is_not_modified(self):
        path=self.base/'unrelated.db'
        conn=sqlite3.connect(path)
        conn.execute('CREATE TABLE precious(value)')
        conn.commit()
        conn.close()
        original=path.read_bytes()
        with self.assertRaises(sqlite3.DatabaseError):Catalog(str(path))
        self.assertEqual(path.read_bytes(),original)

    def test_watcher_directory_arrival_requires_subtree_scan(self):
        path=self.file('到达/已存在子文件.txt')
        watcher=DiscoveryWatcher(self.cat,self.sid,str(self.root))
        watcher.on_created(str(path.parent),is_dir=True)
        self.assertTrue(self.cat.get_source(self.sid)['needs_rescan'])

    def test_real_watcher_rename_and_delete(self):
        path=self.file()
        watcher=DiscoveryWatcher(self.cat,self.sid,str(self.root))
        if not watcher.start():self.skipTest('watchdog observer unavailable')
        def wait_for(condition):
            deadline=time.monotonic()+5
            while not condition() and time.monotonic()<deadline:time.sleep(.025)
            self.assertTrue(condition())
        try:
            renamed=path.with_name('已改名.txt');path.rename(renamed)
            wait_for(lambda:str(renamed) in self.paths() and str(path) not in self.paths())
            renamed.unlink()
            wait_for(lambda:str(renamed) not in self.paths())
        finally:watcher.stop()


    @contextmanager
    def native(self,chunks,target=20,mapping=None):
        self.cat.set_source_state(self.sid,'complete',needs_rescan=False,rescan_reason=None)
        self.cat.save_checkpoint(self.sid,'usn','10','42')
        caps=SourceCapabilities(self.sid,'usn',True,True)
        journal={'usn_journal_id':42,'first_usn':0,'lowest_valid_usn':0,'next_usn':target}
        with ExitStack() as stack:
            stack.enter_context(patch.object(self.svc,'probe_source',return_value=caps))
            stack.enter_context(patch('discovery.provider.usn.open_volume_handle',return_value=123))
            stack.enter_context(patch('discovery.provider.usn.query_usn_journal',return_value=journal))
            stack.enter_context(patch('discovery.provider.usn.close_handle'))
            stack.enter_context(patch('discovery.provider.usn.read_usn_journal_chunk',side_effect=chunks))
            if mapping is not None:
                stack.enter_context(patch.object(self.cat,'frn_path_map',return_value=mapping))
            yield

    def test_empty_journal_chunk_commits_and_returns_new_cursor(self):
        with self.native([(struct.pack('<Q',20),20)]):
            result=self.svc.sync_source(self.sid)
        self.assertFalse(result.needs_rescan)
        self.assertEqual(result.cursor,'20')
        self.assertEqual(self.cat.get_checkpoint(self.sid)['cursor'],'20')

    def test_unknown_record_does_not_advance_cursor(self):
        raw=struct.pack('<QIHH',20,8,9,0)
        with self.native([(raw,20)]):
            result=self.svc.sync_source(self.sid)
        self.assertTrue(result.needs_rescan)
        self.assertEqual(self.cat.get_checkpoint(self.sid)['cursor'],'10')

    def test_hardlink_gap_does_not_advance_cursor(self):
        raw=struct.pack('<Q',20)+_v2_record(11,10,5,'a.txt',usn.USN_REASON_HARD_LINK_CHANGE)
        with self.native([(raw,20)],mapping={5:str(self.root),10:str(self.root/'a.txt')}):
            result=self.svc.sync_source(self.sid)
        self.assertTrue(result.needs_rescan)
        self.assertEqual(self.cat.get_checkpoint(self.sid)['cursor'],'10')

    def test_journal_map_survives_across_chunks(self):
        old=self.file();new=old.with_name('b.txt');old.rename(new)
        first=struct.pack('<Q',20)+_v2_record(11,10,5,'b.txt',usn.USN_REASON_RENAME_NEW_NAME)
        second=struct.pack('<Q',30)+_v2_record(21,10,5,'b.txt',usn.USN_REASON_DATA_OVERWRITE)
        with self.native([(first,20),(second,30)],target=30,mapping={5:str(self.root),10:str(old)}):
            result=self.svc.sync_source(self.sid)
        self.assertFalse(result.needs_rescan)
        self.assertIn(str(new),self.paths())
        self.assertEqual(self.version(new),1)

    def test_backlog_does_not_report_complete(self):
        raw=struct.pack('<Q',20)+_v2_record(11,10,5,'a.txt',usn.USN_REASON_CLOSE)
        with self.native([(raw,20)],target=30), patch('discovery.provider.SYNC_MAX_RECORDS',1):
            result=self.svc.sync_source(self.sid)
        self.assertTrue(result.more_pending)
        self.assertEqual(self.cat.get_source(self.sid)['state'],'partial')
        self.assertEqual(result.cursor,'20')

    def test_metadata_reason_does_not_invalidate_content(self):
        path=self.file()
        self.cat.record_event(ChangeEvent(self.sid,change_type='metadata',
            old_parent_path=str(self.root),old_name=path.name))
        self.assertEqual(self.version(path),0)

    def test_enumeration_uses_header_cursor_not_last_file_id(self):
        raw=struct.pack('<Q',222)+_v2_record(11,999,5,'a.txt',usn.USN_REASON_FILE_CREATE)
        with self.native([]),patch('discovery.provider.usn.enum_usn_chunk',side_effect=[raw,b'']) as enum:
            result=self.svc.bounded_usn_enum(self.sid,100)
        self.assertTrue(result['supported'])
        self.assertEqual(enum.call_args_list[1].args[1],222)

    def test_enum_upper_usn_is_positive_signed_max(self):
        values=[]
        def ioctl(handle,code,inp,out):
            values.append(inp.HighUsn)
            return 0
        with patch('discovery.usn._device_io_control',side_effect=ioctl):
            usn.enum_usn_chunk(123)
        self.assertEqual(values,[(1<<63)-1])

    def test_filename_offset_cannot_point_into_record_header(self):
        raw=bytearray(_v2_record(1,10,5,'a.txt',usn.USN_REASON_FILE_CREATE))
        struct.pack_into('<H',raw,58,0)
        records,stats=usn.parse_usn_buffer(bytes(raw))
        self.assertEqual(records,[])
        self.assertEqual(stats['bad_length'],1)

    def test_root_identity_can_seed_partial_enumeration(self):
        records=[dict(file_ref=10,parent_ref=0x5000000000005,name='a.txt',is_dir=False)]
        paths=sync.build_path_map(records,root='F:\\',root_ref=0x5000000000005)
        self.assertEqual(paths[10],'F:\\a.txt')

    def test_successful_rescan_reconciles_old_failed_events(self):
        self.cat.record_event(ChangeEvent(self.sid,change_type='created',new_parent_path=str(self.root),
                                         new_name='已消失.txt'))
        self.assertTrue(self.cat.get_source(self.sid)['needs_rescan'])
        scan_source(self.cat,self.info)
        with self.cat._conn(read_only=True) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM events WHERE applied=0').fetchone()[0],0)
        self.assertFalse(self.cat.get_source(self.sid)['needs_rescan'])

    def test_hardlink_modification_invalidates_both_paths(self):
        first=self.file()
        second=self.root/'link.txt'
        try:os.link(first,second)
        except OSError:self.skipTest('hardlink unsupported')
        self.cat.upsert_path(self.sid,str(second))
        first.write_text('changed',encoding='utf-8')
        self.cat.touch_modified(self.sid,str(first))
        self.assertEqual(self.version(first),1)
        self.assertEqual(self.version(second),1)

    def test_real_scan_keeps_distinct_file_identities_and_nested_directories(self):
        first=self.file('第一层/第二层/a.txt')
        second=self.file('另一个目录/b.txt')
        report=scan_source(self.cat,self.info)
        self.assertEqual(report.state,'complete')
        hits=self.cat.search(SearchRequest('.txt')).results
        self.assertEqual(len({hit['object_id'] for hit in hits}),2)
        self.assertIn(str(first),self.paths())
        self.assertIn(str(second),self.paths())

    def test_no_extension_filter(self):
        self.file('README');self.file('file.txt')
        hits=self.cat.search(SearchRequest('',ext=[''])).results
        self.assertEqual([hit['name'] for hit in hits],['README'])

    def test_real_long_path_scan(self):
        from discovery.filters import io_path
        path=self.root
        directories=[]
        try:
            for number in range(7):
                path=path/(f'{number}-'+'长路径测试'*7)
                os.mkdir(io_path(path))
                directories.append(path)
            document=path/'deep.txt'
            with open(io_path(document),'w',encoding='utf-8') as stream:stream.write('sample')
            report=scan_source(self.cat,self.info)
            with self.cat._conn(read_only=True) as conn:
                failures=[dict(row) for row in conn.execute('SELECT stage,error FROM failures')]
            self.assertEqual(report.state,'complete',failures)
            self.assertIn(str(document),self.paths())
        finally:
            if 'document' in locals() and os.path.exists(io_path(document)):
                self.assertTrue(document.is_relative_to(self.root))
                os.unlink(io_path(document))
            for directory in reversed(directories):
                self.assertTrue(directory.is_relative_to(self.root))
                os.rmdir(io_path(directory))

"""界面工具与业务入口；所有耗时工作提交到 Jobs。"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from functools import partial
import io
import copy
import json
import html
import os
from pathlib import Path
import re
import sqlite3
import time

from PySide6.QtCore import QProcess, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QSpinBox, QTextBrowser,
    QScrollArea, QVBoxLayout, QWidget, QWidgetAction,
)
from shiboken6 import isValid

import core
import indexer
import searcher
from export import write_digest_docx

from .backend import STATUS
from .widgets import Records, button, label, page_heading, records_table
from .answer_view import AnswerView


class ToolsMixin:
    def keep_dialog(self, dialog):
        self._dialogs.append(dialog)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.destroyed.connect(lambda: self._dialogs.remove(dialog) if dialog in self._dialogs else None)
        dialog.show()
        return dialog

    def text_dialog(self, title, text):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(1000, 720)
        layout = QVBoxLayout(dialog)
        body = QPlainTextEdit()
        body.setReadOnly(True)
        body.setPlainText(str(text))
        layout.addWidget(body)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        controls.rejected.connect(dialog.close)
        layout.addWidget(controls)
        return self.keep_dialog(dialog)

    def records_dialog(self, title, columns, rows, on_open=None):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(1240, 780)
        layout = QVBoxLayout(dialog)
        search = QLineEdit()
        search.setPlaceholderText('筛选列表…')
        layout.addWidget(search)
        model = Records(columns, dialog)
        model.set_rows(rows)
        dialog.source_rows = list(rows)
        table = records_table(model)
        table.setColumnWidth(0, 350)
        layout.addWidget(table, 1)
        search.textChanged.connect(lambda text: model.set_rows([
            row for row in dialog.source_rows if text.casefold() in str(row).casefold()]))
        if on_open:
            def activate(*_):
                indices = table.selectionModel().selectedRows()
                if indices:
                    on_open(model.rows[indices[0].row()])
            table.doubleClicked.connect(activate)
        dialog.table, dialog.model, dialog.layout_box = table, model, layout
        return self.keep_dialog(dialog)

    def add_menu(self):
        self.build_add_menu().exec(self.mapToGlobal(self.rect().center()))

    def build_add_menu(self):
        menu = QMenu(self)
        menu.addAction('添加文件…', self.choose_files)
        menu.addAction('扫描文件夹…', self.choose_folder)
        menu.addAction('待入库文件审核…', partial(self.legacy_tool, 'inbox'))
        menu.addAction('快存剪贴板文字', self.clipboard_save)
        return menu

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, '添加资料', '',
            '支持的文件 (*.pdf *.docx *.doc *.xlsx *.xls *.pptx *.ppt *.txt *.md *.csv *.json *.zip *.7z *.rar *.png *.jpg *.jpeg);;所有文件 (*)')
        if paths:
            self.confirm_import(paths)

    def confirm_import(self, paths):
        paths = list(dict.fromkeys(str(Path(path)) for path in paths))
        directories = [path for path in paths if Path(path).is_dir()]
        files = [path for path in paths if Path(path).is_file()]
        if directories:
            if QMessageBox.question(self, '扫描文件夹',
                f'扫描 {len(directories)} 个文件夹并更新索引？原文件保留在原位置。') != QMessageBox.StandardButton.Yes:
                return
            for folder in directories:
                self.scan_folder(folder)
        if not files:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('添加资料')
        dialog.resize(570, 260)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(f'添加 {len(files)} 个文件', 'section'))
        names = label('\n'.join(Path(path).name for path in files[:5]) + ('\n…' if len(files) > 5 else ''))
        names.setWordWrap(True)
        layout.addWidget(names)
        copy = QCheckBox('复制一份到资料库（原文件保留）')
        copy.setChecked(bool(self.store.settings.get('copy_import', True)))
        layout.addWidget(copy)
        hint = label('不勾选时，直接索引原文件；资料仍保留在原位置。', 'muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        controls.button(QDialogButtonBox.StandardButton.Ok).setText('开始添加')
        controls.accepted.connect(dialog.accept)
        controls.rejected.connect(dialog.reject)
        layout.addWidget(controls)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.store.settings['copy_import'] = copy.isChecked()
            self.store.save()
            self.index_files(files, copy.isChecked())

    def index_files(self, files, copy=False):
        self.jobs.submit(f'添加 {len(files)} 个文件',
            lambda progress, stop: self.store.index_files(files, progress, stop, copy),
            self.after_index, error=self.show_error, mutating=True)
        self.notify('任务已开始，可继续搜索或在“任务”中查看进度')

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, '选择要扫描的资料文件夹', self.store.settings.get('root_dir', ''))
        if folder:
            self.scan_folder(folder)

    def scan_folder(self, folder):
        self.store.settings['root_dir'] = folder
        self.store.save()
        self.jobs.submit('扫描：' + Path(folder).name,
            lambda progress, stop: self.store.scan(folder, progress, stop),
            self.after_index, error=self.show_error, mutating=True)

    def after_index(self, result):
        self.refresh_stats()
        self.refresh_facets()
        if self.nav['library'].isChecked():
            self.load_library()
        elif self.nav['search'].isChecked() and self.search_input.text().strip():
            self.start_search()
        self.start_watcher()
        self.notify('索引处理完成，可在任务中查看结果')
        self.sync_semantic()

    def start_watcher(self):
        folder = self.store.settings.get('root_dir', '')
        enabled = self.store.settings.get('autowatch', True)
        if self.watcher:
            if self.watcher.root == folder and enabled and self.watcher.is_running():
                return
            self.watcher.stop()
            self.watcher = None
        if enabled and folder and Path(folder).is_dir():
            try:
                from watcher import FileWatcher
                self.watcher = FileWatcher(folder, self.store.db,
                    on_event=lambda status, path: self.filesystem_changed.emit(status, path))
                self.watcher.start()
            except Exception as exc:
                self.notify('自动监听未启动：' + str(exc))

    def _watch_changed(self, status, path):
        self.notify(f'资料更新：{Path(path).name} · {status}')
        if not hasattr(self, '_stats_timer'):
            self._stats_timer = QTimer(self)
            self._stats_timer.setSingleShot(True)
            self._stats_timer.timeout.connect(self.refresh_stats)
        self._stats_timer.start(1200)
        if status in ('ok', 'deleted'):
            self.sync_semantic()

    def history_menu(self):
        menu = QMenu(self)
        recent = self.store.settings.get('recent_keywords', [])
        if not recent:
            menu.addAction('暂无搜索历史').setEnabled(False)
        for query in recent[:25]:
            menu.addAction(query, partial(self.use_query, query))
        menu.addSeparator()
        menu.addAction('保存当前搜索条件…', self.save_search)
        saved = menu.addMenu('常用搜索')
        for template in self.store.settings.get('saved_searches', []):
            saved.addAction(template['name'], partial(self.apply_saved_search, template))
        if not saved.actions():
            saved.addAction('暂无保存的搜索').setEnabled(False)
        menu.addAction('管理常用搜索…', self.manage_searches)
        menu.addAction('恢复上次阅读', self.restore_reading)
        menu.exec(self.search_input.mapToGlobal(self.search_input.rect().bottomRight()))

    def use_query(self, query):
        self.navigate('search')
        self.search_input.setText(query)
        self.start_search()

    def save_search(self):
        query = self.search_input.text().strip()
        if not query:
            self.notify('请先输入关键词')
            return
        name, ok = QInputDialog.getText(self, '保存搜索', '名称', text=query)
        if ok and name.strip():
            template = {'name': name.strip(), 'kw': query, 'mode': 'fuzzy' if self.mode.currentIndex() else 'exact',
                        'oa': self.active_filter.isChecked(), 'syn': self.synonym_filter.isChecked(),
                        'ft': self.type_filter.currentData() or 'all', 'cs': self.case_filter.isChecked(),
                        'fields': {key: field.text() for key, field in self.advanced_fields.items()}}
            self.store.settings['saved_searches'] = [item for item in self.store.settings.get('saved_searches', [])
                                                    if item['name'] != template['name']] + [template]
            self.store.save()
            self.notify('搜索条件已保存')

    def apply_saved_search(self, template):
        self.mode.setCurrentIndex(1 if template.get('mode') == 'fuzzy' else 0)
        self.active_filter.setChecked(template.get('oa', True))
        self.synonym_filter.setChecked(template.get('syn', False))
        self.case_filter.setChecked(template.get('cs', False))
        file_type = template.get('ft', 'all')
        index = self.type_filter.findData(file_type)
        if index < 0:
            index = self.type_filter.findText(file_type)
        self.type_filter.setCurrentIndex(max(0, index))
        for key, field in self.advanced_fields.items():
            field.setText(template.get('fields', {}).get(key, ''))
        self.use_query(template.get('kw', ''))

    def manage_searches(self):
        rows = self.store.settings.get('saved_searches', [])
        dialog = self.records_dialog('常用搜索 · 双击应用', [('名称', 'name'), ('关键词', 'kw'), ('模式', 'mode')],
                                     list(rows), self.apply_saved_search)
        def remove():
            selection = dialog.table.selectionModel().selectedRows()
            names = {dialog.model.rows[idx.row()]['name'] for idx in selection}
            self.store.settings['saved_searches'] = [item for item in self.store.settings.get('saved_searches', [])
                                                    if item['name'] not in names]
            self.store.save()
            dialog.model.set_rows(self.store.settings['saved_searches'])
        dialog.layout_box.addWidget(button('删除所选常用搜索', remove))

    def clipboard_save(self):
        if QApplication.clipboard().mimeData().hasUrls():
            self.notify('剪贴板里是文件，请复制文字后再快存')
            return
        text = QApplication.clipboard().text()
        if not text.strip():
            self.notify('剪贴板里没有可保存的文字')
            return
        source = ''
        try:
            import ctypes
            from ctypes import wintypes
            native = ctypes.windll.user32
            native.GetForegroundWindow.restype = wintypes.HWND
            native.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
            buffer = ctypes.create_unicode_buffer(1024)
            native.GetWindowTextW(native.GetForegroundWindow(), buffer, len(buffer))
            source = buffer.value
        except (AttributeError, OSError):
            pass
        folder = self.store.root / 'library' / indexer.OTHER_CATEGORY
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / datetime.now().strftime('剪藏_%Y%m%d_%H%M%S_%f.txt')
        target.write_text(f'剪存时间：{datetime.now():%Y-%m-%d %H:%M:%S}\n来源窗口：{source or "未知"}\n'
                          + '-' * 40 + '\n' + text.strip()[:50000] + '\n', encoding='utf-8')
        def done(status):
            if status not in ('ok', 'skip'):
                self.notify('快存索引未完成：' + str(status))
                return
            self.notify('已快存到剪藏，现在就能搜到')
            self.refresh_stats()
        self.jobs.submit('快存剪贴板并索引', lambda progress, stop: self.store.write(
            lambda db, path: indexer.index_single_file(path, db), str(target)), done, error=self.show_error, mutating=True)

    def restore_reading(self):
        last = self.store.settings.get('last_read')
        if last:
            self.navigate('search')
            self.open_preview(last['path'], last['line'])
        else:
            self.notify('暂无阅读记录')

    def syntax_help(self):
        self.text_dialog('搜索语法',
            '安全 培训        同时包含两个关键词\n安全|培训        包含其中任意一个\n安全 -考试       排除“考试”\n'
            '文号:某文号      按文号筛选\n名称:管理办法    按文件名称筛选\n分类:某分类      按分类筛选\n\n'
            '精确匹配：优先准确命中。\n模糊匹配：兼容一个字的错写、漏写或多写。\n'
            '同义词扩展：沿用已有内置词组和自定义词组。\n\n'
            'Ctrl+F 搜索  ·  Ctrl+D 收藏  ·  F3 下一处命中  ·  Alt+← 返回\n'
            'Ctrl+E 导出结果  ·  F5 刷新\n\n'
            '全文模式按当前位置分段加载长文档，可通过目录或行号跳转，避免大文件拖慢界面。')

    def export_results(self):
        catalog_rows=[]
        if hasattr(self,'discovery_results') and self.result_stack.currentWidget() is self.discovery_results:
            catalog_rows=[self.discovery_results.topLevelItem(i).data(0,Qt.ItemDataRole.UserRole)
                          for i in range(self.discovery_results.topLevelItemCount())]
        if not self.groups and not catalog_rows:
            self.notify('请先搜索资料')
            return
        title=f'导出已加载的搜索结果（{len(catalog_rows)} 条）' if catalog_rows else '导出搜索结果'
        target, selected = QFileDialog.getSaveFileName(self, title,
            '搜索结果.csv', 'CSV 表格 (*.csv)'+(';;Word 汇编 (*.docx)' if self.groups else ''))
        if not target:
            return
        groups = list(self.groups)
        if selected.startswith('Word') or target.lower().endswith('.docx'):
            if not target.lower().endswith('.docx'):
                target += '.docx'
            self._export_digest(target, groups, '搜索结果汇编')
        else:
            if not target.lower().endswith('.csv'):
                target += '.csv'
            def write(progress,stop):
                if catalog_rows:
                    from .backend import export_catalog_csv
                    return export_catalog_csv(target,catalog_rows,stop)
                return export_csv(target,groups)
            self.jobs.submit('导出搜索结果', write,
                lambda _: self.notify('已导出：' + target), error=self.show_error)

    def _export_digest(self, target, groups, title):
        query = self.query
        sections = [(group['path'], [(f"L{m['line_no']}", m['line_no']) for m in group['matches']]) for group in groups]
        def work(progress, stop):
            ok, error = write_digest_docx(self.store.db, target, title,
                '镜仔搜索 · ' + datetime.now().strftime('%Y-%m-%d'), sections,
                highlight_terms=core.kw_terms(query))
            if not ok:
                raise RuntimeError(error)
            return target
        self.jobs.submit('导出 Word 汇编', work, lambda path: self.notify('已导出：' + path), error=self.show_error)

    def export_bookmarks(self):
        def got(rows):
            if not rows:
                self.notify('还没有收藏的条款')
                return
            target, _ = QFileDialog.getSaveFileName(self, '导出收藏汇编', '收藏汇编.docx', 'Word 文件 (*.docx)')
            if target:
                grouped = defaultdict(list)
                for row in rows:
                    grouped[row['path']].append({'line_no': row['line_no']})
                self._export_digest(target, [{'path': path, 'matches': matches} for path, matches in grouped.items()], '收藏条款汇编')
        self.jobs.submit('读取收藏', lambda progress, stop: indexer.list_bookmarks(self.store.db), got, visible=False)

    def library_menu(self):
        self.build_library_menu().exec(self.library_table.mapToGlobal(self.library_table.rect().center()))

    def build_library_menu(self):
        menu = QMenu(self)
        count=len(self.selected_library_rows())
        for title, fn in [('打开原文件', self.open_library_original), ('显示所在文件夹', self.reveal_library_file),
                          ('重命名文件…', self.rename_file), ('重新索引所选文件', self.reindex_selection),
                          ('从索引中移除…', self.remove_library_selection)]:
            action=menu.addAction(title, fn)
            action.setEnabled(count==1 if title in ('打开原文件','显示所在文件夹','重命名文件…') else count>0)
        menu.addSeparator()
        for title, fn in [('批量编辑信息…', self.batch_metadata), ('导出全库台账…', self.export_ledger),
                          ('查重清理', self.show_duplicates), ('清理重复文件…', self.dedupe_cleanup),
                          ('问题文件管理', self.problem_files),
                          ('分类自查与修正', self.category_audit), ('分类规则管理', self.category_rules),
                          ('选择两个文件对比…', self.compare_versions), ('自动配对版本对比…', self.compare_auto),
                          ('标准版本检查', self.standard_check)]:
            action=menu.addAction(title, fn)
            if title=='批量编辑信息…':action.setEnabled(count>0)
            elif title=='选择两个文件对比…':action.setEnabled(count==2)
            elif title=='自动配对版本对比…':action.setEnabled(count==1)
        return menu

    def open_library_original(self):
        rows = self.selected_library_rows()
        if rows:
            QDesktopServices.openUrl(QUrl.fromLocalFile(rows[0]['path']))

    def reveal_library_file(self):
        rows = self.selected_library_rows()
        if rows:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(rows[0]['path']).parent)))

    def reindex_selection(self):
        paths = [row['path'] for row in self.selected_library_rows()]
        if paths:
            def work(progress, stop):
                statuses = []
                with self.store.write_lock:
                    for number, path in enumerate(paths, 1):
                        if stop.is_set():
                            break
                        status = indexer.index_single_file(path, self.store.db)
                        statuses.append(status)
                        progress({'current': number, 'total': len(paths), 'path': path, 'status': status})
                return {'processed': len(statuses), 'failed': sum(str(s).startswith('error') for s in statuses)}
            self.jobs.submit('重新索引文件', work, self.after_index, error=self.show_error, mutating=True)

    def remove_library_selection(self):
        rows = self.selected_library_rows()
        if rows and QMessageBox.question(self, '从索引中移除',
            f'移除选中的 {len(rows)} 条索引？磁盘上的原文件会保留。') == QMessageBox.StandardButton.Yes:
            paths = [row['path'] for row in rows]
            def work(progress, stop):
                with self.store.write_lock:
                    for path in paths:
                        if stop.is_set():
                            break
                        result = indexer.remove_from_library(self.store.db, path, delete_file=False)
                        if result.startswith('error'):
                            raise RuntimeError(result)
            self.jobs.submit('移除索引', work, lambda _: (self.load_library(), self.refresh_stats()),
                             error=self.show_error, mutating=True)

    def rename_file(self):
        rows = self.selected_library_rows()
        if not rows:
            return
        row = rows[0]
        name, ok = QInputDialog.getText(self, '重命名文件', '文件名（含扩展名）', text=row['name'])
        if ok and name.strip() and name != row['name']:
            def done(result):
                self.notify('已重命名' if result == 'ok' else str(result))
                self.load_library()
            self.jobs.submit('重命名文件', lambda progress, stop: self.store.write(
                indexer.rename_in_library, row['path'], name), done, error=self.show_error, mutating=True)

    def edit_metadata(self):
        rows = self.selected_library_rows()
        if not rows:
            self.notify('请先选择文件')
            return
        row = rows[0]
        dialog = QDialog(self)
        dialog.setWindowTitle('编辑资料信息')
        dialog.resize(520, 300)
        form = QFormLayout(dialog)
        form.addRow('文件', label(row['name']))
        fields = {}
        for key, text in [('doc_no', '文号'), ('effective_date', '施行日期'), ('category', '分类')]:
            fields[key] = QLineEdit(row.get(key) or '')
            if key=='effective_date':fields[key].setPlaceholderText('YYYY-MM-DD，例如 2026-10-30；留空可清除')
            form.addRow(text, fields[key])
        status = QComboBox()
        for value, text in STATUS.items():
            status.addItem(text, value)
        status.setCurrentIndex(max(0, status.findData(row['status'])))
        form.addRow('状态', status)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        date_error=label('','muted')
        date_error.hide()
        form.addRow(date_error)
        def accept():
            from service import valid_date
            value=fields['effective_date'].text().strip()
            if value and not valid_date(value):
                date_error.setText('请输入有效日期，例如 2026-10-30，或留空清除。')
                date_error.show()
                fields['effective_date'].setFocus()
                return
            dialog.accept()
        controls.accepted.connect(accept)
        controls.rejected.connect(dialog.reject)
        form.addRow(controls)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            values = {key: field.text().strip() for key, field in fields.items()}
            values['status'] = status.currentData()
            def done(result):
                if result is not True:
                    self.notify(str(result))
                    return
                self.load_library()
                if values['status'] in ('void', 'replaced'):
                    self.warn_incoming([row['path']])
            self.jobs.submit('保存资料信息', lambda progress, stop: self.store.write(
                indexer.update_metadata, row['path'], **values), done,
                error=self.show_error, mutating=True)

    def show_duplicates(self):
        def done(groups):
            rows = []
            for group in groups:
                keep = group['keep']
                for item in group['dups']:
                    rows.append(dict(item, reason=group['reason'], keep=keep['path']))
            self.records_dialog('重复文件检查', [('文件', 'name'), ('原因', 'reason'),
                ('建议保留', 'keep'), ('文件路径', 'path')], rows,
                lambda row: (self.navigate('search'), self.open_preview(row['path'], 1)))
        self.jobs.submit('检查重复文件', lambda progress, stop: indexer.find_duplicates(self.store.db), done, error=self.show_error)

    def dedupe_cleanup(self):
        def done(groups):
            if not groups:
                self.notify('未发现可清理的重复文件')
                return
            dialog = QDialog(self)
            dialog.setWindowTitle('清理重复文件')
            dialog.resize(880, 660)
            layout = QVBoxLayout(dialog)
            layout.addWidget(label(f'共 {len(groups)} 组重复。勾选要移除的重复项（每组保留建议保留的一份；仅移除索引，磁盘文件不动）。', 'muted'))
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            body = QWidget()
            box = QVBoxLayout(body)
            box.setSpacing(8)
            checks = []
            for group in groups:
                keep = group['keep']
                head = QLabel(f"<b>{html.escape(group['reason'])}</b>　保留：{html.escape(keep['name'])}（{keep['size']} 字节）")
                head.setWordWrap(True)
                box.addWidget(head)
                for item in group['dups']:
                    c = QCheckBox(f"{item['name']}（{item['size']} 字节）　{item['path']}")
                    c.setChecked(True)
                    c.dup_path = item['path']
                    checks.append(c)
                    box.addWidget(c)
            box.addStretch()
            scroll.setWidget(body)
            layout.addWidget(scroll, 1)
            buttons = QDialogButtonBox()
            ok = buttons.addButton('移除所选重复项', QDialogButtonBox.ButtonRole.AcceptRole)
            buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            def commit():
                paths = [c.dup_path for c in checks if c.isChecked()]
                if not paths:
                    self.notify('未选择任何重复项')
                    return
                dialog.accept()
                def work(progress, stop):
                    removed = 0
                    with self.store.write_lock:
                        for path in paths:
                            if stop.is_set():
                                break
                            result = indexer.remove_from_library(self.store.db, path, delete_file=False)
                            if result == 'ok':
                                removed += 1
                            elif result.startswith('error'):
                                raise RuntimeError(result)
                    return removed
                def finished(removed):
                    self.notify(f'已移除 {removed} 条重复索引，磁盘文件已保留')
                    self.load_library()
                    self.refresh_stats()
                self.jobs.submit('清理重复文件', work, finished, error=self.show_error, mutating=True)
            ok.clicked.connect(lambda *_: commit())
            self.keep_dialog(dialog)
        self.jobs.submit('检查重复文件', lambda progress, stop: indexer.find_duplicates(self.store.db), done, error=self.show_error)

    def show_missing(self):
        self.jobs.submit('检查失联文件', lambda progress, stop: indexer.find_missing_documents(self.store.db),
            lambda rows: self.records_dialog('失联文件', [('文件', 'name'), ('行数', 'line_count'),
                ('最近索引', lambda r: self.date_text(r['indexed_at'])), ('原路径', 'path')], rows,
                lambda row: (self.navigate('search'), self.open_preview(row['path'], 1))), error=self.show_error)

    def category_audit(self):
        def done(report):
            rows = report['uncategorized'] + report['mismatch']
            self.records_dialog('分类自查 · 双击预览后可在资料库编辑分类', [
                ('文件', 'name'), ('当前分类', 'current'), ('建议分类', 'suggested'), ('路径', 'path')], rows,
                lambda row: (self.navigate('search'), self.open_preview(row['path'], 1)))
        self.jobs.submit('分类自查', lambda progress, stop: indexer.category_audit(self.store.db), done, error=self.show_error)

    def category_rules(self):
        def done(rules):
            dialog = QDialog(self)
            dialog.setWindowTitle('分类规则')
            dialog.resize(760, 560)
            layout = QVBoxLayout(dialog)
            layout.addWidget(label('每行：分类名称 = 关键词1, 关键词2', 'muted'))
            editor = QPlainTextEdit()
            editor.setPlainText('\n'.join(str(rule[0]) + ' = ' + ', '.join(map(str, rule[1:])) for rule in rules))
            layout.addWidget(editor)
            controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
            controls.rejected.connect(dialog.close)
            def save():
                values = []
                for line in editor.toPlainText().splitlines():
                    if not line.strip():
                        continue
                    if '=' not in line:
                        self.notify('每行规则需要包含 =')
                        return
                    name, words = line.split('=', 1)
                    keywords = [word.strip() for word in re.split('[,，]', words) if word.strip()]
                    if not name.strip() or not keywords:
                        self.notify('分类名称和关键词不能为空')
                        return
                    values.append([name.strip()] + keywords)
                if not values:
                    self.notify('请至少保留一条规则')
                    return
                def saved(result):
                    if result is not True:
                        self.notify(str(result))
                        return
                    dialog.close()
                    self.notify('分类规则已保存')
                self.jobs.submit('保存分类规则', lambda progress, stop: self.store.write(
                    indexer.save_category_rules, values), saved, error=self.show_error, mutating=True)
            controls.accepted.connect(save)
            layout.addWidget(controls)
            self.keep_dialog(dialog)
        self.jobs.submit('读取分类规则', lambda progress, stop: indexer.get_category_rules(self.store.db), done, visible=False)

    def compare_versions(self):
        rows = self.selected_library_rows()
        if len(rows) != 2:
            self.notify('请在资料库中按住 Ctrl 选择两个文件，再进行版本对比')
            return
        a, b = rows
        self.compare_pair(a, b)

    def compare_pair(self, a, b):
        from .comparison_ui import show_comparison
        self.jobs.submit('版本对比',lambda progress,stop:searcher.diff_documents(self.store.db,a['path'],b['path']),
            lambda blocks:show_comparison(self,a,b,blocks),error=self.show_error)

    def standard_check(self):
        import updater
        self.jobs.submit('标准版本检查', lambda progress, stop: updater.scan_local(self.store.db),
            lambda report: self.records_dialog('标准版本检查 · 双击查询新版', [
                ('文件', 'name'), ('标准号', 'std_code'), ('状态', lambda r: STATUS.get(r['status'], r['status'])),
                ('路径', 'path')], report['files'], lambda row: QDesktopServices.openUrl(
                    QUrl(updater.build_search_url(row['std_code'])))), error=self.show_error)

    def show_references(self):
        if not self.current:
            self.notify('请先打开一份资料')
            return
        path = self.current['path']
        def work(progress, stop):
            outgoing = searcher.outgoing_refs(self.store.db, path)
            incoming = searcher.incoming_refs(self.store.db, path)
            rows = [dict(row, direction='引用了', dest=row['target_path'], line=row['target_line'] or 1) for row in outgoing]
            rows += [dict(row, direction='被引用', dest=row['path'], line=row['line_no']) for row in incoming]
            return rows
        self.jobs.submit('读取引用关系', work, lambda rows: self.records_dialog('文档引用关系', [
            ('文件 / 条款', lambda r: r.get('title') or r.get('name')), ('方向', 'direction'),
            ('条款', 'clause'), ('路径', 'dest')], rows,
            lambda row: (self.navigate('search'), self.open_preview(row['dest'], row['line'])) if row.get('dest') else
                self.notify('引用资料尚未入库，请先添加对应资料')), error=self.show_error)

    def graph_view(self):
        def work(progress, stop):
            import graph_html
            html, stats = graph_html.generate_graph(self.store.db)
            output = self.store.root / '引用关系图-新版.html'
            output.write_text(html, encoding='utf-8')
            return str(output)
        self.jobs.submit('生成引用关系图', work,
            lambda path: QDesktopServices.openUrl(QUrl.fromLocalFile(path)), error=self.show_error)

    def show_errors(self):
        def done(rows):
            dialog = self.records_dialog('解析错误', [('文件', 'path'), ('阶段', 'stage'), ('错误', 'error'),
                ('时间', lambda r: self.date_text(r['ts']))], rows,
                lambda row: self.jobs.submit('读取错误详情', lambda progress, stop: indexer.get_error_traceback(
                    self.store.db, row['id']), lambda text: self.text_dialog('错误详情', text or row['error']), visible=False))
            actions = QHBoxLayout()
            def retry():
                selected = dialog.table.selectionModel().selectedRows()
                paths = list(dict.fromkeys(dialog.model.rows[idx.row()]['path'] for idx in selected))
                paths = [path for path in paths if Path(path).is_file()]
                if paths:
                    self.index_files(paths)
            actions.addWidget(button('重试所选文件', retry))
            actions.addStretch()
            dialog.layout_box.addLayout(actions)
        self.jobs.submit('读取解析错误', lambda progress, stop: indexer.get_errors(self.store.db), done, visible=False)

    def ocr_diagnostics(self):
        def done(status):
            dialog=QDialog(self)
            dialog.setWindowTitle('OCR 组件诊断')
            dialog.resize(720,460)
            layout=QVBoxLayout(dialog)
            available=bool(status['rapid'] or status['tesseract'])
            layout.addWidget(label('扫描件文字识别可用' if available else '扫描件文字识别暂不可用','section'))
            summary=label('可识别扫描 PDF 和图片中的文字，识别后加入正文索引。' if available else
                '请检查随软件提供的识别组件，或安装 Tesseract 后重新检测。')
            summary.setWordWrap(True)
            layout.addWidget(summary)
            layout.addWidget(label('主引擎：'+('RapidOCR，已就绪' if status['rapid'] else '未就绪')))
            layout.addWidget(label('回退引擎：'+('Tesseract，已就绪' if status['tesseract'] else '未安装')))
            details=json.dumps(status,ensure_ascii=False,indent=2,default=str)
            toggle=button('展开组件详情')
            toggle.setCheckable(True)
            layout.addWidget(toggle)
            body=QPlainTextEdit(details)
            body.setReadOnly(True)
            body.hide()
            toggle.toggled.connect(body.setVisible)
            toggle.toggled.connect(lambda visible:toggle.setText('收起组件详情' if visible else '展开组件详情'))
            layout.addWidget(body,1)
            controls=QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            controls.addButton(button('复制诊断',lambda:QApplication.clipboard().setText(details)),QDialogButtonBox.ButtonRole.ActionRole)
            controls.rejected.connect(dialog.close)
            layout.addWidget(controls)
            self.keep_dialog(dialog)
        self.jobs.submit('检测 OCR 组件',lambda p,s:__import__('parsers').ocr_status(),done,error=self.show_error)

    def backup_library(self):
        target, selected = QFileDialog.getSaveFileName(self, '备份资料库',
            f'镜仔搜索备份-{datetime.now():%Y%m%d-%H%M}.db', '索引备份 (*.db);;完整资料包 (*.zip)')
        if not target:
            return
        if Path(target).resolve() == Path(self.store.db).resolve():
            self.notify('备份文件需要使用另一个名称')
            return
        if selected.startswith('完整') or target.lower().endswith('.zip'):
            def work(progress, stop):
                return indexer.package_library(target, db_path=self.store.db, app_dir=str(self.store.root),
                    stop_event=stop, progress=lambda done, total, name: progress(
                        {'current': done, 'total': total, 'path': name}))
        else:
            work = lambda progress, stop: self.store.backup(target, progress, stop)
        self.jobs.submit('备份资料库', work, lambda _: self.notify('备份已保存：' + target), error=self.show_error)

    def settings_dialog(self):
        from .settings_ui import show_settings
        return show_settings(self)

    def restore_package(self):
        archive, _ = QFileDialog.getOpenFileName(self, '选择完整资料包', '', '资料包 (*.zip)')
        if not archive:
            return
        destination = QFileDialog.getExistingDirectory(self, '选择一个空文件夹用于还原资料')
        if not destination:
            return
        target = Path(destination).resolve()
        if any(target.iterdir()):
            self.notify('请选择空文件夹，用于保存还原后的资料库')
            return
        def work(progress, stop):
            import shutil
            if any(target.iterdir()):
                raise RuntimeError('目标文件夹已包含文件，未执行还原')
            result = indexer.restore_library(archive, app_dir=str(target), stop_event=stop,
                progress=lambda done, total, name: progress({'current': done, 'total': total, 'path': name}))
            stage = Path(result['staging'])
            items = list(stage.iterdir())
            if any((target / item.name).exists() for item in items):
                raise RuntimeError('目标位置已有同名文件，已保留暂存资料')
            for item in items:
                shutil.move(str(item), str(target / item.name))
            stage.rmdir()
            return str(target)
        def done(folder):
            self.notify('资料包已还原到：' + folder)
            if QMessageBox.question(self, '资料包已还原',
                '是否在新窗口中打开还原后的资料库？') == QMessageBox.StandardButton.Yes:
                import sys
                arguments = ['--data-dir', folder]
                if not getattr(sys, 'frozen', False):
                    arguments.insert(0, str(Path(__file__).parents[1] / 'qt_main.py'))
                QProcess.startDetached(sys.executable, arguments)
        self.jobs.submit('还原完整资料包', work, done, error=self.show_error)

    def search_actions_menu(self):
        self.build_search_actions_menu().exec(self.result_actions.mapToGlobal(self.result_actions.rect().bottomLeft()))

    def build_search_actions_menu(self):
        menu = QMenu(self)
        filter_action = menu.addAction('结果内筛选')
        filter_action.setCheckable(True)
        filter_action.setChecked(not self.result_filter.isHidden())
        filter_action.toggled.connect(self.result_filter.setVisible)
        menu.addAction(self.chart_toggle)
        scope = self.discovery_scope_kind()
        sorts = ([('正文结果排序', self.sort_order), ('文件目录排序', self.catalog_sort)] if scope == 'both'
                 else [('排序', self.catalog_sort if scope == 'dir' else self.sort_order)])
        for title, combo in sorts:
            sort_menu = menu.addMenu(title)
            for index in range(combo.count()):
                action = sort_menu.addAction(combo.itemText(index))
                action.setCheckable(True)
                action.setChecked(combo.currentIndex()==index)
                action.triggered.connect(lambda checked=False,i=index,c=combo:c.setCurrentIndex(i))
        menu.addSeparator()
        for title, fn in [('导出搜索结果', self.export_results), ('文档引用关系', self.show_references),
                          ('引用关系图', self.graph_view), ('AI 分析当前搜索结果', self.send_results_to_qa),
                          ]:
            menu.addAction(title, fn)
        return menu

    def _ai_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0,0,0,0)
        self.ai_question = self.search_input
        row = QHBoxLayout()
        self.ai_kind = QComboBox()
        self.ai_kind.addItems(['基于资料','普通聊天'])
        row.addWidget(self.ai_kind,1)
        self.ai_stop = button('停止', self.stop_ai)
        self.ai_stop.setEnabled(False)
        self.ai_stop.hide()
        row.addWidget(self.ai_stop)
        layout.addLayout(row)
        self.ai_use_results = QCheckBox('使用当前搜索结果作为依据')
        self.ai_use_results.setChecked(True)
        self.ai_kind.currentIndexChanged.connect(self.update_ai_layout)
        layout.addWidget(self.ai_use_results)
        self.ai_status = label('准备就绪 · 输入问题后按回车发送', 'muted')
        self.ai_status.setWordWrap(True)
        layout.addWidget(self.ai_status)
        self.ai_submitted = label('', 'muted')
        self.ai_submitted.setTextFormat(Qt.TextFormat.PlainText)
        self.ai_submitted.setWordWrap(True)
        self.ai_submitted.setMaximumHeight(90)
        self.ai_submitted.hide()
        layout.addWidget(self.ai_submitted)
        self.ai_notice = label('', 'muted')
        self.ai_notice.setObjectName('answerNotice')
        self.ai_notice.setWordWrap(True)
        self.ai_notice.setTextFormat(Qt.TextFormat.PlainText)
        self.ai_notice.hide()
        layout.addWidget(self.ai_notice)
        recovery = QHBoxLayout()
        self.ai_search_recovery = button('先搜索正文', lambda: self.search_mode.setCurrentIndex(0))
        self.ai_library_recovery = button('改用全库资料', self.ask_from_library)
        for control in (self.ai_search_recovery, self.ai_library_recovery):
            control.hide()
            recovery.addWidget(control)
        layout.addLayout(recovery)
        self.ai_answer = AnswerView()
        self.ai_answer.setReadOnly(True)
        self.ai_answer.setPlaceholderText('先搜索相关资料，再在这里提出问题。回答过程中可以继续使用其他功能。')
        layout.addWidget(self.ai_answer, 1)
        self.ai_sources = QListWidget()
        self.ai_sources.setMaximumHeight(150)
        self.ai_sources.hide()
        self.ai_sources.itemActivated.connect(lambda item: self._open_ai_source(item))
        self.ai_sources.itemClicked.connect(lambda item: self._open_ai_source(item))
        layout.addWidget(self.ai_sources)
        actions = QHBoxLayout()
        self.ai_copy = button('复制回答', self.copy_ai_answer)
        self.ai_copy.setEnabled(False)
        self.ai_answer.textChanged.connect(lambda: self.ai_copy.setEnabled(bool(self.ai_answer.toPlainText().strip())))
        self.ai_retry = button('重试', self.retry_ai)
        self.ai_retry.setEnabled(False)
        self.ai_new = button('新对话', self.clear_ai_session)
        for item in (self.ai_copy, self.ai_retry, self.ai_new):
            item.setMaximumWidth(110)
            actions.addWidget(item)
        actions.addStretch()
        layout.addLayout(actions)
        settings = QMenu(page)
        settings.addAction('模型与完整智答工作台',lambda:self.legacy_tool('qa'))
        settings.addAction('更新资料语义索引',self.build_semantic)
        cpu = settings.addAction('兼容模式（使用 CPU）')
        cpu.setCheckable(True)
        cpu.setChecked(bool(self.store.settings.get('qa_cpu_only', False)))
        cpu.setToolTip('显卡初始化失败时可启用；通常比显卡推理慢')
        def set_cpu(enabled):
            self.store.settings['qa_cpu_only'] = enabled
            self.store.save()
        cpu.toggled.connect(set_cpu)
        options = button('智答设置')
        options.setMenu(settings)
        options.setMaximumWidth(160)
        layout.addWidget(options)
        self.ai_job = None
        self._ai_generation = 0
        self._ai_history = []
        self._ai_last_request = None
        self._ai_source_paths = set()
        return page

    def update_ai_layout(self,*_):
        chat=self.ai_kind.currentIndex()==1
        self.ai_use_results.setVisible(not chat)
        self.ai_answer.setPlaceholderText('输入问题开始聊天；本模式不检索资料库。' if chat else
            '先搜索相关资料，或使用全库资料提问；回答的参考来源会列在下方。')
        if not self.discovery_is_explore():
            self.splitter.default_sizes=(520,700)
            if chat and self.preview_panel.isVisible():
                self._ai_reference_sizes=self.splitter.sizes()
            self.preview_panel.setVisible(not chat)
            if not chat:
                self.splitter.setSizes(getattr(self,'_ai_reference_sizes',[520,700]))
            self.ai_input.setPlaceholderText('普通聊天 · Enter 发送，Shift+Enter 换行' if chat else '基于资料提问 · Enter 发送，Shift+Enter 换行')
            self.ai_input.setFocus()
            self.result_summary.setText('普通聊天不检索资料库' if chat else '选择来源可在右侧查看参考资料')
        if self._ai_last_request and self._ai_last_request['chat']!=chat:
            self.ai_status.setText('已切换模式 · 下方保留上轮回答，发送新问题后更新')
        self.update_reader_context()

    def ask_from_library(self):
        if self._ai_last_request:
            self.ai_use_results.setChecked(False)
            request = copy.deepcopy(self._ai_last_request)
            request.update(use_results=False, hits=[], chat=False)
            self.ask_ai(_request=request)

    def copy_ai_answer(self):
        text = self.ai_answer.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            self.notify('已复制回答')

    def retry_ai(self):
        if self._ai_last_request:
            self.ai_kind.setCurrentIndex(1 if self._ai_last_request['chat'] else 0)
            self.ai_use_results.setChecked(self._ai_last_request['use_results'])
            request = copy.deepcopy(self._ai_last_request)
            for key in request['settings']:
                request['settings'][key] = self.store.settings.get(key, request['settings'][key])
            self.ask_ai(_request=request)

    def clear_ai_session(self):
        self.stop_ai()
        self._ai_history = []
        self._ai_last_request = None
        self.ai_retry.setEnabled(False)
        self.ai_answer.clear()
        self.ai_notice.clear()
        self.ai_notice.hide()
        self.ai_search_recovery.hide()
        self.ai_library_recovery.hide()
        self._ai_source_paths = set()
        self.ai_sources.clear()
        self.ai_sources.hide()
        self.ai_submitted.clear()
        self.ai_submitted.hide()
        self.ai_status.setText('新对话已准备好 · 输入问题后按回车发送')
        self.update_reader_context()
        if not self.discovery_is_explore():
            self.search_input.clear()
            self.search_input.setFocus()

    def _open_ai_source(self, item):
        hit = item.data(Qt.ItemDataRole.UserRole)
        if getattr(self,'_current_page','search')!='ai':
            self.navigate('search')
        self.open_preview(hit['path'], hit.get('line_no', 1))

    def ask_ai(self, *, _request=None):
        from export import build_search_hits
        question = _request['question'] if _request else self.ai_question.text().strip()
        if not question:
            return
        if self.ai_job in self.jobs.active:
            self.stop_ai()
        self._ai_generation += 1
        generation = self._ai_generation
        if _request:
            request = copy.deepcopy(_request)
        else:
            chat = self.ai_kind.currentIndex()==1
            use_results = self.ai_use_results.isChecked()
            request = dict(question=question, chat=chat, use_results=use_results,
                hits=build_search_hits(self.groups, {}) if use_results and not chat else [],
                history=list(self._ai_history[-8:]) if chat else [],
                settings={key: self.store.settings.get(key, default) for key, default in (
                    ('ollama_base', 'http://127.0.0.1:11434'), ('ollama_chat', 'qwen2.5:7b-instruct'),
                    ('ollama_embed', 'bge-m3'), ('qa_cpu_only', False))})
        self._ai_last_request = copy.deepcopy(request)
        chat, use_results, history, settings = (request[key] for key in ('chat', 'use_results', 'history', 'settings'))
        self.ai_answer.clear()
        self.ai_notice.clear()
        self.ai_notice.hide()
        self.ai_search_recovery.hide()
        self.ai_library_recovery.hide()
        self._ai_source_paths = set()
        self.update_reader_context()
        self.ai_sources.clear()
        self.ai_sources.hide()
        self.ai_stop.setEnabled(True)
        self.ai_stop.show()
        self.ai_kind.setEnabled(False)
        self.ai_use_results.setEnabled(False)
        self.ai_retry.setEnabled(False)
        self.ai_status.setText('正在连接模型…' if chat else '正在准备引用资料…')
        self.ai_submitted.setText('本轮问题：' + question)
        self.ai_submitted.setToolTip(question)
        self.ai_submitted.show()
        def work(progress, stop):
            import ai_qa
            base = settings.get('ollama_base', 'http://127.0.0.1:11434')
            if chat:
                hits = []
            elif request['hits']:
                hits = request['hits']
            elif use_results:
                raise RuntimeError('当前结果没有可引用的正文。请先检索资料正文，或取消“使用当前搜索结果”后检索资料库。')
            else:
                hits = ai_qa.semantic_search(self.store.db, base, settings.get('ollama_embed', 'bge-m3'), question)
            if not hits and not chat:
                raise RuntimeError('没有找到可引用资料，请先搜索相关条款或构建语义索引')
            progress({'sources': hits})
            messages = ([{'role':'system','content':'请用中文准确回答用户的问题。'},*history,
                         {'role':'user','content':question}] if chat else ai_qa.build_messages(question, hits))
            full, buffered, last = '', '', time.monotonic()
            runtime_options = {'options': {'num_gpu': 0, 'num_ctx': 2048}} if settings.get('qa_cpu_only') else {}
            try:
                for chunk in ai_qa.chat_stream(base, settings.get('ollama_chat', 'qwen2.5:7b-instruct'),
                                              messages, should_stop=stop.is_set, **runtime_options):
                    full += chunk
                    buffered += chunk
                    if time.monotonic() - last > .06:
                        progress({'text': buffered})
                        buffered, last = '', time.monotonic()
            finally:
                if buffered:
                    progress({'text': buffered})
            return full
        def progress(value):
            if generation != self._ai_generation:
                return
            if 'text' in value:
                self.ai_status.setText('正在生成回答…')
                self.ai_answer.append_chunk(value['text'])
            if 'sources' in value:
                self.ai_status.setText('正在连接模型…')
                self._ai_last_request['hits'] = copy.deepcopy(value['sources'])
                self._ai_source_paths = {os.path.normcase(os.path.abspath(hit['path'])) for hit in value['sources']}
                self.update_reader_context()
                for number, hit in enumerate(value['sources'], 1):
                    position = f"第 {hit['page']} 页" if hit.get('page') else f"第 {hit.get('line_no',1)} 行"
                    snippet = str(hit.get('text') or hit.get('content') or '').replace('\n',' ')[:90]
                    item = QListWidgetItem(f"[{number}] {hit.get('name') or Path(hit['path']).name} · {position}" + ('\n'+snippet if snippet else ''))
                    item.setToolTip(hit['path']+'\n'+str(hit.get('text') or hit.get('content') or ''))
                    item.setData(Qt.ItemDataRole.UserRole, hit)
                    self.ai_sources.addItem(item)
                self.ai_sources.setVisible(self.ai_sources.count() > 0)
        def done(answer):
            if generation==self._ai_generation:
                self.ai_stop.setEnabled(False)
                self.ai_stop.hide()
                self.ai_kind.setEnabled(True)
                self.ai_use_results.setEnabled(True)
                self.ai_answer.finish_markdown()
                self.ai_retry.setEnabled(True)
                self.ai_status.setText('回答完成' + (f' · {self.ai_sources.count()} 个来源' if not chat else ''))
                if chat:
                    self._ai_history = [*history, {'role':'user','content':question},{'role':'assistant','content':answer}]
        def failed(detail):
            if generation==self._ai_generation:
                self.ai_stop.setEnabled(False)
                self.ai_stop.hide()
                self.ai_kind.setEnabled(True)
                self.ai_use_results.setEnabled(True)
                self.ai_retry.setEnabled(True)
                self.ai_status.setText('回答未完成 · 可重试或在“智答设置”检查模型')
                if not chat and use_results and not request['hits']:
                    self.ai_status.setText('缺少正文依据 · 请先在探索中搜索资料正文')
                    self.ai_notice.setText('当前结果没有可引用的正文。可先搜索正文，或改用全库资料。')
                    self.ai_search_recovery.show()
                    self.ai_library_recovery.show()
                elif 'OllamaUnavailable' in detail:
                    self.ai_status.setText('模型服务暂不可用 · 启动服务后可重试')
                    self.ai_notice.setText('无法连接模型服务。请在“智答设置”检查服务地址与模型。本轮问题与来源已保留，可重试。')
                else:
                    reason = detail.strip().splitlines()[-1].split(': ', 1)[-1]
                    hint = ('显卡初始化失败时，可在“智答设置”启用“兼容模式（使用 CPU）”后重试。'
                            if any(term in detail.lower() for term in ('cuda', 'gpu', 'llama-server')) else
                            '可在“智答设置”检查模型配置，再点击“重试”。')
                    self.ai_notice.setText(reason+'\n'+hint)
                self.ai_notice.show()
                if self.ai_answer.toPlainText().strip():
                    self.ai_status.setText('回答未完成 · 以下内容为部分生成，可复制或重试')
        self.ai_job = self.jobs.submit('智答', work, done, progress=progress,error=failed)

    def stop_ai(self):
        active = self.ai_job in self.jobs.active
        self._ai_generation += 1
        if self.ai_job:
            self.jobs.cancel(self.ai_job)
        self.ai_stop.setEnabled(False)
        self.ai_stop.hide()
        self.ai_kind.setEnabled(True)
        self.ai_use_results.setEnabled(True)
        self.ai_retry.setEnabled(bool(self._ai_last_request))
        if active:
            self.ai_status.setText('已停止 · 已生成的回答和来源保留，可重新提问')

    def build_semantic(self):
        settings = dict(self.store.settings)
        def work(progress, stop):
            import ai_qa
            with self.store.write_lock:
                return ai_qa.build_semantic_index(self.store.db,
                    settings.get('ollama_base', 'http://127.0.0.1:11434'), settings.get('ollama_embed', 'bge-m3'),
                    progress_cb=lambda *args: progress({'status': ' · '.join(map(str, args))}), cancel_event=stop)
        self.jobs.submit('构建语义索引', work, lambda _: self.notify('语义索引已更新'), error=self.show_error, mutating=True)

    def web_search(self):
        import websearch
        dialog = QDialog(self)
        dialog.setWindowTitle('联网检索资料')
        dialog.resize(1050, 660)
        layout = QVBoxLayout(dialog)
        row = QHBoxLayout()
        query = QLineEdit(self.search_input.text())
        query.setPlaceholderText('查找法规、政策或标准…')
        row.addWidget(query, 1)
        source = QComboBox()
        for item in websearch.SOURCES:
            source.addItem(item['name'], item)
        row.addWidget(source)
        run = button('搜索', primary=True)
        row.addWidget(run)
        layout.addLayout(row)
        model = Records([('标题', lambda r: r.get('title') or r.get('name')), ('来源', 'source'),
                         ('日期', lambda r: r.get('date') or r.get('publish_date')), ('链接', 'url')], dialog)
        table = records_table(model)
        table.setColumnWidth(0, 540)
        layout.addWidget(table, 1)
        note = label('双击打开官方原文，也可下载后加入资料库。', 'muted')
        layout.addWidget(note)
        def open_row(*_):
            rows = table.selectionModel().selectedRows()
            if rows:
                item = model.rows[rows[0].row()]
                url = websearch.open_url_of(item)
                if url:
                    QDesktopServices.openUrl(QUrl(url))
        table.doubleClicked.connect(open_row)
        def search():
            text, selected = query.text().strip(), source.currentData()
            if not text:
                return
            if not selected['has_api']:
                QDesktopServices.openUrl(QUrl(websearch.get_jump_url(selected['id'], text)))
                return
            run.setEnabled(False)
            def work(progress, stop):
                result, errors = websearch.search_all(text, [selected['id']])
                items = [item for values in result.values() for item in values]
                if not items and errors:
                    raise RuntimeError('；'.join(str(error) for error in errors.values()))
                return items
            def done(items):
                if isValid(dialog) and dialog.isVisible():
                    model.set_rows(items)
                    note.setText(f'找到 {len(items)} 条结果')
                    run.setEnabled(True)
            def failed(detail):
                if isValid(dialog) and dialog.isVisible():
                    run.setEnabled(True)
                    note.setText(detail.strip().splitlines()[-1])
            self.jobs.submit('联网检索：' + text, work, done, error=failed)
        run.clicked.connect(search)
        query.returnPressed.connect(search)
        download = button('下载并加入资料库')
        def download_selected():
            rows = table.selectionModel().selectedRows()
            if not rows:
                return
            item = model.rows[rows[0].row()]
            target = self.store.root / 'web_downloads'
            target.mkdir(exist_ok=True)
            self.jobs.submit('下载官方资料', lambda progress, stop: websearch.download_item(item, str(target)),
                lambda result: self.confirm_import([result[0] if isinstance(result, tuple) else result]), error=self.show_error)
        download.clicked.connect(download_selected)
        layout.addWidget(download)
        self.keep_dialog(dialog)

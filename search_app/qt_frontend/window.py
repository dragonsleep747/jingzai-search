from __future__ import annotations

import base64
from datetime import datetime
from functools import partial
import os
from pathlib import Path
import re
import time

from PySide6.QtCore import QByteArray, QEvent, QSize, QTimer, Qt, QUrl, Signal, QSignalBlocker
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QProgressBar,
    QPushButton, QSplitter, QStackedWidget, QStyle, QSystemTrayIcon, QTextBrowser,
    QToolBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

import core
import indexer
import searcher
from service import fmt_size

from .backend import STATUS, Store, export_csv, preview_html
from .jobs import Jobs
from .widgets import DistributionChart, Records, button, label, page_heading, panel, records_table, stylesheet
from .tools import ToolsMixin
from .parity import ParityMixin
from .search_experience import SearchExperienceMixin
from .pdf_reader import PdfReader
from .minimal_ui import MinimalUiMixin, SEARCH_CONTROL_HEIGHT
from .discovery_ui import DiscoveryUiMixin


_CATEGORY_PREFIX = re.compile(r'^\d{1,3}[-_]\s*')


def _category_label(value):
    return _CATEGORY_PREFIX.sub('', str(value or '').strip())


def _duplicate_groups(rows):
    counts = {}
    for row in rows:
        name = str(row.get('name') or '')
        stem = re.sub(r'_\d+$', '', Path(name).stem).casefold()
        if stem:
            counts[stem] = counts.get(stem, 0) + 1
    return sum(1 for count in counts.values() if count > 1)


class Window(DiscoveryUiMixin, MinimalUiMixin, SearchExperienceMixin, ParityMixin, ToolsMixin, QMainWindow):
    filesystem_changed = Signal(str, str)

    def __init__(self, store: Store):
        super().__init__()
        from .localization import install_chinese
        install_chinese(QApplication.instance())
        self.store = store
        self.jobs = Jobs(self)
        self.groups = []
        self.current = None
        self.query = ''
        self.search_job = self.preview_job = None
        self._search_generation = self._preview_generation = 0
        self._dialogs = []
        self._task_items = {}
        self._history = []
        self._history_index = -1
        self._closing_requested = False
        self._close_choice = store.settings.get('close_behavior') if store.settings.get('close_behavior') in ('tray','exit') else None
        self._exit_backup_done = False
        self.watcher = None
        self._library = []
        self._bookmarks = []
        self.setWindowTitle('镜仔搜索 · 新版')
        self.setMinimumSize(940, 640)
        self.resize(1380, 880)
        icon_path = Path(getattr(__import__('sys'), '_MEIPASS', Path(__file__).parents[1])) / 'app_icon.ico'
        self.setWindowIcon(QIcon(str(icon_path)))
        self.setAcceptDrops(True)
        self._build()
        self.install_discovery()
        self.install_feature_menu()
        self.search_debounce = QTimer(self)
        self.search_debounce.setSingleShot(True)
        self.search_debounce.setInterval(320)
        self.search_debounce.timeout.connect(self.start_search)
        self.search_input.textEdited.connect(lambda _: self.search_debounce.start() if self.discovery_is_explore() else None)
        for field in self.advanced_fields.values():
            field.textEdited.connect(lambda _: self.search_debounce.start())
        self.mode.currentIndexChanged.connect(lambda _: self.schedule_search())
        self.type_filter.currentIndexChanged.connect(lambda _: self.schedule_search())
        self.active_filter.toggled.connect(lambda _: self.schedule_search())
        self.synonym_filter.toggled.connect(lambda _: self.schedule_search())
        self.case_filter.toggled.connect(lambda _: self.schedule_search())
        self._make_tray()
        self.jobs.changed.connect(self.task_changed)
        self.jobs.changed.connect(lambda _: self.update_menu_badge())
        self.jobs.settled.connect(self._jobs_settled)
        self.filesystem_changed.connect(self._watch_changed)
        self.apply_theme()
        geometry = self.store.settings.get('qt_geometry')
        if geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode('ascii')))
        splitter = self.store.settings.get('qt_splitter')
        if splitter and self.store.settings.get('qt_layout_version') == 3:
            self.splitter.restoreState(QByteArray.fromBase64(splitter.encode('ascii')))
        for key, fn in [('Ctrl+F', self.focus_search), ('F5', self.rescan_folder), ('F1', self.show_user_guide),
                        ('Alt+Left', self.go_back), ('Ctrl+E', self.export_results),
                        ('Ctrl+D', self.toggle_bookmark), ('F3', partial(self.next_hit, 1)),
                        ('Shift+F3', partial(self.next_hit, -1))]:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(fn)
        QTimer.singleShot(0, self.refresh_stats)
        QTimer.singleShot(150, self.start_watcher)
        self.maintenance_timer = QTimer(self)
        self.maintenance_timer.setSingleShot(True)
        self.maintenance_timer.timeout.connect(self.start_maintenance)
        self.maintenance_timer.start(1200)
        QTimer.singleShot(0, self.refresh_facets)

    def _build(self):
        root = QWidget()
        root.setObjectName('workspace')
        self.setCentralWidget(root)
        main = QHBoxLayout(root)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        self.sidebar = QWidget()
        self.sidebar.setObjectName('sidebar')
        self.sidebar.setFixedWidth(178)
        nav_layout = QVBoxLayout(self.sidebar)
        nav_layout.setContentsMargins(10, 18, 10, 12)
        nav_layout.setSpacing(10)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav = {}
        for key, title in [('search', '搜索'), ('library', '资料库'), ('bookmarks', '收藏')]:
            item = button(title)
            item.setObjectName('nav')
            item.setCheckable(True)
            item.clicked.connect(lambda checked=False, key=key: self.navigate(key))
            self.nav_group.addButton(item)
            self.nav[key] = item
            nav_layout.addWidget(item)
        nav_layout.addStretch()
        self.library_summary = label('正在读取资料库…', 'muted')
        self.library_summary.setWordWrap(True)
        self.library_summary.hide()
        self.menu_button = button('☰ 菜单', self.show_application_menu)
        self.menu_button.setObjectName('nav')
        self.menu_button.setToolTip('任务、工具、设置与帮助')
        nav_layout.addWidget(self.menu_button)
        main.addWidget(self.sidebar)
        self.pages = QStackedWidget()
        self.page_indices = {}
        for key, factory in [('search', self._search_page), ('library', self._library_page),
                             ('bookmarks', self._bookmarks_page), ('tasks', self._tasks_page),
                             ('tools', self._tools_page)]:
            self.page_indices[key] = self.pages.addWidget(factory())
        main.addWidget(self.pages, 1)
        self.nav['search'].setChecked(True)
        self._current_page = 'search'
        self.status = label('准备就绪', 'muted')
        self.statusBar().addWidget(self.status, 1)
        self.busy = QProgressBar()
        self.busy.setFixedWidth(140)
        self.busy.hide()
        self.statusBar().addPermanentWidget(self.busy)
        self.location_label = label('本地资料库', 'muted')
        self.location_label.setToolTip(str(self.store.root))
        self.statusBar().addPermanentWidget(self.location_label)
        self.statusBar().hide()
        self.notice = label('', 'notice')
        self.notice.setParent(self.pages)
        self.notice.setWordWrap(True)
        self.notice.setMaximumWidth(420)
        self.notice.hide()
        self.notice_timer = QTimer(self)
        self.notice_timer.setSingleShot(True)
        self.notice_timer.timeout.connect(self.notice.hide)

    def _page(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(26, 24, 26, 18)
        layout.setSpacing(16)
        return widget, layout

    def _search_page(self):
        return self.build_minimal_search_page()

    def _library_page(self):
        page, layout = self._page()
        layout.addWidget(page_heading('资料库', '统一管理已有资料，双击文件可预览。',
                                      [button('添加资料', self.add_menu, True)]))
        bar = QHBoxLayout()
        self.library_filter = QLineEdit()
        self.library_filter.setPlaceholderText('按文件名、文号、分类筛选…')
        self.library_filter.textChanged.connect(self.filter_library)
        bar.addWidget(self.library_filter, 1)
        bar.addWidget(button('刷新', self.load_library))
        self.library_edit = button('编辑信息', self.edit_metadata)
        self.library_edit.setEnabled(False)
        bar.addWidget(self.library_edit)
        self.library_columns_button = button('显示列')
        bar.addWidget(self.library_columns_button)
        bar.addWidget(button('更多操作', self.library_menu))
        layout.addLayout(bar)
        self.library_model = Records([
            ('文件名称', 'name'), ('格式', 'ext'), ('状态', lambda r: STATUS.get(r['status'], r['status'])),
            ('分类', lambda r: _category_label(r.get('category'))), ('文号', 'doc_no'), ('行数', 'line_count'),
            ('最近索引', lambda r: self.date_text(r.get('indexed_at'))), ('原文件路径', 'path')])
        self.library_table = records_table(self.library_model)
        self.library_table.setColumnWidth(0, 310)
        self.library_table.setColumnWidth(1, 70)
        self.library_table.setColumnWidth(2, 75)
        self.library_table.setColumnWidth(3, 130)
        self.library_table.setColumnWidth(4, 150)
        column_menu=QMenu(self)
        visible=self.store.settings.get('library_columns',[0,2,3,4])
        visible=set(visible if isinstance(visible,list) else [0,2,3,4])|{0}
        for index,(title,_) in enumerate(self.library_model.columns):
            self.library_table.setColumnHidden(index,index not in visible)
            action=column_menu.addAction(title)
            action.setCheckable(True)
            action.setChecked(index in visible)
            action.setEnabled(index!=0)
            def toggle(shown,index=index):
                self.library_table.setColumnHidden(index,not shown)
                self.store.settings['library_columns']=[i for i in range(self.library_model.columnCount()) if not self.library_table.isColumnHidden(i)]
                self.store.settings['library_header']=bytes(self.library_table.horizontalHeader().saveState().toBase64()).decode('ascii')
                self.store.save()
            action.toggled.connect(toggle)
        self.library_columns_button.setMenu(column_menu)
        header=self.library_table.horizontalHeader()
        saved=self.store.settings.get('library_header')
        if isinstance(saved,str):header.restoreState(QByteArray.fromBase64(saved.encode('ascii')))
        self.library_table.setColumnHidden(0,False)
        def update_column_menu():
            for i,action in enumerate(column_menu.actions()):
                with QSignalBlocker(action):action.setChecked(not self.library_table.isColumnHidden(i))
        column_menu.aboutToShow.connect(update_column_menu)
        header.sectionResized.connect(lambda *_:self.store.settings.update(library_header=bytes(header.saveState().toBase64()).decode('ascii')))
        self.library_table.selectionModel().selectionChanged.connect(self.update_library_selection)
        self.library_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.library_table.customContextMenuRequested.connect(lambda point:self.build_library_menu().exec(self.library_table.viewport().mapToGlobal(point)))
        self.library_table.doubleClicked.connect(self.open_library_selection)
        layout.addWidget(self.library_table, 1)
        self.library_dup_hint = label('', 'muted')
        self.library_dup_hint.hide()
        layout.addWidget(self.library_dup_hint)
        self.library_count = label('', 'muted')
        layout.addWidget(self.library_count)
        return page

    def _bookmarks_page(self):
        page, layout = self._page()
        layout.addWidget(page_heading('收藏', '把常用条款和阅读位置留在手边。', [button('导出汇编', self.export_bookmarks, True)]))
        actions = QHBoxLayout()
        bookmark_actions=[]
        for title,fn in (('打开条款',self.open_bookmark),('编辑备注',self.edit_bookmark_note),('取消收藏',self.remove_bookmark)):
            control=button(title,fn)
            control.setEnabled(False)
            actions.addWidget(control)
            bookmark_actions.append(control)
        self.bookmark_undo_button=button('撤销取消收藏',self.undo_bookmark_removal)
        self.bookmark_undo_button.hide()
        actions.addWidget(self.bookmark_undo_button)
        actions.addStretch()
        actions.addWidget(button('刷新', self.load_bookmarks))
        layout.addLayout(actions)
        self.bookmarks_model = Records([
            ('收藏标题', lambda r: r.get('title') or r.get('name') or Path(r['path']).name),
            ('位置', lambda r: '第 ' + str(r['line_no'])+' 行'), ('备注', 'note'),
            ('收藏时间', lambda r: self.date_text(r['created_at'])), ('文件', 'path')])
        self.bookmarks_table = records_table(self.bookmarks_model)
        self.bookmarks_table.setColumnWidth(0, 330)
        self.bookmarks_table.setColumnWidth(1, 75)
        self.bookmarks_table.setColumnWidth(2, 260)
        self.bookmarks_table.setWordWrap(True)
        self.bookmarks_table.selectionModel().selectionChanged.connect(lambda *_:[control.setEnabled(bool(self.selected_bookmark())) for control in bookmark_actions])
        self.bookmarks_table.doubleClicked.connect(self.open_bookmark)
        layout.addWidget(self.bookmarks_table, 1)
        self.bookmarks_empty = label('暂无收藏 · 在正文阅读中点击“收藏”即可把条款留在这里', 'muted')
        self.bookmarks_empty.hide()
        layout.addWidget(self.bookmarks_empty)
        return page

    def _tasks_page(self):
        page, layout = self._page()
        layout.addWidget(page_heading('任务', '查看导入、OCR、索引与导出的进度。', [button('解析错误', self.show_errors)]))
        actions = QHBoxLayout()
        actions.addWidget(button('停止所选任务', self.cancel_selected_task))
        actions.addWidget(button('查看详情', self.task_detail))
        actions.addStretch()
        layout.addLayout(actions)
        self.task_tree = QTreeWidget()
        self.task_tree.setHeaderLabels(['任务', '状态', '进度', '当前文件 / 详情'])
        self.task_tree.setColumnWidth(0, 200)
        self.task_tree.setColumnWidth(1, 90)
        self.task_tree.setColumnWidth(2, 90)
        self.task_tree.itemDoubleClicked.connect(self.task_detail)
        layout.addWidget(self.task_tree, 1)
        return page

    def navigate(self, key):
        self._stop_auto_play()
        if key == 'ai':
            if self.search_mode.currentIndex()!=1:
                self.search_mode.setCurrentIndex(1)
            else:
                self.pages.setCurrentIndex(self.page_indices['search'])
            return
        if key == 'search' and hasattr(self,'search_mode') and self.search_mode.currentIndex()==1:
            self.search_mode.setCurrentIndex(0)
        self._current_page = key
        if key in self.nav:
            self.nav[key].setChecked(True)
        else:
            self.nav_group.setExclusive(False)
            for item in self.nav.values():
                item.setChecked(False)
            self.nav_group.setExclusive(True)
        self.pages.setCurrentIndex(self.page_indices[key])
        if key == 'library':
            self.load_library()
        elif key == 'bookmarks':
            self.load_bookmarks()
        elif key == 'tools':
            self.filter_tools()

    def focus_search(self):
        self.navigate('search')
        self.search_input.setFocus()
        self.search_input.selectAll()

    def start_search(self):
        if hasattr(self, 'search_debounce'):
            self.search_debounce.stop()
        if hasattr(self, 'search_mode') and self.search_mode.currentIndex()==1:
            return self.ask_ai()
        if hasattr(self, 'search_mode') and self.search_mode.currentIndex() == 0 \
                and self.discovery_scope_kind() != 'content':
            return self.run_discovery_search()
        self.invalidate_discovery_search()
        query = self.search_input.text().strip()
        for key, field in self.advanced_fields.items():
            if field.text().strip():
                query += f' {key}:{field.text().strip()}'
        query = query.strip()
        if not query:
            if self.search_job in self.jobs.active:
                self.jobs.cancel(self.search_job)
            self._search_generation += 1
            self.query = ''
            self.groups = []
            if hasattr(self, '_last_search_result'):
                del self._last_search_result
            self.results.clear()
            self.distribution.set_groups([])
            self.result_stack.setCurrentIndex(0)
            self.result_empty.setText('从一个关键词开始\n\n输入后停顿即可搜索')
            self.result_heading.setText('搜索结果')
            self.result_summary.setText('支持 Office、PDF、文本和扫描件')
            self.search_button.setEnabled(True)
            self.search_button.setToolTip('搜索')
            return
        if self.search_job in self.jobs.active:
            self.jobs.cancel(self.search_job)
        self._search_generation += 1
        generation = self._search_generation
        fuzzy = self.mode.currentIndex() == 1
        file_type = self.type_filter.currentData()
        active, synonyms = self.active_filter.isChecked(), self.synonym_filter.isChecked()
        case_sensitive = self.case_filter.isChecked()
        self.result_heading.setText('正在搜索…')
        self.search_button.setEnabled(False)
        self.search_button.setToolTip('正在搜索…')
        self.query = query
        self.store.settings['recent_keywords'] = [query] + [
            item for item in self.store.settings.get('recent_keywords', []) if item != query][:24]
        self.store.settings['only_active'] = active
        self.store.settings['synonym'] = synonyms
        self.store.save()

        def done(result):
            if generation != self._search_generation:
                return
            self.search_button.setEnabled(True)
            self.search_button.setToolTip('搜索')
            self.render_results(result)

        def failed(detail):
            if generation == self._search_generation:
                self.search_button.setEnabled(True)
                self.search_button.setToolTip('搜索')
                self.result_heading.setText('搜索未完成')
                self.notify(detail.strip().splitlines()[-1])

        self.search_job = self.jobs.submit('搜索：' + query,
            lambda progress, stop: self.store.search(query, fuzzy, file_type, active, synonyms, stop,
                                                   case_sensitive=case_sensitive),
            done, error=failed, visible=False)

    def _render_results(self, result):
        self.groups = result['groups']
        self.distribution.set_groups(self.groups)
        self.results.clear()
        self.result_filter.clear()
        self.result_heading.setText(f"{len(self.groups)} 个文件")
        from synonyms import describe
        self.result_summary.setText(f"{result['hits']} 处命中 · 用时 {result['elapsed']:.2f} 秒" + describe(result.get('synonyms', {})))
        if not self.groups:
            self.result_empty.setText('没有找到匹配结果\n\n可尝试缩短关键词、模糊匹配或取消筛选。')
            self.result_stack.setCurrentIndex(0)
            return
        self.result_stack.setCurrentIndex(1)
        self._result_queue = list(self.groups)
        self._render_generation = self._search_generation
        self._append_results(self._render_generation)

    def _append_results(self, generation):
        if generation != self._search_generation:
            return
        self.results.setUpdatesEnabled(False)
        try:
            batch = self._result_queue[:20]
            del self._result_queue[:20]
            for group in batch:
                root = QTreeWidgetItem([group['name'], str(group['hits'])])
                root.setData(0, Qt.ItemDataRole.UserRole, (group['path'], group['first_line']))
                root.setToolTip(0, self.result_tooltip(group))
                font = root.font(0)
                font.setBold(True)
                root.setFont(0, font)
                self.append_result_clusters(root, group, 0)
                if group.get('status') in ('void', 'replaced'):
                    root.setForeground(0, QColor('#8a94a6'))
                self.results.addTopLevelItem(root)
                root.setExpanded(self.results.topLevelItemCount() <= 3)
        finally:
            self.results.setUpdatesEnabled(True)
        pending = getattr(self, '_pending_selection', None)
        if pending:
            for number in range(self.results.topLevelItemCount()):
                root = self.results.topLevelItem(number)
                if root.data(0, Qt.ItemDataRole.UserRole)[0] == pending[0]:
                    with QSignalBlocker(self.results):
                        self.results.setCurrentItem(root)
                    self._pending_selection = None
                    break
        if self.results.currentItem() is None and (not pending or not self._result_queue):
            self.results.setCurrentItem(self.results.topLevelItem(0))
        if self._result_queue:
            QTimer.singleShot(0, lambda: self._append_results(generation))
        self.filter_results()

    def filter_results(self):
        if hasattr(self,'discovery_results') and self.result_stack.currentWidget() is self.discovery_results:
            return self.filter_discovery_results()
        query = self.result_filter.text().casefold().strip()
        for number in range(self.results.topLevelItemCount()):
            item = self.results.topLevelItem(number)
            file_match = not query or query in (item.text(0) + item.toolTip(0)).casefold()
            match = file_match
            for index in range(item.childCount()):
                child = item.child(index)
                child_match = file_match or query in child.text(0).casefold()
                more = child.data(0, Qt.ItemDataRole.UserRole)
                if not child_match and isinstance(more, dict) and more.get('type') == 'more':
                    child_match = any(query in str(line['line_text']).casefold()
                        for cluster in more['group']['clusters'][more['start']:]
                        for line in cluster['lines'])
                child.setHidden(not child_match)
                if child_match:
                    match = True
            item.setHidden(not match)

    def _result_selected(self, item, previous):
        if item is not None:
            self._stop_auto_play()
            location = item.data(0, Qt.ItemDataRole.UserRole)
            if location and not isinstance(location, dict):
                if self.results.header_click_active and item.parent() is None and self.current and os.path.normcase(os.path.normpath(self.current['path']))==os.path.normcase(os.path.normpath(location[0])):
                    return
                self.open_preview(*location)

    def eventFilter(self, target, event):
        if hasattr(self, 'reader') and self.zoom_reader_event(target, event):
            return True
        return super().eventFilter(target, event)

    def open_preview(self, path, line=1, history=True, mode_override=None, chapter_end=None):
        if hasattr(self,'_content_read_menu'):
            self.discovery_current=None
            self.read_button.setMenu(self._content_read_menu)
        if history and (self._history_index < 0 or self._history[self._history_index] != (path, line)):
            self._history = self._history[:self._history_index + 1] + [(path, line)]
            self._history_index = len(self._history) - 1
        if self.preview_job in self.jobs.active:
            self.jobs.cancel(self.preview_job)
        self._preview_generation += 1
        generation = self._preview_generation
        mode, query = mode_override or self.preview_mode.currentText(), self.query
        self.doc_title.setText(Path(path).name)
        self.doc_meta.setText('正在读取正文…')

        def done(payload):
            if generation != self._preview_generation:
                return
            self.current = payload
            self.show_preview(payload)

        self.preview_job = self.jobs.submit('读取正文',
            lambda progress, stop: self.store.preview(path, line, mode, query, chapter_end), done,
            error=lambda detail: self.notify(detail.strip().splitlines()[-1]), visible=False)

    def show_preview(self, payload):
        meta = payload['meta']
        pieces = [STATUS.get(meta.get('status'), '有效'), meta.get('doc_no', ''), payload.get('chapter', '')]
        if not payload['exists']:
            pieces.append('原文件暂不可用，索引正文仍可阅读')
        self.doc_meta.setText(' · '.join(str(piece) for piece in pieces if piece))
        self.doc_title.setToolTip(payload['path'])
        group = next((group for group in self.groups if group['path'] == payload['path']), None)
        payload['fuzzy_lines'] = [match['line_no'] for match in group['matches'] if match.get('score', 1) < 1] if group else []
        self.reader.setHtml(preview_html(payload, self.store.settings['dark'],
                                        self.store.settings.get('preview_font', 12)))
        self.reader.scrollToAnchor('L' + str(payload['line']))
        self.bookmark_button.setText('已收藏' if payload['bookmarked'] else '收藏')
        rows = payload['rows']
        self.preview_range.setToolTip(f"正文 L{rows[0][0]}–L{rows[-1][0]}；定位 L{payload['line']}；点击跳转" if rows else '点击跳转')
        self.outline.clear()
        stack = []
        for chapter in payload['chapters']:
            item = QTreeWidgetItem([chapter['title']])
            item.setToolTip(0,chapter['title'])
            font=item.font(0)
            font.setBold(chapter['level']<=1)
            item.setFont(0,font)
            item.setData(0, Qt.ItemDataRole.UserRole, chapter)
            while stack and stack[-1][0] >= chapter['level']:
                stack.pop()
            (stack[-1][1].addChild(item) if stack else self.outline.addTopLevelItem(item))
            stack.append((chapter['level'], item))
        self.outline.expandToDepth(0)
        iterator=__import__('PySide6.QtWidgets',fromlist=['QTreeWidgetItemIterator']).QTreeWidgetItemIterator(self.outline)
        best=None
        while iterator.value():
            item=iterator.value()
            chapter=item.data(0,Qt.ItemDataRole.UserRole)
            if chapter['start_line']<=payload['line']<=chapter['end_line']:best=item
            iterator+=1
        if best:
            self.outline.setCurrentItem(best)
            parent=best.parent()
            while parent:
                parent.setExpanded(True);parent=parent.parent()
            self.outline.scrollToItem(best)
        self.distribution.selected_path=payload['path']
        self.distribution.update()
        self.store.settings['last_read'] = {'path': payload['path'], 'line': payload['line']}
        self.update_document_reader(payload)
        self.update_reader_position()

    def reload_preview(self, *_):
        if self.current:
            self.open_preview(self.current['path'], self.current['line'], history=False)

    def toggle_outline(self):
        self.outline.setVisible(not self.outline.isVisible())
        self.reader_splitter.setSizes([210, 600])

    def _chapter_selected(self, item, *_):
        self._stop_auto_play()
        if self.current:
            chapter = item.data(0, Qt.ItemDataRole.UserRole)
            self.open_preview(self.current['path'], chapter['start_line'], mode_override='章节', chapter_end=chapter['end_line'])

    def go_back(self):
        self._stop_auto_play()
        if self._history_index > 0:
            self._history_index -= 1
            self.open_preview(*self._history[self._history_index], history=False)

    def goto_line(self):
        self._stop_auto_play()
        if self.current:
            line, ok = QInputDialog.getInt(self, '跳转到正文', '行号', self.current['line'], 1, 10000000)
            if ok:
                self.open_preview(self.current['path'], line)

    def next_hit(self, step=1):
        if not self.current:
            return
        group = next((item for item in self.groups if item['path'] == self.current['path']), None)
        if not group:
            return
        lines = [match['line_no'] for match in group['matches']]
        current = self.current['line']
        if step > 0:
            line = next((line for line in lines if line > current), lines[0])
        else:
            line = next((line for line in reversed(lines) if line < current), lines[-1])
        self.open_preview(self.current['path'], line)

    def toggle_auto_play(self, checked):
        if checked:
            if not self.current:
                self.auto_play.setChecked(False)
                self.notify('先在左侧选一篇正文，再开始自动播放')
                return
            timer = QTimer(self)
            timer.setInterval(2600)
            timer.timeout.connect(self._autoplay_step)
            timer.start()
            self._autoplay_timer = timer
            self.notify('自动播放中 · 每 2.6 秒跳到下一处命中，再次点击停止')
        else:
            self._stop_auto_play()

    def _stop_auto_play(self):
        timer = getattr(self, '_autoplay_timer', None)
        if timer is not None:
            timer.stop()
            self._autoplay_timer = None
        button = getattr(self, 'auto_play', None)
        if button is not None and button.isChecked():
            button.setChecked(False)

    def _autoplay_step(self):
        if not self.current:
            self._stop_auto_play()
            return
        group = next((item for item in self.groups if item['path'] == self.current['path']), None)
        lines = sorted({match['line_no'] for match in group['matches']}) if group and group.get('matches') else []
        if lines and self.current['line'] < lines[-1]:
            self.next_hit(1)
            return
        next_path = self._next_group_path(self.current['path'])
        if next_path:
            next_group = next((item for item in self.groups if item['path'] == next_path), None)
            self.open_preview(next_path, (next_group or {}).get('first_line', 1))
        else:
            self._stop_auto_play()
            self.notify('已播放完所有搜索结果')

    def _next_group_path(self, path):
        for index, group in enumerate(self.groups):
            if group['path'] == path and index + 1 < len(self.groups):
                return self.groups[index + 1]['path']
        return None

    def copy_text(self):
        if self.current:
            text = self.reader.textCursor().selectedText().replace('\u2029', '\n')
            QApplication.clipboard().setText(text or '\n'.join(str(row[1]) for row in self.current['rows']))
            self.notify('已复制正文')

    def copy_citation(self):
        if self.current:
            path, line = self.current['path'], self.current['line']
            self.jobs.submit('生成引用', lambda progress, stop: core.build_citation(self.store.db, path, line),
                lambda text: (QApplication.clipboard().setText(text), self.notify('已复制引用')), visible=False)

    def open_original(self):
        if self.current:
            self.open_located_file(self.current['path'], self.current['line'])

    def toggle_bookmark(self):
        if not self.current:
            return
        path, line = self.current['path'], self.current['line']
        bookmarked = self.current['bookmarked']
        fn = (lambda progress, stop: self.store.write(indexer.remove_bookmark, path=path, line_no=line)) if bookmarked else (
            lambda progress, stop: self.store.write(indexer.add_bookmark, path, line, Path(path).name))
        self.jobs.submit('取消收藏' if bookmarked else '收藏条款', fn,
            lambda _: self.reload_preview(), mutating=True)

    def load_library(self):
        self.jobs.submit('读取资料库', lambda progress, stop: self.store.library(),
                         self._library_loaded, visible=False, error=self.show_error)

    def _library_loaded(self, rows):
        self._library = rows
        self.filter_library()

    def filter_library(self):
        query = self.library_filter.text().strip().casefold()
        rows = [row for row in self._library if not query or query in ' '.join(
            str(row.get(key, '')) for key in ('name', 'path', 'category', 'doc_no', 'status')).casefold()]
        self.library_model.set_rows(rows)
        groups = _duplicate_groups(rows)
        self.library_dup_hint.setText(f'发现 {groups} 组同名文件，建议核对是否重复' if groups else '')
        self.library_dup_hint.setVisible(bool(groups))
        self.library_count.setText(f'{len(rows)} 个文件 · 资料库共 {len(self._library)} 个文件')
        self.update_library_selection()

    def update_library_selection(self,*_):
        selected=len(self.selected_library_rows())
        self.library_edit.setEnabled(selected==1)
        if hasattr(self,'library_count'):
            self.library_count.setText(f'{self.library_model.rowCount()} 个文件 · 资料库共 {len(self._library)} 个 · 已选 {selected} 个')

    def selected_library_rows(self):
        return [self.library_model.rows[index.row()] for index in self.library_table.selectionModel().selectedRows()]

    def open_library_selection(self, *_):
        rows = self.selected_library_rows()
        if rows:
            self.navigate('search')
            self.open_preview(rows[0]['path'], 1)

    def load_bookmarks(self):
        self.jobs.submit('读取收藏', lambda progress, stop: indexer.list_bookmarks(self.store.db),
            self._bookmarks_loaded, visible=False, error=self.show_error)

    def _bookmarks_loaded(self, rows):
        setattr(self, '_bookmarks', rows)
        self.bookmarks_model.set_rows(rows)
        self.bookmarks_empty.setVisible(not rows)

    def selected_bookmark(self):
        rows = self.bookmarks_table.selectionModel().selectedRows()
        return self.bookmarks_model.rows[rows[0].row()] if rows else None

    def open_bookmark(self, *_):
        row = self.selected_bookmark()
        if row:
            self.navigate('search')
            self.open_preview(row['path'], row['line_no'])

    def edit_bookmark_note(self):
        row = self.selected_bookmark()
        if row:
            text, ok = QInputDialog.getMultiLineText(self, '收藏备注', '备注', row.get('note') or '')
            if ok:
                self.jobs.submit('保存备注', lambda progress, stop: self.store.write(
                    indexer.set_bookmark_note, row['id'], text), lambda _: self.load_bookmarks(), mutating=True)

    def remove_bookmark(self):
        row = self.selected_bookmark()
        if row:
            removed=dict(row)
            def done(_):
                self._bookmark_undo=removed
                self.bookmark_undo_button.show()
                self.load_bookmarks()
                if self.current and self.current['path']==removed['path'] and self.current['line']==removed['line_no']:
                    self.current['bookmarked']=False
                    self.bookmark_button.setText('收藏')
                self.notify('已取消收藏，可在收藏页撤销')
            self.jobs.submit('取消收藏', lambda progress, stop: self.store.write(
                indexer.remove_bookmark, bookmark_id=row['id']),done,mutating=True)

    def undo_bookmark_removal(self):
        row=getattr(self,'_bookmark_undo',None)
        if not row:return
        self.bookmark_undo_button.setEnabled(False)
        def done(restored):
            self._bookmark_undo=None
            self.bookmark_undo_button.hide()
            self.bookmark_undo_button.setEnabled(True)
            self.load_bookmarks()
            if self.current and self.current['path']==row['path'] and self.current['line']==row['line_no']:
                self.current['bookmarked']=True
                self.bookmark_button.setText('已收藏')
            self.notify('已恢复收藏及备注' if restored else '此条款已经在收藏中，保留现有备注')
        def failed(detail):
            self.bookmark_undo_button.setEnabled(True)
            self.show_error(detail)
        self.jobs.submit('恢复收藏',lambda p,s:self.store.write(indexer.restore_bookmark,row),done,error=failed,mutating=True)

    def refresh_stats(self):
        def done(stats):
            self.library_summary.setText(f"{stats['files']:,} 个资料文件\n{fmt_size(stats['size'])}")
            self.library_summary.setToolTip(f"资料库：{self.store.root}\n最近索引：{self.date_text(stats['last'])}\n解析错误：{stats['errors']}")
        self.jobs.submit('资料库统计', lambda progress, stop: self.store.statistics(), done,
                         visible=False, error=self.show_error)

    def refresh_current(self):
        key = self._current_page
        if key == 'search' and self.search_input.text().strip():
            self.start_search()
        elif key == 'library':
            self.load_library()
        elif key == 'bookmarks':
            self.load_bookmarks()
        self.refresh_stats()

    def task_changed(self, job):
        if job.mutating and job.state == '已完成' and hasattr(self, '_explore_dirty'):
            self._explore_dirty = True
        if not job.visible:
            return
        item = self._task_items.get(job.key)
        if item is None:
            item = QTreeWidgetItem([job.title, job.state, '', ''])
            item.setData(0, Qt.ItemDataRole.UserRole, job.key)
            self.task_tree.insertTopLevelItem(0, item)
            self._task_items[job.key] = item
        progress = job.progress
        total, current = progress.get('total', 0), progress.get('current', 0)
        item.setText(1, job.state)
        item.setText(2, f'{current}/{total}' if total else '')
        detail = progress.get('path') or progress.get('status', '')
        if 'found' in progress:
            detail = f"已发现 {progress['found']} 条 · 失败 {progress.get('failed',0)}"
        if job.error:
            detail = job.error.strip().splitlines()[-1]
        elif job.state == '已完成' and isinstance(job.result, dict):
            detail = ' · '.join(f'{key}：{job.result[key]}' for key in ('ok', 'processed', 'failed', 'total') if key in job.result) or '处理完成'
        item.setText(3, str(detail))
        item.setToolTip(3, str(detail))
        if job.state in ('进行中', '正在停止'):
            self.status.setText(job.title + (' · ' + Path(str(detail)).name if detail else ''))
            self.busy.show()
            self.busy.setRange(0, total if total else 0)
            self.busy.setValue(current)
        elif job.state == '失败':
            self.notify(job.title + '未完成，请在任务中查看详情')

    def _jobs_settled(self):
        self.update_menu_badge()
        if not any(self.jobs.jobs[key].visible for key in self.jobs.active):
            self.busy.hide()
            self.status.setText('准备就绪')
        if self._closing_requested and not self.jobs.active:
            QTimer.singleShot(0, self.close)

    def cancel_selected_task(self):
        item = self.task_tree.currentItem()
        if item:
            self.jobs.cancel(item.data(0, Qt.ItemDataRole.UserRole))

    def task_detail(self, *_):
        item = self.task_tree.currentItem()
        if item:
            job = self.jobs.jobs[item.data(0, Qt.ItemDataRole.UserRole)]
            from shiboken6 import isValid
            dialog = getattr(job, 'import_dialog', None)
            if dialog and isValid(dialog):
                dialog.show()
                dialog.raise_()
                return
            self.task_report(job)

    def task_report(self, job):
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QPlainTextEdit
        import json
        import re
        result = job.result if isinstance(job.result, dict) else {}
        summary = []
        for key, title in (('ok','成功'),('failed','失败'),('skipped','跳过'),('files','文件'),('hits','命中')):
            if key in result and isinstance(result[key], (int,float)):
                summary.append(f'{title} {result[key]} 项')
        state = '部分失败' if result.get('failed') and result.get('ok') else job.state
        reason = re.sub(r'^[A-Za-z_][\w.]*Error:\s*','',job.error.strip().splitlines()[-1]) if job.error else ''
        dialog = QDialog(self)
        dialog.setWindowTitle(job.title+' · '+state)
        dialog.resize(740,460)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(job.title,'section'))
        form = QFormLayout()
        form.addRow('状态',label(state))
        form.addRow('处理结果',label(' · '.join(summary) or ('正在处理，请稍候' if job.key in self.jobs.active else '任务已结束')))
        form.addRow('开始时间',label(self.date_text(job.started)))
        if reason:
            message = label(reason)
            message.setWordWrap(True)
            form.addRow('原因',message)
            tip = label('检查原文件是否存在、所在位置是否在线，或检查相关服务设置后重新执行。','muted')
            tip.setWordWrap(True)
            form.addRow('下一步',tip)
        elif job.progress:
            message=label(str(job.progress.get('status') or job.progress.get('path') or ''))
            message.setWordWrap(True)
            form.addRow('当前进度',message)
        layout.addLayout(form)
        details = job.error or json.dumps(job.result if job.result is not None else job.progress,ensure_ascii=False,indent=2,default=str)
        toggle=button('展开诊断详情')
        toggle.setCheckable(True)
        layout.addWidget(toggle)
        body=QPlainTextEdit(details)
        body.setReadOnly(True)
        body.hide()
        toggle.toggled.connect(body.setVisible)
        toggle.toggled.connect(lambda shown:toggle.setText('收起诊断详情' if shown else '展开诊断详情'))
        layout.addWidget(body,1)
        controls=QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        controls.addButton(button('复制诊断',lambda:QApplication.clipboard().setText(job.title+'\n'+state+'\n'+details)),QDialogButtonBox.ButtonRole.ActionRole)
        controls.rejected.connect(dialog.close)
        layout.addWidget(controls)
        return self.keep_dialog(dialog)

    def notify(self, message):
        self.statusBar().showMessage(str(message), 7000)
        self.notice.setText(str(message))
        self.notice.adjustSize()
        self.notice.move(max(8, self.pages.width() - self.notice.width() - 16),
                         max(8, self.pages.height() - self.notice.height() - 16))
        self.notice.show()
        self.notice.raise_()
        self.notice_timer.start(7000)

    def show_error(self, detail):
        self.notify(str(detail).strip().splitlines()[-1])

    @staticmethod
    def date_text(timestamp):
        return datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M') if timestamp else '—'

    def apply_theme(self):
        compact = self.store.settings.get('compact', True)
        self.setStyleSheet(stylesheet(self.store.settings.get('dark', False), compact))
        self.sidebar.setFixedWidth(144 if compact else 156)
        self.distribution.setFixedHeight(66 if compact else 94)
        self.search_form.setContentsMargins(0, 0, 0, 0)
        self.search_form.setSpacing(4)
        self.search_button.setFixedSize(SEARCH_CONTROL_HEIGHT, SEARCH_CONTROL_HEIGHT)
        from .discovery_ui import magnifier_icon
        self.search_button.setIcon(magnifier_icon('#ffffff', send=not self.discovery_is_explore()))
        for number in range(self.pages.count()):
            page_layout = self.pages.widget(number).layout()
            if page_layout:
                page_layout.setContentsMargins(*( (0, 0, 0, 0) if number == self.page_indices['search'] else
                    (18 if compact else 26, 14 if compact else 24, 18 if compact else 26, 12 if compact else 18)))
                page_layout.setSpacing(0 if number == self.page_indices['search'] else 10)
        if self.current:
            self.show_preview(self.current)
        try:
            import ctypes
            dark = ctypes.c_int(bool(self.store.settings.get('dark')))
            hwnd = int(self.winId())
            from ctypes import wintypes
            setter = ctypes.windll.dwmapi.DwmSetWindowAttribute
            setter.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
            setter(hwnd, 20, ctypes.byref(dark), 4)
        except (AttributeError, OSError):
            pass

    def _make_tray(self):
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip('镜仔搜索 · 新版')
        menu = QMenu(self)
        menu.addAction('显示窗口', self.restore_window)
        menu.addAction('迷你速查', self.quick_search)
        menu.addSeparator()
        menu.addAction('退出', self.exit_application)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.restore_window()
            if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def restore_window(self):
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    def focus_and_restore(self):
        self.restore_window()
        self.focus_search()

    def exit_application(self):
        self._force_exit = True
        self.close()

    def closeEvent(self, event):
        if not getattr(self, '_force_exit', False) and not self._closing_requested:
            if self._close_choice is None:
                choice = QMessageBox(self)
                choice.setWindowTitle('关闭窗口')
                choice.setText('关闭窗口时要怎么做？')
                choice.setInformativeText('可记住选择，也可在“设置 → 资料安全”中修改。')
                remember=QCheckBox('记住选择')
                choice.setCheckBox(remember)
                tray_button = choice.addButton('最小化到托盘', QMessageBox.ButtonRole.ActionRole)
                exit_button = choice.addButton('直接退出', QMessageBox.ButtonRole.DestructiveRole)
                choice.addButton('取消', QMessageBox.ButtonRole.RejectRole)
                choice.exec()
                if choice.clickedButton() is tray_button:
                    self._close_choice = 'tray'
                elif choice.clickedButton() is exit_button:
                    self._close_choice = 'exit'
                else:
                    event.ignore()
                    return
                if remember.isChecked():
                    self.store.settings['close_behavior']=self._close_choice
                    self.store.save()
            if self._close_choice == 'tray':
                self.hide() if self.tray.isVisible() else self.showMinimized()
                event.ignore()
                return
        if self.jobs.active:
            if not self._closing_requested:
                answer = QMessageBox.question(self, '后台任务仍在运行',
                    '停止后台任务并退出？已完成的索引会保留。',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if answer != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return
                self._closing_requested = True
                for key in list(self.jobs.active):
                    self.jobs.cancel(key)
                self.notify('正在停止后台任务，完成后退出…')
            event.ignore()
            return
        if not getattr(self,'_discovery_shutdown_done',False):
            self.discovery_timer.stop()
            # Normal windows with no watches can finish immediately; joining live
            # observers is worker IO and must not block the native window thread.
            if self.discovery._watchers or self.discovery._workers:
                self._closing_requested=True
                def stopped(_):
                    self._discovery_shutdown_done=True
                self.jobs.submit('停止目录监听',lambda progress,stop:self.shutdown_discovery(),stopped)
                event.ignore()
                return
            self._discovery_shutdown_done=True
        self.search_debounce.stop()
        self.maintenance_timer.stop()
        if not self._exit_backup_done and int(self.store.settings.get('max_backups', 3)) > 0:
            self._exit_backup_done = True
            self._closing_requested = True
            if self.watcher:
                self.watcher.stop()
                self.watcher = None
            def backup_failed(detail):
                self.store.settings['backup_failed'] = detail.strip().splitlines()[-1]
            def backup_done(_):
                self.store.settings.pop('backup_failed', None)
            self.jobs.submit('退出前自动备份', self.store.automatic_backup, backup_done,
                             error=backup_failed, mutating=False)
            event.ignore()
            return
        self.store.settings['qt_geometry'] = bytes(self.saveGeometry().toBase64()).decode('ascii')
        if self.explore_preferences_timer.isActive():
            self.save_explore_preferences()
        state = getattr(self, '_reading_splitter_state', self.splitter.saveState()) if self.result_column.isHidden() else self.splitter.saveState()
        if self.search_mode.currentIndex()==1 and hasattr(self,'_explore_splitter_state'):
            state=self._explore_splitter_state
        self.store.settings['qt_splitter'] = bytes(state.toBase64()).decode('ascii')
        self.store.settings['qt_layout_version'] = 3
        self.store.save()
        self.pdf_reader.close_document()
        if self.watcher:
            self.watcher.stop()
        self.discovery_timer.stop()
        self.tray.hide()
        event.accept()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and all(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        self.confirm_import(paths)
        event.acceptProposedAction()

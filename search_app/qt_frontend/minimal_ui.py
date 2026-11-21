"""侧栏导航、左栏检索与通高阅读；功能按使用范围归位。"""
from functools import partial
from pathlib import Path
import html

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal, QSignalBlocker
from PySide6.QtGui import QAction, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMenu, QPushButton, QScrollArea, QSizePolicy, QSplitter, QStackedWidget,
    QToolButton, QTreeWidget, QVBoxLayout, QWidget, QWidgetAction)

import core
from .legacy_bridge import TOOLS
from .pdf_reader import PdfReader
from .result_snippets import SnippetTree
from .widgets import Records, button, label, page_heading, records_table, DistributionChart, ReadingSplitter
from .discovery_ui import magnifier_icon
from .answer_view import QuestionInput

SEARCH_CONTROL_HEIGHT = 28


class FileTitle(QLabel):
    clicked = Signal()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().windowText().color())
        painter.drawText(self.contentsRect(), Qt.AlignmentFlag.AlignVCenter,
            self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideMiddle, self.width()))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


def small_button(text, tooltip, callback=None):
    item = QToolButton()
    item.setText(text)
    item.setToolTip(tooltip)
    item.setAccessibleName(tooltip)
    item.setObjectName('quietControl')
    if callback:
        item.clicked.connect(callback)
    return item


class MinimalUiMixin:
    def build_minimal_search_page(self):
        page = QWidget()
        page.setObjectName('searchPage')
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.splitter = ReadingSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(7)
        self.result_column = QWidget()
        self.result_column.setObjectName('resultColumn')
        column = QVBoxLayout(self.result_column)
        column.setContentsMargins(10, 10, 8, 8)
        column.setSpacing(5)
        self.search_heading = self.result_column
        form = QVBoxLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(4)
        self.search_form = form
        self.search_input = QLineEdit()
        self.search_input.setObjectName('searchInput')
        self.search_input.setFixedHeight(SEARCH_CONTROL_HEIGHT)
        self.search_input.setMinimumWidth(130)
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setPlaceholderText('关键词、文件名或路径…')
        self.search_input.returnPressed.connect(self.start_search)
        self._esc_clear = QShortcut(QKeySequence('Esc'), self.search_input)
        self._esc_clear.setContext(Qt.ShortcutContext.WidgetShortcut)
        self._esc_clear.activated.connect(self.search_input.clear)
        history = self.search_input.addAction(self.style().standardIcon(self.style().StandardPixmap.SP_FileDialogDetailedView),
                                             QLineEdit.ActionPosition.TrailingPosition)
        history.setToolTip('历史、常用搜索与恢复阅读')
        history.triggered.connect(self.history_menu)
        row = QHBoxLayout()
        row.setSpacing(5)
        self.search_mode = QComboBox()
        self.search_mode.setObjectName('searchMode')
        self.search_mode.addItems(['探索', '智答'])
        self.search_mode.setToolTip('探索：按名称/路径找文件；智答：基于资料问答')
        self.search_mode.setFixedSize(56, SEARCH_CONTROL_HEIGHT)
        self.search_mode.setAccessibleName('切换探索或智答')
        row.addWidget(self.search_mode)
        self.input_stack = QStackedWidget()
        self.input_stack.addWidget(self.search_input)
        self.ai_input = QuestionInput()
        self.input_stack.addWidget(self.ai_input)
        self.input_stack.setFixedHeight(SEARCH_CONTROL_HEIGHT)
        self.ai_input.submitted.connect(self.start_search)
        def sync_question():
            with QSignalBlocker(self.search_input):
                self.search_input.setText(self.ai_input.toPlainText())
        self.ai_input.textChanged.connect(sync_question)
        self.search_input.textChanged.connect(lambda text: self.ai_input.setPlainText(text)
            if self.ai_input.toPlainText()!=text else None)
        self.ai_input.height_changed.connect(lambda height:self.input_stack.setFixedHeight(height)
            if self.search_mode.currentIndex()==1 else None)
        row.addWidget(self.input_stack, 1)
        self.search_button = QPushButton()
        self.search_button.setIcon(magnifier_icon('#ffffff'))
        self.search_button.setIconSize(QSize(16, 16))
        self.search_button.setObjectName('searchButton')
        self.search_button.setToolTip('搜索（回车或点击放大镜执行）')
        self.search_button.setAccessibleName('搜索')
        self.search_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_button.clicked.connect(self.start_search)
        self.search_button.setFixedSize(SEARCH_CONTROL_HEIGHT, SEARCH_CONTROL_HEIGHT)
        row.addWidget(self.search_button)
        row.setAlignment(self.search_mode, Qt.AlignmentFlag.AlignTop)
        row.setAlignment(self.search_button, Qt.AlignmentFlag.AlignTop)
        form.addLayout(row)
        row2 = QHBoxLayout()
        row2.setSpacing(5)
        self.options_button = button('筛选 ▾')
        self.options_button.setObjectName('searchOptionsButton')
        self.options_button.setFixedHeight(SEARCH_CONTROL_HEIGHT)
        self.options_button.setCheckable(True)
        self.options_button.setAccessibleName('展开或收起搜索选项')
        row2.addWidget(self.options_button)
        self.scope_status = label('', 'muted')
        self.scope_status.setMinimumWidth(0)
        row2.addWidget(self.scope_status, 1)
        self.range_button = small_button('范围 ▾', '选择搜索范围', self.search_range_menu)
        self.range_button.setObjectName('scopeButton')
        self.range_button.setFixedHeight(SEARCH_CONTROL_HEIGHT)
        row2.addWidget(self.range_button)
        form.addLayout(row2)
        column.addLayout(form)
        self.options_scroll = QScrollArea()
        self.options_scroll.setObjectName('searchOptions')
        self.options_scroll.setWidgetResizable(True)
        self.options_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.options_scroll.setMaximumHeight(380)
        self.options_panel = QWidget()
        self.options_scroll.setWidget(self.options_panel)
        self.build_minimal_options()
        column.addWidget(self.options_scroll)
        self.options_scroll.hide()
        column.addWidget(self.filter_chips)
        self.options_button.toggled.connect(self.options_scroll.setVisible)
        self.options_button.toggled.connect(self.update_option_badge)
        self.distribution = DistributionChart()
        self.distribution.selected.connect(self.jump_distribution)
        self.distribution.hide()
        column.addWidget(self.distribution)
        header = QHBoxLayout()
        self.result_heading = label('搜索结果', 'muted')
        header.addWidget(self.result_heading, 1)
        self.sort_order = self.build_sort_control()
        self.sort_order.setParent(self)
        self.sort_order.hide()
        self.result_actions = small_button('⋯', '结果操作与辅助搜索')
        self.result_actions.clicked.connect(self.search_actions_menu)
        header.addWidget(self.result_actions)
        column.addLayout(header)
        self.result_filter = QLineEdit()
        self.result_filter.setClearButtonEnabled(True)
        self.result_filter.setPlaceholderText('在当前结果中筛选…')
        self.result_filter.textChanged.connect(self.filter_results)
        self.result_filter.hide()
        column.addWidget(self.result_filter)
        self.result_stack = QStackedWidget()
        self.result_empty = label('输入关键词开始搜索', 'muted')
        self.result_empty.setWordWrap(True)
        self.result_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_stack.addWidget(self.result_empty)
        self.results = SnippetTree()
        self.results.setObjectName('snippetResults')
        self.results.setColumnCount(2)
        self.results.setHeaderHidden(True)
        self.results.setColumnWidth(1, 34)
        self.results.header().setStretchLastSection(False)
        from PySide6.QtWidgets import QHeaderView
        self.results.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.results.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.results.setIndentation(12)
        self.results.currentItemChanged.connect(self._result_selected)
        self.results.itemDoubleClicked.connect(self.open_result_original)
        self.results.more_requested.connect(self.open_result_original)
        self.result_stack.addWidget(self.results)
        column.addWidget(self.result_stack, 1)
        self.result_summary = label('', 'muted')
        self.result_summary.setWordWrap(True)
        column.addWidget(self.result_summary)
        self.result_column.setMinimumWidth(290)
        self.splitter.addWidget(self.result_column)
        self.build_minimal_reader()
        self.splitter.addWidget(self.preview_panel)
        self.preview_panel.setMinimumWidth(400)
        self.splitter.setSizes([330, 900])
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        layout.addWidget(self.splitter)
        self.update_option_badge()
        return page

    def build_search_range_menu(self):
        menu = QMenu(self)
        for index, title in enumerate(('电脑文件', '资料库', '两者')):
            action = menu.addAction(title)
            action.setCheckable(True)
            action.setChecked(self.discovery_scope.currentIndex() == index)
            action.triggered.connect(lambda checked=False, i=index: self.discovery_scope.setCurrentIndex(i))
        menu.addSeparator()
        menu.addAction('管理搜索范围…', self.open_range_manager)
        return menu

    def search_range_menu(self):
        self.build_search_range_menu().exec(self.range_button.mapToGlobal(self.range_button.rect().bottomLeft()))

    def build_minimal_options(self):
        saved = self.store.settings.get('explore_preferences', {})
        saved = saved if isinstance(saved, dict) else {}
        def restore_index(combo, key, default=0):
            value = saved.get(key, default)
            combo.setCurrentIndex(value if isinstance(value, int) and 0 <= value < combo.count() else default)
        form = QFormLayout(self.options_panel)
        form.setContentsMargins(4, 6, 4, 8)
        form.setSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.discovery_scope = QComboBox()
        self.discovery_scope.addItems(['电脑文件', '资料库', '两者'])
        restore_index(self.discovery_scope, 'scope', 2)
        self.discovery_scope.setToolTip('电脑文件：已选并建立目录的位置；资料库：已入库的正文；两者：合并检索')
        self.discovery_scope.currentIndexChanged.connect(lambda _: self.schedule_search())
        self.discovery_scope.setParent(self.options_panel)
        self.discovery_scope.hide()  # 范围统一由工具条入口控制。
        self.catalog_scope = QComboBox()
        for text, value in [('文件名称','name'),('完整路径','path'),('名称和路径','all')]:
            self.catalog_scope.addItem(text,value)
        restore_index(self.catalog_scope, 'match')
        form.addRow('目录匹配', self.catalog_scope)
        self.catalog_ext = QLineEdit()
        self.catalog_ext.setText(str(saved.get('ext', ''))[:200])
        self.catalog_ext.setPlaceholderText('例如 pdf,docx；留空为全部格式')
        form.addRow('电脑扩展名',self.catalog_ext)
        self.catalog_root = QLineEdit()
        self.catalog_root.setText(str(saved.get('root', ''))[:4000])
        self.catalog_root.setPlaceholderText('在已选位置内进一步限定目录')
        form.addRow('限定目录',self.catalog_root)
        self.catalog_sort = QComboBox(self)
        for title,value in [('文件名称',('name',False)),('完整路径',('path',False)),
                            ('最近修改',('mtime',True)),('文件大小',('size',True))]:
            self.catalog_sort.addItem(title,value)
        restore_index(self.catalog_sort, 'sort')
        self.catalog_sort.hide()
        self.mode = QComboBox()
        self.mode.addItems(['精确匹配', '模糊匹配（错字容错）'])
        form.addRow('匹配', self.mode)
        self.type_filter = QComboBox()
        for value, name in core.TYPE_OPTIONS:
            self.type_filter.addItem(name, None if value == 'all' else value)
        form.addRow('格式', self.type_filter)
        self.active_filter = QCheckBox('仅有效文件')
        self.active_filter.setChecked(self.store.settings.get('only_active', True))
        self.synonym_filter = QCheckBox('同义词扩展')
        self.synonym_filter.setChecked(self.store.settings.get('synonym', True))
        self.case_filter = QCheckBox('区分大小写')
        for checkbox in (self.active_filter, self.synonym_filter, self.case_filter):
            form.addRow(checkbox)
            checkbox.toggled.connect(self.update_option_badge)
        self.advanced_fields = {}
        for field, hint in [('文号', '文号'), ('名称', '文件名称')]:
            editor = QLineEdit()
            editor.setPlaceholderText(hint)
            editor.returnPressed.connect(self.start_search)
            self.advanced_fields[field] = editor
            form.addRow(field, editor)
        self.facet_combos = {}
        for field, hint in [('分类', '全部分类或手动输入'), ('日期', '年份或 2022..2024'), ('状态', '全部状态')]:
            combo = QComboBox()
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            combo.addItem({'分类':'全部分类','日期':'不限日期','状态':'全部状态'}[field], '')
            combo.lineEdit().setPlaceholderText(hint)
            if field == '状态':
                for title,value in (('有效','现行'),('已废止','废止'),('已替代','替代')):
                    combo.addItem(title, value)
            combo.lineEdit().clear()
            combo.currentIndexChanged.connect(lambda _, name=field: self.choose_facet(name))
            combo.activated.connect(lambda _, name=field: self.choose_facet(name))
            self.facet_combos[field] = combo
            self.advanced_fields[field] = combo.lineEdit()
            form.addRow(field, combo)
        self.advanced_fields['状态'].textEdited.connect(
            lambda value: self.active_filter.setChecked(value.strip() in ('现行','有效','现行有效')) if value.strip() else None)
        self.filter_chips = label('', 'muted')
        self.filter_chips.setWordWrap(True)
        self.filter_chips.setOpenExternalLinks(False)
        self.filter_chips.linkActivated.connect(self.remove_facet)
        self.filter_chips.hide()
        for editor in self.advanced_fields.values():
            editor.textChanged.connect(self.update_filter_chips)
            editor.returnPressed.connect(self.start_search)
        self.mode.currentIndexChanged.connect(self.update_option_badge)
        self.type_filter.currentIndexChanged.connect(self.update_option_badge)
        self.density = QComboBox()
        self.density.addItem('紧凑', True)
        self.density.addItem('舒适', False)
        self.density.setCurrentIndex(0 if self.store.settings.get('compact', True) else 1)
        self.density.currentIndexChanged.connect(self.density_changed)
        form.addRow('布局', self.density)
        self.syntax_button=button('搜索语法与示例',self.syntax_help)
        form.addRow(self.syntax_button)
        self.advanced_controls = [self.catalog_scope,self.catalog_root,self.mode,self.case_filter,
            self.advanced_fields['文号'],self.advanced_fields['名称'],self.facet_combos['日期'],
            self.facet_combos['状态'],self.density,self.syntax_button,self.catalog_ext]
        advanced_rows=[form.takeRow(control) for control in self.advanced_controls]
        self.advanced_toggle=button('高级条件 ▾')
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.toggled.connect(self.update_discovery_filters)
        form.addRow(self.advanced_toggle)
        for row in advanced_rows:
            if row.labelItem:
                form.addRow(row.labelItem.widget(),row.fieldItem.widget())
            else:
                form.addRow(row.fieldItem.widget())
        self.chart_toggle = QAction('显示命中分布', self)
        self.chart_toggle.setCheckable(True)
        self.chart_toggle.setChecked(False)
        self.chart_toggle.toggled.connect(self.toggle_distribution)

    def reset_search_options(self):
        self.catalog_scope.setCurrentIndex(0)
        self.catalog_ext.clear()
        self.catalog_root.clear()
        self.mode.setCurrentIndex(0)
        self.type_filter.setCurrentIndex(0)
        self.active_filter.setChecked(False)
        self.synonym_filter.setChecked(False)
        self.case_filter.setChecked(False)
        self.clear_facets()

    def update_option_badge(self, *_):
        if not hasattr(self, 'advanced_fields'):
            return
        content=self.discovery_scope.currentIndex()!=0
        directory=self.discovery_scope.currentIndex()!=1
        count=int(self.case_filter.isChecked())+int(bool(self.type_filter.currentData()))
        if content:
            count += sum(bool(editor.text().strip()) for editor in self.advanced_fields.values())
            count += sum((self.mode.currentIndex()>0,
                          self.active_filter.isChecked(),self.synonym_filter.isChecked()))
        if directory:
            count += sum((self.catalog_scope.currentIndex()>0,bool(self.catalog_ext.text().strip()),bool(self.catalog_root.text().strip())))
        self.options_button.setText(f'筛选 · {count} ' + ('▴' if self.options_button.isChecked() else '▾'))
        details = [self.mode.currentText()] if content else [self.catalog_scope.currentText()]
        details += [box.text() for box in (self.active_filter, self.synonym_filter) if content and box.isChecked()]
        if self.case_filter.isChecked():
            details.append('区分大小写')
        if content:
            details += [f'{name}：{editor.text()}' for name, editor in self.advanced_fields.items() if editor.text().strip()]
        if directory:
            details += [editor.text().strip() for editor in (self.catalog_ext,self.catalog_root) if editor.text().strip()]
        self.options_button.setToolTip('当前生效：' + '；'.join(details))
        chips=[]
        for field, control in self.advanced_fields.items():
            if content and control.text().strip():
                chips.append((field,field+'：'+control.text().strip()))
        for key,box in (('_active',self.active_filter),('_synonym',self.synonym_filter),('_case',self.case_filter)):
            if box.isChecked() and (key=='_case' or content):
                chips.append((key,box.text()))
        if self.type_filter.currentData():chips.append(('_type',self.type_filter.currentText()))
        if content and self.mode.currentIndex():chips.append(('_mode','模糊匹配'))
        if directory:
            for key,editor in (('_ext',self.catalog_ext),('_root',self.catalog_root)):
                if editor.text().strip():chips.append((key,editor.text().strip()))
            if self.catalog_scope.currentIndex():chips.append(('_match',self.catalog_scope.currentText()))
        links=[f'<a href="{html.escape(key)}">{html.escape(value[:20])} ×</a>' for key,value in chips[:2]]
        if len(chips)>2:links.append(f'<a href="_more">+{len(chips)-2}</a>')
        if chips:links.append('<a href="_clear">清空</a>')
        self.filter_chips.setText(' · '.join(links))
        self.filter_chips.setToolTip('；'.join(value for _,value in chips))
        self.filter_chips.setVisible(bool(chips) and self.search_mode.currentIndex()==0)

    def build_minimal_reader(self):
        self.preview_panel = QWidget()
        self.preview_panel.setObjectName('readerPanel')
        layout = QVBoxLayout(self.preview_panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.reader_header = QWidget()
        self.reader_header.setObjectName('readerHeader')
        self.reader_header.setFixedHeight(34)
        row = QHBoxLayout(self.reader_header)
        row.setContentsMargins(10, 0, 5, 0)
        row.setSpacing(3)
        self.doc_title = FileTitle('选择文件阅读')
        self.doc_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.doc_title.setMinimumWidth(60)
        self.doc_title.setCursor(Qt.CursorShape.PointingHandCursor)
        self.doc_title.clicked.connect(self.document_details)
        row.addWidget(self.doc_title, 1)
        self.doc_meta = QLabel(self.preview_panel)
        self.doc_meta.hide()
        self.preview_range = small_button('', '当前位置；点击跳转', self.goto_reader_position)
        self.preview_range.setMaximumWidth(110)
        row.addWidget(self.preview_range)
        self.hit_position = label('', 'muted')
        row.addWidget(self.hit_position)
        self.hit_previous = small_button('上一处', '上一处关键词命中（Shift+F3）；PDF 翻页在下方工具条', partial(self.next_hit, -1))
        self.hit_next = small_button('下一处', '下一处关键词命中（F3）；PDF 翻页在下方工具条', partial(self.next_hit, 1))
        row.addWidget(self.hit_previous)
        row.addWidget(self.hit_next)
        self.auto_play = small_button('自动播放', '自动按顺序播放每一处关键词命中，再次点击停止', self.toggle_auto_play)
        self.auto_play.setCheckable(True)
        row.addWidget(self.auto_play)
        self.focus_button = small_button('▣', '专注阅读：收起或恢复结果栏（Ctrl+Shift+R）', self.toggle_reading_focus)
        row.addWidget(self.focus_button)
        self.read_button = small_button('阅读', '阅读操作')
        row.addWidget(self.read_button)
        layout.addWidget(self.reader_header)
        self.reader_context = label('', 'muted')
        self.reader_context.setObjectName('readerContext')
        self.reader_context.setWordWrap(True)
        self.reader_context.hide()
        layout.addWidget(self.reader_context)
        self.reader_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.outline = QTreeWidget()
        self.outline.setHeaderLabels(['章节目录'])
        self.outline.itemActivated.connect(self._chapter_selected)
        self.outline.itemClicked.connect(self._chapter_selected)
        self.reader_splitter.addWidget(self.outline)
        self.outline.hide()
        from PySide6.QtWidgets import QTextBrowser
        self.reader = QTextBrowser()
        self.reader.setObjectName('documentReader')
        self.reader.viewport().installEventFilter(self)
        self.reader.setOpenLinks(False)
        self.reader.anchorClicked.connect(self.follow_reference)
        self.reader.highlighted.connect(self.reference_hovered)
        self.reader.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.reader.customContextMenuRequested.connect(self.reference_menu)
        self.reader.setHtml('<p style="color:#8793a3;text-align:center;margin-top:80px">选择左侧文件阅读正文</p>')
        self.reader_splitter.addWidget(self.reader)
        self.reader_splitter.setStretchFactor(1, 1)
        self.reader_stack = QStackedWidget()
        self.reader_stack.addWidget(self.reader_splitter)
        self.pdf_reader = PdfReader(self, compact=True)
        self.pdf_reader.page_changed.connect(self.pdf_page_changed)
        self.pdf_reader.page_changed.connect(lambda _: self.update_reader_position())
        self.pdf_reader.document.statusChanged.connect(lambda _: self.update_reader_position())
        self.reader_stack.addWidget(self.pdf_reader)
        layout.addWidget(self.pdf_reader.controls)
        self.pdf_reader.controls.hide()
        self.reader_stack.currentChanged.connect(lambda index:self.pdf_reader.controls.setVisible(index==1))
        layout.addWidget(self.reader_stack, 1)
        self.reader_kind = QComboBox()
        self.reader_kind.addItems(['提取文字', 'PDF 原始页面'])
        self.reader_kind.setEnabled(False)
        self.reader_kind.currentIndexChanged.connect(self.reader_type_changed)
        self.preview_mode = QComboBox()
        self.preview_mode.addItems(['整段', '上下文', '全文'])
        self.preview_mode.currentTextChanged.connect(self.reload_preview)
        self.reflow = QCheckBox('自适应换行')
        self.reflow.setChecked(self.store.settings.get('reflow', True))
        self.reflow.toggled.connect(self.reflow_changed)
        self.read_menu = QMenu(self)
        self.back_action = self.read_menu.addAction('返回上一阅读位置（Alt+Left）', self.go_back)
        self.read_menu.addAction('显示 / 隐藏章节目录', self.toggle_outline)
        self.read_menu.addAction('跳转到行号…', self.goto_line)
        self.read_menu.addSeparator()
        self.read_menu.addAction('复制正文 / 所选文字（Ctrl+C）', self.copy_text)
        self.read_menu.addAction('复制条款引用', self.copy_citation)
        self.bookmark_button = self.read_menu.addAction('收藏', self.toggle_bookmark)
        self.read_menu.addAction('打开原文件并定位', self.open_original)
        self.read_menu.addAction('正文放大（Ctrl+滚轮）', partial(self.change_reader_font, 1))
        self.read_menu.addAction('正文缩小（Ctrl+滚轮）', partial(self.change_reader_font, -1))
        self.read_menu.addSeparator()
        controls = QWidget()
        form = QFormLayout(controls)
        form.setContentsMargins(10, 4, 10, 4)
        form.addRow('显示', self.reader_kind)
        form.addRow('范围', self.preview_mode)
        form.addRow(self.reflow)
        self.embed_menu_widget(self.read_menu, controls)
        self.pdf_find_action = self.read_menu.addAction('在 PDF 原文中查找', lambda:self.pdf_reader.find.setFocus())
        self.reader_stack.currentChanged.connect(lambda index:self.pdf_find_action.setVisible(index==1))
        self.pdf_find_action.setVisible(False)
        self.read_menu.addAction('文档详细信息', self.document_details)
        self.read_button.setMenu(self.read_menu)
        self.read_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        focus = QShortcut(QKeySequence('Ctrl+Shift+R'), self)
        focus.activated.connect(self.toggle_reading_focus)

    @staticmethod
    def embed_menu_widget(menu, widget):
        action = QWidgetAction(menu)
        action.setDefaultWidget(widget)
        menu.addAction(action)

    def update_reader_position(self):
        if not self.current:
            return
        page = self.current.get('page')
        if self.reader_stack.currentIndex() == 1:
            page = self.pdf_reader.page.value()
        total = self.pdf_reader.document.pageCount() if self.reader_stack.currentIndex()==1 else 0
        self.preview_range.setText(f'{page}/{total} 页' if page and total else f'第 {page} 页' if page else f"第 {self.current['line']} 行")
        group = next((g for g in self.groups if g['path']==self.current['path']), None)
        lines = sorted(set(m['line_no'] for m in group.get('matches',[]))) if group else []
        current = next((i+1 for i,line in enumerate(lines) if line>=self.current['line']),len(lines))
        self.hit_position.setText(f'{current}/{len(lines)} 处' if lines else '')
        self.hit_previous.setEnabled(bool(lines))
        self.hit_next.setEnabled(bool(lines))
        self.hit_previous.setVisible(bool(lines))
        self.hit_next.setVisible(bool(lines))
        self.update_reader_context()

    def update_reader_context(self):
        import os
        current = getattr(self,'discovery_current',None) or self.current
        if not current:
            self.reader_context.hide()
            return
        if getattr(self,'_current_page','search')=='ai':
            path = os.path.normcase(os.path.abspath(current['path']))
            source = path in getattr(self,'_ai_source_paths',set())
            self.reader_context.setText('本轮回答参考资料 · 请结合对应条款核对结论' if source else '上次阅读 · 此资料未作为本轮回答依据')
            self.reader_context.show()
        else:
            stale = bool(self.query and self.result_stack.currentIndex()==0 and not getattr(self,'discovery_pending',0))
            self.reader_context.setText('上次阅读 · 此资料不属于当前搜索结果')
            self.reader_context.setVisible(stale)

    def goto_reader_position(self):
        if self.reader_stack.currentIndex() == 1:
            from PySide6.QtWidgets import QInputDialog
            page, ok = QInputDialog.getInt(self, '跳转到 PDF 页码', '页码',
                self.pdf_reader.page.value(), 1, max(1, self.pdf_reader.document.pageCount()))
            if ok:
                self.pdf_reader.goto(page - 1)
        else:
            self.goto_line()

    def document_details(self):
        if self.current:
            self.text_dialog('文档详细信息', '\n'.join((self.doc_title.text(), self.current['path'],
                self.doc_meta.text(), self.preview_range.toolTip())))

    def change_reader_font(self, step):
        size = max(9, min(28, self.store.settings.get('preview_font', 12) + step))
        position = self.reader.verticalScrollBar().value()
        self.store.settings['preview_font'] = size
        if self.current:
            self.show_preview(self.current)
            self.reader.verticalScrollBar().setValue(position)
        self.store.save()

    def toggle_reading_focus(self):
        if self.result_column.isHidden():
            self.result_column.show()
            self.splitter.setSizes(getattr(self, '_reading_sizes', [330, 900]))
        else:
            self._reading_sizes = self.splitter.sizes()
            self._reading_splitter_state = self.splitter.saveState()
            self.result_column.hide()
        self.focus_button.setText('▣' if not self.result_column.isHidden() else '▤')

    def build_application_menu(self):
        menu = QMenu(self)
        menu.addAction('任务与进度', lambda: self.navigate('tasks'))
        menu.addAction('工具中心', lambda: self.navigate('tools'))
        menu.addAction('设置', self.settings_dialog)
        help_menu = menu.addMenu('帮助')
        help_menu.addAction('完整使用说明（F1）', self.show_user_guide)
        help_menu.addAction('功能位置说明', self.feature_catalog)
        help_menu.addAction('意见反馈', partial(self.legacy_tool, 'feedback'))
        menu.addSeparator()
        menu.addAction('退出软件', self.exit_application)
        return menu

    def show_application_menu(self):
        self.build_application_menu().exec(self.menu_button.mapToGlobal(QPoint(0, self.menu_button.height())))

    def tools_entries(self):
        entries = []
        for name, method, description in [
            ('备份资料库', self.backup_library, '完整资料包或索引备份'),
            ('还原到独立资料库', self.restore_package, '在独立目录恢复'),
            ('原位还原并重启', self.restore_in_place, '校验后切换，原库留存备份'),
            ('打包软件和完整资料库', self.package_software, '包含完整运行组件'),
            ('打开资料库文件夹', self.open_store_folder, '查看资料库位置')]:
            entries.append(dict(name=name, group='资料安全', note=description, run=method))
        for key in ('ref_warnings', 'gap', 'index_health', 'ocr', 'download_watch', 'export_dir', 'synonyms'):
            descriptions={'ref_warnings':'查看失效引用和未入库引用 · 独立窗口','gap':'查找引用资料缺口 · 独立窗口',
                'index_health':'检查文件失联与索引状态 · 独立窗口','ocr':'检查扫描件文字识别能力',
                'download_watch':'选择下载监控文件夹','export_dir':'选择汇编、表格等默认保存位置',
                'synonyms':'管理搜索同义词与内置词组 · 独立窗口'}
            entries.append(dict(name=TOOLS[key][0],group='检查与配置',note=descriptions[key],
                run=self.ocr_diagnostics if key=='ocr' else partial(self.legacy_tool,key)))
        for name, method in [('联网查找资料', self.web_search),
                             ('迷你速查', self.quick_search), ('粘贴文本反查出处', self.reverse_lookup)]:
            entries.append(dict(name=name, group='辅助检索', note='独立检索工具', run=method))
        for key in ('import', 'library', 'bookmarks', 'dedupe', 'category', 'problem_files', 'versions', 'package', 'help'):
            descriptions={'import':'导入预检和冲突处理','library':'管理分类、元数据和文件','bookmarks':'查看收藏条款与备注',
                'dedupe':'分析内容重复和同名版本','category':'检查并修正分类','problem_files':'查看失联、无正文等问题',
                'versions':'选择新旧文件并比较差异','package':'备份完整资料库','help':'查看旧版操作说明'}
            entries.append(dict(name=TOOLS[key][0],group='旧版备用',note=descriptions[key]+' · 独立窗口',run=partial(self.legacy_tool,key)))
        return entries

    def _tools_page(self):
        page, layout = self._page()
        layout.addWidget(page_heading('工具中心', '专项工具按用途收纳。'))
        row = QHBoxLayout()
        self.tool_category = QComboBox()
        self.tool_category.addItems(['资料安全', '检查与配置', '辅助检索', '旧版备用', '全部'])
        self.tool_filter = QLineEdit()
        self.tool_filter.setPlaceholderText('查找工具…')
        row.addWidget(self.tool_category)
        row.addWidget(self.tool_filter, 1)
        row.addWidget(button('打开所选工具', self.run_selected_tool, True))
        layout.addLayout(row)
        self.tool_model = Records([('工具', 'name'), ('分组', 'group'), ('用途', 'note')])
        self.tool_table = records_table(self.tool_model)
        self.tool_table.setColumnWidth(0, 300)
        self.tool_table.setColumnWidth(1, 120)
        self.tool_table.doubleClicked.connect(self.run_selected_tool)
        layout.addWidget(self.tool_table, 1)
        self.tool_empty = label('没有匹配的工具', 'muted')
        self.tool_empty.hide()
        layout.addWidget(self.tool_empty)
        self.tool_category.currentTextChanged.connect(self.filter_tools)
        self.tool_filter.textChanged.connect(self.filter_tools)
        QTimer.singleShot(0, self.filter_tools)
        return page

    def filter_tools(self, *_):
        category, query = self.tool_category.currentText(), self.tool_filter.text().strip().casefold()
        rows = [entry for entry in self.tools_entries()
            if (category == '全部' or category == entry['group']) and
            (not query or query in (entry['name'] + entry['note']).casefold())]
        self.tool_model.set_rows(rows)
        self.tool_empty.setVisible(not rows)
        if rows:
            self.tool_table.selectRow(0)

    def run_selected_tool(self, *_):
        rows = self.tool_table.selectionModel().selectedRows()
        if rows:
            self.tool_model.rows[rows[0].row()]['run']()

    def open_store_folder(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.root)))

    def update_menu_badge(self):
        count = sum(self.jobs.jobs[key].visible for key in self.jobs.active)
        self.menu_button.setText('☰ 菜单' + (f' · {count}' if count else ''))
        self.menu_button.setToolTip('任务、工具、设置与帮助' + (f'；{count} 项任务进行中' if count else ''))

# -*- coding: utf-8 -*-
"""探索（目录发现）与智答模式的 Qt 接入层。

本模块把 search_app/discovery 的控制层接进主窗口：
- 探索/智答模式切换、检索范围（已选电脑位置 / 原资料库 / 两者）。
- 目录结果列表、图片/文件信息预览、打开原文件、打开所在位置、复制路径、
  从目录结果加入资料库（沿用原入库预检与冲突处理）。
- 搜索范围管理对话框：列卷、加目录、选中/取消、建目录、停止、更新、移除、
  查看失败原因与覆盖状态。

只操作目录索引，不复制/移动/改写用户文件。
"""
from __future__ import annotations

import os
import html
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from contextlib import closing

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal, QSignalBlocker, QEvent
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QMenu, QScrollArea,
)

from .widgets import button, label
from .result_snippets import SnippetTree, RESULT_DETAIL
from discovery.scan import SCAN_BACKOFF


def magnifier_icon(color='#3f5570', size=20, send=False):
    icon = QIcon()
    for ratio in (1, 1.25, 1.5, 2, 3):
        pixmap = QPixmap(round(size * ratio), round(size * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color))
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if send:
            middle = size // 2
            painter.drawLine(3, middle, size - 3, middle)
            painter.drawLine(size - 9, 4, size - 3, middle)
            painter.drawLine(size - 9, size - 4, size - 3, middle)
        else:
            radius = max(3, int(size * 0.30))
            center = int(size * 0.42)
            painter.drawEllipse(center - radius, center - radius, radius * 2, radius * 2)
            start = int(size * 0.63)
            painter.drawLine(start, start, size - 2, size - 2)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def _fmt_size(value):
    if value is None:
        return '—'
    value = float(value)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024 or unit == 'TB':
            return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024
    return f'{value:.1f} TB'


def _fmt_mtime(ns):
    if not ns:
        return '—'
    try:
        return datetime.fromtimestamp(ns / 1e9).strftime('%Y-%m-%d %H:%M')
    except (OSError, OverflowError, ValueError):
        return '—'


class DiscoveryResults(SnippetTree):
    """目录命中的平铺结果列表：名称 / 大小 / 修改时间。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(2)
        self.setHeaderHidden(True)
        self.setRootIsDecorated(True)
        self.setUniformRowHeights(False)
        self.setAlternatingRowColors(True)
        header = self.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(1, 56)
        self.setObjectName('discoveryResults')

    def set_hits(self, hits):
        self.clear()
        for hit in hits:
            kind = {'dir': '文件夹', 'link': '链接', 'content': '正文命中'}.get(hit.get('kind'), '名称 / 路径命中')
            name = hit.get('name') or hit.get('path', '')
            size = f"正文\n{hit['hits']} 处" if hit.get('kind')=='content' else '文件夹' if hit.get('kind')=='dir' else '名称'
            item = QTreeWidgetItem([name, size])
            item.setTextAlignment(1,Qt.AlignmentFlag.AlignCenter)
            item.setData(0,RESULT_DETAIL,str(Path(hit['path']).parent))
            item.setData(0, Qt.ItemDataRole.UserRole, hit)
            item.setToolTip(0, '\n'.join([hit.get('path', ''), kind,
                _fmt_size(hit.get('size')), _fmt_mtime(hit.get('mtime_ns')),
                '位置离线，保留旧记录' if not hit.get('online', True) else '']))
            item.setToolTip(1, hit.get('path', ''))
            self.addTopLevelItem(item)

    def keyPressEvent(self,event):
        item=self.currentItem()
        hit=item.data(0,Qt.ItemDataRole.UserRole) if item else None
        if isinstance(hit,dict) and hit.get('path') and event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter):
            self.itemDoubleClicked.emit(item,0)
            return
        super().keyPressEvent(event)


class DiscoveryPreview(QWidget):
    """目录文件的受控预览：图片原图缩放，其余显示文件信息。"""
    open_requested = Signal(str)
    location_requested = Signal(str)
    copy_requested = Signal(str)
    import_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_path = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.toolbar = QWidget()
        self.toolbar.setObjectName('readerHeader')
        self.toolbar.setFixedHeight(34)
        bar = QHBoxLayout(self.toolbar)
        bar.setContentsMargins(10, 0, 5, 0)
        bar.setSpacing(3)
        self.title = QLabel('选择文件查看')
        self.title.setObjectName('fileTitle')
        bar.addWidget(self.title, 1)
        actions = [
            ('打开原文件', '用系统默认程序打开真实文件', 'open'),
            ('所在位置', '在资源管理器中定位', 'location'),
            ('复制路径', '复制完整路径', 'copy'),
            ('加入资料库', '按原入库流程加入内容库', 'import'),
        ]
        for text, tip, key in actions:
            btn = button(text)
            btn.setToolTip(tip)
            if key == 'open':
                btn.clicked.connect(lambda: self.open_requested.emit(self.current_path or ''))
            elif key == 'location':
                btn.clicked.connect(lambda: self.location_requested.emit(self.current_path or ''))
            elif key == 'copy':
                btn.clicked.connect(lambda: self.copy_requested.emit(self.current_path or ''))
            else:
                btn.clicked.connect(lambda: self.import_requested.emit(self.current_path or ''))
            bar.addWidget(btn)
        layout.addWidget(self.toolbar)
        self.toolbar.hide()  # Actions live in the existing single reader toolbar.
        image_bar=QHBoxLayout()
        self.image_zoom=QComboBox()
        self.image_zoom.addItems(['适合窗口','原始比例','50%','100%','200%'])
        self.image_zoom.currentIndexChanged.connect(lambda _:self._scale_image(self._pixmap) if getattr(self,'_pixmap',None) else None)
        self.image_ratio=label('','muted')
        image_bar.addWidget(self.image_zoom)
        image_bar.addWidget(self.image_ratio)
        image_bar.addStretch()
        image_bar.addWidget(label('拖动查看 · Ctrl+滚轮缩放','muted'))
        self.image_controls=QWidget()
        self.image_controls.setLayout(image_bar)
        self.image_controls.hide()
        layout.addWidget(self.image_controls)
        self.image_area=QScrollArea()
        self.image_area.setWidgetResizable(False)
        self.image_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_area.hide()
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setStyleSheet('background:#101820;')
        self.image.setCursor(Qt.CursorShape.OpenHandCursor)
        self.image.installEventFilter(self)
        self.image_area.viewport().installEventFilter(self)
        self._image_pan=None
        self.image_area.setWidget(self.image)
        self.image.hide()
        layout.addWidget(self.image_area, 1)
        self.info = QTextBrowser()
        self.info.setOpenLinks(False)
        self.info.setStyleSheet('border:0; background:transparent;')
        layout.addWidget(self.info, 1)

    def show_hit(self, hit):
        path = hit.get('path', '')
        self.current_path = path
        self.title.setText(hit.get('name') or Path(path).name or path)
        self.title.setToolTip(path)
        ext = (Path(path).suffix.lower() if path else '')
        is_image = ext in ('.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp', '.tif', '.tiff')
        self._pixmap = None
        self.image.hide()
        self.image_area.hide()
        self.image_controls.hide()
        self.image_zoom.setCurrentIndex(0)
        self.info.show()
        kind_text = {'dir': '文件夹', 'link': '链接', 'content': '正文命中'}.get(
            hit.get('kind'), '扩展名 ' + (hit.get('ext') or '无'))
        rows = [
            '<html><body style="font-family:Microsoft YaHei UI;">',
            '<table cellspacing="8">',
            f'<tr><td><b>路径</b></td><td>{html.escape(path)}</td></tr>',
            f'<tr><td><b>类型</b></td><td>{kind_text}</td></tr>',
            f'<tr><td><b>大小</b></td><td>{_fmt_size(hit.get("size"))}</td></tr>',
            f'<tr><td><b>修改时间</b></td><td>{_fmt_mtime(hit.get("mtime_ns"))}</td></tr>',
            f'<tr><td><b>状态</b></td><td>{"离线 / 无法访问" if not hit.get("online") else "可访问"}</td></tr>',
            '</table>',
        ]
        if not os.path.exists(path):
            rows.append('<p style="color:#c13a28;">原文件已更名、删除或离线；目录索引仅保留记录。</p>')
        elif hit.get('kind') == 'dir':
            rows.append('<p>目录命中：双击结果或「打开原文件」进入该目录。</p>')
        elif hit.get('kind') == 'content':
            rows.append('<p>该文件同时命中正文；点击结果打开原文预览。</p>')
        else:
            rows.append('<p>尚未分析内容，此处只显示文件信息。双击结果可打开原文件。</p>')
        rows.append('</body></html>')
        self.info.setHtml(''.join(rows))

    def _scale_image(self, pixmap):
        self._pixmap = pixmap
        self.image_area.show()
        self.image_controls.show()
        viewport=self.image_area.viewport().size()
        scale=min(viewport.width()/max(1,pixmap.width()),viewport.height()/max(1,pixmap.height()))
        if self.image_zoom.currentIndex()==1:scale=1
        elif self.image_zoom.currentIndex()>1:scale=float(self.image_zoom.currentText().rstrip('%'))/100
        scaled=pixmap.scaled(max(1,round(pixmap.width()*scale)),max(1,round(pixmap.height()*scale)),
            Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
        self.image.setPixmap(scaled)
        self.image.resize(scaled.size())
        self.image_ratio.setText(f'{scale*100:.0f}%')

    def eventFilter(self,target,event):
        if target in (self.image,self.image_area.viewport()):
            if target is self.image_area.viewport() and event.type()==QEvent.Type.Resize and self.image.isVisible() and getattr(self,'_pixmap',None) and self.image_zoom.currentIndex()==0:
                QTimer.singleShot(0,lambda:self._scale_image(self._pixmap) if getattr(self,'_pixmap',None) else None)
            if event.type()==QEvent.Type.Wheel and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                step=1 if event.angleDelta().y()>0 else -1
                index=self.image_zoom.currentIndex()
                index=(3 if step>0 else 2) if index<2 else max(2,min(4,index+step))
                self.image_zoom.setCurrentIndex(index)
                return True
            if target is self.image:
                if event.type()==QEvent.Type.MouseButtonPress and event.button()==Qt.MouseButton.LeftButton:
                    self._image_pan=(event.globalPosition(),self.image_area.horizontalScrollBar().value(),self.image_area.verticalScrollBar().value())
                    self.image.setCursor(Qt.CursorShape.ClosedHandCursor)
                    return True
                if event.type()==QEvent.Type.MouseMove and self._image_pan:
                    point,x,y=self._image_pan
                    delta=event.globalPosition()-point
                    self.image_area.horizontalScrollBar().setValue(x-round(delta.x()))
                    self.image_area.verticalScrollBar().setValue(y-round(delta.y()))
                    return True
                if event.type()==QEvent.Type.MouseButtonRelease:
                    self._image_pan=None
                    self.image.setCursor(Qt.CursorShape.OpenHandCursor)
        return super().eventFilter(target,event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.image.isVisible() and getattr(self, '_pixmap', None):
            self._scale_image(self._pixmap)


class RangeDialog(QDialog):
    """搜索范围管理：卷与目录、通道能力、状态、建目录/停止/更新/移除。"""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        self.setWindowTitle('搜索范围')
        self.resize(920, 560)
        layout = QVBoxLayout(self)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(6)
        self.tree.setHeaderLabels(['范围', '通道', '状态', '条目数', '失败', '说明'])
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.tree, 1)
        diagnostics=QCheckBox('显示索引通道等诊断信息')
        diagnostics.toggled.connect(lambda checked:self.tree.setColumnHidden(1,not checked))
        self.tree.setColumnHidden(1,True)
        layout.addWidget(diagnostics)
        actions = QHBoxLayout()
        actions.addWidget(button('添加目录…', self.add_directory))
        actions.addWidget(button('刷新卷', self.refresh_volumes))
        actions.addWidget(button('建立当前目录', self.start_selected))
        actions.addWidget(button('停止当前任务', self.stop_selected))
        actions.addWidget(button('更新当前目录', self.update_selected))
        actions.addWidget(button('移除范围', self.remove_selected))
        actions.addStretch()
        actions.addWidget(button('关闭', self.accept))
        layout.addLayout(actions)
        self.status = label('正在读取范围…', 'muted')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.auto_scan = QCheckBox('启动后继续已选范围，并在后台更新')
        self.auto_scan.setChecked(self.window.discovery._config.get('auto_scan', False))
        self.auto_scan.toggled.connect(lambda enabled: self.window.jobs.submit('保存目录偏好',
            lambda progress, stop: self.window.discovery.set_auto_scan(enabled), visible=False))
        layout.addWidget(self.auto_scan)
        self.tree.itemChanged.connect(self.on_item_changed)
        self._loading = False
        self._refresh_job = None
        self.refresh()

    def _current_id(self):
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def _current_checked(self):
        item = self.tree.currentItem()
        if item is None:
            return None, None
        return item.data(0, Qt.ItemDataRole.UserRole), item.checkState(0) == Qt.CheckState.Checked

    def on_item_changed(self, item, column):
        if self._loading or column != 0:
            return
        source_id = item.data(0, Qt.ItemDataRole.UserRole)
        if source_id:
            selected = item.checkState(0) == Qt.CheckState.Checked
            self.window.invalidate_discovery_search()
            self.window.jobs.submit('调整搜索范围',
                lambda progress, stop: self.window.discovery.select(source_id, selected),
                lambda _: (self.refresh(), self.window.schedule_search()), error=self.window.show_error,
                visible=False)

    def refresh(self):
        if self._refresh_job in self.window.jobs.active:
            return
        self._refresh_job = self.window.jobs.submit('读取搜索范围',
            lambda progress, stop: self.window.discovery.list_ranges(), self._show_ranges,
            error=lambda detail: self.status.setText(detail.splitlines()[-1]), visible=False)

    def _show_ranges(self, rows):
        current_id = self._current_id()
        self._loading = True
        try:
            self.tree.clear()
            for row in rows:
                item = QTreeWidgetItem([
                    row['root'],
                    '原生通道可用' if row['native_usn_readable'] and row['state']=='new' else {'usn': 'NTFS 原生', 'generic': '通用目录'}.get(row['provider'], row['provider']),
                    {'new':'未建立', 'complete':'已更新', 'scanning':'建立中', 'partial':'未完成',
                     'needs_rescan':'待更新', 'offline':'离线'}.get(row['state'], row['state']),
                    str(row['discovered_count']),
                    str(row['failed_count']),
                    range_reason(row['rescan_reason'] or row['cap_reason'] or ''),
                ])
                item.setData(0, Qt.ItemDataRole.UserRole, row['source_id'])
                item.setCheckState(0, Qt.CheckState.Checked if row['selected'] else Qt.CheckState.Unchecked)
                item.setToolTip(0, row['root'])
                item.setToolTip(5, (row['rescan_reason'] or row['cap_reason'] or ''))
                if row['running']:
                    item.setForeground(2, QColor('#c05621'))
                self.tree.addTopLevelItem(item)
                if row['source_id'] == current_id:
                    self.tree.setCurrentItem(item)
        finally:
            self._loading = False
        self.status.setText(f'共 {self.tree.topLevelItemCount()} 个范围。'
                            '勾选控制哪些位置纳入搜索；建立、停止、更新作用于当前高亮行。默认跳过系统目录；权限受限会显示原因。')

    def add_directory(self):
        path = QFileDialog.getExistingDirectory(self, '添加搜索范围', '')
        if path:
            self.window.jobs.submit('添加搜索范围',
                lambda progress, stop: self.window.discovery.add_directory(path),
                lambda _: (self.refresh(), self.window.refresh_discovery_coverage()),
                error=self.window.show_error)

    def refresh_volumes(self):
        self.status.setText('正在探测本机卷…')
        self.window.jobs.submit('探测本机卷',
            lambda progress, stop: self.window.discovery.ensure_volume_sources(),
            lambda _: self.refresh(), error=self.window.show_error, visible=False)

    def start_selected(self):
        source_id, _ = self._current_checked()
        if not source_id:
            return
        self.status.setText('已提交：建立目录（后台执行）…')
        self.window.submit_discovery_task(source_id, scan=True)

    def stop_selected(self):
        source_id, _ = self._current_checked()
        if source_id:
            self.window._discovery_active_ranges.discard(source_id)
            self.window.discovery.cancel(source_id)
            key = self.window._discovery_tasks.get(source_id)
            if key:
                self.window.jobs.cancel(key)
            self.refresh()
            self.status.setText('正在停止，已发现的记录会保留')

    def update_selected(self):
        source_id, _ = self._current_checked()
        if not source_id:
            return
        self.window.submit_discovery_task(source_id)

    def remove_selected(self):
        source_id, _ = self._current_checked()
        if not source_id:
            return
        self.window._discovery_active_ranges.discard(source_id)
        self.window.discovery.cancel(source_id)
        key = self.window._discovery_tasks.get(source_id)
        if key:
            self.window.jobs.cancel(key)
        self.window.jobs.submit('移除目录索引',
            lambda progress, stop: self.window.discovery.remove_range(source_id),
            lambda _: (self.refresh(), self.window.schedule_search()), error=self.window.show_error)


class DiscoveryUiMixin:
    """Queued Qt integration. All disk work belongs to Jobs workers."""
    def install_discovery(self):
        from discovery.scheduler import DiscoveryController
        root = Path(self.store.root)
        self.discovery = DiscoveryController(str(root/'catalog.db'), str(root/'discovery.json'))
        self.discovery_current = None
        self.discovery_generation = 0
        self._discovery_jobs = set()
        self._discovery_tasks = {}
        self._discovery_active_ranges = set()
        self._discovery_next = {}
        self._startup_scan_queue = []
        self._startup_scan_running = False
        self._last_activity = 0.0
        self._range_rows = []
        self._coverage_text = '选择上方搜索范围；原资料库可直接检索'
        self._coverage_job = None
        self._content_read_menu = self.read_menu
        self._range_dialog = None
        self._discovery_errors = []
        self.discovery_pending = 0
        self._preserve_discovery_view = False
        self._explore_filter_visible = False
        self._explore_context = None
        self._explore_dirty = False
        self.explore_preferences_timer = QTimer(self)
        self.explore_preferences_timer.setSingleShot(True)
        self.explore_preferences_timer.setInterval(400)
        self.explore_preferences_timer.timeout.connect(self.save_explore_preferences)
        self.search_mode.currentIndexChanged.connect(self.discovery_mode_changed)
        self.discovery_scope.currentIndexChanged.connect(self.discovery_filters_changed)
        self.discovery_results = DiscoveryResults()
        self.discovery_results.currentItemChanged.connect(self.discovery_result_selected)
        self.discovery_results.itemDoubleClicked.connect(self.discovery_double_clicked)
        self.discovery_results.more_requested.connect(lambda item:self.discovery_result_selected(item,None)
            if item.data(0,Qt.ItemDataRole.UserRole).get('type')=='more' else None)
        self.discovery_results.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.discovery_results.customContextMenuRequested.connect(self.discovery_context_menu)
        self.result_stack.addWidget(self.discovery_results)
        self.discovery_more = button('继续加载', self.load_more_discovery)
        self.discovery_more.hide()
        self.result_column.layout().insertWidget(self.result_column.layout().count()-1, self.discovery_more)
        self.discovery_preview = DiscoveryPreview()
        self.reader_stack.addWidget(self.discovery_preview)
        self._discovery_read_menu = QMenu(self)
        for title, callback in [('打开原文件',self.open_discovery_original),('所在位置',self.open_discovery_location),
                                ('复制路径',self.copy_discovery_path),('加入资料库',self.import_discovery_to_library)]:
            self._discovery_read_menu.addAction(title, callback)
        self._discovery_read_menu.addSeparator()
        self._discovery_read_menu.addAction('专注阅读', self.toggle_reading_focus)
        pdf_actions=self._discovery_read_menu.addMenu('PDF 显示')
        for index in range(self.pdf_reader.zoom.count()):
            pdf_actions.addAction(self.pdf_reader.zoom.itemText(index),lambda checked=False,i=index:self.pdf_reader.zoom.setCurrentIndex(i))
        self._discovery_pdf_actions=pdf_actions
        self.pdf_reader.page_changed.connect(lambda page:self.preview_range.setText(f'第 {page} 页') if self.discovery_current else None)
        self._ai_result_index = self.result_stack.addWidget(self._ai_page())
        self.page_indices['ai'] = self.page_indices['search']
        self._explore_draft = self._ai_draft = ''
        self.discovery_timer = QTimer(self)
        self.discovery_timer.setInterval(1000 * self.discovery._config['auto_sync_seconds'])
        self.discovery_timer.timeout.connect(self._discovery_tick)
        self.discovery_timer.start()
        self.jobs.settled.connect(self._discovery_settled)
        self.catalog_scope.currentIndexChanged.connect(self.discovery_filters_changed)
        self.catalog_sort.currentIndexChanged.connect(self.discovery_sort_changed)
        for control in (self.catalog_ext,self.catalog_root):
            control.textChanged.connect(self.discovery_filters_changed)
        QTimer.singleShot(250,self._discovery_startup)
        self.update_discovery_filters()
        self.result_empty.setText('输入关键词开始搜索\n\n点击上方“范围”选择电脑文件、资料库或两者\n电脑目录可在“管理搜索范围”中添加')
        self.result_empty.linkActivated.connect(lambda action:self.open_range_manager() if action=='ranges' else
            self.reset_search_options() if action=='clear' else self.choose_files())

    def discovery_sort_changed(self, *_):
        self._preserve_discovery_view = True
        self.discovery_filters_changed()

    def _discovery_startup(self):
        if self._closing_requested:
            return
        def work(progress,stop):
            self.discovery.ensure_volume_sources()
            self.discovery.resume_watchers()
            return self.discovery.list_ranges()
        def done(rows):
            self._range_rows = rows
            self.refresh_discovery_coverage()
            if self.discovery._config.get('auto_scan'):
                import time as _t
                self._startup_scan_queue = [r for r in (rows or []) if r.get('selected')]
                now = _t.monotonic()
                for r in self._startup_scan_queue:
                    self._discovery_next[r['source_id']] = now + 3600
                QTimer.singleShot(3000, self._drain_startup_scans)
        self.jobs.submit('检查电脑搜索范围',work,done,error=self.show_error,visible=False)

    def auto_continue_scans(self, rows=None):
        for row in rows or self._range_rows:
            if row['selected']:
                self.submit_discovery_task(row['source_id'], scan=row['state']=='new', visible=True)

    def _drain_startup_scans(self):
        if self._closing_requested or self._startup_scan_running:
            return
        import time as _t
        if _t.monotonic() - self._last_activity < 1.5:
            QTimer.singleShot(800, self._drain_startup_scans)
            return
        if self.discovery_pending:
            QTimer.singleShot(1500, self._drain_startup_scans)
            return
        while self._startup_scan_queue and not self._startup_scan_running:
            row = self._startup_scan_queue.pop(0)
            sid = row['source_id']
            if sid not in self.discovery.selected_ids():
                continue
            self._startup_scan_running = True
            def advance(_report=None, _detail=None):
                self._startup_scan_running = False
                QTimer.singleShot(600, self._drain_startup_scans)
            self.submit_discovery_task(sid, scan=(row['state']=='new'),
                                       visible=False, on_done=advance, on_failed=advance)
            return

    def submit_discovery_task(self, source_id, scan=False, visible=True, on_done=None, on_failed=None):
        if self._closing_requested or self._discovery_tasks.get(source_id) in self.jobs.active:
            return
        self._discovery_active_ranges.add(source_id)
        def work(progress, stop):
            if scan:
                self.discovery.select(source_id,True)
            method = self.discovery.start_scan if scan else self.discovery.sync
            return method(source_id,progress=progress,stop=stop)
        def done(report):
            import time
            self._discovery_next[source_id] = time.monotonic() + (60 if report.get('state') in ('partial','offline') else 0)
            self.refresh_discovery_coverage()
            if self._range_dialog and self._range_dialog.isVisible():
                self._range_dialog.refresh()
            if self.discovery_is_explore() and self.search_input.text().strip():
                self._preserve_discovery_view = True
                self.schedule_search()
            elif not self.discovery_is_explore():
                self._explore_dirty = True
            if on_done:
                on_done(report)
        def failed(detail):
            import time
            self._discovery_next[source_id] = time.monotonic()+60
            if visible:
                self.notify(detail.strip().splitlines()[-1])
            if on_failed:
                on_failed(detail)
        self._discovery_tasks[source_id] = self.jobs.submit('建立目录' if scan else '更新电脑目录',work,
            done,error=failed,visible=visible)

    def _discovery_settled(self):
        self._discovery_jobs.intersection_update(self.jobs.active)
        for sid,key in list(self._discovery_tasks.items()):
            if key not in self.jobs.active:
                del self._discovery_tasks[sid]
                if self._range_dialog and self._range_dialog.isVisible():
                    self._range_dialog.refresh()

    def _discovery_tick(self):
        import time
        if self._closing_requested:
            return
        if time.monotonic() - self._last_activity < 2.0:
            self.refresh_discovery_coverage()
            return
        selected = set(self.discovery.selected_ids())
        for sid in self._discovery_active_ranges & selected:
            if time.monotonic() >= self._discovery_next.get(sid,0):
                self.submit_discovery_task(sid,visible=False)
        self.refresh_discovery_coverage()
        if self._range_dialog and self._range_dialog.isVisible():
            self._range_dialog.refresh()

    def discovery_is_explore(self):
        return self.search_mode.currentIndex()==0

    def discovery_scope_kind(self):
        return ('dir','content','both')[self.discovery_scope.currentIndex()]

    def invalidate_discovery_search(self):
        self.discovery_generation += 1
        self._search_generation += 1
        for key in list(self._discovery_jobs):
            self.jobs.cancel(key)
        if self.search_job in self.jobs.active:
            self.jobs.cancel(self.search_job)
        self.discovery_more.hide()
        self.search_button.setEnabled(True)

    def discovery_filters_changed(self,*_):
        self.invalidate_discovery_search()
        self.update_discovery_filters()
        self.update_option_badge()
        self._explore_dirty = True
        self.store.settings['explore_preferences'] = {
            'scope': self.discovery_scope.currentIndex(), 'match': self.catalog_scope.currentIndex(),
            'ext': self.catalog_ext.text(), 'root': self.catalog_root.text(), 'sort': self.catalog_sort.currentIndex(),
        }
        self.explore_preferences_timer.start()
        if hasattr(self,'search_debounce') and self.discovery_is_explore():
            self.schedule_search()

    def save_explore_preferences(self):
        self.explore_preferences_timer.stop()
        self.store.save()

    def explore_signature(self):
        return (self.search_input.text(), self.discovery_scope.currentIndex(), self.catalog_scope.currentIndex(),
                self.catalog_ext.text(), self.catalog_root.text(), self.catalog_sort.currentIndex(),
                self.mode.currentIndex(), self.type_filter.currentIndex(), self.active_filter.isChecked(),
                self.synonym_filter.isChecked(), self.case_filter.isChecked(),
                tuple(field.text() for field in self.advanced_fields.values()), tuple(self.discovery.selected_ids()))

    def update_discovery_filters(self):
        if not hasattr(self,'options_panel'):
            return
        form=self.options_panel.layout()
        content=self.discovery_scope_kind()!='dir'
        directory=self.discovery_scope_kind()!='content'
        self.range_button.setText('范围 ▾')
        self.scope_status.setText(('电脑文件','资料库','两者')[self.discovery_scope.currentIndex()])
        self.range_button.setToolTip('当前范围：' + ('电脑文件', '资料库', '电脑文件和资料库')[self.discovery_scope.currentIndex()] + '\n点击切换或管理搜索范围')
        for control in [self.mode,self.active_filter,self.synonym_filter,*self.advanced_fields.values()]:
            # Editable combo editors have a different row owner.
            if control in self.advanced_fields.values() and control.parent() in self.facet_combos.values():
                control=control.parent()
            form.setRowVisible(control,content)
        form.setRowVisible(self.type_filter,True)
        for control in (self.catalog_scope,self.catalog_ext,self.catalog_root):
            form.setRowVisible(control,directory)
        if hasattr(self,'advanced_controls'):
            self.advanced_toggle.setText('高级条件 ▴' if self.advanced_toggle.isChecked() else '高级条件 ▾')
            directory_controls=(self.catalog_scope,self.catalog_root,self.catalog_ext)
            common_controls=(self.case_filter,self.density,self.syntax_button)
            for control in self.advanced_controls:
                applicable=directory if control in directory_controls else True if control in common_controls else content
                form.setRowVisible(control,applicable and self.advanced_toggle.isChecked())

    def discovery_mode_changed(self,index):
        pending = self.search_debounce.isActive() or self.search_job in self.jobs.active or any(
            key in self.jobs.active for key in self._discovery_jobs)
        pending = pending or bool(getattr(self, '_result_queue', []))
        if index == 1:
            self._explore_sizes=self.splitter.sizes()
            self._explore_splitter_state=self.splitter.saveState()
            self._explore_context = {
                'signature': self.explore_signature(), 'pending': pending,
                'result_index': self.result_stack.currentIndex(), 'heading': self.result_heading.text(),
                'summary': self.result_summary.text(), 'tooltip': self.result_summary.toolTip(),
                'distribution': self.distribution.isVisible(),
                'reader': (self.current['path'], self.current['line']) if self.current else None,
                'directory': dict(self.discovery_current) if self.discovery_current else None,
            }
            self._explore_dirty = False
        self.search_debounce.stop()
        self.invalidate_discovery_search()
        if index==1:
            self._explore_filter_visible = not self.result_filter.isHidden()
            self._explore_draft=self.search_input.text()
            self.search_input.setText(self._ai_draft)
            self._current_page='ai'
            self.pages.setCurrentIndex(self.page_indices['search'])
            self.nav['search'].setChecked(True)
            self.result_stack.setCurrentIndex(self._ai_result_index)
            self.result_heading.hide()
            self.input_stack.setCurrentIndex(1)
            self.ai_input.setPlainText(self.search_input.text())
            self.ai_input.fit_height()
            self.input_stack.setFixedHeight(self.ai_input.height())
            self.search_input.setPlaceholderText('向资料提问…')
            self.search_button.setToolTip('发送问题')
            self.search_button.setAccessibleName('发送问题')
            self.search_button.setIcon(magnifier_icon('#ffffff', send=True))
            self.result_summary.setText('选择来源可在右侧查看依据')
            self.options_button.hide()
            self.options_scroll.hide()
            self.range_button.hide()
            self.scope_status.hide()
            self.filter_chips.hide()
            self.distribution.hide()
            self.result_filter.hide()
            self.result_actions.hide()
            self.update_reader_context()
            self.update_ai_layout()
        else:
            if hasattr(self,'ai_job'):
                self.stop_ai()
            self._ai_draft=self.search_input.text()
            self.search_input.setText(self._explore_draft)
            self.input_stack.setCurrentIndex(0)
            self.input_stack.setFixedHeight(28)
            self.result_heading.show()
            self.preview_panel.show()
            self.splitter.default_sizes=(330,900)
            if hasattr(self,'_explore_sizes'):self.splitter.setSizes(self._explore_sizes)
            self._current_page='search'
            self.pages.setCurrentIndex(self.page_indices['search'])
            self.search_input.setPlaceholderText('关键词、文件名或路径…')
            self.search_button.setToolTip('搜索')
            self.search_button.setAccessibleName('搜索')
            self.search_button.setIcon(magnifier_icon('#ffffff'))
            self.options_button.show()
            self.options_scroll.setVisible(self.options_button.isChecked())
            self.range_button.show()
            self.scope_status.show()
            self.result_filter.setVisible(self._explore_filter_visible)
            self.result_actions.show()
            context = self._explore_context
            if context and not context['pending'] and not self._explore_dirty and context['signature'] == self.explore_signature():
                self.result_stack.setCurrentIndex(context['result_index'])
                self.result_heading.setText(context['heading'])
                self.result_summary.setText(context['summary'])
                self.result_summary.setToolTip(context['tooltip'])
                self.distribution.setVisible(context['distribution'])
                location = context['reader']
                if location and (not self.current or (self.current['path'], self.current['line']) != location):
                    self.open_preview(*location)
                elif context['directory'] and self.discovery_current != context['directory']:
                    self.preview_discovery_file(context['directory'])
            else:
                self._preserve_discovery_view = True
                self.start_search()
            self.update_reader_context()
        (self.ai_input if index==1 else self.search_input).setFocus()

    def _content_query(self):
        query=self.search_input.text().strip()
        for key,field in self.advanced_fields.items():
            if field.text().strip():
                query+=f' {key}:{field.text().strip()}'
        return query.strip()

    def run_discovery_search(self):
        import time
        self._last_activity = time.monotonic()
        self.search_debounce.stop()
        self.invalidate_discovery_search()
        generation=self.discovery_generation
        query=self.search_input.text().strip()
        content_query=self._content_query() if self.discovery_scope_kind()=='both' else ''
        previous_query = self.query
        self.query=query or content_query
        self.groups=[]
        self._last_explore_groups=[]
        self._last_search_result=None
        self.discovery_dir_hits=[]
        self.discovery_content_hits=None
        self.discovery_pending=0
        self._discovery_has_more=False
        self._discovery_offset=0
        self._discovery_errors=[]
        preserve_view = self._preserve_discovery_view and self.query == previous_query
        self._preserve_discovery_view = False
        self._discovery_view = self.capture_discovery_view() if preserve_view else {}
        if not preserve_view:
            self.discovery_results.clear()
        self.distribution.hide()
        if not preserve_view:
            self.result_filter.clear()
        if not query and not content_query:
            self.result_stack.setCurrentIndex(0)
            self.result_heading.setText('搜索结果')
            self.result_empty.setText('输入关键词开始探索\n\n在“筛选”中选择电脑位置或资料库')
            self.result_summary.setText(self.discovery_coverage_text())
            return
        from discovery.model import SearchRequest
        sort,descending=self.catalog_sort.currentData()
        import searcher
        format_exts=set(searcher.FILE_TYPE_EXTS.get(self.type_filter.currentData(),()))
        manual_exts={'.'+value.strip().lstrip('.').casefold() for value in self.catalog_ext.text().replace(';',',').split(',') if value.strip()}
        extensions=(sorted(format_exts & manual_exts) or ['.__no_matching_extension__']) if format_exts and manual_exts else sorted(format_exts or manual_exts) or None
        self._catalog_request=SearchRequest(query=query,scope=self.catalog_scope.currentData(),
            ext=extensions,
            root=self.catalog_root.text().strip() or None,case_sensitive=self.case_filter.isChecked(),
            sort=sort,descending=descending,limit=100)
        self.result_heading.setText('正在搜索…')
        self.store.settings['recent_keywords']=[query]+[q for q in self.store.settings.get('recent_keywords',[]) if q!=query][:24]
        self.store.settings['only_active'] = self.active_filter.isChecked()
        self.store.settings['synonym'] = self.synonym_filter.isChecked()
        self.store.save()
        if query:
            self._run_dir_search(query,generation)
        if content_query:
            self._run_content_search(self.query,generation)

    def _run_dir_search(self, query, generation, append=False):
        from dataclasses import replace
        self.discovery_pending+=1
        SCAN_BACKOFF.set()
        request=replace(self._catalog_request,offset=self._discovery_offset)
        def work(progress,stop):
            page=self.discovery.search(replace(request,cancel=stop.is_set),dedupe_paths=True).to_dict()
            if page['results']:
                with closing(sqlite3.connect(self.store.db,timeout=15)) as conn:
                    names=[hit['path'] for hit in page['results']]
                    indexed={r[0] for r in conn.execute('SELECT path FROM documents WHERE path IN ('+','.join('?' for _ in names)+')',names)}
                for hit in page['results']:
                    hit['indexed']=hit['path'] in indexed
            return page
        def done(page):
            if generation!=self.discovery_generation:
                return
            self.discovery_dir_hits.extend(page['results'])
            self._discovery_offset+=len(page['results'])
            self._discovery_has_more=page['has_more']
            self._discovery_maybe_render(query)
        key=self.jobs.submit('探索：'+query,work,done,error=lambda detail:self._discovery_fail(detail,generation),visible=False)
        self._discovery_jobs.add(key)
        self.search_job=key

    def load_more_discovery(self):
        if self._discovery_has_more and not self.discovery_pending:
            self.discovery_more.setEnabled(False)
            self._run_dir_search(self.query,self.discovery_generation,append=True)

    def _run_content_search(self, query, generation):
        self.discovery_pending+=1
        SCAN_BACKOFF.set()
        content_query=self._content_query()
        options=dict(fuzzy=self.mode.currentIndex()==1,file_type=self.type_filter.currentData(),
                     active=self.active_filter.isChecked(),synonyms=self.synonym_filter.isChecked(),
                     case_sensitive=self.case_filter.isChecked())
        def work(progress,stop):
            return self.store.search(content_query,stop=stop,**options)
        def done(result):
            if generation!=self.discovery_generation:
                return
            self.discovery_content_hits=result
            self.groups=result['groups']
            self._last_explore_groups=list(self.groups)
            self._last_search_result=result
            self._search_details=result.get('details',{})
            self._discovery_maybe_render(query)
        key=self.jobs.submit('正文检索：'+query,work,done,error=lambda detail:self._discovery_fail(detail,generation),visible=False)
        self._discovery_jobs.add(key)

    def _discovery_fail(self,detail,generation):
        if generation==self.discovery_generation:
            self._discovery_errors.append(detail.strip().splitlines()[-1])
            self._discovery_maybe_render(self.query)

    def _discovery_maybe_render(self,query):
        self.discovery_pending=max(0,self.discovery_pending-1)
        if not self.discovery_pending:
            SCAN_BACKOFF.clear()
        self.render_discovery_results()

    def capture_discovery_view(self):
        from .result_snippets import EXPANDED_LINES
        tree = self.discovery_results
        roots = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        return {
            'expanded': {norm_path(item.data(0, Qt.ItemDataRole.UserRole)['path']): item.isExpanded()
                         for item in roots},
            'snippets': {norm_path(item.data(0, Qt.ItemDataRole.UserRole)['path']): {
                child.data(0, Qt.ItemDataRole.UserRole): child.data(0, EXPANDED_LINES)
                for child in (item.child(j) for j in range(item.childCount()))
                if isinstance(child.data(0, Qt.ItemDataRole.UserRole), tuple)} for item in roots},
            'scroll': tree.verticalScrollBar().value(),
            'selected': tree.currentItem().data(0, Qt.ItemDataRole.UserRole) if tree.currentItem() else None,
        }

    def render_discovery_results(self):
        from .search_experience import rank_groups
        from .result_snippets import EXPANDED_LINES
        view = self.capture_discovery_view() if self.discovery_results.topLevelItemCount() else getattr(self, '_discovery_view', {})
        content=self.discovery_content_hits
        merged=[]
        by_path={norm_path(hit['path']):dict(hit) for hit in self.discovery_dir_hits}
        if content:
            ordered = rank_groups(content['groups'], content.get('details', {}), self.query, self.sort_order.currentData())
            deduped_groups = []
            seen_group_paths = set()
            for group in ordered:
                key = norm_path(group['path'])
                if key in seen_group_paths:
                    continue
                seen_group_paths.add(key)
                deduped_groups.append(group)
            self.groups = deduped_groups
            for group in deduped_groups:
                row=by_path.pop(norm_path(group['path']),{})
                row.update(name=group['name'],path=group['path'],kind='content',hits=group['hits'],
                           first_line=group['first_line'],group=group,indexed=True)
                merged.append(row)
        merged.extend(by_path.values())
        self.distribution.set_groups(self.groups)
        self.distribution.setVisible(bool(self.groups and self.chart_toggle.isChecked()))
        selected=self.discovery_current['path'] if self.discovery_current else (self.current or {}).get('path')
        with QSignalBlocker(self.discovery_results):
            self.discovery_results.set_hits(merged)
            for i,hit in enumerate(merged):
                item=self.discovery_results.topLevelItem(i)
                if hit.get('group'):
                    snippets = view.get('snippets', {}).get(norm_path(hit['path']), {})
                    for start in range(0, max(5, len(snippets)), 5):
                        if start and self.discovery_results.is_more(item.child(item.childCount()-1)):
                            item.takeChild(item.childCount()-1)
                        self.append_result_clusters(item,hit['group'],start)
                    for child in (item.child(j) for j in range(item.childCount())):
                        location = child.data(0, Qt.ItemDataRole.UserRole)
                        if isinstance(location, tuple) and snippets.get(location):
                            child.setData(0, EXPANDED_LINES, snippets[location])
                    item.setExpanded(view.get('expanded', {}).get(norm_path(hit['path']), i==0))
                    if hit['group'].get('status') in ('void', 'replaced'):
                        item.setForeground(0, QColor('#8a94a6'))
                if hit['path']==selected:
                    self.discovery_results.setCurrentItem(item)
        if merged and self.discovery_results.currentItem() is None:
            self.discovery_results.setCurrentItem(self.discovery_results.topLevelItem(0))
        elif merged and ((self.current and self.current.get('query')!=self.query) or
                         (not self.current and self.discovery_results.currentItem().data(0, Qt.ItemDataRole.UserRole).get('indexed'))):
            # Restoring the same file selection must not retain the previous
            # keyword's body/highlights after a new search.
            self.discovery_result_selected(self.discovery_results.currentItem(),None)
        if view.get('selected') and isinstance(view['selected'], tuple):
            for i in range(self.discovery_results.topLevelItemCount()):
                root = self.discovery_results.topLevelItem(i)
                for child in (root.child(j) for j in range(root.childCount())):
                    if child.data(0, Qt.ItemDataRole.UserRole) == view['selected']:
                        with QSignalBlocker(self.discovery_results):
                            self.discovery_results.setCurrentItem(child)
                        break
        self.discovery_more.setVisible(self._discovery_has_more)
        self.discovery_more.setEnabled(not self.discovery_pending)
        if not merged:
            self.result_stack.setCurrentIndex(0)
            if self.discovery_pending:
                self.result_empty.setText('正在检索…')
            elif self.discovery_scope_kind()=='dir' and not self.discovery.selected_ids():
                self.result_empty.setText('尚未选择电脑搜索位置<br><br><a href="ranges">添加或选择搜索范围</a>')
            else:
                self.result_empty.setText('没有找到匹配文件<br><br>可换一个关键词，或检查当前范围与筛选条件。<br>'
                    '<a href="clear">清除筛选条件</a>　<a href="ranges">检查搜索范围</a>')
        else:
            self.result_stack.setCurrentIndex(2)
        self.result_heading.setText(f'{len(merged)}'+('+' if self._discovery_has_more else '')+' 个结果')
        self.update_discovery_summary()
        if self._discovery_errors and not self.discovery_pending:
            self.notify(self._discovery_errors[0])
        self.filter_discovery_results()
        self.discovery_results.doItemsLayout()
        self.discovery_results.verticalScrollBar().setValue(view.get('scroll', 0))
        self.update_reader_context()

    def discovery_context_menu(self, position):
        item=self.discovery_results.itemAt(position)
        if not item or self.discovery_results.is_more(item):return
        value=item.data(0,Qt.ItemDataRole.UserRole)
        line=value[1] if isinstance(value,tuple) else value.get('first_line',1)
        path=value[0] if isinstance(value,tuple) else value.get('path')
        root=item
        while root.parent():root=root.parent()
        hit=root.data(0,Qt.ItemDataRole.UserRole)
        menu=QMenu(self)
        menu.addAction('打开原文件并定位' if hit.get('indexed') else '打开原文件',
            lambda:self.open_located_file(path,line) if hit.get('indexed') else self.open_discovery_original(path))
        menu.addAction('显示所在位置',lambda:self.open_discovery_location(path))
        menu.addAction('复制路径',lambda:self.copy_discovery_path(path))
        if hit.get('indexed'):
            menu.addAction('在软件内阅读',lambda:self.open_preview(path,line))
            menu.addAction('复制条款引用',lambda:self.jobs.submit('生成引用',
                lambda p,s:__import__('core').build_citation(self.store.db,path,line),
                lambda text:QApplication.clipboard().setText(text),visible=False))
            menu.addAction('自动匹配其他版本并对比',lambda:self.compare_path(path))
        elif hit.get('kind')!='dir':
            menu.addAction('加入资料库',lambda:self.import_discovery_to_library(path))
        menu.exec(self.discovery_results.viewport().mapToGlobal(position))
        menu.deleteLater()

    def update_discovery_summary(self):
        indexing = '索引更新中 · ' if getattr(self, '_discovery_tasks', None) else ''
        self.result_summary.setText(('部分检索未完成 · ' if self._discovery_errors else '')+
            ('仍在检索 · ' if self.discovery_pending else '')+indexing+self.discovery_coverage_text())
        self.result_summary.setToolTip('\n'.join(self._discovery_errors))

    def filter_discovery_results(self):
        needle=self.result_filter.text().strip().casefold()
        for i in range(self.discovery_results.topLevelItemCount()):
            item=self.discovery_results.topLevelItem(i)
            hit=item.data(0,Qt.ItemDataRole.UserRole)
            text=(hit.get('name','')+' '+hit['path']+' '+str(hit.get('group',{}))).casefold()
            item.setHidden(bool(needle and needle not in text))
        total = self.discovery_results.topLevelItemCount()
        visible = sum(not self.discovery_results.topLevelItem(i).isHidden() for i in range(total))
        suffix = '+' if getattr(self, '_discovery_has_more', False) else ''
        self.result_heading.setText((f'{visible} / {total}{suffix}' if needle else f'{total}{suffix}') + ' 个结果')
        self.result_filter.setToolTip('仅筛选已经加载的结果；可继续加载后再筛选' if suffix else '在当前结果中筛选')

    def discovery_result_selected(self,current,previous):
        if current is None:
            return
        hit=current.data(0,Qt.ItemDataRole.UserRole)
        if isinstance(hit,tuple):
            self.open_preview(*hit)
        elif isinstance(hit,dict) and hit.get('type')=='more':
            parent=current.parent()
            parent.removeChild(current)
            self.append_result_clusters(parent,hit['group'],hit['start'])
        elif isinstance(hit,dict) and hit.get('path'):
            if hit.get('indexed'):
                if self.discovery_results.header_click_active and current.parent() is None and self.current and norm_path(self.current['path'])==norm_path(hit['path']):
                    return
                self.open_preview(hit['path'],hit.get('first_line',1))
            else:
                self.preview_discovery_file(hit)

    def discovery_double_clicked(self,item,column):
        hit=item.data(0,Qt.ItemDataRole.UserRole)
        if isinstance(hit,tuple):
            self.open_discovery_original(hit[0])
        elif isinstance(hit,dict) and hit.get('path'):
            self.open_discovery_original(hit['path'])

    def preview_discovery_file(self,hit):
        if self.preview_job in self.jobs.active:
            self.jobs.cancel(self.preview_job)
        self._preview_generation+=1
        generation=self._preview_generation
        self.current=None
        self.discovery_current=hit
        self.doc_title.setText(hit.get('name') or Path(hit['path']).name)
        self.doc_title.setToolTip(hit['path'])
        self.doc_meta.setText('')
        self.preview_range.setText('')
        self.read_button.setMenu(self._discovery_read_menu)
        self._discovery_pdf_actions.menuAction().setVisible(Path(hit['path']).suffix.lower()=='.pdf')
        self.pdf_reader.close_document()
        self.reader_stack.setCurrentIndex(2)
        self.hit_previous.setEnabled(False)
        self.hit_next.setEnabled(False)
        self.hit_position.clear()
        self.update_reader_context()
        self.discovery_preview.show_hit(hit)
        if hit.get('kind')=='dir':
            return
        if Path(hit['path']).suffix.lower()=='.pdf' and os.path.isfile(hit['path']):
            self.reader_stack.setCurrentIndex(1)
            self.pdf_reader.open(hit['path'],1,'')
            self.preview_range.setText('第 1 页')
            return
        from .file_preview import load_preview
        def done(payload):
            if generation!=self._preview_generation or not self.discovery_current:
                return
            if 'image' in payload:
                self.discovery_preview.info.hide()
                self.discovery_preview.image.show()
                self.discovery_preview._scale_image(QPixmap.fromImage(payload['image']))
            elif payload.get('html'):
                self.discovery_preview.info.setHtml(payload['html'])
                self.preview_range.setText('部分预览' if payload.get('truncated') else '预览')
            elif payload.get('error'):
                self.notify(payload['error'])
        self.preview_job=self.jobs.submit('读取文件预览',lambda progress,stop:load_preview(hit['path'],stop),done,
            error=lambda detail:self.notify(detail.strip().splitlines()[-1]),visible=False)

    def open_discovery_original(self,path=None):
        path=path or (self.discovery_current or {}).get('path','')
        if not path:
            return
        if not os.path.exists(path):
            self.notify('原文件已更名、删除或离线')
        elif not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            self.notify('未能打开原文件，请检查默认打开程序')

    def open_discovery_location(self,path=None):
        path=path or (self.discovery_current or {}).get('path','')
        if not path or not os.path.isdir(os.path.dirname(path)):
            self.notify('原位置不可用')
            return
        try:
            subprocess.Popen(['explorer.exe','/select,',os.path.normpath(path)],
                             creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except OSError as exc:
            self.notify(str(exc))

    def copy_discovery_path(self,path=None):
        path=path or (self.discovery_current or {}).get('path','')
        if path:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(path)
            self.notify('已复制路径')

    def import_discovery_to_library(self,path=None):
        path=path or (self.discovery_current or {}).get('path','')
        if path and os.path.isfile(path):
            self.confirm_import([path])

    def discovery_coverage_text(self):
        return self._coverage_text

    def refresh_discovery_coverage(self,*_):
        if self._closing_requested or self._coverage_job in self.jobs.active:
            return
        def done(report):
            sources=report['sources']
            total=sum(s['discovered_count'] for s in sources)
            complete=sum(s['state']=='complete' for s in sources)
            offline=sum(s['state']=='offline' for s in sources)
            partial=sum(s['state'] in ('partial','needs_rescan') for s in sources)
            active=sum(s['state']=='scanning' for s in sources)
            remaining=max(0, len(sources)-complete-active-partial-offline)
            if sources:
                parts=[f'文件名目录 {total} 条', f'{complete} 个已更新']
                if active: parts.append(f'{active} 个更新中')
                if partial: parts.append(f'{partial} 个待补全')
                if offline: parts.append(f'{offline} 个离线')
                if remaining: parts.append(f'{remaining} 个未开始')
                self._coverage_text=' · '.join(parts)+'\n正文需加入资料库后检索'
            else:
                self._coverage_text='尚未选择电脑位置；原资料库仍可检索'
            if self.discovery_is_explore() and self.discovery_scope_kind()!='content':
                self.update_discovery_summary()
        self._coverage_job=self.jobs.submit('读取目录覆盖',lambda progress,stop:self.discovery.get_coverage(),done,visible=False)

    def open_range_manager(self):
        if self._range_dialog is None:
            self._range_dialog=RangeDialog(self,self)
        self._range_dialog.refresh()
        self._range_dialog.show()
        self._range_dialog.raise_()
        self._range_dialog.activateWindow()

    def shutdown_discovery(self):
        self.discovery.shutdown()


def norm_path(path):
    return os.path.normpath(path).casefold()


def range_reason(reason):
    lower=reason.lower()
    if 'permission' in lower or 'denied' in lower or 'cannot open volume' in lower:
        return '权限受限，使用通用目录通道'
    if 'usn readable' in lower:
        return '可使用原生通道建立目录'
    if 'application was closed' in lower:
        return '需要同步软件关闭期间的变化'
    if 'non-volume' in lower:
        return '目录枚举与持续监听'
    if 'cancel' in lower:
        return '已停止，旧记录保留；可继续更新'
    if 'unresolved' in lower or 'gap' in lower or 'incomplete' in lower:
        return '部分目录尚待校验，将补充扫描'
    return reason

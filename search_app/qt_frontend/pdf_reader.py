"""原始 PDF 阅读：分页、缩略图、缩放、页内检索。"""
from bisect import bisect_left
from pathlib import Path

from PySide6.QtCore import QEvent, QModelIndex, QPointF, QSize, Qt, Signal, QSignalBlocker, QTimer
from PySide6.QtGui import QIcon, QIntValidator, QPixmap
from PySide6.QtPdf import QPdfDocument, QPdfPageRenderer, QPdfSearchModel
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (QAbstractSpinBox, QComboBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListView, QListWidget, QListWidgetItem, QPushButton, QSpinBox, QSplitter, QVBoxLayout, QWidget)

from .widgets import button, label


class PageInput(QLineEdit):
    valueChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__('1', parent)
        self._value = 1
        self._validator = QIntValidator(1, 1, self)
        self.setValidator(self._validator)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.editingFinished.connect(self.commit)

    def setMinimum(self, value):
        self._validator.setBottom(value)

    def setMaximum(self, value):
        self._validator.setTop(value)

    def setValue(self, value):
        value = max(self._validator.bottom(), min(self._validator.top(), value))
        changed = value != self._value
        self._value = value
        self.setText(str(value))
        if changed:
            self.valueChanged.emit(value)

    def value(self):
        return self._value

    def commit(self):
        self.setValue(int(self.text()) if self.hasAcceptableInput() else self._value)


class PdfReader(QWidget):
    page_changed = Signal(int)

    def __init__(self, parent=None, compact=False):
        super().__init__(parent)
        self.document = QPdfDocument(self)
        self.path = ''
        self.compact = compact
        self.key = None
        self.pending_page = 0
        self.search_index = -1
        self.thumbnail_pages = []
        self.requests = {}
        self._cursor = 0
        self._syncing = False
        self._initializing = False
        self._resizing = False
        self._desired_page = 0
        self.reanchor = QTimer(self)
        self.reanchor.setSingleShot(True)
        self.reanchor.setInterval(80)
        self.reanchor.timeout.connect(self.reanchor_page)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0 if compact else 6)
        self.controls = QWidget()
        control_layout = QVBoxLayout(self.controls)
        control_layout.setContentsMargins(8, 8, 8, 8)
        control_layout.setSpacing(6)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(5)
        self.thumbnail_toggle = button('缩略图')
        self.thumbnail_toggle.setToolTip('显示或隐藏 PDF 页面缩略图')
        self.thumbnail_toggle.setCheckable(True)
        toolbar.addWidget(self.thumbnail_toggle)
        self.previous_page = button('‹', lambda: self.goto(self.page.value() - 2))
        self.previous_page.setToolTip('上一页 PDF')
        self.previous_page.setFixedWidth(28)
        toolbar.addWidget(self.previous_page)
        self.page = PageInput()
        self.page.setMinimum(1)
        self.page.setFixedWidth(66)
        self.page.setToolTip('输入页码，回车跳转')
        self.page.valueChanged.connect(lambda value: self.goto(value - 1))
        toolbar.addWidget(self.page)
        self.total = label('/ —', 'muted')
        toolbar.addWidget(self.total)
        self.next_page = button('›', lambda: self.goto(self.page.value()))
        self.next_page.setToolTip('下一页 PDF')
        self.next_page.setFixedWidth(28)
        toolbar.addWidget(self.next_page)
        toolbar.addStretch()
        self.zoom = QComboBox()
        self.zoom.addItems(['适合宽度', '整页', '75%', '100%', '125%', '150%', '200%'])
        self.zoom.setMaximumWidth(110)
        self.zoom.currentIndexChanged.connect(self.change_zoom)
        toolbar.addWidget(self.zoom)
        control_layout.addLayout(toolbar)
        if not compact:
            layout.addWidget(self.controls)
        self.splitter = QSplitter()
        thumbnails = QWidget()
        column = QVBoxLayout(thumbnails)
        column.setContentsMargins(0, 0, 0, 0)
        self.thumbnails = QListWidget()
        self.thumbnails.setIconSize(QSize(86, 122))
        self.thumbnails.setViewMode(QListView.ViewMode.IconMode)
        self.thumbnails.setFlow(QListView.Flow.TopToBottom)
        self.thumbnails.setWrapping(False)
        self.thumbnails.setGridSize(QSize(106, 154))
        self.thumbnails.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.thumbnails.setMinimumWidth(115)
        self.thumbnails.itemClicked.connect(lambda item: self.goto(item.data(Qt.ItemDataRole.UserRole)))
        column.addWidget(self.thumbnails, 1)
        self.more = button('再加载 20 页', self.more_thumbnails)
        column.addWidget(self.more)
        self.splitter.addWidget(thumbnails)
        self.thumbnail_panel = thumbnails
        self.thumbnail_toggle.toggled.connect(thumbnails.setVisible)
        self.thumbnail_toggle.toggled.connect(lambda checked: self.more_thumbnails()
            if checked and not self.thumbnail_pages else None)
        thumbnails.hide()
        self.view = QPdfView(self)
        self.view.setDocument(self.document)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.view.setPageMode(QPdfView.PageMode.SinglePage)
        self.view.viewport().installEventFilter(self)
        self.view.pageNavigator().currentPageChanged.connect(self.page_selected)
        self.splitter.addWidget(self.view)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([125, 700])
        layout.addWidget(self.splitter, 1)
        self.message = label('选择 PDF 查看原始页面', 'muted')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        if compact:
            self.message.hide()
        find = QHBoxLayout()
        self.find = QLineEdit()
        self.find.setPlaceholderText('在 PDF 原文中查找；扫描件请切到提取文字查看 OCR')
        self.find.setMinimumWidth(80)
        self.find.textChanged.connect(self.search_text)
        self.find.returnPressed.connect(lambda: self.next_match(1))
        find.addWidget(self.find, 1)
        self.match_count = label('0 处', 'muted')
        find.addWidget(self.match_count)
        self.previous_match=button('上一处',lambda:self.next_match(-1))
        self.next_match_button=button('下一处',lambda:self.next_match(1))
        find.addWidget(self.previous_match)
        find.addWidget(self.next_match_button)
        control_layout.addLayout(find)
        self.search = QPdfSearchModel(self)
        self.search.setDocument(self.document)
        self.search.rowsInserted.connect(self.update_match_count)
        self.search.modelReset.connect(self.update_match_count)
        self.view.setSearchModel(self.search)
        self.renderer = QPdfPageRenderer(self)
        self.renderer.setRenderMode(QPdfPageRenderer.RenderMode.MultiThreaded)
        self.renderer.setDocument(self.document)
        self.renderer.pageRendered.connect(self.rendered_thumbnail)
        self.document.statusChanged.connect(self.document_status)
        self.document.passwordRequired.connect(self.password_required)

    def open(self, path, page=1, keyword=''):
        path = str(Path(path).resolve())
        self.pending_page = max(0, int(page) - 1)
        self._desired_page = self.pending_page
        self._jump_first = bool(keyword)
        try:
            stat = Path(path).stat()
            key = path, stat.st_mtime_ns, stat.st_size
        except OSError:
            self.message.setText('原文件暂不可用，可切回提取文字继续阅读索引内容。')
            self.message.show()
            self.close_document()
            return False
        if key != self.key:
            self.close_document()
            self.path, self.key = path, key
            self._initializing = True
            self.message.setText('正在读取 PDF…')
            self.document.load(path)
        elif self.document.status() == QPdfDocument.Status.Ready:
            self.goto(self.pending_page, emit=False)
            self._initializing = False
        if self.find.text() != keyword:
            self.find.setText(keyword)
        elif keyword and self.document.status() == QPdfDocument.Status.Ready:
            self._jump_first = True
            self.update_match_count()
        return True

    def close_document(self):
        self.reanchor.stop()
        self._resizing = False
        self.renderer.setDocument(None)
        self.requests.clear()
        self.thumbnail_pages.clear()
        self.thumbnails.clear()
        self._cursor = 0
        self.key = None
        self.path = ''
        self.document.close()
        self.renderer.setDocument(self.document)

    def document_status(self, status):
        ready = status == QPdfDocument.Status.Ready
        self.page.setEnabled(ready)
        if ready:
            count = self.document.pageCount()
            self.page.setMaximum(max(1, count))
            self.total.setText('/ ' + str(count))
            self.message.setText('原始页面 · 表格与版式保持原貌；扫描页文字请在“提取文字”中查看。')
            self.message.setVisible(not self.compact)
            self.goto(self.pending_page, emit=False)
            self._initializing = False
            self.reanchor.start()
            if self.thumbnail_toggle.isChecked():
                self.more_thumbnails()
        elif status == QPdfDocument.Status.Error:
            self.message.setText('PDF 无法打开：' + str(self.document.error()).split('.')[-1] + '。可切回提取文字。')
            self.message.show()

    def password_required(self):
        password, accepted = QInputDialog.getText(self, 'PDF 需要密码', '打开密码', QLineEdit.EchoMode.Password)
        if accepted:
            self.document.setPassword(password)
            self.document.load(self.path)

    def goto(self, page, emit=True):
        if self.document.status() != QPdfDocument.Status.Ready:
            self.pending_page = max(0, page)
            return
        page = max(0, min(self.document.pageCount() - 1, page))
        self._desired_page = page
        if emit:
            self._resizing = False
        previous = self._syncing
        self._syncing = not emit
        self.view.pageNavigator().jump(page, QPointF(), 0)
        self.page_selected(page)
        self._syncing = previous

    def page_selected(self, page):
        if self._resizing and not self._syncing:
            return
        with QSignalBlocker(self.page):
            self.page.setValue(page + 1)
        self.previous_page.setEnabled(page > 0)
        self.next_page.setEnabled(page < self.document.pageCount()-1)
        if self.thumbnail_toggle.isChecked():
            self.request_thumbnails(range(max(0, page - 2), min(self.document.pageCount(), page + 3)))
            if page in self.thumbnail_pages:
                self.thumbnails.setCurrentRow(self.thumbnail_pages.index(page))
        if not self._syncing and not self._initializing and self.document.status() == QPdfDocument.Status.Ready:
            self._desired_page = page
            self.page_changed.emit(page + 1)

    def eventFilter(self, target, event):
        if target is self.view.viewport() and event.type() == QEvent.Type.Resize:
            if self.document.status() == QPdfDocument.Status.Ready:
                self._resizing = True
                self.reanchor.start()
        return super().eventFilter(target, event)

    def reanchor_page(self):
        if self.document.status() == QPdfDocument.Status.Ready:
            self.goto(self._desired_page, emit=False)
            self.view.verticalScrollBar().setValue(0)
            self.view.horizontalScrollBar().setValue(0)
        self._resizing = False

    def change_zoom(self, index):
        if index == 0:
            self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        elif index == 1:
            self.view.setZoomMode(QPdfView.ZoomMode.FitInView)
        else:
            self.view.setZoomMode(QPdfView.ZoomMode.Custom)
            self.view.setZoomFactor(float(self.zoom.currentText().rstrip('%')) / 100)

    def more_thumbnails(self):
        if self.document.status() != QPdfDocument.Status.Ready:
            return
        end = min(self._cursor + 20, self.document.pageCount())
        self.request_thumbnails(range(self._cursor, end))
        self._cursor = end
        self.more.setEnabled(end < self.document.pageCount())

    def request_thumbnails(self, pages):
        for page in pages:
            if page in self.thumbnail_pages:
                continue
            position = bisect_left(self.thumbnail_pages, page)
            self.thumbnail_pages.insert(position, page)
            item = QListWidgetItem(f'第 {page + 1} 页')
            item.setData(Qt.ItemDataRole.UserRole, page)
            self.thumbnails.insertItem(position, item)
            size = self.document.pagePointSize(page).scaled(86, 122, Qt.AspectRatioMode.KeepAspectRatio).toSize()
            request = self.renderer.requestPage(page, size)
            self.requests[request] = (self.key, page)

    def rendered_thumbnail(self, page, size, image, options, request):
        expected = self.requests.pop(request, None)
        if expected != (self.key, page) or image.isNull() or page not in self.thumbnail_pages:
            return
        self.thumbnails.item(self.thumbnail_pages.index(page)).setIcon(QIcon(QPixmap.fromImage(image)))

    def search_text(self, text):
        self.search_index = -1
        self.view.setCurrentSearchResultIndex(-1)
        self.search.setSearchString(text)
        self.update_match_count()

    def update_match_count(self, *_):
        count=self.search.rowCount(QModelIndex())
        self.match_count.setText(str(count) + ' 处')
        self.previous_match.setEnabled(count>0)
        self.next_match_button.setEnabled(count>0)
        if getattr(self, '_jump_first', False) and count:
            self._jump_first = False
            self.search_index = 0
            result = self.search.resultAtIndex(0)
            self.view.pageNavigator().jump(result.page(), result.location(), 0)
            self.view.setCurrentSearchResultIndex(0)

    def next_match(self, step):
        count = self.search.rowCount(QModelIndex())
        if count:
            self.search_index = (self.search_index + step) % count
            result = self.search.resultAtIndex(self.search_index)
            self.view.pageNavigator().jump(result.page(), result.location(), 0)
            self.view.setCurrentSearchResultIndex(self.search_index)

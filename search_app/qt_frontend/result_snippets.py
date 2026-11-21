"""按列宽排版的命中片段；折叠只影响结果栏，不重新打开正文。"""
import re

from PySide6.QtCore import QPointF, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPalette, QTextCharFormat, QTextLayout, QTextOption
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QTreeWidget


SNIPPET = int(Qt.ItemDataRole.UserRole) + 10
EXPANDED_LINES = SNIPPET + 1
RESULT_DETAIL = SNIPPET + 2


def folded_excerpt(text, terms):
    positions = [text.casefold().find(term.casefold()) for term in terms if term]
    found = [position for position in positions if position >= 0]
    start = max(0, min(found, default=0) - 24)
    return ('…' if start else '') + text[start:], bool(start)


class SnippetDelegate(QStyledItemDelegate):
    def __init__(self, tree):
        super().__init__(tree)
        self.tree = tree
        self.cache = {}

    def layout_for(self, index, width):
        data = index.data(SNIPPET)
        limit = index.data(EXPANDED_LINES) or 0
        width = max(50, int(width) - 20)
        key = (data['text'], tuple(data['terms']), bool(data.get('fuzzy')), limit, width, self.tree.font().toString())
        if key in self.cache:
            return self.cache[key]
        text, hidden_start = (data['text'], False) if limit else folded_excerpt(data['text'], data['terms'])
        text = text.replace('\r', '').replace('\n', '\u2028')
        layout = QTextLayout(text, self.tree.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        formats = []
        for term in data['terms']:
            if not term:
                continue
            for match in re.finditer(re.escape(term), text, re.IGNORECASE):
                # QTextLayout 使用 UTF-16 索引，不能把 Python 的字符索引直接用于 emoji。
                highlight = QTextLayout.FormatRange()
                highlight.start = len(text[:match.start()].encode('utf-16-le')) // 2
                highlight.length = len(match.group().encode('utf-16-le')) // 2
                highlight.format = QTextCharFormat()
                highlight.format.setBackground(QColor('#ffd4a0' if data.get('fuzzy') else '#f3df89'))
                highlight.format.setForeground(QColor('#283141'))
                formats.append(highlight)
        layout.setFormats(formats)
        layout.beginLayout()
        lines, y = [], 0
        for _ in range(limit or 3):
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            line.setPosition(QPointF(0, y))
            y += line.height() + 2
            lines.append(line)
        more = bool(lines and lines[-1].textStart() + lines[-1].textLength() < len(text.encode('utf-16-le')) // 2)
        layout.endLayout()
        control = '继续展开 · 收起' if limit and more else '收起 ▴' if limit else '展开 ▾'
        show_control = bool(limit or more or hidden_start)
        result = layout, int(y), show_control, control, more
        if len(self.cache) > 512:
            self.cache.clear()
        self.cache[key] = result
        return result

    def row_width(self, index):
        item = self.tree.itemFromIndex(index)
        width = self.tree.viewport().width() if item and item.isFirstColumnSpanned() else self.tree.columnWidth(0)
        depth = int(self.tree.rootIsDecorated())
        parent = index.parent()
        while parent.isValid():
            depth += 1
            parent = parent.parent()
        return max(50, width - depth * self.tree.indentation())

    def sizeHint(self, option, index):
        if index.data(RESULT_DETAIL):
            return QSize(0, self.tree.fontMetrics().lineSpacing()*2+12)
        if not index.data(SNIPPET):
            return super().sizeHint(option, index)
        _, height, control, _, _ = self.layout_for(index, self.row_width(index))
        return QSize(0, height + 28 + (24 if control else 0))

    def paint(self, painter, option, index):
        data = index.data(SNIPPET)
        detail = index.data(RESULT_DETAIL)
        if detail and not data:
            painter.save()
            if option.state & QStyle.StateFlag.State_Selected:
                painter.fillRect(option.rect,option.palette.highlight())
            height = option.fontMetrics.lineSpacing()
            rect=option.rect.adjusted(5,3,-5,-3)
            painter.setPen(option.palette.text().color())
            title=option.fontMetrics.elidedText(str(index.data()),Qt.TextElideMode.ElideMiddle,rect.width())
            painter.drawText(QRect(rect.x(),rect.y(),rect.width(),height+2),Qt.AlignmentFlag.AlignVCenter,title)
            painter.setPen(option.palette.color(QPalette.ColorRole.PlaceholderText))
            painter.drawText(QRect(rect.x(),rect.y()+height+3,rect.width(),height),Qt.AlignmentFlag.AlignVCenter,
                option.fontMetrics.elidedText(str(detail),Qt.TextElideMode.ElideMiddle,rect.width()))
            painter.restore()
            return
        if not data:
            return super().paint(painter, option, index)
        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            painter.fillRect(option.rect, option.palette.highlight())
        text_color = option.palette.color(QPalette.ColorRole.Text)
        painter.setPen(text_color)
        font = self.tree.font()
        painter.setFont(font)
        painter.drawText(option.rect.adjusted(10, 3, -8, 0), Qt.AlignmentFlag.AlignTop, data['location'])
        layout, _, control, caption, _ = self.layout_for(index, option.rect.width())
        painter.setClipRect(option.rect)
        layout.draw(painter, QPointF(option.rect.left() + 10, option.rect.top() + 24))
        if control:
            painter.setPen(option.palette.color(QPalette.ColorRole.Link))
            painter.drawText(option.rect.adjusted(10, 0, -8, -3),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, caption)
        painter.restore()


class SnippetTree(QTreeWidget):
    more_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setUniformRowHeights(False)
        self.setWordWrap(True)
        self.setIndentation(12)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.header().setStretchLastSection(False)
        self.setExpandsOnDoubleClick(False)
        self.header_click_active = False
        self.snippet_delegate = SnippetDelegate(self)
        self.setItemDelegateForColumn(0, self.snippet_delegate)
        self.relayout = QTimer(self)
        self.relayout.setSingleShot(True)
        self.relayout.setInterval(35)
        self.relayout.timeout.connect(self.doItemsLayout)

    def addTopLevelItem(self, item):
        super().addTopLevelItem(item)
        for i in range(item.childCount()):
            self.span_snippet(item.child(i))

    @staticmethod
    def span_snippet(item):
        value = item.data(0, Qt.ItemDataRole.UserRole)
        if item.data(0, SNIPPET) or isinstance(value,dict) and value.get('type')=='more':
            item.setFirstColumnSpanned(True)

    @staticmethod
    def is_more(item):
        value = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        return isinstance(value,dict) and value.get('type')=='more'

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.relayout.start()

    def control_at(self, point):
        index = self.indexAt(point)
        if not index.isValid() or index.column() != 0 or not index.data(SNIPPET):
            return None
        rectangle = self.visualRect(index)
        _, _, control, _, _ = self.snippet_delegate.layout_for(index, rectangle.width())
        if control and point.y() >= rectangle.bottom() - 24:
            return index
        return None

    def change_expansion(self, index, collapse=False):
        old = self.verticalScrollBar().value()
        current = index.data(EXPANDED_LINES) or 0
        _, _, _, _, more = self.snippet_delegate.layout_for(index, self.visualRect(index).width())
        value = 0 if collapse or (current and not more) else current + 24
        self.model().setData(index, value, EXPANDED_LINES)
        self.doItemsLayout()
        self.verticalScrollBar().setValue(old)
        self.viewport().update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.position().toPoint())
            if self.control_at(event.position().toPoint()) is not None or self.is_more(item):
                event.accept()
                return
            if item and item.parent() is None and item.childCount():
                self.header_click_active = True
                try:
                    super().mousePressEvent(event)
                finally:
                    self.header_click_active = False
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            index = self.control_at(event.position().toPoint())
            if index is not None:
                rect = self.visualRect(index)
                self.change_expansion(index, collapse=bool(index.data(EXPANDED_LINES) and
                    event.position().x() > rect.left() + 72))
                event.accept()
                return
            item = self.itemAt(event.position().toPoint())
            if self.is_more(item):
                self.more_requested.emit(item)
                return
            if item and item.parent() is None and item.childCount():
                rectangle = self.visualRect(self.indexFromItem(item))
                # The branch arrow is handled by Qt on mouse press. Only the
                # title area toggles here, so one click cannot toggle twice.
                if rectangle.contains(event.position().toPoint()):
                    item.setExpanded(not item.isExpanded())
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        index = self.currentIndex()
        if index.isValid() and index.data(SNIPPET) and event.key() == Qt.Key.Key_Space:
            self.change_expansion(index, collapse=bool(index.data(EXPANDED_LINES)))
            return
        item = self.currentItem()
        if item and item.parent() is None and item.childCount() and event.key()==Qt.Key.Key_Space:
            item.setExpanded(not item.isExpanded())
            return
        if self.is_more(item) and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.more_requested.emit(item)
            return
        super().keyPressEvent(event)

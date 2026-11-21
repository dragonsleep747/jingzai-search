from __future__ import annotations
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QRect, QRectF, QSize, Signal
from PySide6.QtGui import QColor, QPainter, QFont, QIcon, QFontDatabase, QPainterPath, QLinearGradient
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QLabel, QPushButton,
    QTableView, QVBoxLayout, QWidget, QHeaderView, QSplitter, QSplitterHandle,
)


def button(text, callback=None, primary=False, ghost=False, parent=None):
    item = QPushButton(text, parent)
    item.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        item.setObjectName('primary')
    elif ghost:
        item.setObjectName('ghost')
    if callback:
        item.clicked.connect(callback)
    return item


def label(text, name=''):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    return widget


class ReadingSplitter(QSplitter):
    default_sizes=(330,900)

    def createHandle(self):
        handle=ReadingHandle(self.orientation(),self)
        handle.setToolTip('拖动调整列宽；双击恢复默认宽度')
        return handle


class ReadingHandle(QSplitterHandle):
    def mouseDoubleClickEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            self.splitter().setSizes(list(self.splitter().default_sizes))
            event.accept()
        else:super().mouseDoubleClickEvent(event)


class BrandLockup(QWidget):
    """Compact, DPI-aware logo and live outline lettering; no resized text bitmap."""
    def __init__(self, icon_path, parent=None):
        super().__init__(parent)
        self.setObjectName('brandLockup')
        self.setAccessibleName('镜仔搜索')
        self.setToolTip('镜仔搜索')
        self.setFixedHeight(36)
        self.icon = QIcon(str(icon_path))
        families = set(QFontDatabase.families())
        self.family = next((name for name in ('KaiTi', '楷体', 'STKaiti', 'Microsoft YaHei UI') if name in families), 'sans-serif')

    def sizeHint(self):
        return QSize(124, 36)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing |
                               QPainter.RenderHint.SmoothPixmapTransform)
        self.icon.paint(painter, QRect(0, 4, 28, 28))
        font = QFont(self.family)
        font.setPixelSize(22 if self.width() < 130 else 24)
        font.setWeight(QFont.Weight.DemiBold)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        lettering = QPainterPath()
        lettering.addText(34, 26, font, '镜仔搜索')
        bounds = lettering.boundingRect()
        if bounds.right() > self.width() - 1:
            painter.translate(34, 0)
            painter.scale(max(.7, (self.width() - 35) / max(1, bounds.right() - 34)), 1)
            painter.translate(-34, 0)
        gradient = QLinearGradient(34, 0, self.width(), 32)
        dark = self.palette().windowText().color().lightness() > 160
        gradient.setColorAt(0, QColor('#c6ddff' if dark else '#244f86'))
        gradient.setColorAt(1, QColor('#c9bafa' if dark else '#69508e'))
        painter.fillPath(lettering, gradient)


def panel():
    frame = QFrame()
    frame.setObjectName('panel')
    return frame


class Records(QAbstractTableModel):
    def __init__(self, columns, parent=None):
        super().__init__(parent)
        self.columns = columns
        self.rows = []

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        row = self.rows[index.row()]
        key = self.columns[index.column()][1]
        value = key(row) if callable(key) else row.get(key, '')
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return str(value if value is not None else '')
        if role == Qt.ItemDataRole.UserRole:
            return row
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.columns[section][0]
        return super().headerData(section, orientation, role)


def records_table(model):
    table = QTableView()
    table.setModel(model)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(34)
    table.horizontalHeader().setStretchLastSection(True)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    table.setSortingEnabled(False)
    return table


def page_heading(title, subtitle, actions=()):
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 4)
    texts = QVBoxLayout()
    texts.setSpacing(5)
    texts.addWidget(label(title, 'pageTitle'))
    texts.addWidget(label(subtitle, 'muted'))
    layout.addLayout(texts, 1)
    for action in actions:
        layout.addWidget(action)
    return widget


def stylesheet(dark=False, compact=False):
    colors = dict(bg='#f4f6fa', panel='#ffffff', sidebar='#edf1f7', text='#203147',
                  muted='#5b6c85', line='#e1e7ef', hover='#e8eef8', field='#ffffff',
                  select='#e4edff', brand='#285fba', alt='#fafbfd', control='#e7edf6', control_line='#c7d4e5')
    if dark:
        colors.update(bg='#141c28', panel='#1b2533', sidebar='#182231', text='#e5eaf1',
                      muted='#b9c5d6', line='#303e51', hover='#26374e', field='#202d3e',
                      select='#294a78', brand='#83adf6', alt='#1e2a3b', control='#293b53', control_line='#49617e')
    css = """
    QWidget {{ color:{text}; font-family:'Microsoft YaHei UI'; font-size:10pt; }}
    QMainWindow, QWidget#workspace {{ background:{bg}; }}
    QWidget#sidebar {{ background:{sidebar}; border-right:1px solid {line}; }}
    QFrame#panel {{ background:{panel}; border:1px solid {line}; border-radius:12px; }}
    QLabel {{ background:transparent; }}
    QLabel#pageTitle {{ font-size:20pt; font-weight:650; }}
    QLabel#brand {{ font-size:15pt; font-weight:700; }}
    QLabel#muted, QLabel#hint {{ color:{muted}; }}
    QLabel#section {{ font-size:11pt; font-weight:600; }}
    QPushButton, QToolButton {{ background:{panel}; border:1px solid {line};
        border-radius:7px; padding:8px 13px; min-height:18px; }}
    QPushButton:hover, QToolButton:hover {{ background:{hover}; border-color:{muted}; }}
    QPushButton:pressed {{ background:{select}; }}
    QPushButton:disabled {{ color:{muted}; }}
    QPushButton#primary {{ background:#285fba; color:white; border-color:#285fba; font-weight:600; }}
    QPushButton#primary:hover {{ background:#3471d0; }}
    QPushButton#ghost {{ background:transparent; border:1px solid transparent; color:{muted}; }}
    QPushButton#ghost:hover {{ background:{hover}; color:{text}; }}
    QPushButton#nav {{ background:transparent; border:none; text-align:left;
        padding:12px 16px; border-radius:8px; }}
    QPushButton#nav:checked {{ background:{select}; color:{brand}; font-weight:650; }}
    QPushButton#nav:hover {{ background:{hover}; }}
    QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {{ background:{field};
        border:1px solid {line}; border-radius:7px; padding:8px 10px; selection-background-color:#285fba;
        selection-color:white; }}
    QLineEdit:focus, QComboBox:focus {{ border:1px solid #507fc5; }}
    QLineEdit#searchInput {{ font-size:13pt; padding:13px 16px; border-radius:9px; }}
    QComboBox {{ min-height:18px; padding-right:24px; }}
    QComboBox::drop-down {{ border:none; width:24px; }}
    QComboBox QAbstractItemView {{ background:{panel}; selection-background-color:{select}; }}
    QCheckBox {{ spacing:7px; }}
    QTreeWidget, QTableView, QListWidget {{ background:{panel}; alternate-background-color:{alt};
        border:1px solid {line}; border-radius:8px; outline:none; selection-background-color:{select};
        selection-color:{text}; }}
    QTreeWidget::item {{ padding:8px 4px; border-bottom:1px solid {line}; }}
    QTreeWidget::item:selected, QListWidget::item:selected {{ background:{select}; }}
    QTableView::item {{ padding:6px; border:none; }}
    QTableView::item:selected {{ background:{brand}; color:white; }}
    QHeaderView::section {{ background:{sidebar}; padding:10px 8px; border:none;
        border-bottom:1px solid {line}; color:{text}; font-weight:600; }}
    QTextBrowser {{ background:{panel}; border:0; }}
    QSplitter::handle {{ background:{bg}; width:8px; }}
    QSplitter::handle:hover {{ background:{select}; }}
    QScrollBar:vertical {{ background:transparent; width:9px; margin:1px; }}
    QScrollBar::handle:vertical {{ background:{line}; border-radius:4px; min-height:28px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
    QScrollBar:horizontal {{ height:9px; background:transparent; }}
    QScrollBar::handle:horizontal {{ background:{line}; border-radius:4px; min-width:28px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width:0; }}
    QStatusBar {{ background:{sidebar}; color:{muted}; border-top:1px solid {line}; }}
    QMenu {{ background:{panel}; border:1px solid {line}; padding:5px; }}
    QMenuBar {{ background:{sidebar}; border-bottom:1px solid {line}; }}
    QMenuBar::item {{ background:transparent; padding:6px 10px; }}
    QMenuBar::item:selected {{ background:{select}; }}
    QMenu::item {{ padding:8px 30px 8px 12px; }}
    QMenu::item:selected {{ background:{select}; border-radius:4px; }}
    QDialog {{ background:{bg}; }}
    QProgressBar {{ border:0; border-radius:3px; background:{line}; min-height:6px; max-height:6px; }}
    QProgressBar::chunk {{ background:#427acb; border-radius:3px; }}
    QTabWidget::pane {{ border:0; }}
    QTabBar::tab {{ padding:10px 18px; border-bottom:2px solid transparent; }}
    QTabBar::tab:selected {{ color:{brand}; border-bottom:2px solid {brand}; }}
    QToolBar {{ border:0; spacing:5px; background:transparent; }}
    """.format(**colors)
    if compact:
        css += """
        QLabel#pageTitle { font-size:16pt; }
        QPushButton, QToolButton { padding:5px 9px; min-height:17px; }
        QPushButton#nav { padding:9px 12px; }
        QLineEdit, QComboBox, QSpinBox { padding:5px 8px; }
        QLineEdit#searchInput { padding:8px 12px; font-size:12pt; }
        QTreeWidget::item { padding:5px 4px; }
        QHeaderView::section { padding:7px 8px; }
        """
    caret = (Path(__file__).parent / 'assets' / 'caret.svg').as_posix()
    css += ('QComboBox::down-arrow {image:url("' + caret + '");width:10px;height:7px;} '
            'QPushButton:checked {background:' + colors['select'] + ';border-color:' + colors['brand'] + ';} '
            'QComboBox:disabled {color:' + colors['muted'] + ';}')
    css += """
        QPlainTextEdit#questionInput {{background:{panel};color:{text};border:1px solid {control_line};border-radius:5px;padding:0;}}
        QPlainTextEdit#questionInput:focus {{border-color:{brand};}}
        QLabel#readerContext {{background:{select};padding:5px 10px;border-bottom:1px solid {line};}}
        QLabel#answerNotice {{background:{select};padding:8px;border:1px solid {control_line};border-radius:5px;}}
        QPushButton[danger="true"] {{color:#b64332;border-color:#be847a;}}
        QPushButton:focus, QToolButton:focus {{border:1px solid {brand};}}
        QScrollBar:vertical {{width:12px;}}
        QScrollBar::handle:vertical {{min-height:30px;border:3px solid transparent;border-radius:5px;}}
        QSplitter::handle {{background:{line};}}
        QSplitter::handle:hover {{background:{brand};}}
        QLabel#brand {{font-size:11pt;}}
        QPushButton#nav {{padding:9px 6px;}}
        QWidget#resultColumn {{background:{bg};}}
        QWidget#resultColumn, QWidget#resultColumn QWidget {{font-family:'Microsoft YaHei';font-size:10pt;font-weight:400;}}
        QWidget#readerPanel, QWidget#readerHeader {{background:{panel};}}
        QWidget#readerHeader {{border-bottom:1px solid {line};}}
        QWidget#readerHeader QLabel {{font-size:10pt;}}
        QWidget#readerHeader QToolButton:disabled {{color:{muted};}}
        QToolButton#quietControl {{padding:2px 5px;min-height:20px;max-height:26px;
            border:0;border-radius:4px;background:transparent;}}
        QToolButton#quietControl:hover {{background:{hover};}}
        QWidget#resultColumn QLineEdit#searchInput {{font-size:10pt;padding:0 6px;}}
        QTreeWidget#snippetResults {{font-size:10pt;border:0;border-radius:0;background:{bg};}}
        QTreeWidget#snippetResults::item {{padding:5px 2px;}}
        QScrollArea#searchOptions {{border:0;background:{bg};}}
        QLabel#notice {{background:{select};color:{text};padding:10px 14px;border:1px solid {line};border-radius:6px;}}
        QWidget#resultColumn QPushButton, QWidget#resultColumn QToolButton {{
            background:{control};border:1px solid {control_line};border-radius:5px;min-height:20px;padding:3px 8px;}}
        QWidget#resultColumn QLineEdit, QWidget#resultColumn QComboBox {{
            min-height:20px;padding:3px 6px;border-radius:5px;}}
        QWidget#resultColumn QPushButton:hover, QWidget#resultColumn QToolButton:hover {{
            background:{hover};border-color:{brand};}}
        QWidget#resultColumn QPushButton:checked {{background:{select};border-color:{brand};}}
        QWidget#resultColumn QLineEdit#searchInput {{min-height:26px;max-height:26px;padding:0 6px;font-size:10pt;border-color:{control_line};border-radius:5px;}}
        QWidget#resultColumn QComboBox#searchMode {{min-height:26px;max-height:26px;padding:0 1px 0 5px;font-size:10pt;
            background:{control};border:1px solid {control_line};border-radius:5px;}}
        QComboBox#searchMode::drop-down {{width:14px;border:0;}}
        QComboBox#searchMode::down-arrow {{width:10px;height:6px;}}
        QWidget#resultColumn QPushButton#searchButton {{min-height:26px;max-height:26px;padding:0;
            background:#285fba;border:1px solid #285fba;border-radius:5px;}}
        QWidget#resultColumn QPushButton#searchButton:hover {{background:#3471d0;border-color:#3471d0;}}
        QWidget#resultColumn QPushButton#searchButton:pressed {{background:#204e99;}}
        QWidget#resultColumn QPushButton#searchButton:disabled {{background:{control_line};border-color:{control_line};}}
        QWidget#resultColumn QPushButton#searchOptionsButton {{min-height:26px;max-height:26px;padding:0 8px;}}
        QWidget#resultColumn QToolButton#scopeButton {{min-height:26px;max-height:26px;padding:0 8px;
            border-radius:5px;background:{control};border:1px solid {control_line};}}
        QWidget#resultColumn QToolButton#scopeButton:hover {{background:{hover};border-color:{brand};}}
        QLineEdit#searchInput:focus, QComboBox#searchMode:focus {{border:1px solid #507fc5;}}
    """.format(**colors)
    return css


class DistributionChart(QWidget):
    selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.groups = []
        self.selected_path = None
        self.setMouseTracking(True)
        self.setFixedHeight(94)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip('文件命中分布 · 点击柱子跳转到文件；灰色表示已废止或替代')

    def set_groups(self, groups):
        self.groups = sorted(groups, key=lambda row: row['hits'], reverse=True)[:5]
        self.setVisible(bool(self.groups))
        self.update()

    def paintEvent(self, event):
        if not self.groups:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width() / len(self.groups)
        maximum = max(group['hits'] for group in self.groups)
        for index, group in enumerate(self.groups):
            left = index * width + 8
            bar_width = width - 16
            value = max(5, 34 * group['hits'] / max(maximum, 1))
            color = '#8a94a6' if group.get('status') in ('void', 'replaced') else '#477bc5'
            painter.setPen(Qt.PenStyle.NoPen)
            if group['path']==self.selected_path:
                painter.setPen(QColor('#285fba'))
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(QRectF(left, 50 - value, bar_width, value), 4, 4)
            painter.setPen(self.palette().text().color())
            painter.drawText(QRectF(left, 0, bar_width, 18), Qt.AlignmentFlag.AlignCenter, str(group['hits']))
            name = painter.fontMetrics().elidedText(group['name'], Qt.TextElideMode.ElideMiddle, int(bar_width))
            painter.drawText(QRectF(left, 57, bar_width, 25), Qt.AlignmentFlag.AlignCenter, name)

    def mousePressEvent(self, event):
        if self.groups:
            index = min(len(self.groups) - 1, int(event.position().x() / (self.width() / len(self.groups))))
            self.selected.emit(self.groups[index]['path'])

    def mouseMoveEvent(self,event):
        if self.groups:
            index=max(0,min(len(self.groups)-1,int(event.position().x()/max(1,self.width()/len(self.groups)))))
            group=self.groups[index]
            self.setToolTip(f"{group['name']} · 正文 {group['hits']} 处命中\n{group['path']}\n点击查看该文件")
        super().mouseMoveEvent(event)

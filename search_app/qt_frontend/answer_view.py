"""流式答案与可增长的问题输入，保留用户正在阅读的位置。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextCursor, QTextDocument
from PySide6.QtWidgets import QPlainTextEdit, QTextBrowser


class QuestionInput(QPlainTextEdit):
    submitted = Signal()
    height_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('questionInput')
        self.setPlaceholderText('输入问题 · Enter 发送，Shift+Enter 换行')
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.document().setDocumentMargin(4)
        self.setFixedHeight(28)
        self.textChanged.connect(self.fit_height)
        self.document().documentLayout().documentSizeChanged.connect(self.fit_height)

    def fit_height(self,*_):
        lines=0
        block=self.document().begin()
        while block.isValid() and lines<4:
            lines+=max(1,block.layout().lineCount())
            block=block.next()
        lines=max(1,min(4,lines))
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded if lines==4 else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        height = max(28, self.fontMetrics().lineSpacing() * lines + 10)
        if self.height()!=height:
            self.setFixedHeight(height)
            self.height_changed.emit(height)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submitted.emit()
            event.accept()
        else:
            super().keyPressEvent(event)


class AnswerView(QTextBrowser):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.raw_answer = ''
        self.pending_markdown = False
        self.setObjectName('answerView')
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.verticalScrollBar().valueChanged.connect(self.render_if_ready)
        self.selectionChanged.connect(self.render_if_ready)

    def clear(self):
        self.raw_answer = ''
        self.pending_markdown = False
        super().clear()

    def setPlainText(self, text):
        self.raw_answer = text
        self.pending_markdown = False
        super().setPlainText(text)

    def append_chunk(self, text):
        self.raw_answer += text
        bar = self.verticalScrollBar()
        position = bar.value()
        follow = position >= bar.maximum() - 2 and not self.textCursor().hasSelection()
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        bar.setValue(bar.maximum() if follow else position)

    def finish_markdown(self):
        self.pending_markdown = True
        self.render_if_ready()

    def render_if_ready(self, *_):
        if not self.pending_markdown or self.textCursor().hasSelection():
            return
        bar = self.verticalScrollBar()
        if bar.value() < bar.maximum() - 2:
            return
        self.pending_markdown = False
        self.document().setDefaultStyleSheet('table {border-collapse:collapse;} th, td {border:1px solid #8c99ad;padding:5px;} pre {white-space:pre-wrap;}')
        self.document().setMarkdown(self.raw_answer, QTextDocument.MarkdownFeature.MarkdownDialectGitHub | QTextDocument.MarkdownFeature.MarkdownNoHTML)
        bar.setValue(bar.maximum())

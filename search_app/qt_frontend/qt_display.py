"""所有原生弹窗的字体、行高及工作区适配。"""
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QApplication, QDialog, QPlainTextEdit, QTextEdit,
    QTableView, QTreeWidget, QPushButton, QLineEdit, QComboBox, QMessageBox)

from .display import display_sizes, fit_size


class DialogDisplayPolicy(QObject):
    def __init__(self, application, store):
        super().__init__(application)
        self.store = store
        application.installEventFilter(self)

    def polish(self, dialog):
        if not getattr(dialog,'_focus_return_installed',False):
            dialog._focus_return_installed=True
            parent=dialog.parentWidget()
            origin=parent.focusWidget() if parent else None
            def restore_focus(*_):
                from shiboken6 import isValid
                if origin and isValid(origin) and origin.isVisible() and origin.window().isActiveWindow():origin.setFocus()
            dialog.finished.connect(restore_focus)
        if isinstance(dialog,QMessageBox):
            yes=dialog.button(QMessageBox.StandardButton.Yes)
            no=dialog.button(QMessageBox.StandardButton.No)
            if yes and no:
                title=dialog.windowTitle()
                text=dialog.text()
                actions={'从索引中移除':'移除索引','确认清理':'移入回收站' if '回收站' in text else '移除索引',
                    '确认替换库内文件':'替换并保留备份','资料准备完成':'还原并重启',
                    '后台任务仍在运行':'停止任务并退出','资料包已还原':'打开还原资料库',
                    '开机启动':'启用开机启动' if '启用' in text else '关闭开机启动'}
                if title in actions:
                    yes.setText(actions[title])
                    no.setText('继续运行' if title=='后台任务仍在运行' else '取消')
                    dialog.setDefaultButton(QMessageBox.StandardButton.No)
                    if title in ('确认清理','从索引中移除','确认替换库内文件'):
                        yes.setProperty('danger',True)
        ui, text = display_sizes(self.store.settings)
        signature = ui, text
        if getattr(dialog, '_reading_signature', None) == signature:
            return
        dialog._reading_signature = signature
        dialog.setFont(QFont('Microsoft YaHei UI', ui))
        dialog.setStyleSheet(f'QWidget {{font-size:{ui}pt;}} '
            f'QPlainTextEdit, QTextEdit, QTextBrowser {{font-size:{text}pt;}} '
            'QPushButton, QComboBox, QLineEdit, QSpinBox {min-height:23px;} '
            'QTreeWidget::item {padding:4px 5px;}')
        for editor in dialog.findChildren(QPlainTextEdit) + dialog.findChildren(QTextEdit):
            editor.setFont(QFont('Microsoft YaHei UI', text))
        for table in dialog.findChildren(QTableView):
            minimum = table.fontMetrics().height() + 16
            table.verticalHeader().setMinimumSectionSize(minimum)
            table.verticalHeader().setDefaultSectionSize(max(minimum, table.verticalHeader().defaultSectionSize()))
        for tree in dialog.findChildren(QTreeWidget):
            tree.setUniformRowHeights(True)
        if dialog.layout():
            dialog.layout().activate()
        screen = dialog.screen() or QApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            desired_width = max(dialog.width(), dialog.minimumSizeHint().width())
            desired_height = max(dialog.height(), dialog.minimumSizeHint().height())
            width, height = fit_size(desired_width, desired_height, area.width(), area.height())
            dialog.resize(width, height)
            position = dialog.pos()
            dialog.move(max(area.left(), min(position.x(), area.right() - width - 12)),
                        max(area.top() + 12, min(position.y(), area.bottom() - height - 36)))

    def eventFilter(self, target, event):
        if isinstance(target, QDialog) and event.type() == QEvent.Type.Show:
            self.polish(target)
        return False

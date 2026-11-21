"""原生 Qt 入库窗口：预检、逐项选择、冲突决策、后台执行及明细。"""
import csv
import threading
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog,
    QHBoxLayout, QLabel, QMessageBox, QProgressBar, QStyledItemDelegate, QStyleOptionComboBox,
    QStyle, QTableView, QVBoxLayout)
from shiboken6 import isValid

from service import fmt_size
from . import import_service as service
from .widgets import button, label


class ImportModel(QAbstractTableModel):
    headings = ['文件', '大小', '预检结果', '处理方式 ▾', '目标 / 已有文件', '执行结果']

    def __init__(self, parent=None):
        super().__init__(parent)
        self.plans = []
        self.locked = False

    def set_plans(self, plans):
        self.beginResetModel()
        self.plans = plans
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.plans)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headings)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headings[section]

    def flags(self, index):
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if not self.locked and self.plans[index.row()].kind != 'error':
            if index.column() == 0:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            elif index.column() == 3:
                flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.plans[index.row()]
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 2:
            return QColor('#c13a28') if row.kind in ('conflict', 'error') else QColor('#8793a3') if row.kind == 'dup_skip' else None
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            return Qt.CheckState.Checked if row.checked else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.ToolTipRole:
            return (row.source if index.column() == 0 else row.result if index.column() == 5 else
                    '点击选择：' + ' / '.join(service.CHOICES[key] for key in row.choices())
                    if index.column() == 3 else row.target)
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return [Path(row.source).name, fmt_size(row.size), service.LABELS[row.kind],
                    service.CHOICES[row.choice], row.target, row.result][index.column()]

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if self.locked or not index.isValid():
            return False
        row = self.plans[index.row()]
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            row.checked = value in (Qt.CheckState.Checked, Qt.CheckState.Checked.value)
            if row.checked and row.choice == 'skip' and row.kind == 'dup_skip':
                row.choice = 'keep_both'
        elif index.column() == 3 and value in row.choices():
            row.choice = value
            row.checked = value != 'skip'
        else:
            return False
        self.dataChanged.emit(self.index(index.row(), 0), self.index(index.row(), 5))
        return True


class ChoiceDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        if index.model().locked or index.model().plans[index.row()].kind == 'error':
            return super().paint(painter, option, index)
        combo = QStyleOptionComboBox()
        combo.rect = option.rect.adjusted(2, 2, -2, -2)
        combo.palette = option.palette
        combo.state = option.state
        combo.currentText = option.fontMetrics.elidedText(str(index.data()), Qt.TextElideMode.ElideRight, combo.rect.width()-28)
        combo.frame = True
        style = option.widget.style()
        style.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, combo, painter, option.widget)
        style.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, combo, painter, option.widget)

    def createEditor(self, parent, option, index):
        editor = QComboBox(parent)
        for key in index.model().plans[index.row()].choices():
            editor.addItem(service.CHOICES[key], key)
        editor.activated.connect(lambda _: self.commitData.emit(editor))
        QTimer.singleShot(0, lambda: editor.showPopup() if isValid(editor) and editor.isVisible() else None)
        return editor

    def setEditorData(self, editor, index):
        editor.setCurrentIndex(editor.findData(index.model().plans[index.row()].choice))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentData())


class ImportDialog(QDialog):
    def __init__(self, window, paths):
        super().__init__(window)
        self.window, self.paths = window, list(paths)
        self.job = None
        self.running = False
        self.generation = 0
        self.result = None
        self.stop_request = threading.Event()
        self.setWindowTitle('导入资料 · 预检与冲突处理')
        self.resize(1280, 780)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.addWidget(label('先检查，再入库', 'section'))
        layout.addWidget(label('内容重复默认不选；同名冲突默认保留两份。替换前会保留旧文件及索引备份。', 'muted'))
        options = QHBoxLayout()
        self.link_only = QCheckBox('仅登记原路径，不复制文件')
        self.link_only.setChecked(bool(window.store.settings.get('link_only', False)))
        self.link_only.toggled.connect(self.analyze)
        options.addWidget(self.link_only)
        options.addStretch()
        self.select_all = button('全选可处理项', lambda: self.set_checked(True))
        self.select_none = button('清空选择', lambda: self.set_checked(False))
        options.addWidget(self.select_all)
        options.addWidget(self.select_none)
        layout.addLayout(options)
        self.model = ImportModel(self)
        self.model.dataChanged.connect(self.update_count)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(3, ChoiceDelegate(self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate([240, 85, 160, 175, 255]):
            self.table.setColumnWidth(column, width)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.SelectedClicked)
        self.table.clicked.connect(lambda index: self.table.edit(index) if index.column() == 3 and
            not self.model.locked and self.model.plans[index.row()].kind != 'error' else None)
        layout.addWidget(self.table, 1)
        self.count = QLabel('正在预检…')
        layout.addWidget(self.count)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)
        controls = QHBoxLayout()
        self.details_button = button('查看处理明细', self.show_details)
        self.export_button = button('导出明细', self.export_report)
        self.retry_button = button('重新预检失败项', self.retry_failed)
        for item in (self.details_button, self.export_button, self.retry_button):
            item.hide()
            controls.addWidget(item)
        controls.addStretch()
        self.stop_button = button('停止', self.stop)
        self.stop_button.hide()
        controls.addWidget(self.stop_button)
        self.background_button = button('后台运行', self.hide)
        self.background_button.hide()
        controls.addWidget(self.background_button)
        controls.addWidget(button('关闭', self.close))
        self.start_button = button('开始入库', self.start, True)
        self.start_button.setEnabled(False)
        controls.addWidget(self.start_button)
        layout.addLayout(controls)
        self.analyze()

    def analyze(self, *_):
        if self.running:
            return
        if self.job in self.window.jobs.active:
            self.window.jobs.cancel(self.job)
        self.generation += 1
        generation = self.generation
        self.start_button.setEnabled(False)
        self.model.set_plans([])
        self.result = None
        self.model.locked = False
        self.start_button.setText('开始入库')
        for control in (self.link_only, self.select_all, self.select_none):
            control.setEnabled(True)
        for item in (self.details_button, self.export_button, self.retry_button):
            item.hide()
        self.progress.setRange(0, 0)
        link_only = self.link_only.isChecked()
        def done(plans):
            if not isValid(self) or generation != self.generation:
                return
            self.model.locked = False
            self.model.set_plans(plans)
            self.progress.setRange(0, max(1, len(plans)))
            self.progress.setValue(len(plans))
            self.update_count()
        self.job = self.window.jobs.submit('导入预检', lambda progress, stop: service.analyze(
            self.window.store, self.paths, link_only, progress, stop), done,
            progress=self.on_progress, error=self.error, visible=False)

    def on_progress(self, value):
        if not isValid(self):
            return
        total = value.get('total', 0)
        self.progress.setRange(0, total)
        self.progress.setValue(value.get('current', 0))
        self.count.setText(str(value.get('status', '')) + ' · ' + Path(value.get('path', '')).name)

    def error(self, detail):
        if isValid(self):
            self.count.setText('未完成：' + detail.strip().splitlines()[-1])
            self.running = False
            self.model.locked = False
            self.link_only.setEnabled(True)
            self.select_all.setEnabled(True)
            self.select_none.setEnabled(True)
            self.stop_button.hide()
            self.update_count()

    def update_count(self, *_):
        if self.running:
            return
        selected = sum(row.checked and row.choice != 'skip' for row in self.model.plans)
        duplicates = sum(row.kind == 'dup_skip' for row in self.model.plans)
        conflicts = sum(row.kind == 'conflict' for row in self.model.plans)
        added = sum(row.kind not in ('dup_skip','conflict','error') for row in self.model.plans)
        unreadable = sum(row.kind == 'error' for row in self.model.plans)
        if self.result is not None:
            return
        self.count.setText(f'已选 {selected}/{len(self.model.plans)} 份 · 新增 {added} · 重复 {duplicates} · 同名冲突 {conflicts} · 不可读 {unreadable}')
        self.start_button.setEnabled(selected > 0 and self.result is None)

    def set_checked(self, value):
        if self.running:
            return
        for row in self.model.plans:
            row.checked = value and row.kind != 'error' and row.choice != 'skip'
        if self.model.plans:
            self.model.dataChanged.emit(self.model.index(0, 0), self.model.index(len(self.model.plans) - 1, 5))
        self.update_count()

    def start(self):
        if self.result is not None:
            self.accept()
            return
        if self.running:
            return
        replacing = [row for row in self.model.plans if row.checked and row.choice == 'replace']
        if replacing and QMessageBox.question(self, '确认替换库内文件',
                f'将替换 {len(replacing)} 个文件。旧文件和索引备份会保留在 backup。\n' +
                '\n'.join(Path(row.source).name for row in replacing[:8])) != QMessageBox.StandardButton.Yes:
            return
        if not any(row.checked and row.choice != 'skip' for row in self.model.plans):
            return
        if replacing and any(str(Path(row.target).resolve()) == self.window.pdf_reader.path for row in replacing):
            from PySide6.QtCore import QSignalBlocker
            self.window.pdf_reader.close_document()
            with QSignalBlocker(self.window.reader_kind):
                self.window.reader_kind.setCurrentIndex(0)
            self.window.reader_stack.setCurrentIndex(0)
        self.running = True
        self.stop_request.clear()
        self.model.locked = True
        self.start_button.setEnabled(False)
        self.link_only.setEnabled(False)
        self.select_all.setEnabled(False)
        self.select_none.setEnabled(False)
        self.stop_button.show()
        self.background_button.show()
        self.window.store.settings.update(link_only=self.link_only.isChecked(), copy_import=not self.link_only.isChecked())
        self.window.store.save()
        def done(result):
            if not isValid(self):
                return
            self.result = result
            self.running = False
            self.model.locked = True
            self.stop_button.hide()
            self.background_button.hide()
            for control in (self.link_only, self.select_all, self.select_none):
                control.setEnabled(False)
            self.start_button.setText('完成')
            self.start_button.setEnabled(True)
            self.count.setText(f"{'已停止' if result['stopped'] else '处理完成'} · 成功 {result['ok']} · 跳过 {result['skipped']} · 失败 {result['failed']}")
            self.model.layoutChanged.emit()
            self.details_button.show()
            self.export_button.show()
            self.retry_button.setVisible(result['failed'] > 0 or result['stopped'])
            self.window.after_index(result)
        def work(progress, stop):
            class AnyStop:
                def is_set(inner):
                    return stop.is_set() or self.stop_request.is_set()
            return service.execute(self.window.store, self.model.plans, progress, AnyStop())
        self.job = self.window.jobs.submit('导入资料', work, done,
            progress=self.on_progress, error=self.error, mutating=True)
        self.window.jobs.jobs[self.job].import_dialog = self

    def stop(self):
        # 不标记 Job 取消，保留已完成结果并显示部分执行报告。
        if self.job in self.window.jobs.active:
            self.stop_request.set()
            self.count.setText('正在停止，当前文件完成后保留已处理结果…')

    def show_details(self):
        if self.result:
            self.window.records_dialog('入库处理明细', [('源文件', 'source'), ('结果', 'state'),
                ('说明', 'detail'), ('库内位置', 'path')], self.result['details'],
                lambda row: self.window.open_preview(row['path'], 1) if row.get('path') else None)

    def export_report(self):
        if not self.result:
            return
        target, _ = QFileDialog.getSaveFileName(self, '导出入库明细', '入库明细.csv', 'CSV 文件 (*.csv)')
        if target:
            with open(target, 'w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['源文件', '结果', '说明', '库内位置'])
                for row in self.result['details']:
                    writer.writerow([row.get(key, '') for key in ('source', 'state', 'detail', 'path')])
            self.window.notify('已导出入库明细')

    def retry_failed(self):
        if self.result:
            paths = {row['source'] for row in self.result['details'] if row['state'] == '失败'}
            paths.update(row.source for row in self.model.plans if row.checked and not row.result)
            if paths:
                self.paths = sorted(paths)
                self.analyze()

    def closeEvent(self, event):
        if self.running:
            self.hide()
            event.ignore()
            self.window.notify('入库继续在后台运行，可在“任务”双击恢复窗口')
        else:
            if self.job in self.window.jobs.active:
                self.window.jobs.cancel(self.job)
            super().closeEvent(event)

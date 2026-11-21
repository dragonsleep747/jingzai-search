"""在独立 Qt 进程模拟显示缩放，检查真实子窗口字号及可见边界。"""
import argparse
import json
import os
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--scale', type=float, required=True)
parser.add_argument('--output', required=True)
args = parser.parse_args()
workspace = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(workspace / 'search_app'))
from qt_frontend.display import enable_high_dpi, dpi_info
enable_high_dpi()
native_scale = (dpi_info()['dpi'] or 96) / 96
os.environ['QT_SCALE_FACTOR'] = str(args.scale / native_scale)
import qt_main
from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QPlainTextEdit, QScrollArea
from qt_frontend.backend import Store
from qt_frontend.window import Window
from qt_frontend.qt_display import DialogDisplayPolicy

output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=True)
app = QApplication([])
app.setFont(QFont('Microsoft YaHei UI', 10))
app.setStyle('Fusion')
store = Store(output / 'data')
store.settings.update(max_backups=0, qt_startup_refresh=False, mobile_enabled=False)
window = Window(store)
window.dialog_display = DialogDisplayPolicy(app, store)
window.show()
checks, errors = [], []

def record(dialog, name):
    app.processEvents()
    area = dialog.screen().availableGeometry()
    frame = dialog.frameGeometry()
    bounded = (frame.width() <= area.width() + 12 and frame.height() <= area.height() + 12
               and frame.left() >= area.left() - 12 and frame.top() >= area.top() - 12
               and frame.right() <= area.right() + 12 and frame.bottom() <= area.bottom() + 12)
    body = dialog.findChildren(QPlainTextEdit)
    item = {'name': name, 'bounded': bounded, 'dpr': dialog.devicePixelRatioF(),
            'ui_font': dialog.font().pointSize(), 'text_fonts': [text.font().pointSize() for text in body],
            'frame': [frame.x(), frame.y(), frame.width(), frame.height()],
            'available': [area.x(), area.y(), area.width(), area.height()], **dpi_info(int(dialog.winId()))}
    buttons = dialog.findChildren(QDialogButtonBox)
    item['footer_visible'] = all(0 <= button.mapTo(dialog, QPoint()).y() and
        button.mapTo(dialog, QPoint()).y() + button.height() <= dialog.height()
        for box in buttons for button in box.buttons())
    dialog.grab().save(str(output / (name + '.png')))
    item['ok'] = bounded and item['footer_visible'] and item['ui_font'] == 11 and all(size >= 12 for size in item['text_fonts'])
    checks.append(item)

def run():
    try:
        dialog = window.text_dialog('阅读清晰度验收', '中文阅读示例：字体应清晰，行距舒适。\n' * 10)
        record(dialog, 'text')
        dialog.close()
        dialog = window.records_dialog('列表清晰度验收', [('文件', 'name'), ('状态', 'status')],
            [{'name': '原始资料.pdf', 'status': '有效'}, {'name': '新旧版本对比.docx', 'status': '已替代'}])
        record(dialog, 'list')
        dialog.close()
        def inspect():
            try:
                dialog = QApplication.activeModalWidget()
                if not isinstance(dialog, QDialog):
                    raise RuntimeError('设置窗口没有打开')
                record(dialog, 'settings')
                checks[-1]['scrollable'] = bool(dialog.findChildren(QScrollArea))
                dialog.reject()
            except Exception as error:
                errors.append(str(error))
                if QApplication.activeModalWidget():
                    QApplication.activeModalWidget().reject()
        QTimer.singleShot(250, inspect)
        window.settings_dialog()
    except Exception as error:
        errors.append(str(error))
    report = {'scale': args.scale, 'ok': not errors and len(checks) == 3 and all(item['ok'] for item in checks),
              'checks': checks, 'errors': errors}
    (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True), flush=True)
    window.jobs.pool.waitForDone()
    app.processEvents()
    window._force_exit = True
    window.close()
    app.exit(int(not report['ok']))

QTimer.singleShot(400, run)
sys.exit(app.exec())

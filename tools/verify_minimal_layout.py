"""在独立库检查极简界面及模拟缩放；所有截图来自实际 Qt 窗口。"""
import argparse
import json
import os
from pathlib import Path
import sys
import threading

parser = argparse.ArgumentParser()
parser.add_argument('--output', required=True)
parser.add_argument('--scale', type=float, default=1.25)
args = parser.parse_args()
workspace = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(workspace / 'search_app'))
from qt_frontend.display import enable_high_dpi, dpi_info
enable_high_dpi()
os.environ['QT_SCALE_FACTOR'] = str(args.scale / ((dpi_info()['dpi'] or 96) / 96))
import qt_main
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from qt_frontend.backend import Store
from qt_frontend.window import Window
from qt_frontend.minimal_acceptance import check_minimal_layout

output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=True)
app = QApplication([])
app.setFont(QFont('Microsoft YaHei UI', 10))
app.setStyle('Fusion')
store = Store(output / 'data')
store.settings.update(max_backups=0, qt_startup_refresh=False, autowatch=False, mobile_enabled=False)
source = store.root / '安全培训管理办法（界面验收样本）.txt'
source.write_text('第一章 总则\n第一条 员工应接受安全培训，经考核合格后上岗。\n'
    + '第二条 培训记录应真实、完整，定期检查并归档。\n' * 40, encoding='utf-8')
store.index_files([str(source)], lambda _: None, threading.Event())
window = Window(store)
area = app.primaryScreen().availableGeometry()
window.resize(min(1380, area.width() - 40), min(880, area.height() - 60))
window.show()
report = {'ok': False, 'scale': args.scale, 'errors': []}

def finish():
    (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    window._force_exit = True
    window.close()
    app.quit()

def inspect():
    if window.current is None:
        QTimer.singleShot(100, inspect)
        return
    try:
        report.update(check_minimal_layout(window, output))
        window.navigate('tools')
        window.tool_category.setCurrentText('全部')
        app.processEvents()
        window.grab().save(str(output / 'tool-center.png'))
        report['tools'] = [entry['name'] for entry in window.tools_entries()]
        report['ok'] = True
    except Exception as exc:
        report['errors'].append(str(exc))
    finish()

def exception_hook(kind, value, trace):
    import traceback
    report['errors'].append(''.join(traceback.format_exception(kind, value, trace)))
    finish()

sys.excepthook = exception_hook
QTimer.singleShot(50, lambda: window.use_query('安全培训'))
QTimer.singleShot(150, inspect)
QTimer.singleShot(20000, lambda: (report['errors'].append('timeout'), finish()))
app.exec()
print(json.dumps(report, ensure_ascii=True))
sys.exit(0 if report['ok'] and not report['errors'] else 1)

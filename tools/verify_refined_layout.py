"""亮暗主题、窄宽窗口和不同缩放下检查高频界面，不使用用户资料。"""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'search_app'))
from qt_frontend.display import enable_high_dpi,dpi_info
enable_high_dpi()
parser=argparse.ArgumentParser()
parser.add_argument('--scale',type=float,required=True)
parser.add_argument('--out',required=True)
args=parser.parse_args()
os.environ['QT_SCALE_FACTOR']=str(args.scale/((dpi_info()['dpi'] or 96)/96))
import qt_main
from PySide6.QtCore import QPoint,Qt
from PySide6.QtGui import QFont,QImage,QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QDialog,QComboBox,QAbstractButton
from qt_frontend.backend import Store
from qt_frontend.window import Window
from qt_frontend.qt_display import DialogDisplayPolicy
from qt_frontend.experience_acceptance import make_pdf

out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
app=QApplication([]);app.setStyle('Fusion');app.setFont(QFont('Microsoft YaHei UI',10))
store=Store(out/'data')
store.settings.update(max_backups=0,qt_startup_refresh=False,mobile_enabled=False,autowatch=False,qa_auto=False,ollama_base='http://127.0.0.1:1')
source=store.root/'安全管理制度（2026年修订长文件名称排版样本）.txt'
source.write_text('第一章 总则\n第一条 安全培训记录应保存。\n第二条 记录应完整并定期归档。\n'*8,encoding='utf-8')
pdf=store.root/'PDF原文样本.pdf';make_pdf(pdf)
picture=store.root/'图片比例样本.png'
img=QImage(1200,800,QImage.Format.Format_RGB32);img.fill(QColor('#b4c9e8'));img.save(str(picture))
store.index_files([str(source),str(pdf)],lambda _:None,threading.Event())
w=Window(store);w.dialog_display=DialogDisplayPolicy(app,store);w.sync_semantic=lambda:None
w.show();checks=[];errors=[]
sys.excepthook=lambda kind,error,trace:errors.append(str(error))
def idle():
    deadline=time.monotonic()+10
    while (w.jobs.active or w.search_debounce.isActive()) and time.monotonic()<deadline:app.processEvents();QTest.qWait(10)
    app.processEvents();QTest.qWait(50)
def capture(name):
    app.processEvents();QTest.qWait(70)
    toolbar=[w.search_mode,w.search_button,w.range_button,w.options_button,w.input_stack,
        w.preview_range,w.read_button,w.hit_previous,w.hit_next,w.pdf_reader.controls]
    clipped=[]
    for item in toolbar:
        if item.isVisible():
            point=item.mapTo(w,QPoint())
            if point.x()<0 or point.y()<0 or point.x()+item.width()>w.width()+2 or point.y()+item.height()>w.height()+2:clipped.append(item.objectName() or type(item).__name__)
    controls=w.search_mode.height()==28 and w.search_button.width()==28 and w.search_button.height()==28
    path=out/(name+'.png');w.grab().save(str(path))
    checks.append(dict(scene=name,ok=not clipped and controls,clipped=clipped,compact_controls=controls,
        width=w.width(),height=w.height(),dpr=w.devicePixelRatioF(),screenshot=str(path)))
for dark in (False,True):
    store.settings['dark']=dark;w.apply_theme()
    for width,height in ((940,680),(1380,880)):
        if w.search_mode.currentIndex():w.search_mode.setCurrentIndex(0)
        w.preview_panel.show();w.resize(width,height);w.use_query('安全培训');idle()
        prefix=('dark' if dark else 'light')+'-'+str(width)
        capture(prefix+'-explore')
        w.search_mode.setCurrentIndex(1);w.ai_kind.setCurrentIndex(0)
        w.search_input.setText('请结合资料逐项解释安全培训记录要求。\n并给出出处与执行步骤。')
        with patch('ai_qa.chat_stream',return_value=iter(['## 记录要求\n\n1. 保存培训记录。\n2. 定期检查归档。\n\n|事项|要求|\n|---|---|\n|记录|完整|'])):
            w.ask_ai();idle()
        capture(prefix+'-qa')
        w.ai_kind.setCurrentIndex(1);capture(prefix+'-chat')
        w.search_mode.setCurrentIndex(0);w.open_preview(str(pdf));idle();capture(prefix+'-pdf')
        w.preview_discovery_file(dict(path=str(picture),name=picture.name,kind='file',online=True));idle();capture(prefix+'-image')
w._force_exit=True;w._closing_requested=True;idle();w.close();w.jobs.pool.waitForDone(5000)
report=dict(ok=bool(checks) and all(c['ok'] for c in checks) and not errors,scale=args.scale,checks=checks,errors=errors)
(out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(dict(ok=report['ok'],scale=args.scale,scenes=len(checks),errors=errors,clipped=[c for c in checks if not c['ok']]),ensure_ascii=True))
sys.exit(int(not report['ok']))

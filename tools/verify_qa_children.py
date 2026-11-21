"""复现真实兼容入口的智答子窗，不强制显示或替换定位/模态逻辑。"""
import argparse
import builtins
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'search_app'))
import qt_main
from qt_frontend.backend import Store
from qt_frontend.legacy_bridge import run_tool
import tkinter as tk

parser=argparse.ArgumentParser()
parser.add_argument('--output',required=True)
parser.add_argument('--child',choices=('models','history','bench'))
args=parser.parse_args()
out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
if not args.child:
    reports=[]
    for child in ('models','history','bench'):
        result=subprocess.run([sys.executable,__file__,'--output',str(out),'--child',child],capture_output=True,timeout=18,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        path=out/child/'result.json'
        report=json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(child=child,ok=False,error=result.stderr.decode('utf-8',errors='replace'))
        report['exit_code']=result.returncode
        reports.append(report)
    (out/'result.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(reports,ensure_ascii=True))
    sys.exit(int(any(not r['ok'] for r in reports)))

folder=out/args.child;folder.mkdir(exist_ok=True)
store=Store(folder/'data')
store.settings.update(max_backups=0,qa_auto=False,ollama_base='http://127.0.0.1:1',qt_startup_refresh=False)
store.save()
holder={};errors=[];checks=[]
original_import=builtins.__import__
def track(name,*a,**kw):
    module=original_import(name,*a,**kw)
    if name=='main' and hasattr(module,'App') and not holder.get('wrapped'):
        original=module.App.__init__
        def init(app,*a,**kw):original(app,*a,**kw);holder['app']=app
        module.App.__init__=init;holder['wrapped']=True
    return module
builtins.__import__=track
original_loop=tk.Misc.mainloop
def walk(widget):
    yield widget
    for child in widget.winfo_children():yield from walk(child)
def loop(root,*a,**kw):
    root.report_callback_exception=lambda kind,error,trace:errors.append(str(error))
    target_title={'models':'模型设置','history':'问答历史记录','bench':'模型测速'}[args.child]
    def open_child():
        qa=holder['app']._qa_window
        if args.child=='models':qa.show_settings()
        elif args.child=='history':
            (Path(qa._qa_dir)/'2026-10-30-验收.txt').write_text('问题：样本\n回答：完成\n出处：样本第一条',encoding='utf-8')
            qa.show_history()
        else:qa.model_bench()
    def inspect():
        targets=[w for w in walk(root) if isinstance(w,tk.Toplevel) and w.title()==target_title]
        if not targets:
            errors.append('没有创建目标子窗');return
        target=targets[-1]
        content=[w for w in walk(target) if w.winfo_viewable()]
        fields=[w for w in content if w.winfo_class() in ('TEntry','TCombobox','TButton','Listbox')]
        clipped=[w.winfo_class() for w in fields if w.winfo_rootx()+w.winfo_width()>target.winfo_rootx()+target.winfo_width()+2 or
            w.winfo_rooty()+w.winfo_height()>target.winfo_rooty()+target.winfo_height()+2]
        checks.append(dict(title=target.title(),visible=bool(target.winfo_viewable()),alpha=float(target.attributes('-alpha')),
            content=len(content),clipped=clipped,size=[target.winfo_width(),target.winfo_height()]))
        target.destroy()
        checks.append(dict(parent_released=holder['app']._qa_window.win.grab_current() is None))
    root.after(500,open_child)
    root.after(2200,inspect)
    root.after(2700,root.destroy)
    return original_loop(root,*a,**kw)
tk.Misc.mainloop=loop
run_tool('qa',store.root)
ok=bool(checks) and checks[0]['visible'] and checks[0]['alpha']==1 and checks[0]['content']>=3 and not checks[0]['clipped'] and checks[-1].get('parent_released') and not errors
report=dict(child=args.child,ok=ok,checks=checks,errors=errors)
(folder/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
sys.exit(int(not ok))

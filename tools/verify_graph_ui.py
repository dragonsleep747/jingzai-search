"""用隔离 Edge 窗口验证本地引用图交互，不访问外部页面。"""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'search_app'))
import qt_main
import graph_html
parser=argparse.ArgumentParser()
parser.add_argument('--db',required=True)
parser.add_argument('--out',required=True)
args=parser.parse_args()
out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
page=out/'引用关系图.html'
html,stats=graph_html.generate_graph(str(Path(args.db).resolve()));page.write_text(html,encoding='utf-8')
edge=Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
with socket.socket() as reserve:reserve.bind(('127.0.0.1',0));port=reserve.getsockname()[1]
browser=subprocess.Popen([str(edge),'--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
    '--remote-debugging-address=127.0.0.1','--remote-debugging-port='+str(port),'--window-size=1300,900',
    '--user-data-dir='+str(out/'browser-profile'),page.as_uri()],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
try:
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/json',timeout=1) as response:json.load(response)
            break
        except OSError:time.sleep(.1)
    result=subprocess.run(['node',str(root/'tools/verify_graph_ui.mjs'),str(port),str(out)],capture_output=True,timeout=25,creationflags=subprocess.CREATE_NO_WINDOW)
    print(result.stdout.decode('utf-8',errors='replace'))
    if result.stderr:print(result.stderr.decode('utf-8',errors='replace'))
    sys.exit(result.returncode)
finally:
    try:browser.wait(timeout=4)
    except subprocess.TimeoutExpired:browser.terminate()

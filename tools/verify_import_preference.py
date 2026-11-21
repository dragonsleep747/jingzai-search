"""真实 Tk 入库预检的偏好回归；只分析独立样本，不执行入库。"""
import builtins
import json
from pathlib import Path
import sys

workspace = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(workspace / 'search_app'))
import qt_main  # 同正式入口一样先设置 Windows 版本查询回退。
from qt_frontend.legacy_bridge import run_tool

link_only = sys.argv[1] == '1'
folder = workspace / '.tmp' / ('qt-import-preference-' + str(int(link_only)))
folder.mkdir(parents=True, exist_ok=True)
(folder / 'config.qt.json').write_text(json.dumps({'link_only': link_only,
    'copy_import': link_only, 'autowatch': False, 'max_backups': 0}), encoding='utf-8')
sample = folder / '偏好验收.txt'
sample.write_text('这份样本只检查预检采用的导入模式。', encoding='utf-8')
context = folder / 'context.json'
context.write_text(json.dumps({'paths': [str(sample)]}), encoding='utf-8')
captured = []
original_import = builtins.__import__

def importing(name, *args, **kwargs):
    module = original_import(name, *args, **kwargs)
    if name == 'main' and hasattr(module, 'App') and not getattr(module.App, '_preference_verified', False):
        original_flow = module.App._start_import_flow
        def flow(app, paths, mode):
            captured.append(mode)
            return original_flow(app, paths, mode)
        module.App._start_import_flow = flow
        module.App._preference_verified = True
    return module

builtins.__import__ = importing
report_path = folder / 'result.json'
run_tool('import', folder, context_file=context, verify_output=report_path)
report = json.loads(report_path.read_text(encoding='utf-8'))
report.update(expected_link_only=link_only, captured=captured)
report['ok'] = report['ok'] and captured == [link_only]
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=True))
sys.exit(int(not report['ok']))

"""在独立资料库逐个启动完整兼容工具，检测回调异常和实际可见窗口。"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--executable')
    parser.add_argument('--output', required=True)
    parser.add_argument('--require-dpi', action='store_true')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    workspace = Path(__file__).resolve().parents[1]
    command = [args.executable] if args.executable else [sys.executable, str(workspace / 'search_app' / 'qt_main.py')]
    reports = []
    tools = ('help', 'library', 'bookmarks', 'category', 'category_rules', 'ref_warnings',
             'gap', 'index_health', 'synonyms', 'ocr', 'web', 'qa', 'quick', 'inbox', 'import')
    for tool in tools:
        folder = output / ('data-' + tool)
        folder.mkdir(exist_ok=True)
        (folder / 'config.qt.json').write_text(json.dumps({'max_backups': 0, 'ollama_base': 'http://127.0.0.1:1',
            'download_watch_dir': str(folder), 'inbox_auto_check': False}), encoding='utf-8')
        inbox = folder / 'inbox'
        inbox.mkdir(exist_ok=True)
        (inbox / '待入库验收.txt').write_text('完整审核流程验收资料。', encoding='utf-8')
        report = output / (tool + '.json')
        extra = []
        if tool == 'import':
            context = output / 'import-context.json'
            context.write_text(json.dumps({'paths': [str(inbox / '待入库验收.txt')]}), encoding='utf-8')
            extra = ['--legacy-context', str(context)]
        try:
            result = subprocess.run(command + ['--data-dir', str(folder), '--legacy-tool', tool,
                '--verify-legacy', str(report), *extra], capture_output=True, timeout=30,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            data = json.loads(report.read_text(encoding='utf-8')) if report.is_file() else {
                'tool': tool, 'ok': False, 'error': result.stderr.decode('utf-8', errors='replace')[-2000:]}
            data['exit_code'] = result.returncode
            if args.require_dpi:
                displays = data.get('native_display', [])
                # Tk 的 Windows 标准消息框使用系统 DPI 上下文；自有内容窗口
                # 必须 Per-Monitor V2。两者均不允许 DPI-unaware 位图拉伸。
                native_ok = bool(displays) and all(
                    (item.get('awareness') == 2 and item.get('per_monitor_v2')) or
                    (item.get('class') == '#32770' and item.get('awareness') == 1) for item in displays)
                own_ok = all(item.get('awareness') == 2 and item.get('per_monitor_v2') for item in data.get('display', []))
                data['dpi_ok'] = native_ok and own_ok
                data['ok'] = data['ok'] and data['dpi_ok']
        except subprocess.TimeoutExpired:
            data = {'tool': tool, 'ok': False, 'error': 'timeout'}
        reports.append(data)
        print(json.dumps(data, ensure_ascii=True), flush=True)
    (output / 'result.json').write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
    return int(any(not report['ok'] or report.get('exit_code', 0) for report in reports))


if __name__ == '__main__':
    sys.exit(main())

"""高级功能兼容进程。沿用旧版完整界面流程，避免把入口误当作迁移完成。"""
from pathlib import Path
import json
import subprocess
import sys
import time
import threading
import os

TOOLS = {
    'qa': ('完整镜仔智答', 'show_qa_window'),
    'web': ('完整联网检索', 'show_websearch'),
    'import': ('入库预检与冲突处理', 'import_files_dialog'),
    'inbox': ('待入库文件审核', 'show_inbox_review'),
    'library': ('旧版完整库管理', 'show_library'),
    'bookmarks': ('完整收藏管理', 'show_bookmarks'),
    'dedupe': ('完整查重清理', 'show_dedupe'),
    'category': ('完整分类自查', '_show_category_audit'),
    'category_rules': ('分类规则管理', '_show_category_manager'),
    'problem_files': ('问题文件管理', 'show_problem_files'),
    'ref_warnings': ('全库引用预警', 'show_ref_warnings'),
    'gap': ('合规 Gap 扫描', '_show_compliance_gap'),
    'versions': ('完整版本对比', 'show_diff'),
    'index_health': ('索引体检与备份设置', 'show_index_health'),
    'standards': ('标准更新检查', '_show_std_update'),
    'synonyms': ('同义词库与内置词组', 'show_synonym_settings'),
    'ocr': ('OCR 组件诊断', 'show_ocr_status'),
    'package': ('打包资料库', '_package_library'),
    'package_all': ('打包软件与资料库', '_package_all'),
    'restore': ('旧版原位还原资料库', '_restore_library'),
    'download_watch': ('下载文件夹监控', '_choose_download_watch_dir'),
    'download_review': ('下载文件待入库审核', 'show_inbox_review'),
    'export_dir': ('默认存档目录', '_choose_export_dir'),
    'feedback': ('意见反馈', 'open_feedback'),
    'help': ('旧版完整使用说明', 'show_help'),
    'quick': ('迷你速查与粘贴引用', None),
}


def launcher_arguments(*arguments):
    if getattr(sys, 'frozen', False):
        return [sys.executable, *arguments]
    return [sys.executable, str(Path(__file__).parents[1] / 'qt_main.py'), *arguments]


def forward_to_qt(folder, *arguments):
    subprocess.Popen(launcher_arguments('--data-dir', str(folder), *arguments),
                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def run_tool(tool, folder, foreground=0, context_file=None, verify_output=None):
    from .display import enable_high_dpi
    enable_high_dpi()
    if tool not in TOOLS:
        raise ValueError('未知兼容工具')
    folder = Path(folder).resolve()
    import core
    core.APP_DIR = str(folder)
    core.CONFIG_PATH = str(folder / 'config.qt.json')
    resources = Path(getattr(sys, '_MEIPASS', Path(__file__).parents[1]))
    core._resource_path = lambda name: str(resources / name)
    core._resolve_data_root = lambda *_: str(folder)
    import runtime
    runtime.DATA_ROOT, runtime.DB_PATH = str(folder), str(folder / 'index.db')
    runtime.BACKUP_DIR, runtime.LIBRARY_DIR = str(folder / 'backup'), str(folder / 'library')
    import indexer
    indexer.LIBRARY_DIR = runtime.LIBRARY_DIR
    import main as legacy
    import tkinter as tk
    # 本进程只承载用户打开的工具，不重复运行主界面的后台轮询和扫描。
    legacy.App._watch_restore = lambda self: None
    legacy.App._start_hotkeys = lambda self: None
    root = legacy.TkinterDnD.Tk() if legacy._DND_AVAILABLE else tk.Tk()
    root.withdraw()
    from .backend import Store
    from .tk_display import prepare_tk, TkDisplayPolicy
    preferences = Store(folder)
    prepare_tk(root, legacy, preferences.settings)
    app = legacy.App(root)
    # 兼容宿主本身透明，旧主窗的浮入动画会重入 Map 布局并把子窗留在透明态。
    # 独立工具直接显示，避免动画与 DPI 适配、模态子窗争用窗口几何。
    app._anim_ok = lambda: False
    import copy
    import ui_dialogs
    from .backend import Store
    persistence = Store(folder)
    persistence.settings = app.cfg
    persistence._saved_snapshot = copy.deepcopy(app.cfg)
    def save_settings(config):
        persistence.settings = config
        persistence.save()
    legacy.save_config = core.save_config = ui_dialogs.save_config = save_settings
    for timer in root.tk.call('after', 'info'):
        root.after_cancel(timer)
    display_policy = TkDisplayPolicy(root, app)
    app._cover = None
    app._restore_hw = None
    app._cover_dirty = False
    app._cover_rect = None
    app._show_preview = lambda path, line, *args, **kwargs: forward_to_qt(folder, '--open-path', path, '--open-line', str(line))
    app.accept_remote_keyword = lambda query: forward_to_qt(folder, '--search', query)
    app._remote_search = app.accept_remote_keyword
    for method, action in [('_package_all', 'package-software'), ('_package_library', 'backup'),
                           ('_restore_library', 'restore')]:
        setattr(app, method, lambda *args, _action=action, **kwargs: forward_to_qt(folder, '--action', _action))
    width, height = root.winfo_screenwidth(), root.winfo_screenheight()
    root.attributes('-alpha', 0.0)
    root.overrideredirect(True)
    root.geometry(f'1x1+{width // 2}+{height // 2}')
    root.deiconify()
    root.update_idletasks()
    root.title('镜仔搜索 · 完整工具兼容宿主')
    errors = []
    existing_threads = set(threading.enumerate())
    root.report_callback_exception = lambda kind, error, trace: errors.append(str(error))
    def launch():
        if tool == 'quick':
            if app.quick_win is None:
                raise RuntimeError('迷你速查组件不可用')
            app.quick_win.show()
            app.quick_win._poll_results()
            if foreground:
                app.quick_win._saved_hwnd = foreground
            if verify_output:
                sample = folder / '迷你速查验收.txt'
                sample.write_text('第一条 迷你检索独有验收内容。', encoding='utf-8')
                indexer.index_single_file(str(sample), str(folder / 'index.db'))
                app.quick_win.entry.insert(0, '迷你检索独有')
                app.quick_win._search()
            return
        context = None
        if context_file:
            context_path = Path(context_file)
            context = json.loads(context_path.read_text(encoding='utf-8'))
            context_path.unlink(missing_ok=True)
        if tool == 'import' and context and context.get('paths'):
            app._start_import_flow(context['paths'], app.link_only.get())
        else:
            getattr(app, TOOLS[tool][1])()
        if tool == 'qa':
            display_policy.prepare_qa()
        if tool == 'qa' and context and app._qa_window:
            app._qa_window.set_search_context(context['hits'], context['query'], context['files'])
    started = time.monotonic()
    seen = [False]
    quick_command = folder / '.qt_quick_command'
    last_quick_command = [quick_command.read_text(encoding='ascii') if quick_command.exists() else '']

    def toplevels(widget):
        windows = []
        for child in widget.winfo_children():
            if isinstance(child, tk.Toplevel):
                windows.append(child)
                windows.extend(toplevels(child))
        return windows

    def poll():
        if tool == 'quick' and quick_command.exists():
            command = quick_command.read_text(encoding='ascii')
            if command != last_quick_command[0]:
                last_quick_command[0] = command
                app.quick_win.hide()
        windows = [window for window in toplevels(root) if window.winfo_viewable()]
        titles = [window.title() for window in windows]
        native_display = []
        # Windows 原生消息框不在 tkinter.winfo_children 中，也必须算作存活工具。
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            api = ctypes.windll.user32
            api.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
            api.IsWindowVisible.argtypes = (wintypes.HWND,)
            api.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def visit(hwnd, _):
                pid = wintypes.DWORD()
                api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == os.getpid() and api.IsWindowVisible(hwnd):
                    text = ctypes.create_unicode_buffer(512)
                    api.GetWindowTextW(hwnd, text, len(text))
                    if text.value and text.value != '镜仔搜索 · 完整工具兼容宿主':
                        titles.append(text.value)
                        from .display import dpi_info
                        kind = ctypes.create_unicode_buffer(256)
                        api.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
                        api.GetClassNameW(hwnd, kind, len(kind))
                        native_display.append({'title': text.value, 'class': kind.value, **dpi_info(hwnd)})
                return True
            api.EnumWindows(visit, 0)
        titles = list(dict.fromkeys(titles))
        visible = bool(windows or titles)
        if visible:
            seen[0] = True
        flow_ready = tool != 'quick' or bool(app.quick_win.rows)
        if tool == 'import':
            flow_ready = any('确认入库' in title for title in titles)
        if verify_output and time.monotonic() - started > .8 and ((visible and flow_ready) or errors or time.monotonic() - started > 15):
            report = {'tool': tool, 'ok': visible and not errors,
                      'titles': titles, 'errors': errors, 'display': display_policy.describe(windows),
                      'native_display': native_display}
            if tool == 'quick':
                report['search_hits'] = len(app.quick_win.rows)
                report['citation'] = app.quick_win._cite()
                # 真窗口及真实检索，粘贴边界替换成记录器，防止写进用户当前文档。
                import quickwin
                captured = []
                app.root.clipboard_clear = lambda: None
                app.root.clipboard_append = lambda text: captured.append(text)
                quickwin.paste_to_foreground = lambda hwnd: captured.append(hwnd) or True
                app.quick_win._saved_hwnd = 12345
                app.quick_win._on_enter()
                report['paste_route'] = captured[-1] == 12345 if captured else False
                report['ok'] = report['ok'] and bool(report['search_hits'] and report['citation'] and report['paste_route'])
            if tool == 'import':
                report['ok'] = report['ok'] and flow_ready
            Path(verify_output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            if getattr(app, '_qa_window', None):
                app._qa_window._stop_ask.set()
                app._qa_window._build_cancel.set()
            root.destroy()
            return
        busy_threads = [thread for thread in threading.enumerate()
                        if thread not in existing_threads and thread.is_alive()]
        if not verify_output and not visible and not busy_threads and not getattr(app, '_task_busy', False) and not getattr(app, '_bg_mode', False):
            if seen[0] or time.monotonic() - started > 3:
                root.destroy()
                return
        root.after(250, poll)
    root.after(250, poll)
    root.after(0, launch)
    root.mainloop()
    return 0

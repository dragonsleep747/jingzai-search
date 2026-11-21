"""镜仔搜索新版入口：Qt 原生桌面窗口，与旧版独立运行。"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
from pathlib import Path
import sys

from qt_frontend.display import enable_high_dpi
enable_high_dpi()

# Python 3.12 的 platform 在查询 Windows 版本时先访问 WMI。WMI 忙碌或
# 不响应会卡住 OCR 依赖的首次导入；使用标准库已有的 Win32/注册表回退。
if sys.platform == 'win32':
    import platform
    if hasattr(platform, '_wmi_query'):
        def _native_windows_version(*args):
            raise OSError('Use native Windows version APIs')
        platform._wmi_query = _native_windows_version

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from qt_frontend.backend import Store, locate_data
from qt_frontend.native import Hotkeys
from qt_frontend.window import Window


def main(argv=None):
    parser = argparse.ArgumentParser(description='镜仔搜索新版')
    parser.add_argument('--data-dir', help='资料库目录（包含 index.db）')
    parser.add_argument('--search', '-s', help='启动后搜索关键词')
    parser.add_argument('--verify-native', help=argparse.SUPPRESS)
    parser.add_argument('--legacy-tool', help=argparse.SUPPRESS)
    parser.add_argument('--legacy-context', help=argparse.SUPPRESS)
    parser.add_argument('--foreground-hwnd', type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument('--verify-legacy', help=argparse.SUPPRESS)
    parser.add_argument('--open-path', help=argparse.SUPPRESS)
    parser.add_argument('--open-line', type=int, default=1, help=argparse.SUPPRESS)
    parser.add_argument('--refresh', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--finish-restore', help=argparse.SUPPRESS)
    parser.add_argument('--wait-pid', type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument('--action', choices=['package-software', 'backup', 'restore'], help=argparse.SUPPRESS)
    parser.add_argument('legacy_arguments', nargs='*', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.verify_native and not args.data_dir:
        parser.error('验收必须提供独立的 --data-dir')
    folder = locate_data(args.data_dir)
    if args.finish_restore:
        from qt_frontend.maintenance import finish_restore
        return finish_restore(folder, args.finish_restore, args.wait_pid)
    if args.legacy_tool:
        from qt_frontend.legacy_bridge import run_tool
        if args.verify_legacy and not args.data_dir:
            parser.error('兼容工具验收必须提供独立资料库')
        return run_tool(args.legacy_tool, folder, args.foreground_hwnd,
                        args.legacy_context, args.verify_legacy)
    if not args.search and args.legacy_arguments:
        import ipc
        args.search = ipc.read_command(['jingzai', *args.legacy_arguments])
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName('JingzaiSearchQt')
    app.setOrganizationName('Jingzai')
    app.setFont(QFont('Microsoft YaHei UI', 10))
    app.setStyle('Fusion')
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('JingzaiSearch.Qt.v2')
    key = 'JingzaiQt-' + hashlib.sha256(str(folder).casefold().encode('utf-8')).hexdigest()[:20]
    socket = QLocalSocket()
    socket.connectToServer(key)
    if socket.waitForConnected(300):
        socket.write(json.dumps({'search': args.search or '', 'path': args.open_path or '',
                                 'line': args.open_line, 'refresh': args.refresh, 'action': args.action},
                                ensure_ascii=False).encode('utf-8'))
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()
        return 0
    store = Store(folder)
    if args.verify_native:
        store.settings['qt_startup_refresh'] = False
    log_dir = store.root / 'logs'
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(filename=str(log_dir / 'qt-desktop.log'), encoding='utf-8', level=logging.ERROR)
    window = Window(store)
    from qt_frontend.qt_display import DialogDisplayPolicy
    window.dialog_display = DialogDisplayPolicy(app, store)
    actions = {'package-software': window.package_software, 'backup': window.backup_library,
               'restore': window.restore_in_place}
    server = QLocalServer(window)
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen(key):
        QLocalServer.removeServer(key)
        if not server.listen(key):
            QMessageBox.critical(None, '无法启动', '新版窗口已在启动中，请稍后再试。')
            return 1

    def connection():
        peer = server.nextPendingConnection()
        if peer is None:
            return
        def read():
            raw = bytes(peer.readAll())
            try:
                command = json.loads(raw[:16000].decode('utf-8'))
            except (ValueError, UnicodeError):
                command = {}
            window.restore_window()
            if command.get('search'):
                window.use_query(str(command['search'])[:600])
            if command.get('path'):
                window.navigate('search')
                window.open_preview(str(command['path']), max(1, int(command.get('line', 1))))
            if command.get('refresh'):
                window.refresh_current()
            if command.get('action') in actions:
                QTimer.singleShot(0, actions[command['action']])
            peer.disconnectFromServer()
        if peer.bytesAvailable():
            read()
        else:
            peer.readyRead.connect(read)
        peer.disconnected.connect(peer.deleteLater)
    server.newConnection.connect(connection)
    app.aboutToQuit.connect(server.close)
    window.hotkeys = Hotkeys(app, window.quick_search, window.notify)
    window.clip_hotkeys = Hotkeys(app, window.clipboard_save, window.notify, identifier=0x4A5B)
    if not args.verify_native:
        QTimer.singleShot(350, lambda: window.hotkeys.configure(store.settings.get('quick_hotkey', 'ctrl+alt+j')))
        QTimer.singleShot(400, lambda: window.clip_hotkeys.configure(store.settings.get('clip_hotkey', 'ctrl+alt+m')))

    def exception_hook(kind, exception, trace):
        logging.error('GUI callback failed', exc_info=(kind, exception, trace))
        window.notify('操作未完成，详情已记录到日志')
    sys.excepthook = exception_hook
    window.show()
    if args.action:
        QTimer.singleShot(100, actions[args.action])
    if args.search:
        QTimer.singleShot(100, lambda: window.use_query(args.search))
    if args.open_path:
        QTimer.singleShot(100, lambda: window.open_preview(args.open_path, args.open_line))
    if args.verify_native:
        from qt_frontend.acceptance import Acceptance
        window.acceptance = Acceptance(window, Path(args.verify_native))
        QTimer.singleShot(350, window.acceptance.start)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())

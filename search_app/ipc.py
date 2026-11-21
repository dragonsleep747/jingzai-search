# -*- coding: utf-8 -*-
"""命令行参数 / 单实例传参 / jingzai:// URL 协议（批2 T6）。

- read_command：解析 /s|--search 与 jingzai://search?kw=xxx（URL 解码，≤200字）
- 单实例：Windows 命名互斥（Local 会话级）+ 127.0.0.1 临时端口转发（端口写文件）
- 协议注册：HKCU\\Software\\Classes\\jingzai（无需管理员）
仅标准库；winreg/ctypes 失败时调用方负责降级。
"""
import os
import sys
import time
import socket
import socketserver
import threading
import urllib.parse

try:
    import winreg
except ImportError:
    winreg = None

MAX_KW_LEN = 200
_MUTEX_NAME = 'Local\\JingzaiSearch_SingleInstance_v1'
_PORT_FILE = '.ipc_port'
_ERROR_ALREADY_EXISTS = 183

try:
    import ctypes as _ctypes
    k32 = _ctypes.WinDLL('kernel32', use_last_error=True)
except Exception:
    k32 = None


# ---------- 命令行 / URL 解析 ----------

def parse_protocol_url(text):
    """jingzai://search?kw=差旅 → '差旅'；不合规返回 None"""
    try:
        u = urllib.parse.urlparse(text.strip())
        if u.scheme.lower() != 'jingzai':
            return None
        q = urllib.parse.parse_qs(u.query).get('kw', [''])[0]
        q = urllib.parse.unquote(q).strip()
        return q or None
    except Exception:
        return None


def read_command(argv):
    """从 argv 提取搜索词（后出现的显式参数覆盖前面的）：/s 词 | --search=词 | jingzai://链接。返回 str|None"""
    kw = None
    args = list(argv[1:])
    i = 0
    while i < len(args):
        a = args[i]
        low = a.lower()
        if low in ('/s', '-s', '--search') and i + 1 < len(args):
            kw = args[i + 1]
            i += 2
            continue
        if low.startswith(('/s=', '-s=', '--search=')):
            kw = a.split('=', 1)[1]
            i += 1
            continue
        if low.startswith('jingzai:'):
            kw = parse_protocol_url(a)
        i += 1
    if kw:
        kw = urllib.parse.unquote(kw).strip()[:MAX_KW_LEN]
    return kw or None


# ---------- 单实例 ----------

def acquire_mutex():
    """创建命名互斥。返回 True=首个实例；False=已有实例在运行；
    非 Windows / 无 ctypes 时返回 True（放行，不阻断启动）。
    句柄不关闭，随进程存活。"""
    if not sys.platform.startswith('win'):
        return True
    if k32 is None:
        return True
    try:
        import ctypes
        # use_last_error + 模块级同一 loader 实例，跨调用才能读到 183
        k32.CreateMutexW(None, False, _MUTEX_NAME)
        return ctypes.get_last_error() != _ERROR_ALREADY_EXISTS
    except Exception:
        return True


def _port_file_path(app_dir):
    return os.path.join(app_dir, _PORT_FILE)


def forward_to_primary(app_dir, keyword, timeout=2.0):
    """第二实例：读端口文件 → 连接主实例 → 发送搜索词。
    主实例可能尚在启动，重试到 timeout。成功 True。"""
    if not keyword:
        return False
    pf = _port_file_path(app_dir)
    deadline = time.time() + timeout
    port = None
    while time.time() < deadline:
        try:
            with open(pf, 'r', encoding='utf-8') as f:
                port = int(f.read().strip())
            break
        except Exception:
            time.sleep(0.1)
    if not port:
        return False
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1.0) as s:
            s.sendall(keyword.encode('utf-8')[:600] + b'\n')
        return True
    except Exception:
        return False


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            data = self.rfile.readline(600).decode('utf-8', 'ignore').strip()
        except Exception:
            return
        kw = urllib.parse.unquote(data)[:MAX_KW_LEN].strip()
        if kw == '__quit__':
            # 收到退出指令：让主实例自行关闭
            try:
                self.server.on_quit()
            except Exception:
                pass
            return
        if kw:
            self.server.on_keyword(kw)
        try:
            self.wfile.write(b'ok')
        except Exception:
            pass


class IPCServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, app_dir, on_keyword, on_quit=None):
        self.on_keyword = on_keyword
        self.on_quit = on_quit or (lambda: None)
        super().__init__(('127.0.0.1', 0), _Handler)
        self.app_dir = app_dir
        with open(_port_file_path(app_dir), 'w', encoding='utf-8') as f:
            f.write(str(self.server_address[1]))

    def serve_forever_daemon(self):
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def shutdown_clean(self):
        try:
            self.shutdown()
            self.server_close()
        except Exception:
            pass
        try:
            os.remove(_port_file_path(self.app_dir))
        except Exception:
            pass


# ---------- URL 协议注册表 ----------

_PROTO_KEY = r'Software\Classes\jingzai'


def _delete_key_tree(root, path):
    try:
        k = winreg.OpenKey(root, path, 0,
                           winreg.KEY_READ | winreg.KEY_WRITE)
    except OSError:
        return
    while True:
        try:
            sub = winreg.EnumKey(k, 0)
        except OSError:
            break
        _delete_key_tree(root, path + '\\' + sub)
    k.Close()
    winreg.DeleteKey(root, path)


def is_protocol_registered():
    """返回 (是否已注册, 注册表里的 exe 路径)"""
    if not winreg:
        return False, ''
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           _PROTO_KEY + r'\shell\open\command',
                           0, winreg.KEY_READ)
        val, _ = winreg.QueryValueEx(k, '')
        k.Close()
        # 值形如 "C:\...\镜仔搜索.exe" "%1"
        exe = val.split('"')[1] if '"' in val else val
        return True, exe
    except OSError:
        return False, ''


def register_protocol(exe_path):
    """写 HKCU 注册 jingzai:// 协议，无需管理员。"""
    k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, _PROTO_KEY)
    winreg.SetValueEx(k, '', 0, winreg.REG_SZ, 'URL:镜仔搜索协议')
    winreg.SetValueEx(k, 'URL Protocol', 0, winreg.REG_SZ, '')
    k.Close()
    cmd_key = _PROTO_KEY + r'\shell\open\command'
    k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, cmd_key)
    winreg.SetValueEx(k, '', 0, winreg.REG_SZ, f'"{exe_path}" "%1"')
    k.Close()


def unregister_protocol():
    if not winreg:
        return
    _delete_key_tree(winreg.HKEY_CURRENT_USER, _PROTO_KEY)

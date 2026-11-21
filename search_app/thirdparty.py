# -*- coding: utf-8 -*-
"""可选第三方依赖的加载与降级标志。

集中管理原先散在 main.py 顶部的 try/import 降级块：每个可选模块 import
失败时置 None + 对应 _XXX_OK 标志为 False，业务代码据此优雅降级。
main.py 与 ui_dialogs.py 都从这里引用，保证拿到同一份模块对象与标志。
"""
# 全局热键迷你窗（pywin32 缺失时优雅降级）
try:
    import quickwin
    _HOTKEY_OK = quickwin._WIN32_OK
except Exception:
    quickwin = None
    _HOTKEY_OK = False
# 轻量动效工具（纯标准库；加载失败时所有动效自动降级为静态）
try:
    import anim
except Exception:
    anim = None
# Windows 窗口镀铬层（Win11 圆角/深色标题栏/深色原生菜单；非 Win 自动空操作）
try:
    import winchrome
except Exception:
    winchrome = None
# 联网检索制度 / 法规 / 标准
try:
    import websearch
except Exception:
    websearch = None
# 系统托盘（仅 Windows；ctypes 失败时优雅降级）
try:
    import tray as _tray_mod
    _TRAY_OK = _tray_mod._TRAY_OK
except Exception:
    _tray_mod = None
    _TRAY_OK = False
# Ollama 离线问答窗（纯标准库模块，失败不影响主程序）
try:
    import qa_win
    _QA_OK = True
except Exception:
    qa_win = None
    _QA_OK = False
# 引用关系图（纯标准库）
try:
    import graph_html
    _GRAPH_OK = True
except Exception:
    graph_html = None
    _GRAPH_OK = False
# DnD：tkinterdnd2 提供拖拽支持；不可用时回退到 tk.Tk
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _DND_AVAILABLE = True
except ImportError:
    _DND_AVAILABLE = False
# sv-ttk：Windows 11 Fluent 风格主题；不可用时保持默认主题
try:
    import sv_ttk
    _SVTTK = True
except ImportError:
    _SVTTK = False

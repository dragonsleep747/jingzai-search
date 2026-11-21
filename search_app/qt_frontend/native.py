"""仅提供全局热键；窗口绘制、动画和命中测试由 Qt / Windows 管理。"""
import ctypes
from ctypes import wintypes
import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Qt, Signal


def parse_hotkey(text):
    modifiers = 0x4000  # MOD_NOREPEAT
    values = {'ctrl': 2, 'control': 2, 'alt': 1, 'shift': 4, 'win': 8}
    tokens = [token.strip().lower() for token in text.split('+') if token.strip()]
    if not tokens:
        raise ValueError('热键不能为空')
    for token in tokens[:-1]:
        if token not in values:
            raise ValueError('不支持的热键修饰键：' + token)
        modifiers |= values[token]
    key = tokens[-1]
    if len(key) == 1 and key.isascii() and key.isalnum():
        virtual_key = ord(key.upper())
    elif key.startswith('f') and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        virtual_key = 0x70 + int(key[1:]) - 1
    elif key == 'space':
        virtual_key = 32
    else:
        raise ValueError('不支持的热键按键：' + key)
    return modifiers, virtual_key


class HotkeySignals(QObject):
    activated = Signal()


class Hotkeys(QAbstractNativeEventFilter):
    identifier = 0x4A5A

    def __init__(self, application, callback, notify, identifier=None):
        super().__init__()
        self.application, self.notify = application, notify
        if identifier is not None:
            self.identifier = identifier
        self.signals = HotkeySignals(application)
        self.signals.activated.connect(callback, Qt.ConnectionType.QueuedConnection)
        self.registered = False
        application.installNativeEventFilter(self)
        application.aboutToQuit.connect(self.close)

    def configure(self, text):
        self.close()
        if sys.platform != 'win32':
            return False
        try:
            mods, key = parse_hotkey(text)
        except ValueError as exc:
            self.notify(str(exc))
            return False
        native = ctypes.windll.user32
        native.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
        self.registered = bool(native.RegisterHotKey(None, self.identifier, mods, key))
        if not self.registered:
            self.notify(f'热键 {text} 已被其他程序占用，可在设置中更换')
        return self.registered

    def close(self):
        if self.registered:
            ctypes.windll.user32.UnregisterHotKey(None, self.identifier)
            self.registered = False

    def nativeEventFilter(self, event_type, message):
        if sys.platform == 'win32':
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0312 and msg.wParam == self.identifier:
                self.signals.activated.emit()
                return True, 0
        return False, 0

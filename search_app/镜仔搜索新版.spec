# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
from PySide6.QtCore import QLibraryInfo
from pathlib import Path

datas = [('app_icon.ico', '.'), ('app_icon.png', '.'),
         ('../docs/LEGACY_USER_GUIDE.txt', 'docs')]
datas += [('qt_frontend/assets', 'qt_frontend/assets')]
# SumatraPDF (GPLv3) is optional and is NOT distributed with the source.
if Path('SumatraPDF.exe').exists():
    datas += [('SumatraPDF.exe', '.')]
datas += [(str(Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))/'qtbase_zh_CN.qm'), 'translations')]
binaries = []
hiddenimports = ['pythoncom', 'pywintypes', 'win32api', 'win32com.client']
for package in ('win32com', 'rapidocr_onnxruntime', 'onnxruntime', 'tkinterdnd2', 'sv_ttk'):
    package_datas, package_binaries, package_imports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports

a = Analysis(
    ['qt_main.py'], pathex=[], binaries=binaries, datas=datas,
    hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['sitecustomize', 'lib2to3',
              'torch', 'torchvision', 'torchaudio', 'triton', 'matplotlib',
              'numba', 'llvmlite', 'scipy', 'IPython', 'onnxruntime.quantization',
              'onnxruntime.transformers', 'onnxruntime.tools', 'onnxruntime.training',
              'PyQt5', 'PyQt6', 'PySide2', 'PySide6.QtWebEngineCore',
              'PySide6.QtWebEngineWidgets', 'PySide6.QtQml', 'PySide6.QtQuick'],
    noarchive=False, optimize=0,
)
# Qt 6.12 使用 Windows 自带的 ICU（无版本后缀导出）。构建宿主的 PATH
# 包含 Poppler，其同名 ICU 78 DLL 的导出带 _78 后缀；打进去会令 QtCore
# 在启动时报告 WinError 127。让系统解析自己的 ICU，避免同名库污染。
a.binaries = [entry for entry in a.binaries
              if entry[0].casefold() not in ('icuuc.dll', 'icudt78.dll')]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='镜仔搜索新版',
    icon='app_icon.ico', console=False, debug=False, strip=False, upx=False,
    manifest='qt_frontend/windows.manifest',
    bootloader_ignore_signals=False, disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='镜仔搜索新版')

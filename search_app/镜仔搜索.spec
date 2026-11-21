# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []
tmp_ret = collect_all('tkinterdnd2')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('sv_ttk')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('win32com')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
hiddenimports += ['pythoncom', 'pywintypes', 'win32api', 'win32com.client']
hiddenimports += ['qrcode']   # 手机扫码配对二维码
# 批3 T10：RapidOCR（含 3 个内置 ONNX 中文模型与 config.yaml）+ onnxruntime
tmp_ret = collect_all('rapidocr_onnxruntime')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('onnxruntime')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
hiddenimports += ['cv2', 'yaml', 'pyclipper', 'shapely', 'PIL',
                  'onnxruntime.capi.onnxruntime_pybind11_state']
datas += [('app_icon.ico', '.'), ('app_icon.png', '.')]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # torch 必须排除：collect_all('onnxruntime') 会连带
    # onnxruntime.transformers.*（Whisper/SD/LLaMA 示例），它们 import torch，
    # 本机装的是 CUDA 版 torch（含 cudnn/cublas 等 ~4.5GB），
    # 不排除会把 exe 撑到 2.9GB。本程序只用 onnxruntime 的 InferenceSession。
    excludes=['tkinter.test', 'lib2to3',
              'torch', 'torchvision', 'torchaudio', 'triton',
              'onnxruntime.transformers', 'onnxruntime.tools',
              'onnxruntime.training'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# 启动画面：高清静态品牌图（960x600 PNG），主窗口就绪后由 pyi_splash.close() 关闭
splash = Splash('splash.png', binaries=a.binaries, datas=a.datas,
                always_on_top=True, max_img_size=(960, 600))

exe = EXE(
    pyz,
    a.scripts,
    splash,
    a.binaries,
    a.datas,
    [],
    name='镜仔搜索',
    icon='app_icon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # 大体积原生 DLL 不做 UPX：防杀软误报、防压缩后加载崩溃
    upx_exclude=['onnxruntime.dll', 'onnxruntime_providers*.dll',
                 'opencv_world*.dll', '*.pyi'],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

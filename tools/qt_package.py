"""构建入口：系统 WMI 不响应时，使用 Python 自带的 Windows 版本回退。"""
import platform
import os
from pathlib import Path


def _skip_wmi(*args):
    raise OSError('Packaging uses native Windows version APIs instead of WMI')


if hasattr(platform, '_wmi_query'):
    platform._wmi_query = _skip_wmi

support = str(Path(__file__).parent / 'qt_build_support')
os.environ['PYTHONPATH'] = support + os.pathsep + os.environ.get('PYTHONPATH', '')

from PyInstaller.__main__ import run

if __name__ == '__main__':
    run()

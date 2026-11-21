"""仅通过构建进程的 PYTHONPATH 加载，让隔离的依赖探测也避开 WMI。"""
import platform


def _skip_wmi(*args):
    raise OSError('Use native Windows version fallback during packaging')


if hasattr(platform, '_wmi_query'):
    platform._wmi_query = _skip_wmi

"""完整资料包及退出后的原位还原；只切换明确允许的数据文件。"""
import ctypes
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile

import indexer

DATA_NAMES = ('index.db', 'index.db-wal', 'index.db-shm', 'library', 'ocr_cache',
              'inbox', 'config.json', 'config.qt.json', 'category_undo.json')


def package_complete(store, target, progress, stop, software=None):
    target = Path(target).resolve()
    descriptor, name = tempfile.mkstemp(prefix='jingzai-package-', suffix='.zip', dir=target.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        store.save()
        result = indexer.package_library(str(temporary), db_path=store.db, app_dir=str(store.root),
            stop_event=stop, progress=lambda done, total, name: progress(
                {'current': done, 'total': total, 'path': name}))
        if result.get('skipped'):
            raise RuntimeError('部分文件无法读取，未发布不完整资料包：' + ', '.join(result['skipped']))
        with zipfile.ZipFile(temporary, 'a', zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
            archive.write(store.config_path, 'config.qt.json')
            undo = store.root / 'category_undo.json'
            if undo.is_file():
                archive.write(undo, undo.name)
            if software:
                bundle = Path(software).resolve()
                if not (bundle / '_internal').is_dir():
                    raise ValueError('软件包必须包含完整的 _internal 运行目录')
                members = [path for path in bundle.rglob('*') if path.is_file() and not path.is_symlink()
                           and path.resolve() not in (temporary, target)
                           and path.relative_to(bundle).parts[0] not in
                           (*DATA_NAMES, 'backup', 'logs', 'dist', 'build')]
                for number, path in enumerate(members, 1):
                    if stop.is_set():
                        raise InterruptedError('打包已取消')
                    archive.write(path, 'software/' + path.relative_to(bundle).as_posix())
                    progress({'current': number, 'total': len(members), 'path': path.name})
                archive.writestr('打开说明.txt', '解压全部文件后，打开 software/镜仔搜索新版.exe。\n'
                    'index.db 与 library 放在 software 的上一级，新版会自动识别。\n')
        with zipfile.ZipFile(temporary) as archive:
            bad = archive.testzip()
            if bad:
                raise RuntimeError('压缩包校验失败：' + bad)
        os.replace(temporary, target)
        return dict(result, zip_path=str(target), zip_size=target.stat().st_size)
    finally:
        temporary.unlink(missing_ok=True)


def validate_staging(root, staging):
    root, staging = Path(root).resolve(), Path(staging).resolve()
    if staging.parent != root or not staging.name.startswith('_restore_staging_'):
        raise ValueError('还原暂存目录必须是资料库内的独立暂存目录')
    if staging.is_symlink() or not (staging / 'library').is_dir() or not (staging / 'index.db').is_file():
        raise ValueError('还原暂存资料不完整')
    for name in DATA_NAMES:
        candidate = staging / name
        if candidate.is_symlink() or candidate.resolve().parent != staging:
            raise ValueError('还原数据路径越界')
    with closing(sqlite3.connect(f'{(staging / "index.db").as_uri()}?mode=ro', uri=True)) as conn:
        if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('还原索引校验失败')
    return root, staging


def switch_staging(root, staging):
    """调用者必须已经退出；失败时把已移动的数据回滚。保留旧资料备份。"""
    root, staging = validate_staging(root, staging)
    backup = root / 'backup' / ('restore_' + time.strftime('%Y%m%d_%H%M%S') + '_' + str(time.time_ns() % 1000000))
    backup.mkdir(parents=True, exist_ok=False)
    incoming = [name for name in DATA_NAMES if (staging / name).exists()]
    # WAL/SHM 属于旧索引，不能留给新索引。其他未打包设置保留。
    replacing = set(incoming) | {'index.db-wal', 'index.db-shm'}
    previous, installed = [], []
    try:
        for name in DATA_NAMES:
            source = root / name
            if name in replacing and source.exists():
                if source.is_symlink() or source.resolve().parent != root:
                    raise ValueError('现有资料路径越界，未执行切换')
                source.rename(backup / name)
                previous.append(name)
        for name in incoming:
            (staging / name).rename(root / name)
            installed.append(name)
        (backup / 'restore-result.json').write_text(json.dumps(
            {'restored_from': str(staging), 'installed': installed}, ensure_ascii=False), encoding='utf-8')
        return str(backup)
    except Exception:
        for name in reversed(installed):
            (root / name).rename(staging / name)
        for name in reversed(previous):
            (backup / name).rename(root / name)
        raise


def finish_restore(root, staging, parent_pid):
    if parent_pid and os.name == 'nt':
        from ctypes import wintypes
        api = ctypes.windll.kernel32
        api.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        api.OpenProcess.restype = wintypes.HANDLE
        handle = api.OpenProcess(0x00100000, False, parent_pid)
        if handle:
            try:
                api.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
                if api.WaitForSingleObject(handle, 120000) != 0:
                    raise RuntimeError('主程序尚未退出，未切换资料')
            finally:
                api.CloseHandle.argtypes = (wintypes.HANDLE,)
                api.CloseHandle(handle)
    try:
        switch_staging(root, staging)
    except Exception as error:
        if os.name == 'nt':
            ctypes.windll.user32.MessageBoxW(None, '还原未完成，已保留原资料。\n' + str(error), '镜仔搜索', 0x10)
    from .legacy_bridge import launcher_arguments
    subprocess.Popen(launcher_arguments('--data-dir', str(root)),
                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return 0

"""原生入库预检和执行；复用原索引器，替换前保留旧文件并检查变更。"""
from dataclasses import dataclass, asdict
from pathlib import Path
import json
import os
import shutil
import tempfile
import time
import uuid

import indexer
from service import snapshot_db

SUPPORTED = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.md',
             '.csv', '.json', '.log', '.xml', '.html', '.htm', '.zip', '.rar', '.7z',
             '.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.webp', '.py', '.js',
             '.css', '.ini', '.yaml', '.yml', '.sql', '.c', '.cpp', '.h'}
LABELS = {'new': '新文件', 'reindex': '更新库内文件', 'dup_skip': '内容重复',
          'conflict': '同名、内容不同', 'archive': '压缩包 · 逐成员入库',
          'new_link': '登记原路径', 'linked': '已登记 · 更新索引', 'error': '无法读取'}
CHOICES = {'copy': '复制并自动分类', 'keep_both': '保留两份', 'replace': '替换库内同名文件',
           'skip': '跳过', 'link': '登记原路径', 'reindex': '更新索引', 'unpack': '解包并分类'}


@dataclass
class Plan:
    source: str
    size: int = 0
    kind: str = 'new'
    target: str = ''
    sha: str = ''
    target_sha: str = ''
    checked: bool = True
    choice: str = 'copy'
    result: str = ''

    def choices(self):
        if self.kind == 'error':
            return ['skip']
        if self.kind == 'conflict':
            return ['keep_both', 'replace', 'skip']
        if self.kind == 'dup_skip':
            return ['skip', 'keep_both']
        original = {'archive': 'unpack', 'reindex': 'reindex', 'linked': 'link',
                    'new_link': 'link'}.get(self.kind, 'copy')
        return [original, 'skip']


def collect_sources(paths, stop):
    seen, sources = set(), []
    ignored = set(indexer.IGNORE_DIRS) | {'.venv-qt', '_internal', 'backup', 'ocr_cache'}
    for name in paths:
        path = Path(name)
        if path.is_dir():
            candidates = []
            for directory, children, files in os.walk(path):
                if stop.is_set():
                    break
                children[:] = [child for child in children if child.lower() not in ignored]
                candidates.extend(Path(directory) / file for file in files if Path(file).suffix.lower() in SUPPORTED)
        else:
            candidates = [path]
        for candidate in candidates:
            key = os.path.normcase(str(candidate.resolve()))
            if key not in seen:
                sources.append(str(candidate.resolve()))
                seen.add(key)
        if stop.is_set():
            break
    return sources


def analyze(store, paths, link_only, progress, stop):
    sources = collect_sources(paths, stop)
    plans = []
    library = (store.root / 'library').resolve()
    for number, source in enumerate(sources, 1):
        if stop.is_set():
            break
        row = Plan(source)
        try:
            row.size = Path(source).stat().st_size
            if link_only:
                row.kind = 'linked' if indexer.is_registered(store.db, source) else 'new_link'
                row.choice, row.target = 'link', source
            elif Path(source).resolve().is_relative_to(library):
                row.kind, row.choice, row.target = 'reindex', 'reindex', source
                row.sha = indexer._file_sha256(source)
            elif Path(source).suffix.lower() in indexer.ARCHIVE_EXTS:
                row.kind, row.choice = 'archive', 'unpack'
            else:
                row.kind, row.target, row.sha = indexer.plan_import(source, store.db)
                row.choice = {'conflict': 'keep_both', 'dup_skip': 'skip', 'reindex': 'reindex'}.get(row.kind, 'copy')
                row.checked = row.kind != 'dup_skip'
                if row.kind == 'conflict':
                    row.target_sha = indexer._file_sha256(row.target)
        except Exception as error:
            row.kind, row.choice, row.checked, row.result = 'error', 'skip', False, str(error)
        plans.append(row)
        progress({'current': number, 'total': len(sources), 'path': source, 'status': LABELS[row.kind]})
    return plans


def _replace(store, row):
    destination = Path(row.target).resolve()
    if not destination.is_relative_to((store.root / 'library').resolve()):
        raise RuntimeError('替换目标不在资料库内')
    if not destination.is_file() or indexer._file_sha256(str(destination)) != row.target_sha:
        raise RuntimeError('库内文件在预检后发生变化，请重新预检')
    backup = store.root / 'backup' / 'replaced_files' / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
    backup.mkdir(parents=True)
    saved = backup / destination.name
    shutil.copy2(destination, saved)
    temporary = destination.with_name(destination.name + '.import-' + uuid.uuid4().hex)
    old_meta = indexer.get_metadata(store.db, str(destination))
    installed = False
    try:
        shutil.copy2(row.source, temporary)
        os.replace(temporary, destination)
        installed = True
        status = indexer.index_single_file(str(destination), store.db, parse_timeout=120)
        if status not in ('ok', 'skip'):
            raise RuntimeError(status)
        return str(destination), status, str(saved)
    except Exception:
        if installed:
            shutil.copy2(saved, temporary)
            os.replace(temporary, destination)
            indexer.index_single_file(str(destination), store.db, parse_timeout=120)
            indexer.update_metadata(store.db, str(destination), **{key: old_meta.get(key) for key in
                ('doc_no', 'effective_date', 'status', 'category')})
        raise
    finally:
        temporary.unlink(missing_ok=True)


def execute(store, plans, progress, stop):
    details = []
    selected = [row for row in plans if row.checked and row.choice != 'skip']
    def note(source, target, state, message=''):
        details.append({'source': source, 'path': target, 'state': state, 'detail': message})
    for row in plans:
        if not row.checked or row.choice == 'skip':
            note(row.source, row.target, '失败' if row.kind == 'error' else '跳过',
                 row.result or ('内容重复，默认跳过' if row.kind == 'dup_skip' else '未选择或选择跳过'))
    with store.write_lock:
        if any(row.choice == 'replace' for row in selected):
            if not snapshot_db(store.db, str(store.root / 'backup'), 'pre_import_replace_qt'):
                raise RuntimeError('无法创建替换前索引备份，本次未替换任何文件')
        for number, row in enumerate(selected, 1):
            if stop.is_set():
                break
            progress({'current': number - 1, 'total': len(selected), 'path': row.source, 'status': '正在处理'})
            try:
                if not Path(row.source).is_file():
                    raise FileNotFoundError('源文件已移动或删除')
                if row.sha and indexer._file_sha256(row.source) != row.sha:
                    raise RuntimeError('源文件在预检后发生变化，请重新预检')
                if row.choice == 'unpack':
                    with tempfile.TemporaryDirectory(prefix='jingzai-import-') as folder:
                        members = indexer._extract_archive(row.source, folder)
                        if not members:
                            raise RuntimeError('压缩包内没有可导入的文件')
                        before = len(details)
                        for member_number, (path, name) in enumerate(members, 1):
                            if stop.is_set():
                                break
                            progress({'current': number - 1, 'total': len(selected), 'path': row.source,
                                      'status': f'解包 {member_number}/{len(members)} · {name}'})
                            try:
                                if indexer._sha_in_library(store.db, indexer._file_sha256(path)):
                                    note(row.source, '', '跳过', name + '：内容重复')
                                    continue
                                target, status = indexer._import_one_file(path, store.db)
                                if status not in ('ok', 'skip', 'exists'):
                                    raise RuntimeError(status)
                                note(row.source, target, '成功', name)
                            except Exception as error:
                                note(row.source, '', '失败', name + '：' + str(error))
                        failures = sum(item['state'] == '失败' for item in details[before:])
                        row.result = f'已处理 {len(details) - before}/{len(members)} 个成员，失败 {failures}'
                else:
                    saved = ''
                    if row.choice == 'replace':
                        target, status, saved = _replace(store, row)
                    elif row.choice in ('link', 'reindex'):
                        target = row.source
                        status = indexer.link_file_to_library(row.source, store.db, parse_timeout=120)
                    else:
                        # 再检查一次，防止同一批任务、并行进程期间新增重复。
                        current, target, _ = indexer.plan_import(row.source, store.db)
                        if current == 'dup_skip' and row.choice != 'keep_both':
                            row.result = '已跳过内容重复'
                            note(row.source, target, '跳过', row.result)
                            continue
                        target, status, _ = indexer.import_file_to_library(row.source, store.db)
                    if status not in ('ok', 'skip', 'exists', 'dup'):
                        raise RuntimeError(status)
                    row.result = '已完成' + (' · 旧文件已备份' if saved else '')
                    note(row.source, target, '成功', ('旧文件备份：' + saved) if saved else CHOICES[row.choice])
            except Exception as error:
                row.result = '失败：' + str(error)
                note(row.source, '', '失败', str(error))
            progress({'current': number, 'total': len(selected), 'path': row.source, 'status': row.result})
    result = {key: sum(item['state'] == state for item in details)
              for key, state in [('ok', '成功'), ('failed', '失败'), ('skipped', '跳过')]}
    result.update(total=len(plans), selected=len(selected), stopped=stop.is_set(), details=details,
                  plans=[asdict(row) for row in plans])
    folder = store.root / 'logs'
    folder.mkdir(exist_ok=True)
    report = folder / ('import-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6] + '.json')
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    result['report'] = str(report)
    return result

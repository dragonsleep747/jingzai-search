"""新版的数据适配层，不导入 Tk 界面，不改写旧版配置。"""
from __future__ import annotations

import csv
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import threading
import time
import copy
from contextlib import contextmanager

import core
import indexer
import parsers
import searcher
from synonyms import expand_map
from urllib.parse import quote

STATUS = {'active': '有效', 'void': '已废止', 'replaced': '已替代'}


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def locate_data(explicit=None):
    if explicit:
        return Path(explicit).resolve()
    base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parents[2]
    for folder in (base, base.parent):
        if (folder / 'index.db').is_file():
            return folder
    previous = read_json(base / 'config.json').get('last_db_path', '')
    if previous and Path(previous).is_file():
        return Path(previous).parent
    return base


@contextmanager
def settings_lock(path):
    stream = open(path, 'a+b')
    locked = False
    try:
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        locked = True
        yield
    finally:
        stream.seek(0)
        if locked and os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        elif locked:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


class Store:
    def __init__(self, data_dir=None):
        self.root = locate_data(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = str(self.root / 'index.db')
        self.config_path = self.root / 'config.qt.json'
        old = read_json(self.root / 'config.json')
        # 兼容完整旧版偏好，不能只挑少量字段而丢掉问答、备份和存档设置。
        self.settings = dict(old)
        self.settings.update(read_json(self.config_path))
        if self.settings.get('qt_feature_schema', 0) < 2:
            self.settings['copy_import'] = not bool(old.get('link_only', False))
            self.settings['qt_feature_schema'] = 2
        self.settings.setdefault('dark', False)
        self.settings.setdefault('recent_keywords', [])
        self.settings.setdefault('reflow', True)
        self.write_lock = threading.RLock()
        indexer.LIBRARY_DIR = str(self.root / 'library')
        parsers._OCR_CACHE_DIR = str(self.root / 'ocr_cache')
        if not Path(self.db).exists():
            conn, _ = indexer.get_db(self.db)
            conn.close()
        self._saved_snapshot = copy.deepcopy(self.settings)

    def save(self):
        with settings_lock(str(self.config_path) + '.lock'):
            merged = copy.deepcopy(self._saved_snapshot)
            merged.update(read_json(self.config_path))
            for key, value in self.settings.items():
                if key not in self._saved_snapshot or value != self._saved_snapshot[key]:
                    merged[key] = value
            for key in self._saved_snapshot.keys() - self.settings.keys():
                merged.pop(key, None)
            temporary = self.config_path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temporary, self.config_path)
            self.settings.clear()
            self.settings.update(merged)
            self._saved_snapshot = copy.deepcopy(merged)

    def automatic_backup(self, progress, stop):
        keep = max(0, min(10, int(self.settings.get('max_backups', 3))))
        if not keep:
            return None
        folder = self.root / 'backup'
        folder.mkdir(exist_ok=True)
        older = sorted(folder.glob('index_*.db'))
        wal = Path(self.db + '-wal')
        try:
            pending_changes = wal.exists() and wal.stat().st_size > 0
        except OSError:
            pending_changes = True
        if older and Path(self.db).stat().st_mtime <= older[-1].stat().st_mtime and not pending_changes:
            return str(older[-1])
        target = folder / time.strftime('index_%Y%m%d_%H%M%S.db')
        if target.exists():
            target = folder / f'index_{time.strftime("%Y%m%d_%H%M%S")}_{time.time_ns() % 1000000}.db'
        self.backup(target, progress, stop)
        for old in sorted(folder.glob('index_*.db'))[:-keep]:
            old.unlink()
        return str(target)

    def search(self, query, fuzzy=False, file_type=None, active=True, synonyms=True, stop=None, case_sensitive=False):
        started = time.perf_counter()
        syn = expand_map(core.kw_terms(query), self.settings.get('custom_synonyms', '')) if synonyms else None
        fn = searcher.search_fuzzy if fuzzy else searcher.search
        options = dict(file_type=file_type, only_active=active,
                       syn_map=syn, cancel=stop.is_set if stop is not None else None)
        if not fuzzy:
            options['case_sensitive'] = bool(case_sensitive)
        results = fn(self.db, query, **options)
        details = {row['path']: row for row in self.library()}
        return {'query': query, 'groups': searcher.aggregate_results(results),
                'hits': len(results), 'elapsed': time.perf_counter() - started, 'synonyms': syn or {}, 'details': details}

    def facets(self):
        rows = self.library()
        from collections import Counter
        categories = Counter(row['category'] for row in rows if row.get('category'))
        years = Counter(row['effective_date'][:4] for row in rows
                        if str(row.get('effective_date', ''))[:4].isdigit())
        return {'分类': sorted(categories.items()), '日期': sorted(years.items(), reverse=True)}

    def pdf_page_line(self, path, page):
        conn = sqlite3.connect(self.db, timeout=15)
        try:
            row = conn.execute('SELECT l.line_no FROM lines l JOIN documents d ON d.id=l.doc_id '
                'WHERE d.path=? AND l.line_text LIKE ? ORDER BY l.line_no LIMIT 1',
                (path, f'[Page {page}]%')).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def library(self):
        return indexer.list_library(self.db)

    def statistics(self):
        conn = sqlite3.connect(self.db, timeout=15)
        try:
            count, size, last = conn.execute(
                'SELECT COUNT(*), COALESCE(SUM(size),0), MAX(indexed_at) FROM documents').fetchone()
            errors = conn.execute('SELECT COUNT(*) FROM errors').fetchone()[0]
            return {'files': count, 'size': size, 'last': last, 'errors': errors}
        finally:
            conn.close()

    def preview(self, path, line, mode='整段', query='', chapter_end=None):
        line = max(1, int(line))
        meta = indexer.get_metadata(self.db, path)
        if mode == '章节':
            rows = searcher.get_line_range(self.db, path, line, chapter_end or line)
        elif mode == '上下文':
            rows = searcher.get_context(self.db, path, line, before=15, after=25)
        elif mode == '全文':
            rows = searcher.get_line_range(self.db, path, max(1, line - 200), line + 1200)
        else:
            rows = searcher.get_paragraph(self.db, path, line)
        references = {}
        reference_locations = {}
        for number, text in rows:
            for title, clause in searcher.find_refs_in_text(str(text)):
                if clause in reference_locations.get(title,{}):
                    continue
                target = references.get(title,{}).get('path') or searcher.resolve_ref(self.db, title)
                if target:
                    location = {'path': target,
                        'line': searcher.find_clause_line(self.db, target, clause) or 1,
                        'status': indexer.get_metadata(self.db, target).get('status', 'active')}
                    references.setdefault(title,location)
                    reference_locations.setdefault(title,{})[clause]=location
        synonyms = expand_map(core.kw_terms(query), self.settings.get('custom_synonyms', '')) if self.settings.get('synonym', True) else {}
        return {'path': path, 'line': line, 'rows': rows, 'meta': meta,
                'page': int(searcher.extract_page(searcher.get_line_text(self.db, path, line)) or 0) or None,
                'chapters': searcher.list_chapters(self.db, path),
                'chapter': searcher.get_chapter_chain_str(self.db, path, line),
                'bookmarked': bool(indexer.is_bookmarked(self.db, path, line)),
                'exists': Path(path).exists(), 'query': query, 'mode': mode,
                'references': references, 'reference_locations':reference_locations,
                'synonym_terms': [term for values in synonyms.values() for term in values],
                'reflow': self.settings.get('reflow', True)}

    def index_files(self, files, progress, stop, copy=False):
        with self.write_lock:
            if copy:
                result = {'ok': 0, 'failed': 0, 'total': len(files), 'skipped_detail': []}
                for number, path in enumerate(files, 1):
                    if stop.is_set():
                        result['stopped'] = True
                        break
                    action, _, _ = indexer.plan_import(path, self.db)
                    if action == 'dup_skip':
                        progress({'current': number, 'total': len(files), 'path': path, 'status': '内容重复，已跳过'})
                        continue
                    imported = indexer.import_file_to_library(path, self.db)
                    status = imported[1]
                    success = status in ('ok', 'exists', 'dup', 'skip') or status.startswith(('ok', 'archive:'))
                    result['ok' if success else 'failed'] += 1
                    if not success:
                        result['skipped_detail'].append((path, status))
                    progress({'current': number, 'total': len(files), 'path': path, 'status': status})
                return result
            return indexer.index_batch_files(
                files, self.db, stop_event=stop,
                ui_cb=lambda i, total, label, status, path: progress(
                    {'current': i, 'total': total, 'path': path, 'status': status}))

    def scan(self, folder, progress, stop):
        files = []
        ignored = set(indexer.IGNORE_DIRS) | {'.venv-qt', '_internal'}
        for directory, children, names in os.walk(folder):
            children[:] = [name for name in children if name.lower() not in ignored]
            for name in names:
                path = Path(directory) / name
                if path.resolve() == Path(self.db).resolve() or name.startswith('index.db-'):
                    continue
                files.append(str(path))
            if stop.is_set():
                return {'stopped': True, 'total': len(files), 'processed': 0}
        # 扫描与自动监听一致，登记目录中的原文件；拖入/添加采用完整复制入库流程。
        return self.index_files(files, progress, stop, copy=False)

    def write(self, fn, *args, **kwargs):
        with self.write_lock:
            return fn(self.db, *args, **kwargs)

    def backup(self, target, progress, stop):
        src = sqlite3.connect(f'{Path(self.db).as_uri()}?mode=ro', uri=True, timeout=30)
        dst = sqlite3.connect(str(target))
        try:
            def copied(status, remaining, total):
                if stop.is_set():
                    raise InterruptedError('备份已取消')
                progress({'current': total - remaining, 'total': total, 'status': '正在备份'})
            src.backup(dst, pages=256, progress=copied)
        finally:
            dst.close()
            src.close()
        return {'file': str(target)}


def preview_html(payload, dark=False, font_size=12):
    fg, muted, bg, hit = ('#e5eaf1', '#8c99ab', '#1b2533', '#725a27') if dark else (
        '#243449', '#8793a3', '#ffffff', '#fff0b3')
    exact = {term.casefold() for term in core.kw_terms(payload.get('query', ''))}
    terms = sorted(set(core.kw_terms(payload.get('query', ''))) | set(payload.get('synonym_terms', [])), key=len, reverse=True)
    pattern = re.compile('|'.join(re.escape(term) for term in terms if term), re.I) if terms else None

    def highlighted(text):
        if not pattern:
            return html.escape(text)
        parts, position = [], 0
        for match in pattern.finditer(text):
            parts.append(html.escape(text[position:match.start()]))
            color = hit if match.group().casefold() in exact else '#ffd19a'
            parts.append(f'<span style="background-color:{color};">{html.escape(match.group())}</span>')
            position = match.end()
        return ''.join(parts) + html.escape(text[position:])

    def linked(text):
        cursor, fragments = 0, []
        for match in re.finditer(r'《([^》]+)》(?:\s*(第[一二三四五六七八九十百千零0-9０-９]+[条章节项部分篇编点]))?|https?://[^\s<>《》]+', text):
            fragments.append(highlighted(text[cursor:match.start()]))
            value = match.group()
            if match.group(1) and match.group(1) in payload.get('references', {}):
                target = payload.get('reference_locations',{}).get(match.group(1),{}).get(match.group(2) or '') or payload['references'][match.group(1)]
                color = '#c13a28' if target.get('status') in ('void', 'replaced') else '#386ea8'
                url = 'jingzai-preview:?path=' + quote(target['path'], safe='') + '&line=' + str(target['line'])
                tip='单击查看引用；Ctrl+单击打开源文件。当前文件的自引用直接打开源文件。\n'+target['path']+f" · L{target['line']}"
                fragments.append(f'<a href="{html.escape(url, quote=True)}" title="{html.escape(tip,quote=True)}" style="color:{color};">{highlighted(value)}</a>')
            elif value.startswith(('http://', 'https://')):
                fragments.append(f'<a href="{html.escape(value, quote=True)}">{highlighted(value)}</a>')
            else:
                fragments.append(highlighted(value))
            cursor = match.end()
        return ''.join(fragments) + highlighted(text[cursor:])

    rows = []
    content = reflow_rows(payload['rows']) if payload.get('reflow') else [(number, text, [number]) for number, text in payload['rows']]
    for number, text, aliases in content:
        border = 'font-weight:600;' if payload['line'] in aliases else ''
        if any(alias in payload.get('fuzzy_lines', []) for alias in aliases):
            border += 'color:#c05621;'
        anchors = ''.join(f'<a name="L{alias}"></a>' for alias in aliases)
        rows.append(f'<tr><td width="38" align="right" style="color:{muted};font-size:9pt;padding:7px 15px 7px 0;" '
                    f'valign="top">{anchors}{number}</td>'
                    f'<td align="left" style="padding:7px 0;{border}">{linked(str(text)) or "&nbsp;"}</td></tr>')
    if not rows:
        rows = ['<tr><td>该位置暂无可预览正文，请打开原文件查看。</td></tr>']
    return (f'<html><body style="font-family:\'Microsoft YaHei UI\';color:{fg};background:{bg};'
            f'font-size:{int(font_size)}pt;margin:26px;">'
            '<table width="100%" cellspacing="0" cellpadding="0">' + ''.join(rows) + '</table></body></html>')


def reflow_rows(rows):
    """合并硬断行，同时保留每个原始行号的锚点；标题、表格、分页不合并。"""
    from service import is_table_row
    heading = re.compile(r'^\s*(?:第.+[条章节篇]|\d+(?:\.\d+)*[.、\s]|[（(][一二三四五六七八九十0-9]+[）)])')
    output = []
    for number, text in rows:
        text = str(text or '')
        if output:
            previous = output[-1][1]
            prev_page, this_page = searcher.extract_page(previous), searcher.extract_page(text)
            body = searcher._PAGE_RE.sub('', text, count=1)
            prior = searcher._PAGE_RE.sub('', previous, count=1).rstrip()
            join = (body.strip() and len(prior) >= 20 and prev_page == this_page
                    and prior[-1] not in '。；！？：”」』…）)]%'
                    and not heading.match(prior) and not heading.match(body)
                    and not re.search(r'[.·．]{3,}\s*\d*\s*$', prior)
                    and not is_table_row(prior) and not is_table_row(body))
            if join:
                output[-1] = (output[-1][0], previous + body.rstrip(), output[-1][2] + [number])
                continue
        output.append((number, text, [number]))
    return output


def export_catalog_csv(target, rows, stop):
    import tempfile
    fd,temporary=tempfile.mkstemp(prefix='.jingzai-export-',suffix='.csv',dir=str(Path(target).resolve().parent))
    try:
        with os.fdopen(fd,'w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.writer(stream)
            writer.writerow(['名称','路径','命中依据','大小(字节)','修改时间(ns)','正文命中数','位置在线'])
            for row in rows:
                if stop.is_set():
                    return {'stopped':True}
                writer.writerow([row.get('name',''),row['path'],
                    '正文' if row.get('kind')=='content' else '目录' if row.get('kind')=='dir' else '名称/路径',
                    row.get('size',''),row.get('mtime_ns',''),row.get('hits',''),row.get('online',True)])
        os.replace(temporary,target)
        return {'exported':len(rows)}
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def export_csv(target, groups):
    with open(target, 'w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['文件', '路径', '行号', '命中内容', '状态'])
        for group in groups:
            for match in group['matches']:
                writer.writerow([group['name'], group['path'], match['line_no'],
                                 match['line_text'], STATUS.get(group.get('status'), '')])

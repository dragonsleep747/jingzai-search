"""原文件定位：保留旧版 Word/WPS、PDF、Excel、PPT 和文本打开行为。"""
import os
from pathlib import Path
import shutil
import subprocess

from core import kw_terms
from searcher import extract_page, get_line_text
from service import find_acrobat, find_sumatra, parse_excel_loc


def _office(kind, path, keywords=(), sheet='', cell='', page=None):
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return False
    pythoncom.CoInitialize()
    application = document = None
    try:
        programs = {'word': ('Word.Application', 'KWPS.Application'),
                    'excel': ('Excel.Application', 'ket.Application', 'KWET.Application'),
                    'ppt': ('PowerPoint.Application', 'KWPP.Application')}
        for program in programs[kind]:
            try:
                application = win32com.client.DispatchEx(program)
                break
            except Exception:
                continue
        if application is None:
            return False
        application.Visible = True
        if kind == 'word':
            document = application.Documents.Open(os.path.abspath(path), ReadOnly=True)
            if keywords:
                for kw in keywords:
                    find = document.Content.Find
                    find.ClearFormatting()
                    find.Replacement.ClearFormatting()
                    find.Replacement.Highlight = True
                    find.Text, find.Forward, find.Wrap = kw, True, 1
                    find.Execute(Replace=2)
                selection = application.Selection
                selection.HomeKey(Unit=6)
                selection.Find.ClearFormatting()
                selection.Find.Text = keywords[0]
                selection.Find.Forward, selection.Find.Wrap = True, 1
                selection.Find.Execute()
                document.Saved = True
        elif kind == 'excel':
            document = application.Workbooks.Open(os.path.abspath(path), ReadOnly=True)
            try:
                worksheet = document.Worksheets(sheet)
            except Exception:
                worksheet = document.ActiveSheet
            worksheet.Activate()
            if cell:
                worksheet.Range(cell).Select()
            if keywords:
                for kw in keywords:
                    found = worksheet.UsedRange.Find(kw)
                    if found:
                        found.Select()
                        break
        else:
            document = application.Presentations.Open(os.path.abspath(path))
            if page:
                try:
                    application.ActiveWindow.View.GotoSlide(int(page))
                except Exception:
                    document.Slides(int(page)).Select()
        return True
    except Exception:
        if document is not None:
            try:
                document.Close(False) if kind == 'word' else document.Close()
            except Exception:
                pass
        if application is not None:
            try:
                application.Quit()
            except Exception:
                pass
        return False
    finally:
        document = application = None
        pythoncom.CoUninitialize()


def open_located(db, path, line=1, query='', app_dir=None):
    if not Path(path).is_file():
        raise FileNotFoundError('原文件不存在或所在磁盘未连接：' + path)
    path = os.path.abspath(path)
    terms = kw_terms(query)
    keyword = ' '.join(terms)
    text = get_line_text(db, path, int(line)) if line else ''
    page = extract_page(text)
    extension = Path(path).suffix.lower()
    if extension in ('.doc', '.docx'):
        if terms and _office('word', path, terms):
            return '已打开 Word/WPS，并高亮命中词'
    elif extension in ('.xls', '.xlsx'):
        location = parse_excel_loc(text)
        if location and _office('excel', path, terms, sheet=location[0], cell=location[1]):
            return f'已定位到 {location[0]}!{location[1]}' + ('并高亮命中词' if terms else '')
    elif extension in ('.ppt', '.pptx'):
        if page and _office('ppt', path, page=page):
            return f'已定位到第 {page} 张幻灯片'
    elif extension == '.pdf':
        root = str(app_dir or Path(db).parent)
        sumatra = find_sumatra(root, str(Path(root) / 'library'))
        if sumatra:
            args = [sumatra]
            if page:
                args += ['-page', str(page)]
            if keyword:
                args += ['-search', keyword]
            subprocess.Popen(args + [path])
            return f'已打开 PDF' + (f'第 {page} 页' if page else '') + ('并搜索命中词' if keyword else '')
        acrobat = find_acrobat()
        if page and acrobat:
            subprocess.Popen([acrobat, '/A', f'page={page}&search={keyword}', path])
            return f'已定位到 PDF 第 {page} 页'
        if page:
            for edge in (r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
                         r'C:\Program Files\Microsoft\Edge\Application\msedge.exe'):
                if Path(edge).is_file():
                    subprocess.Popen([edge, '--new-window', Path(path).as_uri() + f'#page={page}'])
                    return f'已定位到 PDF 第 {page} 页'
    elif extension in ('.txt', '.md', '.csv', '.json', '.log', '.xml', '.html', '.ini', '.py', '.js', '.css'):
        editor = shutil.which('notepad++') or shutil.which('code')
        if editor and 'notepad++' in editor.lower():
            subprocess.Popen([editor, f'-n{line}', path])
            return f'已定位到第 {line} 行'
        if editor:
            subprocess.Popen([editor, '--goto', f'{path}:{line}'])
            return f'已定位到第 {line} 行'
    try:
        os.startfile(path)
    except OSError:
        return '未能打开原文件：该文件类型没有关联的默认打开程序'
    return '已用默认程序打开原文件（未定位到具体位置）'

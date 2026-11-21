"""On-demand, bounded previews. Never indexes or copies the source into the library."""
import html
import codecs
import os
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImageReader


def load_preview(path, stop):
    suffix = Path(path).suffix.lower()
    if stop.is_set():
        return {}
    if suffix in {'.png','.jpg','.jpeg','.webp','.bmp','.tif','.tiff','.gif'}:
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and max(size.width(),size.height())>2400:
            reader.setScaledSize(size.scaled(QSize(2400, 2400), Qt.AspectRatioMode.KeepAspectRatio))
        image = reader.read()
        return {'image': image} if not image.isNull() else {'error': reader.errorString()}
    text_types = {'.txt','.md','.csv','.log','.json','.xml','.yaml','.yml','.py','.ini','.rst','.sql','.html','.htm','.js','.ts','.css'}
    lines = None
    if suffix in text_types:
        with open(path, 'rb') as stream:
            data = stream.read(1024 * 1024 + 1)
        truncated = len(data) > 1024 * 1024
        data = data[:1024 * 1024]
        encodings = ('utf-16',) if data.startswith((b'\xff\xfe', b'\xfe\xff')) else ('utf-8-sig', 'gb18030')
        for encoding in encodings:
            try:
                # A size cap may split a character. Buffer only that trailing
                # fragment instead of misdetecting the entire file's encoding.
                text = codecs.getincrementaldecoder(encoding)().decode(data, final=not truncated)
                break
            except UnicodeError:
                text = data.decode('utf-8', errors='replace')
        all_lines = text.splitlines()
        lines = all_lines[:2000]
        truncated = truncated or len(all_lines) > 2000
    elif suffix in {'.docx','.xlsx','.pptx'} and os.path.getsize(path) <= 32 * 1024 * 1024:
        import zipfile
        with zipfile.ZipFile(path) as archive:
            if sum(item.file_size for item in archive.infolist()) > 64 * 1024 * 1024:
                return {'error':'文件解压后过大，请打开原文件阅读'}
        import parsers
        parser = {'.docx':parsers.parse_docx,'.xlsx':parsers.parse_xlsx,'.pptx':parsers.parse_pptx}[suffix]
        lines = []
        truncated = False
        for line in parser(path):
            if stop.is_set():
                return {}
            if len(lines) >= 2000:
                truncated = True
                break
            lines.append(str(line[1] if isinstance(line,tuple) and len(line)>1 else line))
    if lines is None:
        return {}
    body = ''.join(f'<p style="margin:3px 0"><span style="color:#8793a3">{i}</span>　{html.escape(line)}</p>'
                   for i,line in enumerate(lines,1))
    return {'html': '<html><body>'+body+('</body></html>'), 'truncated':truncated}

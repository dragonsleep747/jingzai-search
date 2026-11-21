"""独立临时资料库中的功能与 Win32 动画验收，入口仅通过命令行启用。"""
import ctypes
from ctypes import wintypes
import json
import logging
from pathlib import Path
import sys
import threading
import time

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import QApplication


class Acceptance(QObject):
    def __init__(self, window, output):
        super().__init__(window)
        self.window, self.output = window, output
        self.window.store.settings['max_backups'] = 0
        output.mkdir(parents=True, exist_ok=True)
        self.checks, self.cycles, self.errors = [], [], []
        self.components = {}
        self.completed = False
        self.native = ctypes.windll.user32
        self.native.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
        self.native.GetWindowLongW.restype = ctypes.c_long
        self.native.ShowWindowAsync.argtypes = (wintypes.HWND, ctypes.c_int)
        self.native.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t)
        self.native.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        self.native.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        self.native.GetAncestor.restype = wintypes.HWND
        self.native.WindowFromPoint.argtypes = (wintypes.POINT,)
        self.native.WindowFromPoint.restype = wintypes.HWND
        self.hwnd = int(window.winId())
        self.stop = threading.Event()
        self.periodic = QTimer(self)
        self.periodic.setInterval(20)
        self.periodic.timeout.connect(self.sample)
        self.samples = []
        self.cycle_number = 0
        self.deadline = time.monotonic() + 75
        self.watchdog = QTimer(self)
        self.watchdog.timeout.connect(self.timeout)
        self.watchdog.start(500)

    def check(self, condition, name):
        if not condition:
            raise AssertionError(name)
        self.checks.append(name)

    def safe(self, fn):
        def invoke(*_):
            try:
                fn()
            except Exception as exc:
                self.errors.append(f'{type(exc).__name__}: {exc}')
                self.finish()
        return invoke

    def later(self, delay, fn):
        QTimer.singleShot(delay, self.safe(fn))

    def wait_for(self, condition, fn, attempts=150):
        def poll():
            if condition():
                self.safe(fn)()
            elif attempts <= 0:
                self.errors.append('等待界面状态超时')
                self.finish()
            else:
                self.wait_for(condition, fn, attempts - 1)
        self.later(40, poll)

    def start(self):
        first = self.window.store.root / '安全培训验收.txt'
        second = self.window.store.root / '差旅报销验收.txt'
        first.write_text('第一章 总则\n第一条 安全培训应当按期组织。\n第二条 安全员做好培训记录。\n', encoding='utf-8')
        second.write_text('第一章 费用\n第一条 出差后应及时办理差旅报销。\n', encoding='utf-8')
        def work(progress, stop):
            self.check_components()
            return self.window.store.index_files([str(first), str(second)], progress, stop)
        def failed(detail):
            self.errors.append(detail)
            self.finish()
        self.window.jobs.submit('验收资料', work, lambda _: self.loaded(), error=failed, visible=False, mutating=True)

    def check_components(self):
        from docx import Document
        from openpyxl import Workbook
        from pptx import Presentation
        from pptx.util import Inches
        from PIL import Image, ImageDraw, ImageFont
        import indexer
        fixtures = self.window.store.root / 'format-fixtures'
        fixtures.mkdir(exist_ok=True)
        doc = Document()
        doc.add_paragraph('JINGZAI DOCX 2026')
        doc.save(fixtures / 'sample.docx')
        book = Workbook()
        book.active['A1'] = 'JINGZAI XLSX 2026'
        book.save(fixtures / 'sample.xlsx')
        book.close()
        deck = Presentation()
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(2)).text = 'JINGZAI PPTX 2026'
        deck.save(fixtures / 'sample.pptx')
        stream = b'BT /F1 20 Tf 30 700 Td (JINGZAI PDF 2026) Tj ET'
        objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
            b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
            b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
        content, offsets = bytearray(b'%PDF-1.4\n'), [0]
        for number, obj in enumerate(objects, 1):
            offsets.append(len(content))
            content.extend(f'{number} 0 obj\n'.encode() + obj + b'\nendobj\n')
        xref = len(content)
        content.extend(b'xref\n0 6\n0000000000 65535 f \n')
        for offset in offsets[1:]:
            content.extend(f'{offset:010d} 00000 n \n'.encode())
        content.extend(f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode())
        (fixtures / 'sample.pdf').write_bytes(content)
        image = Image.new('RGB', (900, 180), 'white')
        font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 64)
        ImageDraw.Draw(image).text((35, 45), 'JINGZAI 2026', fill='black', font=font)
        image.save(fixtures / 'sample.png')
        for extension in ('docx', 'xlsx', 'pptx', 'pdf', 'png'):
            rows, encoding, kind = indexer.parse_file(str(fixtures / ('sample.' + extension)))
            text = '\n'.join(str(row[1]) for row in rows)
            self.check('2026' in text, extension.upper() + ' 解析组件可用')
            self.components[extension] = True

    def loaded(self):
        self.window.refresh_stats()
        self.check(not self.window.windowFlags() & Qt.WindowType.FramelessWindowHint, '使用系统窗口边框')
        self.check(not self.window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground), '主窗口不使用透明背景')
        self.check(not self.native.GetWindowLongW(self.hwnd, -20) & 0x80000, '主窗口无 WS_EX_LAYERED')
        style = self.native.GetWindowLongW(self.hwnd, -16)
        self.check(style & 0xC00000 and style & 0x40000 and style & 0x30000,
                   '保留系统标题栏、缩放和最小化最大化按钮')
        self.window.use_query('安全培训')
        self.wait_for(lambda: self.window.current is not None, self.searched)

    def searched(self):
        self.check(len(self.window.groups) == 1, '搜索结果正确')
        self.check('安全培训' in self.window.reader.toPlainText(), '正文预览包含命中内容')
        self.window.toggle_bookmark()
        self.wait_for(lambda: self.window.current and self.window.current['bookmarked'], self.bookmarked)

    def bookmarked(self):
        import indexer
        self.check(len(indexer.list_bookmarks(self.window.store.db)) == 1, '收藏沿用原有数据库')
        self.window.navigate('library')
        self.wait_for(lambda: self.window.library_model.rowCount() == 2, self.library_loaded)

    def library_loaded(self):
        self.check(self.window.library_model.rowCount() == 2, '资料库列表正确')
        self.window.navigate('search')
        self.window.resize(1380, 880)
        from .experience_acceptance import ExperienceChecks
        self.experience = ExperienceChecks(self, self.begin_cycle)
        self.experience.start()

    def rect(self):
        value = wintypes.RECT()
        if not self.native.GetWindowRect(self.hwnd, ctypes.byref(value)):
            raise RuntimeError('获取窗口矩形失败')
        return value.left, value.top, value.right, value.bottom

    def begin_cycle(self):
        number = self.cycle_number
        if number == 10 and not getattr(self, '_pdf_cycles', False):
            self._pdf_cycles = True
            self.window.use_query('SECOND')
            self.wait_for(lambda: self.window.reader_stack.currentIndex() == 1, self.begin_cycle)
            return
        if number in (0, 5, 10, 15):
            self.window.store.settings['dark'] = number in (5, 15)
            self.window.apply_theme()
        if number == 10:
            self.window.showMaximized()
        if number in (0, 5):
            self.window.resize(1380 if number == 0 else 1080, 840)
        self.later(160, self.minimize)

    def minimize(self):
        self.was_maximized = self.window.isMaximized()
        self.current_rect = self.rect()
        self.window.grab().save(str(self.output / f'window-{self.cycle_number}.png')) if self.cycle_number in (0, 5, 10, 15) else None
        self.native.PostMessageW(self.hwnd, 0x0112, 0xF020, 0)
        self.later(260, self.restore)

    def restore(self):
        self.check(bool(self.native.IsIconic(self.hwnd)), f'第 {self.cycle_number + 1} 次最小化')
        self.samples = []
        self.native.ShowWindowAsync(self.hwnd, 9)
        self.periodic.start()
        self.later(440, self.restored)

    def sample(self):
        try:
            from PIL import ImageGrab
            x0, y0, x1, y1 = self.current_rect
            x, y = int(x0 + (x1 - x0) * .81), int(y0 + (y1 - y0) * .70)
            window_at_point = self.native.WindowFromPoint(wintypes.POINT(x, y))
            if self.native.GetAncestor(window_at_point, 2) != self.hwnd:
                return  # 被其他窗口挡住的帧没有证据价值
            image = ImageGrab.grab(bbox=(x - 70, y - 45, x + 70, y + 45)).convert('RGB')
            pixels = image.resize((28, 18)).tobytes()
            black = sum(max(pixels[n:n + 3]) <= 8 for n in range(0, len(pixels), 3)) / (len(pixels) // 3)
            self.samples.append(black)
            if black > .4:
                image.save(self.output / f'black-frame-{self.cycle_number}.png')
        except Exception as exc:
            self.errors.append('屏幕采样失败：' + str(exc))
            self.periodic.stop()

    def restored(self):
        self.periodic.stop()
        self.check(not self.native.IsIconic(self.hwnd), f'第 {self.cycle_number + 1} 次还原')
        if self.window.reader_stack.currentIndex() == 1:
            from .experience_acceptance import page_has_visible_ink
            self.check(page_has_visible_ink(self.window.pdf_reader.view), 'PDF 恢复后原页文字可见')
        self.check(self.window.isMaximized() == self.was_maximized, '还原保留最大化状态')
        self.check(len(self.samples) >= 3, '有足够未遮挡屏幕帧')
        worst = max(self.samples)
        self.cycles.append({'cycle': self.cycle_number + 1, 'maximized': self.was_maximized,
                            'reader': 'pdf' if self.window.reader_stack.currentIndex() == 1 else 'text',
                            'dark': self.window.store.settings['dark'], 'frames': len(self.samples),
                            'worst_black_fraction': round(worst, 4)})
        self.check(worst < .4, '恢复过程没有大片纯黑')
        self.cycle_number += 1
        if self.cycle_number < 20:
            self.later(30, self.begin_cycle)
        else:
            self.window.showNormal()
            self.window.resize(1380, 880)
            self.window.store.settings['dark'] = False
            self.window.apply_theme()
            self.later(120, self.finish)

    def timeout(self):
        if time.monotonic() > self.deadline and not self.completed:
            self.errors.append('验收超过 75 秒')
            self.finish()

    def finish(self):
        if self.completed:
            return
        self.completed = True
        self.periodic.stop()
        self.watchdog.stop()
        self.window.grab().save(str(self.output / 'new-desktop.png'))
        report = {'ok': not self.errors and len(self.cycles) == 20, 'checks': self.checks, 'components': self.components,
                  'cycles': self.cycles, 'errors': self.errors, 'data_dir': str(self.window.store.root)}
        (self.output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        self.window._force_exit = True
        # Windowed EXEs can inherit an unusable redirected stdout handle. The
        # JSON file is the receipt; optional console output must not abort exit.
        if sys.stdout is not None:
            try:
                print(json.dumps(report, ensure_ascii=False), flush=True)
            except (OSError, ValueError, AttributeError):
                logging.warning('Native acceptance console output unavailable', exc_info=True)
        def close():
            if self.window.jobs.active:
                QTimer.singleShot(50, close)
            else:
                self.window.close()
                QApplication.instance().exit(0 if report['ok'] else 1)
        close()

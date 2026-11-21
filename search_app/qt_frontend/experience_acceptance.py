"""隔离样本中的原生界面流程验证；仅 --verify-native 入口使用。"""
from pathlib import Path

from PySide6.QtPdf import QPdfDocument
from PySide6.QtGui import QImage


def page_has_visible_ink(view):
    image = view.viewport().grab().toImage()
    if image.width() < 100 or image.height() < 100:
        return False
    region = image.copy(25, 15, image.width() - 50, min(330, image.height() - 30)).convertToFormat(QImage.Format.Format_RGB32)
    data = bytes(region.constBits())
    return sum(max(data[n:n + 3]) < 140 for n in range(0, len(data) - 3, 4)) > 30


def make_pdf(path):
    streams = [b'BT /F1 20 Tf 30 700 Td (JINGZAI FIRST PAGE 2026) Tj ET',
               b'BT /F1 20 Tf 30 700 Td (JINGZAI SECOND PAGE 2026) Tj ET']
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Length ' + str(len(streams[0])).encode() + b' >>\nstream\n' + streams[0] + b'\nendstream',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 7 0 R >>',
        b'<< /Length ' + str(len(streams[1])).encode() + b' >>\nstream\n' + streams[1] + b'\nendstream']
    content, offsets = bytearray(b'%PDF-1.4\n'), [0]
    for number, item in enumerate(objects, 1):
        offsets.append(len(content))
        content += f'{number} 0 obj\n'.encode() + item + b'\nendobj\n'
    xref = len(content)
    content += f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode()
    for offset in offsets[1:]:
        content += f'{offset:010d} 00000 n \n'.encode()
    content += f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()
    Path(path).write_bytes(content)


class ExperienceChecks:
    def __init__(self, acceptance, finished):
        self.a, self.w, self.finished = acceptance, acceptance.window, finished
        self.wait, self.check = acceptance.wait_for, acceptance.check

    def start(self):
        self.w.sync_semantic = lambda: None  # 本验收不依赖模型服务。
        self.source = self.w.store.root / 'native-import' / '原生入库验收.txt'
        self.source.parent.mkdir(exist_ok=True)
        self.source.write_text('原生导入独有内容 IMPORTCHECK2026。', encoding='utf-8')
        self.dialog = self.w.confirm_import([str(self.source), str(self.source)])
        self.wait(lambda: len(self.dialog.model.plans) == 1 and self.dialog.start_button.isEnabled(), self.preflight)

    def preflight(self):
        self.check(self.dialog.model.plans[0].kind == 'new', '原生导入去重预检，无提前入库')
        self.dialog.grab().save(str(self.a.output / 'native-import-preflight.png'))
        self.dialog.start()
        self.dialog.close()
        self.wait(lambda: self.dialog.result is not None, self.imported)

    def imported(self):
        self.check(self.dialog.result['ok'] == 1, '原生预检确认后成功入库')
        self.target = self.dialog.result['details'][0]['path']
        self.check(self.target != str(self.source) and Path(self.target).is_file(), '默认复制并分类归档')
        self.dialog.close()
        self.source.write_text('新版本修订内容 NEWIMPORT2026。', encoding='utf-8')
        self.dialog = self.w.confirm_import([str(self.source)])
        self.wait(lambda: len(self.dialog.model.plans) == 1 and self.dialog.start_button.isEnabled(), self.conflict)

    def conflict(self):
        row = self.dialog.model.plans[0]
        self.check(row.kind == 'conflict' and row.choice == 'keep_both', '同名不同内容明确显示冲突，默认保留两份')
        self.dialog.grab().save(str(self.a.output / 'native-import-conflict.png'))
        self.dialog.start()
        self.wait(lambda: self.dialog.result is not None, self.kept_both)

    def kept_both(self):
        self.check(self.dialog.result['ok'] == 1 and 'IMPORTCHECK' in Path(self.target).read_text(encoding='utf-8'),
                   '保留两份不会覆盖旧文件')
        self.dialog.close()
        self.link_source = self.source.with_name('原路径验收.txt')
        self.link_source.write_text('链接模式独有 LINKCHECK2026。', encoding='utf-8')
        self.dialog = self.w.confirm_import([str(self.link_source)])
        self.dialog.link_only.setChecked(True)
        self.wait(lambda: bool(self.dialog.model.plans) and self.dialog.model.plans[0].kind == 'new_link', self.link_plan)

    def link_plan(self):
        self.dialog.start()
        self.wait(lambda: self.dialog.result is not None, self.linked)

    def linked(self):
        self.check(self.dialog.result['details'][0]['path'] == str(self.link_source), '仅登记路径模式不复制文件')
        self.dialog.close()
        import indexer
        indexer.update_metadata(self.w.store.db, self.target, status='void', effective_date='2024-01-01')
        self.w.search_input.clear()
        combo = self.w.facet_combos['状态']
        combo.setCurrentIndex(combo.findData('废止'))
        self.wait(lambda: self.w.query == '状态:已废止' and bool(self.w.groups), self.filtered)

    def filtered(self):
        self.check(len(self.w.groups) == 1 and self.w.groups[0]['path'] == self.target, '状态下拉筛选支持无正文词查询')
        self.check(not self.w.active_filter.isChecked() and '废止' in self.w.options_button.toolTip(),
                   '折叠选项显示生效提示且废止查询不被有效过滤排除')
        self.w.grab().save(str(self.a.output / 'visual-filters.png'))
        self.w.clear_facets()
        self.w.search_debounce.stop()
        self.w.active_filter.setChecked(True)
        self.pdf_path = self.w.store.root / '原始页面验收.pdf'
        make_pdf(self.pdf_path)
        self.w.jobs.submit('PDF 阅读验收样本', lambda progress, stop: self.w.store.index_files(
            [str(self.pdf_path)], progress, stop), lambda _: self.w.use_query('SECOND'), error=self.a.safe(self.fail))
        self.wait(lambda: self.w.current and self.w.current['path'] == str(self.pdf_path)
                  and self.w.pdf_reader.document.status() == QPdfDocument.Status.Ready, self.pdf_ready)

    def fail(self):
        raise RuntimeError('PDF 样本入库失败')

    def pdf_ready(self):
        self.check(self.w.pdf_reader.page.value() == 2, 'PDF 原始页面自动定位命中页')
        self.w.pdf_reader.thumbnail_toggle.setChecked(True)
        self.wait(lambda: self.w.pdf_reader.thumbnail_pages == [0, 1] and not self.w.pdf_reader.requests, self.thumbnails)

    def thumbnails(self):
        self.check(not self.w.pdf_reader.thumbnails.item(0).icon().isNull(), 'PDF 缩略图后台渲染完成')
        self.wait(lambda: page_has_visible_ink(self.w.pdf_reader.view), self.pdf_rendered)

    def pdf_rendered(self):
        self.check(page_has_visible_ink(self.w.pdf_reader.view), '展开缩略图后 PDF 页面文字实际可见')
        self.w.grab().save(str(self.a.output / 'pdf-original.png'))
        from PySide6.QtWidgets import QApplication
        self.w.read_menu.popup(self.w.read_button.mapToGlobal(self.w.read_button.rect().bottomRight()))
        QApplication.processEvents()
        self.w.read_menu.grab().save(str(self.a.output / 'reading-operations.png'))
        self.w.read_menu.hide()
        self.check(self.w.pdf_reader.controls.isVisible() and self.w.pdf_reader.page.isVisible()
                   and self.w.pdf_reader.find.isVisible(), 'PDF 页码缩放与查找工具条直接可见')
        self.w.pdf_reader.controls.grab().save(str(self.a.output / 'pdf-controls.png'))
        self.w.pdf_reader.goto(0)
        self.wait(lambda: self.w.current.get('page') == 1, self.pdf_navigation)

    def pdf_navigation(self):
        self.w.reader_kind.setCurrentIndex(0)
        self.check(self.w.reader_stack.currentIndex() == 0 and 'FIRST PAGE' in self.w.reader.toPlainText(),
                   '原始 PDF 翻页后提取文字及引用位置同步')
        self.w.density.setCurrentIndex(1)
        self.w.grab().save(str(self.a.output / 'comfortable-reader.png'))
        self.w.density.setCurrentIndex(0)
        self.w.store.settings['pdf_original_preferred'] = True
        self.w.use_query('安全培训')
        self.wait(lambda: self.w.current and '安全培训' in self.w.current['path'], self.done)

    def done(self):
        self.w.resize(1380, 880)
        self.w.grab().save(str(self.a.output / 'compact-reader.png'))
        from .minimal_acceptance import check_minimal_layout
        result = check_minimal_layout(self.w, self.a.output / 'minimal-layout')
        for message in result['checks']:
            self.check(True, message)
        import json
        (self.a.output / 'minimal-layout' / 'result.json').write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        self.a.later(180, self.finished)

# -*- coding: utf-8 -*-
"""镜仔搜索 · 文件解析器测试（stdlib unittest）

覆盖：纯文本按行解析（utf-8/gbk）、Excel 列号换算、
docx/xlsx/pptx 解析往返（用本地库生成最小样本）。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parsers import (  # noqa: E402
    parse_text, _col_letter, parse_docx, parse_xlsx, parse_pptx,
)


class TestParseText(unittest.TestCase):
    def test_utf8_lines(self):
        fd, path = tempfile.mkstemp(suffix='.txt')
        # newline='' 关闭写端换行翻译，保证原始字节与预期一致
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
            f.write('第一行\n第二行\r\n\n第四行')
        try:
            rows = list(parse_text(path, 'utf-8'))
            self.assertEqual(rows, [(1, '第一行'), (2, '第二行'),
                                    (3, ''), (4, '第四行')])
        finally:
            os.remove(path)

    def test_gbk_lines(self):
        fd, path = tempfile.mkstemp(suffix='.txt')
        with os.fdopen(fd, 'w', encoding='gbk') as f:
            f.write('安全生产\n责任制')
        try:
            rows = list(parse_text(path, 'gbk'))
            self.assertEqual(rows, [(1, '安全生产'), (2, '责任制')])
        finally:
            os.remove(path)


class TestColLetter(unittest.TestCase):
    def test_col_letter(self):
        self.assertEqual(_col_letter(1), 'A')
        self.assertEqual(_col_letter(26), 'Z')
        self.assertEqual(_col_letter(27), 'AA')
        self.assertEqual(_col_letter(52), 'AZ')
        self.assertEqual(_col_letter(703), 'AAA')


class TestOfficeParsers(unittest.TestCase):
    """最小样本往返：写入 → parse_xxx → 校验关键内容与定位前缀"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='jz_test_')

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_docx(self):
        from docx import Document
        path = os.path.join(self.tmp, 'a.docx')
        doc = Document()
        doc.add_paragraph('第一章 总则')
        doc.add_paragraph('安全生产责任制')
        doc.save(path)
        texts = [t for _, t in parse_docx(path)]
        self.assertIn('第一章 总则', texts)
        self.assertIn('安全生产责任制', texts)

    def test_xlsx(self):
        from openpyxl import Workbook
        path = os.path.join(self.tmp, 'a.xlsx')
        wb = Workbook()
        ws = wb.active
        ws.title = 'Sheet1'
        ws.append(['编号', '名称'])
        ws.append(['A1', '安全生产'])
        wb.save(path)
        rows = list(parse_xlsx(path))
        joined = ' | '.join(t for _, t in rows)
        self.assertIn('[Sheet1!A1] 编号', joined)
        self.assertIn('[Sheet1!B2] 安全生产', joined)

    def test_pptx(self):
        from pptx import Presentation
        path = os.path.join(self.tmp, 'a.pptx')
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[5])
        slide.shapes.title.text = '检查要点'
        prs.save(path)
        rows = list(parse_pptx(path))
        self.assertTrue(any('检查要点' in t and t.startswith('[Slide 1]')
                            for _, t in rows))


if __name__ == '__main__':
    unittest.main(verbosity=2)

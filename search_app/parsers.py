# -*- coding: utf-8 -*-
"""
镜仔搜索 · 文件解析模块
Copyright (c) 2026 SleepDragon
SPDX-License-Identifier: MIT
"""

"""各格式文件解析器：统一返回 [(line_no, line_text), ...]"""
import os
import re
import sys
import hashlib
import subprocess

# Windows 上 monkey-patch subprocess，隐藏 OCR 子进程的控制台窗口
# pytesseract.get_tesseract_version / get_languages 和 pdf2image 版本检测
# 调用 tesseract.exe / pdftoppm.exe 时未设 startupinfo，导致黑色 CMD 闪现
if sys.platform.startswith('win'):
    _CREATE_NO_WINDOW = 0x08000000
    _orig_Popen = subprocess.Popen
    _orig_run = subprocess.run
    _orig_check_output = subprocess.check_output

    class _Popen(_orig_Popen):
        def __init__(self, *args, **kwargs):
            if 'creationflags' not in kwargs:
                kwargs['creationflags'] = _CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    def _run(*args, **kwargs):
        if 'creationflags' not in kwargs:
            kwargs['creationflags'] = _CREATE_NO_WINDOW
        return _orig_run(*args, **kwargs)

    def _check_output(*args, **kwargs):
        if 'creationflags' not in kwargs:
            kwargs['creationflags'] = _CREATE_NO_WINDOW
        return _orig_check_output(*args, **kwargs)

    subprocess.Popen = _Popen
    subprocess.run = _run
    subprocess.check_output = _check_output



def parse_text(path, encoding):
    """纯文本类，按行返回"""
    with open(path, 'r', encoding=encoding, errors='replace', newline='') as f:
        for i, line in enumerate(f, 1):
            yield i, line.rstrip('\r\n')


def parse_docx(path):
    """Word .docx：段落 + 表格行 + 页眉页脚，尽量保留原始内容"""
    from docx import Document
    doc = Document(path)
    n = 0
    # 1. 正文段落
    for p in doc.paragraphs:
        n += 1
        text = p.text
        if text and text.strip():
            yield n, text
    # 2. 表格（保留行列结构，用 | 分隔单元格，空单元格也保留位置）
    for tbl in doc.tables:
        for r in tbl.rows:
            n += 1
            cells = []
            for c in r.cells:
                ct = c.text.strip() if c.text else ''
                cells.append(ct)
            # 只在整行非空时输出
            if any(cells):
                yield n, ' | '.join(cells)
    # 3. 页眉页脚（补充正文可能遗漏的标题信息）
    for section in doc.sections:
        for hf in (section.header, section.footer,
                    section.first_page_header, section.first_page_footer):
            if hf is None:
                continue
            for p in hf.paragraphs:
                text = p.text.strip() if p.text else ''
                if text:
                    n += 1
                    yield n, text
            for tbl in hf.tables:
                for r in tbl.rows:
                    n += 1
                    cells = [c.text.strip() if c.text else '' for c in r.cells]
                    if any(cells):
                        yield n, ' | '.join(cells)


def _col_letter(n):
    """数字列号 → Excel 字母列号"""
    s = ''
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def parse_xlsx(path):
    """Excel：每非空行产出一条，含 [Sheet!Cell] 定位前缀"""
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    n = 0
    for sheet in wb.worksheets:
        title = sheet.title
        for row_idx, row in enumerate(sheet.iter_rows(values_only=True), 1):
            cells = []
            for col_idx, val in enumerate(row, 1):
                if val is None:
                    continue
                cells.append(f'[{title}!{_col_letter(col_idx)}{row_idx}] {val}')
            if cells:
                n += 1
                yield n, ' | '.join(cells)
    wb.close()


def parse_pptx(path):
    """PPT：每段落产出一条，含 [Slide N] 前缀"""
    from pptx import Presentation
    prs = Presentation(path)
    n = 0
    for slide_idx, slide in enumerate(prs.slides, 1):
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for para in shape.text_frame.paragraphs:
                if para.text:
                    n += 1
                    yield n, f'[Slide {slide_idx}] {para.text}'


def _join_pdf_chars(chars, x_tolerance=5):
    """同行字符按 x0 拼接，间隔过大处补空格（分隔表格列）"""
    chars = sorted(chars, key=lambda c: c['x0'])
    out, prev_x1 = [], None
    for c in chars:
        if prev_x1 is not None and c['x0'] - prev_x1 > x_tolerance:
            out.append(' ')
        out.append(c['text'])
        prev_x1 = c['x1']
    return ''.join(out).strip()


def _pdf_page_lines(page):
    """提取一页文本行（直接基于 chars 聚类）。

    不用 extract_text()：其内部 WordExtractor 会做书写方向检测，部分
    表格页把横排小字误判为竖排(ttb)，按 x0 聚类 → 每个字输出一行。
    这里：
    1. 过滤旋转字符（matrix 的 b/d 分量非零）——斜向水印（如
       “住建部公开信息”45° 大字）每页逐字定位，会污染为数千单字行；
    2. 过滤字号离群的水平大字（水平摆放水印兜底，阈值 2.5×中位字高）；
    3. 按 top 聚类(y_tolerance=2)，行内按 x0 排序。
    """
    chars = page.chars or []
    horiz = []
    for c in chars:
        m = c.get('matrix')
        if m and (abs(m[1]) > 0.02 or abs(m[2]) > 0.02):
            continue  # 旋转水印 / 竖排
        horiz.append(c)
    if not horiz:
        return []
    sizes = sorted(c['height'] for c in horiz)
    med = sizes[len(sizes) // 2]
    max_h = med * 5          # 水平大字水印兜底，放宽以免误杀章节号
    horiz = [c for c in horiz if c['height'] <= max_h]
    horiz.sort(key=lambda c: c['top'])
    groups, cur = [], []
    for c in horiz:
        if not cur or c['top'] - cur[0]['top'] <= 3:  # y_tolerance=3，对齐 pdfplumber 默认
            cur.append(c)
        else:
            groups.append(cur)
            cur = [c]
    if cur:
        groups.append(cur)
    return [ln for g in groups
            if (ln := _join_pdf_chars(g))]


_CJK_RE = re.compile(r'[\u4e00-\u9fff]')


def parse_pdf(path):
    """PDF：每非空行产出一条，含 [Page N] 前缀。
    P1-9: 当 pdfplumber 抽不到文本（扫描版 PDF）时，调用 OCR 兜底。

    预扫描策略（避免 200 页扫描 PDF OCR 跑 10 分钟只出 3 行）：
    - 前 3 页加起来 < 5 行文本 → 判定扫描件，后续所有页跳过 OCR
    - 前 3 页有文本但汉字 < 10（字体无 ToUnicode，提取出的全是
      !"#$% 字形符号）→ 文本层不可信，后续每页强制 OCR
    - 单页 OCR 超时 8s
    """
    import pdfplumber
    _OCR_TIMEOUT = 8
    _PRESCAN_PAGES = 3       # 预扫描前 N 页
    _PRESCAN_MIN_LINES = 5   # 前 N 页累计要有 ≥ 这多行才算可解析
    _PRESCAN_MIN_CJK = 10    # 前 N 页汉字少于此数 → 文本层乱码

    with pdfplumber.open(path) as pdf:
        pages = pdf.pages
        # ---- 预扫描前 N 页（先判定，再产出，避免乱码行提前流出）----
        pre_lines, pre_text = 0, []
        for page in pages[:_PRESCAN_PAGES]:
            ls = _pdf_page_lines(page)
            pre_lines += len(ls)
            pre_text.append('\n'.join(ls))
        if pre_lines < _PRESCAN_MIN_LINES:
            mode = 'scan'          # 扫描件：空页不 OCR
        else:
            pt = ''.join(pre_text)
            nt = len(pt.replace(' ', '').replace('\n', ''))
            mode = ('garbled' if nt > 50 and
                    len(_CJK_RE.findall(pt)) < _PRESCAN_MIN_CJK
                    else 'normal')  # garbled：假文本层，逐页强制 OCR

        n = 0
        for page_num, page in enumerate(pages, 1):
            page_lines = _pdf_page_lines(page)
            # 乱码文件忽略文本层；正常文件空页 OCR 兜底
            if mode == 'garbled' or (not page_lines and mode == 'normal'):
                try:
                    import concurrent.futures as _fut
                    with _fut.ThreadPoolExecutor(max_workers=1) as _ex:
                        _ocr_fut = _ex.submit(_ocr_pdf_page, path, page_num)
                        try:
                            ocr_text = _ocr_fut.result(_OCR_TIMEOUT)
                        except _fut.TimeoutError:
                            ocr_text = ''
                except Exception:
                    ocr_text = ''
                ocr_lines = [l.strip()
                             for l in ocr_text.split('\n') if l.strip()]
                if ocr_lines or mode == 'garbled':
                    page_lines = ocr_lines

            for line in page_lines:
                n += 1
                yield n, f'[Page {page_num}] {line}'


# ========== P1-9: OCR 兜底（图片 + 扫描版 PDF）==========

_TESS_EXE = None  # 缓存 tesseract.exe 路径


# ---------- OCR 结果缓存（按文件 SHA256+页码，重建索引免重复识别）----------
def _app_dir():
    """exe 模式数据放 exe 同目录，源码模式放脚本目录"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


_OCR_CACHE_DIR = os.path.join(_app_dir(), 'ocr_cache')
_SHA_MEMO = {}   # (path, mtime_ns, size) -> sha256，避免逐页重复读整文件


def _file_sha256(path):
    """文件内容哈希（进程内按 mtime/size 记忆化，整个文件只读一次）"""
    st = os.stat(path)
    key = (os.path.abspath(path).lower(), st.st_mtime_ns, st.st_size)
    v = _SHA_MEMO.get(key)
    if v:
        return v
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1048576), b''):
            h.update(chunk)
    v = h.hexdigest()
    if len(_SHA_MEMO) > 64:
        _SHA_MEMO.clear()
    _SHA_MEMO[key] = v
    return v


def _ocr_cache_get(key):
    try:
        with open(os.path.join(_OCR_CACHE_DIR, key + '.txt'),
                  'r', encoding='utf-8') as f:
            return f.read()
    except Exception:
        return None


def _ocr_cache_put(key, text):
    try:
        os.makedirs(_OCR_CACHE_DIR, exist_ok=True)
        with open(os.path.join(_OCR_CACHE_DIR, key + '.txt'),
                  'w', encoding='utf-8') as f:
            f.write(text)
    except Exception:
        pass


def _find_tesseract():
    """查找 tesseract.exe：PATH / 默认安装路径 / 环境变量"""
    global _TESS_EXE
    if _TESS_EXE is not None:
        return _TESS_EXE
    import shutil
    # 1. PATH
    p = shutil.which('tesseract')
    if p:
        _TESS_EXE = p
        return p
    # 2. 常见安装路径
    candidates = [
        r'C:\Program Files\Tesseract-OCR\tesseract.exe',
        r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
        os.path.expanduser(r'~\AppData\Local\Programs\Tesseract-OCR\tesseract.exe'),
    ]
    for c in candidates:
        if os.path.exists(c):
            _TESS_EXE = c
            return c
    # 3. TESSERACT_PREFIX 环境变量
    prefix = os.environ.get('TESSERACT_PREFIX')
    if prefix:
        c = os.path.join(prefix, 'tesseract.exe')
        if os.path.exists(c):
            _TESS_EXE = c
            return c
    return None


# RapidOCR 引擎单例（模型加载 1-2 秒，进程内只加载一次）
_RAPID_ENGINE = None


def ocr_status():
    """T10：供 ⚙ 菜单显示 OCR 引擎状态。
    返回 {rapid:bool, rapid_ver, models:[...], onnxruntime, providers,
         tesseract:path或None}"""
    info = {'rapid': False, 'rapid_ver': '', 'models': [],
            'onnxruntime': '', 'providers': [], 'tesseract': None}
    try:
        import importlib.metadata as md
        info['rapid_ver'] = md.version('rapidocr-onnxruntime')
    except Exception:
        pass
    try:
        import os
        import rapidocr_onnxruntime as _r
        mdir = os.path.join(os.path.dirname(_r.__file__), 'models')
        if os.path.isdir(mdir):
            info['models'] = sorted(f for f in os.listdir(mdir)
                                    if f.lower().endswith('.onnx'))
        import onnxruntime
        info['onnxruntime'] = onnxruntime.__version__
        info['providers'] = onnxruntime.get_available_providers()
        info['rapid'] = bool(info['models'])
    except Exception:
        pass
    try:
        info['tesseract'] = _find_tesseract()
    except Exception:
        info['tesseract'] = None
    return info


def _get_rapid():
    global _RAPID_ENGINE
    if _RAPID_ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _RAPID_ENGINE = RapidOCR()
    return _RAPID_ENGINE


def _rapid_ocr(img):
    """RapidOCR 识别 PIL.Image，返回按行拼接的文本"""
    import numpy as np
    engine = _get_rapid()
    arr = np.array(img.convert('RGB'))[:, :, ::-1]  # RGB → BGR(cv2)
    result, _ = engine(arr)
    if not result:
        return ''
    return '\n'.join(item[1] for item in result)


def _ocr_image(img, lang='chi_sim+eng'):
    """对 PIL.Image 执行 OCR，返回文本字符串。

    优先 RapidOCR（PaddleOCR ONNX 版，自带中英文模型，无需外部引擎），
    失败回退 pytesseract（需系统装 Tesseract-OCR）。
    """
    try:
        text = _rapid_ocr(img)
        if text and text.strip():
            return text
    except Exception:
        pass
    import pytesseract
    # 显式指定 tesseract.exe，避免 PATH 未生效时找不到
    exe = _find_tesseract()
    if exe:
        pytesseract.pytesseract.tesseract_cmd = exe
    else:
        raise RuntimeError('未找到 tesseract.exe，请安装 Tesseract-OCR')
    return pytesseract.image_to_string(img, lang=lang)


def _ocr_pdf_page(pdf_path, page_num, lang='chi_sim+eng'):
    """渲染 PDF 某一页为图片后 OCR（结果按 文件SHA256+页码 缓存）"""
    key = f'{_file_sha256(pdf_path)}_p{page_num}'
    cached = _ocr_cache_get(key)
    if cached is not None:
        return cached
    from pdf2image import convert_from_path
    # poppler 路径（自动查找）
    poppler_path = _find_poppler()
    kwargs = {'first_page': page_num, 'last_page': page_num, 'dpi': 200}
    if poppler_path:
        kwargs['poppler_path'] = poppler_path
    images = convert_from_path(pdf_path, **kwargs)
    if not images:
        return ''
    text = _ocr_image(images[0], lang=lang)
    _ocr_cache_put(key, text)
    return text


def _find_poppler():
    """查找 poppler bin 目录（含 pdftoppm.exe）"""
    import shutil
    p = shutil.which('pdftoppm')
    if p:
        return os.path.dirname(p)
    candidates = [
        os.path.expanduser(r'~\AppData\Local\Programs\poppler'),
        r'C:\poppler\Library\bin',
        r'C:\poppler\bin',
    ]
    for c in candidates:
        if os.path.exists(c):
            # 找 poppler-X.Y.Z\Library\bin
            for root, dirs, _ in os.walk(c):
                for d in dirs:
                    bin_dir = os.path.join(root, d, 'Library', 'bin')
                    if os.path.exists(os.path.join(bin_dir, 'pdftoppm.exe')):
                        return bin_dir
    return None


def parse_image(path):
    """图片文字识别（OCR）：.png/.jpg/.jpeg/.bmp/.tiff/.webp
    结果按文件 SHA256 缓存，重建索引时免重复识别"""
    from PIL import Image
    try:
        img = Image.open(path)
    except Exception as e:
        raise RuntimeError(f'打开图片失败: {e}')
    # 缓存命中：直接回放上次识别并清洗后的行
    key = _file_sha256(path)
    cached = _ocr_cache_get(key)
    if cached is not None:
        n = 0
        for line in cached.split('\n'):
            n += 1
            yield n, line
        return
    text = _ocr_image(img)
    out = []
    for line in text.split('\n'):
        line = line.rstrip()
        if not line.strip():
            continue
        # Tesseract 中文 OCR 会在每个字之间插空格，导致 trigram 索引搜不到
        # 策略：中文片段去掉字间空格；保留英文字间空格
        line = _clean_ocr_line(line)
        out.append(line)
    _ocr_cache_put(key, '\n'.join(out))
    for n, line in enumerate(out, 1):
        yield n, line


def _clean_ocr_line(line):
    """去掉 Tesseract 中文 OCR 输出的字间空格。
    规则：如果一行被空格分隔的 token 大部分是单中文字符，去掉所有空格。
    """
    if not line:
        return line
    tokens = line.split(' ')
    # 大部分 token 是单中文字符 → 去掉所有空格
    cn_tokens = [t for t in tokens if len(t) == 1 and '\u4e00' <= t <= '\u9fff']
    if len(cn_tokens) >= 2 and len(cn_tokens) >= len(tokens) - 1:
        # 全中文行：去掉所有空格
        return ''.join(tokens)
    # 混合行：只合并连续单中文字符间的空格
    import re
    # 合并 "中 中 中" 模式
    line = re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', line)
    return line


# ========== P1-8: 老 Office 二进制 (.doc/.xls/.ppt) ==========

def parse_doc(path):
    """
    .doc (Word 97-2003)：从 OLE 容器读 WordDocument 流，
    提取 Unicode/ANSI 文本片段。复杂格式（表格嵌套/域代码）会丢，
    主文本可拿到。
    """
    import olefile
    n = 0
    try:
        ole = olefile.OleFileIO(path)
        # WordDocument 流存放主文本
        if ole.exists('WordDocument'):
            data = ole.openstream('WordDocument').read()
            # Word 97-2003 文本部分通常是 UTF-16LE 或 cp1252
            # 简化策略：尝试 UTF-16LE 解码，失败则 cp1252
            text = _extract_text_from_doc_stream(data)
            for line in text.split('\n'):
                line = line.rstrip('\r')
                if line.strip():
                    n += 1
                    yield n, line
        ole.close()
    except Exception as e:
        raise RuntimeError(f'解析 .doc 失败: {e}')


def _extract_text_from_doc_stream(data):
    """
    WordDocument 流含 FIB 头 + 文本。
    改进策略：解析 FIB 结构定位文本起始位置，按 Unicode 段提取。
    保留中英文字符，跳过控制字符和二进制噪声。
    """
    import struct
    if len(data) < 512:
        return ''

    # FIB 结构：fcMin 字段在偏移 0x0018（4字节）指示文本起始
    # fcMac 字段在偏移 0x001C（4字节）指示文本结束
    # 但 Word 97+ 用 fcClx 指示 piece table 位置，复杂格式需解析 CLX

    # 简化但更准确：跳过 FIB 头（前 768 字节通常含结构信息）
    # 从文本区域开始扫描

    results = []

    # 方法1：尝试解析 FIB 获取文本范围
    try:
        # fcMin: 文本区起始偏移（FIB 偏移 0x18）
        fc_min = struct.unpack('<I', data[0x18:0x1C])[0]
        # lcb: 文本区长度相关（FIB 偏移 0x4C, ccpText）
        ccp_text = struct.unpack('<I', data[0x4C:0x50])[0]
        if 0 < fc_min < len(data) and ccp_text > 0:
            text_end = min(fc_min + ccp_text * 2, len(data))
            # Word 97+ 默认 Unicode (UTF-16LE)
            raw = data[fc_min:text_end]
            try:
                text = raw.decode('utf-16-le', errors='ignore')
                cleaned = ''.join(
                    c for c in text
                    if c.isprintable() or c in '\n\r\t')
                if len(cleaned) > 50:
                    # 按换行分割为段落，过滤空行
                    for line in cleaned.split('\r'):
                        line = line.strip()
                        if line:
                            results.append(line)
            except Exception:
                pass
    except Exception:
        pass

    # 方法2：回退到改进的全文扫描（按 UTF-16LE 段提取连续中文/英文）
    if not results or len(results) < 5:
        results = []
        # 逐字节扫描，提取 UTF-16LE 连续可解码段
        buf = bytearray()
        i = 0
        while i < len(data) - 1:
            # 取 2 字节看是否为可打印 Unicode
            code = data[i] | (data[i+1] << 8)
            ch = chr(code) if code < 0x10000 else ''
            if ch and (ch.isprintable() or ch in '\r\n\t'):
                buf.extend(data[i:i+2])
            else:
                if len(buf) >= 8:  # 至少 4 个字符才算有效段
                    try:
                        seg = buf.decode('utf-16-le', errors='ignore')
                        seg = ''.join(c for c in seg
                                      if c.isprintable() or c in '\r\n\t')
                        if seg.strip():
                            results.append(seg.strip())
                    except Exception:
                        pass
                buf = bytearray()
            i += 2
        if len(buf) >= 8:
            try:
                seg = buf.decode('utf-16-le', errors='ignore')
                seg = ''.join(c for c in seg
                              if c.isprintable() or c in '\r\n\t')
                if seg.strip():
                    results.append(seg.strip())
            except Exception:
                pass

    return '\n'.join(results) if results else ''


def parse_xls(path):
    """
    .xls (Excel 97-2003 BIFF8)：用 olefile 读 Workbook 流，
    逐条解析 SST（共享字符串表）记录取文本。
    BIFF8 复杂，这里只取字符串表，足够关键词搜索。
    """
    import olefile
    import struct
    n = 0
    strings = []
    try:
        ole = olefile.OleFileIO(path)
        if ole.exists('Workbook'):
            data = ole.openstream('Workbook').read()
            strings = _extract_sst_strings(data)
        ole.close()
    except Exception as e:
        raise RuntimeError(f'解析 .xls 失败: {e}')
    # 每个字符串作为一行
    for s in strings:
        if s and s.strip():
            n += 1
            yield n, s


def _extract_sst_strings(data):
    """
    从 BIFF8 Workbook 流提取 SST 记录中的字符串。
    记录头：2字节 type + 2字节 size + payload
    SST type = 0x00FC
    """
    import struct
    strings = []
    pos = 0
    while pos + 4 <= len(data):
        rec_type = struct.unpack_from('<H', data, pos)[0]
        rec_size = struct.unpack_from('<H', data, pos + 2)[0]
        pos += 4
        if pos + rec_size > len(data):
            break
        if rec_type == 0x00FC:  # SST
            # SST 头：4字节 numRefs + 4字节 numStrings
            if rec_size < 8:
                continue
            num_strings = struct.unpack_from('<I', data, pos + 4)[0]
            sst_pos = pos + 8
            end = pos + rec_size
            for _ in range(num_strings):
                if sst_pos + 3 > end:
                    break
                # 每条：2字节 length + 1字节 option
                length = struct.unpack_from('<H', data, sst_pos)[0]
                option = data[sst_pos + 2]
                sst_pos += 3
                is_unicode = (option & 0x01) != 0
                if is_unicode:
                    byte_len = length * 2
                    if sst_pos + byte_len > end:
                        break
                    try:
                        s = data[sst_pos:sst_pos + byte_len].decode(
                            'utf-16-le', errors='ignore')
                    except Exception:
                        s = ''
                    sst_pos += byte_len
                else:
                    if sst_pos + length > end:
                        break
                    try:
                        s = data[sst_pos:sst_pos + length].decode(
                            'cp1252', errors='ignore')
                    except Exception:
                        s = ''
                    sst_pos += length
                # 跳过 far east / phonetic 扩展（简化：不解析）
                strings.append(s)
            break
        pos += rec_size
    return strings


def parse_ppt(path):
    """
    .ppt (PowerPoint 97-2003)：从 OLE 容器读 PowerPoint Document 流，
    提取可见文本。实现复杂，简化：扫描 ANSI/Unicode 可打印段。
    """
    import olefile
    n = 0
    try:
        ole = olefile.OleFileIO(path)
        # PPT 主流叫 "PowerPoint Document"
        for stream_name in ('PowerPoint Document', 'Current User'):
            if ole.exists(stream_name):
                data = ole.openstream(stream_name).read()
                text = _extract_text_from_doc_stream(data)
                for line in text.split('\n'):
                    line = line.rstrip('\r')
                    if line.strip() and len(line.strip()) > 1:
                        n += 1
                        yield n, line
                break
        ole.close()
    except Exception as e:
        raise RuntimeError(f'解析 .ppt 失败: {e}')


# ========== P1-10: 压缩包内文件遍历 ==========

# 支持的压缩包扩展名 → 打开函数标识
ARCHIVE_EXTS = {'.zip', '.rar', '.7z'}


def _is_text_ext(name):
    """判断压缩包内文件是否为可解析文本/Office/PDF"""
    from detector import TEXT_EXTS
    ext = os.path.splitext(name)[1].lower()
    return ext in TEXT_EXTS or ext in ('.docx', '.xlsx', '.pptx', '.pdf')


def _parse_member(name, data):
    """
    给定压缩包内一个成员的文件名和二进制数据，调用对应解析器。
    返回 [(line_no, line_text), ...]
    """
    import io
    import tempfile
    ext = os.path.splitext(name)[1].lower()
    if ext in ('.docx', '.xlsx', '.pptx'):
        # Office 需要文件路径，写临时文件
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            if ext == '.docx':
                yield from parse_docx(tmp_path)
            elif ext == '.xlsx':
                yield from parse_xlsx(tmp_path)
            else:
                yield from parse_pptx(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    elif ext == '.pdf':
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            n = 0
            for page_num, page in enumerate(pdf.pages, 1):
                # 与普通 PDF 同一套提取（防竖排误判）
                for line in _pdf_page_lines(page):
                    n += 1
                    yield n, f'[Page {page_num}] {line}'
    else:
        # 纯文本：从 chardet 推断编码后按行读
        from detector import detect_encoding
        # chardet.detect 接受 bytes，无需文件
        import chardet
        result = chardet.detect(data[:65536]) or {}
        enc = (result.get('encoding') or 'utf-8').lower()
        if data.startswith(b'\xef\xbb\xbf'):
            enc = 'utf-8-sig'
        text = data.decode(enc, errors='replace')
        for i, line in enumerate(text.splitlines(), 1):
            yield i, line.rstrip('\r\n')


def parse_archive(path):
    """
    解析压缩包内所有可解析文件。
    返回 [(member_name, [(line_no, line_text), ...]), ...]
    只支持 zip/rar/7z，不递归嵌套压缩包（避免炸弹）。
    """
    ext = os.path.splitext(path)[1].lower()
    result = []
    try:
        if ext == '.zip':
            result = _parse_zip(path)
        elif ext == '.rar':
            result = _parse_rar(path)
        elif ext == '.7z':
            result = _parse_7z(path)
    except Exception as e:
        # 损坏的压缩包：抛给上层进 errors 表
        raise
    return result


def _parse_zip(path):
    import zipfile
    out = []
    with zipfile.ZipFile(path, 'r') as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if not _is_text_ext(name):
                continue
            try:
                data = zf.read(info)
                lines = list(_parse_member(name, data))
                if lines:
                    out.append((name, lines))
            except Exception:
                # 单个成员失败不影响其他
                continue
    return out


def _parse_rar(path):
    """rarfile 需要 unrar.exe 在 PATH，找不到则跳过"""
    try:
        import rarfile
    except ImportError:
        return []
    out = []
    try:
        with rarfile.RarFile(path, 'r') as rf:
            for info in rf.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                if not _is_text_ext(name):
                    continue
                try:
                    data = rf.read(info)
                    lines = list(_parse_member(name, data))
                    if lines:
                        out.append((name, lines))
                except Exception:
                    continue
    except Exception:
        pass
    return out


def _parse_7z(path):
    """
    py7zr 没有 readall/read 方法，用 extractall 到临时目录后扫描
    """
    import py7zr
    import tempfile
    out = []
    tmpdir = tempfile.mkdtemp(prefix='anxunqi_7z_')
    try:
        with py7zr.SevenZipFile(path, 'r') as sz:
            sz.extractall(path=tmpdir)
        # 遍历解压后的目录
        for root, _, files in os.walk(tmpdir):
            for fn in files:
                full = os.path.join(root, fn)
                # 取相对路径作为 member 名
                name = os.path.relpath(full, tmpdir)
                # 路径分隔符统一为正斜杠（zip 风格）
                name = name.replace(os.sep, '/')
                if not _is_text_ext(name):
                    continue
                try:
                    with open(full, 'rb') as f:
                        data = f.read()
                    lines = list(_parse_member(name, data))
                    if lines:
                        out.append((name, lines))
                except Exception:
                    continue
    except Exception:
        raise
    finally:
        try:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass
    return out

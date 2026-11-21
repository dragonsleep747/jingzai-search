# 第三方组件与许可

本仓库只分发自研源码。下列组件由使用者自行安装，或仅出现在独立的 Windows 体验版
ZIP 中（该包内附第三方许可文本）。各组件权利归其各自权利人所有。

| 组件 | 版本（体验版） | 上游 |
|---|---|---|
| Qt / Qt PDF | 6.12.0 / 6.140.0 | <https://download.qt.io/official_releases/qt/6.12/6.12.0/submodules/> |
| PySide6 / Shiboken6 | 6.12.0 | <https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.12.0> |
| SumatraPDF | 3.6.1 | <https://github.com/sumatrapdfreader/sumatrapdf/tree/3.6.1rel> |
| Tesseract-OCR | 外部可选 | <https://github.com/UB-Mannheim/tesseract/wiki> |
| poppler-windows | 外部可选 | <https://github.com/oschwartz10612/poppler-windows/releases> |

pip 依赖见 `search_app/requirements.txt`：python-docx、openpyxl、python-pptx、
pdfplumber、olefile、chardet、py7zr、rarfile、watchdog、qrcode、requests、
pytesseract、pdf2image、pillow、PySide6、PyInstaller（打包时）及 OCR 相关组件。
请以各项目自带的 LICENSE 文件为准。

镜仔搜索自研源码按 [MIT License](LICENSE) 授权；Windows 体验版 ZIP 另按包内随附的
《用户许可协议》提供，两者互不等同。

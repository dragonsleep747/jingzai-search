"""随应用打包 Qt 中文资源，覆盖标准按钮、右键和文件选择器。"""
from pathlib import Path
import sys
from PySide6.QtCore import QLibraryInfo, QTranslator


def install_chinese(application):
    if hasattr(application, '_jingzai_translator'):
        return
    translator = QTranslator(application)
    candidates = [Path(getattr(sys,'_MEIPASS',Path(__file__).parents[1]))/'translations',
                  Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))]
    for folder in candidates:
        if translator.load(str(folder/'qtbase_zh_CN.qm')):
            application.installTranslator(translator)
            application._jingzai_translator = translator
            break

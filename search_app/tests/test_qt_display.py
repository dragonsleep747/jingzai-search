import os
from pathlib import Path
import sys
import tempfile
import unittest

from qt_frontend.display import display_sizes, fit_size


class TestDisplaySettings(unittest.TestCase):
    def test_reading_sizes_and_bounds(self):
        self.assertEqual(display_sizes({}), (11, 12))
        self.assertEqual(display_sizes({'dialog_font': 14, 'dialog_text_font': 9}), (14, 14))
        self.assertEqual(display_sizes({'dialog_font': 100, 'dialog_text_font': 100}), (14, 20))

    def test_dialog_dimensions_fit_small_and_large_work_areas(self):
        for available in [(1280, 720), (1920, 1080), (2560, 1440)]:
            width, height = fit_size(1800, 1600, *available)
            self.assertLess(width, available[0])
            self.assertLess(height, available[1])

    def test_executable_manifest_requests_per_monitor_v2(self):
        import xml.etree.ElementTree as xml
        path = Path(__file__).parents[1] / 'qt_frontend' / 'windows.manifest'
        root = xml.parse(path).getroot()
        value = root.find('.//{http://schemas.microsoft.com/SMI/2016/WindowsSettings}dpiAwareness').text
        self.assertIn('PerMonitorV2', value)


class TestNativeDialogDisplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from qt_frontend.backend import Store
        from qt_frontend.qt_display import DialogDisplayPolicy
        self.folder = tempfile.mkdtemp(prefix='jingzai-display-')
        self.store = Store(self.folder)
        self.policy = DialogDisplayPolicy(self.app, self.store)

    def tearDown(self):
        self.app.removeEventFilter(self.policy)
        self.policy.deleteLater()
        self.app.processEvents()

    def test_text_font_and_table_rows_increase_without_changing_main_window(self):
        from PySide6.QtWidgets import QDialog, QPlainTextEdit, QTableView, QVBoxLayout, QWidget
        from PySide6.QtGui import QFont
        main = QWidget()
        main.setFont(QFont('Microsoft YaHei UI', 10))
        dialog = QDialog(main)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit('阅读字号验收')
        table = QTableView()
        layout.addWidget(text)
        layout.addWidget(table)
        dialog.show()
        self.app.processEvents()
        self.assertEqual(text.font().pointSize(), 12)
        self.assertEqual(main.font().pointSize(), 10)
        self.assertGreaterEqual(table.verticalHeader().defaultSectionSize(), table.fontMetrics().height() + 16)
        dialog.close()
        main.close()

    def test_new_preference_applies_when_dialog_is_shown_again(self):
        from PySide6.QtWidgets import QDialog, QPlainTextEdit, QVBoxLayout
        dialog = QDialog()
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        layout.addWidget(text)
        dialog.show()
        self.app.processEvents()
        dialog.hide()
        self.store.settings.update(dialog_font=12, dialog_text_font=15)
        dialog.show()
        self.app.processEvents()
        self.assertEqual(text.font().pointSize(), 15)
        dialog.close()

    def test_settings_remains_scrollable_with_font_controls(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication, QDialog, QScrollArea, QSpinBox
        from qt_frontend.window import Window
        self.store.settings.update(max_backups=0, qt_startup_refresh=False)
        window = Window(self.store)
        window.dialog_display = self.policy
        observed = []
        def inspect():
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, QDialog):
                observed.append((bool(dialog.findChildren(QScrollArea)),
                                 any(spin.value() == 11 for spin in dialog.findChildren(QSpinBox))))
                dialog.reject()
        QTimer.singleShot(100, inspect)
        window.settings_dialog()
        self.assertEqual(observed, [(True, True)])
        window.jobs.pool.waitForDone()
        for _ in range(3):
            self.app.processEvents()
        window._force_exit = True
        window.close()


@unittest.skipUnless(sys.platform == 'win32', 'Windows 兼容工具')
class TestTkReadingFonts(unittest.TestCase):
    def test_small_hardcoded_fonts_are_upgraded_and_icons_are_preserved(self):
        import tkinter as tk
        from tkinter import font as tkfont
        from types import SimpleNamespace
        from qt_frontend.tk_display import TkDisplayPolicy
        root = tk.Tk()
        root.withdraw()
        try:
            app = SimpleNamespace(cfg={}, quick_win=None, _center_win=lambda *args: None)
            policy = TkDisplayPolicy(root, app)
            window = tk.Toplevel(root)
            text = tk.Text(window, font=('Microsoft YaHei UI', 8))
            text.tag_configure('bold', font=('Microsoft YaHei UI', 8, 'bold'))
            icon = tk.Label(window, font=('Segoe MDL2 Assets', 9))
            text.pack()
            icon.pack()
            policy.polish(window)
            self.assertEqual(tkfont.Font(root=root, font=text.cget('font')).actual()['size'], 12)
            self.assertEqual(tkfont.Font(root=root, font=text.tag_cget('bold', 'font')).actual()['size'], 12)
            self.assertEqual(tkfont.Font(root=root, font=icon.cget('font')).actual()['size'], 9)
        finally:
            for timer in root.tk.call('after', 'info'):
                root.after_cancel(timer)
            root.destroy()

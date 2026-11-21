"""Native user journeys with an isolated library and the real local Ollama model.

Run with .venv-qt/Scripts/python.exe tools/ux_live_review.py --out .tmp/ux-review/live
"""
import argparse
import json
import os
import platform
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'search_app'))
os.environ['QT_QPA_PLATFORM'] = 'windows'
if hasattr(platform, '_wmi_query'):
    def skip_wmi(*args):
        raise OSError('Use native Windows version APIs')
    platform._wmi_query = skip_wmi

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
from qt_frontend.backend import Store
from qt_frontend.window import Window


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    parser.add_argument('--deep', action='store_true', help='also exercise the second-round native controls')
    parser.add_argument('--cpu', action='store_true', help='use the native CPU compatibility option')
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    # Every run uses a fresh data directory and never overwrites an earlier run.
    data = out / ('data-' + time.strftime('%H%M%S'))
    store = Store(data)
    store.settings.update(qt_startup_refresh=False, max_backups=0, synonym=False,
                          ollama_base='http://127.0.0.1:11434', ollama_chat='qwen2.5:7b-instruct')
    store.settings['qa_cpu_only'] = args.cpu
    app = QApplication.instance() or QApplication([])
    app.setFont(QFont('Microsoft YaHei UI', 10))
    app.setStyle('Fusion')
    window = Window(store)
    window.show()
    checks, errors, timings = [], [], {}

    def check(value, name):
        if not value:
            raise AssertionError(name)
        checks.append(name)
        print(name, flush=True)

    def wait(condition, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if condition():
                return
            QTest.qWait(15)
        raise TimeoutError('Native journey timed out')

    def idle():
        wait(lambda: not window.jobs.active and not window.search_debounce.isActive())
        QTest.qWait(60)

    def shot(name):
        app.processEvents()
        window.grab().save(str(out / (name + '.png')))

    try:
        idle()
        shot('01-first-use')
        sample = data / '电脑文件'
        sample.mkdir()
        for i in range(205):
            (sample / f'体验样本-{i:03}.txt').write_text(f'文件发现样本 {i}', encoding='utf-8')
        document = sample / '镜仔借阅管理办法.txt'
        document.write_text('第一章 总则\n第一条 纸质借阅期限为五天。\n第二条 到期归还并登记。', encoding='utf-8')
        store.index_files([str(document)], lambda _: None, threading.Event())
        sid = window.discovery.add_directory(str(sample))
        window.build_search_range_menu().actions()[-1].trigger()
        idle()
        dialog = window._range_dialog
        for i in range(dialog.tree.topLevelItemCount()):
            item = dialog.tree.topLevelItem(i)
            if item.data(0, Qt.ItemDataRole.UserRole) == sid:
                dialog.tree.setCurrentItem(item)
        start = next(button for button in dialog.findChildren(QPushButton) if button.text() == '建立当前目录')
        QTest.mouseClick(start, Qt.MouseButton.LeftButton)
        idle()
        check(window.discovery.catalog.count_entries(sid) >= 206, '范围面板实际建立目录')
        dialog.grab().save(str(out / '02-ranges.png'))
        dialog.close()
        window.discovery_scope.setCurrentIndex(0)
        window.search_input.setText('体验样本')
        QTest.mouseClick(window.search_button, Qt.MouseButton.LeftButton)
        idle()
        check(window.discovery_results.topLevelItemCount() == 100, '目录首屏100项')
        for _ in range(2):
            QTest.mouseClick(window.discovery_more, Qt.MouseButton.LeftButton)
            idle()
        check(window.discovery_results.topLevelItemCount() == 205, '继续加载至205项且无遗漏')
        if args.deep:
            window.result_filter.setText('020')
            window.search_mode.setCurrentIndex(1)
            window.search_mode.setCurrentIndex(0)
            idle()
            check(window.discovery_results.topLevelItemCount() == 205 and window.result_filter.text() == '020',
                  '探索智答往返保留205项和筛选')
            window.result_filter.clear()
        shot('03-discovery')
        window.discovery_scope.setCurrentIndex(2)
        window.search_input.setText('借阅期限')
        begin = time.monotonic()
        QTest.mouseClick(window.search_button, Qt.MouseButton.LeftButton)
        idle()
        timings['combined_search_ms'] = round((time.monotonic() - begin) * 1000, 1)
        check(bool(window.current and '五天' in window.reader.toPlainText()), '正文检索与命中预览')
        window.chart_toggle.setChecked(True)
        check(len(window.distribution.groups) == 1, '合并范围的命中分布')
        window.toggle_bookmark()
        idle()
        check(window.current['bookmarked'], '收藏原有正文条款')
        clipboard = QApplication.clipboard()
        prior_text = clipboard.text()
        try:
            window.copy_citation()
            idle()
            check('镜仔借阅管理办法' in clipboard.text(), '复制条款引用')
        finally:
            clipboard.setText(prior_text)
        shot('04-reading')
        window.search_mode.setCurrentIndex(1)
        window.search_input.setText('根据资料，纸质借阅期限是几天？只回答期限。')
        begin = time.monotonic()
        QTest.mouseClick(window.search_button, Qt.MouseButton.LeftButton)
        wait(lambda: bool(window.ai_answer.toPlainText()) or window.ai_job not in window.jobs.active, 120)
        timings['grounded_first_text_ms'] = round((time.monotonic() - begin) * 1000, 1)
        wait(lambda: window.ai_job not in window.jobs.active, 120)
        answer = window.ai_answer.toPlainText()
        check('五天' in answer and window.ai_sources.count() > 0 and '完成' in window.ai_status.text(),
              '真实本机模型按正文回答五天并列出来源')
        item = window.ai_sources.item(0)
        QTest.mouseClick(window.ai_sources.viewport(), Qt.MouseButton.LeftButton,
                         pos=window.ai_sources.visualItemRect(item).center())
        idle()
        check(window.current['path'] == str(document) and window.search_mode.currentIndex() == 1,
              '点击回答来源打开正文并保留智答模式')
        shot('05-grounded-ai')
        if args.deep:
            check(window.current['line'] == 2, '真实回答来源定位到第2行命中')
            window.search_input.setText('尚未发送的后续草稿')
            QTest.mouseClick(window.ai_retry, Qt.MouseButton.LeftButton)
            wait(lambda: window.ai_job not in window.jobs.active, 120)
            check('五天' in window.ai_answer.toPlainText() and window.search_input.text() == '尚未发送的后续草稿',
                  '真实模型重试保留草稿并沿用原问题')
            previous = clipboard.text()
            try:
                QTest.mouseClick(window.ai_copy, Qt.MouseButton.LeftButton)
                check(clipboard.text() == window.ai_answer.toPlainText(), '原生复制回答操作')
            finally:
                clipboard.setText(previous)
            shot('05b-retry-and-copy')
            QTest.mouseClick(window.ai_new, Qt.MouseButton.LeftButton)
            check(not window.ai_answer.toPlainText() and not window._ai_history and not window.ai_retry.isEnabled(),
                  '新对话清空上下文并禁用旧问题重试')
        window.ai_kind.setCurrentIndex(1)
        window.search_input.setText('只回复：镜仔体验检查成功')
        QTest.mouseClick(window.search_button, Qt.MouseButton.LeftButton)
        wait(lambda: window.ai_job not in window.jobs.active, 120)
        check('镜仔体验检查成功' in window.ai_answer.toPlainText() and window.ai_sources.count() == 0,
              '真实本机模型普通聊天完成')
        shot('06-chat')
        window.search_input.setText('请写一篇一千字的图书馆使用故事。')
        QTest.mouseClick(window.search_button, Qt.MouseButton.LeftButton)
        wait(lambda: bool(window.ai_answer.toPlainText()), 120)
        QTest.mouseClick(window.ai_stop, Qt.MouseButton.LeftButton)
        saved = window.ai_answer.toPlainText()
        QTest.qWait(500)
        check('已停止' in window.ai_status.text() and saved == window.ai_answer.toPlainText(), '真实生成停止后不再追加文字')
        wait(lambda: not window.jobs.active, 10)
        shot('07-stopped')
        window.search_mode.setCurrentIndex(0)
        window.navigate('library')
        idle()
        check(window.library_model.rowCount() == 1, '目录发现没有自动污染原资料库')
        window.navigate('bookmarks')
        idle()
        check(len(window._bookmarks) == 1, '原有收藏列表可用')
    except Exception as exc:
        errors.append(f'{type(exc).__name__}: {exc}')
        shot('error')
    finally:
        window.stop_ai()
        for key in list(window.jobs.active):
            window.jobs.cancel(key)
        window._force_exit = True
        window._closing_requested = True
        window.close()
        wait(lambda: not window.jobs.active, 30)
        window.jobs.pool.waitForDone(10000)
        report = dict(ok=not errors, checks=checks, errors=errors, timings=timings,
                      platform=app.platformName(), data_dir=str(data), model=store.settings['ollama_chat'],
                      cpu_compatibility=args.cpu)
        (out / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True), flush=True)
    return int(bool(errors))


if __name__ == '__main__':
    sys.exit(main())

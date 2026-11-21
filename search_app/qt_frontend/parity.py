"""按旧版使用说明补齐操作闭环。兼容入口明确保留旧版完整界面。"""
from functools import partial
import csv
import ctypes
from ctypes import wintypes
import io
import json
import os
from pathlib import Path
import tempfile
import time
import subprocess

from PySide6.QtCore import QEvent, QProcess, QTimer, Qt, QUrl, QUrlQuery
from PySide6.QtGui import QColor, QDesktopServices, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
    QApplication, QComboBox, QFormLayout, QLineEdit, QMenu, QMessageBox, QPlainTextEdit,
    QSpinBox, QTreeWidgetItem, QVBoxLayout,
)
from shiboken6 import isValid

import core
import indexer
import searcher
from service import page_label, snapshot_db, snippet, valid_date

from .document_open import open_located
from .legacy_bridge import TOOLS, launcher_arguments
from .widgets import button, label

SUPPORTED_FILES = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.md',
                   '.csv', '.json', '.log', '.xml', '.html', '.zip', '.rar', '.7z',
                   '.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.webp'}


class ParityMixin:
    def schedule_search(self):
        if hasattr(self,'search_mode') and not self.discovery_is_explore():
            return
        if hasattr(self, 'search_debounce') and (self.search_input.text().strip() or any(
                field.text().strip() for field in self.advanced_fields.values())):
            self.search_debounce.start()

    def rescan_folder(self):
        folder = self.store.settings.get('root_dir', '')
        if folder and Path(folder).is_dir():
            self.scan_folder(folder)
        else:
            self.choose_folder()

    def start_maintenance(self):
        if self.store.settings.pop('backup_failed', None):
            self.notify('上次自动备份未完成，请检查磁盘空间，并手动备份资料库')
            self.store.save()
        if not self.store.settings.get('qt_startup_refresh', True):
            return
        def work(progress, stop):
            with self.store.write_lock:
                if indexer.find_missing_documents(self.store.db):
                    if not snapshot_db(self.store.db, str(self.store.root / 'backup'), 'pre_refresh_qt'):
                        raise RuntimeError('无法创建自检前备份，已保留全部索引')
                report = indexer.refresh_index(self.store.db)
                if not stop.is_set() and not indexer.bigram_ready(self.store.db):
                    indexer.rebuild_bigram(self.store.db, stop_event=stop,
                        progress=lambda done, total: progress({'current': done, 'total': total, 'status': '优化双字词索引'}))
                return report
        def done(report):
            self.refresh_stats()
            if report.get('suspected_offline'):
                self.notify('部分文件所在磁盘暂不可用，已保留索引')
            if self.store.settings.get('qa_auto', False):
                self.sync_semantic()
            self.check_pending_files()
        self.jobs.submit('启动增量自检', work, done, error=self.show_error, visible=False, mutating=True)

    def check_pending_files(self):
        if not self.store.settings.get('inbox_auto_check', True):
            return
        if time.time() - self.store.settings.get('inbox_last_prompt_ts', 0) < 7 * 86400:
            return
        import os
        folders = [(self.store.root / 'inbox', None), (Path(self.store.settings.get('download_watch_dir') or Path.home() / 'Downloads'), time.time() - 86400)]
        found = []
        for folder, cutoff in folders:
            if not folder.is_dir():
                continue
            for path in folder.iterdir():
                try:
                    if path.is_file() and path.suffix.lower() in SUPPORTED_FILES and (cutoff is None or path.stat().st_mtime >= cutoff):
                        found.append(str(path))
                except OSError:
                    continue
        if found:
            self.store.settings['inbox_last_prompt_ts'] = time.time()
            self.store.save()
            if QMessageBox.question(self, '发现待入库资料', f'发现 {len(found)} 个收件箱或近期下载文件，打开完整审核？') == QMessageBox.StandardButton.Yes:
                self.legacy_tool('inbox')

    def sync_semantic(self):
        if getattr(self, '_semantic_job', None) in self.jobs.active:
            self._semantic_pending = True
            return
        settings = dict(self.store.settings)
        def work(progress, stop):
            import ai_qa
            base = settings.get('ollama_base', 'http://127.0.0.1:11434')
            embed = settings.get('ollama_embed', 'bge-m3')
            # 保持已安装模型的增量同步；模型的安装和下载仍在完整问答工作台中操作。
            tags = ai_qa.list_models(base)
            if embed.split(':')[0] not in [tag.split(':')[0] for tag in tags]:
                return '新文件已入库；向量模型尚未安装，可在镜仔智答中补齐'
            info = ai_qa.build_semantic_index(self.store.db, base, embed, cancel_event=stop)
            return f"AI 索引已同步：{info.get('chunks', 0)} 条"
        def done(message):
            self.notify(message)
            if getattr(self, '_semantic_pending', False):
                self._semantic_pending = False
                QTimer.singleShot(500, self.sync_semantic)
        self._semantic_job = self.jobs.submit('自动同步 AI 索引', work, done,
            error=lambda _: self.notify('新资料已入库；AI 服务未就绪，可在镜仔智答中补齐索引'), visible=False)

    def open_result_original(self, item, *_):
        location = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(location, dict) and location.get('type') == 'more':
            parent = item.parent()
            parent.removeChild(item)
            self.append_result_clusters(parent, location['group'], location['start'])
        elif location:
            self.open_located_file(*location)

    def append_result_clusters(self, parent, group, start):
        from .result_snippets import SNIPPET
        terms = core.kw_terms(self.query)
        clusters = group['clusters']
        batch_size = 5
        for cluster in clusters[start:start + batch_size]:
            match = cluster['lines'][0]
            text = '\n'.join(str(row['line_text']).strip() for row in cluster['lines'])
            location = page_label(text, match['line_no'], group['name']) or f"L{match['line_no']}"
            count = f" · {len(cluster['lines'])} 行相邻命中" if len(cluster['lines']) > 1 else ''
            child = QTreeWidgetItem([text, ''])
            child.setData(0, SNIPPET, {'text': text, 'terms': terms, 'location': location + count,
                                     'fuzzy': match.get('score', 1) < 1})
            child.setData(0, Qt.ItemDataRole.UserRole, (group['path'], match['line_no']))
            child.setToolTip(0, text)
            child.setData(0, Qt.ItemDataRole.AccessibleDescriptionRole, '空格展开或收起命中片段；回车打开原文件')
            if match.get('score', 1) < 1:
                child.setForeground(0, QColor('#c05621'))
            parent.addChild(child)
            if parent.treeWidget():
                child.setFirstColumnSpanned(True)
        if start + batch_size < len(clusters):
            more = QTreeWidgetItem([f'还有 {len(clusters) - start - batch_size} 处命中 · 点击展开', ''])
            more.setData(0, Qt.ItemDataRole.UserRole, {'type': 'more', 'group': group, 'start': start + batch_size})
            parent.addChild(more)
            if parent.treeWidget():
                more.setFirstColumnSpanned(True)

    def jump_distribution(self, path):
        tree = self.discovery_results if self.result_stack.currentWidget() is self.discovery_results else self.results
        for number in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(number)
            value = item.data(0, Qt.ItemDataRole.UserRole)
            if value and (value.get('path') if isinstance(value, dict) else value[0]) == path:
                item.setExpanded(True)
                tree.setCurrentItem(item)
                tree.scrollToItem(item)
                return
        group = next((group for group in self.groups if group['path'] == path), None)
        if group:
            self.open_preview(path, group['first_line'])

    def follow_reference(self, url):
        if url.scheme() == 'jingzai-preview':
            target=self.reference_target(url)
            if target is None:
                self.notify('引用地址无效')
                return
            path,line=target
            if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier:
                self.open_located_file(path,line)
            else:
                self.open_preview(path,line)
        elif url.scheme() in ('http', 'https'):
            QDesktopServices.openUrl(url)

    @staticmethod
    def reference_target(url):
        if url.scheme()!='jingzai-preview':
            return None
        values=QUrlQuery(url)
        path=values.queryItemValue('path',QUrl.ComponentFormattingOption.FullyDecoded)
        try:
            line=max(1,int(values.queryItemValue('line') or 1))
        except ValueError:
            return None
        return (path,line) if path else None

    def reference_hovered(self,url):
        target=self.reference_target(url)
        self.reader.setToolTip(f'{target[0]} · L{target[1]}\n单击查看引用；Ctrl+单击打开源文件；右键可选择操作。' if target else '')

    def reference_menu(self,point):
        url=QUrl(self.reader.anchorAt(point))
        target=self.reference_target(url)
        if target:
            path,line=target
            menu=QMenu(self.reader)
            menu.addAction('在软件内查看引用资料',lambda:self.open_preview(path,line))
            menu.addAction('打开源文件并定位',lambda:self.open_located_file(path,line))
            menu.addAction('打开源文件所在位置',lambda:self.open_discovery_location(path))
            menu.addAction('复制源文件路径',lambda:self.copy_discovery_path(path))
        else:
            menu=self.reader.createStandardContextMenu(point)
        menu.exec(self.reader.viewport().mapToGlobal(point))
        menu.deleteLater()

    def reflow_changed(self, checked):
        self.store.settings['reflow'] = bool(checked)
        self.store.save()
        if self.current:
            self.current['reflow'] = bool(checked)
            self.show_preview(self.current)

    def zoom_reader_event(self, target, event):
        if target is self.reader.viewport() and event.type() == QEvent.Type.Wheel:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier and self.current:
                size = self.store.settings.get('preview_font', 12)
                size = max(9, min(28, size + (1 if event.angleDelta().y() > 0 else -1)))
                position = self.reader.verticalScrollBar().value()
                self.store.settings['preview_font'] = size
                self.show_preview(self.current)
                self.reader.verticalScrollBar().setValue(position)
                self.store.save()
                return True
        return False

    def install_feature_menu(self):
        self.menuBar().hide()
        self._legacy_processes = []
        self.results.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.results.customContextMenuRequested.connect(self.result_menu)
        copy = QShortcut(QKeySequence('Ctrl+C'), self.reader)
        copy.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        copy.activated.connect(self.copy_text)

    def legacy_tool(self, tool, context=None):
        self.store.save()
        arguments = ['--data-dir', str(self.store.root), '--legacy-tool', tool]
        if tool == 'quick':
            try:
                ctypes.windll.user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
                foreground = ctypes.windll.user32.GetForegroundWindow()
                arguments += ['--foreground-hwnd', str(foreground or 0)]
            except AttributeError:
                pass
        if context:
            stream = tempfile.NamedTemporaryFile(prefix='jingzai-context-', suffix='.json',
                                                  mode='w', encoding='utf-8', delete=False)
            with stream:
                json.dump(context, stream, ensure_ascii=False)
            arguments += ['--legacy-context', stream.name]
        try:
            process = subprocess.Popen(launcher_arguments(*arguments), cwd=str(self.store.root),
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            process.jingzai_tool = tool
            self._legacy_processes.append(process)
            started = True
        except OSError as error:
            self.show_error(str(error))
            started = False
        if not hasattr(self, '_legacy_timer'):
            self._legacy_timer = QTimer(self)
            self._legacy_timer.setInterval(1000)
            self._legacy_timer.timeout.connect(self.poll_legacy_tools)
            self._legacy_timer.start()
        if not started:
            self.notify('完整工具未能启动')
        else:
            self.notify('已打开：' + TOOLS[tool][0] + '（保留旧版完整流程）')
        return started

    def poll_legacy_tools(self):
        exited = [process for process in self._legacy_processes if process.poll() is not None]
        if exited:
            self._legacy_processes = [process for process in self._legacy_processes if process not in exited]
            self.store.save()  # 合并工具写入的设置，不覆盖并行修改。
            self.load_library()
            self.refresh_stats()
            self.start_watcher()
            self.refresh_current()
            self.sync_semantic()

    def confirm_import(self, paths):
        from .import_dialog import ImportDialog
        if paths:
            return self.keep_dialog(ImportDialog(self, paths))

    def web_search(self):
        self.legacy_tool('web')

    def category_rules(self):
        self.legacy_tool('category_rules')

    def standard_check(self):
        self.legacy_tool('standards')

    def result_menu(self, position):
        item = self.results.itemAt(position)
        if not item:
            return
        self.results.setCurrentItem(item)
        location = item.data(0, Qt.ItemDataRole.UserRole)
        if not location or isinstance(location, dict):
            return
        menu = QMenu(self)
        menu.addAction('打开原文件并定位', lambda: self.open_located_file(*location))
        def copy_citation():
            self.jobs.submit('复制条款引用', lambda progress, stop: core.build_citation(self.store.db, *location),
                lambda text: QApplication.clipboard().setText(text), error=self.show_error, visible=False)
        menu.addAction('复制条款引用', copy_citation)
        menu.addAction('自动匹配其他版本并对比', lambda: self.compare_path(location[0]))
        menu.exec(self.results.viewport().mapToGlobal(position))

    def reverse_lookup(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('反查出处 · 粘贴原文片段')
        dialog.resize(680, 390)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setPlaceholderText('粘贴要找出处的句子；按句号、换行拆分后匹配库内原文。')
        layout.addWidget(text)
        def run():
            import re
            fragments = [item.strip() for item in re.split(r'[。！？；\n]+', text.toPlainText()) if len(item.strip()) >= 4][:30]
            if not fragments:
                self.notify('请至少输入 4 个字的原文片段')
                return
            def work(progress, stop):
                sources = {}
                for number, fragment in enumerate(fragments, 1):
                    if stop.is_set():
                        break
                    # 引号不属于检索语法；先用整句精确子串定位。
                    for row in searcher.search(self.store.db, fragment, only_active=False):
                        key = (row['path'], row['line_no'])
                        sources[key] = dict(row, fragment=fragment)
                    progress({'current': number, 'total': len(fragments)})
                return list(sources.values())
            def done(rows):
                self.records_dialog(f'反查出处 · {len(rows)} 处匹配', [('文件', 'name'), ('行号', 'line_no'),
                    ('匹配原文', 'line_text')], rows, lambda row: self.open_preview(row['path'], row['line_no']))
            self.jobs.submit('反查文本出处', work, done, error=self.show_error)
        layout.addWidget(button('开始反查', run, True))
        self.keep_dialog(dialog)

    def package_software(self):
        import sys
        if not getattr(sys, 'frozen', False):
            self.notify('请在已打包的新版中使用此功能，以包含完整运行组件')
            return
        target, _ = QFileDialog.getSaveFileName(self, '打包软件和完整资料库', '镜仔搜索完整分发包.zip', '资料包 (*.zip)')
        if target:
            from .maintenance import package_complete
            self.jobs.submit('打包软件和完整资料库', lambda progress, stop: package_complete(
                self.store, target, progress, stop, software=Path(sys.executable).parent),
                lambda result: self.notify('完整分发包已保存：' + result['zip_path']), error=self.show_error)

    def backup_library(self):
        from .maintenance import package_complete
        target, kind = QFileDialog.getSaveFileName(self, '备份资料库', '镜仔搜索完整资料包.zip',
                                                 '完整资料包 (*.zip);;索引备份 (*.db)')
        if not target:
            return
        if Path(target).resolve() == Path(self.store.db).resolve():
            self.notify('备份位置不能覆盖正在使用的索引')
            return
        work = (lambda progress, stop: self.store.backup(target, progress, stop)) if target.lower().endswith('.db') else (
            lambda progress, stop: package_complete(self.store, target, progress, stop))
        self.jobs.submit('备份资料库', work, lambda _: self.notify('备份已保存：' + target), error=self.show_error)

    def restore_in_place(self):
        if self.jobs.active or self._legacy_processes:
            self.notify('请先等待任务完成，并关闭完整兼容工具后再还原')
            return
        archive, _ = QFileDialog.getOpenFileName(self, '选择原位还原的完整资料包', '', '资料包 (*.zip)')
        if not archive:
            return
        def ready(result):
            message = (f"已校验 {result['doc_count']} 个资料文件。\n还原将重启新版，现有资料完整保留在 backup 文件夹。\n"
                       '请先关闭旧版和其他使用此资料库的程序。现在切换？')
            if QMessageBox.question(self, '资料准备完成', message) != QMessageBox.StandardButton.Yes:
                self.notify('已取消切换，原资料保持原样')
                return
            import os
            arguments = launcher_arguments('--data-dir', str(self.store.root), '--finish-restore',
                result['staging'], '--wait-pid', str(os.getpid()))
            subprocess.Popen(arguments, cwd=str(self.store.root), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self._exit_backup_done = True
            self._force_exit = True
            self._closing_requested = True
            self._jobs_settled()
        self.jobs.submit('准备还原资料', lambda progress, stop: indexer.restore_library(
            archive, app_dir=str(self.store.root), stop_event=stop,
            progress=lambda done, total, name: progress({'current': done, 'total': total, 'path': name})),
            ready, error=self.show_error)

    def register_protocol(self):
        import sys
        if not getattr(sys, 'frozen', False):
            self.notify('请在已打包的新版中注册协议')
            return
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Classes\jingzai') as key:
            winreg.SetValueEx(key, '', 0, winreg.REG_SZ, 'URL:镜仔搜索')
            winreg.SetValueEx(key, 'URL Protocol', 0, winreg.REG_SZ, '')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Classes\jingzai\shell\open\command') as key:
            command = subprocess.list2cmdline([sys.executable, '--data-dir', str(self.store.root)]) + ' "%1"'
            winreg.SetValueEx(key, '', 0, winreg.REG_SZ, command)
        self.notify('已注册 jingzai:// 唤起新版')

    def toggle_autostart(self):
        import sys
        if not getattr(sys, 'frozen', False):
            self.notify('请在已打包的新版中设置开机启动')
            return
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
            try:
                winreg.QueryValueEx(key, '镜仔搜索新版')
                enabled = True
            except FileNotFoundError:
                enabled = False
            if QMessageBox.question(self, '开机启动', ('关闭' if enabled else '启用') + '新版开机启动？') != QMessageBox.StandardButton.Yes:
                return
            if enabled:
                winreg.DeleteValue(key, '镜仔搜索新版')
            else:
                winreg.SetValueEx(key, '镜仔搜索新版', 0, winreg.REG_SZ,
                    subprocess.list2cmdline([sys.executable, '--data-dir', str(self.store.root)]))
        self.notify('已关闭新版开机启动' if enabled else '已启用新版开机启动')

    def syntax_help(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('搜索速查 · 点击示例直接搜索')
        layout = QVBoxLayout(dialog)
        for title, query in [('同时出现', '安全 培训'), ('任意一个', '安全 | 环保'),
            ('排除词', '安全 -费用'), ('按文号', '文号:12号'), ('废止与替代', '状态:废止|替代'),
            ('日期范围', '日期:2022..2024'), ('仅文件名', '名称:差旅')]:
            layout.addWidget(button(title + '：' + query, lambda value=query: (dialog.close(), self.use_query(value))))
        self.keep_dialog(dialog)

    def quick_search(self):
        for process in self._legacy_processes:
            if getattr(process, 'jingzai_tool', '') == 'quick' and process.poll() is None:
                (self.store.root / '.qt_quick_command').write_text(str(time.time_ns()), encoding='ascii')
                return True
        return self.legacy_tool('quick')

    def send_results_to_qa(self):
        if not self.groups:
            self.notify('请先搜索相关资料')
            return
        self.ai_use_results.setChecked(True)
        self.ai_kind.setCurrentIndex(0)
        self.navigate('ai')

    def show_user_guide(self):
        import sys
        folder = Path(getattr(sys, '_MEIPASS', Path(__file__).parents[2]))
        guide = folder / 'docs' / 'LEGACY_USER_GUIDE.txt'
        self.text_dialog('镜仔搜索 · 完整使用说明',
            '新版入口：侧栏仅搜索、资料库、收藏；搜索输入区切换“探索／智答”。\n'
            '搜索框下方“范围”切换电脑文件、资料库或两者；菜单内可管理位置。“筛选”收纳常用与高级条件。\n'
            '结果操作在数量旁的“⋯”；正文顶部文件名可查看详情，位置可跳转，“阅读”收纳阅读操作。Ctrl+Shift+R 专注阅读。\n'
            '智答可使用搜索到的正文作为依据；模型管理、完整历史与导出在“智答设置 → 模型与完整智答工作台”。\n'
            '添加、扫描、待入库审核、剪贴板快存在资料库页“添加资料”；管理功能在“更多操作”。\n'
            '底部菜单 → 工具中心包含备份还原、专项检查、联网、速查和旧版备用；菜单另有任务、设置与帮助。\n'
            '以下保留旧版完整功能说明，旧版菜单位置以新版功能位置说明为准。\n\n'
            + guide.read_text(encoding='utf-8'))

    def feature_catalog(self):
        locations = [
            ('探索与电脑搜索范围', '搜索输入区 → 探索；范围 → 管理搜索范围', '文件名与路径检索；加入资料库后检索正文'),
            ('智答与完整工作台', '搜索输入区 → 智答 → 智答设置', '原生问答与来源预览；模型、完整历史与导出保留兼容工作台'),
            ('导入文件、扫描文件夹', '资料库页 → 添加资料', '文件预检与冲突处理；扫描登记原路径'),
            ('待入库审核、剪贴板快存', '资料库页 → 添加资料', '完整审核流程与剪贴板快存'),
            ('搜索历史、常用搜索、恢复阅读', '搜索框右侧历史图标', '保存或恢复搜索条件'),
            ('搜索语法示例', '搜索页 → 筛选 → 搜索语法与示例', '点击示例可直接搜索'),
            ('导出结果、引用关系及关系图', '结果数量右侧 → ⋯', '针对当前搜索结果'),
            ('AI 分析当前搜索结果', '结果数量右侧 → ⋯', '携带当前正文命中依据进入新版智答'),
            ('联网检索、迷你速查、文本反查', '菜单 → 工具中心 → 辅助检索', '完整工具；速查保留托盘与全局热键'),
            ('编辑单份文件信息', '资料库页 → 编辑信息', '状态、文号、日期与分类'),
            ('原文件、重命名、重建索引、移除索引', '资料库页 → 更多操作', '针对所选文件'),
            ('批量编辑、全库台账、查重清理', '资料库页 → 更多操作', '批量管理与导出'),
            ('问题文件管理', '资料库页 → 更多操作', '统一查看失联、无文本、废止与已替代文件'),
            ('分类自查与修正、分类规则管理', '资料库页 → 更多操作', '修正、忽略、撤销及自动规则'),
            ('双文件对比、自动配对、标准版本检查', '资料库页 → 更多操作', '手选两份或自动寻找其他版本'),
            ('收藏备注与导出汇编', '侧栏 → 收藏', '正文顶部“阅读”可收藏当前条款'),
            ('任务进度、停止、详情、解析错误及重试', '菜单 → 任务与进度', '后台任务与异常处理'),
            ('备份、独立还原、原位还原、完整软件包', '菜单 → 工具中心 → 资料安全', '不同操作的目标范围和恢复方式不同'),
            ('引用预警、合规扫描、索引体检、OCR 诊断', '菜单 → 工具中心 → 检查与配置', '专项检查工具'),
            ('下载监控目录、默认存档目录、同义词', '菜单 → 工具中心 → 检查与配置', '专项配置'),
            ('主题、正文和子窗口字号、模型等设置', '菜单 → 设置', '统一应用偏好'),
            ('使用说明、反馈、开机启动、协议唤起', '菜单 → 设置 → 启动与唤起', '完整说明支持 F1'),
            ('旧版备用工具', '菜单 → 工具中心 → 旧版备用', '保留旧版管理流程作为备用'),
        ]
        rows = [dict(name=name, location=location, note=note) for name, location, note in locations]
        self.records_dialog('功能位置说明', [('功能', 'name'), ('入口位置', 'location'), ('说明', 'note')], rows)

    def open_located_file(self, path, line=1):
        query = self.query
        self.jobs.submit('打开原文件并定位',
            lambda progress, stop: open_located(self.store.db, path, line, query, str(self.store.root)),
            self.notify, error=self.show_error, visible=False)

    def selected_dialog_rows(self, dialog):
        return [dialog.model.rows[index.row()] for index in dialog.table.selectionModel().selectedRows()]

    def show_duplicates(self):
        def loaded(groups):
            rows = []
            for group in groups:
                for item in group['dups']:
                    rows.append(dict(item, reason=group['reason'], keep=group['keep']['path']))
            dialog = self.records_dialog('查重清理 · 保留建议文件，其余可选中清理', [
                ('重复文件', 'name'), ('原因', 'reason'), ('建议保留', 'keep'), ('路径', 'path')], rows,
                lambda row: self.open_preview(row['path'], 1))
            controls = QHBoxLayout()
            controls.addWidget(button('仅移除所选索引', lambda: self.clean_rows(
                self.selected_dialog_rows(dialog), False, dialog)))
            controls.addWidget(button('所选文件移入回收站', lambda: self.clean_rows(
                self.selected_dialog_rows(dialog), True, dialog)))
            controls.addWidget(button('清理全部同内容重复文件', lambda: self.clean_rows(
                [row for row in rows if row['reason'] == '内容重复'], True, dialog)))
            dialog.layout_box.addLayout(controls)
        self.jobs.submit('查重分析', lambda progress, stop: indexer.find_duplicates(self.store.db),
                         loaded, error=self.show_error)

    def clean_rows(self, rows, recycle=False, dialog=None):
        paths = list(dict.fromkeys(row['path'] for row in rows))
        if not paths:
            self.notify('请先选择要处理的文件')
            return
        message = f'将处理 {len(paths)} 个文件。\n'
        message += '文件移入回收站后移除索引；失败的项目会保留。' if recycle else '只移除索引，磁盘上的文件保留。'
        message += '\n\n'+ '\n'.join(Path(path).name for path in paths[:8]) + (f'\n另 {len(paths)-8} 个文件' if len(paths)>8 else '')
        if QMessageBox.question(self, '确认清理', message) != QMessageBox.StandardButton.Yes:
            return
        def work(progress, stop):
            result = {'removed': [], 'failed': []}
            with self.store.write_lock:
                if not snapshot_db(self.store.db, str(self.store.root / 'backup'), 'pre_cleanup_qt'):
                    raise RuntimeError('无法创建清理前备份，本次未删除任何文件')
                for number, path in enumerate(paths, 1):
                    if stop.is_set():
                        break
                    status = indexer.remove_from_library(self.store.db, path, delete_file=recycle)
                    result['removed' if status in ('ok', 'not_found') else 'failed'].append(path if status in ('ok', 'not_found') else (path, status))
                    progress({'current': number, 'total': len(paths), 'path': path, 'status': status})
            return result
        def done(result):
            self.load_library()
            self.refresh_stats()
            if dialog and isValid(dialog):
                removed = set(result['removed'])
                dialog.source_rows = [row for row in dialog.source_rows if row['path'] not in removed]
                dialog.model.set_rows([row for row in dialog.model.rows if row['path'] not in removed])
            self.notify(f"已处理 {len(result['removed'])} 个，失败 {len(result['failed'])} 个")
            if result['failed']:
                self.text_dialog('清理未完成的文件', '\n'.join(f'{path}: {reason}' for path, reason in result['failed']))
        self.jobs.submit('清理资料', work, done, error=self.show_error, mutating=True)

    def category_audit(self):
        def loaded(report):
            rows = [dict(row, current=row.get('current', ''), unclassified=not row.get('current'))
                    for row in report['uncategorized'] + report['mismatch']]
            dialog = self.records_dialog('分类自查 · 修正、忽略与撤销', [
                ('文件', 'name'), ('当前分类', 'current'), ('建议分类', 'suggested'), ('路径', 'path')], rows,
                lambda row: self.open_preview(row['path'], 1))
            controls = QHBoxLayout()
            def apply(selected):
                mapping = {row['path']: row['suggested'] for row in selected if row.get('suggested')}
                if not mapping:
                    self.notify('所选文件没有建议分类')
                    return
                if QMessageBox.question(self, '确认分类修正',
                    f'修正 {len(mapping)} 个文件的分类，并同步移动库内文件？可撤销本次修正。') != QMessageBox.StandardButton.Yes:
                    return
                def work(progress, stop):
                    with self.store.write_lock:
                        if not snapshot_db(self.store.db, str(self.store.root / 'backup'), 'pre_catfix_qt'):
                            raise RuntimeError('无法创建分类修正前备份，本次未移动任何文件')
                        records = indexer.apply_categories_ex(self.store.db, mapping)
                        (self.store.root / 'category_undo.json').write_text(
                            json.dumps({'time': time.time(), 'records': records}, ensure_ascii=False), encoding='utf-8')
                        return records
                def done(records):
                    self.load_library()
                    self.notify(f'已修正 {len(records)} 个文件')
                    if isValid(dialog):
                        dialog.close()
                    self.category_audit()
                self.jobs.submit('修正分类', work, done, error=self.show_error, mutating=True)
            def ignore():
                paths = [row['path'] for row in self.selected_dialog_rows(dialog)]
                if paths:
                    self.jobs.submit('忽略分类检查', lambda progress, stop: self.store.write(
                        indexer.set_category_ignored, paths, True), lambda _: dialog.close() if isValid(dialog) else None,
                        error=self.show_error, mutating=True)
            controls.addWidget(button('修正所选', lambda: apply(self.selected_dialog_rows(dialog))))
            controls.addWidget(button('修正全部未分类', lambda: apply([row for row in rows if row['unclassified']])))
            controls.addWidget(button('忽略所选', ignore))
            controls.addWidget(button('撤销上次修正', self.undo_categories))
            dialog.layout_box.addLayout(controls)
        self.jobs.submit('分类自查', lambda progress, stop: indexer.category_audit(self.store.db), loaded, error=self.show_error)

    def undo_categories(self):
        path = self.store.root / 'category_undo.json'
        if not path.exists():
            self.notify('没有可撤销的分类修正')
            return
        records = json.loads(path.read_text(encoding='utf-8'))['records']
        if QMessageBox.question(self, '撤销分类修正', f'尝试恢复上次修正的 {len(records)} 个文件？') != QMessageBox.StandardButton.Yes:
            return
        def done(result):
            self.notify(f'恢复 {result[0]} 个，跳过 {result[1]} 个')
            self.load_library()
            if result[1] == 0:
                path.unlink(missing_ok=True)
        self.jobs.submit('撤销分类修正', lambda progress, stop: self.store.write(indexer.revert_categories, records),
                         done, error=self.show_error, mutating=True)

    def problem_files(self):
        def work(progress, stop):
            problems = []
            for row in self.store.library():
                kinds = []
                if not Path(row['path']).is_file():
                    kinds.append('失联')
                if not row['line_count']:
                    kinds.append('无文本')
                if row['status'] in ('void', 'replaced'):
                    kinds.append('废止' if row['status'] == 'void' else '已替代')
                if kinds:
                    problems.append(dict(row, problem=' / '.join(kinds)))
            return problems
        def loaded(rows):
            dialog = self.records_dialog('问题文件管理', [('文件', 'name'), ('问题', 'problem'),
                ('行数', 'line_count'), ('路径', 'path')], rows, lambda row: self.open_preview(row['path'], 1))
            controls = QHBoxLayout()
            controls.addWidget(button('仅清理所选索引', lambda: self.clean_rows(self.selected_dialog_rows(dialog), False, dialog)))
            controls.addWidget(button('所选文件移入回收站', lambda: self.clean_rows(self.selected_dialog_rows(dialog), True, dialog)))
            controls.addWidget(button('重新索引所选', lambda: self.index_files([row['path'] for row in self.selected_dialog_rows(dialog)])))
            dialog.layout_box.addLayout(controls)
        self.jobs.submit('检查问题文件', work, loaded, error=self.show_error)

    def show_missing(self):
        return self.problem_files()

    def batch_metadata(self):
        rows = self.selected_library_rows()
        if not rows:
            self.notify('请先在资料库中选择文件')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f'批量编辑 {len(rows)} 个文件')
        form = QFormLayout(dialog)
        status = QComboBox()
        for value, text in [(None, '保持原状态'), ('active', '有效'), ('void', '已废止'), ('replaced', '已替代')]:
            status.addItem(text, value)
        form.addRow('状态', status)
        change_date = QCheckBox('修改 / 清空施行日期')
        date = QLineEdit()
        date.setPlaceholderText('YYYY-MM-DD；留空表示清除')
        date.setEnabled(False)
        change_date.toggled.connect(date.setEnabled)
        form.addRow(change_date, date)
        change_category = QCheckBox('修改分类')
        category = QComboBox()
        category.setEditable(True)
        category.addItems(list(indexer.CATEGORY_FOLDERS))
        category.setEnabled(False)
        change_category.toggled.connect(category.setEnabled)
        form.addRow(change_category, category)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        controls.rejected.connect(dialog.reject)
        def accept():
            if change_date.isChecked() and date.text().strip() and not valid_date(date.text().strip()):
                self.notify('日期需为有效的 YYYY-MM-DD')
                return
            dialog.accept()
        controls.accepted.connect(accept)
        form.addRow(controls)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = {}
        if status.currentData() is not None:
            values['status'] = status.currentData()
        if change_date.isChecked():
            values['effective_date'] = date.text().strip()
        if change_category.isChecked():
            values['category'] = category.currentText().strip()
        if not values:
            return
        paths = [row['path'] for row in rows]
        def work(progress, stop):
            with self.store.write_lock:
                if not snapshot_db(self.store.db, str(self.store.root / 'backup'), 'pre_metadata_qt'):
                    raise RuntimeError('无法创建元数据修改前备份')
                done = 0
                for number, path in enumerate(paths, 1):
                    if stop.is_set():
                        break
                    result = indexer.update_metadata(self.store.db, path, **values)
                    if result is not True:
                        raise RuntimeError(result)
                    done += 1
                    progress({'current': number, 'total': len(paths), 'path': path})
                return done
        def done(count):
            self.load_library()
            self.notify(f'已更新 {count} 个文件')
            if values.get('status') in ('void', 'replaced'):
                self.warn_incoming(paths)
        self.jobs.submit('批量编辑资料信息', work, done, error=self.show_error, mutating=True)

    def warn_incoming(self, paths):
        def loaded(groups):
            rows = [dict(reference, target=Path(target).name) for target, references in groups for reference in references]
            if rows:
                self.records_dialog('引用提醒 · 以下文件引用了刚标记废止或替代的依据', [
                    ('引用方', 'name'), ('被引用文件', 'target'), ('条款', 'clause'), ('行号', 'line_no')],
                    rows, lambda row: self.open_preview(row['path'], row['line_no']))
        self.jobs.submit('检查受影响的引用', lambda progress, stop: searcher.incoming_refs_multi(self.store.db, paths),
                         loaded, error=self.show_error, visible=False)

    def export_ledger(self):
        target, _ = QFileDialog.getSaveFileName(self, '导出全库台账', '资料库台账.csv', 'CSV 文件 (*.csv)')
        if not target:
            return
        def work(progress, stop):
            rows = self.store.library()
            with open(target, 'w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['序号', '文件名', '文号', '分类', '状态', '施行日期', '最近索引', '路径'])
                for number, row in enumerate(rows, 1):
                    writer.writerow([number, row['name'], row['doc_no'], row['category'], row['status'],
                                     row['effective_date'], row['indexed_at'], row['path']])
            return target
        self.jobs.submit('导出资料台账', work, lambda path: self.notify('已导出：' + path), error=self.show_error)

    def compare_auto(self):
        rows = self.selected_library_rows()
        path = rows[0]['path'] if rows else self.current['path'] if self.current else None
        if not path:
            self.notify('请选择或打开一份资料')
            return
        self.compare_path(path)

    def compare_path(self, path):
        def loaded(candidates):
            if not candidates:
                self.notify('没有找到可配对的其他版本')
                return
            current = next((row for row in self._library if row['path'] == path), {'path': path, 'name': Path(path).name})
            if len(candidates) == 1:
                self.compare_pair(current, candidates[0])
            else:
                self.records_dialog('自动匹配的其他版本 · 双击直接对比', [('文件', 'name'), ('路径', 'path')],
                                    candidates, lambda row: self.compare_pair(current, row))
        self.jobs.submit('查找其他版本', lambda progress, stop: searcher.find_version_siblings(self.store.db, path),
                         loaded, error=self.show_error, visible=False)

"""按用途分组的设置；只有保存时才应用，取消不改变当前配置。"""
from PySide6.QtCore import QUrl
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QApplication,QCheckBox,QComboBox,QDialog,QDialogButtonBox,
    QFormLayout,QLineEdit,QPlainTextEdit,QScrollArea,QSpinBox,QTabWidget,QVBoxLayout,QWidget)
from .widgets import button,label


def _autostart_state():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
            winreg.QueryValueEx(key, '镜仔搜索新版')
        return True
    except OSError:
        return False


def _apply_autostart(enabled, data_dir):
    import subprocess
    import sys
    import winreg
    command = subprocess.list2cmdline([sys.executable, '--data-dir', str(data_dir)])
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
        if enabled:
            winreg.SetValueEx(key, '镜仔搜索新版', 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, '镜仔搜索新版')
            except FileNotFoundError:
                pass


def show_settings(window):
    settings=window.store.settings
    dialog=QDialog(window)
    dialog.setWindowTitle('设置')
    dialog.resize(790,620)
    outer=QVBoxLayout(dialog)
    tabs=QTabWidget()
    outer.addWidget(tabs,1)
    forms={}
    for name in ('外观与阅读','搜索与索引','智答','资料安全','启动与唤起','快捷键'):
        scroll=QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body=QWidget()
        form=QFormLayout(body)
        form.setContentsMargins(16,16,16,16)
        form.setSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        scroll.setWidget(body)
        tabs.addTab(scroll,name)
        forms[name]=form
    def check(name,title,key,default=False):
        item=QCheckBox(title)
        item.setObjectName(key)
        item.setChecked(bool(settings.get(key,default)))
        forms[name].addRow(item)
        return item
    def spin(name,title,key,default,low,high):
        item=QSpinBox()
        item.setObjectName(key)
        item.setRange(low,high)
        item.setValue(int(settings.get(key,default)))
        forms[name].addRow(title,item)
        return item
    dark=check('外观与阅读','深色外观','dark')
    font=spin('外观与阅读','正文大小','preview_font',12,9,24)
    ui=spin('外观与阅读','子窗口控件字号','dialog_font',11,10,14)
    text=spin('外观与阅读','子窗口阅读字号','dialog_text_font',12,11,20)
    sample=label('阅读预览：清晰的文字，紧凑的操作。')
    sample.setWordWrap(True)
    font.valueChanged.connect(lambda size:sample.setStyleSheet(f'font-size:{size}pt;padding:10px;'))
    sample.setStyleSheet(f'font-size:{font.value()}pt;padding:10px;')
    forms['外观与阅读'].addRow('预览',sample)
    forms['外观与阅读'].addRow(label('搜索工具条保持紧凑字号；完整兼容工具重新打开后使用新字号。','muted'))
    watch=check('搜索与索引','自动监听已选择的资料文件夹','autowatch',True)
    forms['搜索与索引'].addRow(label('电脑目录用于文件名称和路径检索；加入资料库后可检索正文。','muted'))
    synonyms=QPlainTextEdit(settings.get('custom_synonyms',''))
    synonyms.setObjectName('custom_synonyms')
    synonyms.setPlaceholderText('每行：主词 = 别名1, 别名2\n例如：培训 = 教育培训, 学习教育')
    synonyms.setMaximumHeight(180)
    forms['搜索与索引'].addRow('自定义同义词',synonyms)
    fields={}
    for group,key,title,default in (
        ('智答','ollama_base','服务地址','http://127.0.0.1:11434'),
        ('智答','ollama_chat','问答模型','qwen2.5:7b-instruct'),
        ('智答','ollama_embed','向量模型','bge-m3'),
        ('快捷键','quick_hotkey','全局搜索','ctrl+alt+j'),
        ('快捷键','clip_hotkey','快存剪贴板文字','ctrl+alt+m')):
        field=QLineEdit(settings.get(key,default))
        field.setObjectName(key)
        field.setClearButtonEnabled(True)
        fields[key]=field
        forms[group].addRow(title,field)
    qa_auto=check('智答','启动时增量更新资料语义索引','qa_auto')
    cpu=check('智答','兼容模式：使用 CPU 推理','qa_cpu_only')
    forms['智答'].addRow(label('显卡初始化失败时可启用兼容模式；CPU 推理通常较慢。','muted'))
    connection=label('尚未检测','muted')
    connection.setWordWrap(True)
    test=button('检测模型服务')
    def test_connection():
        import ai_qa
        test.setEnabled(False)
        connection.setText('正在连接…')
        def done(models):
            from shiboken6 import isValid
            if not isValid(dialog):return
            test.setEnabled(True)
            connection.setText(f'连接正常 · 本机提供 {len(models)} 个模型')
        def failed(detail):
            from shiboken6 import isValid
            if not isValid(dialog):return
            test.setEnabled(True)
            connection.setText('连接未完成，请检查服务是否启动、地址是否正确。')
            connection.setToolTip(detail)
        base=fields['ollama_base'].text().strip()
        window.jobs.submit('检测模型服务',lambda p,s:ai_qa.list_models(base),done,error=failed,visible=False)
    test.clicked.connect(test_connection)
    forms['智答'].addRow(test,connection)
    path=QLineEdit(str(window.store.root))
    path.setReadOnly(True)
    forms['资料安全'].addRow('资料库位置',path)
    backups=spin('资料安全','退出时保留的自动备份份数','max_backups',3,0,10)
    forms['资料安全'].addRow(label('设为 0 可关闭退出备份；手动完整资料包包含原文件，索引备份仅包含检索数据库。','muted'))
    close=QComboBox()
    close.setObjectName('close_behavior')
    for title,value in (('每次启动首次关闭时询问','ask'),('关闭时最小化到托盘','tray'),('关闭时直接退出','exit')):
        close.addItem(title,value)
    close.setCurrentIndex(max(0,close.findData(settings.get('close_behavior','ask'))))
    forms['资料安全'].addRow('关闭窗口',close)
    forms['资料安全'].addRow(label('开机启动与协议唤起在「启动与唤起」页中设置。','muted'))
    import sys as _sys
    frozen = getattr(_sys, 'frozen', False)
    autostart_item = QCheckBox('开机启动')
    autostart_item.setChecked(_autostart_state())
    autostart_item.setEnabled(frozen)
    forms['启动与唤起'].addRow(autostart_item)
    forms['启动与唤起'].addRow(label('登录 Windows 后自动启动新版（未打包的运行环境无法设置）。','muted'))
    protocol_item = button('注册 jingzai:// 唤起新版', window.register_protocol)
    forms['启动与唤起'].addRow(protocol_item)
    forms['启动与唤起'].addRow(label('注册后可在浏览器地址栏输入 jingzai://… 唤起新版。','muted'))
    forms['快捷键'].addRow(label('软件内：Ctrl+F 搜索，F3/Shift+F3 跳转命中，Ctrl+Shift+R 专注阅读，Esc 关闭子窗。','muted'))
    error=label('','muted')
    error.setWordWrap(True)
    error.hide()
    outer.addWidget(error)
    controls=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
    controls.rejected.connect(dialog.reject)
    def accept():
        url=QUrl(fields['ollama_base'].text().strip())
        if url.scheme() not in ('http','https') or not url.host():
            error.setText('服务地址需包含 http:// 或 https:// 及有效主机。')
            error.show();tabs.setCurrentIndex(2);fields['ollama_base'].setFocus();return
        invalid=[]
        for number,line in enumerate(synonyms.toPlainText().splitlines(),1):
            if not line.strip() or line.lstrip().startswith('#'):continue
            parts=line.split('=',1)
            if len(parts)!=2 or not all(part.strip() for part in parts):invalid.append(str(number))
        if invalid:
            error.setText('同义词第 '+ '、'.join(invalid[:8])+' 行未填完整；请按“主词 = 别名1, 别名2”填写。')
            error.show();tabs.setCurrentIndex(1);synonyms.setFocus();return
        dialog.accept()
    controls.accepted.connect(accept)
    outer.addWidget(controls)
    if dialog.exec()!=QDialog.DialogCode.Accepted:return
    settings.update(dark=dark.isChecked(),preview_font=font.value(),dialog_font=ui.value(),
        dialog_text_font=text.value(),autowatch=watch.isChecked(),qa_auto=qa_auto.isChecked(),
        qa_cpu_only=cpu.isChecked(),max_backups=backups.value(),custom_synonyms=synonyms.toPlainText(),
        close_behavior=close.currentData())
    settings.update({key:field.text().strip() for key,field in fields.items()})
    window._close_choice=None if close.currentData()=='ask' else close.currentData()
    if frozen and autostart_item.isChecked() != _autostart_state():
        _apply_autostart(autostart_item.isChecked(), window.store.root)
    window.store.save()
    window.apply_theme()
    if getattr(window,'dialog_display',None):
        for top in QApplication.topLevelWidgets():
            if isinstance(top,QDialog) and top.isVisible():window.dialog_display.polish(top)
    window.start_watcher()
    for key,attribute in (('quick_hotkey','hotkeys'),('clip_hotkey','clip_hotkeys')):
        if getattr(window,attribute,None):getattr(window,attribute).configure(settings[key])

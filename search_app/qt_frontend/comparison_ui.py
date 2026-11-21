"""左右版本、差异统计、同步滚动和逐处导航。"""
import html
from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QCheckBox,QDialog,QDialogButtonBox,QHBoxLayout,QSplitter,QTextBrowser,QVBoxLayout,QWidget
from .widgets import button,label


def show_comparison(window,a,b,blocks):
    dialog=QDialog(window)
    dialog.setWindowTitle('版本对比')
    dialog.resize(1260,760)
    layout=QVBoxLayout(dialog)
    counts={kind:sum(len(block['b'] if kind=='insert' else block['a']) for block in blocks if block['type']==kind)
        for kind in ('delete','insert','replace')}
    bar=QHBoxLayout()
    bar.addWidget(label(f"删除 {counts['delete']} 行 · 新增 {counts['insert']} 行 · 修改 {counts['replace']} 行"),1)
    bar.addWidget(button('交换新旧',lambda:(window.compare_pair(b,a),dialog.close())))
    sync=QCheckBox('同步滚动')
    sync.setChecked(True)
    bar.addWidget(sync)
    position=label('')
    bar.addWidget(position)
    layout.addLayout(bar)
    splitter=QSplitter()
    views=[]
    changes=[i for i,block in enumerate(blocks) if block['type'] not in ('equal','gap')]
    dark=window.store.settings.get('dark',False)
    colors={'delete':('#4c2930','#ffd3d3') if dark else ('#fce4e4','#922f2f'),
        'insert':('#203e30','#c4f2d5') if dark else ('#e2f4e8','#24643f'),
        'replace':('#493f2a','#ffe5a8') if dark else ('#fff2cf','#775316')}
    names={'delete':'删除','insert':'新增','replace':'修改'}
    for side,record,role in (('a',a,'旧版'),('b',b,'新版')):
        box=QWidget()
        column=QVBoxLayout(box)
        heading=label(role+'：'+record['name'],'section')
        heading.setWordWrap(True)
        heading.setToolTip(record['path'])
        column.addWidget(heading)
        browser=QTextBrowser()
        browser.setOpenLinks(False)
        fragments=[]
        for number,block in enumerate(blocks):
            kind=block['type']
            if kind=='gap':
                fragments.append('<p>……相同内容已折叠……</p>');continue
            fragments.append(f'<a name="diff-{number}"></a>')
            lines=block[side]
            for row in range(max(len(block['a']),len(block['b']))):
                line,text=lines[row] if row<len(lines) else ('','')
                background,foreground=colors.get(kind,('transparent','inherit'))
                prefix=names.get(kind,'')+' · ' if kind!='equal' and row==0 else ''
                fragments.append(f'<p style="margin:3px 0;padding:4px;background:{background};color:{foreground};">'
                    + html.escape(prefix+(f'第 {line} 行　' if line else '　')+text)+'</p>')
        size=window.store.settings.get('dialog_text_font',12)
        browser.setHtml(f'<body style="font-family:Microsoft YaHei UI;font-size:{size}pt;">'+''.join(fragments)+'</body>')
        column.addWidget(browser,1)
        splitter.addWidget(box)
        views.append(browser)
    layout.addWidget(splitter,1)
    def scroll(source,target,value):
        if not sync.isChecked():return
        with QSignalBlocker(target):target.setValue(round(value/max(1,source.maximum())*target.maximum()))
    for source,target in ((views[0].verticalScrollBar(),views[1].verticalScrollBar()),(views[1].verticalScrollBar(),views[0].verticalScrollBar())):
        source.valueChanged.connect(lambda value,source=source,target=target:scroll(source,target,value))
    current=[-1]
    def jump(step):
        if not changes:return
        current[0]=(current[0]+step)%len(changes)
        for view in views:
            with QSignalBlocker(view.verticalScrollBar()):view.scrollToAnchor('diff-'+str(changes[current[0]]))
        position.setText(f'差异 {current[0]+1}/{len(changes)}')
    previous=button('上一处差异',lambda:jump(-1))
    following=button('下一处差异',lambda:jump(1))
    previous.setEnabled(bool(changes));following.setEnabled(bool(changes))
    position.setText(f'{len(changes)} 处差异' if changes else '两份文件正文相同')
    controls=QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    controls.addButton(previous,QDialogButtonBox.ButtonRole.ActionRole)
    controls.addButton(following,QDialogButtonBox.ButtonRole.ActionRole)
    controls.rejected.connect(dialog.close)
    layout.addWidget(controls)
    return window.keep_dialog(dialog)

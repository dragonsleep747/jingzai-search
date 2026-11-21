"""隔离验收：检查极简布局、片段伸缩及功能入口，不操作用户资料。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from .result_snippets import SNIPPET, folded_excerpt


def check_minimal_layout(window, output):
    output.mkdir(parents=True, exist_ok=True)
    checks = []
    def check(condition, message):
        if not condition:
            raise AssertionError(message)
        checks.append(message)
    QApplication.processEvents()
    check(not window.menuBar().isVisible() and not window.menuBar().actions(), '顶部没有业务菜单')
    check(set(window.nav) == {'search', 'library', 'bookmarks'} and all(item.icon().isNull() for item in window.nav.values()),
          '侧栏仅三个文字入口与菜单')
    check([window.search_mode.itemText(i) for i in range(2)]==['探索','智答'],'模式切换集中在搜索输入区')
    check(not window.statusBar().isVisible(), '不保留独立底部状态栏')
    check(not window.options_scroll.isVisible() and not window.mode.isVisible(), '搜索匹配与筛选选项默认折叠')
    area = window.reader_stack.geometry()
    ratio = window.reader_stack.height() / window.pages.height()
    check(ratio >= .90, '正文净高度占客户区至少 90%')
    window.options_button.click()
    QApplication.processEvents()
    check(window.options_scroll.isVisible() and window.type_filter.isVisible(), '常用筛选能够打开')
    window.advanced_toggle.click()
    QApplication.processEvents()
    check(window.mode.isVisible(),'高级筛选能够打开')
    check(window.reader_stack.geometry() == area, '展开筛选不挤压正文高度')
    window.grab().save(str(output / 'options-expanded.png'))
    window.options_button.click()
    QApplication.processEvents()
    check(window.reader_header.height() <= 34, '文件名称位置与操作合并为单条细栏')
    window.grab().save(str(output / 'minimal-reader.png'))
    menu = window.build_application_menu()
    check(all(name in [action.text() for action in menu.actions()] for name in ('任务与进度', '工具中心', '设置', '帮助')),
          '菜单保留任务工具设置帮助')
    menu.popup(window.menu_button.mapToGlobal(window.menu_button.rect().topRight()))
    QApplication.processEvents()
    menu.grab().save(str(output / 'application-menu.png'))
    menu.hide()
    tree = window.discovery_results if window.result_stack.currentWidget() is window.discovery_results else window.results
    root = QTreeWidgetItem(['长片段验收', '1'])
    text = '背景说明' * 80 + '独有验收词' + ' 本条规范的完整内容用于检查展开后文字不再截断。' * 30
    child = QTreeWidgetItem([text, ''])
    child.setData(0, SNIPPET, {'text': text, 'terms': ['独有验收词'], 'location': 'L125'})
    root.addChild(child)
    tree.insertTopLevelItem(0, root)
    root.setExpanded(True)
    tree.scrollToItem(child)
    QApplication.processEvents()
    tree.doItemsLayout()
    index = tree.indexFromItem(child)
    before = tree.visualRect(index).height()
    excerpt, _ = folded_excerpt(text, ['独有验收词'])
    check('独有验收词' in excerpt[:40], '折叠片段优先展示命中附近')
    window.grab().save(str(output / 'snippet-collapsed.png'))
    generation = window._preview_generation
    tree.change_expansion(index)
    QApplication.processEvents()
    check(tree.visualRect(index).height() > before, '长命中片段能够展开')
    check(window._preview_generation == generation, '展开片段不重新打开正文')
    window.grab().save(str(output / 'snippet-expanded.png'))
    tree.change_expansion(index, collapse=True)
    check(tree.visualRect(index).height() == before, '长命中片段能够收起')
    tree.takeTopLevelItem(0)
    window.toggle_reading_focus()
    QApplication.processEvents()
    check(window.result_column.isHidden(), '专注阅读能够收起结果栏')
    window.grab().save(str(output / 'reading-focus.png'))
    window.toggle_reading_focus()
    QApplication.processEvents()
    return {'checks': checks, 'reader_height_ratio': ratio, 'header_height': window.reader_header.height(),
            'window_size': [window.width(), window.height()], 'dpr': window.devicePixelRatioF()}

# -*- coding: utf-8 -*-
"""临时 GUI 冒烟：库管理分类列/筛选/自查/分类管理 + 未分类弹窗"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk

import main as appmod

# 源码模式用根目录真实库（与打包版一致）
appmod.DB_PATH = r'.\index.db'
appmod.App._start_hotkeys = lambda self: None   # 避免热键被占用时弹模态

# DnD 可用时 root 必须用 tkinterdnd2.Tk()（与主入口一致）
if appmod._DND_AVAILABLE:
    root = appmod.TkinterDnD.Tk()
else:
    root = tk.Tk()
root.withdraw()
app = appmod.App(root)
# 冒烟无人值守：关掉启动时的「待入库确认」模态框（否则 mainloop 会阻塞等待点击）
app.cfg['inbox_auto_check'] = False
ok = fail = 0


def check(name, cond):
    global ok, fail
    print(('PASS ' if cond else 'FAIL ') + name)
    ok, fail = ok + (1 if cond else 0), fail + (1 if not cond else 0)


# 1. 库管理窗口：分项列 + 左侧分项树栏 + 按钮
app.show_library()
lib_win = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
tree = lib_win._tree
cols = tree['columns']
check('库管理含分项列', 'category' in cols)
n_rows = len(tree.get_children())
check('列表有数据', n_rows > 0)
cat_vals = {tree.set(i, 'category') for i in tree.get_children()}
print('  当前列表分项值:', cat_vals or '{空}')
check('左侧分项树栏存在', hasattr(lib_win, '_cat_tree'))
cat_items = lib_win._cat_tree.get_children('')
check('分项树含 全部文件/未分类',
      '__all__' in cat_items and '__none__' in cat_items)
print('  分项树节点:', [lib_win._cat_tree.item(i, 'text')
                       for i in cat_items])
has_btn = []


def _walk_btns(w):
    for c in w.winfo_children():
        if isinstance(c, tk.ttk.Button):
            has_btn.append(c.cget('text'))
        elif isinstance(c, tk.ttk.Menubutton):
            has_btn.append(c.cget('text'))
            m = c.cget('menu')
            if m:
                try:
                    end_idx = m.index('end')
                    if end_idx is not None:
                        for idx in range(end_idx + 1):
                            has_btn.append(m.entrycget(idx, 'label'))
                except Exception:
                    pass
        _walk_btns(c)
_walk_btns(lib_win)
check('高级工具按钮存在',
      any('高级工具' in t for t in has_btn))

# 1b. 点击分项树节点筛选
lib_win._cat_tree.selection_set('__none__')
lib_win._cat_tree.update()
check('分项树点击筛选生效', lib_win._cat_filter == '__none__')
lib_win._cat_tree.selection_set('__all__')
lib_win._cat_tree.update()
check('切回全部', lib_win._cat_filter == '')

# 2. 分项筛选
try:
    lib_win._cat_filter = '__none__'
    app._fill_library_tree(tree, '__none__')
    check('未分类筛选可用', isinstance(len(tree.get_children()), int))
finally:
    lib_win._cat_filter = ''
    app._fill_library_tree(tree, '')

# 3. 分类管理对话框：行编辑器预填默认 5 个分项
app._show_category_manager(lib_win)
mgr = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
check('分类管理窗口打开', '分类管理' in mgr.title())

# 4. 分类自查对话框（后台自查线程，需短暂 mainloop 才能跑 after 回调）
app._show_category_audit(lib_win)
audit = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
check('分类自查窗口打开', audit.title() == '分类自查')


def _grab_stat():
    import re as _re
    try:
        found = []

        def _walk_lbl(w):
            for c in w.winfo_children():
                try:
                    if isinstance(c, tk.ttk.Label):
                        found.append(c.cget('text'))
                except Exception:
                    pass
                _walk_lbl(c)
        _walk_lbl(audit)
        stat = next((t for t in found if _re.match(r'共 \d+ 个文件', t)), '')
        print('  自查统计:', (stat or found[:3])[:100])
        check('自查完成有统计', bool(stat))
    except Exception as e:
        print('  自查统计读取失败:', repr(e))
        check('自查完成有统计', False)
    finally:
        root.quit()
root.after(1500, _grab_stat)
root.mainloop()

# 6. 未分类弹窗：逐行选择 + 全部设为 + 保存
app._ask_categories_popup([r'd:\x\测试文件A.docx', r'd:\x\测试文件B.pdf'])
pop = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
check('弹窗标题正确', pop.title() == '为文件选择分类')
cbs, save_btns = [], []


def _walk_pop(w):
    for c in w.winfo_children():
        if isinstance(c, tk.ttk.Combobox):
            cbs.append(c)
        if isinstance(c, tk.ttk.Button) and c.cget('text') == '保存':
            save_btns.append(c)
        _walk_pop(c)
_walk_pop(pop)
check('弹窗每个文件一行下拉', len(cbs) == 3)   # 2 行 + 全部设为 1 个
check('保存按钮存在', len(save_btns) == 1)
cbs[0].set('国家标准文件')
save_btns[0].invoke()
check('保存后弹窗关闭', not pop.winfo_exists())

for w in list(root.winfo_children()):
    if isinstance(w, tk.Toplevel):
        w.destroy()
root.destroy()
sys.stdout.flush()
print(f'\n{ok} passed, {fail} failed')
os._exit(1 if fail else 0)

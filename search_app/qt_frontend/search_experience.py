"""搜索筛选、排序、信息密度和原文阅读的原生交互。"""
import html
import math
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, QTimer
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

import core
from .widgets import button, label


def rank_groups(groups, details, query, order='relevance'):
    terms = [term.casefold() for term in core.kw_terms(query)]
    def relevance(group):
        meta = details.get(group['path'], {})
        title = group['name'].casefold()
        title_hits = sum(term in title for term in terms)
        identity_hits = sum(term in str(meta.get('doc_no', '')).casefold() for term in terms)
        density = group['hits'] / max(1, meta.get('line_count', group['hits']))
        best = max((float(row.get('score', 1)) for row in group.get('matches', [])), default=1)
        return title_hits * 30 + identity_hits * 20 + density * 5 + math.log1p(group['hits']) + best
    def key(group):
        meta = details.get(group['path'], {})
        if order == 'name':
            return group['name'].casefold(), group['path'].casefold()
        value = group['hits'] if order == 'hits' else meta.get('effective_date', '') if order == 'date' else (
            meta.get('mtime', 0) or 0) if order == 'modified' else relevance(group)
        # 降序字段不反转文件名，以获得稳定的同值顺序。
        return value
    if order == 'name':
        return sorted(groups, key=key)
    ordered = sorted(groups, key=lambda group: (group['name'].casefold(), group['path'].casefold()))
    return sorted(ordered, key=key, reverse=True)


class SearchExperienceMixin:
    def build_search_controls(self, form, filters, layout):
        self.chart_toggle = button('命中分布')
        self.chart_toggle.setCheckable(True)
        self.chart_toggle.setChecked(bool(self.store.settings.get('show_distribution', False)))
        self.chart_toggle.toggled.connect(self.toggle_distribution)
        self.search_heading.layout().insertWidget(1, self.chart_toggle)
        self.density = QComboBox()
        self.density.addItem('紧凑', True)
        self.density.addItem('舒适', False)
        self.density.setCurrentIndex(0 if self.store.settings.get('compact', True) else 1)
        self.density.setMaximumWidth(95)
        self.density.currentIndexChanged.connect(self.density_changed)
        self.search_heading.layout().insertWidget(2, self.density)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.facet_combos = {}
        for field, title in [('分类', '全部分类'), ('日期', '全部年份'), ('状态', '全部状态')]:
            combo = QComboBox()
            combo.setMinimumWidth(115)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(8)
            combo.addItem(title, '')
            if field == '状态':
                for text in ['现行', '废止', '替代']:
                    combo.addItem(text, text)
            combo.currentIndexChanged.connect(lambda _, name=field: self.choose_facet(name))
            bar.addWidget(combo, 2 if field == '分类' else 1)
            self.facet_combos[field] = combo
        bar.addWidget(button('清除条件', self.clear_facets))
        form.addLayout(bar)
        self.filter_chips = QLabel()
        self.filter_chips.setObjectName('filterChips')
        self.filter_chips.setWordWrap(True)
        self.filter_chips.setOpenExternalLinks(False)
        self.filter_chips.linkActivated.connect(self.remove_facet)
        form.addWidget(self.filter_chips)
        self.filter_chips.hide()
        for field in self.advanced_fields.values():
            field.textChanged.connect(self.update_filter_chips)

    def build_sort_control(self):
        combo = QComboBox()
        for title, value in [('相关程度', 'relevance'), ('命中次数', 'hits'), ('施行日期↓', 'date'),
                              ('最近更新↓', 'modified'), ('文件名称', 'name')]:
            combo.addItem(title, value)
        combo.setCurrentIndex(max(0, combo.findData(self.store.settings.get('result_order', 'relevance'))))
        combo.setMaximumWidth(140)
        combo.setToolTip('相关程度综合标题、文号、正文命中密度和匹配程度；排序不改变检索范围。')
        combo.currentIndexChanged.connect(self.sort_results)
        self.sort_order = combo
        return combo

    def refresh_facets(self):
        def done(facets):
            for field in ('分类', '日期'):
                combo = self.facet_combos[field]
                current = self.advanced_fields[field].text()
                with QSignalBlocker(combo), QSignalBlocker(combo.lineEdit()):
                    combo.clear()
                    combo.addItem('全部分类' if field=='分类' else '不限日期', '')
                    for value, count in facets[field]:
                        combo.addItem(f'{value}（{count}）', value)
                    combo.setCurrentIndex(max(0, combo.findData(current)))
                    combo.setEditText(current)
        self.jobs.submit('读取筛选条件', lambda progress, stop: self.store.facets(), done,
                         error=self.show_error, visible=False)

    def choose_facet(self, field):
        if self.facet_combos[field].currentIndex() < 0:
            return
        value = self.facet_combos[field].currentData() or ''
        if field=='状态' and value:
            value={'现行':'有效','废止':'已废止','替代':'已替代'}.get(value,value)
        self.advanced_fields[field].setText(value)
        if field == '状态' and value:
            self.active_filter.setChecked(value in ('现行','有效','现行有效'))
        self.update_filter_chips()
        self.search_debounce.start()

    def remove_facet(self, field):
        if field=='_more':
            self.options_button.setChecked(True)
            self.advanced_toggle.setChecked(True)
            return
        if field=='_clear':
            self.reset_search_options()
            return
        boxes={'_active':self.active_filter,'_synonym':self.synonym_filter,'_case':self.case_filter}
        editors={'_ext':self.catalog_ext,'_root':self.catalog_root}
        combos={'_type':self.type_filter,'_mode':self.mode,'_match':self.catalog_scope}
        if field in boxes:boxes[field].setChecked(False)
        if field in editors:editors[field].clear()
        if field in combos:combos[field].setCurrentIndex(0)
        if field in self.advanced_fields:
            if field in self.facet_combos:
                with QSignalBlocker(self.facet_combos[field]):
                    self.facet_combos[field].setCurrentIndex(0)
            self.advanced_fields[field].clear()
            self.update_filter_chips()
            self.search_debounce.start()

    def clear_facets(self):
        for combo in self.facet_combos.values():
            with QSignalBlocker(combo):
                combo.setCurrentIndex(0)
        for field in self.advanced_fields.values():
            field.clear()
        self.update_filter_chips()
        self.search_debounce.start()

    def update_filter_chips(self, *_):
        if hasattr(self,'options_button'):
            self.update_option_badge()
            return
        chips = []
        for name, field in self.advanced_fields.items():
            value = field.text().strip()
            if value:
                chips.append(f'<a style="text-decoration:none;color:#3972b9;" href="{name}">'
                             + html.escape(name + '：' + value) + ' ×</a>')
        self.filter_chips.setText('当前条件　' + '　 ·　 '.join(chips))
        self.filter_chips.setVisible(bool(chips))
        if hasattr(self, 'options_button'):
            self.update_option_badge()

    def toggle_distribution(self, checked):
        if hasattr(self, 'distribution'):
            self.distribution.setVisible(bool(checked and self.groups))
        self.store.settings['show_distribution'] = bool(checked)
        self.store.save()

    def density_changed(self, *_):
        self.store.settings['compact'] = bool(self.density.currentData())
        self.store.save()
        self.apply_theme()

    def sort_results(self, *_):
        self.store.settings['result_order'] = self.sort_order.currentData()
        self.store.save()
        if hasattr(self,'discovery_scope') and self.discovery_scope_kind()!='content':
            if getattr(self, 'discovery_content_hits', None):
                self.render_discovery_results()
            return
        if getattr(self, '_last_search_result', None):
            self._sort_only = True
            try:
                self.render_results(self._last_search_result)
            finally:
                self._sort_only = False

    def render_results(self, result):
        self._last_explore_groups=list(result['groups'])
        old_filter = self.result_filter.text() if getattr(self, '_sort_only', False) else ''
        self._pending_selection = (self.current['path'], self.current['line']) if (
            getattr(self, '_sort_only', False) and self.current) else None
        self._last_search_result = result
        self._search_details = result.get('details', {})
        ordered = rank_groups(result['groups'], self._search_details, self.query, self.sort_order.currentData())
        self._render_results(dict(result, groups=ordered))
        self.distribution.setVisible(bool(ordered and self.chart_toggle.isChecked()))
        self.result_filter.setText(old_filter)

    def result_tooltip(self, group):
        meta = self._search_details.get(group['path'], {})
        terms = [term.casefold() for term in core.kw_terms(self.query)]
        origins = []
        if any(term in group['name'].casefold() for term in terms):
            origins.append('标题命中')
        if any(term in str(meta.get('doc_no', '')).casefold() for term in terms):
            origins.append('文号命中')
        if group.get('matches'):
            origins.append('正文 / 同义词命中')
        return '\n'.join([group['path'], ' · '.join(origins), '分类：' + meta.get('category', ''),
                          '施行日期：' + meta.get('effective_date', '')])

    def update_document_reader(self, payload):
        is_pdf = Path(payload['path']).suffix.lower() == '.pdf' and payload['exists']
        with QSignalBlocker(self.reader_kind):
            self.reader_kind.setEnabled(is_pdf)
            self.reader_kind.setCurrentIndex(1 if is_pdf and self.store.settings.get('pdf_original_preferred', True) else 0)
        self.show_reader_kind()

    def reader_type_changed(self, *_):
        if self.current and Path(self.current['path']).suffix.lower() == '.pdf':
            self.store.settings['pdf_original_preferred'] = self.reader_kind.currentIndex() == 1
            self.store.save()
        self.show_reader_kind()

    def show_reader_kind(self):
        use_pdf = self.current and self.reader_kind.currentIndex() == 1 and self.current['exists']
        self.reader_stack.setCurrentIndex(1 if use_pdf else 0)
        if use_pdf:
            terms = core.kw_terms(self.query)
            self.pdf_reader.open(self.current['path'], self.current.get('page') or 1, terms[0] if terms else '')
        elif self.pdf_reader.path:
            self.pdf_reader.close_document()

    def pdf_page_changed(self, page):
        if not self.current or self.reader_stack.currentIndex() != 1 or page < 1:
            return
        path = self.current['path']
        if (self.current.get('page') or 1) == page:
            return
        self._pdf_page_request = (path, page)
        if not hasattr(self, '_pdf_page_timer'):
            self._pdf_page_timer = QTimer(self)
            self._pdf_page_timer.setSingleShot(True)
            self._pdf_page_timer.setInterval(220)
            self._pdf_page_timer.timeout.connect(self.locate_pdf_page)
        self._pdf_page_timer.start()

    def locate_pdf_page(self):
        path, page = self._pdf_page_request
        def done(line):
            if line and self.current and self.current['path'] == path and self.pdf_reader.page.value() == page:
                self.open_preview(path, line, history=False)
        self.jobs.submit('同步原文页码', lambda progress, stop: self.store.pdf_page_line(path, page),
                         done, error=self.show_error, visible=False)

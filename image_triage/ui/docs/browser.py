"""The in-app documentation browser window.

A modeless, wiki-style reader built on ``QTextBrowser``: a searchable category
tree on the left, a polished reading pane on the right, and Back / Forward /
Home navigation across ``doc:`` cross-links. Styling is derived from the live
widget palette so it matches whatever theme the app is running.
"""

from __future__ import annotations

from textwrap import dedent

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPalette, QTextCursor, QTextOption
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollBar,
    QSplitter,
    QStackedWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..icons import build_arrow_icon, build_doc_category_icon, build_home_icon
from .registry import DocRegistry, get_registry

_ARTICLE_ROLE = Qt.ItemDataRole.UserRole + 1

# Maps each documentation category to a drawn monochrome line icon, matching the
# app's toolbar icon style instead of colored emoji.
_CATEGORY_ICON_KIND = {
    "getting-started": "rocket",
    "reviewing": "image",
    "ai-culling": "sparkle",
    "adapters": "target",
    "library": "books",
    "export": "box",
    "settings": "sliders",
    "reference": "book",
}
_CATEGORY_ICON_COLOR = QColor(190, 198, 208)


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )


class _OverlayScrollBar(QScrollBar):
    """A thin, translucent vertical scrollbar that floats over a scroll area's
    viewport instead of consuming layout width.

    It mirrors the host's real (hidden) scrollbar, repositions itself when the
    host resizes, and auto-hides shortly after scrolling stops — the unobtrusive
    macOS-style overlay. Because it takes no layout width, content can sit flush
    wall-to-wall and stay centered.
    """

    _WIDTH = 10
    _MARGIN = 3

    def __init__(self, host: QAbstractScrollArea, *, handle_color: str) -> None:
        super().__init__(Qt.Orientation.Vertical, host)
        self._host = host
        self.setObjectName("docsOverlayScroll")
        self.setStyleSheet(
            "QScrollBar#docsOverlayScroll { background: transparent; width: %dpx; margin: 0px; }"
            " QScrollBar#docsOverlayScroll::handle:vertical {"
            f" background: {handle_color}; border-radius: 4px; min-height: 36px; }}"
            " QScrollBar#docsOverlayScroll::add-line:vertical,"
            " QScrollBar#docsOverlayScroll::sub-line:vertical { height: 0px; background: transparent; }"
            " QScrollBar#docsOverlayScroll::add-page:vertical,"
            " QScrollBar#docsOverlayScroll::sub-page:vertical { background: transparent; }"
            % self._WIDTH
        )
        self._src = host.verticalScrollBar()
        self._src.rangeChanged.connect(self._on_range)
        self._src.valueChanged.connect(self._on_src_value)
        self.valueChanged.connect(self._src.setValue)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(1200)
        self._hide_timer.timeout.connect(self.hide)
        host.installEventFilter(self)
        host.viewport().installEventFilter(self)
        self._on_range(self._src.minimum(), self._src.maximum())
        self.hide()

    def _scrollable(self) -> bool:
        return self._src.maximum() > self._src.minimum()

    def _on_range(self, low: int, high: int) -> None:
        self.setRange(low, high)
        self.setPageStep(self._src.pageStep())
        self.setSingleStep(max(1, self._src.singleStep()))
        self.setValue(self._src.value())
        self._reposition()

    def _on_src_value(self, value: int) -> None:
        # Keep the range current before mirroring; the host's range can change
        # after the overlay is built (content/layout settling).
        if self.maximum() != self._src.maximum() or self.minimum() != self._src.minimum():
            self.setRange(self._src.minimum(), self._src.maximum())
            self.setPageStep(self._src.pageStep())
        if self.value() != value:
            self.setValue(value)
        self._flash()

    def _reposition(self) -> None:
        self.setGeometry(
            self._host.width() - self._WIDTH - self._MARGIN,
            self._MARGIN,
            self._WIDTH,
            self._host.height() - 2 * self._MARGIN,
        )

    def _flash(self) -> None:
        if not self._scrollable():
            self.hide()
            return
        self._reposition()
        self.show()
        self.raise_()
        self._hide_timer.start()

    def eventFilter(self, obj: object, event: QEvent) -> bool:
        kind = event.type()
        if kind == QEvent.Type.Resize:
            self._reposition()
        elif kind in (QEvent.Type.Wheel, QEvent.Type.Enter):
            self._flash()
        return False


class DocsBrowserDialog(QDialog):
    def __init__(self, parent=None, *, registry: DocRegistry | None = None) -> None:
        super().__init__(parent)
        self._registry = registry or get_registry()
        self._history: list[str] = []
        self._history_index = -1
        self._current_article_id = ""
        self._suppress_tree_signal = False

        self.setWindowTitle("Image Triage — Documentation")
        self.setModal(False)
        self.resize(1060, 760)
        self.setMinimumSize(760, 480)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        root.addLayout(self._build_top_bar())

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_nav_panel())
        splitter.addWidget(self._build_content_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 760])
        root.addWidget(splitter, 1)

        self._apply_document_css()
        self._populate_tree()
        home = self._registry.home_article_id()
        if home:
            self.navigate(home)

    # -- Construction -----------------------------------------------------

    def _build_top_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(6)

        # Monochrome line icons in the toolbar's gray, matching the app's icon
        # style instead of colored emoji. Drawn icons center cleanly in the box.
        icon_color = QColor(190, 198, 208)
        icon_px = 22

        def _make_nav_button(icon, tooltip: str, slot) -> QPushButton:
            button = QPushButton(self)
            button.setFixedSize(40, 34)
            button.setIcon(icon)
            button.setIconSize(QSize(icon_px, icon_px))
            button.setStyleSheet("QPushButton { padding: 0px; }")
            button.setToolTip(tooltip)
            button.clicked.connect(slot)
            return button

        self.back_button = _make_nav_button(
            build_arrow_icon(icon_color, pointing_left=True, pixel_size=icon_px), "Back", self._go_back
        )
        self.forward_button = _make_nav_button(
            build_arrow_icon(icon_color, pixel_size=icon_px), "Forward", self._go_forward
        )
        self.home_button = _make_nav_button(
            build_home_icon(icon_color, pixel_size=icon_px), "Home", self._go_home
        )

        self.search_field = QLineEdit(self)
        self.search_field.setPlaceholderText("Search documentation…")
        self.search_field.setClearButtonEnabled(True)
        self.search_field.textChanged.connect(self._on_search_changed)

        bar.addWidget(self.back_button)
        bar.addWidget(self.forward_button)
        bar.addWidget(self.home_button)
        bar.addSpacing(8)
        bar.addWidget(self.search_field, 1)
        return bar

    def _build_nav_panel(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        # Symmetric horizontal inset. With the real scrollbar hidden (an overlay
        # one floats instead), nothing consumes width on the right, so this gives
        # the selection pill an identical gap on both sides.
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(0)

        self.nav_stack = QStackedWidget(panel)

        self.tree = QTreeWidget(panel)
        self.tree.setObjectName("docsNavTree")
        self.tree.setHeaderHidden(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setUniformRowHeights(True)
        self.tree.setRootIsDecorated(False)
        # Indentation 0 removes the branch/indent gutter entirely. The native
        # style draws a thin accent indicator in that gutter for selected child
        # rows (the persistent "blue bar"), and it can't be styled away because
        # an app-level stylesheet makes setPalette inert. Child rows fake their
        # indent with leading space in the label instead (see _populate_tree).
        self.tree.setIndentation(0)
        self.tree.setIconSize(QSize(18, 18))
        # Hide the built-in scrollbar so it consumes no width; a translucent
        # overlay scrollbar (created in _apply_document_css) floats instead.
        self.tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.currentItemChanged.connect(self._on_tree_selection)
        self.nav_stack.addWidget(self.tree)

        self.search_results = QListWidget(panel)
        self.search_results.setObjectName("docsSearchResults")
        self.search_results.setWordWrap(True)
        self.search_results.itemActivated.connect(self._on_result_chosen)
        self.search_results.itemClicked.connect(self._on_result_chosen)
        self.nav_stack.addWidget(self.search_results)

        layout.addWidget(self.nav_stack, 1)
        return panel

    def _build_content_panel(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.breadcrumb = QLabel("", panel)
        self.breadcrumb.setObjectName("docsBreadcrumb")
        self.breadcrumb.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.breadcrumb)

        self.browser = QTextBrowser(panel)
        self.browser.setObjectName("docsContentView")
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.setUndoRedoEnabled(False)
        self.browser.setReadOnly(True)
        self.browser.anchorClicked.connect(self._on_anchor_clicked)
        self.browser.document().setDocumentMargin(20)
        layout.addWidget(self.browser, 1)
        return panel

    # -- Theming ----------------------------------------------------------

    def _apply_document_css(self) -> None:
        palette = self.palette()
        base = palette.base().color()
        text = palette.text().color()
        accent = palette.highlight().color()
        if accent.lightness() < 40 or accent == base:
            accent = QColor("#d3b15b")  # app gold, used when the theme has no usable highlight
        is_dark = base.lightness() < 128

        muted = _mix(text, base, 0.45).name()
        rule = _mix(text, base, 0.78).name()
        code_bg = _mix(base, text, 0.10 if is_dark else 0.06).name()
        callout_bg = _mix(base, accent, 0.12).name()
        accent_hex = accent.name()
        heading = _mix(text, base, 0.04).name()

        css = f"""
        body {{ color: {text.name()}; line-height: 165%; }}
        h1 {{
            color: {heading};
            font-size: 25px;
            margin-top: 2px;
            margin-bottom: 15px;
            padding-bottom: 8px;
            border-bottom: 2px solid {accent_hex};
        }}
        h2 {{
            color: {heading};
            font-size: 19px;
            margin-top: 22px;
            margin-bottom: 8px;
            padding-bottom: 4px;
            border-bottom: 1px solid {rule};
        }}
        h3 {{ color: {heading}; font-size: 16px; margin-top: 19px; margin-bottom: 6px; }}
        p {{ margin-top: 0; margin-bottom: 12px; }}
        a {{ color: {accent_hex}; text-decoration: none; }}
        ul, ol {{ margin-top: 4px; margin-bottom: 14px; }}
        li {{ margin-bottom: 6px; }}
        code {{
            background-color: {code_bg};
            padding: 1px 5px;
        }}
        pre {{
            background-color: {code_bg};
            padding: 10px 12px;
            margin-bottom: 14px;
        }}
        blockquote {{
            background-color: {callout_bg};
            border-left: 3px solid {accent_hex};
            margin: 0 0 14px 0;
            padding: 8px 14px;
            color: {text.name()};
        }}
        table {{ border-collapse: collapse; margin-bottom: 14px; }}
        th, td {{ border: 1px solid {rule}; padding: 5px 10px; }}
        th {{ background-color: {code_bg}; }}
        """
        self.browser.document().setDefaultStyleSheet(dedent(css).strip())
        self.browser.setStyleSheet("QTextBrowser#docsContentView { font-size: 14px; }")

        self.breadcrumb.setStyleSheet(f"color: {muted}; font-size: 13px;")
        self.search_results.setStyleSheet(
            f"QListWidget#docsSearchResults {{ font-size: 14px; outline: 0; border: none; }}"
            f" QListWidget#docsSearchResults::item {{ padding: 7px 6px; border-bottom: 1px solid {rule}; }}"
        )
        # Take full control of item colors and selection so the global app
        # stylesheet (and the native style) can't bleed an accent into rows:
        # the blue-tinted article text, the teal selection strip, and the
        # rounded "pill" highlight all came from the inherited theme. Setting
        # selection-background-color makes the native decoration strip match the
        # fill so no accent line shows; border-radius/margin 0 squares it off.
        item_color = text.name()
        sel_bg = _mix(base, text, 0.20).name()
        sel_text = text.name()
        hover_bg = _mix(base, text, 0.08).name()
        self.tree.setStyleSheet(
            f"""
            QTreeWidget#docsNavTree {{
                font-size: 14px;
                outline: 0;
                border: none;
                background: transparent;
                selection-background-color: {sel_bg};
                selection-color: {sel_text};
            }}
            QTreeWidget#docsNavTree::item {{
                padding: 6px 8px;
                margin: 1px 0px;
                border: none;
                border-radius: 7px;
                color: {item_color};
            }}
            QTreeWidget#docsNavTree::item:hover {{ background: {hover_bg}; border-radius: 7px; }}
            QTreeWidget#docsNavTree::item:selected {{
                background: {sel_bg};
                color: {sel_text};
                border: none;
                border-radius: 7px;
            }}
            QTreeWidget#docsNavTree::branch {{ background: transparent; border: none; }}
            """
        )

        # The app palette uses the theme accent (a blue) for the Link role and a
        # blue/teal for the Highlight role; the item view paints rows/indent
        # strips with those. Force both — plus Link — to neutral docs colors so
        # nothing renders blue: Highlight drives the native selection fill
        # (including the indentation strip), Link drives stray accent-tinted text.
        tree_palette = self.tree.palette()
        sel_color = QColor(sel_bg)
        tree_palette.setColor(QPalette.ColorRole.Highlight, sel_color)
        tree_palette.setColor(QPalette.ColorRole.HighlightedText, text)
        tree_palette.setColor(QPalette.ColorRole.Link, text)
        tree_palette.setColor(QPalette.ColorRole.LinkVisited, text)
        self.tree.setPalette(tree_palette)

        if getattr(self, "_overlay_scroll", None) is None:
            handle = f"rgba({text.red()}, {text.green()}, {text.blue()}, 0.42)"
            self._overlay_scroll = _OverlayScrollBar(self.tree, handle_color=handle)

    # -- Tree -------------------------------------------------------------

    def _populate_tree(self) -> None:
        self.tree.clear()
        # Explicit foregrounds guarantee a uniform text color regardless of the
        # palette Link role or stylesheet precedence quirks.
        item_fg = self.palette().text().color()
        for category in self._registry.categories():
            articles = self._registry.articles_in(category.id)
            if not articles:
                continue
            parent = QTreeWidgetItem([category.title])
            parent.setFlags(parent.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            font = parent.font(0)
            font.setBold(True)
            parent.setFont(0, font)
            parent.setForeground(0, item_fg)
            kind = _CATEGORY_ICON_KIND.get(category.id)
            if kind:
                parent.setIcon(0, build_doc_category_icon(kind, _CATEGORY_ICON_COLOR, pixel_size=18))
            self.tree.addTopLevelItem(parent)
            for article in articles:
                # Leading space fakes the nesting indent now that the tree's own
                # indentation is 0 (which removed the blue branch indicator).
                child = QTreeWidgetItem([f"      {article.title}"])
                child.setData(0, _ARTICLE_ROLE, article.id)
                child.setForeground(0, item_fg)
                if article.summary:
                    child.setToolTip(0, article.summary)
                parent.addChild(child)
            parent.setExpanded(True)

    def _select_in_tree(self, article_id: str) -> None:
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            for j in range(parent.childCount()):
                child = parent.child(j)
                if child.data(0, _ARTICLE_ROLE) == article_id:
                    self._suppress_tree_signal = True
                    self.tree.setCurrentItem(child)
                    self._suppress_tree_signal = False
                    return

    def _on_tree_selection(self, current: QTreeWidgetItem | None, _previous) -> None:
        if self._suppress_tree_signal or current is None:
            return
        article_id = current.data(0, _ARTICLE_ROLE)
        if article_id:
            self.navigate(str(article_id))

    # -- Search -----------------------------------------------------------

    def _on_search_changed(self, text: str) -> None:
        query = text.strip()
        if not query:
            self.nav_stack.setCurrentWidget(self.tree)
            if self._current_article_id:
                self._select_in_tree(self._current_article_id)
            return
        self.search_results.clear()
        hits = self._registry.search(query)
        if not hits:
            placeholder = QListWidgetItem("No matching articles.")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.search_results.addItem(placeholder)
        else:
            for hit in hits:
                snippet = hit.snippet or hit.article.summary
                text_label = hit.article.title if not snippet else f"{hit.article.title}\n{snippet}"
                item = QListWidgetItem(text_label)
                item.setData(_ARTICLE_ROLE, hit.article.id)
                self.search_results.addItem(item)
        self.nav_stack.setCurrentWidget(self.search_results)

    def _on_result_chosen(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        article_id = item.data(_ARTICLE_ROLE)
        if article_id:
            self.navigate(str(article_id))

    # -- Navigation -------------------------------------------------------

    def navigate(self, article_id: str, *, anchor: str = "", record: bool = True) -> None:
        article = self._registry.article(article_id)
        if article is None:
            return
        if record:
            # Drop any forward history when navigating from the middle of the stack.
            del self._history[self._history_index + 1 :]
            if not self._history or self._history[-1] != article_id:
                self._history.append(article_id)
            self._history_index = len(self._history) - 1

        self._current_article_id = article_id
        self.browser.setMarkdown(dedent(article.markdown).strip())
        self._apply_block_spacing()
        if anchor:
            self.browser.scrollToAnchor(anchor)
        else:
            self.browser.moveCursor(QTextCursor.MoveOperation.Start)

        category = self._registry.category(article.category)
        crumb = category.title if category else article.category
        self.breadcrumb.setText(f"{crumb}  ›  {article.title}")
        self._select_in_tree(article_id)
        self._update_history_buttons()

    def _go_back(self) -> None:
        if self._history_index > 0:
            self._history_index -= 1
            self.navigate(self._history[self._history_index], record=False)

    def _go_forward(self) -> None:
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            self.navigate(self._history[self._history_index], record=False)

    def _go_home(self) -> None:
        self.search_field.clear()
        home = self._registry.home_article_id()
        if home:
            self.navigate(home)

    def _update_history_buttons(self) -> None:
        self.back_button.setEnabled(self._history_index > 0)
        self.forward_button.setEnabled(self._history_index < len(self._history) - 1)

    def _on_anchor_clicked(self, url: QUrl) -> None:
        if url.scheme() == "doc":
            target = url.path() or url.toString().split(":", 1)[-1].split("#", 1)[0]
            self.navigate(target, anchor=url.fragment())
        elif url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)
        elif not url.scheme() and url.hasFragment():
            self.browser.scrollToAnchor(url.fragment())

    def _apply_block_spacing(self) -> None:
        option = self.browser.document().defaultTextOption()
        option.setWrapMode(QTextOption.WrapMode.WordWrap)
        self.browser.document().setDefaultTextOption(option)


def open_documentation(parent) -> DocsBrowserDialog:
    """Create-or-raise a single shared documentation window on ``parent``."""

    existing = getattr(parent, "_docs_browser_dialog", None)
    if existing is None:
        existing = DocsBrowserDialog(parent=parent)
        setattr(parent, "_docs_browser_dialog", existing)
    existing.show()
    existing.raise_()
    existing.activateWindow()
    return existing

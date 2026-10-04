from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSlider, QToolButton,
    QLabel, QCheckBox, QFrame, QScrollArea, QTreeWidget, QTreeWidgetItem, QMenu, QComboBox, QLineEdit
)
from PyQt6.QtCore import pyqtSignal, Qt, QTimer, QSettings
import os
import ntpath

from ui.widgets.flow_layout import FlowLayout
from ui.widgets.tag_chip import TagChip
from ui.translations import tr
from database.search_repository import source_section, normalized_path
from database.source_group_store import SourceGroupStore
from ui.theme import get_indicator_stylesheet
import config
from database.db_manager import DatabaseManager
from database.collection_repository import CollectionRepository
from ui.widgets.collections_panel import CollectionsPanel


class TagBubble(QFrame):
    removed = pyqtSignal(str)

    def __init__(self, tag_name, parent=None):
        super().__init__(parent)
        self.tag_name = tag_name
        self.setStyleSheet("""
            QFrame {
                background-color: #252525; border: 1px solid #444;
                border-radius: 4px; padding: 2px 6px;
            }
            QLabel { color: #eee; font-size: 11px; font-weight: normal; border: none; margin: 0; padding: 0; }
            QPushButton {
                background-color: transparent; color: #666; border: none; font-size: 10px; font-weight: bold;
                margin: 0; padding: 0 4px;
            }
            QPushButton:hover { color: #fff; }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        lbl = QLabel(tag_name)
        btn_close = QPushButton("✕")
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.clicked.connect(lambda: self.removed.emit(self.tag_name))

        layout.addWidget(lbl)
        layout.addWidget(btn_close)


from ui.widgets.hybrid_search_input import HybridSearchInput


class SearchPanel(QWidget):
    search_triggered = pyqtSignal(str, str, float, list, list) # text, image_path, threshold, sources, tags
    clear_triggered = pyqtSignal()
    filters_reset = pyqtSignal()
    remove_source_requested = pyqtSignal(str) # path or domain
    catalogs_requested = pyqtSignal()
    manage_tags_requested = pyqtSignal()
    extract_tags_requested = pyqtSignal(str)

    def __init__(self, parent=None, db=None, group_store=None, collection_repo=None):
        super().__init__(parent)
        self.db = db or DatabaseManager(config.DB_PATH)
        self.collection_repo = collection_repo or CollectionRepository(self.db)
        self.group_store = group_store or SourceGroupStore(db=self.db)
        self.source_assignments = {}
        self.disabled_sources = ()
        self._direct_folders = None
        self._own_file_nodes = set()
        self._search_debounce = QTimer()
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(500)
        self._search_debounce.timeout.connect(self._emit_search)

        self.folder_items = {}  # {path: QTreeWidgetItem}
        self.item_archdaily = None
        self.item_behance = None
        self.selected_tags = []

        self._init_ui()

    def _init_ui(self):
        self.setFixedWidth(310)
        self.setStyleSheet(f"""
            QWidget {{ background-color: #0f0f0f; color: #e0e0e0; }}
            QLabel {{ font-weight: bold; margin-top: 6px; margin-bottom: 3px; color: #888; font-size: 11px; text-transform: uppercase; }}
            
            QTreeWidget {{ 
                background-color: #0f0f0f; 
                border: none; 
                outline: none;
                margin-top: 3px;
            }}
            QTreeWidget::item {{ 
                padding: 4px; 
                color: #bbb;
            }}
            QTreeWidget::item:hover {{ background-color: #1a1a1a; color: #fff; }}
            QTreeWidget::item:selected {{ background-color: #222; color: #fff; font-weight: bold; }}
            
            QCheckBox {{ spacing: 6px; font-size: 12px; color: #bbb; }}
            QCheckBox:hover {{ color: #eee; }}

            {get_indicator_stylesheet()}
        """)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        scroll.setWidget(content)
        outer_layout.addWidget(scroll, 1)

        main_layout = QVBoxLayout(content)
        main_layout.setContentsMargins(12, 10, 12, 8)
        main_layout.setSpacing(8)

        # Footer layout (kept for clean structure, settings button moved to top-left)
        self.footer_layout = QHBoxLayout()
        self.footer_layout.setContentsMargins(12, 2, 12, 4)
        outer_layout.addLayout(self.footer_layout)

        # Block 1: Search and Clear Action Buttons
        actions_layout = QHBoxLayout()
        self.btn_search = QPushButton(tr("search"))
        self.btn_search.setStyleSheet("""
            QPushButton { background-color: #444; color: #fff; font-weight: bold; border-radius: 4px; padding: 9px; border: 1px solid #555; }
            QPushButton:hover { background-color: #555; border-color: #666; }
        """)
        self.btn_search.clicked.connect(self._emit_search)

        self.btn_clear = QPushButton("Очистить запрос" if config.CURRENT_LANGUAGE == "ru" else "Clear query")
        self.btn_clear.setStyleSheet("background-color: #282828; color: #ddd; border: 1px solid #444; border-radius: 4px; padding: 9px;")
        self.btn_clear.setToolTip("Очистить поисковый запрос (текст и изображение), сохранив фильтры")
        self.btn_clear.clicked.connect(self._clear_all)

        actions_layout.addWidget(self.btn_search, 1)
        actions_layout.addWidget(self.btn_clear, 1)
        main_layout.addLayout(actions_layout)

        # Block 2: Hybrid Input (Image + Text)
        self.hybrid_input = HybridSearchInput()
        self.hybrid_input.search_requested.connect(self._on_hybrid_enter)
        self.hybrid_input.analyze_requested.connect(self.extract_tags_requested.emit)
        main_layout.addWidget(self.hybrid_input)

        # Query warning (token limit warning)
        self.query_warning = QLabel()
        self.query_warning.setWordWrap(True)
        self.query_warning.setStyleSheet("color: #d3b276; font-size: 11px;")
        self.query_warning.hide()
        main_layout.addWidget(self.query_warning)

        # Text mode selector
        self.text_mode = QComboBox()
        self.text_mode.addItem("По смыслу", "semantic")
        self.text_mode.addItem("По названию / бюро", "metadata")
        self.text_mode.setToolTip(
            "Поиск по записанным названиям проектов, авторам, именам файлов и описаниям без AI. "
            "Работа студии находится, если сведения о студии есть в базе."
        )
        self.text_mode.currentIndexChanged.connect(self._on_source_toggled)
        main_layout.addWidget(self.text_mode)

        # Block 3: Refinements Block (Уточнения)
        self.library_widget = QFrame()
        self.library_widget.setObjectName('refinementsPanel')
        self.library_widget.setStyleSheet('QFrame#refinementsPanel { border-top: 1px solid #2a2a2a; }')
        refinements_layout = QVBoxLayout(self.library_widget)
        refinements_layout.setContentsMargins(0, 10, 0, 0)
        refinements_layout.setSpacing(6)

        # Invisible compatibility objects (active scope is controlled by top bar)
        self.favorite_check = QCheckBox()
        self.favorite_check.hide()
        self.favorite_check.toggled.connect(self._emit_search)
        self.top_check = QCheckBox()
        self.top_check.hide()
        self.top_check.toggled.connect(self._emit_search)

        # Refinement buttons row (Теги на всю ширину + Сброс)
        refinements_buttons = QHBoxLayout()
        refinements_buttons.setSpacing(6)

        self.btn_manage_tags = QPushButton(tr('tags'))
        self.btn_manage_tags.setToolTip("Выбрать теги библиотеки. Число на кнопке — количество выбранных.")
        self.btn_manage_tags.setStyleSheet("background-color: #1f1f1f; color: #eee; border: 1px solid #383838; border-radius: 4px; padding: 6px 10px; font-size: 12px; font-weight: 500;")
        self.btn_manage_tags.clicked.connect(self.manage_tags_requested.emit)
        refinements_buttons.addWidget(self.btn_manage_tags, 1)

        self.reset_filters_button = QPushButton("Сброс")
        self.reset_filters_button.setToolTip("Сбросить все уточнения и восстановить все источники")
        self.reset_filters_button.setStyleSheet("background-color: #222; color: #aaa; border: 1px solid #383838; border-radius: 4px; padding: 6px 10px; font-size: 11px;")
        self.reset_filters_button.clicked.connect(self.reset_filters)
        refinements_buttons.addWidget(self.reset_filters_button)
        refinements_layout.addLayout(refinements_buttons)

        self.filters_button = QPushButton("Условия")
        self.filters_button.setCheckable(True)
        self.filters_button.toggled.connect(self._on_filters_toggled)
        self.filters_button.hide()

        # Tag bubbles display area
        self.tags_widget = QWidget()
        self.tags_flow_layout = FlowLayout(self.tags_widget, margin=0, hSpacing=4, vSpacing=4)
        self.tags_widget.hide()
        refinements_layout.addWidget(self.tags_widget)

        # Extra conditions (Filters widget)
        self.filters_widget = QWidget()
        self.filters_layout = QVBoxLayout(self.filters_widget)
        self.filters_layout.setContentsMargins(0, 4, 0, 0)
        self.filters_layout.setSpacing(6)

        self.tag_match = QComboBox()
        self.tag_match.addItem("Любой из тегов", "any")
        self.tag_match.addItem("Все теги одновременно", "all")
        self.tag_match.setCurrentIndex(1)
        self.tag_match.currentIndexChanged.connect(self._on_source_toggled)
        self.filters_layout.addWidget(self.tag_match)

        self.exclude_input = QLineEdit()
        self.exclude_input.setPlaceholderText("Исключить теги: wood, glass")
        self.exclude_input.setToolTip("Исключаются записи с этими тегами.")
        self.exclude_input.textEdited.connect(lambda _text: self._search_debounce.start())
        self.filters_layout.addWidget(self.exclude_input)

        self.filters_widget.hide()
        refinements_layout.addWidget(self.filters_widget)
        main_layout.addWidget(self.library_widget)

        # Block 3.5: Moodboards / Collections Block (МУДБОРДЫ)
        self.collections_panel = CollectionsPanel(self.collection_repo, self)
        main_layout.addWidget(self.collections_panel)

        # Block 4: Sources Tree Block (Источники)
        sources_header = QHBoxLayout()
        self.lbl_sources = QLabel(tr("sources"))
        sources_header.addWidget(self.lbl_sources, 1)

        self.btn_catalogs = QPushButton("⚙")
        self.btn_catalogs.setFixedSize(26, 26)
        self.btn_catalogs.setToolTip("Управление каталогами, папками и сайтами")
        self.btn_catalogs.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #888;
                border: 1px solid #333;
                border-radius: 4px;
                font-size: 13px;
                padding-bottom: 2px;
            }
            QPushButton:hover {
                background-color: #24272e;
                color: #29b6f6;
                border-color: #29b6f6;
            }
        """)
        self.btn_catalogs.clicked.connect(self.catalogs_requested.emit)
        sources_header.addWidget(self.btn_catalogs)
        main_layout.addLayout(sources_header)

        self.sources_tree = QTreeWidget()
        self.sources_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sources_tree.setHeaderHidden(True)
        self.sources_tree.setIndentation(16)
        self.sources_tree.setColumnCount(1)
        self.sources_tree.customContextMenuRequested.connect(self._on_context_menu)
        self.sources_tree.itemChanged.connect(self._on_item_changed)
        self.sources_tree.setMinimumHeight(180)
        main_layout.addWidget(self.sources_tree, 1)

        # Sync settings from group_store
        self._sync_store_settings()

    def _on_filters_toggled(self, checked):
        self.filters_widget.setVisible(checked)

    def _sync_store_settings(self):
        self.source_assignments = self.group_store.get_source_assignments()
        self.disabled_sources = self.group_store.get_disabled_sources()

    @property
    def section(self):
        return "all"

    def update_custom_folders(self, folders: list, direct_folders=None, group_store=None):
        """Builds hierarchical tree of user groups and sources."""
        if group_store:
            self.group_store = group_store
        self._sync_store_settings()

        self.sources_tree.blockSignals(True)
        if direct_folders is not None:
            self._direct_folders = {normalized_path(path) for path in direct_folders}

        # Preserve checked and expanded states by stable source/group keys
        old_checked = {}
        old_expanded = set()
        def collect_old(item):
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data:
                old_checked[data] = item.checkState(0)
                if item.isExpanded():
                    old_expanded.add(data)
            for i in range(item.childCount()):
                collect_old(item.child(i))

        for i in range(self.sources_tree.topLevelItemCount()):
            collect_old(self.sources_tree.topLevelItem(i))

        # Clear tree
        self.sources_tree.clear()
        self.folder_items.clear()
        self.item_archdaily = None
        self.item_behance = None

        folder_icon = self.sources_tree.style().standardIcon(self.sources_tree.style().StandardPixmap.SP_DirIcon)

        # Build group hierarchy
        groups = self.group_store.get_groups()
        group_items = {}

        def add_groups_recursively(parent_id, parent_item):
            subgroups = [g for g in groups if g.get("parent_id") == parent_id]
            subgroups.sort(key=lambda x: (x.get("order", 0), x["name"]))
            for g in subgroups:
                # Only add if group is not administratively disabled
                if self.group_store.is_group_disabled(g["id"]):
                    continue
                item = QTreeWidgetItem(parent_item or self.sources_tree, [g["name"]])
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                item.setIcon(0, folder_icon)
                key = f"group:{g['id']}"
                item.setData(0, Qt.ItemDataRole.UserRole, key)
                item.setCheckState(0, old_checked.get(key, Qt.CheckState.Checked))
                if key in old_expanded or parent_id is None:
                    item.setExpanded(True)
                group_items[g["id"]] = item
                add_groups_recursively(g["id"], item)

        add_groups_recursively(None, None)

        # 1. Add Web Sources (ArchDaily & Behance) if not administratively disabled
        for web_key, web_name in [("archdaily.com", "ArchDaily"), ("behance.net", "Behance")]:
            if self.group_store.is_source_disabled(web_key):
                continue
            gid = self.group_store.get_source_group(web_key)
            parent = group_items.get(gid) or self.sources_tree

            w_item = QTreeWidgetItem(parent, [web_name])
            w_item.setFlags(w_item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            data_val = "archdaily" if "archdaily" in web_key else "behance"
            w_item.setData(0, Qt.ItemDataRole.UserRole, data_val)
            w_item.setCheckState(0, old_checked.get(data_val, Qt.CheckState.Checked))

            if "archdaily" in web_key:
                self.item_archdaily = w_item
            else:
                self.item_behance = w_item

        # 2. Normalize and filter folder paths
        norm_folders = []
        for f in folders:
            if f in ('archdaily', 'behance', 'archdaily.com', 'behance.net'):
                continue
            if not f or len(f) < 4:
                continue
            if not all(c.isprintable() for c in f):
                continue
            if self.group_store.is_source_disabled(f):
                continue
            norm_folders.append(os.path.normpath(f))

        norm_folders = sorted(list(set(norm_folders)), key=lambda x: (len(x.split(os.sep)), x))
        folder_items_map = {}

        for path in norm_folders:
            norm = normalized_path(path)
            gid = self.group_store.get_source_group(norm)
            parent_item = group_items.get(gid) or self.sources_tree

            # Nested hierarchy under parent folders in the same group or tree
            for test_parent, test_item in folder_items_map.items():
                if norm != test_parent and norm.startswith(test_parent + "/"):
                    parent_item = test_item
                    break

            display_name = os.path.basename(path) or path
            item = QTreeWidgetItem(parent_item, [display_name])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            item.setIcon(0, folder_icon)
            item.setData(0, Qt.ItemDataRole.UserRole, path)
            item.setCheckState(0, old_checked.get(path, Qt.CheckState.Checked))
            if path in old_expanded:
                item.setExpanded(True)

            self.folder_items[path] = item
            folder_items_map[norm] = item

        # Check own file nodes for direct files
        if direct_folders is not None:
            self._direct_folders = {normalized_path(p) for p in direct_folders}
            self._own_file_nodes = set(self._direct_folders)
        else:
            self._direct_folders = None
            self._own_file_nodes = None

        # Hide empty virtual groups and empty physical folders without own files
        def prune_empty(item):
            for i in range(item.childCount() - 1, -1, -1):
                prune_empty(item.child(i))

            data = item.data(0, Qt.ItemDataRole.UserRole) or ""
            has_visible_children = any(not item.child(i).isHidden() for i in range(item.childCount()))

            if data.startswith("group:"):
                if not has_visible_children:
                    item.setHidden(True)
            elif data in ('archdaily', 'behance', 'archdaily.com', 'behance.net'):
                pass
            else:
                norm = normalized_path(data)
                if self._own_file_nodes is not None:
                    has_own_files = norm in self._own_file_nodes
                    if not has_visible_children and not has_own_files:
                        item.setHidden(True)

        for i in range(self.sources_tree.topLevelItemCount() - 1, -1, -1):
            prune_empty(self.sources_tree.topLevelItem(i))

        self._update_parent_check_states()
        self.sources_tree.blockSignals(False)

    def _update_parent_check_states(self):
        """Computes partial or full check state for parent items."""
        def visit(item):
            if item.isHidden():
                return None
            if item.childCount() == 0:
                return item.checkState(0)

            child_states = set()
            for i in range(item.childCount()):
                st = visit(item.child(i))
                if st is not None:
                    child_states.add(st)

            if not child_states:
                return item.checkState(0)

            data = item.data(0, Qt.ItemDataRole.UserRole) or ""
            if data.startswith("group:"):
                if child_states == {Qt.CheckState.Checked}:
                    item.setCheckState(0, Qt.CheckState.Checked)
                elif child_states == {Qt.CheckState.Unchecked}:
                    item.setCheckState(0, Qt.CheckState.Unchecked)
                else:
                    item.setCheckState(0, Qt.CheckState.PartiallyChecked)
            else:
                curr = item.checkState(0)
                if curr in (Qt.CheckState.Checked, Qt.CheckState.PartiallyChecked):
                    if child_states == {Qt.CheckState.Checked}:
                        item.setCheckState(0, Qt.CheckState.Checked)
                    else:
                        item.setCheckState(0, Qt.CheckState.PartiallyChecked)
                else:
                    if child_states == {Qt.CheckState.Unchecked}:
                        item.setCheckState(0, Qt.CheckState.Unchecked)
                    else:
                        item.setCheckState(0, Qt.CheckState.PartiallyChecked)
            return item.checkState(0)

        for i in range(self.sources_tree.topLevelItemCount()):
            visit(self.sources_tree.topLevelItem(i))

    def _on_context_menu(self, pos):
        item = self.sources_tree.itemAt(pos)
        if not item:
            return

        menu = QMenu(self)
        menu.setStyleSheet("QMenu { background-color: #242424; color: white; border: 1px solid #444; } QMenu::item:selected { background-color: #383838; }")

        solo_action = menu.addAction(tr("solo_selection"))
        all_action = menu.addAction(tr("select_everything"))
        menu.addSeparator()
        uncheck_action = menu.addAction("Снять выбор")

        action = menu.exec(self.sources_tree.viewport().mapToGlobal(pos))
        if action == solo_action:
            self._check_all(False)
            item.setCheckState(0, Qt.CheckState.Checked)
            self._on_item_changed(item, 0)
        elif action == all_action:
            self._check_all(True)
        elif action == uncheck_action:
            item.setCheckState(0, Qt.CheckState.Unchecked)
            self._on_item_changed(item, 0)

    def _on_item_changed(self, item, column):
        self.sources_tree.blockSignals(True)
        state = item.checkState(column)

        # Propagate downwards
        def update_children(parent_item):
            for i in range(parent_item.childCount()):
                child = parent_item.child(i)
                if not child.isHidden():
                    child.setCheckState(column, state)
                    update_children(child)

        if state in (Qt.CheckState.Checked, Qt.CheckState.Unchecked):
            update_children(item)

        # Propagate upwards
        self._update_parent_check_states()
        self.sources_tree.blockSignals(False)
        self._on_source_toggled()

    def _apply_section(self):
        """Maintained for compatibility; re-checks enabled sources."""
        self._sync_store_settings()

    def excluded_tags(self):
        return tuple(dict.fromkeys(t.strip() for t in self.exclude_input.text().split(",") if t.strip()))

    def _on_source_toggled(self):
        self._search_debounce.start()

    def reset_filters(self):
        """Resets all refinements and operational selection to all enabled sources, preserving query."""
        self._search_debounce.stop()
        self.favorite_check.blockSignals(True)
        self.favorite_check.setChecked(False)
        self.favorite_check.blockSignals(False)

        self.top_check.blockSignals(True)
        self.top_check.setChecked(False)
        self.top_check.blockSignals(False)

        self.tag_match.blockSignals(True)
        self.tag_match.setCurrentIndex(0)
        self.tag_match.blockSignals(False)

        self.set_selected_tags([])
        self.exclude_input.clear()

        # Check all visible (administratively enabled) sources
        self._check_all(True)
        self._search_debounce.stop()
        self.filters_reset.emit()

    def get_selected_sources(self) -> list:
        all_checked = []
        def collect_checked(item):
            if item.isHidden():
                return
            if item.checkState(0) in (Qt.CheckState.Checked, Qt.CheckState.PartiallyChecked):
                data = item.data(0, Qt.ItemDataRole.UserRole)
                if data and not data.startswith("group:"):
                    all_checked.append(data)
            for i in range(item.childCount()):
                collect_checked(item.child(i))

        for i in range(self.sources_tree.topLevelItemCount()):
            collect_checked(self.sources_tree.topLevelItem(i))

        web_sources = [s for s in all_checked if s in ('archdaily', 'behance')]
        folder_sources = [s for s in all_checked if s not in web_sources]
        if not folder_sources:
            return web_sources

        folder_sources.sort(key=len)
        optimized_folders = []
        for folder in folder_sources:
            is_covered = False
            norm_folder = os.path.normpath(folder)
            for parent in optimized_folders:
                norm_parent = os.path.normpath(parent)
                try:
                    if os.path.commonpath([norm_parent, norm_folder]) == norm_parent:
                        is_covered = True
                        break
                except ValueError:
                    continue
            if not is_covered:
                optimized_folders.append(folder)
        return web_sources + optimized_folders

    def get_excluded_sources(self):
        excluded = list(self.group_store.get_disabled_sources())
        def visit(item):
            if item.isHidden():
                return
            value = item.data(0, Qt.ItemDataRole.UserRole)
            if item.checkState(0) == Qt.CheckState.Unchecked:
                if value and not value.startswith("group:"):
                    excluded.append(value)
                    return  # Top-level unchecked physical folder covers all descendants
            for i in range(item.childCount()):
                visit(item.child(i))

        for i in range(self.sources_tree.topLevelItemCount()):
            visit(self.sources_tree.topLevelItem(i))
        return tuple(dict.fromkeys(excluded))

    def set_selected_tags(self, tags: list):
        self.selected_tags = list(dict.fromkeys(tags))
        self.tags_widget.setVisible(bool(self.selected_tags))
        self.btn_manage_tags.setText(tr('tags') + (f" · {len(self.selected_tags)}" if self.selected_tags else ""))

        # Clear layout
        while self.tags_flow_layout.count():
            item = self.tags_flow_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Add bubbles
        for tag in tags:
            bubble = TagBubble(tag)
            bubble.removed.connect(self._on_tag_removed)
            self.tags_flow_layout.addWidget(bubble)

    def _on_tag_removed(self, tag: str):
        if tag in self.selected_tags:
            self.selected_tags.remove(tag)
            self.set_selected_tags(self.selected_tags)
            self._emit_search()

    def _emit_search(self):
        text = self.hybrid_input.text_input.text().strip()
        img_path = self.hybrid_input.image_path
        self._search_debounce.stop()
        threshold = 0.0
        sources = self.get_selected_sources()
        self.search_triggered.emit(text, img_path, threshold, sources, getattr(self, 'selected_tags', []))

    def _on_hybrid_enter(self, text, img_path):
        self._emit_search()

    def _check_all(self, state_bool: bool):
        self.sources_tree.blockSignals(True)
        state = Qt.CheckState.Checked if state_bool else Qt.CheckState.Unchecked
        def process_recursive(item):
            if not item.isHidden():
                item.setCheckState(0, state)
            for i in range(item.childCount()):
                process_recursive(item.child(i))

        for i in range(self.sources_tree.topLevelItemCount()):
            process_recursive(self.sources_tree.topLevelItem(i))
        self.sources_tree.blockSignals(False)
        self._on_source_toggled()

    def _clear_all(self):
        """Clears search query text and image, preserving filters and sources."""
        self._search_debounce.stop()
        self.hybrid_input.clear_all()
        self.query_warning.hide()
        self.clear_triggered.emit()

    def retranslate_ui(self):
        self.btn_search.setText(tr("search"))
        self.btn_clear.setText("Очистить запрос" if config.CURRENT_LANGUAGE == "ru" else "Clear query")
        self.btn_manage_tags.setText(tr('tags') + (f" · {len(self.selected_tags)}" if self.selected_tags else ""))
        self.lbl_sources.setText(tr("sources"))
        self.hybrid_input.retranslate_ui()

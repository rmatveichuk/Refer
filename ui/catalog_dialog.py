"""Spacious Catalog & Library Management Center with user groups and calm indicators."""
import ntpath
import os
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QComboBox, QCheckBox, QLineEdit,
    QFileDialog, QDialogButtonBox, QMessageBox, QSplitter, QWidget, QFrame,
    QMenu, QInputDialog
)

from database.search_repository import normalized_path, source_section, like_prefix
from database.source_group_store import SourceGroupStore
from ui.theme import get_indicator_stylesheet
from ui.translations import tr


def is_disabled(path, disabled):
    path = normalized_path(path)
    return any(path == normalized_path(root) or path.startswith(normalized_path(root) + "/") for root in disabled)


class FolderImportDialog(QDialog):
    def __init__(self, parent=None, path="", group_id=None, group_store=None, default_group=None):
        super().__init__(parent)
        self.setWindowTitle("Подключить каталог" if not path else "Пересканировать каталог")
        self.setMinimumWidth(560)
        self.group_store = group_store
        effective_gid = default_group or group_id

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Каталог с изображениями и превью моделей"))

        row = QHBoxLayout()
        self.path_input = QLineEdit(path)
        row.addWidget(self.path_input, 1)
        browse = QPushButton("Выбрать…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        layout.addLayout(row)

        # Target group selector
        self.group_combo = QComboBox()
        if self.group_store:
            self.group_combo.addItem("Без группы (в корень)", "")
            for g in self.group_store.get_groups():
                self.group_combo.addItem(g["name"], g["id"])
            if effective_gid:
                idx = self.group_combo.findData(effective_gid)
                if idx >= 0:
                    self.group_combo.setCurrentIndex(idx)
        else:
            self.group_combo.addItem("По умолчанию", "")
        layout.addWidget(QLabel("Группа в библиотеке:"))
        layout.addWidget(self.group_combo)

        # Independent import options
        self.recursive = QCheckBox("Включать вложенные папки")
        self.recursive.setChecked(True)
        self.no_textures = QCheckBox("Пропускать текстуры и технические карты")
        self.no_textures.setChecked(True)
        self.skip_deleted = QCheckBox("Не возвращать изображения, удалённые из библиотеки")
        self.skip_deleted.setChecked(True)

        for control in (self.recursive, self.no_textures, self.skip_deleted):
            layout.addWidget(control)

        note = QLabel("Файлы остаются в выбранной папке. Обновление добавляет новые изображения; ранее добавленные сохраняются.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Начать сканирование")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "Выберите каталог", self.path_input.text())
        if path:
            self.path_input.setText(path)

    def options(self):
        norm_path = os.path.normpath(self.path_input.text().strip())
        return {
            "path": norm_path,
            "group_id": self.group_combo.currentData(),
            "mode": "3D Models" if self.no_textures.isChecked() else "All",
            "no_textures": self.no_textures.isChecked(),
            "recursive": self.recursive.isChecked(),
            "skip_deleted": self.skip_deleted.isChecked(),
        }

    def accept(self):
        opts = self.options()
        if not opts["path"] or not os.path.isdir(opts["path"]):
            QMessageBox.warning(
                self,
                "Каталог недоступен",
                "Выберите существующую папку. Если диск отключён, подключите его и повторите."
            )
            return
        super().accept()


class MoveToGroupDialog(QDialog):
    """Dialog to choose a destination group, preventing cycles."""
    def __init__(self, group_store: SourceGroupStore, exclude_group_ids: set, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Переместить в группу")
        self.setMinimumWidth(360)
        self.group_store = group_store
        self.selected_group_id = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Выберите целевую группу:"))

        self.combo = QComboBox()
        self.combo.addItem("Корень библиотеки (без группы)", None)

        all_groups = self.group_store.get_groups()
        for g in all_groups:
            if g["id"] not in exclude_group_ids:
                # Show indentation if nested
                level = 0
                curr = g.get("parent_id")
                while curr:
                    level += 1
                    curr = next((x.get("parent_id") for x in all_groups if x["id"] == curr), None)
                prefix = "  " * level + ("└─ " if level > 0 else "")
                self.combo.addItem(f"{prefix}{g['name']}", g["id"])

        layout.addWidget(self.combo)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Переместить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self):
        self.selected_group_id = self.combo.currentData()
        self.accept()


class CatalogDialog(QDialog):
    """Spacious Center of Management for catalogs, user groups, and scraping."""
    def __init__(self, db, assignments_or_store=None, disabled=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Каталоги библиотеки")
        self.setMinimumSize(960, 600)
        self.resize(1060, 680)

        self.db = db
        self.main_window = parent

        # Support both SourceGroupStore or legacy dict assignments
        if isinstance(assignments_or_store, SourceGroupStore):
            self.original_store = assignments_or_store
            self.group_store = self.original_store.create_draft()
        else:
            self.original_store = SourceGroupStore(db=self.db)
            if isinstance(assignments_or_store, dict):
                for k, v in assignments_or_store.items():
                    norm_k = normalized_path(k)
                    target_gid = "grp_3d" if v == "models" else "grp_arch"
                    self.original_store.assign_source(norm_k, target_gid)
            if isinstance(disabled, (list, tuple)):
                for d in disabled:
                    self.original_store.set_source_disabled(d, True)
            self.group_store = self.original_store.create_draft()

        self.action = None
        self._counts_cache = {}
        self._all_db_sources = []
        self._init_data()
        self._init_ui()
        self._populate_tree()

    def _init_data(self):
        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT domain FROM sources ORDER BY domain").fetchall()
            self._all_db_sources = [
                r["domain"] for r in rows if r["domain"]
            ]
            # Precalculate counts for registered sources
            for src in self._all_db_sources:
                norm = normalized_path(src)
                if norm in ("archdaily.com", "behance.net", "archdaily", "behance"):
                    domain_val = "archdaily.com" if "archdaily" in norm else "behance.net"
                    count = conn.execute(
                        "SELECT COUNT(*) FROM assets a JOIN sources s ON a.source_id=s.id WHERE s.domain=?",
                        (domain_val,)
                    ).fetchone()[0]
                else:
                    count = conn.execute(
                        "SELECT COUNT(*) FROM assets WHERE REPLACE(local_path, '\\', '/') LIKE ? ESCAPE '!'",
                        (like_prefix(src),)
                    ).fetchone()[0]
                self._counts_cache[norm] = count

    def _init_ui(self):
        self.setStyleSheet(f"""
            QDialog {{ background-color: #121212; color: #e0e0e0; }}
            QPushButton {{ 
                background-color: #282828; color: #e0e0e0;
                border: 1px solid #3b3b3b; border-radius: 4px; padding: 6px 12px; font-size: 12px;
            }}
            QPushButton:hover {{ background-color: #353535; }}
            QPushButton:disabled {{ color: #555; background-color: #1a1a1a; border-color: #282828; }}
            QLineEdit, QComboBox {{ 
                background-color: #1d1d1d; color: #e0e0e0;
                border: 1px solid #383838; border-radius: 4px; padding: 5px;
            }}
            QTreeWidget {{
                background-color: #161616; border: 1px solid #282828; border-radius: 4px;
                color: #ddd; outline: none; padding: 4px;
            }}
            QTreeWidget::item {{ padding: 5px; border-radius: 3px; }}
            QTreeWidget::item:hover {{ background-color: #202020; }}
            QTreeWidget::item:selected {{ background-color: #2b2b2b; color: #fff; font-weight: bold; }}
            {get_indicator_stylesheet()}
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(10)

        # Top Action Bar
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        self.btn_create_group = QPushButton("Создать группу")
        self.btn_create_group.clicked.connect(self._create_group)
        top_bar.addWidget(self.btn_create_group)

        self.btn_add_folder = QPushButton("Добавить папку…")
        self.btn_add_folder.clicked.connect(self._add_folder)
        top_bar.addWidget(self.btn_add_folder)

        self.btn_add_web = QPushButton("Добавить с сайта…")
        self.btn_add_web.clicked.connect(self._toggle_web_import)
        top_bar.addWidget(self.btn_add_web)

        top_bar.addSpacing(12)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #333;")
        top_bar.addWidget(sep)
        top_bar.addSpacing(12)

        self.btn_hidden = QPushButton("Скрытые")
        self.btn_hidden.setToolTip("Управление скрытыми изображениями библиотеки")
        self.btn_hidden.clicked.connect(self._open_hidden_assets)
        top_bar.addWidget(self.btn_hidden)

        self.btn_check_files = QPushButton("Проверить файлы")
        self.btn_check_files.setToolTip("Показать отсутствующие файлы и отключённые диски без удаления данных")
        self.btn_check_files.clicked.connect(self._check_files)
        top_bar.addWidget(self.btn_check_files)

        self.btn_index = QPushButton("Индексация")
        self.btn_index.setToolTip("Запустить фоновое вычисление векторов для неиндексированных изображений")
        self.btn_index.clicked.connect(self._start_indexing)
        top_bar.addWidget(self.btn_index)

        top_bar.addStretch()
        main_layout.addLayout(top_bar)

        # Web import drawer / section (hidden by default unless active or requested)
        self.web_import_widget = QFrame()
        self.web_import_widget.setFrameShape(QFrame.Shape.StyledPanel)
        self.web_import_widget.setStyleSheet("background-color: #1a1a1a; border: 1px solid #333; border-radius: 5px; padding: 6px;")
        web_layout = QHBoxLayout(self.web_import_widget)
        web_layout.setContentsMargins(10, 6, 10, 6)
        web_layout.setSpacing(8)

        self.web_parser_combo = QComboBox()
        self.web_parser_combo.addItems(["Behance", "ArchDaily"])
        web_layout.addWidget(self.web_parser_combo)

        self.web_url_input = QLineEdit()
        self.web_url_input.setPlaceholderText("Вставьте ссылку на проект (ArchDaily или Behance)...")
        web_layout.addWidget(self.web_url_input, 1)

        self.btn_web_scrape = QPushButton("Начать загрузку")
        self.btn_web_scrape.clicked.connect(self._on_web_scrape_clicked)
        web_layout.addWidget(self.btn_web_scrape)

        self.web_import_widget.hide()
        main_layout.addWidget(self.web_import_widget)

        # Search / filter line
        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Поиск по каталогам, сайтам и группам...")
        self.search_input.textChanged.connect(self._filter_tree)
        search_row.addWidget(self.search_input)
        main_layout.addLayout(search_row)

        # Main Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet("QSplitter::handle { background-color: #222; width: 4px; }")

        # Left Pane: Tree
        tree_container = QWidget()
        tree_layout = QVBoxLayout(tree_container)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        tree_layout.setSpacing(4)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Группы и источники", "Изображений"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setStyleSheet("QHeaderView::section { background-color: #1a1a1a; color: #888; border: none; padding: 4px; }")
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self.tree.itemSelectionChanged.connect(self._on_item_selected)
        self.tree.itemChanged.connect(self._on_item_check_changed)
        tree_layout.addWidget(self.tree)
        splitter.addWidget(tree_container)

        # Right Pane: Properties & Details
        self.properties_card = QWidget()
        prop_layout = QVBoxLayout(self.properties_card)
        prop_layout.setContentsMargins(12, 6, 6, 6)
        prop_layout.setSpacing(10)

        self.prop_title = QLabel("Свойства выбранного узла")
        self.prop_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #eee;")
        prop_layout.addWidget(self.prop_title)

        self.prop_type_badge = QLabel("—")
        self.prop_type_badge.setStyleSheet("color: #29b6f6; font-size: 11px; font-weight: bold; text-transform: uppercase;")
        prop_layout.addWidget(self.prop_type_badge)

        form_frame = QFrame()
        form_frame.setObjectName("PropertiesForm")
        form_frame.setStyleSheet("""
            QFrame#PropertiesForm {
                background-color: #171717;
                border: 1px solid #262626;
                border-radius: 4px;
                padding: 10px;
            }
            QFrame#PropertiesForm QLabel {
                border: none;
                padding: 0;
                background: transparent;
                color: #9e9e9e;
            }
        """)
        form_layout = QVBoxLayout(form_frame)
        form_layout.setSpacing(8)

        form_layout.addWidget(QLabel("Название:"))
        self.prop_name_edit = QLineEdit()
        self.prop_name_edit.editingFinished.connect(self._on_name_edited)
        form_layout.addWidget(self.prop_name_edit)

        form_layout.addWidget(QLabel("Путь или адрес:"))
        self.prop_path_label = QLabel("—")
        self.prop_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.prop_path_label.setStyleSheet("color: #aaa; font-family: monospace; font-size: 11px;")
        self.prop_path_label.setWordWrap(True)
        form_layout.addWidget(self.prop_path_label)

        form_layout.addWidget(QLabel("Изображений в базе:"))
        self.prop_count_label = QLabel("0")
        self.prop_count_label.setStyleSheet("color: #eee; font-weight: bold;")
        form_layout.addWidget(self.prop_count_label)

        form_layout.addWidget(QLabel("Доступность источника:"))
        self.prop_avail_label = QLabel("—")
        form_layout.addWidget(self.prop_avail_label)

        self.prop_enabled_check = QCheckBox("Включён в библиотеку (участвует в поиске)")
        self.prop_enabled_check.toggled.connect(self._on_prop_enabled_toggled)
        form_layout.addWidget(self.prop_enabled_check)

        prop_layout.addWidget(form_frame)

        # Context action buttons in properties pane
        actions_header = QLabel("Действия:")
        actions_header.setStyleSheet("color: #888; font-size: 11px; text-transform: uppercase; font-weight: bold;")
        prop_layout.addWidget(actions_header)

        self.btn_rescan = QPushButton("Пересканировать каталог…")
        self.btn_rescan.clicked.connect(self._rescan_selected)
        prop_layout.addWidget(self.btn_rescan)

        self.btn_open_folder = QPushButton("Открыть папку в проводнике")
        self.btn_open_folder.clicked.connect(self._open_in_explorer)
        prop_layout.addWidget(self.btn_open_folder)

        self.btn_move_group = QPushButton("Переместить в группу…")
        self.btn_move_group.clicked.connect(self._move_selected_to_group)
        prop_layout.addWidget(self.btn_move_group)

        self.btn_delete_group = QPushButton("Удалить группу")
        self.btn_delete_group.setStyleSheet("QPushButton { color: #ff7777; border-color: #552222; } QPushButton:hover { background-color: #442222; }")
        self.btn_delete_group.clicked.connect(self._delete_selected_group)
        prop_layout.addWidget(self.btn_delete_group)

        prop_layout.addStretch()
        splitter.addWidget(self.properties_card)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        main_layout.addWidget(splitter, 1)

        # Bottom Bar: Info & Dialog Buttons
        bottom_bar = QHBoxLayout()
        self.status_label = QLabel("Изменения структуры и отключения вступают в силу после «Применить».")
        self.status_label.setStyleSheet("color: #888; font-size: 11px;")
        bottom_bar.addWidget(self.status_label, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.btn_save = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.btn_save.setText("Применить")
        self.btn_cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self.btn_cancel.setText("Отмена")
        buttons.accepted.connect(self._apply_and_close)
        buttons.rejected.connect(self.reject)
        bottom_bar.addWidget(buttons)
        main_layout.addLayout(bottom_bar)

        self._update_properties_view(None)

    def _populate_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()

        # Build group hierarchy
        groups = self.group_store.get_groups()
        group_items = {}  # gid -> QTreeWidgetItem

        # Root groups first
        def add_group_and_children(parent_id, parent_item):
            subgroups = [g for g in groups if g.get("parent_id") == parent_id]
            subgroups.sort(key=lambda x: (x.get("order", 0), x["name"]))
            for g in subgroups:
                item = QTreeWidgetItem(parent_item or self.tree, [g["name"], ""])
                item.setData(0, Qt.ItemDataRole.UserRole, {"type": "group", "id": g["id"]})
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                is_dis = self.group_store.is_group_disabled(g["id"])
                item.setCheckState(0, Qt.CheckState.Unchecked if is_dis else Qt.CheckState.Checked)
                item.setExpanded(True)
                group_items[g["id"]] = item
                add_group_and_children(g["id"], item)

        add_group_and_children(None, None)

        # Add Web Sources
        for web_key, web_name in [("archdaily.com", "ArchDaily"), ("behance.net", "Behance")]:
            gid = self.group_store.get_source_group(web_key)
            parent = group_items.get(gid) or self.tree
            count = self._counts_cache.get(web_key, 0)
            w_item = QTreeWidgetItem(parent, [web_name, str(count)])
            w_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "site", "key": web_key, "count": count})
            w_item.setFlags(w_item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            is_dis = self.group_store.is_source_disabled(web_key)
            w_item.setCheckState(0, Qt.CheckState.Unchecked if is_dis else Qt.CheckState.Checked)

        # Add Registered Local Folder Sources
        folder_sources = [
            s for s in self._all_db_sources
            if s and not s.lower().startswith(("archdaily", "behance", "artstation"))
        ]
        # Sort folders by path hierarchy
        norm_folders = sorted(list(set(folder_sources)), key=lambda x: (len(x.split(os.sep)), x))
        folder_items_map = {}

        for folder in norm_folders:
            norm = normalized_path(folder)
            gid = self.group_store.get_source_group(norm)
            parent_item = group_items.get(gid) or self.tree

            # Check for disk hierarchy nesting within the parent group or root
            for test_parent_path, test_item in folder_items_map.items():
                if norm != test_parent_path and norm.startswith(test_parent_path + "/"):
                    parent_item = test_item
                    break

            count = self._counts_cache.get(norm, 0)
            disp_name = os.path.basename(folder) or folder
            f_item = QTreeWidgetItem(parent_item, [disp_name, str(count)])
            f_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "folder", "path": folder, "count": count})
            f_item.setFlags(f_item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            is_dis = self.group_store.is_source_disabled(folder)
            f_item.setCheckState(0, Qt.CheckState.Unchecked if is_dis else Qt.CheckState.Checked)
            f_item.setExpanded(True)
            folder_items_map[norm] = f_item

        self._update_all_parent_check_states()
        self.tree.blockSignals(False)

    def _update_all_parent_check_states(self):
        """Recursively computes partial or full check state for parent items."""
        def visit(item):
            if item.childCount() == 0:
                return item.checkState(0)
            child_states = set()
            total_count = 0
            for i in range(item.childCount()):
                child = item.child(i)
                st = visit(child)
                child_states.add(st)
                # Accumulate count for virtual groups
                try:
                    c = int(child.text(1) or 0)
                    total_count += c
                except ValueError:
                    pass

            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data.get("type") == "group":
                item.setText(1, str(total_count) if total_count > 0 else "")

            if child_states == {Qt.CheckState.Checked}:
                item.setCheckState(0, Qt.CheckState.Checked)
            elif child_states == {Qt.CheckState.Unchecked}:
                item.setCheckState(0, Qt.CheckState.Unchecked)
            else:
                item.setCheckState(0, Qt.CheckState.PartiallyChecked)
            return item.checkState(0)

        for i in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(i))

    def _on_item_check_changed(self, item, column):
        if column != 0:
            return
        self.tree.blockSignals(True)
        state = item.checkState(0)

        # Propagate to children
        def set_children(p, s):
            for i in range(p.childCount()):
                c = p.child(i)
                c.setCheckState(0, s)
                set_children(c, s)

        if state in (Qt.CheckState.Checked, Qt.CheckState.Unchecked):
            set_children(item, state)

        self._update_all_parent_check_states()
        self.tree.blockSignals(False)

        # Update properties panel if this item is selected
        if self.tree.currentItem() == item:
            self._update_properties_view(item)

    def _on_item_selected(self):
        item = self.tree.currentItem()
        self._update_properties_view(item)

    def _update_properties_view(self, item):
        if not item:
            self.prop_title.setText("Ничего не выбрано")
            self.prop_type_badge.setText("—")
            self.prop_name_edit.setEnabled(False)
            self.prop_name_edit.clear()
            self.prop_path_label.setText("—")
            self.prop_count_label.setText("—")
            self.prop_avail_label.setText("—")
            self.prop_enabled_check.setEnabled(False)
            self.btn_rescan.setEnabled(False)
            self.btn_open_folder.setEnabled(False)
            self.btn_move_group.setEnabled(False)
            self.btn_delete_group.setEnabled(False)
            return

        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        itype = data.get("type")
        name = item.text(0)
        self.prop_title.setText(name)
        self.prop_name_edit.setText(name)

        is_checked = item.checkState(0) != Qt.CheckState.Unchecked
        self.prop_enabled_check.setEnabled(True)
        self.prop_enabled_check.blockSignals(True)
        self.prop_enabled_check.setChecked(is_checked)
        self.prop_enabled_check.blockSignals(False)

        count = item.text(1) or "0"
        self.prop_count_label.setText(f"{count} изображений")

        if itype == "group":
            self.prop_type_badge.setText("Виртуальная группа")
            gid = data.get("id")
            self.prop_name_edit.setEnabled(bool(gid))  # Cannot rename root "Без группы"
            self.prop_path_label.setText("Внутренняя группировка Refer")
            self.prop_avail_label.setText("Готова")
            self.prop_avail_label.setStyleSheet("color: #77dd77;")
            self.btn_rescan.setEnabled(False)
            self.btn_open_folder.setEnabled(False)
            self.btn_move_group.setEnabled(bool(gid))
            self.btn_delete_group.setEnabled(bool(gid))

        elif itype == "site":
            self.prop_type_badge.setText("Веб-источник")
            self.prop_name_edit.setEnabled(False)
            key = data.get("key", "")
            self.prop_path_label.setText(f"https://{key}")
            self.prop_avail_label.setText("Доступен онлайн")
            self.prop_avail_label.setStyleSheet("color: #77dd77;")
            self.btn_rescan.setEnabled(False)
            self.btn_open_folder.setEnabled(False)
            self.btn_move_group.setEnabled(True)
            self.btn_delete_group.setEnabled(False)

        elif itype == "folder":
            self.prop_type_badge.setText("Подключённый каталог")
            self.prop_name_edit.setEnabled(False)
            path = data.get("path", "")
            self.prop_path_label.setText(path)
            avail = os.path.isdir(path)
            self.prop_avail_label.setText("Доступен на диске" if avail else "Диск отключён / папка недоступна")
            self.prop_avail_label.setStyleSheet("color: #77dd77;" if avail else "color: #ff8888;")
            self.btn_rescan.setEnabled(avail)
            self.btn_open_folder.setEnabled(avail)
            self.btn_move_group.setEnabled(True)
            self.btn_delete_group.setEnabled(False)

    def _on_name_edited(self):
        item = self.tree.currentItem()
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if data.get("type") == "group" and data.get("id"):
            new_name = self.prop_name_edit.text().strip()
            if new_name:
                try:
                    self.group_store.rename_group(data["id"], new_name)
                    item.setText(0, new_name)
                    self.prop_title.setText(new_name)
                except (ValueError, RuntimeError) as e:
                    QMessageBox.warning(self, "Ошибка", str(e))

    def _on_prop_enabled_toggled(self, checked):
        item = self.tree.currentItem()
        if not item:
            return
        item.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def _on_tree_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return

        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        itype = data.get("type")

        menu = QMenu(self)
        if itype == "group":
            gid = data.get("id")
            if gid:
                menu.addAction("Создать подгруппу…", lambda: self._create_group(parent_id=gid))
                menu.addAction("Переименовать…", lambda: self._rename_group(gid))
                menu.addAction("Переместить в группу…", self._move_selected_to_group)
                menu.addSeparator()
                menu.addAction("Удалить группу", self._delete_selected_group)
            else:
                menu.addAction("Создать группу…", self._create_group)

        elif itype == "folder":
            menu.addAction("Пересканировать…", self._rescan_selected)
            menu.addAction("Открыть в проводнике", self._open_in_explorer)
            menu.addSeparator()
            menu.addAction("Переместить в группу…", self._move_selected_to_group)

        elif itype == "site":
            menu.addAction("Переместить в группу…", self._move_selected_to_group)

        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _create_group(self, parent_id=None):
        name, ok = QInputDialog.getText(self, "Новая группа", "Введите название группы:")
        if ok and name.strip():
            try:
                gid = self.group_store.create_group(name.strip(), parent_id=parent_id)
                self._populate_tree()
            except (ValueError, RuntimeError) as e:
                QMessageBox.warning(self, "Ошибка", str(e))

    def _rename_group(self, group_id):
        group = self.group_store.get_group(group_id)
        if not group:
            return
        name, ok = QInputDialog.getText(self, "Переименовать группу", "Название группы:", text=group["name"])
        if ok and name.strip():
            try:
                self.group_store.rename_group(group_id, name.strip())
                self._populate_tree()
            except (ValueError, RuntimeError) as e:
                QMessageBox.warning(self, "Ошибка", str(e))

    def _move_selected_to_group(self):
        item = self.tree.currentItem()
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        itype = data.get("type")

        exclude_ids = set()
        if itype == "group":
            gid = data.get("id")
            if not gid:
                return
            exclude_ids.add(gid)
            # Add all descendants to prevent cycles
            def add_desc(pid):
                for g in self.group_store.get_groups():
                    if g.get("parent_id") == pid:
                        exclude_ids.add(g["id"])
                        add_desc(g["id"])
            add_desc(gid)

        dlg = MoveToGroupDialog(self.group_store, exclude_ids, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            target_gid = dlg.selected_group_id
            try:
                if itype == "group":
                    self.group_store.move_group(data["id"], target_gid)
                elif itype == "folder":
                    self.group_store.assign_source(data["path"], target_gid)
                elif itype == "site":
                    self.group_store.assign_source(data["key"], target_gid)
                self._populate_tree()
            except (ValueError, RuntimeError) as e:
                QMessageBox.warning(self, "Ошибка перемещения", str(e))

    def _delete_selected_group(self):
        item = self.tree.currentItem()
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if data.get("type") != "group":
            return
        gid = data.get("id")
        if not gid:
            return

        # Check if group has elements
        has_children = item.childCount() > 0
        target_reassign = None

        if has_children:
            reply = QMessageBox.question(
                self,
                "Удаление группы",
                f"Группа «{item.text(0)}» содержит вложенные элементы.\nПеренести их в другую группу перед удалением?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

            exclude_ids = {gid}
            # Exclude the group and all its descendants to prevent cycles
            def add_desc(pid):
                for g in self.group_store.get_groups():
                    if g.get("parent_id") == pid:
                        exclude_ids.add(g["id"])
                        add_desc(g["id"])
            add_desc(gid)

            dlg = MoveToGroupDialog(self.group_store, exclude_ids, self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            target_reassign = dlg.selected_group_id

        try:
            self.group_store.delete_group(gid, target_group_id=target_reassign)
            self._populate_tree()
        except (ValueError, RuntimeError) as e:
            QMessageBox.warning(self, "Ошибка удаления", str(e))

    def _rescan_selected(self):
        item = self.tree.currentItem()
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if data.get("type") != "folder":
            return
        path = data.get("path")
        self.action = ("rescan", path)
        if not self._apply_and_close():
            self.action = None

    _on_rescan_folder = _rescan_selected

    def _add_folder(self):
        self.action = ("add", "")
        if not self._apply_and_close():
            self.action = None

    def _open_in_explorer(self):
        item = self.tree.currentItem()
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        path = data.get("path")
        if path and os.path.exists(path):
            if sys.platform == "win32":
                os.startfile(path)
            else:
                subprocess.Popen(["xdg-open", path])

    def _toggle_web_import(self):
        self.web_import_widget.setVisible(not self.web_import_widget.isVisible())
        if self.web_import_widget.isVisible():
            self.web_url_input.setFocus()
            # If main window is scraping, reflect state
            if self.main_window and getattr(self.main_window, "active_scraper", None):
                self.btn_web_scrape.setText("Остановить")
                self.btn_web_scrape.setStyleSheet("background-color: #552222; color: #ff8888;")
                self.web_url_input.setEnabled(False)
            else:
                self.btn_web_scrape.setText("Начать загрузку")
                self.btn_web_scrape.setStyleSheet("")
                self.web_url_input.setEnabled(True)

    def _on_web_scrape_clicked(self):
        if not self.main_window:
            return
        if getattr(self.main_window, "active_scraper", None):
            self.main_window.stop_scrape()
            self.btn_web_scrape.setText("Начать загрузку")
            self.btn_web_scrape.setStyleSheet("")
            self.web_url_input.setEnabled(True)
        else:
            url = self.web_url_input.text().strip()
            if not url:
                QMessageBox.information(self, "Ссылка", "Введите ссылку на проект для импорта.")
                return
            parser = self.web_parser_combo.currentText()
            self.main_window.start_scrape(parser, url)
            self.btn_web_scrape.setText("Остановить")
            self.btn_web_scrape.setStyleSheet("background-color: #552222; color: #ff8888;")
            self.web_url_input.setEnabled(False)

    def _open_hidden_assets(self):
        if self.main_window:
            self.main_window._open_hidden_assets()

    def _check_files(self):
        if self.main_window:
            self.main_window._cleanup_missing_files()

    def _start_indexing(self):
        if self.main_window:
            self.main_window.start_indexing()

    def _filter_tree(self, text):
        query = text.strip().lower()
        if not query:
            def show_all(item):
                item.setHidden(False)
                for i in range(item.childCount()):
                    show_all(item.child(i))
            for i in range(self.tree.topLevelItemCount()):
                show_all(self.tree.topLevelItem(i))
            return

        def filter_item(item):
            name = item.text(0).lower()
            data = item.data(0, Qt.ItemDataRole.UserRole) or {}
            extra = (data.get("path") or data.get("key") or "").lower()
            self_match = (query in name or query in extra)

            child_match = False
            for i in range(item.childCount()):
                if filter_item(item.child(i)):
                    child_match = True

            matched = self_match or child_match
            item.setHidden(not matched)
            if matched:
                item.setExpanded(True)
            return matched

        for i in range(self.tree.topLevelItemCount()):
            filter_item(self.tree.topLevelItem(i))

    def _apply_and_close(self):
        """Saves tree enabled/disabled states into draft group_store and commits draft."""
        def collect_states(item):
            data = item.data(0, Qt.ItemDataRole.UserRole) or {}
            itype = data.get("type")
            st = item.checkState(0)

            if itype == "group":
                gid = data.get("id")
                if gid:
                    self.group_store.set_group_disabled(gid, st == Qt.CheckState.Unchecked)
            elif itype == "folder":
                path = data.get("path")
                self.group_store.set_source_disabled(path, st == Qt.CheckState.Unchecked)
            elif itype == "site":
                key = data.get("key")
                self.group_store.set_source_disabled(key, st == Qt.CheckState.Unchecked)

            for i in range(item.childCount()):
                collect_states(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            collect_states(self.tree.topLevelItem(i))

        try:
            if hasattr(self, "original_store") and self.original_store is not None:
                self.original_store.commit_draft(self.group_store)
            else:
                self.group_store.save()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка сохранения", f"Не удалось сохранить изменения: {e}")
            return False

        self.accept()
        return True

    _on_accept = _apply_and_close

    @property
    def paths(self):
        return list(self._all_db_sources)

    def find_item_by_path(self, path: str):
        norm = normalized_path(path)
        def search(item):
            data = item.data(0, Qt.ItemDataRole.UserRole) or {}
            p = data.get("path") or data.get("key")
            if p and (p == path or normalized_path(p) == norm):
                return item
            for i in range(item.childCount()):
                res = search(item.child(i))
                if res:
                    return res
            return None

        for i in range(self.tree.topLevelItemCount()):
            res = search(self.tree.topLevelItem(i))
            if res:
                return res
        return None

    def values(self):
        """Backward compatible signature for tests/code expecting (assignments, disabled)."""
        assignments = self.group_store.get_source_assignments()
        disabled = tuple(s for s in self._all_db_sources if self.group_store.is_source_disabled(s))
        return assignments, disabled

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSlider, 
    QLabel, QCheckBox, QFrame, QScrollArea, QTreeWidget, QTreeWidgetItem, QMenu
)
from PyQt6.QtCore import pyqtSignal, Qt, QTimer
import os

from ui.widgets.hybrid_search_input import HybridSearchInput

class SearchPanel(QWidget):
    search_triggered = pyqtSignal(str, str, float, list) # text, image_path, threshold, sources
    clear_triggered = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._search_debounce = QTimer()
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(500)
        self._search_debounce.timeout.connect(self._emit_search)
        self._init_ui()

    def _init_ui(self):
        self.setFixedWidth(300)
        self.setStyleSheet("""
            QWidget { background-color: #121212; color: #e0e0e0; }
            QLabel { font-weight: bold; margin-top: 10px; margin-bottom: 5px; }
            
            QTreeWidget { 
                background-color: #121212; 
                border: none; 
                outline: none;
                margin-top: 5px;
            }
            QTreeWidget::item { 
                padding: 4px; 
            }
            QTreeWidget::item:hover { background-color: #1a1a1a; }
            QTreeWidget::item:selected { background-color: #1e2a30; color: #29b6f6; }
            
            QTreeWidget::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #555;
                border-radius: 3px;
                background-color: #1e1e1e;
            }
            QTreeWidget::indicator:checked {
                background-color: #29b6f6;
                border-color: #29b6f6;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)

        # Block 1: Search and Clear Actions
        actions_layout = QHBoxLayout()
        self.btn_search = QPushButton("Поиск")
        self.btn_search.setStyleSheet("background-color: #29b6f6; color: black; font-weight: bold; border-radius: 6px; padding: 8px;")
        self.btn_search.clicked.connect(self._emit_search)

        self.btn_clear = QPushButton("Очистить")
        self.btn_clear.setStyleSheet("background-color: #2d2d2d; color: white; border: 1px solid #444; border-radius: 6px; padding: 8px;")
        self.btn_clear.clicked.connect(self._clear_all)

        actions_layout.addWidget(self.btn_search, 1)
        actions_layout.addWidget(self.btn_clear, 1)
        main_layout.addLayout(actions_layout)

        # Block 2: Hybrid Input
        self.hybrid_input = HybridSearchInput()
        self.hybrid_input.search_requested.connect(self._on_hybrid_enter)
        main_layout.addWidget(self.hybrid_input)

        # Line separator
        line1 = QFrame()
        line1.setFrameShape(QFrame.Shape.HLine)
        line1.setStyleSheet("border-top: 1px solid #333;")
        main_layout.addWidget(line1)

        # Block 3: Sensitivity
        sens_header_layout = QHBoxLayout()
        lbl_sens = QLabel("Чувствительность")
        self.lbl_sens_value = QLabel("60%")
        self.lbl_sens_value.setStyleSheet("""
            font-weight: bold; font-size: 12px; color: #29b6f6;
            background-color: #1e2a30; border: 1px solid #29b6f6;
            border-radius: 4px; padding: 1px 6px;
        """)
        sens_header_layout.addWidget(lbl_sens)
        sens_header_layout.addStretch()
        sens_header_layout.addWidget(self.lbl_sens_value)
        main_layout.addLayout(sens_header_layout)

        self.slider_sens = QSlider(Qt.Orientation.Horizontal)
        self.slider_sens.setRange(0, 100)
        self.slider_sens.setValue(60)
        self.slider_sens.setStyleSheet("""
            QSlider::groove:horizontal { border: 1px solid #444; height: 4px; background: #1e1e1e; border-radius: 2px; }
            QSlider::handle:horizontal { background: #29b6f6; width: 14px; margin: -5px 0; border-radius: 7px; }
        """)
        self.slider_sens.valueChanged.connect(self._on_slider_value_changed)
        self.slider_sens.sliderReleased.connect(self._on_slider_released)
        
        sens_labels_layout = QHBoxLayout()
        lbl_wide = QLabel("Широкий")
        lbl_wide.setStyleSheet("font-size: 10px; color: #888;")
        lbl_exact = QLabel("Точный")
        lbl_exact.setStyleSheet("font-size: 10px; color: #888;")
        sens_labels_layout.addWidget(lbl_wide)
        sens_labels_layout.addStretch()
        sens_labels_layout.addWidget(lbl_exact)
        
        main_layout.addWidget(self.slider_sens)
        main_layout.addLayout(sens_labels_layout)

        # Line separator
        line2 = QFrame()
        line2.setFrameShape(QFrame.Shape.HLine)
        line2.setStyleSheet("border-top: 1px solid #333;")
        main_layout.addWidget(line2)

        # Block 4: Sources Tree
        lbl_sources = QLabel("Источники")
        main_layout.addWidget(lbl_sources)

        self.sources_tree = QTreeWidget()
        self.sources_tree.setHeaderHidden(True)
        self.sources_tree.setIndentation(15)
        self.sources_tree.setColumnCount(1)
        self.sources_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sources_tree.customContextMenuRequested.connect(self._on_context_menu)
        self.sources_tree.itemChanged.connect(self._on_item_changed)
        main_layout.addWidget(self.sources_tree, 1)

        # Static Items
        self.item_archdaily = None
        self.item_behance = None
        self.folder_items = {} # {path: QTreeWidgetItem}

    def update_custom_folders(self, folders: list):
        """Builds hierarchical tree efficiently."""
        self.sources_tree.blockSignals(True)
        
        # Preserve states
        expanded_paths = {path for path, item in self.folder_items.items() if item.isExpanded()}
        checked_paths = {path for path, item in self.folder_items.items() if item.checkState(0) == Qt.CheckState.Checked}
        
        # Preserve web source states (they stay checked if nothing was set yet)
        ad_checked = self.item_archdaily.checkState(0) == Qt.CheckState.Checked if self.item_archdaily else True
        bh_checked = self.item_behance.checkState(0) == Qt.CheckState.Checked if self.item_behance else True

        # Clear everything
        self.sources_tree.clear()
        self.folder_items.clear()

        # 1. Web Sources (ArchDaily & Behance)
        self.item_archdaily = QTreeWidgetItem(self.sources_tree, ["ArchDaily"])
        self.item_archdaily.setFlags(self.item_archdaily.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        self.item_archdaily.setCheckState(0, Qt.CheckState.Checked if ad_checked else Qt.CheckState.Unchecked)
        self.item_archdaily.setData(0, Qt.ItemDataRole.UserRole, "archdaily")

        self.item_behance = QTreeWidgetItem(self.sources_tree, ["Behance"])
        self.item_behance.setFlags(self.item_behance.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        self.item_behance.setCheckState(0, Qt.CheckState.Checked if bh_checked else Qt.CheckState.Unchecked)
        self.item_behance.setData(0, Qt.ItemDataRole.UserRole, "behance")

        # 2. Normalize and filter folder paths
        norm_folders = []
        for f in folders:
            if not f or len(f) < 4: continue
            if not all(c.isprintable() for c in f): continue
            norm_folders.append(os.path.normpath(f))
        
        norm_folders = sorted(list(set(norm_folders)))
        
        folder_icon = self.sources_tree.style().standardIcon(self.sources_tree.style().StandardPixmap.SP_DirIcon)
        root = self.sources_tree.invisibleRootItem()

        # Reordering: Move 'references' to top of local folders so it's 3rd overall
        ref_path = next((f for f in norm_folders if "references" in f.lower() and len(f) < 20), None)
        if ref_path:
            norm_folders.remove(ref_path)
            norm_folders.insert(0, ref_path)

        for path in norm_folders:
            parent_item = root
            parts = path.split(os.sep)
            
            for i in range(len(parts) - 1, 0, -1):
                test_parent = os.sep.join(parts[:i])
                if len(parts[0]) == 2 and parts[0][1] == ':' and i == 1:
                    test_parent = parts[0] + os.sep
                
                if test_parent in self.folder_items:
                    parent_item = self.folder_items[test_parent]
                    break
            
            display_name = os.path.basename(path) if parent_item != root else path
            if not display_name: display_name = path
            
            item = QTreeWidgetItem(parent_item, [display_name])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            item.setIcon(0, folder_icon)
            
            # Default check state logic:
            # ONLY ArchDaily, Behance, and the 'references' folder are checked by default
            is_checked = False
            if not checked_paths:
                # Initial run logic
                if path == ref_path:
                    is_checked = True
            else:
                # Preserve user's existing selections
                is_checked = path in checked_paths
            
            item.setCheckState(0, Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked)
            item.setData(0, Qt.ItemDataRole.UserRole, path)
            
            if path in expanded_paths:
                item.setExpanded(True)
                
            self.folder_items[path] = item

        self.sources_tree.blockSignals(False)

    def _on_context_menu(self, pos):
        item = self.sources_tree.itemAt(pos)
        if not item: return
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { background-color: #2d2d2d; color: white; border: 1px solid #444; } QMenu::item:selected { background-color: #29b6f6; color: black; }")
        solo_action = menu.addAction("Выбрать только это")
        all_action = menu.addAction("Выбрать всё")
        action = menu.exec(self.sources_tree.viewport().mapToGlobal(pos))
        if action == solo_action:
            self.sources_tree.blockSignals(True)
            def uncheck_recursive(p):
                p.setCheckState(0, Qt.CheckState.Unchecked)
                for i in range(p.childCount()): uncheck_recursive(p.child(i))
            uncheck_recursive(self.sources_tree.invisibleRootItem())
            item.setCheckState(0, Qt.CheckState.Checked)
            self.sources_tree.blockSignals(False)
            self._on_source_toggled()
        elif action == all_action:
            self._clear_all()

    def _on_item_changed(self, item, column):
        self.sources_tree.blockSignals(True)
        state = item.checkState(column)
        def update_children(parent_item):
            for i in range(parent_item.childCount()):
                child = parent_item.child(i)
                child.setCheckState(column, state)
                update_children(child)
        update_children(item)
        self.sources_tree.blockSignals(False)
        self._on_source_toggled()

    def _on_source_toggled(self):
        if not self.hybrid_input.text_input.text().strip() and not self.hybrid_input.image_path:
            self.clear_triggered.emit()

    def get_selected_sources(self) -> list:
        all_checked = []
        def collect_checked(item):
            if item.checkState(0) == Qt.CheckState.Checked:
                data = item.data(0, Qt.ItemDataRole.UserRole)
                if data: all_checked.append(data)
            for i in range(item.childCount()): collect_checked(item.child(i))
        collect_checked(self.sources_tree.invisibleRootItem())
        web_sources = [s for s in all_checked if s in ('archdaily', 'behance')]
        folder_sources = [s for s in all_checked if s not in web_sources]
        if not folder_sources: return web_sources
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
                except ValueError: continue
            if not is_covered: optimized_folders.append(folder)
        return web_sources + optimized_folders

    def _emit_search(self):
        text = self.hybrid_input.text_input.text().strip()
        img_path = self.hybrid_input.image_path
        threshold = self.slider_sens.value() / 100.0
        sources = self.get_selected_sources()
        self.search_triggered.emit(text, img_path, threshold, sources)

    def _on_hybrid_enter(self, text, img_path):
        self._emit_search()

    def _on_slider_value_changed(self, value: int):
        self.lbl_sens_value.setText(f"{value}%")

    def _on_slider_released(self):
        self._search_debounce.start()

    def _clear_all(self):
        self.hybrid_input.clear_all()
        self.slider_sens.setValue(60)
        
        self.sources_tree.blockSignals(True)
        def uncheck_recursive(item):
            item.setCheckState(0, Qt.CheckState.Unchecked)
            for i in range(item.childCount()): 
                uncheck_recursive(item.child(i))
        
        uncheck_recursive(self.sources_tree.invisibleRootItem())
        self.sources_tree.collapseAll()
        self.sources_tree.blockSignals(False)
        
        self.clear_triggered.emit()

"""Restore hidden images; rows and vector IDs stay in their original library."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QPushButton, QDialogButtonBox)
from database.models import Asset


class HiddenAssetsDialog(QDialog):
    def __init__(self, db, visibility, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Скрытые изображения")
        self.resize(850, 430)
        self.restore_ids = []
        self.db = db
        self.assets = []
        layout = QVBoxLayout(self)
        note = QLabel("Двойной клик открывает изображение. Восстановление возвращает прежние теги и избранное. Фильтры поиска и отключённые каталоги сохраняются.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["ID", "Проект", "Изображение / источник"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(1, 230)
        self.table.itemDoubleClicked.connect(self._open_image)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        entries = visibility.entries()
        ids = [int(key) for key in entries]
        self.available_ids = []
        with db.get_connection() as conn:
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(f"SELECT a.*, p.title AS project_title FROM assets a LEFT JOIN projects p ON p.id=a.project_id WHERE a.id IN ({placeholders}) ORDER BY a.id", chunk).fetchall()
                for row in rows:
                    row = dict(row)
                    if not visibility.is_hidden(row, entries):
                        continue
                    self.available_ids.append(row["id"])
                    self.assets.append(Asset(**{key: row[key] for key in Asset.__dataclass_fields__ if key in row}))
                    index = self.table.rowCount()
                    self.table.insertRow(index)
                    for column, value in enumerate([row["id"], row.get("project_title") or "", row.get("local_path") or row.get("original_url") or row.get("thumbnail_path") or ""]):
                        self.table.setItem(index, column, QTableWidgetItem(str(value)))
                        self.table.item(index, column).setToolTip(str(value))
        layout.addWidget(self.table)
        missing = len(ids) - len(self.available_ids)
        self.summary = QLabel(f"Скрыто в этой библиотеке: {len(self.available_ids)}" + (f". В другом состоянии библиотеки: {missing}" if missing else ""))
        layout.addWidget(self.summary)
        actions = QHBoxLayout()
        selected = QPushButton("Восстановить выбранные")
        selected.clicked.connect(self._restore_selected)
        all_button = QPushButton("Восстановить все")
        all_button.clicked.connect(self._restore_all)
        for button in (selected, all_button):
            button.setEnabled(bool(self.available_ids))
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        close.rejected.connect(self.reject)
        layout.addWidget(close)

    def _open_image(self, item):
        from ui.widgets.image_viewer import ImageViewerWindow
        self._viewer = ImageViewerWindow(self.db, self.parent())
        self._viewer.setParent(self, Qt.WindowType.Window)
        self._viewer.btn_delete.setEnabled(False)
        self._viewer.btn_delete.hide()
        self._viewer.set_assets(self.assets, item.row())
        self._viewer.show()

    def _restore_selected(self):
        self.restore_ids = [int(self.table.item(index.row(), 0).text()) for index in self.table.selectionModel().selectedRows()]
        if self.restore_ids:
            self.accept()

    def _restore_all(self):
        self.restore_ids = list(self.available_ids)
        self.accept()

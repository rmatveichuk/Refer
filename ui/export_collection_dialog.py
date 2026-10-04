from __future__ import annotations

import os
from pathlib import Path
from threading import Event
from typing import Optional, Dict, Any

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QComboBox, QCheckBox,
    QProgressBar, QMessageBox, QGroupBox
)

from database.collection_repository import CollectionRepository, BoardSnapshot
from export.moodboard_exporter import export_moodboard, Cancelled


class ExportWorker(QThread):
    progress = pyqtSignal(int, int, str)
    succeeded = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(
        self,
        board: BoardSnapshot,
        target_dir: str,
        export_format: str,
        export_mode: str,
        use_numbered_names: bool,
        parent=None
    ):
        super().__init__(parent)
        self.board = board
        self.target_dir = target_dir
        self.export_format = export_format
        self.export_mode = export_mode
        self.use_numbered_names = use_numbered_names
        self.cancel_event = Event()

    def run(self):
        try:
            result = export_moodboard(
                board=self.board,
                target_dir=self.target_dir,
                format=self.export_format,
                mode=self.export_mode,
                use_numbered_names=self.use_numbered_names,
                cancel=self.cancel_event,
                progress=lambda n, total, title: self.progress.emit(n, total, title)
            )
            self.succeeded.emit(result)
        except Cancelled:
            self.failed.emit("Экспорт отменён. Временные файлы удалены.")
        except Exception as e:
            self.failed.emit(f"Ошибка при экспорте: {e}")


class ExportCollectionDialog(QDialog):
    def __init__(self, collection_id: int, repository: CollectionRepository, parent=None):
        super().__init__(parent)
        self.collection_id = collection_id
        self.repository = repository
        self.worker: Optional[ExportWorker] = None
        self.output_result: Optional[Dict[str, Any]] = None

        self.board_snapshot = self.repository.snapshot(collection_id)
        self._init_ui()

    def _init_ui(self):
        self.setWindowTitle(f"Экспорт набора: {self.board_snapshot.name}")
        self.resize(540, 360)
        self.setStyleSheet("""
            QDialog { background-color: #1a1b1e; color: #e0e0e0; }
            QLabel { color: #d0d0d0; }
            QGroupBox { border: 1px solid #333; border-radius: 6px; margin-top: 10px; padding-top: 12px; font-weight: bold; color: #29b6f6; }
            QLineEdit, QComboBox { background-color: #121316; border: 1px solid #333; border-radius: 4px; padding: 6px 10px; color: #fff; }
            QPushButton { background-color: #2a2d34; border: 1px solid #444; border-radius: 4px; padding: 6px 14px; color: #fff; }
            QPushButton:hover { background-color: #353942; }
            QPushButton#btnStart { background-color: #1976D2; font-weight: bold; border-color: #2196F3; }
            QPushButton#btnStart:hover { background-color: #1565C0; }
            QProgressBar { border: 1px solid #333; border-radius: 4px; text-align: center; background: #121316; height: 18px; }
            QProgressBar::chunk { background-color: #29b6f6; }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # 1. Folder Selection
        folder_group = QGroupBox("Папка проекта")
        folder_layout = QHBoxLayout(folder_group)

        self.path_input = QLineEdit()
        initial_dir = self.board_snapshot.export_dir or str(Path.home())
        self.path_input.setText(initial_dir)
        self.path_input.setPlaceholderText("Выберите папку для сохранения...")

        browse_btn = QPushButton("Обзор…")
        browse_btn.clicked.connect(self._on_browse)

        folder_layout.addWidget(self.path_input, 1)
        folder_layout.addWidget(browse_btn)
        layout.addWidget(folder_group)

        # 2. Options Group
        options_group = QGroupBox("Параметры экспорта")
        options_layout = QVBoxLayout(options_group)

        # Format
        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("Формат:"))
        self.format_combo = QComboBox()
        self.format_combo.addItem("🌐 Веб-мудборд HTML (0 МБ на диске, ссылки на оригиналы)", "web_html")
        self.format_combo.addItem("📁 Автономный HTML (копии файлов в images)", "offline_html")
        self.format_combo.addItem("📂 Папка изображений (файлы + manifest.json)", "folder")
        fmt_row.addWidget(self.format_combo, 1)
        options_layout.addLayout(fmt_row)

        # Web mode info notice
        self.web_info_label = QLabel(
            "✨ Референсы не занимают место на диске проекта. Создаётся автономный "
            "moodboard.html со ссылками на оригиналы в полном разрешении и оффлайн-кеш."
        )
        self.web_info_label.setWordWrap(True)
        self.web_info_label.setStyleSheet("color: #29b6f6; font-size: 11px; padding: 4px 2px;")
        options_layout.addWidget(self.web_info_label)

        # Mode Widget (for offline copy / hardlink)
        self.mode_widget = QWidget()
        mode_inner_layout = QVBoxLayout(self.mode_widget)
        mode_inner_layout.setContentsMargins(0, 4, 0, 0)
        mode_inner_layout.setSpacing(8)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Файлы:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Копировать файлы (Copy) — надёжно для передачи", "copy")
        self.mode_combo.addItem("Auto (Hardlink если тот же диск, иначе копия)", "auto")
        self.mode_combo.addItem("Только Hardlink (мгновенно, 0 байт на диске)", "hardlink")
        self.mode_combo.addItem("Символические ссылки (Symlink)", "symlink")
        mode_row.addWidget(self.mode_combo, 1)
        mode_inner_layout.addLayout(mode_row)

        self.numbered_check = QCheckBox("Нумеровать файлы (001_Автор_Проект.ext)")
        self.numbered_check.setChecked(True)
        mode_inner_layout.addWidget(self.numbered_check)

        options_layout.addWidget(self.mode_widget)
        layout.addWidget(options_group)

        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
        self._on_format_changed()

        # 3. Progress and Status
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel(f"В наборе: {len(self.board_snapshot.items)} изображений")
        self.status_label.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(self.status_label)

        # 4. Buttons
        actions_layout = QHBoxLayout()
        self.btn_open = QPushButton("Открыть результат")
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self._on_open_result)

        self.btn_start = QPushButton("Экспортировать")
        self.btn_start.setObjectName("btnStart")
        self.btn_start.clicked.connect(self._on_start)

        self.btn_cancel = QPushButton("Закрыть")
        self.btn_cancel.clicked.connect(self.reject)

        actions_layout.addWidget(self.btn_open)
        actions_layout.addStretch()
        actions_layout.addWidget(self.btn_start)
        actions_layout.addWidget(self.btn_cancel)
        layout.addLayout(actions_layout)

    def _on_format_changed(self):
        fmt = self.format_combo.currentData()
        if fmt == "web_html":
            self.mode_widget.hide()
            self.web_info_label.show()
        else:
            self.mode_widget.show()
            self.web_info_label.hide()

    def _on_browse(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Выберите целевую папку",
            self.path_input.text() or str(Path.home()),
            QFileDialog.Option.ShowDirsOnly
        )
        if folder:
            self.path_input.setText(folder)

    def _on_start(self):
        target_dir = self.path_input.text().strip()
        if not target_dir:
            QMessageBox.warning(self, "Экспорт", "Укажите целевую папку для экспорта.")
            return

        if not self.board_snapshot.items:
            QMessageBox.information(self, "Экспорт", "В наборе нет изображений.")
            return

        # Save directory
        self.repository.remember_export_dir(self.collection_id, target_dir)

        # Lock UI
        self.btn_start.setEnabled(False)
        self.path_input.setEnabled(False)
        self.format_combo.setEnabled(False)
        self.mode_combo.setEnabled(False)
        self.numbered_check.setEnabled(False)
        self.btn_cancel.setText("Отмена")

        total = len(self.board_snapshot.items)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(0)
        self.status_label.setText("Подготовка файлов к экспорту…")

        fmt = self.format_combo.currentData()
        mode = "web" if fmt == "web_html" else self.mode_combo.currentData()
        use_num = False if fmt == "web_html" else self.numbered_check.isChecked()

        self.worker = ExportWorker(
            board=self.board_snapshot,
            target_dir=target_dir,
            export_format=fmt,
            export_mode=mode,
            use_numbered_names=use_num,
            parent=self
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.succeeded.connect(self._on_succeeded)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_progress(self, current: int, total: int, title: str):
        self.progress_bar.setValue(current)
        self.status_label.setText(f"Экспорт {current}/{total}: {title}")

    def _on_succeeded(self, result: dict):
        self.output_result = result
        if result.get("format") == "web_html":
            self.status_label.setText(f"✅ Готово! Создан moodboard.html ({result['count']} референсов, 0 МБ на диске)")
            self.btn_open.setText("🌐 Открыть moodboard.html")
        else:
            modes_str = ", ".join(f"{k}: {v}" for k, v in result.get("modes", {}).items())
            self.status_label.setText(f"✅ Готово! Экспортировано {result['count']} файлов ({modes_str})")
            self.btn_open.setText("Открыть результат")
        self.btn_open.setEnabled(True)

    def _on_failed(self, msg: str):
        self.status_label.setText(f"❌ {msg}")
        QMessageBox.warning(self, "Экспорт", msg)

    def _on_finished(self):
        self.worker = None
        self.btn_start.setEnabled(True)
        self.path_input.setEnabled(True)
        self.format_combo.setEnabled(True)
        self.mode_combo.setEnabled(True)
        self.numbered_check.setEnabled(True)
        self.btn_cancel.setText("Закрыть")

    def _on_open_result(self):
        if self.output_result and "entrypoint" in self.output_result:
            path = self.output_result["entrypoint"]
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def reject(self):
        if self.worker is not None:
            self.worker.cancel_event.set()
            self.status_label.setText("Отмена… удаляем временные файлы")
        else:
            super().reject()

    def closeEvent(self, event):
        if self.worker is not None:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)

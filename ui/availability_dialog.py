from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QHeaderView, QDialogButtonBox


class AvailabilityDialog(QDialog):
    def __init__(self, report, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Проверка доступности файлов")
        self.resize(950, 480)
        layout = QVBoxLayout(self)
        summary = f"Проверено: {report.checked} из {report.total or report.checked}. Проблем с доступом: {len(report.issues)}. Данные не удалялись."
        if report.cancelled:
            summary = "Проверка остановлена. " + summary
        label = QLabel(summary)
        label.setWordWrap(True)
        layout.addWidget(label)
        note = QLabel("Недоступность каталога может означать отключённый диск или отсутствие прав. Подключите источник и проверьте снова. Отсутствие файла не удаляет его теги и избранное.")
        note.setWordWrap(True)
        layout.addWidget(note)
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["ID", "Состояние", "Путь"])
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        table.setColumnWidth(1, 350)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        from collections import Counter
        offline = Counter(issue["path"] for issue in report.issues if issue["status"] == "source_unavailable")
        display = [{"id": "—", "message": f"Источник недоступен: {count} изображений", "path": path}
                   for path, count in offline.items()]
        display.extend(issue for issue in report.issues if issue["status"] != "source_unavailable")
        for row, issue in enumerate(display[:1000]):
            table.insertRow(row)
            for column, value in enumerate([issue["id"], issue["message"], issue["path"]]):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                table.setItem(row, column, item)
        layout.addWidget(table)
        if len(display) > 1000:
            layout.addWidget(QLabel("Показаны первые 1000 проблем. Уточните доступность источников и повторите проверку."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

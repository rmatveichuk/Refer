"""Run library filtering and FAISS ranking away from the GUI thread."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from PyQt6.QtCore import QObject, QRunnable, pyqtSignal
from database.search_repository import SearchRepository


class ReadOnlyDatabase:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def get_connection(self):
        connection = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()


class ResultsWorker(QRunnable):
    class Signals(QObject):
        result = pyqtSignal(int, object, object)
        error = pyqtSignal(int, str)

    def __init__(self, revision, db, faiss, visibility, filters, assignments, text, vector, limit, metadata_only):
        super().__init__()
        self.revision = revision
        self.db = ReadOnlyDatabase(db.db_path) if hasattr(db, 'db_path') else db
        self.faiss, self.visibility = faiss, visibility
        self.filters, self.assignments = filters, dict(assignments)
        self.text, self.vector, self.limit, self.metadata_only = text, vector, limit, metadata_only
        self.signals = self.Signals()

    def run(self):
        try:
            repository = SearchRepository(self.db, self.faiss, self.assignments, self.visibility)
            assets, total = repository.search(self.filters, text=self.text, vector=self.vector,
                                             limit=self.limit, metadata_only=self.metadata_only)
            self.signals.result.emit(self.revision, assets, total)
        except Exception as error:
            self.signals.error.emit(self.revision, str(error))

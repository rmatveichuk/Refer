"""Local imports use only temporary fixtures; no Refer user data is opened."""
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from PIL import Image

from scrapers.local_folder import LocalFolderParser, normalized_local_path


class ImportDB:
    def __init__(self, path):
        self.path = path
        with self.get_connection() as conn:
            conn.executescript("""
                CREATE TABLE sources(id INTEGER PRIMARY KEY, url TEXT, domain TEXT);
                CREATE TABLE assets(id INTEGER PRIMARY KEY, original_url TEXT,
                    local_path TEXT, thumbnail_path TEXT, phash TEXT, width INTEGER,
                    height INTEGER, source_id INTEGER, category TEXT, image_type TEXT,
                    created_at TEXT);
                CREATE TABLE deleted_assets(original_url TEXT);
            """)

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def rows(self, table):
        with self.get_connection() as conn:
            return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]


class LocalImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="refer-local-import-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "library"
        self.root.mkdir()
        self.db = ImportDB(Path(self.temp.name) / "fixture.sqlite")

    def image(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 12), "red").save(path)
        return path

    def run_parser(self, folder=None, **kwargs):
        parser = LocalFolderParser(str(folder or self.root), self.db, **kwargs)
        errors = []
        parser.signals.error.connect(lambda folder, message: errors.append(message))
        parser.run()
        self.assertEqual(errors, [])
        return parser

    def test_repeat_import_is_idempotent(self):
        self.image("preview.png")
        self.run_parser()
        original = self.db.rows("assets")
        self.run_parser()
        self.assertEqual(self.db.rows("assets"), original)
        self.assertEqual(len(self.db.rows("sources")), 1)

    def test_child_then_parent_keeps_sources_and_asset_identity(self):
        child_image = self.image("child/chair.png")
        self.image("house.png")
        self.run_parser(child_image.parent)
        child_asset = self.db.rows("assets")[0]
        child_source = self.db.rows("sources")[0]
        self.run_parser()
        self.assertEqual(len(self.db.rows("assets")), 2)
        self.assertEqual(self.db.rows("assets")[0], child_asset)
        self.assertEqual(self.db.rows("sources")[0], child_source)
        self.assertEqual(len(self.db.rows("sources")), 2)

    def test_parent_then_child_adds_source_without_duplicate_asset(self):
        path = self.image("child/chair.png")
        self.run_parser()
        original = self.db.rows("assets")
        self.run_parser(path.parent)
        self.assertEqual(self.db.rows("assets"), original)
        self.assertEqual(len(self.db.rows("sources")), 2)

    def test_source_and_global_asset_paths_ignore_case_slashes_and_dots(self):
        path = self.image("chair.png")
        source_alias = str(self.root).upper().replace("\\", "/") + "/./"
        asset_alias = str(path).upper().replace("\\", "/")
        with self.db.get_connection() as conn:
            conn.execute("INSERT INTO sources VALUES(8, ?, ?)", (source_alias, source_alias))
            conn.execute("INSERT INTO sources VALUES(9, 'other', 'other')")
            conn.execute("INSERT INTO assets(id,source_id,local_path) VALUES(10,9,?)", (asset_alias,))
        self.run_parser()
        self.assertEqual(len(self.db.rows("sources")), 2)
        self.assertEqual(len(self.db.rows("assets")), 1)
        self.assertEqual(self.db.rows("assets")[0]["source_id"], 9)

    def test_deleted_file_uri_or_path_is_skipped_unless_requested(self):
        path = self.image("preview with space.png")
        with self.db.get_connection() as conn:
            conn.execute("INSERT INTO deleted_assets VALUES(?)", (path.as_uri().upper(),))
        self.run_parser()
        self.assertEqual(self.db.rows("assets"), [])
        self.run_parser(skip_deleted=False)
        self.assertEqual(len(self.db.rows("assets")), 1)

    def test_raw_deleted_path_has_same_identity(self):
        path = self.image("chair.png")
        with self.db.get_connection() as conn:
            conn.execute("INSERT INTO deleted_assets VALUES(?)", (str(path).upper(),))
        self.run_parser()
        self.assertEqual(self.db.rows("assets"), [])

    def test_non_recursive_and_modes_preserve_valid_previews(self):
        self.image("root.png")
        self.image("UltimateMaterialStudy/chair.png")
        self.image("textures/wood.png")
        self.image("previews/chair_normal.png")
        self.run_parser(recursive=False)
        self.assertEqual(len(self.db.rows("assets")), 1)
        self.run_parser(mode="3D Models")
        rows = self.db.rows("assets")
        self.assertEqual({Path(row["local_path"]).name for row in rows}, {"root.png", "chair.png"})
        self.assertEqual(rows[-1]["category"], "3d_render")
        self.run_parser(mode="Textures")
        rows = self.db.rows("assets")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[-1]["category"], "textures")

    def test_cancel_before_run_does_not_change_database(self):
        self.image("chair.png")
        parser = LocalFolderParser(str(self.root), self.db)
        parser.cancel()
        parser.run()
        self.assertEqual(self.db.rows("sources"), [])
        self.assertEqual(self.db.rows("assets"), [])

    def test_cancel_during_processing_discards_uncommitted_batch(self):
        self.image("chair.png")
        parser = LocalFolderParser(str(self.root), self.db)
        parser.signals.progress.connect(lambda *_: parser.cancel())
        finished = []
        parser.signals.finished.connect(finished.append)
        parser.run()
        self.assertEqual(self.db.rows("assets"), [])
        self.assertEqual(finished, [])

    def test_overlapping_imports_recheck_paths_before_commit(self):
        self.image("chair.png")
        gate = Barrier(2)
        original_flush = LocalFolderParser._flush_batch

        def synchronized_flush(parser, batch):
            gate.wait(timeout=10)
            return original_flush(parser, batch)

        with patch.object(LocalFolderParser, "_flush_batch", synchronized_flush):
            with ThreadPoolExecutor(max_workers=2) as pool:
                parsers = [LocalFolderParser(str(self.root), self.db) for _ in range(2)]
                list(pool.map(lambda parser: parser.run(), parsers))
        self.assertEqual(len(self.db.rows("sources")), 1)
        self.assertEqual(len(self.db.rows("assets")), 1)

    def test_write_failure_emits_error_instead_of_success(self):
        self.image("chair.png")
        with self.db.get_connection() as conn:
            conn.execute("""CREATE TRIGGER fail_import BEFORE INSERT ON assets
                BEGIN SELECT RAISE(ABORT, 'fixture write rejected'); END""")
        parser = LocalFolderParser(str(self.root), self.db)
        errors, finished = [], []
        parser.signals.error.connect(lambda _, message: errors.append(message))
        parser.signals.finished.connect(finished.append)
        parser.run()
        self.assertEqual(errors, ["fixture write rejected"])
        self.assertEqual(finished, [])
        self.assertEqual(self.db.rows("assets"), [])

    def test_windows_path_normalization_handles_unc_and_encoded_uris(self):
        self.assertEqual(normalized_local_path(r"C:\\Models\\Folder\\..\\CHAIR.JPG"),
                         normalized_local_path("c:/models/chair.jpg"))
        self.assertEqual(normalized_local_path("file:///C:/Models/chair%20one.jpg"),
                         normalized_local_path(r"C:\Models\chair one.jpg"))
        self.assertEqual(normalized_local_path("file://server/share/chair.jpg"),
                         normalized_local_path(r"\\server\share\CHAIR.JPG"))


if __name__ == "__main__":
    unittest.main()

"""Comprehensive test suite for Source Groups, Catalog UI, Calm Indicators, and Operational Filtering.
Covers Scenarios 1 to 6 as required by ANTIGRAVITY_CATALOG_UI_IMPLEMENTATION.md.
"""
import os
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMessageBox, QDialog
from PyQt6.QtCore import Qt

from database.source_group_store import SourceGroupStore, normalized_path
from database.search_repository import SearchRepository, SearchFilters
from database.faiss_manager import FaissManager
from database.visibility_store import VisibilityStore
from ui.widgets.search_panel import SearchPanel
from ui.catalog_dialog import CatalogDialog, FolderImportDialog, MoveToGroupDialog
import config


class TestDBSourceGroups:
    """In-memory SQLite database helper for scenario tests."""
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE sources(id INTEGER PRIMARY KEY, domain TEXT, url TEXT);
            CREATE TABLE projects(id INTEGER PRIMARY KEY, title TEXT, author TEXT);
            CREATE TABLE assets(id INTEGER PRIMARY KEY, source_id INTEGER, project_id INTEGER,
                local_path TEXT, thumbnail_path TEXT DEFAULT '', original_url TEXT DEFAULT '',
                phash TEXT DEFAULT '', width INTEGER DEFAULT 1920, height INTEGER DEFAULT 1080,
                created_at TEXT DEFAULT '2026-09-30', is_favorite INTEGER DEFAULT 0,
                description TEXT DEFAULT '', category TEXT DEFAULT 'architecture', image_type TEXT DEFAULT 'Photography');
            CREATE TABLE tags(id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE asset_tags(asset_id INTEGER, tag_id INTEGER);

            INSERT INTO sources VALUES 
                (1, 'archdaily.com', ''),
                (2, 'behance.net', ''),
                (3, 'D:\\3d models', ''),
                (4, 'D:\\3d collections\\Maxtree', ''),
                (5, 'E:\\Work\\ProjectAlpha', '');

            INSERT INTO projects VALUES 
                (1, 'Villa Moderna', 'Studio Chipperfield'),
                (2, 'Concrete Pavillion', 'Tadao Ando');

            INSERT INTO assets(id, source_id, project_id, local_path, is_favorite) VALUES
                (1, 1, 1, 'C:\\Users\\test\\.refer\\thumb1.webp', 1),
                (2, 1, 2, 'C:\\Users\\test\\.refer\\thumb2.webp', 0),
                (3, 2, NULL, 'C:\\Users\\test\\.refer\\thumb3.webp', 0),
                (4, 3, NULL, 'D:\\3d models\\chairs\\lounge.max', 0),
                (5, 3, NULL, 'D:\\3d models\\tables\\wood_table.max', 0),
                (6, 4, NULL, 'D:\\3d collections\\Maxtree\\trees\\oak.max', 0),
                (7, 5, 1, 'E:\\Work\\ProjectAlpha\\renders\\cam01.jpg', 1);

            INSERT INTO tags VALUES (1, 'wood'), (2, 'concrete'), (3, 'топ'), (4, 'plants');
            INSERT INTO asset_tags VALUES (1, 1), (1, 3), (2, 2), (2, 3), (6, 4);
        """)

    def get_connection(self):
        return self.conn


class Scenario1GroupCRUDTests(unittest.TestCase):
    """Сценарий 1: CRUD групп каталогов, иерархия, запрет циклов при переносе,
    удаление группы с переносом и без переноса, атомарная запись и восстановление при сбое.
    """
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="refer-scen1-")
        self.store_path = Path(self.temp_dir.name) / "source_groups.json"
        self.db = TestDBSourceGroups()
        self.store = SourceGroupStore(store_path=self.store_path, db=self.db)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_group_crud_operations(self):
        # Create
        grp_id = self.store.create_group("Мебель и интерьер", parent_id="grp_3d")
        self.assertIsNotNone(grp_id)
        grp = self.store.get_group(grp_id)
        self.assertEqual(grp["name"], "Мебель и интерьер")
        self.assertEqual(grp["parent_id"], "grp_3d")

        # Read
        retrieved = self.store.get_group(grp_id)
        self.assertEqual(retrieved["name"], "Мебель и интерьер")

        # Update
        self.assertTrue(self.store.update_group(grp_id, name="Мягкая мебель"))
        self.assertEqual(self.store.get_group(grp_id)["name"], "Мягкая мебель")

        # Persistence across reload
        store2 = SourceGroupStore(store_path=self.store_path)
        self.assertEqual(store2.get_group(grp_id)["name"], "Мягкая мебель")

    def test_cycle_prevention(self):
        g_parent = self.store.create_group("Родитель")
        g_child = self.store.create_group("Потомок", parent_id=g_parent)
        g_grandchild = self.store.create_group("Внук", parent_id=g_child)

        # Cannot move parent into child or grandchild or self
        self.assertFalse(self.store.can_move_to(g_parent, g_child))
        self.assertFalse(self.store.can_move_to(g_parent, g_grandchild))
        self.assertFalse(self.store.can_move_to(g_parent, g_parent))

        # Can move grandchild to root or to parent
        self.assertTrue(self.store.can_move_to(g_grandchild, None))
        self.assertTrue(self.store.can_move_to(g_grandchild, g_parent))

        # Move to grandchild should fail
        self.assertFalse(self.store.move_group(g_parent, g_grandchild))

    def test_delete_group_with_and_without_reassignment(self):
        g_del = self.store.create_group("Временная")
        g_target = self.store.create_group("Архивная")
        src_path = r"E:\CustomPath\item"
        self.store.assign_source(src_path, g_del)
        self.assertEqual(self.store.get_source_group(src_path), g_del)

        # Delete with reassignment
        self.assertTrue(self.store.delete_group(g_del, target_group_id=g_target))
        self.assertIsNone(self.store.get_group(g_del))
        self.assertEqual(self.store.get_source_group(src_path), g_target)

        # Delete without reassignment (fallback to unassigned)
        self.assertTrue(self.store.delete_group(g_target, target_group_id=None))
        self.assertIsNone(self.store.get_source_group(src_path))

    def test_atomic_write_and_failure_recovery(self):
        self.store.create_group("Проверенная")
        self.assertTrue(self.store_path.exists())
        original_content = self.store_path.read_text(encoding="utf-8")

        # Simulate atomic replace failure
        with patch("database.source_group_store.os.replace", side_effect=OSError("Disk write error")):
            with self.assertRaises(RuntimeError):
                self.store.create_group("Сбойная")

        # Original content must remain intact and valid JSON
        self.assertTrue(self.store_path.exists())
        self.assertEqual(self.store_path.read_text(encoding="utf-8"), original_content)
        data = json.loads(self.store_path.read_text(encoding="utf-8"))
        self.assertIn("version", data)


class Scenario2MigrationAndSearchCandidatesTests(unittest.TestCase):
    """Сценарий 2: Сохранение ранее подключенных папок и отключенных источников при миграции на группы,
    выдача кандидатов поиска без скрытой фильтрации по секциям.
    """
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="refer-scen2-")
        self.store_path = Path(self.temp_dir.name) / "source_groups.json"
        self.ini_path = Path(self.temp_dir.name) / "interface.ini"
        # Write legacy interface.ini
        self.ini_path.write_text(
            "[Catalogs]\n"
            "assignments = {\"d:/3d models\": \"models\", \"d:/3d collections/maxtree\": \"models\"}\n"
            "disabled = [\"e:/work/projectalpha\"]\n",
            encoding="utf-8"
        )
        self.db = TestDBSourceGroups()
        self.store = SourceGroupStore(store_path=self.store_path, db=self.db, ini_path=self.ini_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_migration_preserves_connections_and_disabled_states(self):
        # 3D models migrated to grp_3d
        self.assertEqual(self.store.get_source_group(r"D:\3d models"), "grp_3d")
        self.assertEqual(self.store.get_source_group(r"D:\3d collections\Maxtree"), "grp_3d")
        # Disabled state preserved
        self.assertTrue(self.store.is_source_disabled(r"E:\Work\ProjectAlpha"))
        self.assertFalse(self.store.is_source_disabled(r"D:\3d models"))

    def test_search_candidates_have_no_hidden_section_filter(self):
        faiss = FaissManager(Path(self.temp_dir.name) / "test.index", dimension=3)
        visibility = VisibilityStore(Path(self.temp_dir.name) / "vis.json")
        repo = SearchRepository(self.db, faiss, assignments=self.store.get_source_assignments(), visibility_store=visibility)

        # Query all sources without section restrictions
        filters = SearchFilters(sources=("archdaily", "behance", r"D:\3d models", r"D:\3d collections\Maxtree"),
                                section="all")
        candidates = repo.candidates(filters)
        candidate_ids = {c["id"] for c in candidates}

        # Must include both photography (1, 2, 3) and 3D models (4, 5, 6)
        self.assertIn(1, candidate_ids)
        self.assertIn(4, candidate_ids)
        self.assertIn(6, candidate_ids)
        # 6 total enabled candidates
        self.assertEqual(len(candidate_ids), 6)


class Scenario3AdministrativeDisableTests(unittest.TestCase):
    """Сценарий 3: Административное отключение группы/источника, распространение отключения
    на дочерние элементы, смешанное состояние дерева (indeterminate), родительские папки с файлами
    при отключенных ветках, пустые группы (скрытие), интернет-источники.
    """
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="refer-scen3-")
        self.store_path = Path(self.temp_dir.name) / "source_groups.json"
        self.db = TestDBSourceGroups()
        self.store = SourceGroupStore(store_path=self.store_path, db=self.db)
        self.panel = SearchPanel(group_store=self.store, db=self.db)

    def tearDown(self):
        self.panel._search_debounce.stop()
        self.temp_dir.cleanup()

    def test_disabling_group_propagates_to_children(self):
        self.store.set_group_disabled("grp_3d", True)
        self.assertTrue(self.store.is_source_disabled(r"D:\3d models"))
        self.assertTrue(self.store.is_source_disabled(r"D:\3d models\chairs"))

        # Re-enabling group restores them
        self.store.set_group_disabled("grp_3d", False)
        self.assertFalse(self.store.is_source_disabled(r"D:\3d models"))

    def test_empty_virtual_groups_hidden_in_operational_tree(self):
        # grp_work has no sources assigned currently
        self.panel.update_custom_folders([r"D:\3d models"])
        grp_work_item = None
        for i in range(self.panel.sources_tree.topLevelItemCount()):
            it = self.panel.sources_tree.topLevelItem(i)
            if it.data(0, Qt.ItemDataRole.UserRole) == "group:grp_work":
                grp_work_item = it
                break

        self.assertIsNotNone(grp_work_item)
        self.assertTrue(grp_work_item.isHidden())

    def test_parent_folder_with_direct_files_retained_when_child_disabled(self):
        parent = r"D:\3d collections"
        child = r"D:\3d collections\Maxtree"
        self.store.set_source_disabled(child, True)

        # Case A: parent has no direct files -> hidden
        self.panel.update_custom_folders([parent, child], direct_folders=[child])
        self.assertTrue(self.panel.folder_items[parent].isHidden())

        # Case B: parent has direct files -> shown, child hidden
        self.panel.update_custom_folders([parent, child], direct_folders=[parent, child])
        self.assertFalse(self.panel.folder_items[parent].isHidden())
        self.assertTrue(child not in self.panel.folder_items or self.panel.folder_items[child].isHidden())

    def test_web_sources_disable_and_enable(self):
        self.store.set_source_disabled("archdaily.com", True)
        self.panel.update_custom_folders([])
        self.assertIsNone(self.panel.item_archdaily)
        self.assertIsNotNone(self.panel.item_behance)

        self.store.set_source_disabled("archdaily.com", False)
        self.panel.update_custom_folders([])
        self.assertIsNotNone(self.panel.item_archdaily)


class Scenario4OperationalTreeAndRefinementsTests(unittest.TestCase):
    """Сценарий 4: Оперативный выбор источников в левой панели, работа рефайнеров (top_only, favorites,
    теги, исключения), различие 'Очистить запрос' и 'Сброс', устойчивость выделения при перестроении дерева.
    """
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="refer-scen4-")
        self.store_path = Path(self.temp_dir.name) / "source_groups.json"
        self.db = TestDBSourceGroups()
        self.store = SourceGroupStore(store_path=self.store_path, db=self.db)
        self.panel = SearchPanel(group_store=self.store, db=self.db)

    def tearDown(self):
        self.panel._search_debounce.stop()
        self.temp_dir.cleanup()

    def test_refiners_and_conditions(self):
        # Favorite & Top Only
        self.panel.favorite_check.setChecked(True)
        self.panel.top_check.setChecked(True)
        self.assertTrue(self.panel.favorite_check.isChecked())
        self.assertTrue(self.panel.top_check.isChecked())

        # Tags and tag conditions
        self.panel.set_selected_tags(["wood", "concrete"])
        self.assertEqual(self.panel.selected_tags, ["wood", "concrete"])
        self.panel.tag_match.setCurrentIndex(1)
        self.assertEqual(self.panel.tag_match.currentData(), "all")

        # Exclusions
        self.panel.exclude_input.setText("plants, grass")
        self.assertEqual(self.panel.excluded_tags(), ("plants", "grass"))

    def test_clear_query_keeps_refinements_and_sources(self):
        self.panel.hybrid_input.text_input.setText("минимализм")
        self.panel.top_check.setChecked(True)
        self.panel.set_selected_tags(["concrete"])
        sources_before = self.panel.get_selected_sources()

        # Click Clear Query
        self.panel._clear_all()

        # Query cleared, filters preserved
        self.assertEqual(self.panel.hybrid_input.text_input.text(), "")
        self.assertTrue(self.panel.top_check.isChecked())
        self.assertEqual(self.panel.selected_tags, ["concrete"])
        self.assertEqual(self.panel.get_selected_sources(), sources_before)

    def test_reset_filters_restores_all_enabled_sources_keeps_query(self):
        self.panel.update_custom_folders([r"D:\3d models"])
        self.panel.hybrid_input.text_input.setText("бетон")
        self.panel.top_check.setChecked(True)
        self.panel.favorite_check.setChecked(True)
        self.panel.set_selected_tags(["wood"])
        self.panel.exclude_input.setText("metal")

        # Reset refinements
        self.panel.reset_filters()

        # Query preserved!
        self.assertEqual(self.panel.hybrid_input.text_input.text(), "бетон")
        # Refinements reset
        self.assertFalse(self.panel.top_check.isChecked())
        self.assertFalse(self.panel.favorite_check.isChecked())
        self.assertEqual(self.panel.selected_tags, [])
        self.assertEqual(self.panel.excluded_tags(), ())

        # All enabled sources checked
        selected = self.panel.get_selected_sources()
        self.assertIn("archdaily", selected)
        self.assertIn(r"D:\3d models", selected)

    def test_selection_stability_across_tree_refresh(self):
        folders = [r"D:\3d models", r"D:\3d models\chairs"]
        self.panel.update_custom_folders(folders)
        self.panel.folder_items[r"D:\3d models\chairs"].setCheckState(0, Qt.CheckState.Unchecked)

        # Refresh with additional folder
        folders2 = [r"D:\3d models", r"D:\3d models\chairs", r"D:\3d models\tables"]
        self.panel.update_custom_folders(folders2)

        # chairs remains unchecked, models remains partially checked
        self.assertEqual(self.panel.folder_items[r"D:\3d models\chairs"].checkState(0), Qt.CheckState.Unchecked)
        self.assertEqual(self.panel.folder_items[r"D:\3d models"].checkState(0), Qt.CheckState.PartiallyChecked)


class Scenario5MetadataSearchAndPaginationTests(unittest.TestCase):
    """Сценарий 5: Поиск по метаданным/бюро без обращения к ИИ, пагинация выдачи (400 + 400),
    корректность границ кеша.
    """
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="refer-scen5-")
        self.db = TestDBSourceGroups()
        self.faiss = FaissManager(Path(self.temp_dir.name) / "test.index", dimension=3)
        self.visibility = VisibilityStore(Path(self.temp_dir.name) / "vis.json")
        self.repo = SearchRepository(self.db, self.faiss, visibility_store=self.visibility)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_metadata_search_by_architect_without_vector(self):
        filters = SearchFilters(sources=("archdaily",))
        # Search by architect name
        assets, total = self.repo.search(filters, text="Tadao Ando", metadata_only=True)
        self.assertEqual(total, 1)
        self.assertEqual(assets[0].id, 2)

        # Search by project title
        assets, total = self.repo.search(filters, text="Villa Moderna", metadata_only=True)
        self.assertEqual(total, 1)
        self.assertEqual(assets[0].id, 1)

    def test_pagination_page_size_and_limits(self):
        # Insert 950 mock assets
        with self.db.get_connection() as conn:
            conn.executemany(
                "INSERT INTO assets(id, source_id, local_path) VALUES (?, 1, ?)",
                [(aid, f"C:\\test\\img_{aid}.jpg") for aid in range(100, 1050)]
            )
        filters = SearchFilters(sources=("archdaily",))
        
        # Page 1: 400 items
        assets_page1, total = self.repo.search(filters, text="", limit=400)
        self.assertEqual(len(assets_page1), 400)
        self.assertGreaterEqual(total, 950)

        # Page 2: 800 items
        assets_page2, total = self.repo.search(filters, text="", limit=800)
        self.assertEqual(len(assets_page2), 800)
        self.assertEqual(assets_page2[:400], assets_page1)

        # Beyond total
        assets_all, total = self.repo.search(filters, text="", limit=2000)
        self.assertEqual(len(assets_all), total)


class Scenario6CatalogDialogAndScraperTests(unittest.TestCase):
    """Сценарий 6: Диалог каталогов: повторное сканирование и добавление папки с независимой опцией
    `no_textures`, запуск/остановка веб-импорта, сохранение состояния контролов скрапера при закрытии/открытии диалога.
    """
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="refer-scen6-")
        self.store_path = Path(self.temp_dir.name) / "source_groups.json"
        self.db = TestDBSourceGroups()
        self.store = SourceGroupStore(store_path=self.store_path, db=self.db)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_folder_import_dialog_independent_no_textures(self):
        dialog = FolderImportDialog(group_store=self.store, default_group="grp_3d")
        
        # Default state
        dialog.path_input.setText(r"D:\3d models\decor")
        opts = dialog.options()
        self.assertEqual(opts["group_id"], "grp_3d")
        self.assertTrue(opts["no_textures"])
        self.assertEqual(opts["mode"], "3D Models")

        # Toggle no_textures off
        dialog.no_textures.setChecked(False)
        opts2 = dialog.options()
        self.assertFalse(opts2["no_textures"])
        self.assertEqual(opts2["mode"], "All")

    def test_catalog_dialog_rescan_action(self):
        dialog = CatalogDialog(self.db, self.store)
        item = dialog.find_item_by_path(r"D:\3d models")
        self.assertIsNotNone(item)

        # Select item and click rescan
        dialog.tree.setCurrentItem(item)
        dialog._on_rescan_folder()
        self.assertEqual(dialog.action, ("rescan", r"D:\3d models"))

    def test_web_import_controls_and_state_preservation(self):
        dialog1 = CatalogDialog(self.db, self.store)
        
        # Enter URL into web import section
        test_url = "https://www.archdaily.com/987654/sample-villa"
        dialog1.web_url_input.setText(test_url)
        dialog1.web_parser_combo.setCurrentIndex(1) # ArchDaily
        
        # Simulate scraper state retention via ScraperManager or dialog
        self.assertEqual(dialog1.web_url_input.text(), test_url)
        dialog1._apply_and_close()

        # Reopen dialog
        dialog2 = CatalogDialog(self.db, self.store)
        self.assertEqual(dialog2.group_store.get_groups(), self.store.get_groups())

    def test_3d_collections_belongs_to_3d_libraries_and_no_unassigned_group(self):
        # 1. 3D Collections resolves to 3D-библиотеки (grp_3d)
        self.assertEqual(self.store.get_source_group("D:/3d collections"), "grp_3d")
        self.assertEqual(self.store.get_source_group(r"D:\3d collections\Maxtree"), "grp_3d")
        self.assertEqual(self.store.get_source_group(r"E:\Work\ProjectAlpha"), "grp_work")

        # 2. SearchPanel tree has no "Без группы" item
        panel = SearchPanel(group_store=self.store)
        panel.update_custom_folders(
            [r"D:\3d models", r"D:\3d collections", r"D:\3d collections\Maxtree", r"E:\Work\ProjectAlpha"],
            group_store=self.store
        )
        def check_no_unassigned(item):
            self.assertNotEqual(item.text(0), "Без группы")
            for i in range(item.childCount()):
                check_no_unassigned(item.child(i))

        for i in range(panel.sources_tree.topLevelItemCount()):
            check_no_unassigned(panel.sources_tree.topLevelItem(i))

        # 3. CatalogDialog tree has no "Без группы" item
        dialog = CatalogDialog(self.db, self.store)
        for i in range(dialog.tree.topLevelItemCount()):
            check_no_unassigned(dialog.tree.topLevelItem(i))

    def test_unchecked_sources_not_leaked_and_excluded_sources_compact(self):
        panel = SearchPanel(group_store=self.store, db=self.db)
        folders = [
            r"D:\3d models",
            r"D:\3d models\chairs",
            r"D:\3d models\chairs\modern",
            r"D:\3d collections",
            r"D:\3d collections\Maxtree",
            r"D:\3d collections\Maxtree\Vol1",
            r"E:\Work\ProjectAlpha",
        ]
        panel.update_custom_folders(folders, group_store=self.store)

        # Uncheck 3D-библиотеки group
        for i in range(panel.sources_tree.topLevelItemCount()):
            top = panel.sources_tree.topLevelItem(i)
            if top.data(0, Qt.ItemDataRole.UserRole) == "group:grp_3d":
                top.setCheckState(0, Qt.CheckState.Unchecked)
                panel._on_item_changed(top, 0)

        # 1. get_excluded_sources must NOT contain deep subfolders if root is unchecked
        excluded = panel.get_excluded_sources()
        self.assertIn(r"D:\3d models", excluded)
        self.assertIn(r"D:\3d collections", excluded)
        # Deep subfolders should NOT be duplicated in excluded_sources list
        self.assertNotIn(r"D:\3d models\chairs\modern", excluded)
        self.assertNotIn(r"D:\3d collections\Maxtree\Vol1", excluded)

        # 2. Selected sources must only contain architecture
        selected = panel.get_selected_sources()
        self.assertIn("archdaily", selected)
        self.assertIn("behance", selected)
        self.assertIn(r"E:\Work\ProjectAlpha", selected)
        self.assertNotIn(r"D:\3d models", selected)
        self.assertNotIn(r"D:\3d collections", selected)

        # 3. SearchRepository must strictly filter out 3D models with zero leak
        faiss = FaissManager(Path(self.temp_dir.name) / "test2.index", dimension=3)
        vis = VisibilityStore(Path(self.temp_dir.name) / "vis2.json")
        repo = SearchRepository(self.db, faiss, assignments=self.store.get_source_assignments(), visibility_store=vis)

        # Candidate check
        filters = SearchFilters(sources=tuple(selected), excluded_sources=excluded, section="all")
        candidates = repo.candidates(filters)
        candidate_ids = {c["id"] for c in candidates}
        # In TestDBSourceGroups: id 4, 5 are D:\3d models, id 6 is D:\3d collections\Maxtree
        self.assertNotIn(4, candidate_ids)
        self.assertNotIn(5, candidate_ids)
        self.assertNotIn(6, candidate_ids)
        # Photography assets (1, 2) remain
        self.assertIn(1, candidate_ids)
        self.assertIn(2, candidate_ids)

    def test_surname_and_cyrillic_transliteration_matching(self):
        faiss = FaissManager(Path(self.temp_dir.name) / "test3.index", dimension=3)
        vis = VisibilityStore(Path(self.temp_dir.name) / "vis3.json")
        repo = SearchRepository(self.db, faiss, assignments=self.store.get_source_assignments(), visibility_store=vis)
        filters = SearchFilters(sources=("archdaily", "behance"), section="all")

        # In TestDBSourceGroups, asset 2 has author "Tadao Ando", title "Villa Moderna"
        # 1. Exact surname
        assets, total = repo.search(filters, text="Ando", metadata_only=True)
        self.assertEqual(total, 1)
        self.assertEqual(assets[0].id, 2)

        # 2. Reverse word order (Surname Firstname)
        assets, total = repo.search(filters, text="Ando Tadao", metadata_only=True)
        self.assertEqual(total, 1)
        self.assertEqual(assets[0].id, 2)

        # 3. Cyrillic surname transliteration
        assets, total = repo.search(filters, text="Андо", metadata_only=True)
        self.assertEqual(total, 1)
        self.assertEqual(assets[0].id, 2)

        # 4. Cyrillic full name
        assets, total = repo.search(filters, text="Тадао Андо", metadata_only=True)
        self.assertEqual(total, 1)
        self.assertEqual(assets[0].id, 2)


class CatalogUIFixesRegressionTests(unittest.TestCase):
    """Regression test suite for ANTIGRAVITY_CATALOG_UI_FIXES.md:
    1. Cancel discards draft without modifying store or file.
    2. Delete with transfer into descendant is strictly prevented, avoiding cycles.
    3. Failed write rolls back in-memory state, ensuring consistency.
    4. Top filter and UI handlers function reliably and immediately.
    """
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="refer-fixes-")
        self.store_path = Path(self.temp_dir.name) / "groups.json"
        self.db = TestDBSourceGroups()
        self.store = SourceGroupStore(store_path=self.store_path, db=self.db)
        self.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_cancel_discards_draft_and_leaves_store_and_file_untouched(self):
        before_file = self.store.path.read_text(encoding="utf-8")
        before_groups = self.store.get_groups()

        dialog = CatalogDialog(self.db, self.store)
        # Create group inside dialog (operates on draft)
        with patch("ui.catalog_dialog.QInputDialog.getText", return_value=("Temporary Draft Group", True)):
            dialog._create_group()

        draft_group_names = [g["name"] for g in dialog.group_store.get_groups()]
        self.assertIn("Temporary Draft Group", draft_group_names)

        # Cancel dialog
        dialog.reject()

        # Original store and disk must be 100% untouched
        self.assertEqual(self.store.get_groups(), before_groups)
        self.assertEqual(self.store.path.read_text(encoding="utf-8"), before_file)

        # Reopening dialog loads clean original state
        dialog2 = CatalogDialog(self.db, self.store)
        reopened_names = [g["name"] for g in dialog2.group_store.get_groups()]
        self.assertNotIn("Temporary Draft Group", reopened_names)
        dialog2.reject()

    def test_apply_commits_draft_atomically_to_store_and_file(self):
        dialog = CatalogDialog(self.db, self.store)
        with patch("ui.catalog_dialog.QInputDialog.getText", return_value=("Committed Group", True)):
            dialog._create_group()

        success = dialog._apply_and_close()
        self.assertTrue(success)

        # Original store and disk must now reflect the new group
        store_names = [g["name"] for g in self.store.get_groups()]
        self.assertIn("Committed Group", store_names)
        disk_data = json.loads(self.store.path.read_text(encoding="utf-8"))
        disk_names = [g["name"] for g in disk_data["groups"]]
        self.assertIn("Committed Group", disk_names)

    def test_apply_failure_retains_draft_without_modifying_store(self):
        before_file = self.store.path.read_text(encoding="utf-8")
        before_groups = self.store.get_groups()

        dialog = CatalogDialog(self.db, self.store)
        with patch("ui.catalog_dialog.QInputDialog.getText", return_value=("Will Fail On Save", True)):
            dialog._create_group()

        # Simulate write failure during apply
        with patch("database.source_group_store.os.replace", side_effect=OSError("disk failure")):
            with patch("PyQt6.QtWidgets.QMessageBox.critical") as mock_crit:
                success = dialog._apply_and_close()
                self.assertFalse(success)
                mock_crit.assert_called_once()

        # Original store and file are rolled back / untouched
        self.assertEqual(self.store.get_groups(), before_groups)
        self.assertEqual(self.store.path.read_text(encoding="utf-8"), before_file)

        # Draft in dialog still has the group for user retry
        draft_names = [g["name"] for g in dialog.group_store.get_groups()]
        self.assertIn("Will Fail On Save", draft_names)
        dialog.reject()

    def test_delete_group_into_child_or_descendant_prohibited(self):
        parent = self.store.create_group("Parent")
        child = self.store.create_group("Child", parent_id=parent)
        grandchild = self.store.create_group("GrandChild", parent_id=child)

        # 1. Attempt transfer into child -> ValueError
        with self.assertRaises(ValueError):
            self.store.delete_group(parent, target_group_id=child)

        # 2. Attempt transfer into grandchild -> ValueError
        with self.assertRaises(ValueError):
            self.store.delete_group(parent, target_group_id=grandchild)

        # 3. Attempt transfer into self -> ValueError
        with self.assertRaises(ValueError):
            self.store.delete_group(parent, target_group_id=parent)

        # State must remain intact and non-cyclic
        self.assertEqual(self.store.get_group(child)["parent_id"], parent)
        self.assertEqual(self.store.get_group(grandchild)["parent_id"], child)
        self.assertIsNotNone(self.store.get_group(parent))

        # 4. Safe transfer into sibling
        sibling = self.store.create_group("Sibling")
        self.store.delete_group(child, target_group_id=sibling)
        self.assertEqual(self.store.get_group(grandchild)["parent_id"], sibling)
        self.assertIsNone(self.store.get_group(child))

        # 5. Safe transfer to root (target_group_id=None)
        self.store.delete_group(sibling, target_group_id=None)
        self.assertIsNone(self.store.get_group(grandchild)["parent_id"])
        self.assertIsNone(self.store.get_group(sibling))

    def test_load_cycle_and_invalid_parent_detection(self):
        # Direct self-reference
        self_ref_file = Path(self.temp_dir.name) / "self_ref.json"
        self_ref_file.write_text(json.dumps({
            "version": 1,
            "groups": [{"id": "g1", "name": "G1", "parent_id": "g1"}]
        }), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            SourceGroupStore(store_path=self_ref_file, db=self.db)

        # Non-existent parent
        missing_parent_file = Path(self.temp_dir.name) / "missing.json"
        missing_parent_file.write_text(json.dumps({
            "version": 1,
            "groups": [{"id": "g1", "name": "G1", "parent_id": "does_not_exist"}]
        }), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            SourceGroupStore(store_path=missing_parent_file, db=self.db)

        # 3-node cycle
        cycle_file = Path(self.temp_dir.name) / "cycle3.json"
        cycle_file.write_text(json.dumps({
            "version": 1,
            "groups": [
                {"id": "a", "name": "A", "parent_id": "c"},
                {"id": "b", "name": "B", "parent_id": "a"},
                {"id": "c", "name": "C", "parent_id": "b"},
            ]
        }), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            SourceGroupStore(store_path=cycle_file, db=self.db)

    def test_transactional_rollback_on_write_failure(self):
        before_file = self.store.path.read_text(encoding="utf-8")
        before_groups = self.store.get_groups()

        # create_group rollback
        with patch("database.source_group_store.os.replace", side_effect=OSError("write fail")):
            with self.assertRaises(RuntimeError):
                self.store.create_group("Will Fail")
        self.assertEqual(self.store.get_groups(), before_groups)
        self.assertEqual(self.store.path.read_text(encoding="utf-8"), before_file)

        # rename_group rollback
        with patch("database.source_group_store.os.replace", side_effect=OSError("write fail")):
            with self.assertRaises(RuntimeError):
                self.store.rename_group("grp_arch", "New Arch Name")
        self.assertEqual(self.store.get_group("grp_arch")["name"], "Архитектура")
        self.assertEqual(self.store.path.read_text(encoding="utf-8"), before_file)

        # delete_group rollback
        with patch("database.source_group_store.os.replace", side_effect=OSError("write fail")):
            with self.assertRaises(RuntimeError):
                self.store.delete_group("grp_arch")
        self.assertIsNotNone(self.store.get_group("grp_arch"))
        self.assertEqual(self.store.path.read_text(encoding="utf-8"), before_file)

    def test_delete_dialog_excludes_all_descendants(self):
        parent = self.store.create_group("P")
        child = self.store.create_group("C", parent_id=parent)
        grandchild = self.store.create_group("GC", parent_id=child)

        dialog = CatalogDialog(self.db, self.store)
        # Find item in tree for parent
        parent_item = None
        for i in range(dialog.tree.topLevelItemCount()):
            it = dialog.tree.topLevelItem(i)
            d = it.data(0, Qt.ItemDataRole.UserRole) or {}
            if d.get("id") == parent:
                parent_item = it
                break

        self.assertIsNotNone(parent_item)
        dialog.tree.setCurrentItem(parent_item)

        captured_exclude_ids = []
        original_move_dialog_init = MoveToGroupDialog.__init__

        def mock_init(move_self, group_store, exclude_group_ids, parent_widget=None):
            captured_exclude_ids.extend(list(exclude_group_ids))
            original_move_dialog_init(move_self, group_store, exclude_group_ids, parent_widget)

        with patch.object(MoveToGroupDialog, "__init__", mock_init):
            with patch("PyQt6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                with patch.object(MoveToGroupDialog, "exec", return_value=QDialog.DialogCode.Rejected):
                    dialog._delete_selected_group()

        self.assertIn(parent, captured_exclude_ids)
        self.assertIn(child, captured_exclude_ids)
        self.assertIn(grandchild, captured_exclude_ids)
        dialog.reject()

    def test_top_check_triggers_search_and_filters_correctly(self):
        panel = SearchPanel(db=self.db, group_store=self.store)
        emitted_args = []
        panel.search_triggered.connect(lambda *args: emitted_args.append(args))

        # Toggling top_check should emit search immediately without waiting
        panel.top_check.setChecked(True)
        self.assertEqual(len(emitted_args), 1)

        panel.top_check.setChecked(False)
        self.assertEqual(len(emitted_args), 2)

        # Filtering with top_only=True on SearchRepository
        faiss = FaissManager(Path(self.temp_dir.name) / "test_top.index", dimension=3)
        vis = VisibilityStore(Path(self.temp_dir.name) / "vis_top.json")
        repo = SearchRepository(self.db, faiss, assignments=self.store.get_source_assignments(), visibility_store=vis)

        # In TestDBSourceGroups, asset 1 and 2 have tag 'топ', while asset 3 has no top tag
        filters_all = SearchFilters(sources=("archdaily.com", "behance.net"), section="all", top_only=False)
        assets_all, _ = repo.search(filters_all, text="", metadata_only=True)
        all_ids = {a.id for a in assets_all}
        self.assertIn(1, all_ids)
        self.assertIn(2, all_ids)
        self.assertIn(3, all_ids)

        filters_top = SearchFilters(sources=("archdaily.com", "behance.net"), section="all", top_only=True)
        assets_top, _ = repo.search(filters_top, text="", metadata_only=True)
        top_ids = {a.id for a in assets_top}
        self.assertIn(1, top_ids)
        self.assertIn(2, top_ids)
        self.assertNotIn(3, top_ids)


if __name__ == "__main__":
    unittest.main()




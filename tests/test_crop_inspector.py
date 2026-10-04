"""Unit tests for crop inspection, photometry analysis of cropped regions, and bbox validation."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from database.db_manager import DatabaseManager
from database.visibility_store import VisibilityStore
from mcp_server.crop_inspector import inspect_asset_crop
import mcp_server.crop_inspector as ci


class CropInspectorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="refer_test_crop_"))
        self.db_path = self.temp_dir / "test_crop.db"
        self.hidden_path = self.temp_dir / "hidden.json"

        # Create two-color test image (Left: RED, Right: BLUE)
        self.img_path = self.temp_dir / "two_color.png"
        img = Image.new("RGB", (400, 200))
        # Left half red
        for x in range(200):
            for y in range(200):
                img.putpixel((x, y), (255, 0, 0))
        # Right half blue
        for x in range(200, 400):
            for y in range(200):
                img.putpixel((x, y), (0, 0, 255))
        img.save(self.img_path)

        self.db_mgr = DatabaseManager(self.db_path)
        with self.db_mgr.get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sources (id INTEGER PRIMARY KEY, url TEXT, domain TEXT);
                CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY, title TEXT, url TEXT, author TEXT);
                CREATE TABLE IF NOT EXISTS assets (
                    id INTEGER PRIMARY KEY,
                    original_url TEXT,
                    local_path TEXT,
                    thumbnail_path TEXT,
                    phash TEXT,
                    width INTEGER,
                    height INTEGER,
                    source_id INTEGER,
                    project_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    image_type TEXT,
                    description TEXT
                );
            """)
            conn.execute("INSERT INTO projects (id, title, author) VALUES (1, 'Color Test', 'Tester')")
            conn.execute("""
                INSERT INTO assets (id, project_id, local_path, thumbnail_path, phash, width, height)
                VALUES (1, 1, ?, ?, 'testhash', 400, 200)
            """, (str(self.img_path), str(self.img_path)))
            conn.commit()

        # Patch get_db_manager in crop_inspector
        self.orig_get_db = ci.get_db_manager
        ci.get_db_manager = lambda: self.db_mgr

    def tearDown(self):
        ci.get_db_manager = self.orig_get_db
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_left_and_right_crop_different_palettes(self):
        """P2.5: Left crop (red) and right crop (blue) must have distinctly different palettes and warmth."""
        # 1. Left crop (Red)
        bytes_left, meta_left = inspect_asset_crop(asset_id=1, bbox_norm=[0.0, 0.0, 0.5, 1.0])
        self.assertTrue(meta_left["crop_applied"])
        self.assertEqual(meta_left["measurement_region"], "crop")
        self.assertEqual(meta_left["view_dimensions"], [200, 200])

        warmth_left = meta_left["warmth_palette"]
        hex_left = meta_left["palette"][0]["hex"].lower()

        # 2. Right crop (Blue)
        bytes_right, meta_right = inspect_asset_crop(asset_id=1, bbox_norm=[0.5, 0.0, 1.0, 1.0])
        self.assertTrue(meta_right["crop_applied"])
        self.assertEqual(meta_right["measurement_region"], "crop")
        self.assertEqual(meta_right["view_dimensions"], [200, 200])

        warmth_right = meta_right["warmth_palette"]
        hex_right = meta_right["palette"][0]["hex"].lower()

        # Check distinctness
        self.assertNotEqual(hex_left, hex_right)
        self.assertGreater(warmth_left, 0.6, "Red region should be warm")
        self.assertLess(warmth_right, 0.4, "Blue region should be cool")
        self.assertGreater(warmth_left - warmth_right, 0.3)

    def test_invalid_bbox_raises_error(self):
        """P2.5: Invalid bbox (NaN, wrong length, inverted/empty) raises explicit ValueError."""
        # NaN coordinate
        with self.assertRaises(ValueError):
            inspect_asset_crop(asset_id=1, bbox_norm=[float("nan"), 0.0, 1.0, 1.0])

        # Wrong length
        with self.assertRaises(ValueError):
            inspect_asset_crop(asset_id=1, bbox_norm=[0.0, 0.5])

        # Inverted x
        with self.assertRaises(ValueError):
            inspect_asset_crop(asset_id=1, bbox_norm=[0.8, 0.0, 0.2, 1.0])

        # Out of bounds
        with self.assertRaises(ValueError):
            inspect_asset_crop(asset_id=1, bbox_norm=[-0.1, 0.0, 1.0, 1.0])

    def test_full_image_inspection(self):
        """When bbox_norm is None, inspects full frame and reports measurement_region='full'."""
        img_bytes, meta = inspect_asset_crop(asset_id=1, bbox_norm=None)
        self.assertFalse(meta["crop_applied"])
        self.assertEqual(meta["measurement_region"], "full")
        self.assertEqual(meta["view_dimensions"], [400, 200])


if __name__ == "__main__":
    unittest.main()

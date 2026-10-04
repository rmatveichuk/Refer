"""Regression checks for legacy original paths and valid thumbnails."""
import tempfile
import unittest
from pathlib import Path

from mcp_server.image_paths import resolve_image_path
from mcp_server.preview_generator import generate_contact_sheet
from PIL import Image


class ImagePathTests(unittest.TestCase):
    def test_legacy_original_uses_thumbnail_in_contact_sheet(self):
        with tempfile.TemporaryDirectory() as directory:
            thumbnail = Path(directory) / "thumbnail.png"
            Image.new("RGB", (40, 40), "blue").save(thumbnail)
            row = {"local_path": str(Path(directory) / "old" / "missing.png"),
                   "thumbnail_path": str(thumbnail)}
            path = resolve_image_path(row)
            self.assertEqual(path, thumbnail)
            data, manifest = generate_contact_sheet([
                {"asset_id": 1, "image_path": path, "project": "Test"}
            ])
            self.assertTrue(data)
            self.assertTrue(manifest["cells"][0]["available"])

    def test_existing_original_is_preferred(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "original.png"
            original.touch()
            self.assertEqual(resolve_image_path({"local_path": str(original),
                "thumbnail_path": "missing.png"}), original)

    def test_missing_files_and_directories_are_not_images(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(resolve_image_path({"local_path": directory,
                "thumbnail_path": str(Path(directory) / "missing.png")}))
            self.assertIsNone(resolve_image_path({"local_path": None,
                "thumbnail_path": ""}))

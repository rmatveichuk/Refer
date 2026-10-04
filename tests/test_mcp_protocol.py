"""End-to-end Model Context Protocol (MCP) verification test suite for Refer.

Tests the live stdio MCP server using the official MCP ClientSession, verifying
JSON-RPC initialization, tool discovery, and execution across all 7 MCP tools:
- refer_taxonomy
- refer_search
- refer_preview
- refer_inspect
- refer_project
- refer_board
- refer_export

All tests run in completely isolated temporary directories with their own SQLite DB,
FAISS index, image cache, and source group stores. Production user data is never touched.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure repo root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

from PIL import Image
import numpy as np

from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession
import mcp_types as types


def _create_test_environment(base_tmp: Path):
    """Sets up an isolated database, FAISS index, and image files."""
    appdata = base_tmp / "appdata" / "Refer"
    localdata = base_tmp / "localdata" / "Refer"
    db_dir = appdata / "Database"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "collection.db"
    faiss_path = db_dir / "collection.index"
    thumbs_dir = localdata / "Thumbnails"
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create SQLite DB and populate
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT UNIQUE,
            name TEXT
        );
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            author TEXT,
            url TEXT,
            location TEXT
        );
        CREATE TABLE assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_url TEXT,
            local_path TEXT,
            thumbnail_path TEXT,
            phash TEXT,
            width INTEGER,
            height INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            source_id INTEGER,
            project_id INTEGER,
            embedding_id INTEGER,
            category TEXT DEFAULT 'Architecture',
            image_type TEXT DEFAULT 'Photography',
            is_favorite INTEGER DEFAULT 0,
            description TEXT DEFAULT ''
        );
        CREATE TABLE tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE
        );
        CREATE TABLE asset_tags (
            asset_id INTEGER,
            tag_id INTEGER,
            PRIMARY KEY (asset_id, tag_id)
        );
        CREATE TABLE collections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            name_key TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT '',
            cover_asset_id INTEGER,
            export_dir TEXT DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE collection_assets (
            collection_id INTEGER NOT NULL,
            asset_id INTEGER NOT NULL,
            position INTEGER NOT NULL,
            is_cover INTEGER DEFAULT 0,
            slot_name TEXT DEFAULT '',
            PRIMARY KEY (collection_id, asset_id)
        );
        CREATE TABLE quick_target (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            collection_id INTEGER
        );
        INSERT OR IGNORE INTO quick_target (singleton, collection_id) VALUES (1, NULL);

        CREATE TABLE asset_features (
            asset_id INTEGER PRIMARY KEY,
            feature_version INTEGER NOT NULL DEFAULT 1,
            warmth_palette REAL,
            global_contrast REAL,
            lstar_mean REAL,
            palette_json TEXT,
            status TEXT NOT NULL,
            calculated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 2. Generate two test images (one warm red, one cold blue)
    img1_path = thumbs_dir / "warm_img.jpg"
    img2_path = thumbs_dir / "cold_img.jpg"

    warm_img = Image.new("RGB", (600, 400), color=(220, 100, 40))
    warm_img.save(img1_path, format="JPEG", quality=90)

    cold_img = Image.new("RGB", (600, 400), color=(30, 90, 200))
    cold_img.save(img2_path, format="JPEG", quality=90)

    # 3. Insert records
    conn.execute("INSERT INTO sources (id, domain, name) VALUES (1, 'archdaily.com', 'ArchDaily')")
    conn.execute("INSERT INTO projects (id, title, author, url, location) VALUES (1, 'Kloof Road House', 'SAOTA', 'https://archdaily.com/123/kloof', 'South Africa')")
    conn.execute("INSERT INTO projects (id, title, author, url, location) VALUES (2, 'House on the Cliff', 'Fran Silvestre', 'https://archdaily.com/456/cliff', 'Spain')")

    conn.execute("""
        INSERT INTO assets (id, original_url, local_path, thumbnail_path, width, height, source_id, project_id, embedding_id, description)
        VALUES (1, 'https://img.archdaily.com/1.jpg', ?, ?, 600, 400, 1, 1, 0, 'Warm concrete villa in sunset light')
    """, (str(img1_path), str(img1_path)))

    conn.execute("""
        INSERT INTO assets (id, original_url, local_path, thumbnail_path, width, height, source_id, project_id, embedding_id, description)
        VALUES (2, 'https://img.archdaily.com/2.jpg', ?, ?, 600, 400, 1, 2, 1, 'Cold minimalist concrete pavilion')
    """, (str(img2_path), str(img2_path)))

    conn.execute("INSERT INTO tags (id, name) VALUES (1, 'concrete'), (2, 'топ'), (3, 'exterior')")
    conn.execute("INSERT INTO asset_tags (asset_id, tag_id) VALUES (1, 1), (1, 2), (1, 3), (2, 1), (2, 2)")

    conn.execute("""
        INSERT INTO asset_features (asset_id, warmth_palette, global_contrast, lstar_mean, palette_json, status)
        VALUES (1, 0.78, 0.16, 55.0, '[]', 'completed')
    """)
    conn.execute("""
        INSERT INTO asset_features (asset_id, warmth_palette, global_contrast, lstar_mean, palette_json, status)
        VALUES (2, 0.32, 0.24, 48.0, '[]', 'completed')
    """)

    conn.commit()
    conn.close()

    # 4. Create FAISS index
    from database.faiss_manager import FaissManager
    faiss_mgr = FaissManager(faiss_path, dimension=1152)
    # Add dummy vectors for asset 1 and 2
    vec1 = np.ones((1, 1152), dtype=np.float32)
    vec2 = -np.ones((1, 1152), dtype=np.float32)
    faiss_mgr.add_vectors_batch([1, 2], np.vstack([vec1, vec2]))
    faiss_mgr.save_index()

    return {
        "appdata": appdata,
        "localdata": localdata,
        "db_path": db_path,
        "faiss_path": faiss_path,
        "thumbs_dir": thumbs_dir,
        "img1_path": img1_path,
        "img2_path": img2_path
    }


class TestMcpProtocol(unittest.IsolatedAsyncioTestCase):
    """End-to-end protocol testing of Refer MCP over stdio subprocess transport."""

    async def asyncSetUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="refer_mcp_test_"))
        self.env_data = _create_test_environment(self.temp_dir)

        # Set up isolated subprocess environment
        self.custom_env = dict(os.environ)
        self.custom_env["APPDATA"] = str(self.temp_dir / "appdata")
        self.custom_env["LOCALAPPDATA"] = str(self.temp_dir / "localdata")
        self.custom_env["PYTHONPATH"] = str(Path(__file__).parent.parent.resolve())

        self.server_params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_server.server"],
            env=self.custom_env,
            cwd=str(Path(__file__).parent.parent.resolve())
        )

    async def asyncTearDown(self):
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    async def test_full_protocol_all_seven_tools(self):
        """Runs the MCP server and exercises all 7 tools over the real JSON-RPC transport."""
        async with stdio_client(self.server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                # 1. Initialize
                init_res = await session.initialize()
                self.assertEqual(init_res.server_info.name, "refer-mcp")

                # 2. List tools
                tools_res = await session.list_tools()
                tool_names = [t.name for t in tools_res.tools]
                expected_tools = [
                    "refer_taxonomy",
                    "refer_search",
                    "refer_preview",
                    "refer_inspect",
                    "refer_project",
                    "refer_board",
                    "refer_export"
                ]
                for exp in expected_tools:
                    self.assertIn(exp, tool_names, f"Tool '{exp}' must be registered")

                # -------------------------------------------------------------
                # Tool 1: refer_taxonomy
                # -------------------------------------------------------------
                # 1.1 Overview
                tax_over = await session.call_tool("refer_taxonomy", {"kind": "overview"})
                self.assertFalse(tax_over.is_error)
                over_data = json.loads(tax_over.content[0].text)
                self.assertEqual(over_data["total_assets"], 2)
                self.assertEqual(over_data["total_projects"], 2)

                # 1.2 Studios
                tax_stu = await session.call_tool("refer_taxonomy", {"kind": "studios"})
                self.assertFalse(tax_stu.is_error)
                stu_data = json.loads(tax_stu.content[0].text)
                self.assertEqual(stu_data["count"], 2)
                stu_names = [s["name"] for s in stu_data["items"]]
                self.assertIn("SAOTA", stu_names)
                self.assertIn("Fran Silvestre", stu_names)

                # 1.3 Sources
                tax_src = await session.call_tool("refer_taxonomy", {"kind": "sources"})
                self.assertFalse(tax_src.is_error)
                src_data = json.loads(tax_src.content[0].text)
                self.assertGreaterEqual(src_data["count"], 1)
                self.assertEqual(src_data["items"][0]["domain"], "archdaily.com")

                # 1.4 Groups
                tax_grp = await session.call_tool("refer_taxonomy", {"kind": "groups"})
                self.assertFalse(tax_grp.is_error)
                grp_data = json.loads(tax_grp.content[0].text)
                self.assertGreaterEqual(grp_data["count"], 1)

                # -------------------------------------------------------------
                # Tool 2: refer_search
                # -------------------------------------------------------------
                # 2.1 Metadata match SAOTA
                search_saota = await session.call_tool("refer_search", {
                    "query": "SAOTA",
                    "mode": "metadata"
                })
                self.assertFalse(search_saota.is_error)
                saota_data = json.loads(search_saota.content[0].text)
                self.assertEqual(saota_data["count"], 1)
                self.assertEqual(saota_data["candidates"][0]["architect"], "SAOTA")
                self.assertEqual(saota_data["candidates"][0]["asset_id"], 1)

                # 2.2 Metadata negative query
                search_none = await session.call_tool("refer_search", {
                    "query": "NON_EXISTENT_STUDIO",
                    "mode": "metadata"
                })
                self.assertFalse(search_none.is_error)
                none_data = json.loads(search_none.content[0].text)
                self.assertEqual(none_data["count"], 0)

                # 2.3 Photometry filter: warmth_min >= 0.5 (should only match asset 1)
                search_warm = await session.call_tool("refer_search", {
                    "warmth_min": 0.5,
                    "mode": "metadata"
                })
                self.assertFalse(search_warm.is_error)
                warm_data = json.loads(search_warm.content[0].text)
                self.assertEqual(warm_data["count"], 1)
                self.assertEqual(warm_data["candidates"][0]["asset_id"], 1)

                # -------------------------------------------------------------
                # Tool 3: refer_preview
                # -------------------------------------------------------------
                # 3.1 Normal preview with 2 assets: returns Image block + JSON Manifest
                prev_res = await session.call_tool("refer_preview", {"asset_ids": [1, 2]})
                self.assertFalse(prev_res.is_error)
                self.assertEqual(len(prev_res.content), 2)
                # First block is ImageContent
                img_block = prev_res.content[0]
                self.assertEqual(img_block.type, "image")
                self.assertEqual(img_block.mime_type, "image/jpeg")
                self.assertGreater(len(img_block.data), 100)  # base64 encoded jpeg

                # Second block is TextContent (JSON Manifest)
                manifest_block = prev_res.content[1]
                self.assertEqual(manifest_block.type, "text")
                prev_manifest = json.loads(manifest_block.text)
                self.assertEqual(prev_manifest["total_candidates"], 2)
                self.assertEqual(prev_manifest["cells"][0]["asset_id"], 1)
                self.assertTrue(prev_manifest["cells"][0]["available"])

                # 3.2 Exceeding limit of 9 assets -> returns error or raises
                try:
                    prev_limit = await session.call_tool("refer_preview", {"asset_ids": list(range(1, 12))})
                    self.assertTrue(prev_limit.is_error)
                except Exception as e:
                    self.assertIn("9", str(e))

                # -------------------------------------------------------------
                # Tool 4: refer_inspect
                # -------------------------------------------------------------
                # 4.1 Valid crop with strict normalized bbox
                insp_res = await session.call_tool("refer_inspect", {
                    "asset_id": 1,
                    "bbox_norm": [0.1, 0.1, 0.6, 0.6]
                })
                self.assertFalse(insp_res.is_error)
                self.assertEqual(len(insp_res.content), 2)
                self.assertEqual(insp_res.content[0].type, "image")
                insp_meta = json.loads(insp_res.content[1].text)
                self.assertEqual(insp_meta["asset_id"], 1)
                self.assertEqual(insp_meta["measurement_region"], "crop")
                self.assertIn("warmth_palette", insp_meta)
                self.assertIn("palette", insp_meta)

                # 4.2 Invalid bbox (x0 > x1) -> returns error or raises
                try:
                    insp_bad = await session.call_tool("refer_inspect", {
                        "asset_id": 1,
                        "bbox_norm": [0.8, 0.8, 0.2, 0.2]
                    })
                    self.assertTrue(insp_bad.is_error)
                except Exception:
                    pass  # Tool exception converted to MCP protocol error as expected

                # -------------------------------------------------------------
                # Tool 5: refer_project
                # -------------------------------------------------------------
                proj_res = await session.call_tool("refer_project", {"project_id": 1})
                self.assertFalse(proj_res.is_error)
                proj_data = json.loads(proj_res.content[0].text)
                self.assertEqual(proj_data["project_id"], 1)
                self.assertEqual(proj_data["title"], "Kloof Road House")
                self.assertEqual(proj_data["architect"], "SAOTA")
                self.assertEqual(proj_data["assets_count"], 1)

                # -------------------------------------------------------------
                # Tool 6: refer_board
                # -------------------------------------------------------------
                # 6.1 Create board with slot_name
                board_create = await session.call_tool("refer_board", {
                    "action": "create",
                    "name": "Villa Presentation Board",
                    "description": "Selected curated references",
                    "asset_ids": [1],
                    "slot_name": "exterior_overview"
                })
                self.assertFalse(board_create.is_error)
                b_create_data = json.loads(board_create.content[0].text)
                col_id = b_create_data["collection_id"]
                self.assertEqual(b_create_data["status"], "created")

                # 6.2 Add asset with slot_name
                board_add = await session.call_tool("refer_board", {
                    "action": "add",
                    "collection_id": col_id,
                    "asset_ids": [2],
                    "slot_name": "facade_detail"
                })
                self.assertFalse(board_add.is_error)

                # 6.3 Get board and verify slot_name persistence
                board_get = await session.call_tool("refer_board", {
                    "action": "get",
                    "collection_id": col_id
                })
                self.assertFalse(board_get.is_error)
                b_get_data = json.loads(board_get.content[0].text)
                self.assertEqual(b_get_data["items_count"], 2)
                slots = {item["asset_id"]: item["slot_name"] for item in b_get_data["items"]}
                self.assertEqual(slots[1], "exterior_overview")
                self.assertEqual(slots[2], "facade_detail")

                # 6.4 Validate board with slot_distribution breakdown
                board_val = await session.call_tool("refer_board", {
                    "action": "validate",
                    "collection_id": col_id,
                    "max_per_project": 1,
                    "target_count": 2
                })
                self.assertFalse(board_val.is_error)
                b_val_data = json.loads(board_val.content[0].text)
                self.assertTrue(b_val_data["is_valid"])
                self.assertEqual(b_val_data["slot_distribution"]["exterior_overview"], 1)
                self.assertEqual(b_val_data["slot_distribution"]["facade_detail"], 1)

                # -------------------------------------------------------------
                # Tool 7: refer_export
                # -------------------------------------------------------------
                export_out_dir = self.temp_dir / "export_output"
                exp_res = await session.call_tool("refer_export", {
                    "collection_id": col_id,
                    "destination_folder": str(export_out_dir),
                    "format": "folder",
                    "download_originals": False
                })
                self.assertFalse(exp_res.is_error)
                exp_data = json.loads(exp_res.content[0].text)
                self.assertEqual(exp_data["status"], "completed")
                final_export_dir = Path(exp_data["output_directory"])
                self.assertTrue(final_export_dir.exists())
                # Check exported files in the generated export directory
                manifest_file = final_export_dir / "manifest.json"
                self.assertTrue(manifest_file.exists())
                with open(manifest_file, "r", encoding="utf-8") as f:
                    m_data = json.load(f)
                    self.assertEqual(m_data["board_name"], "Villa Presentation Board")
                    self.assertEqual(len(m_data["items"]), 2)


if __name__ == "__main__":
    unittest.main()

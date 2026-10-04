"""Refer MCP Server: Model Context Protocol interface for AI reference curation."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Union

try:
    from mcp.server.mcpserver import MCPServer as FastMCP, Image
except (ImportError, ModuleNotFoundError):
    from mcp.server.fastmcp import FastMCP, Image

from mcp_server.db import get_db_manager, get_collection_repository
from mcp_server.search_engine import execute_search
from mcp_server.preview_generator import generate_contact_sheet
from mcp_server.crop_inspector import inspect_asset_crop
from export.moodboard_exporter import export_moodboard
import config
from database.visibility_store import VisibilityStore
from database.source_group_store import SourceGroupStore
from database.search_repository import normalized_path
from mcp_server.access_policy import check_asset_access
from mcp_server.image_paths import resolve_image_path

logger = logging.getLogger(__name__)

mcp = FastMCP(
    name="refer-mcp",
    instructions=(
        "Refer: Curated Architectural Reference Library. "
        "Allows AI agents to search 70,000+ architectural assets, evaluate composition and lighting "
        "via in-memory contact sheets (refer_preview), inspect micro-textures with native crop (refer_inspect), "
        "manage moodboards directly inside the desktop app (refer_board), and export client-ready deliverables (refer_export)."
    )
)


@mcp.tool()
def refer_taxonomy(
    kind: str = "overview",
    query: str = "",
    limit: int = 50
) -> Dict[str, Any]:
    """
    Explore the library taxonomy, architectural studios, and available tags.
    
    Args:
        kind: 'overview' (database stats), 'studios' (architectural bureaus), 
              'materials' (concrete, timber, etc.), 'tags' (all tags),
              'sources' (web domains & folders), or 'groups' (source categories).
        query: Optional search filter for studio name, tag, or domain.
        limit: Max items to return (default 50).
    """
    db = get_db_manager()
    kind_lower = (kind or "overview").strip().lower()
    q_like = f"%{query.strip().lower()}%" if query else "%"

    with db.get_connection() as conn:
        if kind_lower == "overview":
            total_assets = conn.execute("SELECT count(1) FROM assets").fetchone()[0]
            total_projects = conn.execute("SELECT count(1) FROM projects").fetchone()[0]
            total_collections = conn.execute("SELECT count(1) FROM collections").fetchone()[0]
            total_tags = conn.execute("SELECT count(1) FROM tags").fetchone()[0]
            features_count = conn.execute("SELECT count(1) FROM asset_features WHERE status='completed'").fetchone()[0]

            return {
                "total_assets": total_assets,
                "total_projects": total_projects,
                "total_collections": total_collections,
                "total_tags": total_tags,
                "features_indexed": features_count,
                "vector_dimension": 1152,
                "model": "google/siglip2-so400m-patch14-384"
            }

        elif kind_lower == "studios":
            rows = conn.execute("""
                SELECT p.author as name, COUNT(DISTINCT p.id) as project_count, COUNT(a.id) as asset_count
                FROM projects p
                LEFT JOIN assets a ON a.project_id = p.id
                WHERE p.author IS NOT NULL AND p.author != '' AND LOWER(p.author) LIKE ?
                GROUP BY p.author
                ORDER BY asset_count DESC
                LIMIT ?
            """, (q_like, limit)).fetchall()

            return {
                "kind": "studios",
                "count": len(rows),
                "items": [dict(r) for r in rows]
            }

        elif kind_lower in {"materials", "tags"}:
            rows = conn.execute("""
                SELECT t.name, COUNT(at.asset_id) as asset_count
                FROM tags t
                LEFT JOIN asset_tags at ON at.tag_id = t.id
                WHERE LOWER(t.name) LIKE ?
                GROUP BY t.id
                ORDER BY asset_count DESC
                LIMIT ?
            """, (q_like, limit)).fetchall()

            return {
                "kind": kind_lower,
                "count": len(rows),
                "items": [dict(r) for r in rows]
            }

        elif kind_lower == "sources":
            group_store = SourceGroupStore(db=db)
            disabled_sources = set(group_store.get_disabled_sources())
            assignments = group_store.get_source_assignments()

            rows = conn.execute("""
                SELECT s.id, s.domain, COUNT(a.id) as asset_count
                FROM sources s
                LEFT JOIN assets a ON a.source_id = s.id
                WHERE s.domain IS NOT NULL AND LOWER(s.domain) LIKE ?
                GROUP BY s.id
                ORDER BY asset_count DESC
                LIMIT ?
            """, (q_like, limit)).fetchall()

            items = []
            for r in rows:
                dom = r["domain"] or ""
                norm = normalized_path(dom)
                items.append({
                    "id": r["id"],
                    "domain": dom,
                    "asset_count": r["asset_count"],
                    "group_id": assignments.get(norm),
                    "enabled": norm not in disabled_sources and dom not in disabled_sources
                })

            return {
                "kind": "sources",
                "count": len(items),
                "items": items
            }

        elif kind_lower == "groups":
            group_store = SourceGroupStore(db=db)
            groups = group_store.get_groups()
            disabled_groups = set(group_store.get_disabled_groups())
            assignments = group_store.get_source_assignments()

            items = []
            for g in groups:
                gid = g["id"]
                assigned_srcs = [src for src, assigned_id in assignments.items() if assigned_id == gid]
                items.append({
                    "id": gid,
                    "name": g.get("name", ""),
                    "parent_id": g.get("parent_id"),
                    "enabled": gid not in disabled_groups,
                    "sources_count": len(assigned_srcs),
                    "sources": assigned_srcs
                })

            return {
                "kind": "groups",
                "count": len(items),
                "items": items
            }

        else:
            raise ValueError(f"Unknown taxonomy kind '{kind}'. Valid: 'overview', 'studios', 'materials', 'tags', 'sources', 'groups'.")


@mcp.tool()
def refer_search(
    query: str = "",
    mode: str = "hybrid",
    image_asset_id: Optional[int] = None,
    collection_id: Optional[int] = None,
    sources: Optional[List[str]] = None,
    studios: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    exclude_tags: Optional[List[str]] = None,
    exclude_project_ids: Optional[List[int]] = None,
    warmth_min: Optional[float] = None,
    warmth_max: Optional[float] = None,
    contrast_max: Optional[float] = None,
    slot_hint: Optional[str] = None,
    max_per_project: Optional[int] = 1,
    top_only: bool = False,
    favorites: bool = False,
    limit: int = 30
) -> Dict[str, Any]:
    """
    Search candidate references using hybrid semantic search (SigLIP 2), SQL metadata filters, and diversity constraints.
    
    Args:
        query: Natural language prompt (e.g. 'concrete villa pine forest diffuse light')
        mode: 'hybrid' (vector + metadata), 'semantic' (vector only), or 'metadata' (SQL only)
        image_asset_id: Optional asset ID to find visual analogues (image-to-image)
        collection_id: Restrict search to assets within a specific collection/moodboard
        sources: List of specific sources or domains to search within
        studios: List of studio/author names to filter by
        tags: List of tags to filter by (e.g. ['concrete', 'exterior'])
        exclude_tags: List of tags to exclude (e.g. ['drawing', 'night'])
        exclude_project_ids: List of project IDs to exclude
        warmth_min: Min warmth score (0.0=cold, 1.0=warm)
        warmth_max: Max warmth score (0.0=cold, 1.0=warm)
        contrast_max: Max global contrast (e.g. 0.22 for soft overcast light)
        slot_hint: 'overview' | 'interior' | 'detail' | 'landscape' (augments query)
        max_per_project: Limit assets from the same building (default 1 for high diversity)
        top_only: Restrict to curated benchmark references (tag 'топ')
        favorites: Restrict to user favorites
        limit: Number of candidates to return (default 30)
    """
    results = execute_search(
        query=query,
        mode=mode,
        image_asset_id=image_asset_id,
        collection_id=collection_id,
        sources=sources,
        studios=studios,
        tags=tags,
        exclude_tags=exclude_tags,
        exclude_project_ids=exclude_project_ids,
        warmth_min=warmth_min,
        warmth_max=warmth_max,
        contrast_max=contrast_max,
        slot_hint=slot_hint,
        max_per_project=max_per_project,
        top_only=top_only,
        favorites=favorites,
        limit=limit
    )

    clean_results = []
    for r in results:
        clean_results.append({
            "asset_id": r["asset_id"],
            "project_id": r["project_id"],
            "project": r["project"],
            "architect": r["architect"],
            "source_domain": r["source_domain"],
            "score_l2": round(r["score_l2"], 4) if r.get("score_l2") is not None else None,
            "warmth_palette": r.get("warmth_palette"),
            "global_contrast": r.get("global_contrast")
        })

    return {
        "count": len(clean_results),
        "slot_hint": slot_hint,
        "max_per_project": max_per_project,
        "candidates": clean_results
    }


@mcp.tool()
def refer_preview(
    asset_ids: List[int]
) -> List[Union[Image, str]]:
    """
    Generates an in-memory contact sheet (preview grid) of candidate images for rapid AI vision assessment.
    Does NOT save files to disk.
    
    Args:
        asset_ids: List of 1 to 9 asset IDs to inspect together.
        
    Returns:
        [ImageContent (JPEG), Manifest JSON string with cell-to-asset mapping]
    """
    if not asset_ids:
        raise ValueError("Must provide at least one asset_id (1 to 9).")
    if len(asset_ids) > 9:
        raise ValueError(f"Cannot preview more than 9 assets at once (requested: {len(asset_ids)}).")

    db = get_db_manager()
    visibility_store = VisibilityStore(config.APP_ROAMING_DIR / "hidden_assets.json")
    group_store = SourceGroupStore(db=db)
    disabled_sources = group_store.get_disabled_sources()
    items = []

    with db.get_connection() as conn:
        placeholders = ",".join("?" for _ in asset_ids)
        rows = conn.execute(f"""
            SELECT a.id, a.thumbnail_path, a.local_path, a.original_url, a.phash,
                   s.domain as source_domain,
                   p.title as project, p.author as architect,
                   af.warmth_palette, af.global_contrast
            FROM assets a
            LEFT JOIN projects p ON a.project_id = p.id
            LEFT JOIN sources s ON a.source_id = s.id
            LEFT JOIN asset_features af ON a.id = af.asset_id
            WHERE a.id IN ({placeholders})
        """, asset_ids).fetchall()

        rows_by_id = {r["id"]: dict(r) for r in rows}

        for aid in asset_ids:
            if aid in rows_by_id:
                r = rows_by_id[aid]
                accessible, reason = check_asset_access(
                    r,
                    visibility_store=visibility_store,
                    disabled_sources=disabled_sources
                )
                if not accessible:
                    logger.info(f"Asset #{aid} is excluded from preview (reason: {reason}).")
                    continue
                items.append({
                    "asset_id": aid,
                    "image_path": resolve_image_path(r),
                    "project": r["project"],
                    "architect": r["architect"],
                    "warmth_palette": r["warmth_palette"],
                    "global_contrast": r["global_contrast"]
                })

    img_bytes, manifest = generate_contact_sheet(items, cell_size=512, cols=3)

    return [
        Image(data=img_bytes, format="jpeg"),
        json.dumps(manifest, ensure_ascii=False, indent=2)
    ]


@mcp.tool()
def refer_inspect(
    asset_id: int,
    bbox_norm: Optional[List[float]] = None
) -> List[Union[Image, str]]:
    """
    Examines an asset at 100% native resolution, or crops a specific detail (e.g. concrete texture, joinery).
    Computes exact CIE LAB color palette and warmth score on the inspected region.
    
    Args:
        asset_id: Asset ID to inspect
        bbox_norm: Optional [x0, y0, x1, y1] normalized coordinates (0.0 to 1.0)
        
    Returns:
        [ImageContent (JPEG), Metadata JSON string with dimensions, source info, and color palette]
    """
    img_bytes, metadata = inspect_asset_crop(asset_id=asset_id, bbox_norm=bbox_norm)
    return [
        Image(data=img_bytes, format="jpeg"),
        json.dumps(metadata, ensure_ascii=False, indent=2)
    ]


@mcp.tool()
def refer_project(
    project_id: int
) -> Dict[str, Any]:
    """
    Retrieves full architectural context of a project: title, architect, location, source URL, 
    and all associated photos, elevations, and floor plans.
    """
    db = get_db_manager()
    visibility_store = VisibilityStore(config.APP_ROAMING_DIR / "hidden_assets.json")
    group_store = SourceGroupStore(db=db)
    disabled_sources = group_store.get_disabled_sources()

    with db.get_connection() as conn:
        project_row = conn.execute("""
            SELECT p.*, s.domain as source_domain
            FROM projects p
            LEFT JOIN sources s ON p.url LIKE '%' || s.domain || '%'
            WHERE p.id = ?
        """, (project_id,)).fetchone()

        if not project_row:
            raise ValueError(f"Project #{project_id} not found.")

        asset_rows = conn.execute("""
            SELECT a.id, a.thumbnail_path, a.local_path, a.original_url, a.phash,
                   a.width, a.height, a.image_type, a.description,
                   s.domain as source_domain,
                   af.warmth_palette, af.global_contrast
            FROM assets a
            LEFT JOIN sources s ON a.source_id = s.id
            LEFT JOIN asset_features af ON a.id = af.asset_id
            WHERE a.project_id = ?
            ORDER BY a.id ASC
        """, (project_id,)).fetchall()

    visible_assets = [
        {
            "id": r["id"],
            "thumbnail_path": r["thumbnail_path"],
            "width": r["width"],
            "height": r["height"],
            "image_type": r["image_type"],
            "description": r["description"],
            "warmth_palette": r["warmth_palette"],
            "global_contrast": r["global_contrast"]
        }
        for r in asset_rows
        if check_asset_access(dict(r), visibility_store=visibility_store, disabled_sources=disabled_sources)[0]
    ]

    return {
        "project_id": project_row["id"],
        "title": project_row["title"],
        "architect": project_row["author"],
        "url": project_row["url"],
        "assets_count": len(visible_assets),
        "assets": visible_assets
    }


@mcp.tool()
def refer_board(
    action: str,
    collection_id: Optional[int] = None,
    name: Optional[str] = None,
    description: str = "",
    asset_ids: Optional[List[int]] = None,
    slot_name: Optional[str] = None,
    max_per_project: Optional[int] = None,
    target_count: Optional[int] = None
) -> Dict[str, Any]:
    """
    Manage moodboard collections directly in the Refer desktop application.
    Collections created or updated here immediately reflect in the GUI sidebar.
    
    Args:
        action: 'create' | 'add' | 'remove' | 'list' | 'get' | 'validate'
        collection_id: Target collection ID (required for add, remove, get, validate)
        name: Name of the collection (for 'create' or renaming)
        description: Optional notes/brief for the collection
        asset_ids: List of asset IDs to add or remove
        slot_name: Optional slot/role (e.g. 'overview', 'interior', 'facade', 'detail', 'landscape')
        max_per_project: For 'validate': ensure no building exceeds N shots
        target_count: For 'validate': ensure board has exactly target number of assets
    """
    repo = get_collection_repository()
    act = (action or "").strip().lower()

    if act == "create":
        if not name:
            raise ValueError("Parameter 'name' is required when creating a collection.")
        new_id = repo.create_collection(name=name, description=description, ids=asset_ids or [], slot_name=slot_name)
        return {
            "status": "created",
            "collection_id": new_id,
            "name": name,
            "items_count": len(asset_ids or []),
            "slot_name": slot_name
        }

    elif act == "add":
        if collection_id is None:
            raise ValueError("collection_id is required for 'add'.")
        if not asset_ids:
            raise ValueError("asset_ids is required for 'add'.")
        added = repo.add_assets(collection_id, asset_ids, slot_name=slot_name)
        return {
            "status": "updated",
            "collection_id": collection_id,
            "added_count": len(added),
            "added_asset_ids": added,
            "slot_name": slot_name
        }

    elif act == "remove":
        if collection_id is None:
            raise ValueError("collection_id is required for 'remove'.")
        if not asset_ids:
            raise ValueError("asset_ids is required for 'remove'.")
        repo.remove_assets(collection_id, asset_ids)
        return {
            "status": "updated",
            "collection_id": collection_id,
            "removed_asset_ids": asset_ids
        }

    elif act == "list":
        collections = repo.get_collections_with_counts()
        return {
            "count": len(collections),
            "collections": collections
        }

    elif act == "get":
        if collection_id is None:
            raise ValueError("collection_id is required for 'get'.")
        col = repo.get_collection(collection_id)
        if not col:
            raise ValueError(f"Collection #{collection_id} not found.")
        assets = repo.get_collection_assets_with_slots(collection_id)

        db = get_db_manager()
        visibility_store = VisibilityStore(config.APP_ROAMING_DIR / "hidden_assets.json")
        group_store = SourceGroupStore(db=db)
        disabled_sources = group_store.get_disabled_sources()

        items = []
        for a in assets:
            accessible, reason = check_asset_access(
                dict(a),
                visibility_store=visibility_store,
                disabled_sources=disabled_sources
            )
            items.append({
                "asset_id": a["id"],
                "project_id": a["project_id"],
                "project": a["project_title"],
                "architect": a["project_author"],
                "slot_name": a["slot_name"],
                "thumbnail_path": a["thumbnail_path"],
                "local_path": a["local_path"],
                "is_cover": bool(a["is_cover"]),
                "position": a["position"],
                "is_accessible": accessible,
                "inaccessible_reason": reason
            })

        return {
            "collection": col,
            "items_count": len(items),
            "items": items
        }

    elif act == "validate":
        if collection_id is None:
            raise ValueError("collection_id is required for 'validate'.")
        col = repo.get_collection(collection_id)
        if not col:
            raise ValueError(f"Collection #{collection_id} not found.")
        assets = repo.get_collection_assets_with_slots(collection_id)

        db = get_db_manager()
        visibility_store = VisibilityStore(config.APP_ROAMING_DIR / "hidden_assets.json")
        group_store = SourceGroupStore(db=db)
        disabled_sources = group_store.get_disabled_sources()

        # Check project distribution, slot distribution, and accessibility
        project_counts: Dict[int, int] = {}
        slot_distribution: Dict[str, int] = {}
        violations = []

        for a in assets:
            pid = a["project_id"] or 0
            project_counts[pid] = project_counts.get(pid, 0) + 1
            slot = a["slot_name"] or "unassigned"
            slot_distribution[slot] = slot_distribution.get(slot, 0) + 1

            accessible, reason = check_asset_access(
                dict(a),
                visibility_store=visibility_store,
                disabled_sources=disabled_sources
            )
            if not accessible:
                if reason == "hidden":
                    violations.append(f"Asset #{a['id']} is hidden by library visibility policy.")
                elif reason == "source_disabled":
                    violations.append(f"Asset #{a['id']} belongs to disabled source or directory.")

        if max_per_project is not None:
            for pid, count in project_counts.items():
                if pid > 0 and count > max_per_project:
                    violations.append(f"Project #{pid} has {count} assets (max allowed: {max_per_project})")

        count_ok = True
        if target_count is not None and len(assets) != target_count:
            count_ok = False
            violations.append(f"Collection has {len(assets)} assets (target: {target_count})")

        return {
            "collection_id": collection_id,
            "name": col["name"],
            "total_assets": len(assets),
            "unique_projects": len([pid for pid in project_counts if pid > 0]),
            "slot_distribution": slot_distribution,
            "is_valid": len(violations) == 0,
            "violations": violations
        }

    else:
        raise ValueError(f"Unknown action '{action}'. Valid: 'create', 'add', 'remove', 'list', 'get', 'validate'.")


@mcp.tool()
def refer_export(
    collection_id: int,
    destination_folder: str,
    format: str = "web_html",
    download_originals: bool = True
) -> Dict[str, Any]:
    """
    Exports a curated moodboard into a client-ready package or local directory.
    Uses the core Refer MoodboardExporter.
    
    Args:
        collection_id: Collection to export
        destination_folder: Target directory path on disk
        format: 'web_html' (lightweight offline/CDN HTML, 0 MB disk duplication),
                'folder' (images + manifest.json),
                'html' (images + standalone offline presentation)
        download_originals: If True, fetches highest resolution master files from CDN when available
    """
    repo = get_collection_repository()
    snapshot = repo.snapshot(collection_id)

    db = get_db_manager()
    visibility_store = VisibilityStore(config.APP_ROAMING_DIR / "hidden_assets.json")
    group_store = SourceGroupStore(db=db)
    disabled_sources = group_store.get_disabled_sources()

    # Filter out inaccessible assets before export
    accessible_items = []
    skipped_count = 0
    for item in snapshot.items:
        row_dict = {
            "id": item.id,
            "local_path": item.local_path,
            "source_domain": item.source_domain,
            "original_url": item.original_url
        }
        accessible, _ = check_asset_access(
            row_dict,
            visibility_store=visibility_store,
            disabled_sources=disabled_sources
        )
        if accessible:
            accessible_items.append(item)
        else:
            skipped_count += 1

    if skipped_count > 0:
        logger.info(f"refer_export: {skipped_count} inaccessible asset(s) skipped from export.")
        from database.collection_repository import BoardSnapshot
        snapshot = BoardSnapshot(
            id=snapshot.id,
            name=snapshot.name,
            description=snapshot.description,
            export_dir=snapshot.export_dir,
            items=tuple(accessible_items)
        )

    target_path = Path(destination_folder).expanduser().resolve()
    target_path.mkdir(parents=True, exist_ok=True)

    try:
        export_mode = "web" if format == "web_html" else "copy"
        result = export_moodboard(
            board=snapshot,
            target_dir=target_path,
            format=format,
            mode=export_mode,
            download_originals=download_originals
        )
    except Exception as e:
        logger.exception(f"export_moodboard failed: {e}")
        raise RuntimeError(f"export_moodboard error: {type(e).__name__}: {e}") from e

    return {
        "status": "completed",
        "collection_id": collection_id,
        "collection_name": snapshot.name,
        "format": format,
        "exported_count": result.get("count", len(snapshot.items)),
        "skipped_inaccessible_count": skipped_count,
        "output_directory": result.get("directory"),
        "entrypoint": result.get("entrypoint")
    }


def main():
    """Runs the MCP server via stdio transport."""
    mcp.run()


if __name__ == "__main__":
    main()

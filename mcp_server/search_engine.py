"""Search orchestrator for Refer MCP: hybrid semantic and SQL metadata retrieval.

Integrates with the shared SearchRepository, VisibilityStore, and SourceGroupStore.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

import numpy as np
import config
from mcp_server.db import get_db_manager, get_faiss_manager
from database.search_repository import SearchRepository, SearchFilters
from database.source_group_store import SourceGroupStore
from database.visibility_store import VisibilityStore
from mcp_server.access_policy import check_asset_access
from mcp_server.image_paths import resolve_image_path

logger = logging.getLogger(__name__)

_ai_client = None


def get_ai_client():
    """Lazily instantiates the AiProcessClient (which attaches to the shared service)."""
    global _ai_client
    if _ai_client is None:
        try:
            from ai.process_client import AiProcessClient
            _ai_client = AiProcessClient()
            logger.info("AiProcessClient connected for MCP server.")
        except Exception as e:
            logger.warning(f"Could not initialize AiProcessClient: {e}")
            _ai_client = None
    return _ai_client


def execute_search(
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
    limit: int = 30,
    top_only: bool = False,
    favorites: bool = False,
    search_repo: Optional[SearchRepository] = None,
    group_store: Optional[SourceGroupStore] = None,
    visibility_store: Optional[VisibilityStore] = None
) -> List[Dict[str, Any]]:
    """
    Executes search combining SQLite filters, FAISS vector search, and diversity constraints.
    Adheres strictly to visibility policies, source groups, and shared SearchRepository.
    """
    if search_repo is not None:
        db = getattr(search_repo, "db", None) or get_db_manager()
        faiss_mgr = getattr(search_repo, "faiss", None)
    else:
        db = get_db_manager()
        faiss_mgr = get_faiss_manager()

    if visibility_store is None:
        visibility_store = getattr(search_repo, "visibility", None) or VisibilityStore(config.APP_ROAMING_DIR / "hidden_assets.json")
    if group_store is None:
        group_store = SourceGroupStore(db=db)
    if search_repo is None:
        search_repo = SearchRepository(
            db=db,
            faiss_manager=faiss_mgr,
            assignments=group_store.get_source_assignments(),
            visibility_store=visibility_store
        )

    disabled_sources = group_store.get_disabled_sources()

    # 1. Determine active sources
    if sources:
        active_sources = [s for s in sources if s]
    else:
        with db.get_connection() as conn:
            domain_rows = conn.execute("SELECT DISTINCT domain FROM sources WHERE domain IS NOT NULL").fetchall()
            domains = [r[0] for r in domain_rows if r[0]]
            local_rows = conn.execute("SELECT DISTINCT local_path FROM assets WHERE local_path IS NOT NULL").fetchall()
            folder_roots = set()
            for r in local_rows:
                lp = r[0]
                if lp:
                    parts = lp.replace("\\", "/").split("/")
                    if len(parts) > 1:
                        folder_roots.add(parts[0] + "/" + parts[1] if len(parts) > 2 else parts[0])
                    else:
                        folder_roots.add(lp)

        assigned_sources = list(group_store.get_source_assignments().keys())
        all_discovered = list(dict.fromkeys(domains + assigned_sources + list(folder_roots)))
        active_sources = [s for s in all_discovered if s not in disabled_sources]
        if not active_sources:
            # Fallback to web sources
            active_sources = ["archdaily.com", "behance.net"]

    # 2. Build SearchFilters
    filters = SearchFilters(
        sources=tuple(active_sources),
        excluded_sources=tuple(disabled_sources),
        tags=tuple(tags) if tags else (),
        exclude_tags=tuple(exclude_tags) if exclude_tags else (),
        favorites=favorites,
        top_only=top_only,
        studios=tuple(studios) if studios else (),
        exclude_project_ids=tuple(exclude_project_ids) if exclude_project_ids else (),
        warmth_min=warmth_min,
        warmth_max=warmth_max,
        contrast_max=contrast_max,
        collection_id=collection_id
    )

    # 3. Apply slot_hint to prompt augmentation
    effective_query = (query or "").strip()
    if slot_hint:
        hint_lower = slot_hint.lower()
        if "interior" in hint_lower:
            effective_query = f"{effective_query} interior indoor architecture room design".strip()
        elif "detail" in hint_lower or "facade" in hint_lower:
            effective_query = f"{effective_query} architectural detail facade material close-up texture joint".strip()
        elif "landscape" in hint_lower:
            effective_query = f"{effective_query} landscape architecture outdoor environment surroundings exterior natural setting".strip()
        elif "overview" in hint_lower or "exterior" in hint_lower:
            effective_query = f"{effective_query} exterior wide overview facade landscape architecture".strip()

    norm_mode = (mode or "hybrid").lower()
    if norm_mode not in {"metadata", "semantic", "hybrid"}:
        raise ValueError(f"Unsupported search mode '{mode}'. Valid modes: 'metadata', 'semantic', 'hybrid'.")

    # Pure metadata mode
    if norm_mode == "metadata":
        return search_repo.search_candidates(
            filters=filters,
            text=effective_query,
            vector=None,
            limit=limit,
            metadata_only=True,
            max_per_project=max_per_project
        )

    # Semantic or Hybrid: requires vector
    query_vector = None

    if image_asset_id is not None:
        with db.get_connection() as conn:
            row = conn.execute("""
                SELECT a.*, s.domain as source_domain
                FROM assets a
                LEFT JOIN sources s ON a.source_id = s.id
                WHERE a.id = ?
            """, (image_asset_id,)).fetchone()
        if not row:
            raise ValueError(f"Asset #{image_asset_id} not found.")
        accessible, reason = check_asset_access(
            dict(row),
            visibility_store=visibility_store,
            group_store=group_store,
            disabled_sources=disabled_sources
        )
        if not accessible:
            if reason == "hidden":
                raise ValueError(f"Asset #{image_asset_id} is hidden by library visibility policy.")
            elif reason == "source_disabled":
                raise ValueError(f"Asset #{image_asset_id} belongs to a disabled source or directory.")
            else:
                raise ValueError(f"Asset #{image_asset_id} is not accessible.")
        target_path = resolve_image_path(row)
        if target_path is None:
            raise FileNotFoundError(f"Local image file for asset #{image_asset_id} not found on disk.")
        ai = get_ai_client()
        if ai is None:
            raise RuntimeError("AI inference service is unavailable for image vector search.")
        query_vector = ai.get_image_embedding(target_path)
    elif effective_query:
        ai = get_ai_client()
        if ai is not None:
            try:
                query_vector = ai.get_text_embedding(effective_query)
            except Exception as err:
                logger.warning(f"AI text embedding failed: {err}")
                if norm_mode == "semantic":
                    raise RuntimeError(f"AI inference service failed: {err}") from err
        else:
            if norm_mode == "semantic":
                raise RuntimeError("AI inference service is unavailable for semantic search.")

    if query_vector is None:
        # If vector is unavailable (e.g. query is empty or AI unavailable in hybrid):
        # In hybrid mode with query: return literal metadata matches only, never random images!
        if effective_query:
            return search_repo.search_candidates(
                filters=filters,
                text=effective_query,
                vector=None,
                limit=limit,
                metadata_only=True,
                max_per_project=max_per_project
            )
        else:
            return search_repo.search_candidates(
                filters=filters,
                text="",
                vector=None,
                limit=limit,
                metadata_only=True,
                max_per_project=max_per_project
            )

    return search_repo.search_candidates(
        filters=filters,
        text=effective_query if norm_mode == "hybrid" else "",
        vector=query_vector,
        limit=limit,
        metadata_only=False,
        max_per_project=max_per_project
    )

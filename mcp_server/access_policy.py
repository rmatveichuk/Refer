"""Unified access and visibility policy for Refer MCP and library components.

Ensures consistent enforcement of individual asset hiding (VisibilityStore) and
source / folder administrative disabling (SourceGroupStore) across:
- refer_search (including image_asset_id)
- refer_preview
- refer_inspect (inspect_asset_crop)
- refer_project
- refer_board (get / validate)
- refer_export
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, Set, Sequence
import config
from database.search_repository import normalized_path
from database.source_group_store import SourceGroupStore
from database.visibility_store import VisibilityStore

logger = logging.getLogger(__name__)


def is_source_or_path_disabled(
    source_or_path: str,
    disabled_sources: Sequence[str] | Set[str]
) -> bool:
    """
    Checks if a domain, catalog, or folder path is disabled directly or via a parent folder.
    
    Examples:
        If disabled_sources contains 'archdaily.com', 'archdaily.com' is disabled.
        If disabled_sources contains 'c:/refs', 'c:/refs/villa/shot1.jpg' is disabled.
        If disabled_sources contains 'c:/refs/interiors', 'c:/refs/interiors/room.jpg' is disabled,
        but 'c:/refs/exteriors/facade.jpg' is NOT disabled.
    """
    if not source_or_path or not disabled_sources:
        return False

    norm_target = normalized_path(source_or_path)
    ex_set = {normalized_path(s) for s in disabled_sources}
    if norm_target in ex_set:
        return True

    # Check parent folder prefix match
    ex_prefixes = tuple(e.rstrip("/") + "/" for e in ex_set)
    if ex_prefixes and norm_target.startswith(ex_prefixes):
        return True

    return False


def check_asset_access(
    asset_data: Dict[str, Any] | Any,
    visibility_store: Optional[VisibilityStore] = None,
    group_store: Optional[SourceGroupStore] = None,
    disabled_sources: Optional[Sequence[str] | Set[str]] = None
) -> Tuple[bool, Optional[str]]:
    """
    Evaluates whether an asset is accessible according to visibility and source group policies.
    
    Returns:
        (is_accessible: bool, reason: Optional[str])
        where reason can be:
            None (accessible)
            "hidden" (individually hidden via VisibilityStore)
            "source_disabled" (source domain or parent/local directory disabled in SourceGroupStore)
    """
    if not asset_data:
        return False, "not_found"

    row = dict(asset_data) if not isinstance(asset_data, dict) else asset_data

    # 1. Check individual asset visibility (hidden)
    if visibility_store is None:
        visibility_store = VisibilityStore(config.APP_ROAMING_DIR / "hidden_assets.json")
    if visibility_store.is_hidden(row):
        return False, "hidden"

    # 2. Check disabled sources and paths
    if disabled_sources is None:
        if group_store is not None:
            disabled_sources = group_store.get_disabled_sources()
        else:
            try:
                from mcp_server.db import get_db_manager
                db = get_db_manager()
                group_store = SourceGroupStore(db=db)
                disabled_sources = group_store.get_disabled_sources()
            except Exception as err:
                logger.debug(f"Could not load SourceGroupStore for access check: {err}")
                disabled_sources = []

    if disabled_sources:
        # Check source domain
        domain = row.get("source_domain") or row.get("domain") or ""
        if domain and is_source_or_path_disabled(domain, disabled_sources):
            return False, "source_disabled"

        # Check local path and any parent directory
        local_path = row.get("local_path") or ""
        if local_path and is_source_or_path_disabled(local_path, disabled_sources):
            return False, "source_disabled"

    return True, None


def is_asset_accessible(
    asset_data: Dict[str, Any] | Any,
    visibility_store: Optional[VisibilityStore] = None,
    group_store: Optional[SourceGroupStore] = None,
    disabled_sources: Optional[Sequence[str] | Set[str]] = None
) -> bool:
    """Convenience boolean check for asset accessibility."""
    accessible, _ = check_asset_access(
        asset_data,
        visibility_store=visibility_store,
        group_store=group_store,
        disabled_sources=disabled_sources
    )
    return accessible

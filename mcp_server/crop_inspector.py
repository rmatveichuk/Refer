"""Native resolution asset inspector and crop tool for micro-detail analysis."""
from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any

from PIL import Image, ImageOps
import config
from mcp_server.db import get_db_manager
from mcp_server.photometry import extract_photometry

logger = logging.getLogger(__name__)


def inspect_asset_crop(
    asset_id: int,
    bbox_norm: Optional[List[float] | Tuple[float, float, float, float]] = None,
    image_format: str = "JPEG",
    quality: int = 90,
    db: Any = None,
    visibility_store: Any = None,
    group_store: Any = None
) -> Tuple[bytes, Dict[str, Any]]:
    """
    Inspects an asset at full native resolution, or crops a specific bounding box.
    Does NOT save crop to disk.
    
    Args:
        asset_id: SQLite asset ID
        bbox_norm: Optional [x0, y0, x1, y1] normalized coords (0.0 to 1.0)
        image_format: 'JPEG' or 'WEBP'
        quality: Image quality
        db: Optional DatabaseManager instance
        visibility_store: Optional VisibilityStore instance
        group_store: Optional SourceGroupStore instance
        
    Returns:
        (image_bytes, metadata_dict)
    """
    if db is None:
        db = get_db_manager()
    if visibility_store is None:
        from database.visibility_store import VisibilityStore
        visibility_store = VisibilityStore(config.APP_ROAMING_DIR / "hidden_assets.json")
    if group_store is None:
        from database.source_group_store import SourceGroupStore
        group_store = SourceGroupStore(db=db)

    with db.get_connection() as conn:
        row = conn.execute("""
            SELECT a.*, p.title as project_title, p.author as project_author, 
                   p.url as project_url, s.domain as source_domain
            FROM assets a
            LEFT JOIN projects p ON a.project_id = p.id
            LEFT JOIN sources s ON a.source_id = s.id
            WHERE a.id = ?
        """, (asset_id,)).fetchone()

    if not row:
        raise ValueError(f"Asset #{asset_id} not found in database.")

    from mcp_server.access_policy import check_asset_access
    accessible, reason = check_asset_access(
        dict(row),
        visibility_store=visibility_store,
        group_store=group_store
    )
    if not accessible:
        if reason == "hidden":
            raise ValueError(f"Asset #{asset_id} is hidden by library visibility policy.")
        elif reason == "source_disabled":
            raise ValueError(f"Asset #{asset_id} belongs to a disabled source or directory.")
        else:
            raise ValueError(f"Asset #{asset_id} is not accessible.")

    # 1. Resolve best available file on local disk
    resolved_path = None

    if row["local_path"] and Path(row["local_path"]).is_file():
        resolved_path = Path(row["local_path"])
    elif row["thumbnail_path"] and Path(row["thumbnail_path"]).is_file():
        resolved_path = Path(row["thumbnail_path"])
    else:
        # Fallback check in thumbnails dir
        candidate = config.THUMBNAILS_DIR / f"{row['phash']}.webp"
        if candidate.is_file():
            resolved_path = candidate

    if not resolved_path or not resolved_path.is_file():
        raise FileNotFoundError(f"Local image file for asset #{asset_id} does not exist on disk.")

    # Determine if resolved file is actually from thumbnail cache
    is_cache_fallback = False
    try:
        resolved_abs = resolved_path.resolve()
        thumb_dir_abs = config.THUMBNAILS_DIR.resolve()
        if thumb_dir_abs in resolved_abs.parents or resolved_abs.parent == thumb_dir_abs or "thumbnails" in str(resolved_abs).lower():
            is_cache_fallback = True
    except Exception:
        is_cache_fallback = True

    with Image.open(resolved_path) as full_img:
        full_img = ImageOps.exif_transpose(full_img)
        full_img = full_img.convert("RGB")
        orig_w, orig_h = full_img.size

        crop_applied = False
        crop_box_px = None

        if bbox_norm is not None:
            if not isinstance(bbox_norm, (list, tuple)) or len(bbox_norm) != 4:
                raise ValueError(f"Invalid bbox_norm: {bbox_norm}. Expected a 4-element sequence [x0, y0, x1, y1].")
            import math
            for idx, val in enumerate(bbox_norm):
                if not isinstance(val, (int, float)) or math.isnan(val) or math.isinf(val):
                    raise ValueError(f"Invalid coordinate at index {idx} in bbox_norm: {val}. Must be finite float.")

            x0, y0, x1, y1 = [float(v) for v in bbox_norm]
            if not (0.0 <= x0 <= 1.0 and 0.0 <= y0 <= 1.0 and 0.0 <= x1 <= 1.0 and 0.0 <= y1 <= 1.0):
                raise ValueError(f"Coordinates in bbox_norm must be in [0.0, 1.0], got {[x0, y0, x1, y1]}.")
            if x1 <= x0 or y1 <= y0:
                raise ValueError(f"Empty or inverted bbox: x0={x0}, y0={y0}, x1={x1}, y1={y1}. Must satisfy x1 > x0 and y1 > y0.")

            px_x0 = int(round(x0 * orig_w))
            px_y0 = int(round(y0 * orig_h))
            px_x1 = int(round(x1 * orig_w))
            px_y1 = int(round(y1 * orig_h))

            # Clamp safely within pixel bounds
            px_x0 = max(0, min(orig_w - 1, px_x0))
            px_y0 = max(0, min(orig_h - 1, px_y0))
            px_x1 = max(px_x0 + 1, min(orig_w, px_x1))
            px_y1 = max(px_y0 + 1, min(orig_h, px_y1))

            crop_box_px = [px_x0, px_y0, px_x1, px_y1]
            target_img = full_img.crop((px_x0, px_y0, px_x1, px_y1))
            crop_applied = True
        else:
            target_img = full_img

        view_w, view_h = target_img.width, target_img.height

        # Limit maximum view dimension to prevent memory explosion
        MAX_VIEW_DIM = 2560
        is_downsampled = False
        if target_img.width > MAX_VIEW_DIM or target_img.height > MAX_VIEW_DIM:
            target_img.thumbnail((MAX_VIEW_DIM, MAX_VIEW_DIM), Image.Resampling.BILINEAR)
            is_downsampled = True

        # Save cropped or inspected view into in-memory bytes
        buf = io.BytesIO()
        target_img.save(buf, format=image_format, quality=quality)
        img_bytes = buf.getvalue()

        # Compute accurate photometry directly on the inspected/cropped region
        photometry_data = extract_photometry(target_img)

    metadata = {
        "asset_id": asset_id,
        "project": row["project_title"] or "Unknown Project",
        "architect": row["project_author"] or "Unknown Architect",
        "source_domain": row["source_domain"] or "",
        "original_url": row["original_url"] or "",
        "project_url": row["project_url"] or "",
        "file_used": str(resolved_path),
        "is_cache_fallback": is_cache_fallback,
        "native_dimensions": [orig_w, orig_h],
        "crop_applied": crop_applied,
        "crop_coords_norm": [x0, y0, x1, y1] if crop_applied else None,
        "crop_coords_px": crop_box_px,
        "view_dimensions": [view_w, view_h],
        "returned_dimensions": [target_img.width, target_img.height],
        "is_downsampled": is_downsampled,
        "measurement_region": "crop" if crop_applied else "full",
        "warmth_palette": photometry_data.get("warmth_palette"),
        "global_contrast": photometry_data.get("global_contrast"),
        "lstar_mean": photometry_data.get("lstar_mean"),
        "palette": photometry_data.get("palette")
    }

    return img_bytes, metadata

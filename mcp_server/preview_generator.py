"""In-memory contact sheet (preview grid) generator for AI vision evaluation."""
from __future__ import annotations

import io
import math
from pathlib import Path
from typing import List, Dict, Any, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageOps


def generate_contact_sheet(
    items: List[Dict[str, Any]],
    cell_size: int = 512,
    cols: int = 3,
    image_format: str = "JPEG",
    quality: int = 85
) -> Tuple[bytes, Dict[str, Any]]:
    """
    Generates an in-memory contact sheet (e.g. 3x3 grid) with cell numbers and IDs.
    Does NOT save to disk.
    
    Args:
        items: List of dicts with keys: 'asset_id', 'image_path', 'project', 'author', etc.
        cell_size: Dimension of each square cell in pixels (default 512)
        cols: Number of columns (default 3)
        image_format: 'JPEG' or 'WEBP'
        quality: Compression quality (default 85)
        
    Returns:
        (image_bytes, manifest_dict)
    """
    n = len(items)
    if n == 0:
        # Return blank dark image
        blank = Image.new("RGB", (cell_size, cell_size), color=(22, 24, 28))
        buf = io.BytesIO()
        blank.save(buf, format=image_format, quality=quality)
        return buf.getvalue(), {"sheet_cells": [], "count": 0}

    # Dynamic grid dimensions based on item count
    if n <= 4 and cols >= 3:
        actual_cols = 2
        actual_rows = math.ceil(n / 2)
    else:
        actual_cols = cols
        actual_rows = math.ceil(n / cols)

    sheet_w = actual_cols * cell_size
    sheet_h = actual_rows * cell_size

    # Background color: subtle studio dark #141518
    sheet = Image.new("RGB", (sheet_w, sheet_h), color=(20, 21, 24))
    draw = ImageDraw.Draw(sheet)

    # Simple fallback font
    try:
        font_large = ImageFont.truetype("arial.ttf", 28)
        font_small = ImageFont.truetype("arial.ttf", 16)
    except Exception:
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    manifest_cells = []

    for idx, item in enumerate(items):
        cell_idx = idx + 1
        r = idx // actual_cols
        c = idx % actual_cols
        x_offset = c * cell_size
        y_offset = r * cell_size

        asset_id = item.get("asset_id")
        img_path = item.get("image_path")
        project_title = item.get("project", "") or ""
        architect = item.get("architect", "") or ""

        # Load image safely
        img_loaded = None
        if img_path and Path(img_path).is_file():
            try:
                with Image.open(img_path) as src_img:
                    src_img = ImageOps.exif_transpose(src_img)
                    src_img = src_img.convert("RGB")
                    # Contain image inside cell with 12px padding
                    pad = 8
                    max_w = cell_size - pad * 2
                    max_h = cell_size - pad * 2
                    img_loaded = ImageOps.contain(src_img, (max_w, max_h), Image.Resampling.BILINEAR)
            except Exception:
                img_loaded = None

        # Paste image centered in cell
        if img_loaded:
            paste_x = x_offset + (cell_size - img_loaded.width) // 2
            paste_y = y_offset + (cell_size - img_loaded.height) // 2
            sheet.paste(img_loaded, (paste_x, paste_y))
        else:
            # Draw empty placeholder box
            draw.rectangle(
                [x_offset + 10, y_offset + 10, x_offset + cell_size - 10, y_offset + cell_size - 10],
                outline=(50, 54, 62),
                width=1
            )
            draw.text((x_offset + cell_size // 2 - 40, y_offset + cell_size // 2 - 10), "No Image", fill=(100, 105, 115), font=font_small)

        # Draw cell grid border
        draw.rectangle(
            [x_offset, y_offset, x_offset + cell_size, y_offset + cell_size],
            outline=(35, 38, 44),
            width=1
        )

        # Draw cell badge in top-left corner
        badge_text = f"{cell_idx:02d}"
        sub_text = f"#{asset_id}" if asset_id else ""
        
        # Pill badge background
        badge_x = x_offset + 14
        badge_y = y_offset + 14
        pill_w = 80 if sub_text else 52
        pill_h = 38
        
        draw.rounded_rectangle(
            [badge_x, badge_y, badge_x + pill_w, badge_y + pill_h],
            radius=6,
            fill=(10, 11, 14),
            outline=(70, 75, 88),
            width=1
        )

        draw.text((badge_x + 8, badge_y + 4), badge_text, fill=(245, 248, 255), font=font_large)
        if sub_text:
            draw.text((badge_x + 46, badge_y + 11), sub_text, fill=(160, 168, 185), font=font_small)

        manifest_cells.append({
            "cell": cell_idx,
            "asset_id": asset_id,
            "architect": architect,
            "project": project_title,
            "available": (img_loaded is not None),
            "warmth_palette": item.get("warmth_palette"),
            "global_contrast": item.get("global_contrast")
        })

    # Encode directly to RAM
    buf = io.BytesIO()
    sheet.save(buf, format=image_format, quality=quality, optimize=True)
    img_bytes = buf.getvalue()

    manifest = {
        "total_candidates": len(manifest_cells),
        "grid_layout": f"{actual_cols}x{actual_rows}",
        "resolution": f"{sheet_w}x{sheet_h}",
        "cells": manifest_cells
    }

    return img_bytes, manifest

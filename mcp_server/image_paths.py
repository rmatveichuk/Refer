"""Resolve existing images without modifying library records."""
from pathlib import Path


def resolve_image_path(asset):
    """Prefer the original file, falling back to its existing thumbnail."""
    for field in ("local_path", "thumbnail_path"):
        value = asset[field]
        if value:
            path = Path(value)
            if path.is_file():
                return path
    return None

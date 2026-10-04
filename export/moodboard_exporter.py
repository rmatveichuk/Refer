from __future__ import annotations

import html
import json
import logging
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from threading import Event
from urllib.parse import quote
from typing import Optional, Callable, Dict, Any

from PIL import Image, ImageOps
from database.collection_repository import BoardSnapshot

logger = logging.getLogger(__name__)

MIME = "application/x-refer-asset-ids"
EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}


class Cancelled(Exception):
    """Исключение при ручной отмене экспорта пользователем."""
    pass


def sanitize_filename(name: str, max_length: int = 50) -> str:
    slug = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name or '').strip(' .')
    return slug[:max_length] or 'item'


def export_moodboard(
    board: BoardSnapshot,
    target_dir: str | Path,
    format: str = "folder",
    mode: str = "copy",
    use_numbered_names: bool = True,
    cancel: Optional[Event] = None,
    progress: Optional[Callable[[int, int, str], None]] = None
) -> Dict[str, Any]:
    """
    Экспортирует снимок набора в папку проекта.
    
    Args:
        board: Снимок набора (BoardSnapshot)
        target_dir: Путь к целевой рабочей папке
        format: 'folder' (файлы + manifest) или 'html' (автономный HTML + печать)
        mode: 'copy', 'hardlink', 'auto' (hardlink -> copy fallback), 'symlink'
        use_numbered_names: Добавлять нумерацию 001_Author_Project.ext
        cancel: threading.Event для безопасного прерывания
        progress: callback(current, total, title)
    """
    if format not in {"folder", "html"}:
        raise ValueError("Неизвестный формат экспорта. Допустимы: 'folder', 'html'.")

    if mode not in {"copy", "hardlink", "auto", "symlink"}:
        raise ValueError("Неизвестный режим экспорта. Допустимы: 'copy', 'hardlink', 'auto', 'symlink'.")

    if not board.items:
        raise ValueError("В мудборде нет изображений для экспорта.")

    cancel = cancel or Event()
    progress = progress or (lambda n, total, text: None)

    def check_cancel():
        if cancel.is_set():
            raise Cancelled("Экспорт отменён пользователем.")

    check_cancel()

    root = Path(target_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

    slug = sanitize_filename(board.name, 40) or "board"
    unique_tag = uuid.uuid4().hex[:6]
    final_dir = root / f"refer-{slug}-{unique_tag}"
    stage_dir = Path(tempfile.mkdtemp(prefix=".refer-tmp-", dir=root))

    records = []
    cards_html = []

    try:
        images_dir = stage_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        if format == "html":
            previews_dir = stage_dir / "previews"
            previews_dir.mkdir(parents=True, exist_ok=True)

        total_items = len(board.items)

        for n, item in enumerate(board.items, 1):
            check_cancel()

            # Determine source path: prefer local_path if exists, otherwise thumbnail_path
            src = None
            if item.local_path and Path(item.local_path).exists():
                src = Path(item.local_path).resolve()
            elif item.thumbnail_path and Path(item.thumbnail_path).exists():
                src = Path(item.thumbnail_path).resolve()

            if not src or not src.is_file():
                logger.warning(f"Файл для ассета #{item.id} не найден на диске, пропуск.")
                continue

            suffix = src.suffix.lower()
            if not suffix:
                suffix = ".webp"

            # Clean name with author / project
            meta_parts = []
            if item.author:
                meta_parts.append(sanitize_filename(item.author, 24))
            if item.title:
                meta_parts.append(sanitize_filename(item.title, 30))

            meta_slug = "_".join(meta_parts)
            if not meta_slug:
                meta_slug = f"asset_{item.id}"

            if use_numbered_names:
                filename = f"{n:03d}_{meta_slug}{suffix}"
            else:
                filename = f"{src.stem}_{item.id}{suffix}"

            rel_img = f"images/{filename}"
            dst = stage_dir / rel_img
            actual_mode = mode

            # Perform linking or copying
            if mode in {"hardlink", "auto"}:
                try:
                    os.link(src, dst)
                    actual_mode = "hardlink"
                except OSError:
                    if mode == "hardlink":
                        raise
                    actual_mode = "copy"

            elif mode == "symlink":
                os.symlink(src, dst)
                actual_mode = "symlink"

            if actual_mode == "copy":
                with src.open("rb") as inp, dst.open("xb") as out:
                    while True:
                        check_cancel()
                        buf = inp.read(4 * 1024 * 1024)
                        if not buf:
                            break
                        out.write(buf)
                try:
                    shutil.copystat(src, dst)
                except Exception:
                    pass

            # HTML previews and cards
            if format == "html":
                thumb_rel = f"previews/{n:03d}.jpg"
                thumb_dest = stage_dir / thumb_rel

                try:
                    with Image.open(dst) as img:
                        img = ImageOps.exif_transpose(img)
                        img.thumbnail((1000, 1000))
                        rgb_img = img.convert("RGB")
                        rgb_img.save(thumb_dest, "JPEG", quality=88, optimize=True)
                except Exception as e:
                    logger.debug(f"Could not resize preview with PIL: {e}, copying raw")
                    shutil.copy2(dst, thumb_dest)

                display_title = html.escape(f"{item.author} — {item.title}" if item.author and item.title else item.title or item.author or f"Кадр #{n}")
                cards_html.append(f"""
                <div class="card" onclick="openModal('{quote(rel_img)}')">
                    <div class="img-wrap">
                        <img src="{quote(thumb_rel)}" alt="{display_title}" loading="lazy">
                    </div>
                    <div class="card-caption">
                        <span class="num">{n:02d}</span>
                        <span class="title">{display_title}</span>
                    </div>
                </div>
                """)

            records.append({
                "index": n,
                "asset_id": item.id,
                "title": item.title,
                "author": item.author,
                "file": rel_img,
                "source_file": src.name,
                "mode": actual_mode,
                "is_cover": item.is_cover
            })

            progress(n, total_items, item.title or item.author or f"#{n}")

        # Write manifest.json
        manifest = {
            "version": 1,
            "refer_board_id": board.id,
            "board_name": board.name,
            "description": board.description,
            "exported_at": datetime.now().isoformat(),
            "format": format,
            "items_count": len(records),
            "items": records
        }

        with open(stage_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        # Write index.html if html format
        if format == "html":
            escaped_name = html.escape(board.name)
            escaped_desc = html.escape(board.description) if board.description else ""
            desc_html = f'<p class="meta-desc">{escaped_desc}</p>' if escaped_desc else ""
            html_content = _build_html_template(
                title=escaped_name,
                desc_html=desc_html,
                cards_html="".join(cards_html),
                date_str=datetime.now().strftime("%d.%m.%Y"),
                count_str=str(len(records))
            )
            with open(stage_dir / "index.html", "w", encoding="utf-8") as f:
                f.write(html_content)

        check_cancel()

        # Atomic rename to final directory
        os.rename(stage_dir, final_dir)

        modes_summary = {}
        for r in records:
            modes_summary[r["mode"]] = modes_summary.get(r["mode"], 0) + 1

        return {
            "directory": str(final_dir),
            "entrypoint": str(final_dir / "index.html" if format == "html" else final_dir),
            "count": len(records),
            "modes": modes_summary
        }

    except Exception:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise


def _build_html_template(title: str, desc_html: str, cards_html: str, date_str: str, count_str: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} — Референсы</title>
    <style>
        :root {{
            --bg: #111215;
            --card-bg: #1a1b1f;
            --text-main: #f0f2f5;
            --text-muted: #8a909a;
            --accent: #29b6f6;
            --border: #282a30;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg);
            color: var(--text-main);
            padding: 32px 24px;
            line-height: 1.5;
        }}
        header {{
            max-width: 1600px;
            margin: 0 auto 28px;
            display: flex;
            justify-content: space-between;
            align-items: flex-end;
            border-bottom: 1px solid var(--border);
            padding-bottom: 18px;
        }}
        h1 {{ font-size: 26px; font-weight: 700; color: #fff; letter-spacing: -0.3px; }}
        .meta-desc {{ color: var(--text-muted); font-size: 14px; margin-top: 4px; }}
        .meta-tag {{ font-size: 13px; color: var(--text-muted); }}
        .grid {{
            max-width: 1600px;
            margin: 0 auto;
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
            gap: 20px;
        }}
        .card {{
            background: var(--card-bg);
            border-radius: 8px;
            border: 1px solid var(--border);
            overflow: hidden;
            display: flex;
            flex-direction: column;
            cursor: pointer;
            transition: transform 0.18s ease, border-color 0.18s ease;
        }}
        .card:hover {{
            transform: translateY(-3px);
            border-color: #3e4450;
        }}
        .img-wrap {{
            width: 100%;
            height: 240px;
            background: #0d0e10;
            display: flex;
            align-items: center;
            justify-content: center;
            overflow: hidden;
        }}
        .img-wrap img {{
            width: 100%;
            height: 100%;
            object-fit: cover;
            display: block;
        }}
        .card-caption {{
            padding: 10px 14px;
            font-size: 13px;
            display: flex;
            align-items: center;
            gap: 10px;
            background: var(--card-bg);
        }}
        .num {{
            font-weight: 700;
            color: var(--accent);
            font-size: 12px;
        }}
        .title {{
            color: var(--text-main);
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        /* Modal */
        #modal {{
            display: none;
            position: fixed;
            inset: 0;
            background: rgba(0, 0, 0, 0.94);
            z-index: 1000;
            justify-content: center;
            align-items: center;
            padding: 30px;
        }}
        #modal.open {{ display: flex; }}
        #modal-img {{
            max-width: 95vw;
            max-height: 92vh;
            object-fit: contain;
            border-radius: 4px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.8);
        }}
        #modal-close {{
            position: absolute;
            top: 20px;
            right: 25px;
            color: #fff;
            font-size: 36px;
            cursor: pointer;
            width: 44px;
            height: 44px;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 50%;
            background: rgba(255,255,255,0.1);
        }}
        /* Print Stylesheet (A4 Landscape Album) */
        @page {{
            size: A4 landscape;
            margin: 12mm;
        }}
        @media print {{
            body {{ background: white; color: black; padding: 0; }}
            header {{ border-bottom: 2px solid #ccc; margin-bottom: 15px; padding-bottom: 8px; }}
            h1 {{ color: black; font-size: 18pt; }}
            .meta-tag, .meta-desc {{ color: #555; }}
            .grid {{ display: block; }}
            .card {{
                display: inline-block;
                vertical-align: top;
                width: 32%;
                margin: 0 0.8% 12px 0;
                border: 1px solid #ccc;
                page-break-inside: avoid;
                break-inside: avoid;
                background: white;
            }}
            .img-wrap {{ height: 48mm; background: white; }}
            .card-caption {{ font-size: 9pt; color: black; }}
            .title {{ color: black; }}
            .num {{ color: black; }}
            #modal {{ display: none !important; }}
        }}
    </style>
</head>
<body>
    <header>
        <div>
            <h1>{title}</h1>
            {desc_html}
        </div>
        <div class="meta-tag">
            {count_str} референсов • Экспортировано {date_str}
        </div>
    </header>

    <div class="grid">
        {cards_html}
    </div>

    <div id="modal" onclick="closeModal()">
        <span id="modal-close" onclick="closeModal()">&times;</span>
        <img id="modal-img" src="" alt="Full view" onclick="event.stopPropagation()">
    </div>

    <script>
        function openModal(src) {{
            const modal = document.getElementById('modal');
            const img = document.getElementById('modal-img');
            img.src = src;
            modal.classList.add('open');
        }}
        function closeModal() {{
            document.getElementById('modal').classList.remove('open');
        }}
        document.addEventListener('keydown', (e) => {{
            if (e.key === 'Escape') closeModal();
        }});
    </script>
</body>
</html>
"""

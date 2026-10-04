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
from urllib.parse import quote, urlsplit
from typing import Optional, Callable, Dict, Any

import requests
from PIL import Image, ImageOps
from database.collection_repository import BoardSnapshot
from scrapers.cdn_resolver import resolve_master_url

logger = logging.getLogger(__name__)

MIME = "application/x-refer-asset-ids"
EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}


class Cancelled(Exception):
    """Исключение при ручной отмене экспорта пользователем."""
    pass


def sanitize_filename(name: str, max_length: int = 50) -> str:
    slug = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name or '')
    slug = slug.strip(' .')[:max_length].strip(' .')
    return slug or 'item'


def download_web_image(
    url: str,
    target_path_without_ext: Path,
    cancel: Optional[Event] = None,
    timeout: int = 30,
    referer: str = ""
) -> Optional[Path]:
    """
    Загружает оригинальное изображение в полном качестве с CDN источника.
    Автоматически нормализует URL к мастер-разрешению (large_jpg, без обрезки).
    Возвращает итоговый Path с правильным расширением файла или None в случае сбоя.
    """
    if not url or not url.startswith("http"):
        return None

    master_url = resolve_master_url(url)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }
    if referer and referer.startswith("http"):
        headers["Referer"] = referer
    elif "archdaily" in master_url:
        headers["Referer"] = "https://www.archdaily.com/"
    elif "behance" in master_url:
        headers["Referer"] = "https://www.behance.net/"

    url_path = urlsplit(master_url).path
    ext = os.path.splitext(url_path)[1].lower()
    if not ext or ext not in EXTENSIONS:
        ext = ".jpg"

    target_path_without_ext.parent.mkdir(parents=True, exist_ok=True)
    final_path = target_path_without_ext.with_suffix(ext)
    tmp_path = final_path.with_suffix(f"{ext}.tmp_{uuid.uuid4().hex[:6]}")

    try:
        with requests.get(master_url, stream=True, timeout=timeout, headers=headers) as resp:
            if resp.status_code != 200:
                logger.warning(f"Не удалось скачать оригинал {master_url}: HTTP {resp.status_code}")
                return None

            content_type = (resp.headers.get("Content-Type") or "").lower()
            if "png" in content_type:
                ext = ".png"
            elif "webp" in content_type:
                ext = ".webp"
            elif "jpeg" in content_type or "jpg" in content_type:
                ext = ".jpg"

            final_path = target_path_without_ext.with_suffix(ext)

            total_downloaded = 0
            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    if cancel and cancel.is_set():
                        tmp_path.unlink(missing_ok=True)
                        return None
                    if chunk:
                        f.write(chunk)
                        total_downloaded += len(chunk)

            if total_downloaded < 1024:
                tmp_path.unlink(missing_ok=True)
                return None

            tmp_path.replace(final_path)
            return final_path

    except Exception as e:
        logger.warning(f"Ошибка скачивания оригинала {master_url}: {e}")
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        return None


def export_moodboard(
    board: BoardSnapshot,
    target_dir: str | Path,
    format: str = "web_html",
    mode: str = "web",
    use_numbered_names: bool = True,
    download_originals: bool = True,
    cancel: Optional[Event] = None,
    progress: Optional[Callable[[int, int, str], None]] = None
) -> Dict[str, Any]:
    """
    Экспортирует снимок набора в папку проекта.
    
    Args:
        board: Снимок набора (BoardSnapshot)
        target_dir: Путь к целевой рабочей папке
        format: 'web_html' (0 МБ на диске, веб-ссылки на оригиналы),
                'folder' (файлы + manifest) или 'html'/'offline_html' (оффлайн копии)
        mode: 'web', 'copy', 'hardlink', 'auto', 'symlink'
        use_numbered_names: Добавлять нумерацию 001_Author_Project.ext
        cancel: threading.Event для безопасного прерывания
        progress: callback(current, total, title)
    """
    valid_formats = {"web_html", "folder", "html", "offline_html"}
    if format not in valid_formats:
        raise ValueError(f"Неизвестный формат экспорта: '{format}'. Допустимы: {', '.join(sorted(valid_formats))}.")

    if not board.items:
        raise ValueError("В мудборде нет изображений для экспорта.")

    if format == "web_html" or (format == "html" and mode == "web"):
        return _export_web_moodboard(board, target_dir, cancel=cancel, progress=progress)

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

        if format in {"html", "offline_html"}:
            previews_dir = stage_dir / "previews"
            previews_dir.mkdir(parents=True, exist_ok=True)

        total_items = len(board.items)

        for n, item in enumerate(board.items, 1):
            check_cancel()

            # Clean name with author / project
            meta_parts = []
            if item.author:
                meta_parts.append(sanitize_filename(item.author, 24))
            if item.title:
                meta_parts.append(sanitize_filename(item.title, 30))

            meta_slug = "_".join(meta_parts)
            if not meta_slug:
                meta_slug = f"asset_{item.id}"

            base_name = f"{n:03d}_{meta_slug}" if use_numbered_names else f"{meta_slug}_{item.id}"
            target_without_ext = images_dir / base_name

            downloaded_file = None
            if download_originals and item.original_url and item.original_url.startswith("http"):
                progress(n, total_items, f"Скачивание #{n}/{total_items}: {meta_slug}…")
                downloaded_file = download_web_image(
                    url=item.original_url,
                    target_path_without_ext=target_without_ext,
                    cancel=cancel,
                    referer=item.project_url
                )

            if downloaded_file and downloaded_file.exists():
                dst = downloaded_file
                filename = dst.name
                rel_img = f"images/{filename}"
                actual_mode = "downloaded_original"
            else:
                # Fallback: prefer local_path if exists, otherwise thumbnail_path
                src = None
                if item.local_path and Path(item.local_path).exists():
                    src = Path(item.local_path).resolve()
                elif item.thumbnail_path and Path(item.thumbnail_path).exists():
                    src = Path(item.thumbnail_path).resolve()

                if not src or not src.is_file():
                    logger.warning(f"Файл для ассета #{item.id} не найден на диске, пропуск.")
                    continue

                suffix = src.suffix.lower() or ".webp"
                filename = f"{base_name}{suffix}"
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
            if format in {"html", "offline_html"}:
                thumb_rel = f"previews/{n:03d}.jpg"
                thumb_dest = stage_dir / thumb_rel
                thumb_dest.parent.mkdir(parents=True, exist_ok=True)

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
                "source_file": dst.name if downloaded_file else (src.name if src else ""),
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
        if format in {"html", "offline_html"}:
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
            "entrypoint": str(final_dir / "index.html" if format in {"html", "offline_html"} else final_dir),
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


def resolve_project_web_url(project_url: str, domain: str) -> str:
    """Нормализует URL проекта для перехода на сайт (ArchDaily, Behance и т.д.)."""
    if not project_url:
        return ""
    p_url = str(project_url).strip()
    if p_url.startswith("http://") or p_url.startswith("https://"):
        return p_url
    dom = (domain or "").lower()
    clean = p_url.strip("/")
    if "archdaily" in dom or dom == "archdaily.com":
        return f"https://www.archdaily.com/{clean}"
    if "behance" in dom or dom == "behance.net":
        return f"https://www.behance.net/gallery/{clean}"
    if dom:
        return f"https://{dom}/{clean}"
    if clean.isdigit():
        return f"https://www.archdaily.com/{clean}"
    return project_url


def _export_web_moodboard(
    board: BoardSnapshot,
    target_dir: str | Path,
    cancel: Optional[Event] = None,
    progress: Optional[Callable[[int, int, str], None]] = None
) -> Dict[str, Any]:
    """
    Экспортирует снимок набора в виде легкого автономного HTML-файла в папке проекта.
    Zero Disk Duplication: физические копии изображений не создаются, используются
    прямые веб-ссылки на оригиналы с CDN и локальные миниатюры в качестве оффлайн-фоллбэка.
    """
    cancel = cancel or Event()
    progress = progress or (lambda n, total, text: None)

    if cancel.is_set():
        raise Cancelled("Экспорт отменён пользователем.")

    root = Path(target_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

    records = []
    cards_html = []
    total_items = len(board.items)

    for n, item in enumerate(board.items, 1):
        if cancel.is_set():
            raise Cancelled("Экспорт отменён пользователем.")

        img_src = ""
        if item.original_url and item.original_url.startswith("http"):
            img_src = item.original_url
        elif item.thumbnail_path and Path(item.thumbnail_path).exists():
            img_src = Path(item.thumbnail_path).as_uri()
        elif item.local_path and Path(item.local_path).exists():
            img_src = Path(item.local_path).as_uri()

        fallback_src = ""
        if item.thumbnail_path and Path(item.thumbnail_path).exists():
            fallback_src = Path(item.thumbnail_path).as_uri()
        elif item.local_path and Path(item.local_path).exists():
            fallback_src = Path(item.local_path).as_uri()

        project_web_url = resolve_project_web_url(item.project_url, item.source_domain)

        author_display = item.author.strip() if item.author else "Архитектура"
        title_display = item.title.strip() if item.title else f"Референс #{n}"
        location_display = item.project_location.strip() if item.project_location else ""

        search_corpus = f"{author_display} {title_display} {location_display} {item.source_domain}".lower()

        card_title = html.escape(title_display)
        card_author = html.escape(author_display)
        card_location = html.escape(location_display)

        onerror_attr = f"onerror=\"if(this.src!=='{fallback_src}'){{this.src='{fallback_src}';}}\"" if fallback_src else ""
        meta_badges = []
        if location_display:
            meta_badges.append(f"<span>📍 {card_location}</span>")
        if item.source_domain:
            meta_badges.append(f"<span>{html.escape(item.source_domain)}</span>")

        cards_html.append(f"""
        <div class="card" data-index="{n-1}" data-search="{html.escape(search_corpus)}" onclick="openModal({n-1})">
            <div class="img-wrap">
                <img src="{img_src}" alt="{card_title}" loading="lazy" {onerror_attr}>
            </div>
            <div class="card-caption">
                <span class="card-num">{n:02d}</span>
                <div class="card-text">
                    <div class="card-author">{card_author}</div>
                    <div class="card-title">{card_title}</div>
                    {"<div class='card-meta-line'>" + "".join(meta_badges) + "</div>" if meta_badges else ""}
                </div>
            </div>
        </div>
        """)

        records.append({
            "index": n,
            "asset_id": item.id,
            "title": item.title,
            "author": item.author,
            "location": item.project_location,
            "domain": item.source_domain,
            "original_url": item.original_url,
            "project_url": project_web_url,
            "image_src": img_src,
            "fallback_src": fallback_src,
            "is_cover": item.is_cover
        })

        progress(n, total_items, item.title or item.author or f"#{n}")

    escaped_board_name = html.escape(board.name)
    escaped_desc = html.escape(board.description) if board.description else ""
    desc_html = f'<p class="header-desc">{escaped_desc}</p>' if escaped_desc else ""

    html_page = _build_web_html_template(
        title=escaped_board_name,
        desc_html=desc_html,
        cards_html="".join(cards_html),
        items_json=json.dumps(records, ensure_ascii=False),
        date_str=datetime.now().strftime("%d.%m.%Y"),
        count_str=str(len(records))
    )

    html_target = root / "moodboard.html"
    tmp_html = root / f".moodboard-{uuid.uuid4().hex[:6]}.tmp"
    with open(tmp_html, "w", encoding="utf-8") as f:
        f.write(html_page)
    tmp_html.replace(html_target)

    manifest = {
        "version": 1,
        "format": "web_html",
        "refer_board_id": board.id,
        "board_name": board.name,
        "description": board.description,
        "exported_at": datetime.now().isoformat(),
        "items_count": len(records),
        "items": records
    }
    tmp_manifest = root / f".manifest-{uuid.uuid4().hex[:6]}.tmp"
    with open(tmp_manifest, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    tmp_manifest.replace(root / "manifest.json")

    return {
        "directory": str(root),
        "entrypoint": str(html_target),
        "count": len(records),
        "modes": {"web_links": len(records)},
        "format": "web_html"
    }


def sync_collection_web_moodboard(repo, collection_id: int, target_dir: Optional[str] = None) -> Optional[Path]:
    """Быстро генерирует или обновляет moodboard.html в папке проекта (0 МБ на диске)."""
    try:
        snapshot = repo.snapshot(collection_id)
        out_dir = target_dir or snapshot.export_dir
        if not out_dir:
            return None
        p = Path(out_dir).expanduser().resolve()
        if not p.exists():
            return None
        result = export_moodboard(
            board=snapshot,
            target_dir=p,
            format="web_html",
            mode="web"
        )
        return Path(result["entrypoint"])
    except Exception as e:
        logger.warning(f"Could not auto-sync moodboard for collection #{collection_id}: {e}")
        return None


def _build_web_html_template(
    title: str,
    desc_html: str,
    cards_html: str,
    items_json: str,
    date_str: str,
    count_str: str
) -> str:
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} — Референсы (Refer)</title>
    <style>
        :root {{
            --bg: #0d0e12;
            --card-bg: #16181e;
            --card-hover: #1e2029;
            --text-main: #f0f2f5;
            --text-muted: #8b92a0;
            --accent: #29b6f6;
            --accent-hover: #4fc3f7;
            --border: #262933;
            --border-hover: #3d4352;
            --badge-bg: #222530;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background-color: var(--bg);
            color: var(--text-main);
            line-height: 1.5;
            padding-bottom: 60px;
        }}
        header {{
            position: sticky;
            top: 0;
            z-index: 100;
            background: rgba(13, 14, 18, 0.90);
            backdrop-filter: blur(14px);
            border-bottom: 1px solid var(--border);
            padding: 14px 24px;
        }}
        .header-inner {{
            max-width: 1720px;
            margin: 0 auto;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            flex-wrap: wrap;
        }}
        .header-title-group h1 {{
            font-size: 20px;
            font-weight: 700;
            color: #fff;
            letter-spacing: -0.2px;
        }}
        .header-desc {{
            font-size: 13px;
            color: var(--text-muted);
            margin-top: 2px;
        }}
        .header-meta {{
            font-size: 12px;
            color: var(--text-muted);
            margin-top: 3px;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .count-badge {{
            background-color: var(--badge-bg);
            color: var(--accent);
            padding: 2px 8px;
            border-radius: 10px;
            font-weight: 600;
            font-size: 11px;
        }}
        .header-controls {{
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }}
        .search-box {{
            position: relative;
            display: flex;
            align-items: center;
        }}
        .search-box input {{
            background: #181920;
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 7px 12px 7px 32px;
            color: #fff;
            font-size: 13px;
            width: 240px;
            outline: none;
            transition: border-color 0.18s, width 0.18s;
        }}
        .search-box input:focus {{
            border-color: var(--accent);
            width: 300px;
        }}
        .search-icon {{
            position: absolute;
            left: 10px;
            color: var(--text-muted);
            font-size: 13px;
            pointer-events: none;
        }}
        .btn-ctrl {{
            background: #1c1e25;
            color: #e0e4ec;
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 7px 13px;
            font-size: 12px;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            transition: all 0.18s ease;
            user-select: none;
        }}
        .btn-ctrl:hover {{
            background: #252833;
            border-color: var(--border-hover);
            color: #fff;
        }}
        .container {{
            max-width: 1720px;
            margin: 24px auto 0;
            padding: 0 24px;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
            gap: 20px;
        }}
        .grid.compact {{
            grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
            gap: 14px;
        }}
        .card {{
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            cursor: pointer;
            transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
        }}
        .card:hover {{
            transform: translateY(-3px);
            border-color: var(--border-hover);
            box-shadow: 0 10px 24px rgba(0, 0, 0, 0.4);
        }}
        .img-wrap {{
            position: relative;
            width: 100%;
            height: 230px;
            background: #090a0d;
            overflow: hidden;
        }}
        .grid.compact .img-wrap {{
            height: 160px;
        }}
        .img-wrap img {{
            width: 100%;
            height: 100%;
            object-fit: cover;
            display: block;
            transition: transform 0.28s ease;
        }}
        .card:hover .img-wrap img {{
            transform: scale(1.03);
        }}
        .card-caption {{
            padding: 10px 12px;
            display: flex;
            align-items: flex-start;
            gap: 10px;
            background: var(--card-bg);
        }}
        .card-num {{
            font-size: 11px;
            font-weight: 700;
            color: var(--accent);
            background: rgba(41, 182, 246, 0.12);
            padding: 2px 6px;
            border-radius: 4px;
            margin-top: 1px;
        }}
        .card-text {{
            flex: 1;
            min-width: 0;
        }}
        .card-author {{
            font-size: 12px;
            font-weight: 600;
            color: #ffffff;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .card-title {{
            font-size: 13px;
            color: var(--text-muted);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            margin-top: 2px;
        }}
        .card-meta-line {{
            display: flex;
            align-items: center;
            gap: 8px;
            margin-top: 4px;
            font-size: 11px;
            color: #68707f;
        }}
        #modal {{
            display: none;
            position: fixed;
            inset: 0;
            background: rgba(6, 7, 9, 0.96);
            z-index: 1000;
            flex-direction: column;
            justify-content: space-between;
            align-items: center;
            padding: 18px 24px;
            box-sizing: border-box;
        }}
        #modal.open {{
            display: flex;
        }}
        .modal-top-bar {{
            width: 100%;
            display: flex;
            justify-content: space-between;
            align-items: center;
            color: #fff;
            padding: 0 10px;
        }}
        .modal-counter {{
            font-size: 14px;
            color: var(--text-muted);
            font-weight: 600;
        }}
        .modal-close-btn {{
            font-size: 26px;
            color: #ccc;
            cursor: pointer;
            width: 38px;
            height: 38px;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 50%;
            background: rgba(255,255,255,0.08);
            transition: background 0.18s, color 0.18s;
        }}
        .modal-close-btn:hover {{
            background: rgba(255,255,255,0.2);
            color: #fff;
        }}
        .modal-center {{
            position: relative;
            flex: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            width: 100%;
            min-height: 0;
            margin: 10px 0;
        }}
        #modal-img {{
            max-width: 95vw;
            max-height: 78vh;
            object-fit: contain;
            border-radius: 4px;
            box-shadow: 0 14px 45px rgba(0,0,0,0.85);
        }}
        .nav-btn {{
            position: absolute;
            top: 50%;
            transform: translateY(-50%);
            background: rgba(22, 24, 30, 0.75);
            border: 1px solid rgba(255,255,255,0.15);
            color: #fff;
            font-size: 20px;
            width: 46px;
            height: 46px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.18s ease;
            user-select: none;
        }}
        .nav-btn:hover {{
            background: rgba(41, 182, 246, 0.9);
            border-color: var(--accent);
        }}
        .nav-prev {{ left: 16px; }}
        .nav-next {{ right: 16px; }}
        .modal-bottom-bar {{
            background: #15171d;
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 10px 20px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 20px;
            max-width: 95vw;
            width: 920px;
        }}
        .modal-info {{
            min-width: 0;
            flex: 1;
        }}
        .modal-author {{
            font-size: 14px;
            font-weight: 700;
            color: #fff;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .modal-title {{
            font-size: 13px;
            color: var(--text-muted);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .modal-actions {{
            display: flex;
            align-items: center;
            gap: 10px;
            flex-shrink: 0;
        }}
        .modal-btn {{
            background: #20232c;
            border: 1px solid #333846;
            color: #e2e6ef;
            padding: 6px 13px;
            border-radius: 5px;
            font-size: 12px;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            cursor: pointer;
            transition: background 0.18s, color 0.18s;
        }}
        .modal-btn:hover {{
            background: var(--accent);
            border-color: var(--accent);
            color: #000;
            font-weight: 600;
        }}
        @page {{
            size: A4 landscape;
            margin: 10mm;
        }}
        @media print {{
            body {{ background: #fff !important; color: #000 !important; padding: 0; }}
            header {{ position: static; background: none; border-bottom: 2px solid #ccc; padding: 0 0 10px; }}
            .header-controls {{ display: none !important; }}
            .header-title-group h1 {{ color: #000 !important; font-size: 18pt; }}
            .header-meta {{ color: #555 !important; }}
            .container {{ margin: 10px 0 0; padding: 0; max-width: 100%; }}
            .grid {{ display: block !important; }}
            .card {{
                display: inline-block !important;
                vertical-align: top;
                width: 32% !important;
                margin: 0 0.8% 12px 0 !important;
                border: 1px solid #ddd !important;
                background: #fff !important;
                page-break-inside: avoid;
                break-inside: avoid;
                box-shadow: none !important;
                transform: none !important;
            }}
            .img-wrap {{ height: 48mm !important; background: #fff !important; }}
            .img-wrap img {{ transform: none !important; }}
            .card-caption {{ background: #fff !important; padding: 6px 8px !important; }}
            .card-num {{ color: #000 !important; background: #eee !important; }}
            .card-author {{ color: #000 !important; font-size: 9pt !important; }}
            .card-title {{ color: #555 !important; font-size: 8.5pt !important; }}
            .card-meta-line {{ color: #777 !important; font-size: 7.5pt !important; }}
            #modal {{ display: none !important; }}
        }}
    </style>
</head>
<body>
    <header>
        <div class="header-inner">
            <div class="header-title-group">
                <h1>{title}</h1>
                {desc_html}
                <div class="header-meta">
                    <span class="count-badge" id="visible-badge">{count_str} кадров</span>
                    <span>Экспортировано {date_str}</span>
                    <span>• Референсы Refer</span>
                </div>
            </div>
            <div class="header-controls">
                <div class="search-box">
                    <span class="search-icon">🔍</span>
                    <input type="text" id="searchInput" placeholder="Поиск (автор, проект, город)…" oninput="filterCards()">
                </div>
                <button class="btn-ctrl" onclick="toggleCompactMode()" id="btnCompact">⊞ Сетка</button>
                <button class="btn-ctrl" onclick="window.print()">🖨 Печать (A4)</button>
            </div>
        </div>
    </header>

    <div class="container">
        <div class="grid" id="cardsGrid">
            {cards_html}
        </div>
    </div>

    <!-- Modal Lightbox -->
    <div id="modal" onclick="closeModal()">
        <div class="modal-top-bar" onclick="event.stopPropagation()">
            <span class="modal-counter" id="modalCounter">1 / 1</span>
            <span class="modal-close-btn" onclick="closeModal()">&times;</span>
        </div>
        <div class="modal-center">
            <div class="nav-btn nav-prev" onclick="event.stopPropagation(); prevImage();">&#10094;</div>
            <img id="modal-img" src="" alt="Full view" onclick="event.stopPropagation()">
            <div class="nav-btn nav-next" onclick="event.stopPropagation(); nextImage();">&#10095;</div>
        </div>
        <div class="modal-bottom-bar" onclick="event.stopPropagation()">
            <div class="modal-info">
                <div class="modal-author" id="modalAuthor">Автор</div>
                <div class="modal-title" id="modalTitle">Проект</div>
            </div>
            <div class="modal-actions">
                <a class="modal-btn" id="modalBtnProject" href="#" target="_blank" rel="noopener noreferrer">🌐 Страница проекта</a>
                <a class="modal-btn" id="modalBtnOrig" href="#" target="_blank" rel="noopener noreferrer">🔍 Оригинал фото</a>
            </div>
        </div>
    </div>

    <script>
        const items = {items_json};
        let currentIndex = 0;

        function openModal(index) {{
            if (index < 0 || index >= items.length) return;
            currentIndex = index;
            updateModal();
            document.getElementById('modal').classList.add('open');
            document.body.style.overflow = 'hidden';
        }}

        function closeModal() {{
            document.getElementById('modal').classList.remove('open');
            document.body.style.overflow = '';
        }}

        function prevImage() {{
            if (!items.length) return;
            currentIndex = (currentIndex - 1 + items.length) % items.length;
            updateModal();
        }}

        function nextImage() {{
            if (!items.length) return;
            currentIndex = (currentIndex + 1) % items.length;
            updateModal();
        }}

        function updateModal() {{
            const it = items[currentIndex];
            if (!it) return;
            const img = document.getElementById('modal-img');
            img.src = it.image_src;

            document.getElementById('modalCounter').textContent = (currentIndex + 1) + ' / ' + items.length;
            document.getElementById('modalAuthor').textContent = it.author || 'Архитектура';
            document.getElementById('modalTitle').textContent = it.title + (it.location ? ' • ' + it.location : '');

            const btnProj = document.getElementById('modalBtnProject');
            if (it.project_url) {{
                btnProj.style.display = 'inline-flex';
                btnProj.href = it.project_url;
            }} else {{
                btnProj.style.display = 'none';
            }}

            const btnOrig = document.getElementById('modalBtnOrig');
            if (it.original_url && it.original_url.startsWith('http')) {{
                btnOrig.style.display = 'inline-flex';
                btnOrig.href = it.original_url;
            }} else if (it.image_src) {{
                btnOrig.style.display = 'inline-flex';
                btnOrig.href = it.image_src;
            }} else {{
                btnOrig.style.display = 'none';
            }}
        }}

        function filterCards() {{
            const q = document.getElementById('searchInput').value.toLowerCase().trim();
            const cards = document.querySelectorAll('.card');
            let visible = 0;
            cards.forEach(c => {{
                const s = c.getAttribute('data-search') || '';
                if (!q || s.includes(q)) {{
                    c.style.display = '';
                    visible++;
                }} else {{
                    c.style.display = 'none';
                }}
            }});
            document.getElementById('visible-badge').textContent = visible + ' кадров' + (visible < items.length ? ' (из ' + items.length + ')' : '');
        }}

        function toggleCompactMode() {{
            const grid = document.getElementById('cardsGrid');
            grid.classList.toggle('compact');
            const isCompact = grid.classList.contains('compact');
            document.getElementById('btnCompact').textContent = isCompact ? '⊞ Обычная' : '▤ Компактно';
        }}

        document.addEventListener('keydown', (e) => {{
            const modal = document.getElementById('modal');
            if (!modal.classList.contains('open')) return;
            if (e.key === 'Escape') closeModal();
            else if (e.key === 'ArrowLeft') prevImage();
            else if (e.key === 'ArrowRight') nextImage();
        }});
    </script>
</body>
</html>
"""

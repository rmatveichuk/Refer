"""scrapers/plan_filter.py

Tier 0 / Tier 1 каскадного отсева архитектурных чертежей, планов, схем
в пайплайне Refer — до скачивания полноразмерных файлов и до Vision-токенов.

    from scrapers.plan_filter import is_drawing_by_text, is_drawing_by_header

    if is_drawing_by_text(url, alt) or is_drawing_by_header(url)[0]:
        return  # чертёж/схема, дальше не идём

Контракт ответов `is_drawing_by_header` (ровно четыре кода):
    "extreme_aspect_ratio" | "too_small" | "ok" | "skipped"

Файл — UTF-8 (в паттернах есть кириллица).
"""

from __future__ import annotations

import io
import logging
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image, UnidentifiedImageError

__all__ = [
    "is_drawing_by_text",
    "is_drawing_by_header",
    "MAX_HEAD_BYTES",
    "MIN_DIM",
    "AR_MAX",
    "AR_MIN",
]

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Блок 1. Отсев по URL / alt-тексту (стоимость ~0, работает прямо в краулере)
# --------------------------------------------------------------------------- #

#: Порог, после которого вызывающий код считает результат «мусором».
MAX_HEAD_BYTES = 65_536          # 64 KiB: хватает на SOF/IHDR любой картинки
MIN_DIM = 1000                   # обе стороны меньше — это превью/иконка/план
AR_MAX = 2.2                     # панорама-развёртка, раскладка фасада, аксонометрия
AR_MIN = 0.55                    # вертикальная развёртка/разрез

#: Разделители путей и имён файлов нормализуются в пробел, поэтому паттерны
#: ниже описаны для пробельно-разделённых токенов ("floor-plan-01" -> "floor plan 01").
_SEP_RE = re.compile(r"[_\-./\\|:;,+=()\[\]{}#?&%~]+")

#: Границы «слова»: латиница + кириллица + цифры.
#: \b из re не годится — он не работает на кириллице и пропускает цифры.
_LEFT = r"(?<![a-z\u0400-\u04ff0-9])"
_RIGHT = r"(?![a-z\u0400-\u04ff0-9])"

#: Токены-признаки чертежа. ВНИМАНИЕ: одиночного "plan"/"section" здесь нет
#: намеренно — "Plan B House" или "Section 8" иначе убьют весь проект.
#: Для них требуется цифровой хвост: plan_01, section-2, plans3.
_DRAWING_TOKENS: tuple[str, ...] = (
    r"floor\s?plans?",
    r"site\s?plans?",
    r"master\s?plans?",
    r"ground\s?floor\s?plans?",
    r"plans?\s?\d+",
    r"sections?\s?\d+",
    r"grundriss\w*",
    r"schnitt\w*",
    r"elevation\w*",
    r"план\w*",
    r"разрез\w*",
    r"axo",                       # guard не даст совпасть с "axon"/"axonometric"…
    r"axonometr\w*",              # …а это добьёт axonometric/axonometry/axonometria
    r"isometric\w*",
    r"isometry",
    r"diagram\w*",
    r"blueprint\w*",
    r"dwg",
    r"cad",
)

#: Токены-«оправдания»: если они есть, отсев не срабатывает (см. OVERRIDE_WINS).
_STRONG_OVERRIDE_TOKENS: tuple[str, ...] = (
    r"renders?",
    r"renderings?",
    r"photos?",
    r"photograph\w*",
    r"exterior\w*",
    r"interiors?",
    r"hero",
    r"beauty",
    r"final",
    r"hdr\w*",
)

#: Имена файлов с номерами кадров камер (dsc0421, img_8842).
#: В URL они могут оправдать случайное совпадение со словом "section" в пути,
#: но не должны перебивать явное текстовое описание чертежа в alt-теге.
_CAMERA_OVERRIDE_TOKENS: tuple[str, ...] = (
    r"dsc\s?\d+",
    r"img\s?\d+",
)

_OVERRIDE_TOKENS: tuple[str, ...] = _STRONG_OVERRIDE_TOKENS + _CAMERA_OVERRIDE_TOKENS

#: Опциональные токены, не входящие в базовый набор. Включаются вызовом
#: _wrap(_DRAWING_TOKENS + EXTRA_PATTERNS) — например, если в вашем корпусе
#: сайтов "drawing"/"планировка" действительно значат чертёж.
EXTRA_PATTERNS: tuple[str, ...] = (
    r"drawings?",
    r"key\s?plans?",
    r"master\s?plann\w*",
    r"планировк\w*",
    r"чертеж\w*",
    r"схем\w*",
    r"фрагмент\w*",
)

#: Спецификация говорит «override побеждает». Флаг вынесен наружу, чтобы
#: можно было перейти к приоритетной схеме (чертёж > override) без правки кода.
OVERRIDE_WINS = True

#: Домен в haystack не подмешиваем: "axonometric-studio.com" или
#: "plans.co" убьют весь хост оптом. Хотите иначе — include_host=True.
INCLUDE_HOST = False


def _wrap(tokens: tuple[str, ...]) -> re.Pattern[str]:
    """Собрать альтернативу из токенов, обернув её в границы «слова»."""
    return re.compile(_LEFT + "(?:" + "|".join(tokens) + ")" + _RIGHT)


_PLAN_RE = _wrap(_DRAWING_TOKENS)
_OVERRIDE_RE = _wrap(_OVERRIDE_TOKENS)
_STRONG_OVERRIDE_RE = _wrap(_STRONG_OVERRIDE_TOKENS)
_CAMERA_OVERRIDE_RE = _wrap(_CAMERA_OVERRIDE_TOKENS)


def _normalize(text: str) -> str:
    """lower + unquote + разделители -> пробел. Регистр кириллицы тоже складываем."""
    if not text:
        return ""
    t = urllib.parse.unquote(text).lower()
    t = _SEP_RE.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def _text_basis(url: str, alt_text: str, include_host: bool) -> str:
    """URL+alt в нормализованном виде. Host отбрасывается (см. INCLUDE_HOST)."""
    try:
        parts = urllib.parse.urlsplit(url)
        chunk = [parts.path or "", parts.query or ""]
        if include_host:
            chunk.insert(0, parts.netloc or "")
    except ValueError:                    # битый URL — работаем как с голым текстом
        chunk = [url]
    chunk.append(alt_text or "")
    return _normalize(" ".join(chunk))


def is_drawing_by_text(
    url: str,
    alt_text: str = "",
    *,
    include_host: bool = INCLUDE_HOST,
) -> bool:
    """True, если URL/alt выглядят как план, разрез, схема, аксонометрия, CAD.

    Решение принимается по нормализованному базису (path + query + alt).
    Наличие сильного override-токена (render/photo/exterior/hero/hdr…) даёт False,
    даже если рядом стоит drawing-токен.
    Слабые токены камер (img_1234, dsc_0123) в URL отменяют ложные срабатывания
    в URL (например section_1), но не перебивают явное указание чертежа в alt.
    """
    url_basis = _text_basis(url, "", include_host)
    alt_basis = _normalize(alt_text) if alt_text else ""
    basis = f"{url_basis} {alt_basis}".strip()
    if not basis:
        return False

    has_drawing_url = _PLAN_RE.search(url_basis) is not None if url_basis else False
    has_drawing_alt = _PLAN_RE.search(alt_basis) is not None if alt_basis else False

    if not (has_drawing_url or has_drawing_alt):
        return False

    if OVERRIDE_WINS:
        if _STRONG_OVERRIDE_RE.search(basis) is not None:
            return False
        if has_drawing_alt:
            # В alt прямо сказано, что это план — камера в URL не перебивает
            return True
        if _CAMERA_OVERRIDE_RE.search(url_basis) is not None:
            return False

    return True


# --------------------------------------------------------------------------- #
# Блок 2. Отсев по заголовку файла: Range-GET 64 KiB, без полной загрузки
# --------------------------------------------------------------------------- #

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _range_get(url: str, timeout: int) -> tuple[int, bytes, str]:
    """Забрать не больше MAX_HEAD_BYTES байт. Возвращает (status, data, content_type).

    Вынесено в отдельную функцию, чтобы тесты могли подменить её без сети.
    """
    req = urllib.request.Request(
        url,
        headers={
            "Range": f"bytes=0-{MAX_HEAD_BYTES - 1}",
            "User-Agent": _USER_AGENT,
            "Accept": "image/avif,image/webp,image/jpeg,image/png,*/*;q=0.8",
            # Критично: при gzip разбор SOF/IHDR по первым байтам невозможен.
            "Accept-Encoding": "identity",
            "Connection": "close",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = getattr(resp, "status", None) or resp.getcode()
        content_type = (resp.headers.get("Content-Type") or "").strip().lower()
        data = resp.read(MAX_HEAD_BYTES)      # на случай, если Range проигнорирован
    return int(status), data, content_type


_NON_IMAGE_PREFIXES = ("text/", "application/json", "application/xml", "image/svg")


def is_drawing_by_header(
    image_url: str,
    timeout: int = 5,
    *,
    parse_non_range: bool = False,
) -> tuple[bool, str]:
    """Геометрия картинки по её заголовку, без скачивания полного файла.

    Returns:
        (True, "extreme_aspect_ratio") — AR > 2.2 или < 0.55: развёртки, раскладки,
                                        аксонометрии, длинные панорамы-превью.
        (True, "too_small")           — обе стороны < 1000 px: превью, иконка,
                                        миниатюра плана.
        (False, "ok")                 — похоже на фотографию/крупный рендер.
        (False, "skipped")            — сети нет, сервер не отдал 206, файл не
                                        распознан. Решение отдаётся следующему тиру.
    """
    try:
        status, data, content_type = _range_get(image_url, timeout)
    except Exception as exc:                      # сеть на скрапинге сыпется разнообразно
        log.debug("plan_filter: range-get failed for %s: %s", image_url, exc)
        return False, "skipped"

    if status != 206 and not (parse_non_range and status == 200):
        return False, "skipped"                   # Range не поддержан — не гадаем
    if not data:
        return False, "skipped"
    if content_type.startswith(_NON_IMAGE_PREFIXES):
        return False, "skipped"                   # HTML-заглушка CDN, JSON-ошибка, SVG

    try:
        with Image.open(io.BytesIO(data)) as im:
            width, height = im.size
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        log.debug("plan_filter: undecodable header for %s: %s", image_url, exc)
        return False, "skipped"

    if not width or not height:
        return False, "skipped"

    aspect = width / height
    if aspect > AR_MAX or aspect < AR_MIN:
        return True, "extreme_aspect_ratio"
    if width < MIN_DIM and height < MIN_DIM:
        return True, "too_small"
    return False, "ok"


# --------------------------------------------------------------------------- #
# Self-тесты: python -m scrapers.plan_filter
# --------------------------------------------------------------------------- #

_FAILED: list[str] = []


def _check(name: str, got: object, want: object) -> None:
    ok = got == want
    if not ok:
        _FAILED.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got={got!r} want={want!r}")


def _img_bytes(width: int, height: int, fmt: str = "JPEG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (118, 134, 152)).save(buf, fmt, quality=70)
    return buf.getvalue()


class _PatchedFetch:
    """Подменяет _range_get на время теста, чтобы не ходить в сеть."""

    def __init__(self, fn) -> None:
        self.fn = fn

    def __enter__(self):
        global _range_get
        self._orig = _range_get
        _range_get = self.fn              # type: ignore[assignment]
        return self

    def __exit__(self, *exc) -> bool:
        global _range_get
        _range_get = self._orig           # type: ignore[assignment]
        return False


def _run_text_tests() -> None:
    print("\nБлок 1 — is_drawing_by_text")

    drawings = [
        ("archdaily", "https://www.archdaily.com/98765/house/wp-content/uploads/2023/05/floor-plan-01.jpg", ""),
        ("grundriss", "https://studio.ch/media/grundriss.pdf", ""),
        ("cyrillic-plan", "https://bureau.ru/img/план_1_этажа.jpg", ""),
        ("cyrillic-razrez", "https://bureau.ru/img/разрез-А-А.jpg", ""),
        ("plan-number", "https://x.com/proj/plan_02.jpg", ""),
        ("site-plan", "https://x.com/proj/site-plan.jpg", ""),
        ("section-number", "https://x.com/proj/section-3.jpg", ""),
        ("axonometric", "https://x.com/proj/axonometric-view.jpg", ""),
        ("isometric", "https://x.com/proj/isometric-scheme.jpg", ""),
        ("elevation", "https://x.com/proj/north-elevation.jpg", ""),
        ("alt-only", "https://x.com/media/IMG_8842.jpg", "Typical floor plan, level 2"),
        ("dwg", "https://x.com/files/proj-2024.dwg", ""),
        ("cad", "https://x.com/files/cad/export.png", ""),
    ]
    for name, url, alt in drawings:
        _check(f"drawing:{name}", is_drawing_by_text(url, alt), True)

    photos = [
        ("render-override", "https://x.com/proj/floor-plan-render-04.jpg", ""),
        ("photo-override", "https://x.com/proj/photo_dsc0421.jpg", ""),
        ("exterior-override", "https://x.com/renders/exterior_dusk.jpg", ""),
        ("hero-override", "https://x.com/proj/hero.jpg", ""),
        ("img-number-override", "https://x.com/proj/section_1_img_4402.jpg", ""),
        ("plain-plan-word", "https://x.com/projects/plan-b-house/image.jpg", ""),
        ("axon-word", "https://x.com/projects/axon-residence/image.jpg", ""),
        ("cadmium-word", "https://x.com/materials/cadmium-coating-01.jpg", ""),
        ("drawing-word", "https://x.com/proj/drawing-room-interior.jpg", ""),
        ("host-not-scanned", "https://axonometric-studio.com/proj/IMG_2201.jpg", ""),
    ]
    for name, url, alt in photos:
        _check(f"photo:{name}", is_drawing_by_text(url, alt), False)


def _run_header_tests() -> None:
    print("\nБлок 2 — is_drawing_by_header")

    wide = _img_bytes(3000, 600)
    scroll = _img_bytes(700, 1600)
    small = _img_bytes(800, 600)
    photo = _img_bytes(2000, 1200)
    photo_port = _img_bytes(1200, 1600)      # AR 0.75 — законный вертикальный кадр

    cases = [
        ("panorama-3000x600", (206, wide, "image/jpeg"), (True, "extreme_aspect_ratio")),
        ("scroll-700x1600", (206, scroll, "image/jpeg"), (True, "extreme_aspect_ratio")),
        ("thumb-800x600", (206, small, "image/jpeg"), (True, "too_small")),
        ("photo-2000x1200", (206, photo, "image/jpeg"), (False, "ok")),
        ("portrait-photo-1200x1600", (206, photo_port, "image/jpeg"), (False, "ok")),
        ("no-range-200", (200, photo, "image/jpeg"), (False, "skipped")),
        ("html-stub-206", (206, b"<html>nope</html>", "text/html"), (False, "skipped")),
        ("garbage-bytes", (206, b"\x00" * 4096, "image/jpeg"), (False, "skipped")),
        ("empty-data", (206, b"", "image/jpeg"), (False, "skipped")),
    ]

    for name, (status, data, ctype), want in cases:
        with _PatchedFetch(lambda u, t, s=status, d=data, c=ctype: (s, d, c)):
            _check(f"header:{name}", is_drawing_by_header("http://dummy/img.jpg"), want)


if __name__ == "__main__":
    _run_text_tests()
    _run_header_tests()
    if _FAILED:
        print(f"\nFAILED ({len(_FAILED)}): {', '.join(_FAILED)}")
        sys.exit(1)
    print("\nAll plan_filter self-tests passed successfully.")

"""Read-only file diagnostics. A missing drive never implies deleted assets."""
import ntpath
import os
import stat
from collections import Counter
from dataclasses import dataclass
from urllib.parse import urlparse, unquote


def file_path(value):
    if value and value.startswith("file:"):
        parsed = urlparse(value)
        path = unquote(parsed.path)
        if parsed.netloc and parsed.netloc != "localhost":
            path = "//" + parsed.netloc + path
        if len(path) > 2 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return path
    return value or ""


def path_state(path, directory=False):
    try:
        info = os.stat(path)
        valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        return "available" if valid else "wrong_type"
    except FileNotFoundError:
        return "missing"
    except (PermissionError, OSError, ValueError):
        return "unavailable"


@dataclass
class AvailabilityReport:
    checked: int
    issues: list
    total: int = 0
    cancelled: bool = False

    @property
    def counts(self):
        return dict(Counter(row["status"] for row in self.issues))


def inspect_files(db, cancellation=None, progress=None):
    with db.get_connection() as conn:
        rows = conn.execute("SELECT a.id, a.local_path, a.thumbnail_path, a.image_type, s.domain AS source_domain FROM assets a LEFT JOIN sources s ON s.id=a.source_id ORDER BY a.id").fetchall()
    issues, source_states, checked = [], {}, 0
    for row in rows:
        if cancellation is not None and cancellation.is_set():
            break
        checked += 1
        if progress and checked % 250 == 0:
            progress(checked, len(rows))
        row = dict(row)
        original, thumb = file_path(row["local_path"]), file_path(row["thumbnail_path"])
        source = file_path(row["source_domain"])
        local_source = bool(source and (ntpath.isabs(source) or source.startswith("/")))
        local = local_source or row["image_type"] == "Local"
        if local:
            root = source if local_source else ntpath.splitdrive(original)[0] + "\\" if ntpath.splitdrive(original)[0] else os.path.dirname(original)
            if root not in source_states:
                source_states[root] = path_state(root, directory=True) if root else "unavailable"
            if source_states[root] != "available":
                status, message, path = "source_unavailable", "Источник недоступен; данные сохранены", root
            elif not original:
                status, message, path = "path_missing", "В записи нет пути к оригиналу", source
            else:
                state = path_state(original)
                if state == "available":
                    continue
                status = "file_missing" if state == "missing" else "file_unavailable"
                message, path = ("Файл отсутствует в доступном каталоге" if state == "missing" else "Файл недоступен или путь ведёт к папке"), original
        else:
            target = thumb or original
            if target and path_state(target) == "available":
                continue
            if original and original != target and path_state(original) == "available":
                status, message, path = "preview_missing", "Превью недоступно; сохранённый файл доступен", target
            else:
                status, message, path = "preview_unavailable", "Нет доступного сохранённого изображения", target
        issues.append({"id": row["id"], "status": status, "message": message, "path": path, "source": source})
    if progress:
        progress(checked, len(rows))
    return AvailabilityReport(checked, issues, len(rows), checked < len(rows))

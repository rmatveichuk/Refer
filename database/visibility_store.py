"""Reversible image visibility, without mutating SQLite, FAISS or originals."""
import hashlib
import json
import os
import tempfile
from dataclasses import asdict, is_dataclass
from pathlib import Path
from threading import RLock


_locks = {}
_locks_guard = RLock()


def asset_identity(asset):
    from database.search_repository import normalized_path
    row = asdict(asset) if is_dataclass(asset) else dict(asset)
    # An ID reused by an imported/replaced library is insufficient identification.
    origin = row.get("original_url") or normalized_path(row.get("local_path") or row.get("thumbnail_path") or "")
    payload = json.dumps([origin, row.get("phash") or ""], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class VisibilityStore:
    def __init__(self, path):
        self.path = Path(path)
        with _locks_guard:
            self._lock = _locks.setdefault(str(self.path.resolve()), RLock())

    def entries(self):
        with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return {}
            except (OSError, ValueError) as error:
                raise RuntimeError("Не удалось прочитать список скрытых изображений. Проверьте файл hidden_assets.json.") from error
            entries = data.get("hidden") if isinstance(data, dict) and data.get("version") == 1 else None
            if not isinstance(entries, dict) or any(not str(key).isdigit() or not isinstance(value, str) or len(value) != 64 for key, value in entries.items()):
                raise RuntimeError("Список скрытых изображений имеет неподдерживаемый формат.")
            return entries

    def is_hidden(self, asset, entries=None):
        row = asdict(asset) if is_dataclass(asset) else dict(asset)
        entries = self.entries() if entries is None else entries
        expected = entries.get(str(row["id"]))
        return expected is not None and expected == asset_identity(row)

    def hide(self, assets):
        with self._lock:
            entries = self.entries()
            for asset in assets:
                row = asdict(asset) if is_dataclass(asset) else dict(asset)
                entries[str(int(row["id"]))] = asset_identity(row)
            self._write(entries)

    def restore(self, ids):
        with self._lock:
            entries = self.entries()
            for asset_id in ids:
                entries.pop(str(int(asset_id)), None)
            self._write(entries)

    def _write(self, entries):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=self.path.name + ".", suffix=".tmp", delete=False) as file:
                temp_path = Path(file.name)
                json.dump({"version": 1, "hidden": entries}, file, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, self.path)
        except OSError as error:
            raise RuntimeError("Не удалось сохранить скрытые изображения. Библиотека не изменена.") from error
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()

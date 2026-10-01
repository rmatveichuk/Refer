"""Versioned user-defined source groups and administrative enablement for Refer."""
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import logging
import ntpath
import os
from pathlib import Path
import tempfile
import uuid
from threading import RLock

from database.search_repository import normalized_path

logger = logging.getLogger(__name__)

_locks = {}
_locks_guard = RLock()

DEFAULT_GROUPS = [
    {"id": "grp_arch", "name": "Архитектура", "parent_id": None, "order": 0},
    {"id": "grp_3d", "name": "3D-библиотеки", "parent_id": None, "order": 1},
    {"id": "grp_work", "name": "Рабочие проекты", "parent_id": None, "order": 2},
]


class SourceGroupStore:
    def __init__(self, path=None, db=None, ini_path=None, store_path=None):
        import config
        resolved_path = store_path or path
        self.path = Path(resolved_path) if resolved_path else (config.APP_ROAMING_DIR / "source_groups.json")
        self.ini_path = Path(ini_path) if ini_path else (config.APP_ROAMING_DIR / "interface.ini")
        self.db = db
        with _locks_guard:
            self._lock = _locks.setdefault(str(self.path.resolve()), RLock())

        self._groups = []
        self._source_assignments = {}  # normalized_path(source) -> group_id
        self._disabled_sources = set()  # set of normalized_path(source)
        self._disabled_groups = set()  # set of group_id
        self._is_draft = False

        with self._lock:
            self.load()

    @staticmethod
    def _validate_tree_structure(groups: list[dict]):
        """Validates that group definitions are acyclic and parent references are valid."""
        g_ids = set()
        for g in groups:
            if not isinstance(g, dict) or "id" not in g or "name" not in g:
                raise RuntimeError("Некорректная запись группы: отсутствует id или name.")
            gid = g["id"]
            if gid in g_ids:
                raise RuntimeError(f"Обнаружен дубликат идентификатора группы: {gid}.")
            g_ids.add(gid)

        for g in groups:
            gid = g["id"]
            pid = g.get("parent_id")
            if pid is not None:
                if pid == gid:
                    raise RuntimeError(f"Группа {gid} ссылается сама на себя как на родителя (цикл).")
                if pid not in g_ids:
                    raise RuntimeError(f"Группа {gid} ссылается на несуществующую родительскую группу {pid}.")

        parent_map = {g["id"]: g.get("parent_id") for g in groups}
        for gid in g_ids:
            visited = set()
            curr = gid
            while curr is not None:
                if curr in visited:
                    raise RuntimeError(f"Обнаружен цикл в иерархии групп: группа {curr} зациклена.")
                visited.add(curr)
                curr = parent_map.get(curr)

    @contextmanager
    def _transaction(self):
        """Rolls back in-memory state if an operation or save fails."""
        snapshot = (
            [dict(g) for g in self._groups],
            dict(self._source_assignments),
            set(self._disabled_sources),
            set(self._disabled_groups),
        )
        try:
            yield
        except Exception:
            self._groups = [dict(g) for g in snapshot[0]]
            self._source_assignments = dict(snapshot[1])
            self._disabled_sources = set(snapshot[2])
            self._disabled_groups = set(snapshot[3])
            raise

    def create_draft(self) -> 'SourceGroupStore':
        """Creates an in-memory draft clone that does not write to disk until committed."""
        with self._lock:
            draft = SourceGroupStore.__new__(SourceGroupStore)
            draft.path = self.path
            draft.ini_path = self.ini_path
            draft.db = self.db
            draft._lock = RLock()
            draft._is_draft = True
            draft._groups = [dict(g) for g in self._groups]
            draft._source_assignments = dict(self._source_assignments)
            draft._disabled_sources = set(self._disabled_sources)
            draft._disabled_groups = set(self._disabled_groups)
            return draft

    def commit_draft(self, draft: 'SourceGroupStore'):
        """Atomically commits all changes from an in-memory draft into this store and file."""
        with self._lock:
            draft_groups = [dict(g) for g in draft._groups]
            self._validate_tree_structure(draft_groups)
            draft_assignments = dict(draft._source_assignments)
            draft_disabled_sources = set(draft._disabled_sources)
            draft_disabled_groups = set(draft._disabled_groups)

            with self._transaction():
                self._groups = draft_groups
                self._source_assignments = draft_assignments
                self._disabled_sources = draft_disabled_sources
                self._disabled_groups = draft_disabled_groups
                self.save()

    def load(self):
        with self._lock:
            if not self.path.exists():
                self._init_defaults()
                return

            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"Failed to read source_groups.json: {e}")
                raise RuntimeError(f"Не удалось прочитать файл групп каталогов source_groups.json: {e}") from e

            if not isinstance(data, dict) or data.get("version") != 1:
                raise RuntimeError("Файл source_groups.json имеет неподдерживаемый формат или версию.")

            groups = list(data.get("groups", []))
            self._validate_tree_structure(groups)
            self._groups = groups
            self._source_assignments = {normalized_path(k): v for k, v in data.get("source_assignments", {}).items()}
            self._disabled_sources = {normalized_path(s) for s in data.get("disabled_sources", [])}
            self._disabled_groups = set(data.get("disabled_groups", []))

    def _init_defaults(self):
        """Initializes default groups and migrates from existing DB sources and interface.ini."""
        self._groups = [dict(g) for g in DEFAULT_GROUPS]
        self._source_assignments = {
            "archdaily.com": "grp_arch",
            "behance.net": "grp_arch",
            "archdaily": "grp_arch",
            "behance": "grp_arch",
            "d:/3d collections": "grp_3d",
            "d:/3d models": "grp_3d",
            "e:/references": "grp_arch",
            "e:/work": "grp_work",
        }
        self._disabled_sources = set()
        self._disabled_groups = set()

        # Migrate from interface.ini if available
        ini_sections = {}
        ini_disabled = []
        if self.ini_path and self.ini_path.exists():
            try:
                import configparser
                cp = configparser.ConfigParser()
                cp.read(str(self.ini_path), encoding="utf-8")
                for sec_name in ["Catalogs", "DEFAULT"]:
                    if cp.has_section(sec_name) or sec_name == "DEFAULT":
                        if cp.has_option(sec_name, "assignments"):
                            try:
                                ini_sections.update({normalized_path(k): v for k, v in json.loads(cp.get(sec_name, "assignments")).items()})
                            except Exception:
                                pass
                        if cp.has_option(sec_name, "disabled"):
                            try:
                                ini_disabled.extend([normalized_path(s) for s in json.loads(cp.get(sec_name, "disabled"))])
                            except Exception:
                                pass

                from PyQt6.QtCore import QSettings
                settings = QSettings(str(self.ini_path), QSettings.Format.IniFormat)
                saved_sections = settings.value("Catalogs/assignments") or settings.value("source_sections") or {}
                if isinstance(saved_sections, str):
                    try:
                        saved_sections = json.loads(saved_sections)
                    except Exception:
                        pass
                if isinstance(saved_sections, dict):
                    ini_sections.update({normalized_path(k): v for k, v in saved_sections.items()})

                disabled_list = settings.value("Catalogs/disabled") or settings.value("disabled_sources") or []
                if isinstance(disabled_list, str):
                    try:
                        disabled_list = json.loads(disabled_list)
                    except Exception:
                        pass
                if isinstance(disabled_list, (list, tuple)):
                    ini_disabled.extend([normalized_path(s) for s in disabled_list])
            except Exception as e:
                logger.warning(f"Failed to parse interface.ini: {e}")

        for s in ini_disabled:
            self._disabled_sources.add(s)

        # Connect sources from SQLite if available
        if self.db:
            try:
                with self.db.get_connection() as conn:
                    rows = conn.execute("SELECT domain FROM sources").fetchall()
                    for row in rows:
                        dom = row["domain"]
                        if not dom:
                            continue
                        norm = normalized_path(dom)
                        if norm in ("archdaily.com", "behance.net", "archdaily", "behance"):
                            self._source_assignments[norm] = "grp_arch"
                            continue

                        sec = ini_sections.get(norm)
                        if sec == "models":
                            self._source_assignments[norm] = "grp_3d"
                        elif sec == "references":
                            self._source_assignments[norm] = "grp_arch"
                        else:
                            parts = set(norm.split("/"))
                            if parts & {"3d models", "3d collections", "maxtree", "globe plants"}:
                                self._source_assignments[norm] = "grp_3d"
                            elif parts & {"references", "референсы"}:
                                self._source_assignments[norm] = "grp_arch"
                            else:
                                self._source_assignments[norm] = "grp_work"

                        # Also assign parent folders
                        p_parts = norm.split("/")
                        for i in range(1, len(p_parts)):
                            parent_prefix = "/".join(p_parts[:i])
                            if len(parent_prefix) > 3 and parent_prefix not in self._source_assignments:
                                subparts = set(parent_prefix.split("/"))
                                if subparts & {"3d models", "3d collections", "maxtree", "globe plants"}:
                                    self._source_assignments[parent_prefix] = "grp_3d"
                                elif subparts & {"references", "референсы"}:
                                    self._source_assignments[parent_prefix] = "grp_arch"
                                elif subparts & {"work", "проекты", "projects"}:
                                    self._source_assignments[parent_prefix] = "grp_work"
            except Exception as e:
                logger.warning(f"Could not inspect DB sources during group init: {e}")

        try:
            self.save()
        except Exception as e:
            logger.warning(f"Could not save initial source_groups.json: {e}")

    def save(self):
        """Atomically saves groups, assignments, and disabled states."""
        with self._lock:
            self._validate_tree_structure(self._groups)
            if getattr(self, "_is_draft", False):
                # In-memory draft updates state and validates structure without touching disk.
                return

            data = {
                "version": 1,
                "groups": self._groups,
                "source_assignments": self._source_assignments,
                "disabled_sources": sorted(list(self._disabled_sources)),
                "disabled_groups": sorted(list(self._disabled_groups)),
            }
            payload = json.dumps(data, ensure_ascii=False, indent=2)

            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=self.path.parent,
                    prefix=self.path.name + ".",
                    suffix=".tmp",
                    delete=False,
                ) as f:
                    temp_path = Path(f.name)
                    f.write(payload)
                os.replace(temp_path, self.path)
            except Exception as e:
                if temp_path and temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass
                logger.error(f"Failed to save source_groups.json: {e}")
                raise RuntimeError(f"Ошибка сохранения файла групп источников: {e}") from e

    # --- Group Operations ---

    def get_groups(self) -> list[dict]:
        with self._lock:
            return [dict(g) for g in self._groups]

    def get_group(self, group_id: str) -> dict | None:
        with self._lock:
            for g in self._groups:
                if g["id"] == group_id:
                    return dict(g)
            return None

    def create_group(self, name: str, parent_id: str | None = None) -> str:
        name = name.strip()
        if not name:
            raise ValueError("Название группы не может быть пустым.")

        with self._lock:
            with self._transaction():
                if parent_id and not any(g["id"] == parent_id for g in self._groups):
                    raise ValueError(f"Родительская группа {parent_id} не найдена.")

                gid = "grp_" + uuid.uuid4().hex[:12]
                siblings = [g for g in self._groups if g.get("parent_id") == parent_id]
                order = max([g.get("order", 0) for g in siblings] or [-1]) + 1

                self._groups.append({
                    "id": gid,
                    "name": name,
                    "parent_id": parent_id,
                    "order": order,
                })
                self.save()
                return gid

    def rename_group(self, group_id: str, new_name: str):
        new_name = new_name.strip()
        if not new_name:
            raise ValueError("Название группы не может быть пустым.")

        with self._lock:
            with self._transaction():
                for g in self._groups:
                    if g["id"] == group_id:
                        g["name"] = new_name
                        self.save()
                        return
                raise ValueError(f"Группа {group_id} не найдена.")

    def update_group(self, group_id: str, name: str | None = None, parent_id: str | None = None) -> bool:
        with self._lock:
            with self._transaction():
                if name:
                    self.rename_group(group_id, name)
                if parent_id is not None:
                    self.move_group(group_id, parent_id)
                return True

    def _is_descendant(self, parent_id: str, target_id: str) -> bool:
        curr = target_id
        visited = set()
        while curr and curr not in visited:
            visited.add(curr)
            if curr == parent_id:
                return True
            parent = next((g.get("parent_id") for g in self._groups if g["id"] == curr), None)
            curr = parent
        return False

    def can_move_to(self, group_id: str, new_parent_id: str | None) -> bool:
        if new_parent_id == group_id:
            return False
        with self._lock:
            if new_parent_id is not None:
                if not any(g["id"] == new_parent_id for g in self._groups):
                    return False
                if self._is_descendant(group_id, new_parent_id):
                    return False
            return True

    def move_group(self, group_id: str, new_parent_id: str | None) -> bool:
        with self._lock:
            with self._transaction():
                if not self.can_move_to(group_id, new_parent_id):
                    return False

                group = next((g for g in self._groups if g["id"] == group_id), None)
                if not group:
                    raise ValueError(f"Группа {group_id} не найдена.")

                group["parent_id"] = new_parent_id
                self.save()
                return True

    def delete_group(self, group_id: str, target_group_id: str | None = None) -> bool:
        with self._lock:
            with self._transaction():
                group = next((g for g in self._groups if g["id"] == group_id), None)
                if not group:
                    raise ValueError(f"Группа {group_id} не найдена.")

                if target_group_id is not None:
                    if target_group_id == group_id:
                        raise ValueError("Целевая группа совпадает с удаляемой.")
                    if not any(g["id"] == target_group_id for g in self._groups):
                        raise ValueError(f"Целевая группа {target_group_id} не найдена.")
                    if self._is_descendant(group_id, target_group_id):
                        raise ValueError(f"Нельзя перенести содержимое в удаляемую группу или её потомка ({target_group_id}).")

                subgroups = [g for g in self._groups if g.get("parent_id") == group_id]
                assigned_sources = [s for s, gid in self._source_assignments.items() if gid == group_id]

                if subgroups or assigned_sources:
                    if target_group_id is None:
                        # Unassign rather than error if target is None
                        for s in assigned_sources:
                            self._source_assignments.pop(s, None)
                        for g in subgroups:
                            g["parent_id"] = None
                    else:
                        for s in assigned_sources:
                            self._source_assignments[s] = target_group_id
                        for g in subgroups:
                            g["parent_id"] = target_group_id

                self._groups = [g for g in self._groups if g["id"] != group_id]
                self._disabled_groups.discard(group_id)
                self.save()
                return True

    # --- Source Operations ---

    def assign_source(self, source_key: str, group_id: str | None, save: bool = False):
        norm = normalized_path(source_key)
        with self._lock:
            with self._transaction():
                if group_id:
                    if not any(g["id"] == group_id for g in self._groups):
                        raise ValueError(f"Группа {group_id} не существует.")
                    self._source_assignments[norm] = group_id
                else:
                    self._source_assignments.pop(norm, None)
                if save:
                    self.save()

    def get_source_group(self, source_key: str) -> str | None:
        norm = normalized_path(source_key)
        with self._lock:
            if norm in self._source_assignments:
                return self._source_assignments[norm]
            # Check parent folder assignments
            parts = norm.split("/")
            for i in range(len(parts) - 1, 0, -1):
                prefix = "/".join(parts[:i])
                if prefix in self._source_assignments:
                    return self._source_assignments[prefix]
            # Check child folder assignments (if child belongs to a group, parent can belong to it too)
            for assigned_path, gid in self._source_assignments.items():
                if assigned_path.startswith(norm + "/"):
                    return gid
            # Heuristic keyword match
            parts_set = set(norm.lower().split("/"))
            if parts_set & {"3d models", "3d collections", "3d_models", "3d_collections", "maxtree", "globe plants", "models"}:
                return "grp_3d"
            if parts_set & {"references", "референсы", "archdaily", "behance"}:
                return "grp_arch"
            if parts_set & {"work", "проекты", "projects", "triple d"}:
                return "grp_work"
            return None

    def get_source_assignments(self) -> dict[str, str]:
        with self._lock:
            return dict(self._source_assignments)

    # --- Disabled States ---

    def is_source_disabled(self, source_key: str) -> bool:
        norm = normalized_path(source_key)
        with self._lock:
            if norm in self._disabled_sources:
                return True
            for disabled in self._disabled_sources:
                if norm.startswith(disabled + "/"):
                    return True
            gid = self.get_source_group(norm)
            if gid and self.is_group_disabled(gid):
                return True
            return False

    def is_group_disabled(self, group_id: str) -> bool:
        with self._lock:
            curr = group_id
            visited = set()
            while curr and curr not in visited:
                visited.add(curr)
                if curr in self._disabled_groups:
                    return True
                curr = next((g.get("parent_id") for g in self._groups if g["id"] == curr), None)
            return False

    def set_source_disabled(self, source_key: str, disabled: bool, save: bool = False):
        norm = normalized_path(source_key)
        with self._lock:
            with self._transaction():
                if disabled:
                    self._disabled_sources.add(norm)
                else:
                    self._disabled_sources.discard(norm)
                    # If a parent is disabled, removing this exact key might not re-enable it unless
                    # parent is also un-disabled. We remove matching children/parent if needed.
                    self._disabled_sources = {
                        s for s in self._disabled_sources if not (norm == s or norm.startswith(s + "/"))
                    }
                if save:
                    self.save()

    def set_group_disabled(self, group_id: str, disabled: bool, save: bool = False):
        with self._lock:
            with self._transaction():
                if disabled:
                    self._disabled_groups.add(group_id)
                else:
                    self._disabled_groups.discard(group_id)
                if save:
                    self.save()

    def get_disabled_sources(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._disabled_sources))

    def get_disabled_groups(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._disabled_groups))

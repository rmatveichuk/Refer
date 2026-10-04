"""Read-only, shared filtering and ranking for the GUI and future MCP adapter."""
from dataclasses import dataclass
import ntpath
import numpy as np
from database.models import Asset


def normalized_path(value):
    path = value.replace("\\", "/").rstrip("/").casefold()
    return {"archdaily": "archdaily.com", "behance": "behance.net"}.get(path, path)


def source_section(value, assignments=None):
    path = normalized_path(value or "")
    for prefix, section in sorted((assignments or {}).items(), key=lambda item: len(item[0]), reverse=True):
        prefix = normalized_path(prefix)
        if path == prefix or path.startswith(prefix + "/"):
            return section
    parts = set(path.split("/"))
    if parts & {"3d models", "3d collections", "maxtree", "globe plants"}:
        return "models"
    if path in {"archdaily", "archdaily.com", "behance", "behance.net", "artstation.com"} or parts & {"references", "референсы"}:
        return "references"
    return "unassigned"


def is_plant_source(value):
    return bool(set(normalized_path(value or "").split("/")) & {"maxtree", "globe plants"})


@dataclass(frozen=True)
class SearchFilters:
    sources: tuple = ()
    excluded_sources: tuple = ()
    section: str = "all"
    tags: tuple = ()
    exclude_tags: tuple = ()
    tag_match: str = "all"
    favorites: bool = False
    top_only: bool = False
    plants_only: bool = False
    project_id: int | None = None
    author: str | None = None
    collection_id: int | None = None


def like_prefix(path):
    # Folder names containing '%' or '_' are literal, never SQL wildcards.
    return path.replace("\\", "/").rstrip("/").replace("!", "!!").replace("%", "!%").replace("_", "!_") + "/%"


def transliterate_ru(text: str) -> str:
    mapping = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
        'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
        'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
        'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
        'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'
    }
    return "".join(mapping.get(c, c) for c in text.lower())


class SearchRepository:
    def __init__(self, db, faiss_manager=None, assignments=None, visibility_store=None):
        self.db = db
        self.faiss = faiss_manager
        self.assignments = assignments or {}
        from database.visibility_store import VisibilityStore
        import config
        self.visibility = visibility_store or VisibilityStore(config.APP_ROAMING_DIR / "hidden_assets.json")

    def candidates(self, filters):
        if not filters.sources:
            return []
        conditions, params, sources = [], [], []
        for source in dict.fromkeys(filters.sources):
            if source in {"archdaily", "behance", "archdaily.com", "behance.net"}:
                sources.append("s.domain = ?")
                params.append({"archdaily": "archdaily.com", "behance": "behance.net"}.get(source, source))
            else:
                sources.append("REPLACE(a.local_path, '\\', '/') LIKE ? ESCAPE '!'")
                params.append(like_prefix(source))
        conditions.append("(" + " OR ".join(sources) + ")")
        if filters.project_id is not None:
            conditions.append("a.project_id = ?")
            params.append(filters.project_id)
        if filters.author:
            conditions.append("p.author = ?")
            params.append(filters.author)
        if filters.collection_id is not None:
            conditions.append("EXISTS (SELECT 1 FROM collection_assets ca WHERE ca.asset_id = a.id AND ca.collection_id = ?)")
            params.append(filters.collection_id)
        if filters.favorites:
            conditions.append("a.is_favorite = 1")
        if filters.top_only:
            conditions.append("EXISTS (SELECT 1 FROM asset_tags at JOIN tags t ON t.id=at.tag_id WHERE at.asset_id=a.id AND t.name IN ('топ','top'))")
        tags = tuple(dict.fromkeys(filters.tags))
        if tags:
            placeholders = ",".join("?" for _ in tags)
            if filters.tag_match == "any":
                conditions.append(f"EXISTS (SELECT 1 FROM asset_tags at JOIN tags t ON t.id=at.tag_id WHERE at.asset_id=a.id AND t.name IN ({placeholders}))")
            else:
                conditions.append(f"(SELECT COUNT(DISTINCT t.name) FROM asset_tags at JOIN tags t ON t.id=at.tag_id WHERE at.asset_id=a.id AND t.name IN ({placeholders})) = ?")
            params.extend(tags)
            if filters.tag_match != "any":
                params.append(len(tags))
        if filters.exclude_tags:
            placeholders = ",".join("?" for _ in filters.exclude_tags)
            conditions.append(f"NOT EXISTS (SELECT 1 FROM asset_tags at JOIN tags t ON t.id=at.tag_id WHERE at.asset_id=a.id AND t.name IN ({placeholders}))")
            params.extend(filters.exclude_tags)
        sql = """SELECT a.*, p.title AS project_title, p.author AS project_author, s.domain AS source_domain
                 FROM assets a LEFT JOIN projects p ON p.id=a.project_id
                 LEFT JOIN sources s ON s.id=a.source_id WHERE """ + " AND ".join(conditions) + " ORDER BY a.created_at DESC, a.id DESC"
        with self.db.get_connection() as conn:
            rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
        hidden = self.visibility.entries()
        ex_set = {normalized_path(e) for e in (filters.excluded_sources or ())}
        ex_prefixes = tuple(e.rstrip("/") + "/" for e in ex_set)
        def allowed(row):
            if self.visibility.is_hidden(row, hidden):
                return False
            # Downloaded web assets may store their thumbnail cache as local_path.
            # Their source domain determines the section, never the cache directory.
            domain = row.get("source_domain") or ""
            path = domain if domain in {"archdaily.com", "behance.net", "artstation.com"} else row.get("local_path") or domain
            normalized = normalized_path(path)
            if normalized in ex_set or (ex_prefixes and normalized.startswith(ex_prefixes)):
                return False
            section = source_section(path, self.assignments)
            return (filters.section == "all" or section == filters.section) and (not filters.plants_only or is_plant_source(path))
        return [row for row in rows if allowed(row)]

    def search(self, filters, text="", vector=None, limit=400, metadata_only=False):
        rows = self.candidates(filters)
        by_id = {row["id"]: row for row in rows}
        query = text.strip().casefold()
        words = query.split()
        latin_query = transliterate_ru(query)
        latin_words = latin_query.split()

        def matches(row):
            if not query:
                return False
            combined = f"{row.get('project_title') or ''} {row.get('project_author') or ''} {ntpath.basename(row.get('local_path') or '')} {row.get('description') or ''}".casefold()
            if query in combined or latin_query in combined:
                return True
            if words and all(w in combined for w in words):
                return True
            if latin_words and all(w in combined for w in latin_words):
                return True
            return False

        matched = [row["id"] for row in rows if matches(row)]
        if metadata_only:
            result_ids = matched if query else list(by_id)
            total = len(result_ids)
        elif vector is None:
            result_ids = list(by_id)
            total = len(result_ids)
        elif not by_id:
            result_ids, total = [], 0
        else:
            distances, ids = self.faiss.search(vector, k=min(limit, len(rows)), valid_ids=list(by_id))
            ranked = [int(aid) for aid, distance in zip(ids, distances) if int(aid) in by_id and np.isfinite(distance)]
            # Literal metadata matches are an explicit priority, never fabricated distances.
            result_ids = list(dict.fromkeys(matched + ranked))
            total = None  # Top-K is not a count of all semantically relevant images.
        assets = []
        for aid in result_ids[:limit]:
            row = by_id[aid]
            fields = {key: row[key] for key in Asset.__dataclass_fields__ if key in row}
            assets.append(Asset(**fields))
        return assets, total

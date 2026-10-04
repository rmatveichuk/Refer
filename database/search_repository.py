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
    studios: tuple = ()
    exclude_project_ids: tuple = ()
    warmth_min: float | None = None
    warmth_max: float | None = None
    contrast_max: float | None = None
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
        if filters.studios:
            placeholders = ",".join("?" for _ in filters.studios)
            conditions.append(f"LOWER(p.author) IN ({placeholders})")
            params.extend(s.strip().casefold() for s in filters.studios)
        elif filters.author:
            conditions.append("LOWER(p.author) = ?")
            params.append(filters.author.strip().casefold())
        if filters.exclude_project_ids:
            placeholders = ",".join("?" for _ in filters.exclude_project_ids)
            conditions.append(f"a.project_id NOT IN ({placeholders})")
            params.extend(filters.exclude_project_ids)
        if filters.warmth_min is not None:
            conditions.append("EXISTS (SELECT 1 FROM asset_features af WHERE af.asset_id = a.id AND af.warmth_palette >= ?)")
            params.append(float(filters.warmth_min))
        if filters.warmth_max is not None:
            conditions.append("EXISTS (SELECT 1 FROM asset_features af WHERE af.asset_id = a.id AND af.warmth_palette <= ?)")
            params.append(float(filters.warmth_max))
        if filters.contrast_max is not None:
            conditions.append("EXISTS (SELECT 1 FROM asset_features af WHERE af.asset_id = a.id AND af.global_contrast <= ?)")
            params.append(float(filters.contrast_max))
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

        with self.db.get_connection() as conn:
            has_features = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='asset_features'").fetchone() is not None
            if has_features:
                feature_cols = ", af.warmth_palette, af.global_contrast, af.palette_json"
                feature_join = " LEFT JOIN asset_features af ON af.asset_id=a.id"
            else:
                feature_cols = ", NULL AS warmth_palette, NULL AS global_contrast, NULL AS palette_json"
                feature_join = ""

            sql = f"""SELECT a.*, p.title AS project_title, p.author AS project_author, s.domain AS source_domain{feature_cols}
                     FROM assets a LEFT JOIN projects p ON p.id=a.project_id
                     LEFT JOIN sources s ON s.id=a.source_id{feature_join}
                     WHERE """ + " AND ".join(conditions) + " ORDER BY a.created_at DESC, a.id DESC"
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

    def search_candidates(self, filters, text="", vector=None, limit=30, metadata_only=False, max_per_project=1):
        rows = self.candidates(filters)
        if not rows:
            return []
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
        scores_by_id = {}

        if metadata_only:
            result_ids = matched if query else list(by_id)
        elif vector is None:
            if not query:
                result_ids = list(by_id)
            else:
                result_ids = matched
        else:
            fetch_k = min(len(rows), max(limit * 20, 500))
            distances, ids = self.faiss.search(vector, k=fetch_k, valid_ids=list(by_id))
            scores_by_id = {int(aid): float(dist) for aid, dist in zip(ids, distances) if int(aid) in by_id and np.isfinite(dist)}
            ranked = [int(aid) for aid in scores_by_id]
            # Literal metadata matches are an explicit priority
            result_ids = list(dict.fromkeys(matched + ranked))

        results = []
        project_counts = {}
        for aid in result_ids:
            row = by_id[aid]
            pid = row.get("project_id") or 0
            if max_per_project is not None and pid > 0:
                if project_counts.get(pid, 0) >= max_per_project:
                    continue
                project_counts[pid] = project_counts.get(pid, 0) + 1

            results.append({
                "asset_id": row["id"],
                "project_id": row.get("project_id"),
                "project": row.get("project_title") or "Untitled Project",
                "architect": row.get("project_author") or "Unknown Architect",
                "source_domain": row.get("source_domain") or "",
                "thumbnail_path": row.get("thumbnail_path"),
                "image_path": row.get("local_path") or row.get("thumbnail_path"),
                "score_l2": scores_by_id.get(row["id"]),
                "warmth_palette": row.get("warmth_palette"),
                "global_contrast": row.get("global_contrast")
            })
            if len(results) >= limit:
                break

        return results

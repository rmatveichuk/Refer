from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime

@dataclass
class Tag:
    id: Optional[int] = None
    name: str = ""

@dataclass
class Source:
    id: Optional[int] = None
    url: str = ""
    domain: str = ""

@dataclass
class Project:
    id: Optional[int] = None
    title: str = ""
    url: str = ""
    author: str = ""
    location: str = ""

@dataclass
class Asset:
    id: Optional[int] = None
    original_url: str = ""
    local_path: str = ""
    thumbnail_path: str = ""
    phash: str = ""
    width: int = 0
    height: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    source_id: Optional[int] = None
    project_id: Optional[int] = None
    embedding_id: Optional[int] = None
    category: str = "3d_render"  # "3d_render" или "photography"
    image_type: str = "Photography"
    is_favorite: bool = False  # Избранное
    tags: List[Tag] = field(default_factory=list)

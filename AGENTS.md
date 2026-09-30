# Refer — Руководство для AI-Агентов (Agent Architecture & Workflow Guide) 🤖🏛️

Данный документ предназначен для AI-агентов (ChatGPT, Claude, Cursor, Antigravity, Windsurf и др.), работающих с проектом **Refer**. 
Он объясняет структуру базы данных, устройство эталонного каталога референсов, как использовать референсы для генерации/анализа стилей и как собирать новые подборки.

---

## 1. Обзор архитектуры и эталонной базы (Benchmark Catalog)

Refer содержит эталонную кураторскую подборку мирового уровня:
* **Объем подборки:** **1 029** чистых архитектурных фотографий высокого качества.
* **Качество данных:** **0 чертежей, схем, планов этажей и диаграмм**. Только реализованная архитектура и интерьеры.
* **Временной срез:** Строго завершенные проекты **2021–2026 годов** (138 проектов).
* **География и бюро:** **55 ведущих мировых бюро** (Pritzker-лауреаты и лидеры частной архитектуры: *David Chipperfield, SAOTA, Studio Arthur Casas, Sordo Madaleno, Pezo von Ellrichshausen, Luigi Rosselli, Edition Office, Alberto Campo Baeza, Fala Atelier, Fran Silvestre, Olson Kundig, K-Studio, Block722, HARQUITECTES, Bernardo Bader, Pedevilla* и др.).
* **Векторная база:** Все 1 029 фото (и вся остальная библиотека из 70 000+ ассетов) векторизованы моделью **Google SigLIP 2** (`google/siglip2-so400m-patch14-384`, 1152-dim) и загружены в FAISS-индекс.
* **Метка в базе:** Все эталонные ассеты помечены тегом **`топ`** (и **`top`**).

---

## 2. Физическое расположение данных на диске

Все пути определяются в [config.py](config.py):

| Сущность | Физический путь | Назначение |
| :--- | :--- | :--- |
| **База данных SQLite** | `%AppData%\Refer\Database\collection.db` | Хранит таблицы `assets`, `projects`, `tags`, `asset_tags`, `sources` |
| **Векторный индекс FAISS** | `%AppData%\Refer\Database\collection.index` | 1152-мерные векторы SigLIP 2 для мгновенного поиска |
| **Кеш миниатюр (WebP)** | `%LocalAppData%\Refer\Thumbnails\<hash>.webp` | Оптимизированные изображения 1024px без потерь качества |
| **Веса модели SigLIP 2** | `%LocalAppData%\Refer\Models\...` | Локальные веса трансформера |
| **Реестр топ-студий** | `registry/studios.json` | 150 проверенных мировых бюро и студий ArchViz |

> [!IMPORTANT]
> Никогда не создавайте сторонние временные папки для скачивания на рабочих дисках пользователя (`C:`, `E:` и т.д.). Все ассеты Refer хранятся исключительно в структурированном кеше `%LocalAppData%\Refer\Thumbnails` и связываются через `collection.db`.

---

## 3. Как агенту использовать референсы для работы

Когда пользователю требуется подобрать референс, изучить архитектурный стиль, создать мудборд или сгенерировать 3D-сцену / промпт для Midjourney / Stable Diffusion:

### Сценарий А. Выборка референсов по тегам и материалам (SQLite)
Агент может быстро получить пути к реальным файлам на диске:

```python
import sqlite3
import os

db_path = os.path.expandvars(r"%APPDATA%\Refer\Database\collection.db")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Пример: Найти 5 лучших референсов с бетоном от топовых бюро
cur.execute("""
    SELECT a.id, a.thumbnail_path, p.title as project, p.author as architect
    FROM assets a
    JOIN projects p ON a.project_id = p.id
    JOIN asset_tags at ON a.id = at.asset_id
    JOIN tags t ON at.tag_id = t.id
    WHERE t.name = 'топ'
    AND a.id IN (
        SELECT at2.asset_id FROM asset_tags at2 
        JOIN tags t2 ON at2.tag_id = t2.id 
        WHERE t2.name = 'concrete'
    )
    LIMIT 5
""")

for row in cur.fetchall():
    print(f"ID: {row['id']} | Бюро: {row['architect']} | Проект: {row['project']}")
    print(f"Путь к картинке: {row['thumbnail_path']}")
```

### Сценарий Б. Семантический поиск по смыслу через SigLIP 2 (FAISS)
Если запрос сложный и художественный (например, *"минималистичная гостиная с теплыми деревянными рейками и мягким рассеянным закатным светом"*):

```python
import os
import numpy as np
from ai.engine import AiEngine
from database.faiss_manager import FaissManager
import config

# 1. Получаем 1152-мерный вектор текста через SigLIP 2
engine = AiEngine()
query_vector = engine.get_text_embedding("minimalist living room warm timber slats soft sunset light")

# 2. Ищем по базе векторов
faiss_mgr = FaissManager(config.FAISS_PATH, dimension=config.VECTOR_DIMENSION)
asset_ids, scores = faiss_mgr.search(query_vector, top_k=20)

# 3. Фильтруем результаты, чтобы оставить только эталонный ТОП
# (проверяем наличие тега 'топ' у полученных asset_ids в collection.db)
```

### Сценарий В. Подача картинки в Vision (VLM)
Любой найденный файл `row['thumbnail_path']` является стандартным WebP/JPEG файлом в разрешении 1024px. Агент может открыть его через PIL (`Image.open(path)`) или прочитать байты и передать в мультимодальную модель для детального анализа:
* Светотеневой рисунок и композиция кадра;
* Палитра и узлы стыковки материалов (дерево + бетон, камень + штукатурка);
* Формирование точного промпта для генерации аналогичной визуализации.

---

## 4. Как агенту собрать НОВЫЙ ТОП или расширить текущий

Если требуется собрать новые референсы, в проекте настроен полностью автономный модульный пайплайн в папке `scrapers/`.

### Архитектура скрапера:
1. **[registry/studios.json](registry/studios.json)** — официальный реестр студий (100 архитектурных бюро + 50 ArchViz студий с проверенными прямыми `archdaily_url` и фильтрами стилей).
2. **[scrapers/plan_filter.py](scrapers/plan_filter.py)** — двухкаскадный фильтр отсева чертежей:
   * **Tier 0:** Мгновенный regex-анализ URL и alt-текста (`plan`, `section`, `elevation`, `axonometry`, `diagram`, `scheme`).
   * **Tier 1:** Бинарный Range-GET анализ первых 64 КБ заголовка изображения (проверка пропорций, соотношения сторон и формата).
3. **[scrapers/cdn_resolver.py](scrapers/cdn_resolver.py)** — нормализует CDN ArchDaily на мастер-разрешение `large_jpg` (2000px).
4. **[scrapers/archdaily_parser.py](scrapers/archdaily_parser.py)** — извлекает метаданные `cXenseParse` (год постройки, офис, фотографы, материалы, категории), применяет фильтр дат (2021–2026).
5. **[scrapers/studio_collector.py](scrapers/studio_collector.py)** — главный CLI-оркестратор.

### Команды для сбора:

#### 1. Проверить список доступных студий:
```bash
python -m scrapers.studio_collector --list
```

#### 2. Собрать конкретное бюро по ID или названию:
```bash
python -m scrapers.studio_collector --studio "kengo-kuma" --max-images-per-project 8
```

#### 3. Собрать список конкретных студий через запятую:
```bash
python -m scrapers.studio_collector --studios "tadao-ando,snohetta,big-bjarke-ingels" --max-images-per-project 8
```

#### 4. Пакетная сборка со смещением (offset) и ограничением объема:
```bash
# Например, собрать следующую пачку из 20 студий, начиная с 50-й, до общего лимита в 1500 топ-картинок:
python -m scrapers.studio_collector --batch 20 --offset 50 --max-images-per-project 8 --target-total 1500
```

#### 5. Проверить статистику базы по авторам и проектам:
```bash
python -m scrapers.studio_collector --stats
```

---

## 5. Векторизация и целостность базы данных

* Скрипт `studio_collector.py` автоматически вызывает метод `embed_unindexed_assets()` после каждого успешно обработанного бюро.
* Новые картинки сразу векторизуются моделью **SigLIP 2** и сохраняются в `collection.index`.
* Если по какой-то причине в базе появились ассеты без векторов, агент может в любой момент доиндексировать их одной командой:
```bash
python -c "from scrapers.studio_collector import StudioCollector; StudioCollector().embed_unindexed_assets()"
```

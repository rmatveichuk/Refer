# Спецификация и План реализации: Refer MCP & Vision Curator (v1.1)

> **Статус документа:** Согласованный инженерный план (с учетом ревью Sol 6.1 по кодовой базе)  
> **Целевая аудитория:** AI-агенты (Sol 6.1, Antigravity, Claude, Cursor) и разработчики проекта Refer  
> **Ключевой принцип:** 100% совместимость с существующими классами (`CollectionRepository`, `MoodboardExporter`), Windows OpenMP обходами и схемой SQLite.

---

## 1. Архитектурный контекст и привязка к кодовой базе

1. **База данных (`%AppData%\Refer\Database\collection.db`):**
   * Сущности мудбордов: таблицы **`collections`** и **`collection_assets`** (НЕ `moodboards`!).
   * Работа с мудбордами: строго через существующий **[`CollectionRepository`](file:///c:/Users/RMatv/OneDrive/Рабочий%20стол/Dev/Refer/database/collection_repository.py)**.
2. **Векторный поиск (`%AppData%\Refer\Database\collection.index`):**
   * Текущий индекс: **`IndexIDMap(IndexFlatL2)`**.
   * **Важно (Windows):** Поиск выполняется через существующий обход на **NumPy** из-за конфликта рантаймов OpenMP. Сохраняем эту логику!
   * **Семантика Score:** По индексу L2 меньшее расстояние означает большее сходство ($0.0$ — идентично). В MCP выдаем либо нормализованный score, либо честный L2 distance с явным указанием типа метрики.
3. **Модель и VRAM (RTX 4060 8 ГБ):**
   * **Защита от дублирования:** Запрещено запускать вторую копию `AiProcessClient` внутри MCP-сервера (это удвоит расход VRAM с 2 ГБ до 4+ ГБ).
   * **Решение:** MCP-сервер переиспользует существующий запущенный инференс-процесс или подключается к общему воркеру.
4. **Кеш и оригиналы:**
   * Кеш: `%LocalAppData%\Refer\Thumbnails\<hash>.webp` (1024px / 2000px).
   * Инструмент `inspect` честно сообщает: использован ли локальный мастер-файл оригинала или превью из кеша, с указанием реальных размеров в пикселях.
5. **Экспорт:**
   * Расширение существующего **[`MoodboardExporter`](file:///c:/Users/RMatv/OneDrive/Рабочий%20стол/Dev/Refer/export/moodboard_exporter.py)**, чтобы GUI приложения и MCP использовали единый движок выгрузки.
6. **Синхронизация GUI с внешними записями:**
   * Поскольку запись из MCP происходит в другом процессе, GUI приложения Refer должен отслеживать внешние изменения через `PRAGMA data_version` на таймере (раз в 1-2 сек) и обновлять боковую панель мудбордов.

---

## 2. Спецификация 7 инструментов MCP-сервера (`refer-mcp`)

```
┌────────────────────────────────────────────────────────────────────────┐
│                          REFER MCP SERVER                              │
├────────────────────────────────────────────────────────────────────────┤
│  1. refer.taxonomy  ──► Интроспекция (студии, материалы, теги)         │
│  2. refer.search    ──► Поиск (текст/L2, слоты, фильтры, разнообразие) │
│  3. refer.preview   ──► Контакт-лист 3х3 (In-Memory RAM для Vision)    │
│  4. refer.inspect   ──► Кроп узла + метаданные + честный статус файла  │
│  5. refer.project   ──► Полный контекст объекта и все ракурсы          │
│  6. refer.board     ──► Слотовая курация (через CollectionRepository)  │
│  7. refer.export    ──► Выгрузка (через расширенный MoodboardExporter) │
└────────────────────────────────────────────────────────────────────────┘
```

### 1. `refer.taxonomy`
* **Вход:** `kind` (`"overview"`, `"studios"`, `"materials"`, `"tags"`), `query` (optional), `limit` (default 50).
* **Выход:** Список значений с реальными счетчиками `project_count` и `asset_count` из SQLite.

### 2. `refer.search`
* **Вход:**
  * `query` (optional str): текстовый запрос.
  * `mode` (enum: `"semantic"` | `"metadata"` | `"hybrid"`).
  * `image_asset_id` (optional int): поиск по визуальному сходству.
  * `collection_id` (optional int): ограничить поиск внутри существующей коллекции.
  * `filters` (dict): источники (`sources`), студии (`studios`), теги (`tags`), диапазон теплоты/контраста.
  * `slot_hint` (optional str): `"overview"` | `"interior"` | `"detail"` | `"landscape"` (используется как семантический модификатор промпта, проверяется агентом через зрение).
  * `max_per_project` (int, default 1 для разнообразия, или `null` / `N` при исследовании одного объекта).
  * `limit` (int, default 30).
* **Выход:** Список `{asset_id, project_id, architect, score_l2, title}` без картинок.

### 3. `refer.preview`
* **Вход:** `asset_ids` (list[int], 1..9 штук).
* **Выход:** 
  * JPEG/PNG изображение 1536×1536, собранное **строго в RAM (BytesIO)** с оверлеем номеров ячеек (1..9). На диск не пишется.
  * Структурированный JSON-манифест ячеек (`cell_1` → `asset_id`).

### 4. `refer.inspect`
* **Вход:** `asset_id` (int), `bbox_norm` (optional `[x0, y0, x1, y1]`).
* **Выход:**
  * Кроп в нативном разрешении доступного файла.
  * Метаданные: `file_used_path`, `actual_dimensions: [w, h]`, `is_cache_fallback: bool`, `palette_hex`, `warmth_palette`.

### 5. `refer.project`
* **Вход:** `project_id` (int).
* **Выход:** Все ассеты проекта (фасады, интерьеры, чертежи), авторы, год постройки, ссылки.

### 6. `refer.board`
* **Вход:**
  * `action` (`"create"`, `"add"`, `"remove"`, `"list"`, `"get"`, `"validate"`).
  * `collection_id` (optional int).
  * `name` (optional str).
  * `asset_id` (optional int).
  * `slot_name` (optional str: `"overview"`, `"interior"`, `"detail"`).
  * `max_per_project` (optional int — проверка на превышение квоты одного здания).
  * `target_count` (optional int — для проверки комплектности слотов).
* **Реализация:** Вызывает методы `CollectionRepository`. Записи мгновенно сохраняются в `collections` и `collection_assets`.

### 7. `refer.export`
* **Вход:**
  * `collection_id` (int).
  * `destination_folder` (str).
  * `format` (`"images_only"`, `"html_presentation"`, `"full_package"`).
* **Реализация:** Делегирует задачу в `MoodboardExporter` (расширенный поддержкой автономного HTML-мудборда и JSON-манифеста).

---

## 3. Таблица фотометрических признаков (`asset_features`)

Вместо усложнения базовой таблицы `assets`, создается отдельная версионируемая таблица характеристик:

```sql
CREATE TABLE IF NOT EXISTS asset_features (
    asset_id INTEGER PRIMARY KEY,
    feature_version INTEGER NOT NULL DEFAULT 1,
    warmth_palette REAL,          -- 0.0 (холодный) .. 1.0 (теплый), NULL если не рассчитано
    global_contrast REAL,         -- std(L* / 100.0) по шкале CIE L*
    lstar_mean REAL,              -- 0.0 .. 100.0 (средняя светлота)
    palette_json TEXT,            -- Top-5 LAB + HEX + веса
    status TEXT NOT NULL,         -- 'completed', 'missing_file', 'error'
    calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
);
```

### Принципы расчета:
* **Не занулять отсутствующие данные:** если файл поврежден или отсутствует — пишется `NULL` и `status='missing_file'`.
* **OpenCV LABuint8 нюанс:** При конвертации через OpenCV `cv2.cvtColor(img, cv2.COLOR_BGR2LAB)` учитывать, что $L \in [0, 255], a \in [0, 255], b \in [0, 255]$. Формулы нормализуются к стандартному CIE LAB ($L^* \in [0, 100], a^*, b^* \in [-128, 127]$).
* **Калибровка:** Формулы используются как мягкий rerank / подсказка, жесткие фильтры вводятся только после визуальной валидации выборки.

---

## 4. Прагматичный порядок реализации (Рекомендация Sol 6.1)

### Шаг 1: Базовый контур поиска и зрения (Core Loop) — ✅ ВЫПОЛНЕНО
1. Модуль `mcp_server/` с инструментами `refer.taxonomy`, `refer.search`, `refer.preview`, `refer.inspect`, `refer.project` реализован.
2. Поиск интегрирован через `SearchRepository`, `IndexIDMap(IndexFlatL2)` с сохранением NumPy-обхода на Windows.
3. Генерация контакт-листов в RAM и кропов с палитрой CIE LAB проверена сквозными тестами.
4. Создан общий сервис инференса `ai.shared_service` (TCP loopback) для предотвращения дублирования модели и защиты VRAM.

### Шаг 2: Мудборды и связь с GUI — ✅ ВЫПОЛНЕНО
1. Инструмент `refer.board` подключен к `CollectionRepository` с поддержкой назначения и валидации слотов (`slot_name`).
2. В Refer GUI внедрен постоянный observer-коннект с проверкой `PRAGMA data_version;` для автоматического обновления боковой панели и галереи мудбордов при внешних изменениях.
3. Добавлен параметр `max_per_project` для контроля архитектурного разнообразия.

### Шаг 3: Экспорт — ✅ ВЫПОЛНЕНО
1. `MoodboardExporter` расширен поддержкой пакетного экспорта (изображения + `manifest.json` + оффлайн/веб презентации).
2. Подключен инструмент `refer.export` с автоматическим выбором корректного режима связывания/копирования (`mode='copy'`).

### Шаг 4: Фотометрия (Фактический статус) — 🟡 ЧАСТИЧНО / НА ЛЕТУ
1. Таблица `asset_features` и схема хранения палитр CIE LAB созданы и поддерживаются.
2. Расчёт характеристик кропа и кадра в нативном разрешении выполняется «на лету» в `refer.inspect`.
3. Фоновый оффлайн-демон для ночного перерасчета всех 70 000 кадров вынесен в v1.2 (детальное обоснование в [MCP_ACCEPTANCE_REPORT.md](research/MCP_ACCEPTANCE_REPORT.md)).

---

## 5. Результаты верификации

Подробный протокольный отчёт по 136 изолированным тестам и сквозной проверке через официальный MCP SDK сохранён в **[`docs/research/MCP_ACCEPTANCE_REPORT.md`](research/MCP_ACCEPTANCE_REPORT.md)**.


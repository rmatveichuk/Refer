# Спецификация конвейера сбора данных: Refer Ingestion Pipeline 1.0
*Синтез инженерных решений (Опус, Астра, Грок) для автоматического сбора эталонных работ*

---

## 1. Архитектурная философия: Каскад по стоимости

Главный принцип эффективного сбора — **каскадный отсев**:
Каждый последующий шаг стоит дороже предыдущего по времени, сети и вычислениям. Отсекаем мусор как можно раньше:

```
[URL / Текстовый контекст]  (~0 ms, 0 байт)     -> Отсекает 60% чертежей
            ↓
[HTTP Range-GET заголовка]  (32-64 КБ)          -> Отсекает превью и неверный AR
            ↓
[Анализ миниатюры на CPU]   (OpenCV/NumPy)      -> Отсекает схемы и аксонометрии
            ↓
[Zero-shot SigLIP 2]        (Локальный GPU/CPU) -> Семантический фильтр (Photo vs Plan)
            ↓
[Решение CDN Master]        (CDN URL Resolver)  -> Извлечение полного исходника
            ↓
[MMR Отбор «Золотой 5-ки»]  (SigLIP 2 косинус)  -> 5-8 разнообразных кадров
            ↓
[VLM-валидация качества]    (Только финалисты)  -> Исключение дефектов рендера
```

---

## 2. Блок 1. Моментальный отсев планов и чертежей

### Уровень 0: Регулярные выражения в краулере (0 байт)
Проверяется URL, имя файла, `alt`, `figcaption` и контекст ссылки:

```python
PLAN_PATTERN = r'(?i)\b(floor[-_ ]?plan|plan[-_]?\d|grundriss|план|section[-_]?\d|schnitt|elevation|разрез|фасад-чертеж|\baxo\b|axonometric|isometric|diagram|blueprint|site[-_]?plan|master[-_]?plan|dwg|cad)\b'
RENDER_OVERRIDE = r'(?i)(photo|render|hero|exterior|west-elevation-at-dusk)'
```
*Правило:* Если путь матчит `PLAN_PATTERN` и не содержит `RENDER_OVERRIDE` — файл отбрасывается без скачивания.

### Уровень 1: Проверка заголовка (Range-GET 32–64 КБ)
Через `PIL.Image.open()` читается заголовок (SOF-маркер):
* Отсекаются изображения с экстремальным Aspect Ratio ($> 2.2$ или $< 0.55$).
* Отсекаются крошечные превью ($< 1200$ px по длинной стороне) и иконки.

### Уровень 2: Анализ миниатюры (OpenCV на 320–512 px)
Логистический скор комбинации признаков:
* **`line_length_ratio` (Canny + Hough):** Доля пикселей в длинных прямолинейных отрезках $> 0.5$ (у фотографий $< 0.15$).
* **Цветовое разнообразие:** Уникальных цветов (после квантования 5 бит/канал) $< 5\%$.
* **Доля фонового цвета (белая бумага):** $> 50\%$ пикселей с яркостью $\ge 245$ при низкой насыщенности.

### Уровень 3: Zero-shot SigLIP 2 (Бесплатный семантический тир)
Пакетное сравнение миниатюр в SigLIP 2 против текстовых промптов:
* `"an architectural photograph or realistic 3D render"` vs `"a floor plan drawing or architectural diagram"`.
* Занимает миллисекунды на GPU, полностью исключает необходимость вызова платных VLM.

---

## 3. Блок 2. Скрапинг нестандартных сайтов студий

### Главное правило: Слушать сеть, а не бороться с DOM
Вместо попыток угадать анимации Webflow, React или GSAP, `browser-act` перехватывает сетевой трафик страницы:
```javascript
page.on('response', response => {
  const ct = response.headers()['content-type'] || '';
  if (ct.startsWith('image/')) {
    saveMediaCandidate(response.url());
  }
});
```

### Принудительная инициализация ленивых элементов
Инжект скрипта перед загрузкой страницы отключает задержки `IntersectionObserver`:
```javascript
window.IntersectionObserver = class {
  constructor(cb) { this.cb = cb; }
  observe(el) { setTimeout(() => this.cb([{ isIntersecting: true, target: el, intersectionRatio: 1 }]), 0); }
  unobserve() {} disconnect() {} takeRecords() { return []; }
};
```
* Маппинг атрибутов: `data-src`, `data-lazy-src`, `data-original` принудительно копируются в `src`.
* Скролл: 3 прохода шагом в 0.7 экрана до стабилизации высоты документа.
* Поиск горизонтальных скролл-контейнеров (`scrollWidth > clientWidth * 1.5`) и программная прокрутка `scrollLeft`.

---

## 4. Блок 3. Выжимание максимального разрешения (CDN Resolver)

Для получения исходных мастер-файлов применяются детерминированные правила трансформации URL:

| CDN / Платформа | Шаблон превью | Правило получения Master |
|---|---|---|
| **ArchDaily CDN** | `images.adsttc.com/.../large_jpg/...` | Замена `large_jpg` / `medium_jpg` $\to$ **`original_jpg`** |
| **Cloudinary** | `res.cloudinary.com/.../image/upload/w_1200,c_fill,q_auto/...` | Удаление сегмента трансформаций (`w_`, `c_`, `q_`, `dpr_`) до `v\d+/` |
| **Imgix** | `domain.imgix.net/image.jpg?w=1200&h=800&fit=crop` | Полное удаление query-параметров (кроме токенов безопасности `s` / `expires`) |
| **Squarespace** | `images.squarespace-cdn.com/.../?format=1500w` | Замена на `?format=original` или `?format=2500w` |
| **Webflow CDN** | `website-files.com/.../image-p-1080.jpg` | Удаление суффикса `-p-\d+` перед расширением файла |
| **Next.js Image** | `/_next/image?url=%2Fimages%2Fhero.jpg&w=1200&q=75` | URL-декодирование параметра `url` для прямого скачивания |

*Защита:* Если примененное правило отдает HTTP 404 или HTML вместо картинки — фолбэк на максимальный вариант из `srcset`.

---

## 5. Блок 4. Определение даты проекта (Фильтр $\le 5$ лет)

### Критическое правило: Игнорировать копирайт в футере
Копирайт «© 2026 Studio» относится к сайту, а не к проекту. Блок `footer` жестко исключается из поиска.

### Приоритет источников года:
1. **Спецификация страницы:** Табличные пары `dt/dd` с ключами `Year`, `Completed`, `Completion Year`, `Built`.
2. **JSON-LD Schema.org:** Поля `dateCreated`, `datePublished` (для 3D-студий дата публикации статьи обычно равна дате создания рендера).
3. **Regex в первом абзаце описания:** `\b(202[1-6])\b` с привязкой к словам `completed in`, `built in`, `project year`.
4. **Wayback Machine CDX API (Фолбэк):**
   ```
   web.archive.org/cdx/search/cdx?url=<url>&output=json&limit=1
   ```
   Дата первого снапшота до 2021 года гарантирует, что проект старый (`too_old`).

*Статус неопределенности:* Если год не найден — проекту присваивается статус `needs_date` (карантин, без скачивания тяжелых файлов).

---

## 6. Блок 5. Распутывание авторства: Проект vs Кадр

Авторство разделяется на уровне проекта и конкретного кадра:

```json
{
  "project_id": "k-studio-dexamenes",
  "architects": ["K-Studio"],
  "visualizers": [],
  "photographers": ["Claus Brechenmacher", "Reiner Baumann"],
  "images": [
    {
      "id": "img_01",
      "medium": "photo",
      "credit": "Claus Brechenmacher",
      "role": "hero_exterior"
    }
  ]
}
```

### Правила атрибуции:
* **На сайте архитектурного бюро:** Студия по умолчанию является `architect`. Если в подписи к фото указано `Visualization: The Boundary` $\to$ кадр помечается `medium = render, visualizers = ["The Boundary"]`.
* **На сайте ArchViz-студии:** Студия по умолчанию является `visualizer`. Поле `architect` извлекается из строк `Architect:`, `Client:`, `Designed by:`.
* **На ArchDaily:** Кредиты парсятся из блока `Project specifications`: `Architects:` $\to$ архитекторы, `Photographs:` $\to$ фотографы, `Visualization:` $\to$ визуализаторы.

---

## 7. Блок 6. Алгоритм отбора «Золотой пятёрки» (MMR Selection)

Вместо скачивания всех 40 картинок из проекта отбирается разнообразный сет из 5–8 ключевых сюжетов через метод **Maximal Marginal Relevance (MMR)**:

### Целевые слоты набора:
1. **Hero (Главный фасад):** Общий объем здания с четким опиранием на землю/рельеф.
2. **Second Angle (Второй ракурс):** Противоположный угол или выразительная входная группа.
3. **Dusk / Night (Вечерний свет):** Тёмно-синее небо и теплое свечение из окон (2700K).
4. **Material Close-up (Фактура):** Камень, бетон, дерево или рейки крупным планом с сохранением архитектурного контекста.
5. **Outdoor Living (Терраса / Вода):** Бассейн, патио, пергола, зона отдыха на улице.
6. **Landscape Context (Дальний план):** Интеграция здания в окружающий пейзаж (скалы, горы, сосны).

### Механика отбора:
1. Все отфильтрованные превью проекта кодируются через **SigLIP 2**.
2. Рассчитывается матрица косинусных расстояний.
3. Кадры с расстоянием $< 0.08$ объединяются в кластеры (дубликаты ракурсов) — выбирается только один кадр с максимальным разрешением.
4. Жадным алгоритмом (Greedy MMR) последовательно выбираются кадры, максимизирующие покрытие ролей и новизну относительно уже выбранных.

---

## 8. Итоговая структура таблицы в SQLite

```sql
CREATE TABLE IF NOT EXISTS asset_intake (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    studio_id TEXT,
    source_domain TEXT,
    project_url TEXT,
    original_url TEXT,
    master_url TEXT,
    cdn_provider TEXT,
    is_true_master BOOLEAN DEFAULT 0,
    width INTEGER,
    height INTEGER,
    medium TEXT, -- 'photo', 'render', 'drawing_rejected'
    role TEXT,   -- 'hero', 'second_angle', 'dusk', 'material', 'terrace', 'context'
    architects JSON,
    visualizers JSON,
    photographers JSON,
    project_year INTEGER,
    date_source TEXT,
    phash TEXT,
    siglip_vector_id INTEGER,
    quality_status TEXT DEFAULT 'candidate', -- 'candidate', 'too_old', 'drawing', 'accepted_hero'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

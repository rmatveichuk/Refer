# Refer Studio Registry & Quality Benchmark

Реестр студий архитектуры и 3D-визуализации для пополнения базы референсов [Refer].

## Уровни источников (Tiers)

- **Tier 1 (Gold Standard / Эталоны):**
  - Мировые лидеры архитектурной визуализации (Mir, The Boundary, Luxigon, Brick Visual, DBOX, Binyan Studios).
  - Ведущие мировые архитектурные бюро с безупречной подачей (Studio MK27, SAOTA, K-Studio, Vincent Van Duysen, Foster + Partners).
  - *Правило:* Контент из этой группы служит золотым эталоном (Ground Truth) качества для калибровки AI-оценки. По умолчанию имеет высший траст.

- **Tier 2 (Curated Professional):**
  - Профессиональные студии и победители профильных наград (Dezeen Awards, ArchDaily BOTY, CGarchitect Nominees, Rookies Pro).
  - Проекты проходят базовую валидацию и VLM-фильтр перед включением в основной индекс.

- **Tier 3 (Community / Raw Ingestion):**
  - Общие потоки из Behance, ArtStation, общих скраперов.
  - Попадают в базу только при прохождении строгого порога качества (Quality Score >= 8.0/10).

## Структура данных студии

Каждая запись студии содержит:
- `id`: уникальный слаг (например, `mir`, `saota`, `the-boundary`)
- `name`: официальное название
- `type`: `archviz` | `architecture` | `hybrid`
- `tier`: `tier_1` | `tier_2` | `tier_3`
- `website`: официальный сайт
- `portfolio_urls`: прямые ссылки на галереи/проекты для скрапера
- `specialties`: специализация (`residential_villas`, `high_rise`, `interior`, `landscape`, `public_spaces`)
- `signature_style`: характерный визуальный почерк (например, `cinematic atmosphere, overcast, raw textures`)
- `awards`: ключевые признания и награды

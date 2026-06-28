# MedArchive — парсер прайс-листов клиник

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.138-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16+-4169E1?logo=postgresql&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-16-000000?logo=next.js&logoColor=white)
![uv](https://img.shields.io/badge/uv-package_manager-DE5FE9?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-22c55e)

**Автоматически извлекает прайс-листы клиник-партнёров из PDF, DOCX, XLS/XLSX (включая отсканированные PDF с OCR), структурирует данные `клиника → услуга → цена` в Postgres и выдаёт их через REST API с дашбордом для оператора.**

---

ссылка на презентацию: [blue_and_White_Minimalist_Digital_Marketing_Presentation,_копия.pdf](https://github.com/user-attachments/files/29436164/blue_and_White_Minimalist_Digital_Marketing_Presentation._.pdf)


## Проблема

Клиники-партнёры присылают прайс-листы в произвольных форматах — текстовые PDF, отсканированные PDF без разметки, DOCX с правками, XLS и XLSX с разными заголовками. Каждый документ оформлен по-своему: разные названия услуг, разные тарифные схемы (резидент/нерезидент), цены в разных валютах. Ручная обработка занимает часы, данные устаревают, опечатки OCR накапливаются в базе.

## Решение

Единый пайплайн инджестинга, который:

- **Читает любой формат** — PDF (текст), отсканированный PDF (OCR), DOCX, XLS, XLSX, ZIP-архивы
- **Восстанавливает таблицы геометрически** — для скан-PDF колонки реконструируются по x/y-координатам слов, без зависимости от табличной разметки
- **Исправляет ошибки OCR** — `12 ООС` → 12 000, `II 000` → 11 000, `80С` → 800
- **Конвертирует валюты** — цены в USD/EUR/RUB автоматически переводятся в KZT по актуальному курсу
- **Сопоставляет с каталогом** — точное, синонимное и нечёткое (rapidfuzz) сопоставление с эталонным каталогом услуг
- **Версионирует** — история цен хранится вечно, новая версия прайса не перезаписывает старую
- **Отправляет на проверку** — строки с подозрительными данными помечаются `needs_review` и ждут оператора, а не отбрасываются

---

## Быстрый старт

**Требования:** Python 3.10+, [`uv`](https://docs.astral.sh/uv/), PostgreSQL

```bash
# 1. Клонируй и подними базу данных
git clone https://github.com/kekeront/medtech2.git && cd medtech2
sudo -u postgres psql -c "CREATE ROLE medarchive LOGIN PASSWORD 'medarchive' CREATEDB; CREATE DATABASE medarchive OWNER medarchive;"

# 2. Установи зависимости, создай схему, запусти API
cp .env.example .env && uv sync && uv run python -m app.cli init
uv run uvicorn app.api:app --reload --port 8000
```

- Встроенный UI: http://localhost:8000
- Swagger / OpenAPI: http://localhost:8000/docs

**Дашборд (Next.js):**

```bash
cd frontend && pnpm install && pnpm dev   # → http://localhost:3000
```

---

## Ключевые возможности

| # | Возможность | Детали |
|---|-------------|--------|
| 1 | **5 форматов** | PDF текст, скан-PDF (OCR), DOCX, XLS, XLSX, ZIP-архивы |
| 2 | **Умный OCR** | EasyOCR / Surya / Tesseract — pluggable; геометрические колонки без разметки |
| 3 | **Исправление OCR** | Детектирование и авто-коррекция цифровых артефактов кириллицы |
| 4 | **FX-конвертация** | USD/EUR/RUB → KZT по ЦБ, fallback на кешированный курс |
| 5 | **Fuzzy-каталог** | Точный → синонимный → нечёткий матчинг с порогом (rapidfuzz) |
| 6 | **Очередь ревью** | Подозрительные строки → `needs_review`, не теряются |
| 7 | **Версионирование** | История цен сохраняется, `is_active` переключается на новую версию |
| 8 | **REST API + Swagger** | 15 эндпоинтов, документация автогенерируется |
| 9 | **Next.js дашборд** | Аналитика, поиск, партнёры, очередь, загрузка файлов |

---

## Архитектура

```
PDF / DOCX / XLS / XLSX / ZIP
         │
         ▼
┌────────────────────┐
│   Format Detect    │  detect.py  →  pdf / ocr_pdf / docx / excel
└────────┬───────────┘
         │
         ▼
┌────────────────────┐
│      Parser        │  геометрические колонки (скан), таблицы (docx),
│                    │  листы с авто-заголовком (excel)
└────────┬───────────┘
         │
         ▼
┌────────────────────┐
│   Tariff Mapper    │  tariffs.py  →  resident / nonresident / FX→KZT
└────────┬───────────┘
         │
         ▼
┌────────────────────┐
│  Catalogue Match   │  normalize.py  →  точный / синоним / fuzzy
└────────┬───────────┘
         │
         ▼
┌────────────────────┐
│    Validation      │  price>0, nonres≥res, delta>50%, невалидная дата
│                    │  → needs_review (не отброс)
└────────┬───────────┘
         │
         ▼
┌────────────────────┐
│     PostgreSQL     │  partners / price_documents / price_items / services
└────────────────────┘
         │
    ┌────┴─────┐
    ▼          ▼
FastAPI    Next.js
REST API   Dashboard
```

Подробнее: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

---

## Стек технологий

| Слой | Технология |
|------|-----------|
| Backend | Python 3.10+, FastAPI 0.138, SQLAlchemy 2.0 |
| База данных | PostgreSQL 16+ |
| OCR | EasyOCR, Surya OCR, Tesseract (pluggable) |
| LLM-парсинг | Groq (Llama 3), Google Gemini (опционально) |
| Нормализация | rapidfuzz |
| Frontend | Next.js 16, Tailwind CSS v4, Recharts, shadcn/ui |
| Package managers | uv (Python), pnpm (Node) |

---

## API-эндпоинты (ТЗ 4.5)

| Метод | Путь | Назначение |
|-------|------|-----------|
| GET | `/services?category=` | Каталог услуг с фильтрацией |
| GET | `/services/{id}/partners` | Кто предоставляет услугу + цены |
| GET | `/partners?city=&is_active=` | Список партнёров |
| GET | `/partners/{id}/services` | Прайс-лист конкретного партнёра |
| GET | `/search?q=` | Поиск по услугам, партнёрам, позициям |
| GET | `/unmatched` | Позиции без привязки к каталогу |
| GET | `/review` | Строки, ожидающие ручной проверки |
| POST | `/match` | Ручная привязка позиций к каталогу |
| POST | `/items/{id}/verify` | Подтвердить / скорректировать позицию |
| POST | `/services/import` | Загрузить каталог из XLSX/JSON |
| POST | `/catalogue/bootstrap` | Синтезировать каталог из имеющихся данных |
| POST | `/rematch` | Перезапустить сопоставление с каталогом |
| POST | `/upload` | Загрузить файл через HTTP (`?ocr=true`) |
| GET | `/documents`, `/stats` | Статус обработки, дашборд качества |
| POST | `/ocr`, GET `/ocr/health` | Автономный OCR для скан-PDF (опционально) |

Полная документация: http://localhost:8000/docs

---

## Структура проекта

```
medarchive/
├── app/
│   ├── parsers/          # Форматные парсеры
│   │   ├── detect.py     # Определение формата файла
│   │   ├── pdf.py        # Текстовый PDF (PyMuPDF)
│   │   ├── ocr_pdf.py    # Скан-PDF с геометрической реконструкцией колонок
│   │   ├── docx.py       # DOCX с принятием правок
│   │   ├── excel.py      # XLS/XLSX, авто-поиск заголовка
│   │   └── numbers.py    # Исправление OCR-артефактов в числах
│   ├── api.py            # FastAPI-приложение (все эндпоинты)
│   ├── pipeline.py       # Оркестрация пайплайна инджестинга
│   ├── models.py         # SQLAlchemy ORM-модели
│   ├── schemas.py        # Pydantic-схемы API
│   ├── normalize.py      # Сопоставление с каталогом (exact / fuzzy)
│   ├── tariffs.py        # Определение и маппинг тарифных колонок
│   ├── fx.py             # FX-конвертация валют
│   ├── cli.py            # CLI: init / ingest / stats / rematch
│   ├── config.py         # Настройки (DATABASE_URL, пороги)
│   └── static/           # Встроенный веб-интерфейс
├── frontend/             # Next.js 16 дашборд
│   ├── app/              # App Router — страницы
│   ├── components/       # Переиспользуемые компоненты
│   └── lib/api.ts        # Типизированный API-клиент
├── tests/
│   ├── golden/           # Эталонные результаты парсинга (10 прайс-листов)
│   └── eval/             # Оценка качества парсера
├── docs/
│   └── ARCHITECTURE.md   # Детальная архитектура
├── .env.example          # Шаблон переменных окружения
└── pyproject.toml
```

---

## Переменные окружения

| Переменная | По умолчанию | Описание |
|-----------|-------------|---------|
| `DATABASE_URL` | `postgresql://medarchive:medarchive@localhost/medarchive` | Строка подключения к PostgreSQL |
| `GROQ_API_KEY` | — | API-ключ Groq (опционально, для LLM-парсинга) |
| `GOOGLE_API_KEY` | — | API-ключ Google Gemini (опционально) |
| `OCR_BACKEND` | `easyocr` | OCR-движок: `easyocr` / `surya` / `tesseract` |

```bash
cp .env.example .env   # заполни нужные значения
```

---

## Дополнительные команды CLI

```bash
# Загрузить файл, папку или ZIP-архив с прайсами
uv run python -m app.cli ingest ./Хакатон
uv run python -m app.cli ingest scan.pdf --ocr      # принудительный OCR

# Статистика
uv run python -m app.cli stats

# Каталог услуг
uv run python -m app.cli load-catalogue catalogue.xlsx
uv run python -m app.cli bootstrap-catalogue --min-count 2
uv run python -m app.cli rematch
```

---

## Тесты

```bash
uv run pytest tests/                      # все тесты
uv run pytest tests/eval/run_eval.py      # оценка качества парсера на эталонных данных
```

---

## Ограничения

- OCR (EasyOCR) на CPU работает медленно — ~30-60 с на страницу; на GPU значительно быстрее
- Fuzzy-матчинг чувствителен к порогу: при агрессивном пороге возможны ложные сопоставления
- Версионирование опирается на `effective_date` из документа; если дата не указана, берётся дата инджестинга

---

MIT License © 2025 MedArchive Team

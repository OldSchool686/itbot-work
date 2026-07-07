# IT_BOT — Система техподдержки в мессенджере Макс MAX Messenger

Чат-бот для автоматизации IT-поддержки сотрудников компании. RAG на базе локальной LLM, веб-панель администратора и с push-уведомлениями в MAX мессенджер, возможна интеграция с Bitrix24 (не реализовано)
Типовой вариант был развернут на Ubuntu Server 24.04

## Архитектура

```
┌─────────────┐     ┌──────────────────────────────────────────────┐
│  MAX        │     │              Docker Compose                   │
│  Messenger  │◄──► │                                              │
│  (пользов-ль)│     │  ┌──────────┐    ┌─────────────────────┐   │
└─────────────┘     │  │  bot /   │    │  backend (FastAPI)  │   │
                     │  │bot_webhook│◄─►│  :8000              │   │
                     │  └──────────┘    │                      │   │
                     │                  │  ├── auth + admin     │   │
                     │                  │  ├── tickets CRUD     │   │
                     │                  │  ├── RAG pipeline     │   │
                     │                  │  ├── Bitrix24 sync    │   │
                     │                  │  └── mobile API (FCM) │   │
                     │                  └─────────────────────┘   │
                     │         ▲              ▲            ▲      │
                     │         │              │            │      │
                     │  ┌──────┴──┐    ┌─────┴───┐ ┌─────┴──┐  │
                     │  │ Postgres│    │ Redis   │ │ChromaDB│  │
                     │  │ :5432   │    │ :6379   │ │ :8000  │  │
                     │  └─────────┘    └─────────┘ └────────┘   │
                     │         ▲              ▲                  │
                     │         └──────────────┘                  │
                     │  ┌─────────────────────┐                 │
                     │  │ ollama (LLM + emb.) │                 │
                     │  │ :11434 GPU/CPU      │                 │
                     │  └─────────────────────┘                 │
                     └──────────────────────────────────────────┘

┌─────────────┐     ┌─────────────┐
│  Bitrix24   │◄──► │  Nginx +    │
│  (CRM)      │     │  SSL/TLS    │
└─────────────┘     └──────┬──────┘
                           │ proxy_pass :8000

┌─────────────┐     ┌─────────────┐
│  Mobile App │◄──► │  Firebase   │
│  (push FCM) │     │  Cloud Msg  │
└─────────────┘     └─────────────┘
```

## Схема работы

### Создание заявки (основной поток)

```
1. Пользователь открывает бота в MAX Messenger → /start
2. Бот проверяет пользователя по whitelist (API backend → PostgreSQL)
3. Если пользователь не в whitelist → запрос добавления с уведомлением админу
4. Пользователь выбирает категорию заявки из инлайн-кнопок
5. Вводит описание проблемы (текст, фото, файлы)
6. Бот отправляет данные во внутренний API backend (X-Internal-Token)
7. Backend создаёт тикет в PostgreSQL + синхронизирует сделку в Bitrix24 CRM
8. Если включён RAG → бот ищет в загруженных документах:
   a. Текст вопроса → Ollama embedding (nomic-embed-text)
   b. Поиск по векторам в ChromaDB (top-K ближайших чанков)
   c. Ollama LLM генерирует ответ на основе контекста + документов
9. Ответ отправляется пользователю через MAX API
10. Администратор видит тикет в веб-панели (/admin/) и может ответить вручную
```

### RAG-канал (поиск по базе знаний)

```
Пользователь → «Как настроить почту?»
  ↓
Ollama: nomic-embed-text → вектор запроса (768 dim)
  ↓
ChromaDB: cosine similarity search → top-5 чанков документов
  ↓
Ollama: qwen2.5:3b + системный промпт + контекст из чанков
  ↓
Ответ: «Для настройки почты выполните следующие шаги...»
  ↓
Кэш ответа в Redis (TTL = 1 час) → повторный запрос мгновенный
```

### Админ-панель

```
Nginx (:443) → backend FastAPI (:8000)
  ├── /admin/login          — JWT авторизация, HttpOnly cookie
  ├── /admin/tickets        — список тикетов, фильтрация, поиск
  ├── /admin/documents      — загрузка PDF/DOCX → парсинг → RAG индексация
  ├── /admin/templates      — шаблоны ответов (скачивание по ссылке)
  ├── /api/v1/users         — управление whitelist пользователей
  └── /api/v1/mobile/*      — API для мобильного приложения (FCM push)
```

### Bitrix24 синхронизация

```
Новый тикет → POST bitrix24/rest/*/deal.add → сделка «Новая»
Обновление статуса:
  NEW → IN_PROGRESS → RESOLVED → CLOSED
Автозакрытие через BITRIX24_AUTO_CLOSE_DAYS дней без активности
```

## Стек технологий

| Компонент | Технология | Назначение |
|-----------|-----------|-----------|
| Бот | Python, Telethon (MAX API) | Чат-бот в MAX Messenger |
| Backend | Python, FastAPI, SQLAlchemy | REST API, бизнес-логика |
| База данных | PostgreSQL 16 | Тикеты, пользователи, админы |
| Кэш и сессии | Redis 7 | Rate limiting, JWT, RAG cache |
| Векторная БД | ChromaDB | Хранение эмбеддингов документов |
| LLM | Ollama (qwen2.5:3b / phi4-mini) | Генерация ответов и эмбеддинги |
| Веб-панель | FastAPI + Jinja2 templates | Административный интерфейс |
| CRM | Bitrix24 REST API | Синхронизация сделок |
| Push | Firebase Cloud Messaging | Уведомления в мобильное приложение |
| Оркестрация | Docker Compose | Развёртывание всех сервисов |

## Сервисы и порты

| Контейнер | Порт | Назначение |
|-----------|------|-----------|
| `it_bot_backend` | 8000 | FastAPI (REST + админ-панель) |
| `it_bot_max` | — | Бот polling-режим |
| `it_bot_webhook` | 8081 | Flask мини-приложение для MAX |
| `it_bot_ollama` | 11434 | Локальная LLM (GPU/CPU) |
| `it_bot_postgres` | 5432 | PostgreSQL |
| `it_bot_redis` | 6379 | Redis |
| `it_bot_chromadb` | 8001 | ChromaDB векторная БД |

## Быстрый старт

```bash
git clone https://github.com/OldSchool686/itbot-max.git
cd itbot-max
cp .env.example .env
# Редактировать .env — заполнить токены и пароли
docker compose up -d --build
```

Подробная инструкция по установке на Ubuntu 24.04 — см. [INSTALL.md](INSTALL.md).

## Безопасность

- JWT-аутентификация админ-панели с HttpOnly cookie
- Rate limiting: 3 req/min на login, 30 req/min на admin API, 60 req/min на общий API
- Защита от брутфорса в приложении + fail2ban на уровне сервера
- Внутренний API (бот ↔ backend) защищён `X-Internal-Token` заголовком
- CORS whitelist, security headers, timing attack mitigation
- Strict mode whitelist пользователей — только добавленные номера могут пользоваться ботом

## Структура проекта

```
├── bot/                      # Чат-бот MAX Messenger
│   ├── main.py               # Точка входа (polling)
│   ├── webhook.py            # Webhook + мини-приложение
│   ├── handlers/             # Обработчики команд и сообщений
│   ├── keyboards/            # Инлайн-клавиатуры
│   ├── fsm/                  # FSM-машина состояний (Redis storage)
│   └── utils/                # Утилиты бота (whitelist, consent, фото)
├── backend/                  # FastAPI сервер
│   ├── main.py               # Точка входа + middleware
│   ├── api/                  # API роутеры
│   ├── models/               # SQLAlchemy ORM модели
│   ├── services/             # Бизнес-логика (Bitrix, RAG, Ollama)
│   ├── utils/                # Утилиты (auth, config, rate limiter)
│   └── admin_panel/          # Шаблоны и статика админ-панели
├── scripts/                  # SQL миграции, seed скрипты
├── documents/                # Загруженные документы для RAG
├── uploads/                  # Фото пользователей из бота
├── tests/                    # Интеграционные тесты
└── docker-compose.yml        # Оркестрация всех сервисов
```

## Лицензия

Proprietary. Все права защищены.

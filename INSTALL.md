# IT Support Bot — Инструкция по установке и настройке на Ubuntu 24.04

## Содержание

1. [Системные требования](#системные-требования)
2. [Подготовка сервера](#подготовка-сервера)
3. [Установка Docker и Docker Compose](#установка-docker-and-docker-compose)
4. [Клонирование репозитория](#клонирование-репозитория)
5. [Настройка переменных окружения](#настройка-переменных-окружения)
6. [Запуск проекта](#запуск-проекта)
7. [Первичная настройка Ollama](#первичная-настройка-ollama)
8. [Проверка работоспособности](#проверка-работоспособности)
9. [Настройка Nginx (опционально)](#настройка-nginx-опционально)
10. [Управление сервисами](#управление-сервисами)
11. [Миграция индексов PostgreSQL](#миграция-индексов-postgresql)
12. [Настройка автозапуска](#настройка-автозапуска)

---

## Системные требования

| Ресурс | Минимум | Рекомендуется |
|--------|---------|---------------|
| CPU | 4 ядра | 8+ ядер (для Ollama LLM) |
| RAM | 16 GB | 32+ GB (Ollama потребляет ~8-12 ГБ для моделей 3B) |
| SSD | 50 GB | 100+ GB (модели + документы) |
| ОС | Ubuntu 24.04 LTS | Ubuntu 24.04 LTS |
| Docker | 20.10+ | Последняя версия |

---

## Подготовка сервера

### Обновление системы

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl git wget unzip nginx certbot python3-pip
sudo reboot
```

После перезагрузки войдите в систему:

```bash
ssh user@your-server-ip
```

---

## Установка Docker и Docker Compose

### Автоматическая установка Docker

```bash
# Скрипт установки от Docker
curl -fsSL https://get.docker.com | sudo sh

# Добавляем текущего пользователя в группу docker (чтобы не писать sudo)
sudo usermod -aG docker $USER

# Перезапускаем сессию для применения прав групп
newgrp docker
```

### Проверка установки

```bash
docker --version        # Docker version 24.x.x или выше
docker compose version  # Docker Compose version v2.x.x
```

---

## Клонирование репозитория

```bash
# Создаём директорию для проекта
sudo mkdir -p /opt/it-bot
sudo chown $USER:$USER /opt/it-bot

cd /opt/it-bot

# Клонирование репозитория
git clone https://github.com/your-org/it-support-bot.git .
```

---

## Настройка переменных окружения

### Создание файла .env

```bash
cp .env.example .env
nano .env
```

### Обязательные параметры для редактирования

| Переменная | Описание | Пример значения |
|------------|----------|-----------------|
| `MAX_BOT_TOKEN` | Токен бота MAX Messenger | `your_max_bot_token_here` |
| `BITRIX24_WEBHOOK_URL` | URL вебхука Bitrix24 | `https://company.bitrix24.ru/rest/user_id/token/` |
| `ADMIN_SESSION_SECRET` | Секрет для JWT (мин. 32 символа) | Генерируется командой ниже |
| `ADMIN_INITIAL_PASSWORD` | Пароль админа при первом запуске | Замените на надёжный пароль! |
| `INTERNAL_API_KEY` | Ключ для внутреннего API (бот ↔ backend) | Минимум 32 случайных символа |
| `APP_BASE_URL` | Базовый URL приложения (для ссылок на скачивание, вебхуки) | `https://bot.spadm.ru` |

### Опциональные параметры

| Переменная | Описание | По умолчанию |
|------------|----------|---|
| `ADMIN_TOKEN_EXPIRE_MINUTES` | Время жизни сессии админа (минуты) | 30 |
| `MAX_POLLING` | Режим работы бота (true=polling, false=webhook) | true |
| `FCM_PROJECT_ID` / `FCM_CRED_JSON` | Firebase Cloud Messaging для push-уведомлений (мобильное приложение) | пусто |

### Генерация безопасных ключей

```bash
# Для ADMIN_SESSION_SECRET и INTERNAL_API_KEY:
openssl rand -hex 32

# Пример вывода: a1b2c3d4e5f67890... (скопируйте значение)
```

---

## Запуск проекта

### Первый запуск

```bash
cd /opt/it-bot

# Сборка и запуск всех сервисов
docker compose up -d --build

# Просмотр логов в реальном времени
docker compose logs -f
```

### Ожидаемый порядок запуска

1. **postgres** — база данных (первый)
2. **redis** — кэш и сессии
3. **chromadb** — векторная БД для RAG
4. **ollama** — локальный LLM (загружает модели при первом старте)
5. **backend** — FastAPI сервер (ждёт postgres, redis, chromadb)
6. **bot** — MAX Messenger бот polling (ждёт backend и redis)
7. **bot_webhook** — Flask мини-приложение для MAX (порт 8081, ждёт backend и redis)

---

## Первичная настройка Ollama

### Загрузка моделей

Модели настраиваются через `.env` переменные `OLLAMA_MODEL_CHAT` и `OLLAMA_MODEL_EMBED`.

При первом запуске Ollama автоматически загрузит модели из `.env`:

```bash
# Проверить статус загрузки моделей
docker compose exec ollama ollama list

# Если модели не загрузились, вручную:
docker compose exec -it ollama bash
ollama pull qwen2.5:3b        # Для чата и ответов (OLLAMA_MODEL_CHAT)
ollama pull nomic-embed-text   # Для эмбеддингов документов (OLLAMA_MODEL_EMBED)
exit
```

**Выбор модели по железу:**

| Модель | RAM | CPU | Назначение |
|--------|-----|-----|------------|
| `qwen3.5:2b` | 4-6 ГБ | 4+ ядер | Слабые серверы или ПК (Ryzen 5 5500GT и аналоги) |
| `qwen2.5:3b` | 8-12 ГБ | 6+ ядер | Рекомендуемая по умолчанию |

Сменить модель: отредактируйте `OLLAMA_MODEL_CHAT` в `.env`, затем пересоберите бэкенд.

### Настройка CPU (по умолчанию)

По умолчанию Ollama работает на CPU. В `docker-compose.yml` уже настроены оптимизации:

```yaml
ollama:
  environment:
    OLLAMA_NUM_THREAD: "6"       # Кол-во потоков CPU (ядра процессора, не потоки)
    OLLAMA_KEEP_ALIVE: "-1"      # Держать модель в памяти навсегда (не выгружать)
    OLLAMA_NUM_PARALLEL: "1"     # Максимум 1 одновременный запрос
```

Рекомендуется настроить лимиты ресурсов, чтобы Ollama не захватил всю память сервера:

```yaml
deploy:
  resources:
    limits:
      cpus: '6.0'                # Лимит CPU (ядра процессора)
      memory: 16G                # Максимум ОЗУ для контейнера
    reservations:
      cpus: '4.0'               # Гарантированное кол-во ядер
      memory: 8G                # Минимальный резерв памяти
```

### Настройка GPU (опционально)

Если сервер имеет NVIDIA GPU, добавьте в `docker-compose.yml`:

```yaml
services:
  ollama:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```

Затем пересоберите:

```bash
docker compose up -d --build
```

---

## Проверка работоспособности

### Health Check Endpoints

```bash
# Быстрая проверка (только процесс жив)
curl http://localhost:8000/api/v1/health/live

# Проверка критичных зависимостей (postgres + redis)
curl http://localhost:8000/api/v1/health/ready

# Полный статус всех сервисов
curl http://localhost:8000/api/v1/health | python3 -m json.tool
```

### Ожидаемый ответ полного health check

```json
{
  "status": "ok",
  "dependencies": {
    "postgres": {"status": "healthy"},
    "redis": {"status": "healthy"},
    "ollama": {"status": "healthy", "models": ["qwen2.5:3b", "nomic-embed-text"]},
    "chromadb": {"status": "healthy"},
    "bitrix24": {"status": "healthy"}
  }
}
```

### Тестирование RAG запроса

```bash
curl -X POST http://localhost:8000/api/v1/rag/query \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: $INTERNAL_API_KEY" \
  -d '{"query_text": "Как настроить почту?", "user_id": 1}' | python3 -m json.tool
```

---

## Настройка Nginx (опционально, но рекомендуется для защиты)

### Создание конфига с rate limiting

Rate limiting на уровне nginx — второй барьер после защиты в самом приложении.
Блокирует брутфорс до того, как запрос дойдёт до backend.

```bash
sudo nano /etc/nginx/sites-available/it-bot-backend
```

**Важно:** `limit_req_zone` должен быть в контексте `http{}` — то есть в `/etc/nginx/nginx.conf` или в отдельном файле `/etc/nginx/conf.d/rate_limit.conf`. Nginx не позволяет определять зоны внутри `server {}`.

#### Шаг 1: Зоны rate limiting (глобальный уровень)

```bash
sudo nano /etc/nginx/conf.d/it-bot-rate-limit.conf
```

```nginx
# login — 3 req/min, burst 2 (атакующий сделает максимум 5 запросов за минуту)
limit_req_zone $binary_remote_addr zone=login_limit:10m rate=3r/m;

# admin panel / auth API — 30 req/min, burst 10
limit_req_zone $binary_remote_addr zone=admin_limit:10m rate=30r/m;

# общий API — 60 req/min, burst 20
limit_req_zone $binary_remote_addr zone=api_limit:10m rate=60r/m;
```

#### Шаг 2: Конфиг сервера

```nginx
server {
    listen 80;
    server_name your-domain.com;

    # Скрыть версию nginx
    server_tokens off;

    # === Лимит размера тела запроса (ОБЯЗАТЕЛЕН для /api/v1/documents/upload) ===
    # 2026-06-02: без client_max_body_size nginx возвращает 413 на файлы > 1MB
    # ДО того, как backend их увидит (default nginx = 1m).
    # Backend лимит: backend/api/documents.py:33-34 (DOCUMENT_MAX_SIZE = 50MB).
    # 60m даёт запас 10MB над backend лимитом.
    client_max_body_size 60m;
    client_body_buffer_size 128k;
    client_body_timeout 60s;

    # --- Login endpoint (самый критичный): 3 req/min ---
    location = /api/v1/auth/login {
        limit_req zone=login_limit burst=2 nodelay;
        limit_req_status 429;

        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # --- Admin HTML pages и auth API (logout, me): 30 req/min ---
    location /admin/ {
        limit_req zone=admin_limit burst=10 nodelay;
        limit_req_status 429;

        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location = /admin/login {
        limit_req zone=admin_limit burst=5 nodelay;
        limit_req_status 429;

        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /api/v1/auth/ {
        limit_req zone=admin_limit burst=10 nodelay;
        limit_req_status 429;

        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # --- Общий API (включая RAG): 60 req/min ---
    location /api/ {
        limit_req zone=api_limit burst=20 nodelay;
        limit_req_status 429;

        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;  # Для RAG запросов (LLM может отвечать долго)
    }

    # --- Всё остальное ---
    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # Static files (admin panel)
    location /static/ {
        alias /opt/it-bot/backend/admin_panel/static/;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
```

### Активация конфига

```bash
sudo ln -s /etc/nginx/sites-available/it-bot-backend /etc/nginx/sites-enabled/
sudo nginx -t                    # Проверка синтаксиса
sudo systemctl reload nginx      # Применение без перезагрузки
```

### SSL сертификат (Let's Encrypt)

После настройки rate limiting получите SSL:

```bash
sudo certbot --nginx -d your-domain.com
```

> `certbot` автоматически добавит `listen 443 ssl`, redirect HTTP→HTTPS и настроит HSTS.
> Зоны rate limiting из `/etc/nginx/conf.d/it-bot-rate-limit.conf` сохранятся — они не зависят от server block.

### Проверка rate limiting

```bash
# Быстрый тест: 10 запросов к login за секунду (должны получить 429 после ~5-го)
for i in {1..10}; do curl -s -o /dev/null -w "%{http_code}\n" https://your-domain.com/api/v1/auth/login; done
```

Ожидаемый вывод: первые запросы вернут `302` (redirect на HTTPS) или `405`, а после исчерпания лимита — `429`.

---

## Управление сервисами

### Основные команды

| Команда | Описание |
|---------|----------|
| `docker compose up -d` | Запуск всех сервисов в фоне |
| `docker compose down` | Остановка всех сервисов |
| `docker compose restart backend` | Перезапуск конкретного сервиса |
| `docker compose logs -f bot` | Логи бота в реальном времени |
| `docker compose exec backend bash` | Консоль внутри контейнера backend |
| `docker compose ps` | Статус всех контейнеров |

### Просмотр логов

```bash
# Все логи
docker compose logs -f --tail=100

# Только ошибки
docker compose logs -f 2>&1 \| grep -i error

# Логи конкретного сервиса за последние 5 минут
docker compose logs --since 5m backend
```

### Обновление кода

```bash
cd /opt/it-bot
git pull origin main          # Получить обновления
docker compose up -d --build  # Пересобрать и запустить
docker compose ps             # Проверить статус
```

---

## Миграция индексов PostgreSQL

После обновления кода могут добавиться новые индексы. Примените их:

```bash
# Вариант 1: Через Docker exec (без перезапуска)
docker compose exec postgres psql \
  -U bot_user -d it_bot \
  -f /docker-entrypoint-initdb.d/init.sql

# Вариант 2: Пересоздание контейнера (перезапуск БД)
docker compose down postgres
docker compose up -d postgres
```

---

## Настройка автозапуска

### Docker Compose как systemd сервис

Создайте сервисный файл:

```bash
sudo nano /etc/systemd/system/it-bot.service
```

```ini
[Unit]
Description=IT Support Bot (Docker Compose)
Requires=docker.service
After=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/it-bot
ExecStartPre=/usr/bin/docker compose down
ExecStart=/usr/local/bin/docker compose up -d --build
ExecStop=/usr/local/bin/docker compose down
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Активируйте сервис:

```bash
sudo systemctl daemon-reload
sudo systemctl enable it-bot.service   # Автозапуск при загрузке системы
sudo systemctl start it-bot.service    # Запустить сейчас
sudo systemctl status it-bot.service   # Проверить статус
```

---

## Мониторинг производительности

### Ресурсы Docker

```bash
# Использование CPU/RAM каждым контейнером
docker stats --no-stream

# Постоянный мониторинг
watch -n 5 'docker stats --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"'
```

### Performance API

```bash
# Статистика запросов (требует JWT администратора)
curl http://localhost:8000/api/v1/performance/stats \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" | python3 -m json.tool

# Пример ответа:
# {
#   "count": 1523,
#   "avg_ms": 45.2,
#   "p95_ms": 180.5,
#   "max_ms": 2100.0
# }
```

### Логирование медленных запросов

Запросы дольше 500ms автоматически логируются как WARNING:

```bash
docker compose logs -f backend | grep WARNING
```

---

## Частые проблемы и решения

### Ollama не загружает модели

```bash
# Проверить доступность GPU (если есть)
nvidia-smi

# Перезапустить Ollama с очисткой кэша
docker compose stop ollama
docker rm it_bot_ollama
docker compose up -d ollama

# Ручная загрузка моделей
docker compose exec -it ollama bash
ollama pull qwen2.5:3b
```

### Redis не отвечает

```bash
# Проверить статус Redis
docker compose exec redis redis-cli ping    # Должно вернуть PONG

# Если ошибка — перезапустить Redis
docker compose restart redis
```

### Бот не подключается к backend

```bash
# Проверить INTERNAL_API_KEY в .env обоих сервисов
grep INTERNAL_API_KEY .env

# Проверить доступность backend из бота
docker compose exec bot curl http://backend:8000/api/v1/health/live
```

### Bot webhook (mini-app) не отвечает

bot_webhook работает на порту 8081 и используется для MAX мини-приложения.

```bash
# Проверить статус
curl http://localhost:8081/webhook/max

# Перезапустить
docker compose restart bot_webhook

# Логи
docker compose logs -f bot_webhook
```

### PostgreSQL не создаёт таблицы

```bash
# Проверить init.sql
docker compose logs postgres | grep -i error

# Ручное применение миграции
docker compose exec postgres psql \
  -U bot_user -d it_bot -f /docker-entrypoint-initdb.d/init.sql
```

---

## Безопасность

### Обязательные действия после установки

1. **Сменить пароль администратора** (первый вход через начальные учётки)
2. **Настроить время сессии админа**: по умолчанию 30 минут (`ADMIN_TOKEN_EXPIRE_MINUTES=30` в `.env`). Измените при необходимости, но не ставьте больше 60 минут для безопасности.
3. **Удалить начальные учётные данные** из `.env`:
  ```bash
    ADMIN_INITIAL_USERNAME=deleted_never_use_again
    ADMIN_INITIAL_PASSWORD=deleted_never_use_again
    ```
4. **Настроить firewall**:
   ```bash
   sudo ufw allow 22/tcp      # SSH
   sudo ufw allow 80/tcp      # HTTP (Nginx)
   sudo ufw allow 443/tcp     # HTTPS (Nginx)
   sudo ufw enable
   ```

### Резервное копирование данных

```bash
# PostgreSQL backup
docker compose exec postgres pg_dump -U bot_user it_bot > backup_$(date +%Y%m%d).sql

# Восстановление из бэкапа
cat backup_20250101.sql | docker compose exec -T postgres psql -U bot_user it_bot
```

---

## Уровень 3: Защита на уровне сервера (fail2ban + iptables)

Этот уровень автоматически банит IP в firewall при обнаружении атак.
Требует root-доступ к серверу. Fail2ban и ufw должны быть уже установлены.

### Шаг 1: Настройка Docker logging для fail2ban

fail2ban читает текстовые логи, но Docker по умолчанию пишет JSON в `/var/lib/docker/containers/`.
Самый надёжный способ — перенаправить логи backend через journald или syslog.

**Вариант A: journald driver (рекомендуется)**

Добавьте в `docker-compose.yml` для сервиса `backend`:

```yaml
services:
  backend:
    logging:
      driver: journald
      options:
        tag: "it-bot-backend"
```

Перезапустите:

```bash
docker compose up -d backend
journalctl -u docker --since "1 hour ago" | grep it-bot-backend  # Проверить, что логи идут
```

**Вариант B: file driver (альтернатива)**

```yaml
services:
  backend:
    logging:
      driver: files
      options:
        max-size: "10m"
        max-file: "3"
        tag: "it-bot-backend"
```

Логи будут в `/var/log/docker/`.

### Шаг 2: Fail2ban фильтр для FastAPI

Создайте файл фильтра:

```bash
sudo nano /etc/fail2ban/filter.d/it-bot-login.conf
```

```ini
[Definition]

# Логи FastAPI содержат строки вида:
# WARNING ... Failed login attempt: user='admin' ip=1.2.3.4
# WARNING ... Login attempt for unknown user: admin
# WARNING ... IP 1.2.3.4 blocked after 5 login attempts

failregex = ^.*WARNING.*Failed login attempt: .* ip=<HOST>.*$
            ^.*WARNING.*Login attempt for unknown user:.*ip=<HOST>.*$
            ^.*WARNING.*IP <HOST> blocked after.*login attempts.*$

ignoreregex =

# Для journald driver
datepattern = ^L:%(year)d-%(month)d-%(day)d %(hour)d:%(minute)d:%(second)d
```

Проверьте, что фильтр работает:

```bash
sudo fail2ban-regex <(journalctl -u docker --since "1 hour ago" | grep it-bot-backend) /etc/fail2ban/filter.d/it-bot-login.conf
```

Команда покажет количество совпадений. Если 0 — проверьте формат логов в `docker compose logs backend`.

### Шаг 3: Jail для IT-Bot

Добавьте секцию в `/etc/fail2ban/jail.local`:

```bash
sudo nano /etc/fail2ban/jail.local
```

**Для journald driver:**

```ini
[it-bot-login]
enabled = true
port = http,https
filter = it-bot-login
logdriver = systemd
journalmatch = _SYSTEMD_UNIT=docker.service SYSLOG_FACILITY=3
maxretry = 5
findtime = 60
bantime = 900
banaction = ufw-force-deny-ip
```

**Для file driver:**

```ini
[it-bot-login]
enabled = true
port = http,https
filter = it-bot-login
logpath = /var/log/docker/it-bot-backend*.log
maxretry = 5
findtime = 60
bantime = 900
banaction = ufw
```

**Важно:** Если на вашем сервере установлен `ufw-force-deny-ip`, используйте его вместо `ufw`. Проверить доступные action'ы: `ls /etc/fail2ban/action.d/ | grep -i ufw`

Перезапустите fail2ban:

```bash
sudo systemctl restart fail2ban
sudo fail2ban-client status it-bot-login   # Проверить, что jail активен
```

### Шаг 4: Базовые правила iptables для защиты сервисов

Скрытые порты (Redis, PostgreSQL, Ollama) не должны быть доступны из интернета.

```bash
# Узнать Docker network subnet
docker network ls
docker network inspect $(docker network ls --format '{{.Name}}' | head -1) | grep Subnet

# Блокировать доступ к Redis (6379), PostgreSQL (5432), Ollama (11434), ChromaDB (8001) извне
# Разрешаем только localhost и Docker network
DOCKER_SUBNET="172.18.0.0/16"  # Замените на ваш subnet

sudo ufw deny from any to $DOCKER_SUBNET port 6379   # Redis
sudo ufw deny from any to $DOCKER_SUBNET port 5432    # PostgreSQL
sudo ufw deny from any to $DOCKER_SUBNET port 11434   # Ollama
sudo ufw deny from any to $DOCKER_SUBNET port 8001    # ChromaDB

# Разрешить только HTTP/HTTPS и SSH извне (если ещё не настроено)
sudo ufw allow 22/tcp   # SSH
sudo ufw allow 80/tcp   # HTTP → nginx → backend
sudo ufw allow 443/tcp  # HTTPS → nginx → backend

# Ограничить SSH: максимум 4 новых подключения в минуту (защита от брутфорса SSH)
sudo ufw limit 22/tcp

sudo ufw reload
sudo ufw status verbose
```

### Шаг 5: Проверка защиты

```bash
# fail2ban: статус всех jail'ов
sudo fail2ban-client status

# Забаненные IP
sudo fail2ban-client status it-bot-login | grep "Banned IP list"

# UFW правила
sudo ufw status numbered

# Протестировать брутфорс (с localhost!) — после 5 попыток должен получить 429
for i in {1..10}; do curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/v1/auth/login; done
```

### Полезные команды fail2ban

```bash
# Разбанить IP (если случайно заблокировали себя)
sudo fail2ban-client set it-bot-login unbanip 1.2.3.4

# Посмотреть логи jail'а
sudo fail2ban-client loglevel 3
journalctl -u fail2ban -f

# Автоматический сброс банов (через bantime) — не требует ручной работы
```

---

## Мониторинг защиты

### Проверка всех уровней в одной команде

```bash
echo "=== Fail2ban ===" && sudo fail2ban-client status it-bot-login \
&& echo "" && echo "=== UFW ===" && sudo ufw status numbered \
&& echo "" && echo "=== Docker ===" && docker compose ps \
&& echo "" && echo "=== Backend health ===" && curl -s http://localhost:8000/api/v1/health/live
```

### Мониторинг атак в реальном времени

```bash
# Логи backend — ищите WARNING (неудачные попытки входа)
docker compose logs -f --tail=50 backend | grep -i "warning\|failed login\|blocked"

# Fail2ban — новые баны
journalctl -u fail2ban -f --since "1 hour ago" | grep -E "(Ban|Unban)"

# Nginx 429 ошибки (rate limiting сработал)
sudo tail -f /var/log/nginx/error.log | grep "limiting requests\|429"
```

### Быстрая диагностика проблем

```bash
# Сервисы работают?
docker compose ps

# Backend отвечает?
curl -s http://localhost:8000/api/v1/health/live

# Fail2ban активен?
sudo fail2ban-client status it-bot-login

# Nginx не блокирует случайно ваш IP?
sudo ufw status numbered | grep "REJECT\|DENY"
```

### Разбанить IP (если заблокировали себя)

```bash
# Через fail2ban
sudo fail2ban-client set it-bot-login unbanip YOUR_IP

# Через UFW
sudo ufw status numbered          # Найти номер правила
sudo ufw delete <NUMBER>           # Удалить правило по номеру
```

---

## Поддержка и документация

- API документация: `http://your-domain.com/docs` (Swagger UI)
- Health check: `http://your-domain.com/api/v1/health`
- Логи: `docker compose logs -f --tail=500`

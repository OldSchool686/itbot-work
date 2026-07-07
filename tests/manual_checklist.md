# Manual Test Checklist — IT Support Bot

## Prerequisites
- [ ] Docker Compose running: `docker compose up -d`
- [ ] All services healthy: `docker compose ps` shows 6 containers UP
- [ ] Health check passes: `curl http://localhost:8000/api/v1/health` → `{"status":"ok"}`

---

## Phase 2: Backend Core

### Auth
- [ ] Login with default admin credentials via curl:
  ```bash
  curl -X POST http://localhost:8000/api/v1/auth/login \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"change_me_now"}'
  ```
- [ ] JWT token returned in response
- [ ] `/api/v1/auth/me` returns admin username with Bearer token

---

## Phase 3: Admin + Whitelist

### User Management
- [ ] Add user via API: `POST /api/v1/users/add` with `{phone:"+79001234567", full_name:"Test User"}`
- [ ] List users: `GET /api/v1/users/list` shows added user
- [ ] Deactivate user: `PUT /api/v1/users/1/deactivate` → is_active=false
- [ ] CSV import (upsert mode): upload CSV with phone, full_name columns
- [ ] CSV export: download and verify format matches import

### Bot Internal API
- [ ] Check access for whitelisted user: `POST /api/v1/bot/check-access {phone:"+79001234567"}` → allowed=true
- [ ] Check access for unknown phone → allowed=false
- [ ] User by phone lookup returns correct data

---

## Phase 4: Bot Foundation (MAX Messenger)

### /start Flow
- [ ] Send `/start` to bot in MAX → request contact button appears
- [ ] Share contact with whitelisted number → welcome message + main menu
- [ ] Share contact with non-whitelisted number → "Доступ ограничен" message
- [ ] Whitelisted user without consent → PDn text + agree/disagree buttons
- [ ] Click "Согласен" → consent saved, main menu shown

### Main Menu
- [ ] "Создать заявку" → category selection screen
- [ ] "Поиск по базе знаний" → prompt for question
- [ ] "Мои заявки" → list of recent tickets (or empty message)
- [ ] "Помощь" → static help text
- [ ] "Остановить" → session cleared

---

## Phase 5: Ticket Creation + Bitrix24

### Full Ticket Flow
- [ ] Select category (computer/mfu/software/certificate/other)
- [ ] Pre-filled name shown, confirm or edit
- [ ] Department selection via keyboard
- [ ] Description input (~500 chars max)
- [ ] Photo upload (up to 3 photos)
- [ ] Confirmation screen with all fields + edit buttons per field
- [ ] Submit → confirmation message "Заявка #N создана"

### Bitrix24 Verification
- [ ] Deal created in B24 CRM with correct title format "[BOT #N] category: name"
- [ ] Custom field UF_CRM_IT_BOT_ID set to ticket ID
- [ ] Contact linked (or created) with phone number
- [ ] Stage set to NEW

---

## Phase 6: RAG Pipeline

### Document Upload
- [ ] Login to admin panel at `http://localhost:8000/admin`
- [ ] Navigate to "Документы" tab
- [ ] Upload PDF file → indexed successfully (shows chunk count)
- [ ] Upload DOCX file → indexed successfully
- [ ] Upload TXT file → indexed successfully
- [ ] Document appears in table with correct metadata

### RAG Search in Bot
- [ ] Click "Поиск по базе знаний" in MAX bot
- [ ] Ask question related to uploaded document content
- [ ] Receive answer with source references (filename + chunk index)
- [ ] Same question again → cached response (faster, shows "(ответ из кэша)")
- [ ] Unrelated question → fallback message suggesting ticket creation

### Document Management
- [ ] Delete document from admin panel → removed from vector store
- [ ] Deleted document no longer returns in search results

---

## Phase 7: Admin Panel

### Login & Dashboard
- [ ] Login page at `/admin/login` works
- [ ] Correct credentials → redirect to dashboard
- [ ] Wrong credentials → error message shown
- [ ] Dashboard shows stats (users, documents, admins count)
- [ ] Tab navigation works (Dashboard/Документы/Пользователи/Администраторы)

### User Management in UI
- [ ] Users table displays all allowed users with correct data
- [ ] Search/filter by name or phone
- [ ] Add new user via inline form → appears in table
- [ ] Deactivate user → status changes to "Отключён"
- [ ] Delete user → removed from table
- [ ] CSV import page works (upload, select mode, execute)
- [ ] CSV export downloads valid file

### Admin Management
- [ ] Add new admin via inline form
- [ ] Admins table shows all admins with last login time

---

## Phase 8: Background Tasks

### Pending Sync Retry
- [ ] Create ticket with B24 service down → status='pending_sync'
- [ ] Start B24 service → within 5 minutes ticket synced, status='new', bitrix_deal_id set

### Auto-Close
- [ ] Ticket with status='resolved' and updated_at > 7 days ago → auto-closed to 'closed'
- [ ] B24 deal stage updated to CLOSED if bitrix_deal_id exists

---

## Phase 9: Final Verification

### System-Wide
- [ ] All unit tests pass: `pytest backend/tests/ -v` (123+ passed)
- [ ] Docker Compose starts cleanly: no errors in logs
- [ ] Admin panel accessible at http://localhost:8000/admin
- [ ] Bot responds to /start in MAX with real bot token
- [ ] Full user flow tested end-to-end in MAX

### Performance
- [ ] RAG response time < 15 seconds (with Qwen 3B on CPU)
- [ ] Admin panel loads in < 2 seconds
- [ ] API health check responds in < 100ms

---

## Sign-off

| Date | Tester | Phase | Status | Notes |
|------|--------|-------|--------|-------|
| | | All | ☐ Pass / ☐ Fail | |

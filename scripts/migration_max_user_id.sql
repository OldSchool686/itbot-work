-- Migration: add max_user_id to allowed_users
-- Run once against existing database:
--   docker exec it_bot_postgres psql -U bot_user -d it_bot -f /docker-entrypoint-initdb.d/migration_max_user_id.sql

ALTER TABLE allowed_users ADD COLUMN IF NOT EXISTS max_user_id BIGINT UNIQUE;
ALTER TABLE allowed_users ALTER COLUMN phone DROP NOT NULL;

ALTER TABLE tickets ADD COLUMN IF NOT EXISTS closed_by_user BOOLEAN DEFAULT FALSE;
COMMENT ON COLUMN tickets.closed_by_user IS 'True if ticket was closed by the user via bot, not by admin';

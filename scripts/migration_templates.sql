ALTER TABLE documents ADD COLUMN IF NOT EXISTS is_template BOOLEAN DEFAULT FALSE;
COMMENT ON COLUMN documents.is_template IS 'True if document is a downloadable ticket template';

ALTER TABLE documents ADD COLUMN IF NOT EXISTS description TEXT;
COMMENT ON COLUMN documents.description IS 'Optional description for templates, used in RAG search';

CREATE INDEX IF NOT EXISTS idx_documents_is_template_active ON documents (is_template, is_active);

-- Fix existing rows: NULL → FALSE (DEFAULT does not backfill existing rows)
UPDATE documents SET is_template = FALSE WHERE is_template IS NULL;

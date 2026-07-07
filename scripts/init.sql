CREATE TABLE IF NOT EXISTS admins (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(500),
    is_active BOOLEAN DEFAULT TRUE,
    last_login_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS allowed_users (
    id SERIAL PRIMARY KEY,
    phone VARCHAR(20) UNIQUE,
    max_user_id BIGINT UNIQUE,
    full_name VARCHAR(500) NOT NULL,
    department VARCHAR(500),
    consent_given BOOLEAN DEFAULT FALSE,
    consent_timestamp TIMESTAMPTZ,
    is_active BOOLEAN DEFAULT TRUE,
    added_by VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    max_user_id BIGINT UNIQUE NOT NULL,
    first_name VARCHAR(255),
    last_name VARCHAR(255),
    phone VARCHAR(20),
    consent_given BOOLEAN DEFAULT FALSE,
    consent_timestamp TIMESTAMPTZ,
    is_whitelisted BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tickets (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    full_name VARCHAR(500) NOT NULL,
    phone VARCHAR(20) NOT NULL,
    department VARCHAR(500) NOT NULL,
    category VARCHAR(100) NOT NULL,
    description TEXT NOT NULL,
    photo_urls JSONB,
    bitrix_deal_id INTEGER UNIQUE,
    status VARCHAR(50) DEFAULT 'new',
    closed_by_user BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    filename VARCHAR(500) NOT NULL,
    original_path VARCHAR(1000),
    file_type VARCHAR(20) NOT NULL,
    size_bytes BIGINT,
    chunks_count INTEGER DEFAULT 0,
    uploaded_by VARCHAR(255),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rag_queries (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    query_text TEXT NOT NULL,
    response_text TEXT,
    sources_used JSONB,
    cached BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS departments (
    id SERIAL PRIMARY KEY,
    name VARCHAR(500) NOT NULL UNIQUE,
    type VARCHAR(50) NOT NULL,
    parent_id INTEGER REFERENCES departments(id),
    is_active BOOLEAN DEFAULT TRUE
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_allowed_users_is_active ON allowed_users(is_active);
CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_created_at ON tickets(created_at);
CREATE INDEX IF NOT EXISTS idx_documents_filename ON documents(filename);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_tickets_status_updated ON tickets(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_tickets_pending_sync ON tickets(status, created_at) WHERE status = 'pending_sync';
CREATE INDEX IF NOT EXISTS idx_documents_is_active ON documents(is_active);
CREATE INDEX IF NOT EXISTS idx_departments_name_active ON departments(is_active, name);
CREATE INDEX IF NOT EXISTS idx_rag_queries_user_id ON rag_queries(user_id);

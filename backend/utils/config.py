from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # MAX Messenger Bot
    max_bot_token: str = ""
    max_webhook_url: str = ""
    max_polling: bool = True

    # Bitrix24 Integration
    bitrix24_webhook_url: str = ""
    bitrix24_deal_type_id: str = "SERVICE"
    bitrix24_stage_new: str = "NEW"
    bitrix24_stage_in_progress: str = "IN_PROGRESS"
    bitrix24_stage_resolved: str = "RESOLVED"
    bitrix24_stage_closed: str = "CLOSED"
    bitrix24_auto_close_days: int = 7

    # Ollama
    ollama_base_url: str = "http://ollama:11434"
    ollama_model_chat: str = "qwen2.5:3b"
    ollama_model_embed: str = "nomic-embed-text"

    # PostgreSQL
    postgres_db: str = "it_bot"
    postgres_user: str = "bot_user"
    postgres_password: str = ""
    database_url: str = ""  # MUST be set in .env — no default password allowed

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # ChromaDB
    chroma_db_host: str = "chromadb"
    chroma_db_port: int = 8000

    # Admin Panel Authentication
    admin_session_secret: str = ""
    admin_token_expire_minutes: int = 30
    admin_initial_username: str = ""  # Set in .env to auto-create initial admin
    admin_initial_password: str = ""  # MUST be set in .env — no default password allowed

    # Initial Allowed User (whitelist seed)
    admin_initial_phone: str = ""
    admin_initial_full_name: str = ""

    # Brute-force protection
    login_max_attempts: int = 5          # max attempts per IP in window
    login_window_seconds: int = 60       # sliding window size (seconds)
    login_lockout_threshold: int = 10    # failed attempts before account lockout
    login_lockout_duration: int = 900    # lockout duration (seconds, 15 min)

    # Application
    app_env: str = "production"
    log_level: str = "INFO"
    rag_chunk_size: int = 500
    rag_chunk_overlap: int = 50
    rag_top_k: int = 5
    ai_cache_ttl: int = 3600

    # User Whitelist
    whitelist_enabled: bool = True
    whitelist_strict_mode: bool = True
    whitelist_phone_formats: str = "+7XXXXXXXXXX,8XXXXXXXXXX,+XXXXXXXXXXX"

    # Bot webhook service URL (for backend -> bot communication)
    bot_webhook_url: str = "http://it_bot_webhook:8080"

    # Internal API protection
    internal_api_key: str = ""

    # Firebase Cloud Messaging (push notifications)
    fcm_project_id: str = ""
    fcm_cred_json: str = ""  # Path to Firebase service account JSON

    # CORS
    allowed_cors_origins: str = "http://localhost:8000,https://your-domain.com"

    @model_validator(mode="after")
    def validate_security(self) -> "Settings":
        if len(self.admin_session_secret) < 32:
            raise ValueError(
                "ADMIN_SESSION_SECRET must be at least 32 characters. "
                "Set it in .env for security."
            )
        if not self.database_url:
            raise ValueError("DATABASE_URL must be set in .env file")
        if self.admin_initial_username and len(self.admin_initial_password) < 8:
            raise ValueError(
                "ADMIN_INITIAL_PASSWORD must be at least 8 characters "
                "when ADMIN_INITIAL_USERNAME is set"
            )
        return self

    class Config:
        env_file = ".env"


settings = Settings()

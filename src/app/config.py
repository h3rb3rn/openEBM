"""Central application settings, loaded from environment variables / .env."""
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    environment: str = "production"
    log_level: str = "INFO"
    secret_key: str = "changeme_jwt_secret_min_32_chars"
    access_token_expire_minutes: int = 480
    session_ttl_seconds: int = 3600
    # Set to true only once TLS is terminated in front of the app (reverse
    # proxy or direct HTTPS) — otherwise browsers silently drop the session
    # cookie and login appears to fail.
    cookie_secure: bool = False

    # PostgreSQL. database_url is the superuser bootstrap connection, used
    # only for running Alembic migrations (DDL like CREATE POLICY needs
    # elevated privileges). The running app queries through app_database_url
    # instead — a NOSUPERUSER/NOBYPASSRLS role, so the row-level-security
    # policies (see alembic/versions/..._enable_row_level_security.py)
    # actually apply. A superuser connection bypasses RLS unconditionally
    # regardless of policy configuration, so this split is not optional.
    database_url: str = "postgresql+asyncpg://ebm_user:changeme@postgres:5432/ebm_db"
    app_database_url: str = "postgresql+asyncpg://ebm_app:changeme@postgres:5432/ebm_db"

    # Valkey / Redis
    valkey_url: str = "redis://:changeme@valkey:6379/0"

    # ChromaDB
    chroma_host: str = "chromadb"
    chroma_port: int = 8000
    chroma_token: str = "changeme"

    # Neo4j
    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "changeme"

    # MCP validation server
    mcp_server_url: str = "http://mcp-server:8001"
    mcp_secret: str = "changeme_mcp_secret"

    # LLM backends. "ollama" uses OLLAMA_BASE_URL with automatic protocol
    # detection (native Ollama vs. OpenAI-compatible); "openai" always uses
    # OPENAI_BASE_URL. Secrets must come from the environment, never from code.
    llm_provider: str = "openai"
    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_model: str = "llama3.1:8b"
    embedding_model: str = "nomic-embed-text"
    openai_api_key: str = ""
    openai_model: str = "qwen3.6:35b@N04-RTX"
    openai_base_url: str = "https://api.moe-sovereign.org/v1"

    # Interoperability: shared secret for the container-internal
    # MCP-server -> FastAPI channel (X-Internal-Key header)
    internal_api_key: str = "changeme_internal_ebm_key_min32"
    app_internal_url: str = "http://app:8000"

    # Outbound email (password reset). If smtp_host is empty, email sending
    # is a logged no-op rather than an error — matches how other optional
    # integrations in this app degrade (e.g. Sentry, see error_tracking.py).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_from: str = "EBM Analyzer <noreply@example.org>"
    # Public URL the app is reachable at, used to build links in emails
    # (password reset). Distinct from app_internal_url, which is the
    # container-internal address other services use to reach this app.
    public_base_url: str = "http://localhost:8080"

    # Error tracking (Sentry-compatible). Empty = disabled, see
    # error_tracking.py. Point at a self-hosted Sentry/GlitchTip instance
    # to stay sovereign — this is not tied to Sentry's SaaS.
    sentry_dsn: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
